"""Real saved DEV integration; requires the private local DEV source, no inference."""
from copy import deepcopy
import hashlib
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient
from backend.app import create_app
from backend.importing import parse_json
from backend.service import APIError
from cqa_eval.data import InputError
from cqa_eval.submission import CRITERION_CODES

ROOT = Path(__file__).resolve().parents[2]
DEV = ROOT / "codex_context/data/dev.json"
RESULTS = ROOT / "results/dev/results.json"


@unittest.skipUnless(DEV.is_file(), "Real DEV source must be supplied locally; transcripts are not committed")
class ResultImportTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.db = Path(self.temp.name) / "test.sqlite3"
        self.payload = parse_json(RESULTS.read_bytes())
        self.original_hash = hashlib.sha256(RESULTS.read_bytes()).hexdigest()
        self.transport = patch("cqa_eval.evaluator._http_post", side_effect=AssertionError("No inference permitted"))
        self.post = self.transport.start()
        self.addCleanup(self.transport.stop)

    def app(self, results=True):
        return create_app(db_path=self.db, dev_path=DEV, load_env_file=False,
                          results_path=RESULTS if results else None)

    def test_real_submission_endpoints_evidence_scores_errors_and_restart(self):
        app = self.app()
        with TestClient(app) as client:
            listing = client.get("/api/conversations?limit=200").json()
            self.assertEqual(listing["total"], 65)
            self.assertEqual(client.get("/api/conversations?status=completed").json()["total"], 64)
            self.assertEqual(client.get("/api/conversations?status=failed").json()["total"], 1)
            expected = {r["conversation_id"]: r for r in self.payload["results"]}
            stored = {}
            for summary in listing["items"]:
                source = expected[summary["conversation_id"]]
                detail = client.get("/api/conversations/" + summary["conversation_id"]).json()
                evaluation = client.get("/api/evaluations/" + detail["latest_evaluation_id"]).json()
                stored[evaluation["evaluation_id"]] = evaluation
                self.assertEqual(evaluation["import_source"]["result"], source)
                self.assertFalse(evaluation["synthetic"])
                self.assertTrue(evaluation["import_source"]["read_only"])
                self.assertIsNone(evaluation["metadata"]["api_calls"])
                self.assertEqual(evaluation["original"], evaluation["final"])
                if source["status"] == "error":
                    self.assertEqual(evaluation["status"], "failed")
                    self.assertIsNone(evaluation["final"])
                    self.assertEqual(evaluation["error"]["message"], source["error"])
                    continue
                final = evaluation["final"]
                self.assertEqual(final["calculation"]["total_score"], source["score"]["final_score"])
                self.assertEqual(final["critical_error"], source["score"]["critical_flag"])
                self.assertEqual(len(final["assessment"]["criteria"]), 8)
                self.assertAlmostEqual(sum(r["earned_points"] or 0 for r in final["calculation"]["rows"]),
                                       source["score"]["final_score"], delta=0.0051)
                for row in final["assessment"]["criteria"]:
                    original = next(c for c in source["criteria"] if c["criterion_id"] == CRITERION_CODES[row["criterion_id"]])
                    self.assertEqual(row["reason"], original["rationale"])
                    self.assertEqual(row["evidence_message_ids"], [detail["messages"][turn - 1]["message_id"] for turn in original["evidence_turns"]])
                    self.assertIsNone(row["checked_range"])
                    self.assertEqual(row["violations"], [])
                response = client.post(f"/api/evaluations/{evaluation['evaluation_id']}/appeals", json={
                    "criterion_id": "01", "comment": "read-only check", "expected_revision": 1})
                self.assertEqual(response.status_code, 409)
            self.assertEqual(app.state.service.import_official_results(self.payload), {"imported": 0, "skipped": 65})
        with TestClient(self.app()) as client:
            for evaluation_id, evaluation in stored.items():
                self.assertEqual(client.get("/api/evaluations/" + evaluation_id).json(), evaluation)
            with client.app.state.store.transaction(write=False) as db:
                self.assertEqual(db.execute("SELECT COUNT(*) FROM evaluations").fetchone()[0], 65)
        self.post.assert_not_called()
        self.assertEqual(hashlib.sha256(RESULTS.read_bytes()).hexdigest(), self.original_hash)

    def test_invalid_source_rejected_before_any_evaluation_is_inserted(self):
        with TestClient(self.app(False)) as client:
            invalid = deepcopy(self.payload)
            invalid["results"][-1]["criteria"][0]["evidence_turns"] = [999999]
            with self.assertRaises(InputError):
                client.app.state.service.import_official_results(invalid)
            with client.app.state.store.transaction(write=False) as db:
                self.assertEqual(db.execute("SELECT COUNT(*) FROM evaluations").fetchone()[0], 0)

    def test_conflicting_reimport_rolls_back_entire_transaction(self):
        with TestClient(self.app()) as client:
            service = client.app.state.service
            # Force the first item to be newly inserted before a later conflict.
            with service.store.transaction() as db:
                db.execute("DELETE FROM evaluations WHERE conversation_id=?", (self.payload["results"][0]["conversation_id"],))
            changed = deepcopy(self.payload)
            changed["results"][1]["criteria"][0]["rationale"] += " Changed test input."
            with self.assertRaises(APIError):
                service.import_official_results(changed)
            with service.store.transaction(write=False) as db:
                self.assertEqual(db.execute("SELECT COUNT(*) FROM evaluations").fetchone()[0], 64)
                self.assertIsNone(db.execute("SELECT 1 FROM evaluations WHERE conversation_id=?",
                                            (self.payload["results"][0]["conversation_id"],)).fetchone())

    def test_existing_native_evaluation_is_preserved(self):
        with TestClient(self.app()) as client:
            service = client.app.state.service
            with service.store.transaction() as db:
                db.execute("UPDATE evaluations SET id='native-existing' WHERE conversation_id=?",
                           (self.payload["results"][0]["conversation_id"],))
            with self.assertRaises(APIError):
                service.import_official_results(self.payload)
            with service.store.transaction(write=False) as db:
                self.assertEqual(db.execute("SELECT COUNT(*) FROM evaluations").fetchone()[0], 65)
                self.assertIsNotNone(db.execute("SELECT 1 FROM evaluations WHERE id='native-existing'").fetchone())

    def test_unrelated_dialogues_do_not_block_official_dev_import(self):
        with TestClient(self.app(False)) as client:
            synthetic = parse_json((ROOT / "codex_context/eval/synthetic_dialogue.json").read_bytes())
            self.assertEqual(client.post("/api/conversations/import", json=synthetic).status_code, 200)
            self.assertEqual(client.app.state.service.import_official_results(self.payload), {"imported": 65, "skipped": 0})
            self.assertEqual(client.get("/api/conversations").json()["total"], 66)
            detail = client.get("/api/conversations/" + synthetic["conversation_id"]).json()
            self.assertIsNone(detail["latest_evaluation_id"])
