"""Batch safety tests: all model responses are synthetic, no network/GPU."""
from copy import deepcopy
from dataclasses import replace
import io
import json
from pathlib import Path
import tempfile
import unittest
from contextlib import redirect_stdout, redirect_stderr
from unittest.mock import patch, Mock
from urllib.error import HTTPError

from cqa_eval.batch import atomic_write, collect_results, load_token_counter, run_batch, run_lock
from cqa_eval.cli import main
from cqa_eval.data import InputError, load_dev, read_json
from cqa_eval.evaluator import PersistenceError, Settings, request_body, prepare_request

ROOT = Path(__file__).resolve().parents[1]


class BatchTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.directory = Path(self.temp.name) / "batch"
        self.checklist = read_json(ROOT / "codex_context/checklist.json")
        fixture = read_json(ROOT / "codex_context/eval/synthetic_dialogue.json")
        self.records = [dict(deepcopy(fixture), conversation_id=f"synthetic-{i}") for i in range(3)]
        from cqa_eval.data import normalize_dialogue
        self.records = [normalize_dialogue(r) for r in self.records]
        self.settings = Settings(api_key="never-log-this", base_url="https://example.test/v1",
                                 model="synthetic-model", api_style="chat_completions",
                                 max_output_tokens=1024, max_attempts=1)
        self.options = dict(context_window=10000, token_counter=lambda p: 100)

    def envelope(self, url, body, settings):
        payload = json.loads(body["messages"][1]["content"])
        dialogue, checklist = payload["conversation"], payload["checklist"]
        criteria = []
        for c in checklist["criteria"]:
            answer = c["scale_max"] if c["kind"] == "scale" else ("no" if c["kind"] == "flag" else "yes")
            criteria.append({"criterion_id": c["id"], "status": "evaluated", "answer": answer,
                             "reason": "Synthetic test response, not a real assessment",
                             "evidence_message_ids": ["m001"], "checked_range": None,
                             "na_reason": None, "uncertainty": None, "violations": []})
        result = {"conversation_id": dialogue["conversation_id"], "checklist_version": checklist["version"], "criteria": criteria}
        return {"model": "synthetic-model", "usage": {"prompt_tokens": 100, "completion_tokens": 50, "total_tokens": 150},
                "choices": [{"finish_reason": "stop", "message": {"content": json.dumps(result)}}]}

    def run_batch(self, **kwargs):
        return run_batch(self.records, self.checklist, self.settings, self.directory, **self.options, **kwargs)

    def test_all_65_and_ten_new_ids_without_code_changes(self):
        for size in (65, 10):
            with self.subTest(size=size):
                records = load_dev(ROOT / "codex_context/data/dev.json") if size == 65 else [
                    dict(deepcopy(self.records[0]), conversation_id=f"unseen-prod-{i}") for i in range(10)]
                target = self.directory / str(size)
                with patch("cqa_eval.evaluator._http_post", side_effect=self.envelope) as post:
                    result = run_batch(records, self.checklist, self.settings, target, **self.options)
                    self.assertEqual(post.call_count, size)
                self.assertTrue(result["complete"])
                self.assertEqual(result["counts"]["validated"], size)
                bundle = collect_results(records, self.checklist, target)
                self.assertEqual(len(bundle["results"]), size)
                self.assertFalse(bundle["organizer_format_verified"])
                self.assertEqual(len(list(target.glob("records/*/attempt-*/response_received-*.json"))), size)

    def test_persists_before_next_call_and_resume_skips_success(self):
        def post(url, body, settings):
            if json.loads(body["messages"][1]["content"])["conversation"]["conversation_id"] == "synthetic-1":
                self.assertTrue((self.directory / "records/0001/attempt-0001/result.json").exists())
            return self.envelope(url, body, settings)
        with patch("cqa_eval.evaluator._http_post", side_effect=post) as mock:
            first = self.run_batch(limit=1)
            self.assertFalse(first["complete"])
            before = (self.directory / "records/0001/attempt-0001/result.json").read_bytes()
            self.assertTrue(self.run_batch(resume=True)["complete"])
            self.assertEqual(mock.call_count, 3)
            self.run_batch(resume=True)
            self.assertEqual(mock.call_count, 3)
        self.assertEqual(before, (self.directory / "records/0001/attempt-0001/result.json").read_bytes())

    def test_interrupt_preserves_success_and_requires_explicit_retry(self):
        calls = 0
        def post(*args):
            nonlocal calls
            calls += 1
            if calls == 2:
                raise KeyboardInterrupt()
            return self.envelope(*args)
        with patch("cqa_eval.evaluator._http_post", side_effect=post):
            with self.assertRaises(KeyboardInterrupt):
                self.run_batch()
        with patch("cqa_eval.evaluator._http_post", side_effect=self.envelope) as mock:
            status = self.run_batch(resume=True)
            self.assertEqual(status["counts"], {"validated": 2, "error": 0, "interrupted": 1, "pending": 0})
            self.assertEqual(mock.call_count, 1)
            self.assertTrue(self.run_batch(resume=True, retry_interrupted=True)["complete"])
            self.assertEqual(mock.call_count, 2)
        self.assertTrue((self.directory / "records/0002/attempt-0001/started.json").exists())

    def test_invalid_response_saved_and_retry_keeps_history(self):
        def invalid(*args):
            envelope = self.envelope(*args)
            assessment = json.loads(envelope["choices"][0]["message"]["content"])
            assessment["criteria"][0]["evidence_message_ids"] = ["m999"]
            envelope["choices"][0]["message"]["content"] = json.dumps(assessment)
            return envelope
        with patch("cqa_eval.evaluator._http_post", side_effect=invalid):
            self.assertEqual(self.run_batch()["counts"]["error"], 3)
        with patch("cqa_eval.evaluator._http_post", side_effect=self.envelope) as mock:
            self.run_batch(resume=True)
            mock.assert_not_called()
            result = self.run_batch(resume=True, retry_errors=True)
            self.assertTrue(result["complete"])
            self.assertEqual(result["api_attempts_started"], 6)
            self.assertEqual(result["usage"]["total_tokens"], 900)
            self.assertIsNone(result["cost_usd"])
        self.assertEqual(len(list(self.directory.glob("records/*/attempt-*/error.json"))), 3)

    def test_auth_stops_batch_without_leaking_provider_message(self):
        with patch("cqa_eval.evaluator._http_post", side_effect=HTTPError("secret-url", 401, "secret-error", {}, None)) as post:
            result = self.run_batch()
        self.assertEqual(post.call_count, 1)
        self.assertEqual(result["counts"]["pending"], 2)
        for file in self.directory.rglob("*.json"):
            self.assertNotIn("never-log-this", file.read_text())
            self.assertNotIn("secret-error", file.read_text())

    def test_changed_configuration_and_corrupt_result_block_before_api(self):
        with patch("cqa_eval.evaluator._http_post", side_effect=self.envelope):
            self.run_batch(limit=1)
        for change in ("dialogue", "model", "prompt", "checklist"):
            with self.subTest(change=change), patch("cqa_eval.evaluator._http_post") as post:
                records, checklist, settings = deepcopy(self.records), deepcopy(self.checklist), self.settings
                if change == "dialogue": records[0]["transcript"][0]["text"] += "Changed"
                if change == "checklist": checklist["criteria"][0]["weight"] += 1
                if change == "model": settings = replace(settings, model="different")
                from cqa_eval.batch import prepare_request as original
                def prepare(*args):
                    value = original(*args)
                    if change == "prompt": value["system"] += "Changed"
                    return value
                with patch("cqa_eval.batch.prepare_request", side_effect=prepare), self.assertRaises(InputError):
                    run_batch(records, checklist, settings, self.directory, resume=True, **self.options)
                post.assert_not_called()
        result_path = self.directory / "records/0001/attempt-0001/result.json"
        result = read_json(result_path)
        result["calculation"]["total_score"] = "0.00"
        atomic_write(result_path, result)
        with patch("cqa_eval.evaluator._http_post") as post, self.assertRaises(InputError):
            self.run_batch(resume=True)
        post.assert_not_called()

    def test_context_checks_entire_dataset_before_calls_no_truncation(self):
        with patch("cqa_eval.evaluator._http_post") as post:
            self.options["token_counter"] = lambda p: 99999 if 'synthetic-2' in p["user"] else 100
            with self.assertRaises(InputError): self.run_batch(limit=1)
            post.assert_not_called()
            self.options["token_counter"] = None
            with self.assertRaises(InputError): self.run_batch()
            post.assert_not_called()
        self.assertFalse(self.directory.exists())

    def test_output_cap_is_explicit_and_optional_for_single_evaluation(self):
        prepared = prepare_request(self.records[0], self.checklist)
        self.assertEqual(request_body(prepared, self.settings)["max_tokens"], 1024)
        self.assertNotIn("max_tokens", request_body(prepared, replace(self.settings, max_output_tokens=None)))
        self.assertEqual(request_body(prepared, replace(self.settings, api_style="responses"))["max_output_tokens"], 1024)

    def test_local_tokenizer_never_downloads_or_loads_model_weights(self):
        import sys
        from types import ModuleType
        module = ModuleType("transformers")
        module.AutoTokenizer = Mock()
        tokenizer = module.AutoTokenizer.from_pretrained.return_value
        tokenizer.apply_chat_template.return_value = [1, 2, 3]
        with patch.dict(sys.modules, {"transformers": module}):
            count = load_token_counter(self.temp.name)
            self.assertEqual(count({"system": "instruction", "user": "full dialogue"}), 3)
        module.AutoTokenizer.from_pretrained.assert_called_once_with(self.temp.name, local_files_only=True, trust_remote_code=False)
        tokenizer.apply_chat_template.assert_called_once_with(
            [{"role": "system", "content": "instruction"}, {"role": "user", "content": "full dialogue"}],
            tokenize=True, add_generation_prompt=True)

    def test_template_kwargs_preserve_false_and_do_not_change_http_request(self):
        import sys
        from types import ModuleType
        module = ModuleType("transformers")
        module.AutoTokenizer = Mock()
        tokenizer = module.AutoTokenizer.from_pretrained.return_value
        tokenizer.apply_chat_template.return_value = [1, 2, 3]
        supplied = {"enable_thinking": False}
        with patch.dict(sys.modules, {"transformers": module}):
            counter = load_token_counter(self.temp.name, chat_template_kwargs=supplied)
            supplied["enable_thinking"] = True  # Caller mutation cannot change the captured settings.
            self.assertEqual(counter(prepare_request(self.records[0], self.checklist)), 3)
        self.assertIs(tokenizer.apply_chat_template.call_args.kwargs["enable_thinking"], False)
        self.options["token_counter"] = counter
        with patch("cqa_eval.evaluator._http_post", side_effect=self.envelope) as post:
            self.run_batch(limit=1)
        manifest = read_json(self.directory / "manifest.json")
        self.assertEqual(manifest["preflight"]["chat_template_kwargs"], {"enable_thinking": False})
        self.assertNotIn("chat_template_kwargs", post.call_args.args[1])

    def test_changed_template_kwargs_block_resume_even_when_token_counts_equal(self):
        import sys
        from types import ModuleType
        module = ModuleType("transformers")
        module.AutoTokenizer = Mock()
        module.AutoTokenizer.from_pretrained.return_value.apply_chat_template.return_value = [1] * 100
        with patch.dict(sys.modules, {"transformers": module}):
            self.options["token_counter"] = load_token_counter(self.temp.name, chat_template_kwargs={"enable_thinking": False})
            with patch("cqa_eval.evaluator._http_post", side_effect=self.envelope):
                self.run_batch(limit=1)
            old_manifest = read_json(self.directory / "manifest.json")
            self.options["token_counter"] = load_token_counter(self.temp.name, chat_template_kwargs={"enable_thinking": True})
            with patch("cqa_eval.evaluator._http_post") as post, self.assertRaisesRegex(InputError, "changed"):
                self.run_batch(resume=True)
            post.assert_not_called()
        self.assertEqual(old_manifest, read_json(self.directory / "manifest.json"))

    def test_template_kwargs_reject_nonobjects_and_reserved_counting_controls(self):
        invalid = [[], "false", False, {"enable_thinking": float("nan")}, {1: False}]
        invalid += [{key: False} for key in (
            "tokenize", "add_generation_prompt", "chat_template", "truncation", "max_length",
            "return_tensors", "return_dict", "return_assistant_tokens_mask", "padding",
            "conversation", "messages", "tools", "documents", "continue_final_message", "tokenizer_kwargs",
        )]
        for value in invalid:
            with self.subTest(value=value), self.assertRaises(InputError):
                load_token_counter(self.temp.name, chat_template_kwargs=value)

    def test_cli_template_kwargs_are_strict_and_require_a_tokenizer(self):
        source = Path(self.temp.name) / "input.json"
        atomic_write(source, {"isSuccess": True, "data": {"records": self.records}})
        args = ["batch", "--input", str(source), "--run-dir", str(self.directory),
                "--context-window", "10000", "--max-output-tokens", "1024", "--preflight"]
        cases = [(None, '{"enable_thinking":false}', "requires --tokenizer")]
        cases += [(self.temp.name, text, None) for text in (
            "null", "[]", "false", '"text"', "{invalid}", '{"tokenize":false}',
            '{"enable_thinking":NaN}', '{"enable_thinking":false,"enable_thinking":true}',
        )]
        for directory, raw, expected in cases:
            supplied = args + (["--tokenizer", directory] if directory else []) + ["--chat-template-kwargs", raw]
            with self.subTest(raw=raw, directory=directory), \
                    patch("cqa_eval.cli.Settings.from_env", return_value=self.settings), \
                    patch("cqa_eval.cli.load_token_counter") as loader, \
                    patch("cqa_eval.evaluator._http_post") as post, \
                    redirect_stdout(io.StringIO()) as output:
                self.assertEqual(main(supplied), 2)
                self.assertIn("error", json.loads(output.getvalue()))
                if expected:
                    self.assertIn(expected, output.getvalue())
                loader.assert_not_called()
                post.assert_not_called()
        with patch("cqa_eval.cli.Settings.from_env", return_value=self.settings), \
                patch("cqa_eval.cli.load_token_counter", return_value=lambda prepared: 100) as loader, \
                patch("cqa_eval.evaluator._http_post") as post, redirect_stdout(io.StringIO()):
            self.assertEqual(main(args + ["--tokenizer", self.temp.name,
                                         "--chat-template-kwargs", '{"enable_thinking":false}']), 0)
        loader.assert_called_once_with(Path(self.temp.name), chat_template_kwargs={"enable_thinking": False})
        post.assert_not_called()

    def test_valid_but_unresolved_answer_is_saved_and_blocks_handoff(self):
        def unresolved(*args):
            envelope = self.envelope(*args)
            assessment = json.loads(envelope["choices"][0]["message"]["content"])
            assessment["criteria"][0].update(status="needs_review", answer=None, uncertainty="Synthetic missing basis")
            envelope["choices"][0]["message"]["content"] = json.dumps(assessment)
            return envelope
        with patch("cqa_eval.evaluator._http_post", side_effect=unresolved):
            status = self.run_batch()
        self.assertTrue(status["complete"])
        self.assertFalse(status["ready_for_handoff"])
        self.assertEqual(status["needs_review"], 3)
        self.assertTrue(all(i["total_score"] is None for i in status["items"]))
        with self.assertRaises(InputError): collect_results(self.records, self.checklist, self.directory)

    def test_journal_write_error_stops_without_retry(self):
        from cqa_eval.batch import atomic_write as original
        def broken(path, value):
            if path.name.startswith("response_received"):
                raise OSError("disk full")
            original(path, value)
        with patch("cqa_eval.batch.atomic_write", side_effect=broken), patch("cqa_eval.evaluator._http_post", side_effect=self.envelope) as post:
            with self.assertRaises(PersistenceError): self.run_batch()
            self.assertEqual(post.call_count, 1)
        self.assertFalse(list(self.directory.glob("records/*/attempt-*/result.json")))

    def test_atomic_replace_failure_keeps_previous_file(self):
        file = self.directory / "value.json"
        atomic_write(file, {"old": True})
        with patch("cqa_eval.batch.os.replace", side_effect=OSError("disk")), self.assertRaises(OSError):
            atomic_write(file, {"new": True})
        self.assertEqual(read_json(file), {"old": True})

    def test_cannot_start_paid_call_without_durable_start_marker(self):
        from cqa_eval.batch import atomic_write as original
        def broken(path, value):
            if path.name.startswith("attempt_started"):
                raise OSError("disk full")
            original(path, value)
        with patch("cqa_eval.batch.atomic_write", side_effect=broken), patch("cqa_eval.evaluator._http_post") as post:
            with self.assertRaises(PersistenceError): self.run_batch()
            post.assert_not_called()

    def test_result_disk_failure_leaves_raw_response_and_stops_next_call(self):
        from cqa_eval.batch import atomic_write as original
        def broken(path, value):
            if path.name == "result.json": raise OSError("disk full")
            original(path, value)
        with patch("cqa_eval.batch.atomic_write", side_effect=broken), patch("cqa_eval.evaluator._http_post", side_effect=self.envelope) as post:
            with self.assertRaises(OSError): self.run_batch()
            self.assertEqual(post.call_count, 1)
        self.assertTrue((self.directory / "records/0001/attempt-0001/response_received-01.json").exists())
        self.assertFalse((self.directory / "records/0002").exists())

    def test_lock_and_existing_unrelated_directory_are_protected(self):
        with run_lock(self.directory), self.assertRaises(InputError):
            with run_lock(self.directory): pass
        atomic_write(self.directory / "human.json", {"preserve": True})
        with patch("cqa_eval.evaluator._http_post") as post, self.assertRaises(InputError):
            self.run_batch()
        post.assert_not_called()
        self.assertEqual(read_json(self.directory / "human.json"), {"preserve": True})

    def test_collect_rejects_incomplete_unresolved_and_changed_data(self):
        with patch("cqa_eval.evaluator._http_post", side_effect=self.envelope):
            self.run_batch(limit=1)
        with self.assertRaises(InputError): collect_results(self.records, self.checklist, self.directory)
        with patch("cqa_eval.evaluator._http_post", side_effect=self.envelope):
            self.run_batch(resume=True)
        changed = deepcopy(self.records)
        changed[0]["transcript"][0]["text"] += "Changed"
        with self.assertRaises(InputError): collect_results(changed, self.checklist, self.directory)

    def test_cli_preflight_is_offline_and_checks_expected_count(self):
        args = ["batch", "--run-dir", str(self.directory), "--expected-count", "65",
                "--context-window", "32768", "--max-output-tokens", "4096", "--preflight"]
        with patch("cqa_eval.evaluator._http_post") as post, redirect_stdout(io.StringIO()) as output:
            self.assertEqual(main(args), 2)  # No tokenizer: diagnostic only, not ready.
        self.assertEqual(json.loads(output.getvalue())["records"], 65)
        post.assert_not_called()
        self.assertFalse(self.directory.exists())
        args[args.index("65")] = "10"
        with redirect_stdout(io.StringIO()) as output:
            self.assertEqual(main(args), 2)
        self.assertIn("Expected 10 records", output.getvalue())

    def test_cli_batch_and_collect_round_trip(self):
        source = Path(self.temp.name) / "input.json"
        output_path = Path(self.temp.name) / "results.json"
        atomic_write(source, {"isSuccess": True, "data": {"records": self.records}})
        args = ["batch", "--input", str(source), "--expected-count", "3", "--run-dir", str(self.directory),
                "--context-window", "10000", "--max-output-tokens", "1024", "--tokenizer", self.temp.name]
        with patch("cqa_eval.cli.Settings.from_env", return_value=self.settings), \
                patch("cqa_eval.cli.load_token_counter", return_value=lambda p: 100), \
                patch("cqa_eval.evaluator._http_post", side_effect=self.envelope), \
                redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
            self.assertEqual(main(args), 0)
        collect = ["batch-collect", "--input", str(source), "--run-dir", str(self.directory), "--out", str(output_path)]
        with patch("cqa_eval.evaluator._http_post") as post, redirect_stdout(io.StringIO()):
            self.assertEqual(main(collect), 0)
            before = output_path.read_bytes()
            self.assertEqual(main(collect), 2)
            self.assertEqual(before, output_path.read_bytes())
        post.assert_not_called()
        self.assertEqual(len(read_json(output_path)["results"]), 3)


if __name__ == "__main__":
    unittest.main()
