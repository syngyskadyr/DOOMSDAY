"""Organizer export checks using invented transcripts and saved predictions only."""
from copy import deepcopy
from contextlib import chdir, redirect_stdout
import io
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from jsonschema import Draft202012Validator

from cqa_eval.batch import atomic_write
from cqa_eval.core import calculate_score
from cqa_eval.data import InputError, fingerprint, normalize_dialogue, read_json
from cqa_eval.submission import (
    CRITERION_CODES, OFFICIAL_CHECKLIST_HASH, export_submission,
    export_submission_with_provenance, submission_schema, validate_submission,
)
from cqa_eval.validation import validate_response


ROOT = Path(__file__).resolve().parents[1]


class SubmissionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.directory = Path(self.temp.name) / "batch"
        self.directory.mkdir()
        self.checklist = read_json(ROOT / "codex_context/checklist.json")
        transcript = [
            {"timestamp": "00:00", "speaker": "client", "text": "Invented navigation question."},
            {"timestamp": "00:01", "speaker": "bot", "text": "Invented bot greeting."},
            {"timestamp": "00:02", "speaker": "system", "text": "Invented handoff event."},
            {"timestamp": "00:03", "speaker": "operator", "text": "Invented greeting and instruction."},
            {"timestamp": "00:04", "speaker": "client", "text": "Invented acknowledgement."},
            {"timestamp": "00:05", "speaker": "operator", "text": "Invented summary and farewell."},
        ]
        self.records = [normalize_dialogue({"conversation_id": f"synthetic-prod-{i}",
            "channel": "agent chat", "language": "EN", "audio_url": None,
            "transcript": deepcopy(transcript)}) for i in range(10)]
        self.model = "invented-served-model"
        self.items = [{"conversation_id": r["conversation_id"], "dialogue_hash": fingerprint(r),
                       "prompt_hash": fingerprint(["invented-prompt", i]),
                       "request_hash": fingerprint(["invented-request", i])} for i, r in enumerate(self.records)]
        self.write_manifest()

    def write_manifest(self):
        atomic_write(self.directory / "manifest.json", {
            "format": "cqa-batch-1", "model": self.model,
            "api_style": "chat_completions", "structured_output": "json_schema",
            "checklist_hash": fingerprint(self.checklist),
            "preflight": {"records": len(self.records), "items": self.items}})

    def assessment(self, index):
        rows = []
        for c in self.checklist["criteria"]:
            answer = c["scale_max"] if c["kind"] == "scale" else ("no" if c["kind"] == "flag" else "yes")
            rows.append({"criterion_id": c["id"], "status": "evaluated", "answer": answer,
                "reason": "Invented rationale for exporter tests.", "evidence_message_ids": ["m004"],
                "checked_range": None, "na_reason": None, "uncertainty": None, "violations": []})
        return {"conversation_id": self.records[index]["conversation_id"],
                "checklist_version": self.checklist["version"], "criteria": rows}

    def metadata(self, index):
        return {**{k: self.items[index][k] for k in ("dialogue_hash", "prompt_hash", "request_hash")},
            "checklist_hash": fingerprint(self.checklist), "checklist_version": self.checklist["version"],
            "requested_model": self.model, "model": self.model,
            "attempts": [{"requested_model": self.model, "model": self.model}]}

    def write(self, index, state="ok", assessment=None, attempt=1):
        folder = self.directory / "records" / f"{index+1:04d}" / f"attempt-{attempt:04d}"
        atomic_write(folder / "started.json", {"conversation_id": self.records[index]["conversation_id"],
                                              "request_hash": self.items[index]["request_hash"]})
        if state == "interrupted":
            return folder
        if state == "error":
            atomic_write(folder / "error.json", {"conversation_id": self.records[index]["conversation_id"],
                "error": "PRIVATE PROVIDER DETAIL https://private.example/token", "metadata": self.metadata(index)})
            return folder
        assessment = assessment or self.assessment(index)
        validated = validate_response(assessment, self.records[index], self.checklist)
        score = calculate_score(self.checklist, validated)
        atomic_write(folder / "result.json", {"assessment": assessment, "calculation": score,
            "critical_error": score["gross_error"], "checklist_snapshot": self.checklist,
            "metadata": self.metadata(index),
            "evidence": {"never_copy": "PRIVATE EXTRACTED TRANSCRIPT AND URL"}})
        return folder

    def complete(self):
        for index in range(len(self.records)):
            self.write(index)

    def export(self, **kwargs):
        args = dict(team_name="Invented team", model_name=self.model, model_mode="local", dataset="prod")
        args.update(kwargs)
        with patch("cqa_eval.evaluator._http_post") as post:
            result = export_submission(self.records, self.checklist, self.directory, **args)
            post.assert_not_called()
        return result

    def test_schema_mapping_types_and_source_indices(self):
        self.assertEqual(fingerprint(self.checklist), OFFICIAL_CHECKLIST_HASH)
        Draft202012Validator.check_schema(submission_schema())
        self.complete()
        before = {str(p): p.read_bytes() for p in self.directory.rglob("*.json")}
        output = self.export()
        self.assertEqual(set(output), {"schema_version", "team_name", "model", "results"})
        self.assertEqual(len(output["results"]), 10)
        row = output["results"][0]
        self.assertEqual([c["criterion_id"] for c in row["criteria"]], list(CRITERION_CODES.values()))
        self.assertIs(row["criteria"][0]["value"], True)
        self.assertIs(row["criteria"][-1]["value"], False)
        self.assertEqual(row["criteria"][0]["evidence_turns"], [4])  # Includes bot and system turns.
        self.assertIsInstance(row["score"]["final_score"], float)
        self.assertEqual(row["score"]["final_score"], 100.0)
        self.assertNotIn("PRIVATE", json.dumps(output))
        self.assertEqual(before, {str(p): p.read_bytes() for p in self.directory.rglob("*.json")})

    def test_na_global_rounding_and_flag_does_not_zero_score(self):
        self.complete()
        assessment = self.assessment(0)
        for index in (1, 2):
            assessment["criteria"][index].update(answer="na", na_reason="Invented permitted N/A condition.")
        assessment["criteria"][5]["answer"] = 7
        assessment["criteria"][7].update(answer="yes", violations=[{
            "type": "off_company_paid_service", "reason": "Invented violation, not a real dialogue.",
            "evidence_message_ids": ["m006"]}])
        self.write(0, assessment=assessment)
        row = self.export()["results"][0]
        self.assertIsNone(row["criteria"][1]["value"])
        self.assertIsNone(row["criteria"][2]["value"])
        self.assertIn("N/A:", row["criteria"][1]["rationale"])
        self.assertEqual(row["score"]["final_score"], 93.08)  # 60.5 / 65, ROUND_HALF_UP.
        self.assertIs(row["score"]["critical_flag"], True)
        self.assertIs(row["criteria"][-1]["value"], True)
        self.assertEqual(row["criteria"][-1]["evidence_turns"], [4, 6])

    def test_all_permitted_na_fields_and_range_expansion_stable_union(self):
        self.complete()
        assessment = self.assessment(0)
        for index in (1, 2, 3, 6):
            assessment["criteria"][index].update(answer="na", na_reason="Invented applicability condition.")
        assessment["criteria"][0].update(answer="no", evidence_message_ids=["m006", "m002"],
            checked_range={"start_message_id": "m002", "end_message_id": "m004"})
        self.write(0, assessment=assessment)
        row = self.export()["results"][0]
        self.assertEqual(row["criteria"][0]["evidence_turns"], [6, 2, 3, 4])
        self.assertEqual([c["criterion_id"] for c in row["criteria"] if c["value"] is None],
                         ["discovery", "identification", "consultation", "closure"])

    def test_failed_interrupted_and_unresolved_keep_ids_as_errors(self):
        self.complete()
        # Replace selected histories with each real durable failure state.
        import shutil
        for index in (0, 1, 2):
            shutil.rmtree(self.directory / "records" / f"{index+1:04d}")
        self.write(0, "error")
        self.write(1, "interrupted")
        assessment = self.assessment(2)
        assessment["criteria"][0].update(status="needs_review", answer=None,
            uncertainty="Invented unresolved question.", evidence_message_ids=[])
        self.write(2, assessment=assessment)
        output = self.export()
        self.assertEqual([r["status"] for r in output["results"][:3]], ["error"] * 3)
        for index, row in enumerate(output["results"][:3]):
            self.assertEqual(row["conversation_id"], self.records[index]["conversation_id"])
            self.assertEqual(row["criteria"], [])
            self.assertIsNone(row["score"])
        self.assertNotIn("PRIVATE PROVIDER", json.dumps(output))

    def test_pending_requires_explicit_opt_in_and_preserves_exact_coverage(self):
        self.write(0)
        with self.assertRaisesRegex(InputError, "allow_unprocessed"):
            self.export()
        output = self.export(allow_unprocessed=True)
        self.assertEqual(len(output["results"]), 10)
        self.assertEqual(sum(r["status"] == "error" for r in output["results"]), 9)
        self.assertEqual([r["conversation_id"] for r in output["results"]], [r["conversation_id"] for r in self.records])

    def test_canonical_model_name_can_use_explicit_verified_served_alias(self):
        self.complete()
        output = self.export(model_name="Invented/CanonicalModel", served_model_name=self.model)
        self.assertEqual(output["model"], {"mode": "local", "name": "Invented/CanonicalModel"})
        with self.assertRaises(InputError):
            self.export(model_name="wrong-model")
        folder = self.directory / "records/0001/attempt-0001/result.json"
        saved = read_json(folder)
        saved["metadata"]["model"] = "another-actual-model"
        atomic_write(folder, saved)
        with self.assertRaisesRegex(InputError, "actual saved model"):
            self.export()

    def test_source_manifest_extra_ids_and_custom_checklists_rejected(self):
        self.complete()
        for kind in ("order", "source", "manifest_id", "extra_dir", "weight", "instruction", "extra_criterion"):
            with self.subTest(kind=kind):
                records, checklist = deepcopy(self.records), deepcopy(self.checklist)
                manifest = read_json(self.directory / "manifest.json")
                if kind == "order": records.reverse()
                if kind == "source": records[0]["transcript"][0]["text"] += " changed"
                if kind == "manifest_id":
                    manifest["preflight"]["items"][0]["conversation_id"] = "unexpected-id"
                    atomic_write(self.directory / "manifest.json", manifest)
                if kind == "extra_dir": (self.directory / "records/9999").mkdir()
                if kind == "weight": checklist["criteria"][0]["weight"] += 1
                if kind == "instruction": checklist["criteria"][0]["instruction"] += " changed"
                if kind == "extra_criterion": checklist["criteria"].append(dict(checklist["criteria"][0], id="09"))
                with self.assertRaises(InputError):
                    export_submission(records, checklist, self.directory, "Team", self.model, "local", "prod")
                if kind == "manifest_id": self.write_manifest()
                if kind == "extra_dir": (self.directory / "records/9999").rmdir()

    def test_saved_response_score_flag_and_attempt_identity_revalidated(self):
        self.complete()
        path = self.directory / "records/0001/attempt-0001/result.json"
        original = read_json(path)
        for kind in ("score", "flag", "answer", "evidence", "metadata", "snapshot"):
            with self.subTest(kind=kind):
                saved = deepcopy(original)
                if kind == "score": saved["calculation"]["total_score"] = "0.00"
                if kind == "flag": saved["critical_error"] = True
                if kind == "answer": saved["assessment"]["criteria"][5]["answer"] = 8
                if kind == "evidence": saved["assessment"]["criteria"][0]["evidence_message_ids"] = ["m999"]
                if kind == "metadata": saved["metadata"]["request_hash"] = "different"
                if kind == "snapshot": saved["checklist_snapshot"]["version"] = "2"
                atomic_write(path, saved)
                with self.assertRaises(InputError): self.export()
        atomic_write(path, original)
        self.write(0, attempt=2)
        with self.assertRaisesRegex(InputError, "Multiple successful"):
            self.export()

    def test_output_schema_types_duplicates_score_precision_and_coverage(self):
        self.complete()
        original = self.export()
        for kind in ("boolean_as_integer", "integer_as_boolean", "invalid_scale", "illegal_na", "duplicate_criterion",
                     "duplicate_id", "extra_id", "missing_id", "score_string", "score_precision", "score_arithmetic", "flag_mismatch", "evidence_zero", "evidence_outside", "extra_field"):
            with self.subTest(kind=kind):
                output = deepcopy(original)
                row = output["results"][0]
                if kind == "boolean_as_integer": row["criteria"][0]["value"] = 1
                if kind == "integer_as_boolean": row["criteria"][3]["value"] = True
                if kind == "invalid_scale": row["criteria"][5]["value"] = 8
                if kind == "illegal_na": row["criteria"][0]["value"] = None
                if kind == "duplicate_criterion": row["criteria"][1] = deepcopy(row["criteria"][0])
                if kind == "duplicate_id": output["results"][1]["conversation_id"] = row["conversation_id"]
                if kind == "extra_id": output["results"][1]["conversation_id"] = "unknown"
                if kind == "missing_id": output["results"].pop()
                if kind == "score_string": row["score"]["final_score"] = "100.00"
                if kind == "score_precision": row["score"]["final_score"] = 99.999
                if kind == "score_arithmetic": row["score"]["final_score"] = 99.99
                if kind == "flag_mismatch": row["score"]["critical_flag"] = True
                if kind == "evidence_zero": row["criteria"][0]["evidence_turns"] = [0]
                if kind == "evidence_outside": row["criteria"][0]["evidence_turns"] = [7]
                if kind == "extra_field": row["audio_url"] = "not allowed"
                with self.assertRaises(InputError): validate_submission(output, self.records, "prod")

    def test_prod_exact_ten_and_dev_all_65_with_five_calls(self):
        for size in (9, 11):
            records = deepcopy(self.records[:9] if size == 9 else self.records + [dict(self.records[0], conversation_id="eleventh")])
            with self.assertRaisesRegex(InputError, "exactly 10"):
                export_submission(records, self.checklist, self.directory, "Team", self.model, "local", "prod", True)
        self.records = [dict(deepcopy(self.records[0]), conversation_id=f"synthetic-dev-{i}",
                             channel="agent chat" if i < 60 else "voice call") for i in range(65)]
        self.items = [{"conversation_id": r["conversation_id"], "dialogue_hash": fingerprint(r),
                       "prompt_hash": fingerprint(["prompt", i]), "request_hash": fingerprint(["request", i])}
                      for i, r in enumerate(self.records)]
        self.write_manifest()
        output = self.export(dataset="dev", allow_unprocessed=True)
        self.assertEqual(len(output["results"]), 65)
        self.records[-1]["channel"] = "agent chat"
        with self.assertRaisesRegex(InputError, "channel counts"):
            self.export(dataset="dev", allow_unprocessed=True)

    def test_source_recording_link_is_not_copied_from_rationale(self):
        self.records[0]["audio_url"] = "https://private.example/recording?token=private"
        self.items[0]["dialogue_hash"] = fingerprint(self.records[0])
        self.write_manifest()
        self.complete()
        assessment = self.assessment(0)
        assessment["criteria"][0]["reason"] = "Faithful quoted explanation. " + self.records[0]["audio_url"]
        self.write(0, assessment=assessment)
        output = self.export()
        self.assertNotIn(self.records[0]["audio_url"], json.dumps(output))
        self.assertIn("Faithful quoted explanation.", output["results"][0]["criteria"][0]["rationale"])

    def test_real_batch_writer_contract_exports_with_mocked_provider_only(self):
        from cqa_eval.batch import run_batch
        from cqa_eval.evaluator import Settings
        target = Path(self.temp.name) / "actual-batch-writer"
        settings = Settings(api_key="invented-not-a-secret", base_url="https://example.test/v1",
            model=self.model, api_style="chat_completions", max_output_tokens=1024, max_attempts=1)

        def response(url, body, settings):
            cid = json.loads(body["messages"][1]["content"])["conversation"]["conversation_id"]
            index = next(i for i, record in enumerate(self.records) if record["conversation_id"] == cid)
            return {"model": self.model, "choices": [{"finish_reason": "stop", "message": {
                "content": json.dumps(self.assessment(index))}}]}

        with patch("cqa_eval.evaluator._http_post", side_effect=response) as post:
            status = run_batch(self.records, self.checklist, settings, target,
                               context_window=10000, token_counter=lambda prepared: 100)
            self.assertEqual(post.call_count, 10)
            post.reset_mock()
            output = export_submission(self.records, self.checklist, target,
                                       "Invented team", self.model, "local", "prod")
            post.assert_not_called()
        self.assertTrue(status["complete"])
        self.assertTrue(all(row["status"] == "ok" for row in output["results"]))

    def test_malformed_artifacts_and_error_identity_fail_closed(self):
        self.complete()
        path = self.directory / "records/0001/attempt-0001/result.json"
        original = read_json(path)
        atomic_write(path, {"metadata": original["metadata"]})
        with self.assertRaisesRegex(InputError, "incomplete or malformed"):
            self.export()
        atomic_write(path, original)
        saved = deepcopy(original)
        saved["critical_error"] = 0
        atomic_write(path, saved)
        with self.assertRaisesRegex(InputError, "critical_error"):
            self.export()
        atomic_write(path, original)
        self.write(1, "error", attempt=2)
        error_path = self.directory / "records/0002/attempt-0002/error.json"
        error = read_json(error_path)
        error["conversation_id"] = "another-case"
        atomic_write(error_path, error)
        with self.assertRaisesRegex(InputError, "Saved error conversation ID"):
            self.export()

    def cli_args(self, destination=None):
        source = Path(self.temp.name) / "synthetic-input.json"
        atomic_write(source, {"isSuccess": True, "data": {"records": self.records}})
        args = ["export-results", "--dataset", "prod", "--input", str(source),
            "--checklist", str(ROOT / "codex_context/checklist.json"),
            "--run-dir", str(self.directory), "--team-name", "Вымышленная команда",
            "--model-name", "Invented/CanonicalModel", "--served-model-name", self.model,
            "--model-mode", "local"]
        return args + (["--out", str(destination)] if destination is not None else [])

    def test_cli_writes_official_json_without_environment_or_model_calls(self):
        from cqa_eval.cli import main
        self.complete()
        destination = Path(self.temp.name) / "prepared/results.json"
        with patch("cqa_eval.cli.Settings.from_env", side_effect=AssertionError("Export must be offline")) as env, \
                patch("cqa_eval.evaluator._http_post") as post, \
                patch("cqa_eval.cli.load_token_counter") as tokenizer, \
                redirect_stdout(io.StringIO()) as stream:
            code = main(self.cli_args(destination))
        self.assertEqual(code, 0)
        env.assert_not_called()
        post.assert_not_called()
        tokenizer.assert_not_called()
        summary = json.loads(stream.getvalue())
        self.assertEqual(summary["exported"], 10)
        self.assertEqual(summary["errors"], 0)
        self.assertEqual(summary["schema_version"], "1.0")
        self.assertNotIn("results", summary)
        output = read_json(destination)
        validate_submission(output, self.records, "prod")
        self.assertEqual(output["team_name"], "Вымышленная команда")
        self.assertEqual(output["model"]["name"], "Invented/CanonicalModel")
        if os.name != "nt":
            self.assertEqual(destination.stat().st_mode & 0o777, 0o600)

    def test_cli_default_destination_and_existing_file_is_never_overwritten(self):
        from cqa_eval.cli import main
        self.complete()
        args = self.cli_args()
        with chdir(self.temp.name), redirect_stdout(io.StringIO()):
            self.assertEqual(main(args), 0)
        destination = Path(self.temp.name) / "results/prod/results.json"
        self.assertTrue(destination.is_file())
        before = destination.read_bytes()
        with chdir(self.temp.name), patch("cqa_eval.submission.export_submission") as exporter, \
                patch("cqa_eval.evaluator._http_post") as post, redirect_stdout(io.StringIO()) as stream:
            self.assertEqual(main(args), 2)
        self.assertIn("already exists", json.loads(stream.getvalue())["error"])
        exporter.assert_not_called()
        post.assert_not_called()
        self.assertEqual(destination.read_bytes(), before)

    def test_cli_prod_requires_explicit_input_and_never_exports_default_dev(self):
        from cqa_eval.cli import main
        context = Path(self.temp.name) / "synthetic-context"
        atomic_write(context / "checklist.json", self.checklist)
        atomic_write(context / "data/dev.json", {"isSuccess": True, "data": {"records": self.records}})
        destination = Path(self.temp.name) / "must-not-exist.json"
        args = ["--context", str(context), "export-results", "--dataset", "prod",
            "--run-dir", str(self.directory), "--team-name", "Invented team",
            "--model-name", self.model, "--out", str(destination)]
        with patch("cqa_eval.cli.Settings.from_env") as env, \
                patch("cqa_eval.submission.export_submission") as exporter, \
                patch("cqa_eval.evaluator._http_post") as post, redirect_stdout(io.StringIO()) as stream:
            self.assertEqual(main(args), 2)
        self.assertIn("explicit --input", json.loads(stream.getvalue())["error"])
        exporter.assert_not_called()
        env.assert_not_called()
        post.assert_not_called()
        self.assertFalse(destination.exists())

    def recovery(self, failed=(0, 3, 7), recovery_errors=()):
        """Real durable writer, invented provider responses, no external calls."""
        from cqa_eval.batch import run_batch
        from cqa_eval.evaluator import Settings
        for index in range(len(self.records)):
            self.write(index, "error" if index in failed else "ok")
        target = Path(self.temp.name) / "recovery"
        selected = [self.records[index] for index in failed]
        settings = Settings(api_key="invented-not-a-secret", base_url="https://example.test/v1",
            model=self.model, api_style="chat_completions", max_output_tokens=1024, max_attempts=1)

        def response(url, body, settings):
            cid = json.loads(body["messages"][1]["content"])["conversation"]["conversation_id"]
            index = next(i for i, row in enumerate(self.records) if row["conversation_id"] == cid)
            assessment = self.assessment(index)
            if index in recovery_errors:
                # A complete JSON response deliberately rejected by the actual validator.
                assessment["criteria"][-1]["evidence_message_ids"] = []
            return {"model": self.model, "choices": [{"finish_reason": "stop", "message": {
                "content": json.dumps(assessment)}}]}

        with patch("cqa_eval.evaluator._http_post", side_effect=response) as post:
            run_batch(selected, self.checklist, settings, target, context_window=10000,
                      token_counter=lambda prepared: 100)
        self.assertEqual(post.call_count, len(failed))
        return target

    def test_sparse_recovery_preserves_successes_and_separate_provenance(self):
        recovery = self.recovery()
        before = {str(p): p.read_bytes() for root in (self.directory, recovery) for p in root.rglob("*.json")}
        original = self.export()
        with patch("cqa_eval.evaluator._http_post") as post:
            output, audit = export_submission_with_provenance(self.records, self.checklist,
                self.directory, "Invented team", self.model, "local", "prod", recovery_run_dir=recovery)
            post.assert_not_called()
        self.assertEqual(sum(row["status"] == "error" for row in output["results"]), 0)
        for i in range(10):
            if i not in (0, 3, 7):
                self.assertEqual(output["results"][i], original["results"][i])
        self.assertEqual([row["conversation_id"] for row in audit["results"] if row["source"] == "recovery"],
                         [self.records[i]["conversation_id"] for i in (0, 3, 7)])
        self.assertEqual(audit["submission_hash"], fingerprint(output))
        self.assertEqual(set(audit["runs"]), {"baseline", "recovery"})
        self.assertTrue(audit["runs"]["recovery"]["artifact_sha256"])
        self.assertNotIn("PRIVATE", json.dumps(audit))
        self.assertEqual(before, {str(p): p.read_bytes() for root in (self.directory, recovery) for p in root.rglob("*.json")})

    def test_failed_recovery_stays_explicit_error_without_zero_or_na(self):
        recovery = self.recovery(recovery_errors=(3,))
        output = self.export(recovery_run_dir=recovery)
        errors = [row for row in output["results"] if row["status"] == "error"]
        self.assertEqual(len(errors), 1)
        self.assertEqual(errors[0]["conversation_id"], self.records[3]["conversation_id"])
        self.assertEqual(errors[0]["criteria"], [])
        self.assertIsNone(errors[0]["score"])

    def test_recovery_rejects_wrong_coverage_data_model_and_multiple_attempts(self):
        recovery = self.recovery()
        path = recovery / "manifest.json"
        original = read_json(path)
        for kind in ("success_id", "missing_id", "order", "data", "model", "retry_limit"):
            with self.subTest(kind=kind):
                value = deepcopy(original)
                if kind == "success_id": value["preflight"]["items"][0] = deepcopy(self.items[1])
                if kind == "missing_id":
                    value["preflight"]["items"].pop()
                    value["preflight"]["records"] -= 1
                if kind == "order": value["preflight"]["items"].reverse()
                if kind == "data": value["preflight"]["items"][0]["dialogue_hash"] = "0" * 64
                if kind == "model": value["model"] = "different-model"
                if kind == "retry_limit": value["max_attempts"] = 2
                atomic_write(path, value)
                with self.assertRaises(InputError): self.export(recovery_run_dir=recovery)
        atomic_write(path, original)
        import shutil
        shutil.copytree(recovery / "records/0001/attempt-0001", recovery / "records/0001/attempt-0002")
        with self.assertRaisesRegex(InputError, "one fresh attempt"):
            self.export(recovery_run_dir=recovery)
        shutil.rmtree(recovery / "records/0001/attempt-0001")
        with self.assertRaisesRegex(InputError, "selected later attempt"):
            self.export(recovery_run_dir=recovery)

    def test_recovery_never_accepts_manual_json_repair_or_mixed_prompt(self):
        recovery = self.recovery()
        path = recovery / "records/0001/attempt-0001/result.json"
        original = read_json(path)
        saved = deepcopy(original)
        saved["assessment"]["criteria"][0]["reason"] = "Manual repair must not replace the original model JSON."
        atomic_write(path, saved)
        with self.assertRaisesRegex(InputError, "original provider response"):
            self.export(recovery_run_dir=recovery)
        atomic_write(path, original)
        saved = deepcopy(original)
        saved["metadata"]["instruction_hash"] = "0" * 64
        atomic_write(path, saved)
        with self.assertRaisesRegex(InputError, "mixes instruction"):
            self.export(recovery_run_dir=recovery)

    def test_recovery_rejects_missing_or_inconsistent_journals_and_calls(self):
        recovery = self.recovery()
        path = recovery / "records/0001/attempt-0001/result.json"
        original = read_json(path)
        for field, value in (("call_count", 2), ("retry_count", 1), ("instruction_hash", None)):
            with self.subTest(field=field):
                saved = deepcopy(original)
                saved["metadata"][field] = value
                atomic_write(path, saved)
                with self.assertRaises(InputError): self.export(recovery_run_dir=recovery)
        atomic_write(path, original)
        journal = recovery / "records/0001/attempt-0001/response_received-01.json"
        journal.unlink()
        with self.assertRaisesRegex(InputError, "original provider response journal"):
            self.export(recovery_run_dir=recovery)

    def test_cli_recovery_requires_private_audit_and_writes_new_files_only(self):
        from cqa_eval.cli import main
        recovery = self.recovery()
        destination = Path(self.temp.name) / "export/results.json"
        audit = Path(self.temp.name) / "private/provenance.json"
        args = self.cli_args(destination) + ["--recovery-run-dir", str(recovery)]
        with redirect_stdout(io.StringIO()) as stream:
            self.assertEqual(main(args), 2)
        self.assertIn("--provenance-out", json.loads(stream.getvalue())["error"])
        self.assertFalse(destination.exists())
        with patch("cqa_eval.evaluator._http_post") as post, redirect_stdout(io.StringIO()):
            self.assertEqual(main(args + ["--provenance-out", str(audit)]), 0)
            post.assert_not_called()
        self.assertEqual(read_json(audit)["submission_hash"], fingerprint(read_json(destination)))
        before = destination.read_bytes(), audit.read_bytes()
        with redirect_stdout(io.StringIO()):
            self.assertEqual(main(args + ["--provenance-out", str(audit)]), 2)
        self.assertEqual(before, (destination.read_bytes(), audit.read_bytes()))
        if os.name != "nt":self.assertEqual(audit.stat().st_mode & 0o777, 0o600)

    def test_interrupted_or_pending_recovery_is_never_a_score(self):
        recovery = self.recovery()
        folder = recovery / "records/0001/attempt-0001"
        for name in ("result.json", "response_received-01.json", "attempt_finished-01.json"):
            (folder / name).unlink()
        output, audit = export_submission_with_provenance(self.records, self.checklist,
            self.directory, "Invented team", self.model, "local", "prod", recovery_run_dir=recovery)
        self.assertEqual(output["results"][0]["status"], "error")
        self.assertIsNone(output["results"][0]["score"])
        self.assertEqual(audit["results"][0]["state"], "interrupted")
        self.assertIsNone(audit["results"][0]["api_calls_in_selected_attempt"])
        (folder / "attempt_started-01.json").unlink()
        output = self.export(recovery_run_dir=recovery)
        self.assertEqual(output["results"][0]["status"], "error")
        import shutil
        shutil.rmtree(folder)
        with self.assertRaisesRegex(InputError, "allow_unprocessed"):
            self.export(recovery_run_dir=recovery)
        output = self.export(recovery_run_dir=recovery, allow_unprocessed=True)
        self.assertEqual(output["results"][0]["status"], "error")
        self.assertIsNone(output["results"][0]["score"])

    def test_recovery_cannot_replace_an_already_successful_source(self):
        self.complete()
        with self.assertRaisesRegex(InputError, "separate directories"):
            self.export(recovery_run_dir=self.directory)
        target = Path(self.temp.name) / "unnecessary-recovery"
        atomic_write(target / "manifest.json", {})
        with self.assertRaisesRegex(InputError, "successes cannot be replaced"):
            self.export(recovery_run_dir=target)

    def test_cli_export_cannot_write_into_immutable_run(self):
        from cqa_eval.cli import main
        recovery = self.recovery()
        destination = Path(self.temp.name) / "export/results.json"
        for audit in (recovery / "new-audit.json", self.directory / "new-audit.json", destination):
            with self.subTest(path=audit), redirect_stdout(io.StringIO()):
                self.assertEqual(main(self.cli_args(destination) + ["--recovery-run-dir", str(recovery),
                    "--provenance-out", str(audit)]), 2)
                self.assertFalse(destination.exists())


if __name__ == "__main__":
    unittest.main()
