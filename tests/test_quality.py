"""Offline metric/provenance regression tests with invented labels and predictions.

Synthetic human_reviewed markers below exercise the gate; they are not real
annotations and establish no model quality or organizer ground truth.
"""
from contextlib import redirect_stdout
from copy import deepcopy
from decimal import Decimal
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from cqa_eval.batch import atomic_write
from cqa_eval.core import calculate_score
from cqa_eval.data import InputError, fingerprint, normalize_dialogue, read_json
from cqa_eval.quality import compare_paired_runs, compare_predictions, prepare_review
from cqa_eval.validation import response_schema, validate_response


ROOT = Path(__file__).resolve().parents[1]


class QualityTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.checklist = read_json(ROOT / "codex_context/checklist.json")
        source = read_json(ROOT / "codex_context/eval/synthetic_dialogue.json")
        self.records = [normalize_dialogue(dict(deepcopy(source), conversation_id=f"synthetic-pair-{i}", language=lang))
                        for i, lang in enumerate(("RU", "KZ", "EN"))]
        self.labels = prepare_review(self.records, self.checklist, [record["conversation_id"] for record in self.records])
        for index, case in enumerate(self.labels["cases"]):
            case.update(annotation_status="human_reviewed", reviewer="synthetic-test-reviewer")
            case["criteria"] = [{"id": row["criterion_id"], **{k: v for k, v in row.items() if k != "criterion_id"}}
                                for row in self.assessment(index)["criteria"]]
        self.conditions = {"model_name": "Invented/OfflineModel", "model_revision": "1" * 40,
            "quantization": "invented-awq", "tokenizer_sha256": "2" * 64,
            "template_sha256": "3" * 64, "vllm_version": "invented-1.0", "context_window": 32768,
            "decoding": {"temperature": 0.7, "top_p": 0.8, "top_k": 20, "min_p": 0.0,
                         "max_output_tokens": 4096, "seed": None, "enable_thinking": False}}
        self.baseline, self.a = self.make_run("baseline")
        self.candidate, self.b = self.make_run("candidate")

    def assessment(self, index):
        rows = []
        for criterion in self.checklist["criteria"]:
            answer = criterion["scale_max"] if criterion["kind"] == "scale" else ("no" if criterion["kind"] == "flag" else "yes")
            rows.append({"criterion_id": criterion["id"], "status": "evaluated", "answer": answer,
                "reason": "Invented offline quality test explanation.", "evidence_message_ids": ["m002"],
                "checked_range": {"start_message_id": "m001", "end_message_id": "m004"},
                "na_reason": None, "uncertainty": None, "violations": []})
        return {"conversation_id": self.records[index]["conversation_id"],
                "checklist_version": self.checklist["version"], "criteria": rows}

    def make_run(self, tag):
        directory = self.base / tag
        items = [{"conversation_id": record["conversation_id"], "dialogue_hash": fingerprint(record),
                  "prompt_hash": fingerprint([tag, "prompt", record["conversation_id"]]),
                  "request_hash": fingerprint([tag, "request", record["conversation_id"]])} for record in self.records]
        manifest = {"format": "cqa-batch-1", "model": "invented-served-model",
            "checklist_hash": fingerprint(self.checklist), "api_style": "chat_completions",
            "structured_output": "json_schema", "max_attempts": 1, "timeout": 300,
            "preflight": {"records": len(items), "items": items, "context_window": 32768,
                          "max_output_tokens": 4096, "chat_template_kwargs": {"enable_thinking": False}}}
        atomic_write(directory / "manifest.json", manifest)
        provenance = {"format": "cqa-paired-provenance-1", "manifest_hash": fingerprint(manifest),
            "instruction_hash": fingerprint("Invented instruction " + tag),
            "response_schema_hash": fingerprint(response_schema()), "served_model_name": manifest["model"],
            "serving_conditions": deepcopy(self.conditions)}
        return directory, provenance

    def write(self, directory, provenance, index, state="completed", assessment=None, attempt=1):
        manifest = read_json(directory / "manifest.json")
        item = manifest["preflight"]["items"][index]
        folder = directory / "records" / f"{index+1:04d}" / f"attempt-{attempt:04d}"
        atomic_write(folder / "started.json", {"conversation_id": item["conversation_id"], "request_hash": item["request_hash"]})
        if state == "interrupted":
            return folder
        metadata = {**{k: item[k] for k in ("dialogue_hash", "prompt_hash", "request_hash")},
            "checklist_hash": fingerprint(self.checklist), "checklist_version": self.checklist["version"],
            "instruction_hash": provenance["instruction_hash"], "response_schema_hash": provenance["response_schema_hash"],
            "api_style": manifest["api_style"], "requested_model": manifest["model"], "model": manifest["model"],
            "generation_settings": {"structured_output": "json_schema", "max_output_tokens": 4096},
            "call_count": 1, "attempts": [{"attempt": 1, "requested_model": manifest["model"], "model": manifest["model"]}]}
        if state == "error":
            atomic_write(folder / "error.json", {"conversation_id": item["conversation_id"],
                "error": "PRIVATE invented provider error https://private.invalid/secret", "metadata": metadata})
            return folder
        assessment = assessment or self.assessment(index)
        validated = validate_response(assessment, self.records[index], self.checklist)
        calculation = calculate_score(self.checklist, validated)
        atomic_write(folder / "result.json", {"assessment": assessment, "calculation": calculation,
            "checklist_snapshot": self.checklist, "critical_error": calculation["gross_error"], "metadata": metadata})
        return folder

    def complete(self):
        for index in range(len(self.records)):
            self.write(self.baseline, self.a, index)
            self.write(self.candidate, self.b, index)

    def paired(self, labels=None, a=None, b=None):
        with patch("cqa_eval.evaluator._http_post") as post:
            result = compare_paired_runs(self.records, self.checklist, labels or self.labels,
                self.baseline, self.candidate, a or self.a, b or self.b)
            post.assert_not_called()
        return result

    def test_existing_metrics_keep_fixed_denominators_and_explicit_failure_rate(self):
        predictions = [self.assessment(0), {"conversation_id": self.records[1]["conversation_id"], "error": "private detail"}]
        result = compare_predictions(self.records, self.checklist, self.labels, predictions)
        self.assertEqual(result["requested"], 3)
        self.assertEqual(result["processed"], 1)
        self.assertEqual(result["processing_error_count"], 2)
        self.assertAlmostEqual(result["processing_error_rate"], 2 / 3)
        self.assertEqual(result["quality_metrics"]["agreement"]["all"]["total"], 24)
        self.assertEqual(result["quality_metrics"]["agreement"]["all"]["correct"], 8)
        self.assertEqual(result["quality_metrics"]["score_pairs"], 1)
        self.assertEqual(result["quality_metrics"]["score_pair_ids"], [self.records[0]["conversation_id"]])
        self.assertEqual({row["error"] for row in result["processing_errors"]}, {"saved_processing_error", "missing_prediction"})
        self.assertNotIn("private detail", json.dumps(result, default=str))

    def test_na_confusion_and_critical_unknown_is_not_explicit_no(self):
        labels = deepcopy(self.labels)
        labels["cases"][0]["criteria"][1].update(answer="na", na_reason="Invented sufficient starting context.")
        for case in labels["cases"]:
            case["criteria"][-1].update(answer="yes", violations=[{"type": "insult", "reason": "Invented flag unit case.", "evidence_message_ids": ["m002"]}])
        predictions = [self.assessment(index) for index in range(3)]
        predictions[1]["criteria"][1].update(answer="na", na_reason="Invented predicted N/A.")
        predictions[1]["criteria"][-1].update(status="needs_review", answer=None, uncertainty="Invented unresolved flag.")
        result = compare_predictions(self.records, self.checklist, labels, predictions)
        self.assertEqual(result["quality_metrics"]["na"]["applicable_instead_of_na"], 1)
        self.assertEqual(result["quality_metrics"]["na"]["na_instead_of_applicable"], 1)
        flag = result["quality_metrics"]["flags"]["08"]
        self.assertEqual((flag["fn"], flag["explicit_false_negatives"], flag["unresolved_positive_examples"]), (3, 2, 1))
        self.assertEqual(flag["missing_or_unknown"], 1)
        self.assertIn("including missing/unknown", flag["fn_note"])
        self.assertEqual(result["processed"], 3)
        self.assertEqual(result["completed"], 2)
        self.assertAlmostEqual(result["needs_review_rate"], 1 / 3)

    def test_malformed_duplicate_and_extra_predictions_fail_safely(self):
        for predictions in ([None], [42], [{"conversation_id": "not-requested", "error": "invented"}],
                            [self.assessment(0), self.assessment(0)]):
            with self.subTest(predictions=predictions), self.assertRaises(InputError):
                compare_predictions(self.records, self.checklist, self.labels, predictions)
        malformed = {"conversation_id": self.records[0]["conversation_id"], "assessment": None}
        result = compare_predictions(self.records, self.checklist, self.labels, [malformed])
        self.assertEqual(result["processing_errors"][0]["error"], "invalid_prediction")

    def test_evidence_reviews_require_matching_prediction_digest(self):
        labels = deepcopy(self.labels)
        prediction = self.assessment(0)
        for row in labels["cases"][0]["criteria"]:
            row["evidence_review"] = {"checked": True, "supported": True}
        result = compare_predictions(self.records, self.checklist, labels, [prediction])
        self.assertEqual(result["quality_metrics"]["evidence"]["checked"], 0)
        self.assertEqual(result["quality_metrics"]["evidence"]["ignored_unbound_or_stale"], 8)
        for row in labels["cases"][0]["criteria"]:
            row["evidence_review"]["prediction_hash"] = fingerprint(prediction)
        result = compare_predictions(self.records, self.checklist, labels, [prediction])
        self.assertEqual(result["quality_metrics"]["evidence"]["checked"], 8)
        prediction["criteria"][0]["reason"] += " Changed prediction."
        result = compare_predictions(self.records, self.checklist, labels, [prediction])
        self.assertEqual(result["quality_metrics"]["evidence"]["checked"], 0)

    def test_paired_common_numeric_mae_has_fixed_ids_and_does_not_hide_failures(self):
        self.write(self.baseline, self.a, 0)
        wrong = self.assessment(1)
        wrong["criteria"][0]["answer"] = "no"  # A: error 10 on record 2.
        self.write(self.baseline, self.a, 1, assessment=wrong)
        self.write(self.baseline, self.a, 2, "error")
        wrong = self.assessment(0)
        wrong["criteria"][3]["answer"] = 10  # B: error 10 on record 1.
        self.write(self.candidate, self.b, 0, assessment=wrong)
        self.write(self.candidate, self.b, 1, "error")
        self.write(self.candidate, self.b, 2)
        result = self.paired()
        self.assertEqual(result["fixed_reviewed_criterion_denominator"], 24)
        for arm in ("baseline", "candidate"):
            self.assertEqual(result[arm]["quality_metrics"]["score_mae"], Decimal(5))
            self.assertEqual(result[arm]["quality_metrics"]["score_pairs"], 2)
            self.assertAlmostEqual(result[arm]["processing_error_rate"], 1 / 3)
        common = result["paired_quality_metrics"]
        self.assertEqual(common["score_pairs"], 1)
        self.assertEqual(common["score_pair_ids"], [self.records[0]["conversation_id"]])
        self.assertEqual(common["baseline_mae"], 0)
        self.assertEqual(common["candidate_mae"], 10)
        self.assertEqual(common["mae_delta_candidate_minus_baseline"], 10)
        self.assertNotEqual(result["baseline"]["quality_metrics"]["score_pair_ids"], result["candidate"]["quality_metrics"]["score_pair_ids"])

    def test_blank_and_ai_labels_have_no_quality_metrics_but_failure_rates_remain(self):
        self.write(self.baseline, self.a, 0)
        self.write(self.baseline, self.a, 1, "interrupted")
        self.write(self.candidate, self.b, 0, "error")
        blank = prepare_review(self.records, self.checklist, [r["conversation_id"] for r in self.records])
        for labels in (blank, deepcopy(self.labels)):
            if labels is not blank:
                for case in labels["cases"]: case["annotation_status"] = "ai_reviewed"
            result = self.paired(labels=labels)
            self.assertIsNone(result["paired_quality_metrics"])
            self.assertIsNone(result["baseline"]["quality_metrics"])
            self.assertEqual(result["reviewed_records"], 0)
            self.assertEqual(result["baseline"]["state_counts"], {"completed": 1, "interrupted": 1, "pending": 1})
            self.assertEqual(result["candidate"]["state_counts"], {"error": 1, "pending": 2})
            self.assertAlmostEqual(result["baseline"]["processing_error_rate"], 2 / 3)
            self.assertEqual(result["candidate"]["processing_error_rate"], 1)

    def test_provenance_hashes_include_artifacts_labels_and_distinct_uniform_prompts(self):
        self.complete()
        before = {str(p): p.read_bytes() for p in self.base.rglob("*") if p.is_file()}
        result = self.paired()
        self.assertEqual(before, {str(p): p.read_bytes() for p in self.base.rglob("*") if p.is_file()})
        self.assertEqual(result["labels_hash"], fingerprint(self.labels))
        self.assertNotEqual(result["provenance"]["baseline"]["instruction_hash"], result["provenance"]["candidate"]["instruction_hash"])
        self.assertEqual(result["provenance"]["baseline"]["serving_conditions_hash"], result["provenance"]["candidate"]["serving_conditions_hash"])
        self.assertIn("manifest.json", result["provenance"]["baseline"]["artifact_sha256"])
        self.assertIn("no remote verification", result["reproducibility_note"])

    def test_different_or_missing_explicit_conditions_and_manifest_binding_rejected(self):
        self.complete()
        for field in ("model_revision", "tokenizer_sha256", "template_sha256", "vllm_version", "decoding", "manifest_hash"):
            with self.subTest(field=field):
                provenance = deepcopy(self.b)
                if field == "manifest_hash": provenance[field] = "f" * 64
                elif field == "decoding": provenance["serving_conditions"][field]["temperature"] = 0.9
                else: provenance["serving_conditions"][field] = ("f" * 40 if field == "model_revision" else "f" * 64 if "sha256" in field else "different")
                with self.assertRaises(InputError): self.paired(b=provenance)
        provenance = deepcopy(self.b)
        del provenance["serving_conditions"]["tokenizer_sha256"]
        with self.assertRaises(InputError): self.paired(b=provenance)

    def test_mixed_instruction_request_or_model_metadata_is_rejected(self):
        self.complete()
        path = self.candidate / "records/0002/attempt-0001/result.json"
        original = read_json(path)
        for field in ("instruction_hash", "prompt_hash", "request_hash", "model", "response_schema_hash"):
            with self.subTest(field=field):
                changed = deepcopy(original)
                changed["metadata"][field] = "different"
                atomic_write(path, changed)
                with self.assertRaises(InputError): self.paired()
        atomic_write(path, original)

    def test_different_ids_data_checklist_and_label_subsets_are_rejected(self):
        self.complete()
        labels = deepcopy(self.labels)
        labels["cases"].pop()
        with self.assertRaisesRegex(InputError, "exactly the IDs"):
            self.paired(labels=labels)
        manifest = read_json(self.candidate / "manifest.json")
        for field in ("conversation_id", "dialogue_hash"):
            changed = deepcopy(manifest)
            changed["preflight"]["items"][0][field] = "different"
            atomic_write(self.candidate / "manifest.json", changed)
            provenance = deepcopy(self.b)
            provenance["manifest_hash"] = fingerprint(changed)
            with self.assertRaises(InputError): self.paired(b=provenance)
        atomic_write(self.candidate / "manifest.json", manifest)
        labels = deepcopy(self.labels)
        labels["checklist_hash"] = "f" * 64
        with self.assertRaises(InputError): self.paired(labels=labels)

    def test_single_attempt_policy_rejects_retry_selection_and_multiple_calls(self):
        self.complete()
        self.write(self.candidate, self.b, 0, "error", attempt=2)
        with self.assertRaisesRegex(InputError, "multiple attempts"):
            self.paired()
        import shutil
        shutil.rmtree(self.candidate / "records/0001/attempt-0002")
        path = self.candidate / "records/0001/attempt-0001/result.json"
        saved = read_json(path)
        saved["metadata"]["call_count"] = 2
        saved["metadata"]["attempts"].append(deepcopy(saved["metadata"]["attempts"][0]))
        atomic_write(path, saved)
        with self.assertRaisesRegex(InputError, "at most one"):
            self.paired()
        manifest = read_json(self.candidate / "manifest.json")
        manifest["max_attempts"] = 2
        atomic_write(self.candidate / "manifest.json", manifest)
        provenance = deepcopy(self.b)
        provenance["manifest_hash"] = fingerprint(manifest)
        with self.assertRaisesRegex(InputError, "max_attempts=1"):
            self.paired(b=provenance)

    def test_paired_does_not_reuse_evidence_review_even_when_bound_to_one_arm(self):
        self.complete()
        labels = deepcopy(self.labels)
        prediction = read_json(self.baseline / "records/0001/attempt-0001/result.json")
        for row in labels["cases"][0]["criteria"]:
            row["evidence_review"] = {"checked": True, "supported": True, "prediction_hash": fingerprint(prediction)}
        result = self.paired(labels=labels)
        for arm in ("baseline", "candidate"):
            self.assertEqual(result[arm]["quality_metrics"]["evidence"]["checked"], 0)
        self.assertIn("not reused", result["evidence_review_policy"])

    def cli_args(self, destination):
        source, labels, a, b = [self.base / name for name in ("source.json", "labels.json", "a.json", "b.json")]
        atomic_write(source, {"isSuccess": True, "data": {"records": self.records}})
        blank = prepare_review(self.records, self.checklist, [r["conversation_id"] for r in self.records])
        atomic_write(labels, blank)
        atomic_write(a, self.a)
        atomic_write(b, self.b)
        return ["eval", "paired", "--input", str(source), "--checklist", str(ROOT / "codex_context/checklist.json"),
            "--labels", str(labels), "--baseline-run-dir", str(self.baseline), "--candidate-run-dir", str(self.candidate),
            "--baseline-provenance", str(a), "--candidate-provenance", str(b), "--out", str(destination)]

    def test_cli_blank_gt_without_any_predictions_is_offline_and_never_overwrites(self):
        from cqa_eval.cli import main
        destination = self.base / "report.json"
        args = self.cli_args(destination)  # Both durable runs contain only manifests.
        with patch("cqa_eval.cli.Settings.from_env") as env, patch("cqa_eval.evaluator._http_post") as post, \
                redirect_stdout(io.StringIO()) as stream:
            self.assertEqual(main(args), 0)
        env.assert_not_called()
        post.assert_not_called()
        self.assertFalse(json.loads(stream.getvalue())["quality_metrics_available"])
        result = read_json(destination)
        self.assertIsNone(result["paired_quality_metrics"])
        self.assertEqual(result["baseline"]["processing_error_rate"], 1)
        before = destination.read_bytes()
        with redirect_stdout(io.StringIO()): self.assertEqual(main(args), 2)
        self.assertEqual(destination.read_bytes(), before)
        inside = self.baseline / "must-not-be-created.json"
        with redirect_stdout(io.StringIO()): self.assertEqual(main(self.cli_args(inside)), 2)
        self.assertFalse(inside.exists())


if __name__ == "__main__":
    unittest.main()
