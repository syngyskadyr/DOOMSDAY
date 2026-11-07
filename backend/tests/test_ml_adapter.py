"""Offline adapter checks; no test contacts a model provider."""
from copy import deepcopy
from decimal import Decimal
import json
import os
from pathlib import Path
import unittest
from unittest.mock import patch

from backend.ml_adapter import (
    AdapterEvaluationError, AdapterUnavailable, AdapterValidationError, MLAdapter,
)


ROOT = Path(__file__).resolve().parents[2]


class MLAdapterTests(unittest.TestCase):
    def setUp(self):
        self.adapter = MLAdapter()
        self.checklist = json.loads((ROOT / "codex_context/checklist.json").read_text(encoding="utf-8-sig"))
        self.dialogue = json.loads((ROOT / "codex_context/eval/synthetic_dialogue.json").read_text(encoding="utf-8-sig"))
        self.assessment = json.loads((ROOT / "codex_context/eval/synthetic_response.json").read_text(encoding="utf-8-sig"))

    def test_offline_score_is_http_numeric_and_renamed(self):
        with patch.dict(os.environ, {}, clear=True):
            state = self.adapter.result_state(self.dialogue, self.checklist, self.assessment)
        self.assertEqual(state["calculation"]["total_score"], 100.0)
        self.assertIsInstance(state["calculation"]["total_score"], float)
        self.assertFalse(state["critical_error"])
        self.assertFalse(state["calculation"]["no_applicable_criteria"])
        self.assertEqual(set(state["calculation"]["rows"][0]),
                         {"criterion_id", "base_weight", "effective_weight", "earned_points"})
        self.assertIn("possible_points", state["calculation"]["sections"][0])
        self.assertNotIn("applicable_max", state["calculation"]["sections"][0])
        json.dumps(state, allow_nan=False)

    def test_unknown_flag_does_not_zero_numeric_score(self):
        row = self.assessment["criteria"][-1]
        row.update(status="needs_review", answer=None, uncertainty="Недостаточно контекста.")
        state = self.adapter.result_state(self.dialogue, self.checklist, self.assessment)
        self.assertIsNone(state["critical_error"])
        self.assertEqual(state["calculation"]["total_score"], 100.0)

    def test_empty_denominator_maps_to_explicit_marker(self):
        for criterion in self.checklist["criteria"]:
            if criterion["kind"] != "flag":
                criterion["weight"] = 0
        state = self.adapter.result_state(self.dialogue, self.checklist, self.assessment)
        self.assertIsNone(state["calculation"]["total_score"])
        self.assertTrue(state["calculation"]["no_applicable_criteria"])

    def test_configuration_uses_shared_validator_and_http_fields(self):
        changes = [lambda c: c.pop("title"),
                   lambda c: c["criteria"][0].update(weight=-1),
                   lambda c: c["criteria"][0].update(section_id="missing"),
                   lambda c: c["criteria"][0].update(levels=[{"value": 1, "description": "bad"}]),
                   lambda c: c["criteria"][0].update(flag_types=["insult"]),
                   lambda c: c["criteria"][0].update(na_rule="unused"),
                   lambda c: c["criteria"][3]["levels"].pop()]
        for change in changes:
            with self.subTest(change=change):
                checklist = deepcopy(self.checklist)
                change(checklist)
                with self.assertRaises(AdapterValidationError):
                    self.adapter.validate_checklist(checklist)

    def test_invalid_result_cannot_bypass_shared_validation(self):
        self.assessment["criteria"][0]["evidence_message_ids"] = ["m999"]
        with self.assertRaises(AdapterValidationError):
            self.adapter.result_state(self.dialogue, self.checklist, self.assessment)

    def test_editor_validates_without_ml_installed(self):
        with patch.object(self.adapter, "_modules", side_effect=AdapterUnavailable()):
            self.adapter.validate_checklist(self.checklist)
            invalid = deepcopy(self.checklist)
            invalid["criteria"][3]["levels"].pop()
            with self.assertRaises(AdapterValidationError):
                self.adapter.validate_checklist(invalid)
            with self.assertRaises(AdapterUnavailable):
                self.adapter.result_state(self.dialogue, self.checklist, self.assessment)
            with self.assertRaises(AdapterUnavailable):
                self.adapter.evaluate(self.dialogue, self.checklist)

    def test_availability_never_calls_network_or_exposes_setting_values(self):
        secret = "private-key-and-url-token"
        with patch.dict(os.environ, {}, clear=True), patch("cqa_eval.evaluator._http_post") as post:
            ready, message = self.adapter.availability()
            self.assertFalse(ready)
            self.assertIn("LLM_API_KEY", message)
            post.assert_not_called()
        with patch.dict(os.environ, {"LLM_API_KEY": secret, "LLM_BASE_URL": "https://host/?token=" + secret,
                                     "LLM_MODEL": "test", "LLM_API_STYLE": "responses"}, clear=True):
            ready, message = self.adapter.availability()
            self.assertFalse(ready)
            self.assertNotIn(secret, message)

    def test_usage_aggregates_all_attempts_and_preserves_unknown_totals(self):
        metadata = {"model": "test-model", "call_count": 2, "retry_count": 1,
                    "duration_seconds": 0.25, "cost_usd": Decimal("0.0002"),
                    "attempts": [{"usage": {"input_tokens": 10, "output_tokens": 2}},
                                 {"usage": {"input_tokens": 20, "output_tokens": 3}}],
                    "api_key": "must-not-pass-through"}
        mapped = self.adapter.metadata(metadata)
        self.assertEqual(mapped["usage"], {"input_tokens": 30, "output_tokens": 5})
        self.assertEqual(mapped["latency_ms"], 250)
        self.assertEqual(mapped["estimated_cost_usd"], 0.0002)
        self.assertNotIn("api_key", mapped)
        metadata["attempts"][0]["usage"] = None
        metadata["cost_usd"] = None
        mapped = self.adapter.metadata(metadata)
        self.assertEqual(mapped["usage"], {"input_tokens": None, "output_tokens": None})
        self.assertIsNone(mapped["estimated_cost_usd"])
        self.assertTrue(mapped["cost_note"])

    def test_evaluator_error_keeps_safe_attempt_metadata(self):
        from cqa_eval.evaluator import EvaluationError
        metadata = {"call_count": 1, "attempts": [{"error": "invalid_model_response", "usage": None}]}
        with patch.object(self.adapter, "_settings", return_value=object()), \
                patch("cqa_eval.evaluate_dialogue", side_effect=EvaluationError("private signed URL", metadata)):
            with self.assertRaises(AdapterEvaluationError) as caught:
                self.adapter.evaluate(self.dialogue, self.checklist)
        self.assertEqual(caught.exception.code, "INVALID_MODEL_RESPONSE")
        self.assertEqual(caught.exception.metadata["api_calls"], 1)
        self.assertNotIn("private", caught.exception.message)


if __name__ == "__main__":
    unittest.main()
