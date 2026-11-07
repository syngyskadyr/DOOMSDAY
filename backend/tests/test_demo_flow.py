"""Sequential API integration evidence; this is not a browser demo.

The evaluator is an explicit synthetic test dependency. Transcript validation,
assessment validation and every score calculation use the real ML adapter and
shared scorer. No provider request is made.
"""
from copy import deepcopy
from pathlib import Path
import tempfile
import time
import unittest

from fastapi.testclient import TestClient

from backend.app import create_app
from backend.tests.test_api import SyntheticAdapter, record


class DemoFlowTest(unittest.TestCase):
    def request_json(self, client, method, path, expected_status=200, **kwargs):
        response = client.request(method, path, **kwargs)
        self.assertEqual(response.status_code, expected_status, response.text)
        return response.json()

    def evaluate(self, client, conversation_id, checklist):
        queued = self.request_json(
            client, "POST", f"/api/conversations/{conversation_id}/evaluations", 202,
            json={"checklist_version": checklist["version"]},
        )
        self.assertEqual(queued["status"], "queued")
        self.assertEqual(queued["checklist_snapshot"], checklist)
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            evaluation = self.request_json(client, "GET", f"/api/evaluations/{queued['evaluation_id']}")
            if evaluation["status"] not in {"queued", "running"}:
                self.assertEqual(evaluation["status"], "completed", evaluation)
                self.assertTrue(evaluation["synthetic"])
                self.assertEqual(evaluation["metadata"]["api_calls"], 0)
                self.assertIsNone(evaluation["metadata"]["model"])
                return evaluation
            time.sleep(0.01)
        self.fail("Synthetic evaluation did not finish within five seconds")

    def assert_evidence_links(self, assessment, messages):
        positions = {message["message_id"]: index for index, message in enumerate(messages)}
        for row in assessment["criteria"]:
            for message_id in row["evidence_message_ids"]:
                self.assertIn(message_id, positions)
            for violation in row["violations"]:
                for message_id in violation["evidence_message_ids"]:
                    self.assertIn(message_id, positions)
            span = row["checked_range"]
            if span is not None:
                self.assertIn(span["start_message_id"], positions)
                self.assertIn(span["end_message_id"], positions)
                self.assertLessEqual(positions[span["start_message_id"]], positions[span["end_message_id"]])

    def test_synthetic_api_journey_with_appeals_versions_and_restart(self):
        dialogue = record("synthetic-demo-flow-001")
        adapter = SyntheticAdapter()
        with tempfile.TemporaryDirectory(prefix="doomsday-demo-flow-") as directory:
            db_path = Path(directory) / "demo.sqlite3"
            app = create_app(db_path=db_path, dev_path=None, adapter=adapter, load_env_file=False)
            with TestClient(app) as client:
                imported = self.request_json(client, "POST", "/api/conversations/import", json=dialogue)
                self.assertEqual(imported, {"imported": 1, "skipped": 0,
                                            "conversation_ids": [dialogue["conversation_id"]]})
                detail = self.request_json(client, "GET", f"/api/conversations/{dialogue['conversation_id']}")
                messages = detail["messages"]
                self.assertEqual(len(messages), len(dialogue["transcript"]))
                for index, message in enumerate(messages):
                    self.assertEqual(message["message_id"], f"m{index + 1:03d}")
                    self.assertEqual(message["source_index"], index)
                    for field in ("timestamp", "speaker", "text"):
                        self.assertEqual(message[field], dialogue["transcript"][index][field])
                    self.assertIsNone(message["audio_offset_seconds"])

                checklist_v1 = self.request_json(client, "GET", "/api/checklists/current")
                first = self.evaluate(client, dialogue["conversation_id"], checklist_v1)
                self.assertEqual(first["original"], first["final"])
                self.assert_evidence_links(first["original"]["assessment"], messages)
                original = deepcopy(first["original"])
                original_metadata = deepcopy(first["metadata"])

                appeal = self.request_json(
                    client, "POST", f"/api/evaluations/{first['evaluation_id']}/appeals", 201,
                    json={"criterion_id": "07", "comment": "Синтетическая проверка пересмотра завершения.",
                          "expected_revision": first["revision"]},
                )
                replacement = deepcopy(appeal["before"])
                replacement.update(answer="no", reason="Синтетическое решение для проверки пересчёта.",
                                   checked_range={"start_message_id": messages[0]["message_id"],
                                                  "end_message_id": messages[-1]["message_id"]})
                resolved = self.request_json(
                    client, "POST", f"/api/appeals/{appeal['appeal_id']}/resolve",
                    json={"decision": "change", "reason": "Ответ изменён в интеграционном тесте.",
                          "replacement": replacement, "expected_revision": first["revision"]},
                )
                revised = resolved["evaluation"]
                expected_assessment = deepcopy(original["assessment"])
                expected_assessment["criteria"] = [
                    replacement if row["criterion_id"] == "07" else row
                    for row in expected_assessment["criteria"]
                ]
                expected_state = adapter.result_state(dialogue, checklist_v1, expected_assessment)
                self.assertEqual(revised["final"], expected_state)
                self.assertLess(revised["final"]["calculation"]["total_score"], original["calculation"]["total_score"])
                self.assertEqual(revised["original"], original)
                self.assertEqual(revised["metadata"], original_metadata)
                self.assertEqual(resolved["appeal"]["before"], appeal["before"])
                self.assertEqual(resolved["appeal"]["status"], "resolved")
                self.assert_evidence_links(revised["final"]["assessment"], messages)

                # A second review confirms the current answer and preserves its
                # score while retaining both decisions in the appeal journal.
                confirmation = self.request_json(
                    client, "POST", f"/api/evaluations/{first['evaluation_id']}/appeals", 201,
                    json={"criterion_id": "07", "comment": "Синтетическая проверка подтверждения.",
                          "expected_revision": revised["revision"]},
                )
                confirmed = self.request_json(
                    client, "POST", f"/api/appeals/{confirmation['appeal_id']}/resolve",
                    json={"decision": "confirm", "reason": "Текущее решение подтверждено в тесте.",
                          "replacement": None, "expected_revision": revised["revision"]},
                )
                persisted_first = confirmed["evaluation"]
                self.assertEqual(persisted_first["final"], expected_state)
                self.assertEqual(persisted_first["original"], original)
                self.assertEqual(persisted_first["revision"], first["revision"] + 2)

                edited = deepcopy(checklist_v1)
                closing_criterion = next(criterion for criterion in edited["criteria"] if criterion["id"] == "07")
                closing_criterion["instruction"] += " Новая синтетическая редакция правила."
                dynamic_criterion = deepcopy(edited["criteria"][0])
                dynamic_criterion.update(id="demo-dynamic-criterion", title="Новый синтетический критерий",
                                         weight=3, instruction="Синтетическое добавленное правило для интеграции.")
                edited["criteria"].append(dynamic_criterion)
                checklist_v2 = self.request_json(
                    client, "POST", "/api/checklists", 201,
                    json={"base_version": checklist_v1["version"], "checklist": edited},
                )
                self.assertNotEqual(checklist_v2["version"], checklist_v1["version"])
                second = self.evaluate(client, dialogue["conversation_id"], checklist_v2)
                self.assertNotEqual(second["evaluation_id"], first["evaluation_id"])
                self.assertEqual(second["original"], second["final"])
                self.assertEqual(second["checklist_snapshot"], checklist_v2)
                self.assertIn("demo-dynamic-criterion", [row["criterion_id"] for row in second["final"]["assessment"]["criteria"]])
                self.assertEqual(len(adapter.seen), 2)
                self.assertEqual(adapter.seen[0][1], checklist_v1)
                self.assertEqual(adapter.seen[1][1], checklist_v2)
                received_rule = next(criterion for criterion in adapter.seen[1][1]["criteria"] if criterion["id"] == "07")
                self.assertIn("Новая синтетическая редакция", received_rule["instruction"])
                self.assert_evidence_links(second["final"]["assessment"], messages)
                self.assertEqual(self.request_json(client, "GET", f"/api/evaluations/{first['evaluation_id']}"),
                                 persisted_first)

            # A fresh app and adapter read the same SQLite file; startup must not
            # submit evaluations or alter any historical snapshot or decision.
            restarted_adapter = SyntheticAdapter()
            restarted = create_app(db_path=db_path, dev_path=None, adapter=restarted_adapter,
                                   load_env_file=False)
            with TestClient(restarted) as client:
                stored_first = self.request_json(client, "GET", f"/api/evaluations/{first['evaluation_id']}")
                stored_second = self.request_json(client, "GET", f"/api/evaluations/{second['evaluation_id']}")
                self.assertEqual(stored_first, persisted_first)
                self.assertEqual(stored_second, second)
                self.assertEqual(stored_first["original"], original)
                self.assertEqual(stored_first["final"], expected_state)
                self.assertEqual(stored_first["metadata"], original_metadata)
                self.assertEqual(stored_first["checklist_snapshot"], checklist_v1)
                self.assertEqual(stored_second["checklist_snapshot"], checklist_v2)
                for checklist in (checklist_v1, checklist_v2):
                    self.assertEqual(self.request_json(client, "GET", f"/api/checklists/{checklist['version']}"), checklist)
                self.assertEqual(self.request_json(client, "GET", "/api/checklists/current"), checklist_v2)
                resolved_appeals = self.request_json(client, "GET", "/api/appeals?status=resolved")
                self.assertEqual(resolved_appeals["total"], 2)
                self.assertEqual({item["appeal_id"] for item in resolved_appeals["items"]},
                                 {appeal["appeal_id"], confirmation["appeal_id"]})
                detail = self.request_json(client, "GET", f"/api/conversations/{dialogue['conversation_id']}")
                self.assertEqual(detail["messages"], messages)
                self.assertEqual([item["evaluation_id"] for item in detail["evaluation_history"]],
                                 [second["evaluation_id"], first["evaluation_id"]])
                self.assertEqual(detail["latest_evaluation_id"], second["evaluation_id"])
                self.assertFalse(self.request_json(client, "GET", "/api/conversations")["items"][0]["has_open_appeals"])
                self.assertEqual(restarted_adapter.seen, [])


if __name__ == "__main__":
    unittest.main()
