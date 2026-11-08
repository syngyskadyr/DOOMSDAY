"""Export the real ML collection contract; responses/transcripts are synthetic."""
from contextlib import chdir, redirect_stdout
from copy import deepcopy
import io
import json
from pathlib import Path
import subprocess
import unittest
from unittest.mock import patch

from cqa_eval.batch import atomic_write, collect_results, run_batch, run_lock
from cqa_eval.cli import main
from cqa_eval.data import InputError, fingerprint, read_json
from cqa_eval.evaluator import Settings
from cqa_eval.submission import _snapshot, export_collected_submission, validate_submission
import test_submission as fixtures


class CollectedSubmissionTests(unittest.TestCase):
    def setUp(self):
        self.fixture = fixtures.SubmissionTests(methodName="runTest")
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.fixture.complete()

    def bundle(self):
        f = self.fixture
        return collect_results(f.records, f.checklist, f.directory)

    def export(self, bundle=None, records=None, **kwargs):
        f = self.fixture
        with patch("cqa_eval.evaluator._http_post") as post:
            result = export_collected_submission(f.records if records is None else records, f.checklist,
                self.bundle() if bundle is None else bundle, "Synthetic team", f.model, "local", "prod", **kwargs)
            post.assert_not_called()
        return result

    def test_actual_collect_mapping_and_no_source_path_access(self):
        bundle = self.bundle()
        bundle["results"].reverse()  # Source order is restored from the original dataset.
        for row in bundle["summary"]["items"]:
            row["result"] = "unavailable-private-path/result.json"
        before = deepcopy(bundle)
        result = self.export(bundle)
        self.assertEqual(bundle, before)
        self.assertEqual(result, self.fixture.export(team_name="Synthetic team"))
        self.assertIs(type(result["results"][0]["score"]["final_score"]), float)
        self.assertNotIn("PRIVATE EXTRACTED", json.dumps(result))
        self.assertNotIn("unavailable-private-path", json.dumps(result))

    def test_na_scales_and_original_turn_numbers_match_pdf_example(self):
        f = self.fixture
        assessment = f.assessment(0)
        for index in (1, 2):
            assessment["criteria"][index].update(answer="na", na_reason="Synthetic applicability reason.",
                evidence_message_ids=["m001"], checked_range={"start_message_id": "m001", "end_message_id": "m003"})
        assessment["criteria"][3]["answer"] = 15
        assessment["criteria"][5]["answer"] = 7
        f.write(0, assessment=assessment)
        row = self.export()["results"][0]
        self.assertEqual(row["score"], {"final_score": 85.38, "critical_flag": False})
        self.assertEqual([r["criterion_id"] for r in row["criteria"]],
                         ["contact", "discovery", "identification", "consultation", "empathy", "dialogue", "closure", "critical"])
        self.assertIsNone(row["criteria"][1]["value"])
        self.assertIsNone(row["criteria"][2]["value"])
        self.assertEqual(row["criteria"][1]["evidence_turns"], [1, 2, 3])
        self.assertEqual(row["criteria"][0]["evidence_turns"], [4])
        self.assertEqual(row["criteria"][3]["value"], 15)
        self.assertEqual(row["criteria"][5]["value"], 7)
        self.assertIn("Synthetic applicability reason.", row["criteria"][1]["rationale"])

    def test_remaining_na_criteria_and_all_scale_steps(self):
        f = self.fixture
        for index, values in ((3, [0, 5, 10, 15, 20, "na"]), (5, [0, 4, 7, 10]), (6, ["na"])):
            for value in values:
                with self.subTest(index=index, value=value):
                    assessment = f.assessment(0)
                    assessment["criteria"][index].update(answer=value, na_reason="Synthetic N/A condition" if value == "na" else None)
                    f.write(0, assessment=assessment)
                    row = self.export()["results"][0]["criteria"][index]
                    self.assertEqual(row["value"], None if value == "na" else value)

    def test_critical_flag_does_not_zero_score(self):
        f = self.fixture
        assessment = f.assessment(0)
        assessment["criteria"][-1].update(answer="yes", violations=[{
            "type": "insult", "reason": "Synthetic violation", "evidence_message_ids": ["m004"]}])
        f.write(0, assessment=assessment)
        row = self.export()["results"][0]
        self.assertEqual(row["score"], {"final_score": 100.0, "critical_flag": True})
        self.assertIs(row["criteria"][-1]["value"], True)

    def test_missing_duplicate_extra_ids_are_rejected_not_fabricated_errors(self):
        original = self.bundle()
        for kind in ("missing", "duplicate", "unknown", "summary_duplicate"):
            bundle = deepcopy(original)
            if kind == "missing": bundle["results"].pop()
            if kind == "duplicate": bundle["results"][1] = deepcopy(bundle["results"][0])
            if kind == "unknown": bundle["results"][0]["assessment"]["conversation_id"] = "unknown"
            if kind == "summary_duplicate": bundle["summary"]["items"][1] = deepcopy(bundle["summary"]["items"][0])
            with self.subTest(kind=kind), self.assertRaises(InputError): self.export(bundle)
        records = deepcopy(self.fixture.records)
        records[1] = deepcopy(records[0])
        with self.assertRaises(InputError): self.export(original, records=records)

    def test_tampered_wrappers_headers_and_summary_rejected(self):
        original = self.bundle()
        changes = [lambda b: b.update(format="made-up"), lambda b: b.update(manifest_hash="missing"),
                   lambda b: b["summary"].update(complete=False),
                   lambda b: b["summary"]["items"][0].update(total_score="0.00"),
                   lambda b: b["results"][0]["calculation"].update(total_score="0.00"),
                   lambda b: b["results"][0].update(critical_error=True),
                   lambda b: b["results"][0]["metadata"].update(dialogue_hash="0"*64),
                   lambda b: b["results"][0]["metadata"].update(model="wrong-model"),
                   lambda b: b["results"][0]["checklist_snapshot"].update(version="changed"),
                   lambda b: b["results"][0]["assessment"]["criteria"][0].update(answer=1),
                   lambda b: b["results"][0]["assessment"]["criteria"][3].update(answer=True),
                   lambda b: b["results"][0]["assessment"]["criteria"][5].update(answer=8),
                   lambda b: b["results"][0]["assessment"]["criteria"][0].update(evidence_message_ids=["m999"])]
        for change in changes:
            bundle = deepcopy(original)
            change(bundle)
            with self.subTest(change=change), self.assertRaises(InputError): self.export(bundle)

    def test_unresolved_error_interrupted_pending_are_exported_via_run(self):
        f = self.fixture
        # Remove only our synthetic success fixtures, never real artifacts.
        for index, state in ((0, "error"), (1, "interrupted"), (2, "pending")):
            folder = f.directory / "records" / f"{index+1:04d}" / "attempt-0001"
            (folder / "result.json").unlink()
            if state != "pending": f.write(index, state=state)
            else:
                (folder / "started.json").unlink()
                folder.rmdir()
        assessment = f.assessment(3)
        assessment["criteria"][0].update(status="needs_review", answer=None, uncertainty="Synthetic missing basis")
        f.write(3, assessment=assessment)
        with self.assertRaises(InputError): self.bundle()
        output = f.export(allow_unprocessed=True)
        for row in output["results"][:4]:
            self.assertEqual(row["status"], "error")
            self.assertEqual(row["criteria"], [])
            self.assertIsNone(row["score"])
            self.assertTrue(row["error"])

    def test_real_batch_collect_export_for_65_dev_and_10_prod(self):
        f = self.fixture
        for dataset, size in (("dev", 65), ("prod", 10)):
            with self.subTest(dataset=dataset):
                records = [dict(deepcopy(f.records[0]), conversation_id=f"synthetic-{dataset}-{i}",
                               channel="voice call" if dataset == "dev" and i >= 60 else "agent chat") for i in range(size)]
                target = Path(f.temp.name) / dataset
                settings = Settings(api_key="synthetic-test", base_url="https://example.test/v1", model=f.model,
                                    api_style="chat_completions", max_output_tokens=1024, max_attempts=1)
                def response(url, body, settings):
                    assessment = f.assessment(0)
                    assessment["conversation_id"] = json.loads(body["messages"][1]["content"])["conversation"]["conversation_id"]
                    return {"model": f.model, "choices": [{"finish_reason": "stop", "message": {"content": json.dumps(assessment)}}]}
                with patch("cqa_eval.evaluator._http_post", side_effect=response) as post:
                    run_batch(records, f.checklist, settings, target, context_window=10000, token_counter=lambda p: 100)
                    self.assertEqual(post.call_count, size)
                    post.reset_mock()
                    bundle = collect_results(records, f.checklist, target)
                    output = export_collected_submission(records, f.checklist, bundle, "Synthetic", f.model, "local", dataset)
                    post.assert_not_called()
                self.assertEqual(len(output["results"]), size)
                validate_submission(output, records, dataset)

    def test_cli_collected_default_prod_path_and_no_overwrite(self):
        f = self.fixture
        source = Path(f.temp.name) / "prod.json"
        collected = Path(f.temp.name) / "collected.json"
        atomic_write(source, {"isSuccess": True, "data": {"records": f.records}})
        atomic_write(collected, self.bundle())
        original = collected.read_bytes()
        args = ["--context", str(fixtures.ROOT / "codex_context"), "export-results", "--dataset", "prod",
                "--input", str(source), "--collected", str(collected), "--team-name", "Synthetic", "--model-name", f.model]
        with chdir(f.temp.name), redirect_stdout(io.StringIO()), patch("cqa_eval.evaluator._http_post") as post:
            self.assertEqual(main(args), 0)
            output = Path("results/prod/results.json")
            before = output.read_bytes()
            self.assertEqual(main(args), 2)
            self.assertEqual(before, output.read_bytes())
            post.assert_not_called()
        self.assertEqual(original, collected.read_bytes())

    def test_official_paths_not_ignored_private_artifacts_still_ignored(self):
        for path in ("results/dev/results.json", "results/prod/results.json"):
            proc = subprocess.run(["git", "check-ignore", "--no-index", "--quiet", path], cwd=fixtures.ROOT)
            self.assertEqual(proc.returncode, 1, path)
        for path in ("results/dev/private.json", "artifacts/private.json", "codex_context/data/dev.json", ".env"):
            proc = subprocess.run(["git", "check-ignore", "--no-index", "--quiet", path], cwd=fixtures.ROOT)
            self.assertEqual(proc.returncode, 0, path)

    def test_snapshot_excludes_only_coordination_lock_while_holding_lock(self):
        f = self.fixture
        with run_lock(f.directory):
            snapshot = _snapshot(f.directory)
        self.assertNotIn(".lock", snapshot)
        self.assertIn("manifest.json", snapshot)
        self.assertEqual(len(snapshot), 21)  # Manifest + ten starts + ten results.

    def test_validate_results_cli_uses_source_coverage_without_writing_submission(self):
        f = self.fixture
        source = Path(f.temp.name) / "source.json"
        target = Path(f.temp.name) / "official.json"
        atomic_write(source, {"isSuccess": True, "data": {"records": f.records}})
        atomic_write(target, self.export())
        before = target.read_bytes()
        args = ["--context", str(fixtures.ROOT / "codex_context"), "validate-results", "--dataset", "prod",
                "--input", str(source), "--response", str(target)]
        with redirect_stdout(io.StringIO()) as output, patch("cqa_eval.evaluator._http_post") as post:
            self.assertEqual(main(args), 0)
            self.assertEqual(json.loads(output.getvalue())["ok"], 10)
            post.assert_not_called()
        self.assertEqual(before, target.read_bytes())
        with redirect_stdout(io.StringIO()):
            self.assertEqual(main(args + ["--out", str(target)]), 2)
        self.assertEqual(before, target.read_bytes())
        corrupted = read_json(target)
        corrupted["results"].pop()
        atomic_write(target, corrupted)
        with redirect_stdout(io.StringIO()): self.assertEqual(main(args), 2)

    def test_served_alias_must_match_recorded_model_not_a_documentation_default(self):
        f = self.fixture
        for alias in ("qwen3-14b-awq", "doomsday-qwen3-14b-awq"):
            with self.subTest(alias=alias):
                f.model = alias
                f.write_manifest()
                f.complete()
                bundle = self.bundle()
                result = export_collected_submission(f.records, f.checklist, bundle, "Synthetic", "Qwen/Qwen3-14B-AWQ",
                                                      "local", "prod", served_model_name=alias)
                self.assertEqual(result["model"]["name"], "Qwen/Qwen3-14B-AWQ")
                with self.assertRaises(InputError):
                    export_collected_submission(f.records, f.checklist, bundle, "Synthetic", "Qwen/Qwen3-14B-AWQ",
                                                "local", "prod", served_model_name="different-alias")


if __name__ == "__main__":
    unittest.main()
