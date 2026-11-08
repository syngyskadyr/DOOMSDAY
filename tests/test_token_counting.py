"""Regression coverage for list, BatchEncoding and tensor chat-template outputs."""
from collections import UserDict
from copy import deepcopy
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from cqa_eval.batch import load_token_counter, preflight, run_batch
from cqa_eval.data import InputError, normalize_dialogue, read_json
from cqa_eval.evaluator import Settings, prepare_request

ROOT = Path(__file__).resolve().parents[1]


class TokenCountingTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.module = Mock()
        self.tokenizer = self.module.AutoTokenizer.from_pretrained.return_value
        self.modules = patch.dict("sys.modules", {"transformers": self.module})
        self.modules.start()
        self.addCleanup(self.modules.stop)
        self.counter = load_token_counter(self.directory.name)
        self.checklist = read_json(ROOT / "codex_context/checklist.json")
        self.dialogue = normalize_dialogue(read_json(ROOT / "codex_context/eval/synthetic_dialogue.json"))
        self.settings = Settings(api_key="synthetic", base_url="https://example.test/v1",
                                 model="synthetic", api_style="chat_completions", max_output_tokens=2560)

    def test_flat_batched_mapping_and_tensor_sequence_lengths(self):
        for output in ([1] * 123, [[1] * 123],
                       UserDict(input_ids=[1] * 123, attention_mask=[1] * 123),
                       {"input_ids": [[1] * 123], "attention_mask": [[1] * 123]},
                       SimpleNamespace(shape=(123,)), SimpleNamespace(shape=(1, 123)),
                       {"input_ids": SimpleNamespace(shape=(1, 123)), "attention_mask": None}):
            with self.subTest(output_type=type(output).__name__):
                self.tokenizer.apply_chat_template.return_value = output
                self.assertEqual(self.counter({"system": "rules", "user": "conversation"}), 123)

    def test_invalid_or_multiple_sequences_fail_closed(self):
        for output in ({"attention_mask": [1, 1]}, [], [[]], [[1], [2]], [True], [-1],
                       "text", [{"role": "system"}, {"role": "user"}],
                       SimpleNamespace(shape=()), SimpleNamespace(shape=(0,)),
                       SimpleNamespace(shape=(2, 123)), SimpleNamespace(shape=(1, 1, 123))):
            with self.subTest(output_type=type(output).__name__):
                self.tokenizer.apply_chat_template.return_value = output
                with self.assertRaises(InputError):
                    self.counter({"system": "rules", "user": "conversation"})

    def test_different_full_requests_cannot_count_mapping_keys_or_messages(self):
        # Emulate a BatchEncoding with TWO keys for every request, while varying
        # sequence length. The original len(BatchEncoding) bug reports 2 for both.
        def encode(messages, **kwargs):
            self.assertEqual(len(messages), 2)
            self.assertTrue(kwargs["tokenize"])
            self.assertTrue(kwargs["add_generation_prompt"])
            self.assertNotIn("truncation", kwargs)
            size = sum(len(m["content"].encode("utf-8")) for m in messages) + 12
            return UserDict(input_ids=[1] * size, attention_mask=[1] * size)
        self.tokenizer.apply_chat_template.side_effect = encode
        short = self.dialogue
        long = deepcopy(short)
        long["conversation_id"] = "synthetic-long"
        long["transcript"][-1]["text"] += " long transcript" * 5000
        report = preflight([short, long], self.checklist, self.settings,
                           context_window=8192, margin=512, token_counter=self.counter)
        counts = [item["input_tokens"] for item in report["items"]]
        self.assertGreater(counts[0], 2)
        self.assertGreater(counts[1], counts[0] + 10000)
        for dialogue, item in zip((short, long), report["items"]):
            prepared = prepare_request(dialogue, self.checklist)
            expected = len(prepared["system"].encode("utf-8")) + len(prepared["user"].encode("utf-8")) + 12
            self.assertEqual(item["input_tokens"], expected)
            self.assertEqual(item["required_context"], expected + 2560 + 512)
        self.assertIn("synthetic-long", report["oversized_ids"])
        self.assertFalse(report["ready"])
        # Changing system rules or checklist must also change the counted input.
        prepared = prepare_request(short, self.checklist)
        self.assertGreater(self.counter(dict(prepared, system=prepared["system"] + " rules" * 100)), counts[0])
        checklist = deepcopy(self.checklist)
        checklist["criteria"][0]["instruction"] += " additional rules" * 100
        self.assertGreater(self.counter(prepare_request(short, checklist)), counts[0])

    def test_exact_context_boundary_and_no_live_request_when_oversized(self):
        self.tokenizer.apply_chat_template.return_value = {"input_ids": SimpleNamespace(shape=(1, 5120)), "attention_mask": None}
        options = dict(context_window=8192, margin=512, token_counter=self.counter)
        report = preflight([self.dialogue], self.checklist, self.settings, **options)
        self.assertTrue(report["ready"])
        self.assertEqual(report["items"][0]["required_context"], 8192)
        self.tokenizer.apply_chat_template.return_value["input_ids"].shape = (1, 5121)
        report = preflight([self.dialogue], self.checklist, self.settings, **options)
        self.assertFalse(report["ready"])
        self.assertEqual(report["oversized_ids"], [self.dialogue["conversation_id"]])
        with patch("cqa_eval.evaluator._http_post") as post:
            with self.assertRaises(InputError):
                run_batch([self.dialogue], self.checklist, self.settings, Path(self.directory.name) / "run", **options)
            post.assert_not_called()


if __name__ == "__main__":
    unittest.main()
