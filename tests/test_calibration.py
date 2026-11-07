"""Offline dataset/form integrity tests, not tests of model accuracy."""
from collections import Counter
from contextlib import redirect_stderr, redirect_stdout
from copy import deepcopy
import io
from pathlib import Path
import tempfile
import unittest

from cqa_eval.data import InputError, load_dev, read_json
from cqa_eval.quality import compare_predictions
from scripts.prepare_calibration import main, prepare, split_form

ROOT = Path(__file__).resolve().parents[1]
CONTEXT = ROOT / "codex_context"


class CalibrationTests(unittest.TestCase):
    def setUp(self):
        self.manifest = read_json(CONTEXT / "eval/calibration_15.json")
        self.checklist = read_json(CONTEXT / "checklist.json")
        self.form = prepare(CONTEXT / "data/dev.json", self.checklist, self.manifest)

    def test_blind_form_balanced_complete_and_unlabeled(self):
        self.assertEqual(Counter(c["language"] for c in self.form["cases"]), {"RU": 5, "KZ": 5, "EN": 5})
        self.assertEqual(Counter(c["split"] for c in self.form["cases"]), {"development": 12, "reserved": 3})
        for case in self.form["cases"]:
            self.assertNotIn("selection_focus", case)
            self.assertNotIn("group", case)
            self.assertNotIn("transcript", case)
            self.assertEqual(case["annotation_status"], "unreviewed")
            self.assertIsNone(case["reviewer"])
            self.assertEqual({r["id"] for r in case["criteria"]}, {c["id"] for c in self.checklist["criteria"]})
            self.assertTrue(all(r["answer"] is None and not r["reason"] for r in case["criteria"]))
        result = compare_predictions(load_dev(CONTEXT / "data/dev.json"), self.checklist, self.form, [])
        self.assertEqual(result["requested"], 15)
        self.assertEqual(result["reviewed_records"], 0)
        self.assertIsNone(result["quality_metrics"])
        self.assertEqual(self.form, read_json(CONTEXT / "eval/calibration_15_review_template.json"))

    def test_related_scenarios_cannot_cross_split(self):
        changed = deepcopy(self.manifest)
        changed["cases"][-1]["group"] = changed["cases"][0]["group"]
        with self.assertRaisesRegex(InputError, "crosses"):
            prepare(CONTEXT / "data/dev.json", self.checklist, changed)

    def test_split_preserves_annotations_and_metadata(self):
        case = self.form["cases"][0]
        case["reviewer"] = "synthetic-test-reviewer"
        case["criteria"][0]["reason"] = "A partial human note must survive splitting unchanged"
        outputs = split_form(self.form, self.manifest)
        self.assertEqual(len(outputs["development"]["cases"]), 12)
        self.assertEqual(len(outputs["reserved"]["cases"]), 3)
        self.assertEqual(outputs["development"]["cases"][0], case)
        self.assertEqual(outputs["reserved"]["checklist_hash"], self.form["checklist_hash"])
        self.assertEqual(outputs["reserved"]["selection_hash"], self.form["selection_hash"])
        self.form["cases"].append(deepcopy(case))
        with self.assertRaises(InputError):
            split_form(self.form, self.manifest)

    def test_changed_dev_or_manifest_rejected(self):
        changed = deepcopy(self.manifest)
        changed["dev_sha256"] = "0" * 64
        with self.assertRaises(InputError):
            prepare(CONTEXT / "data/dev.json", self.checklist, changed)
        changed = deepcopy(self.manifest)
        changed["version"] = "different"
        with self.assertRaises(InputError):
            split_form(self.form, changed)

    def test_generation_never_overwrites_existing_annotations(self):
        with tempfile.TemporaryDirectory() as temp:
            destination = Path(temp) / "annotations"
            arguments = ["--manifest", str(CONTEXT / "eval/calibration_15.json"),
                         "--dev", str(CONTEXT / "data/dev.json"),
                         "--checklist", str(CONTEXT / "checklist.json"),
                         "--out-dir", str(destination)]
            with redirect_stdout(io.StringIO()):
                main(arguments)
            file = destination / "review_all.json"
            original = file.read_bytes()
            self.assertEqual(len(read_json(file)["cases"]), 15)
            with redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
                main(arguments)
            self.assertEqual(file.read_bytes(), original)


if __name__ == "__main__":
    unittest.main()
