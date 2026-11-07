"""HTTP contract integration tests with an explicitly injected synthetic evaluator.

The model is never called. Validation and every score calculation still use the
real cqa_eval implementation through MLAdapter.
"""
from copy import deepcopy
import json
from pathlib import Path
import tempfile
import threading
import time
import unittest
from unittest.mock import patch
from urllib.parse import quote

from fastapi.testclient import TestClient

from backend.app import create_app
from backend.ml_adapter import AdapterUnavailable, MLAdapter
from backend.store import Store


ROOT = Path(__file__).resolve().parents[2]
SYNTHETIC_DIALOGUE = json.loads(
    (ROOT / "codex_context/eval/synthetic_dialogue.json").read_text(encoding="utf-8")
)


def record(conversation_id="test-001", **changes):
    result = deepcopy(SYNTHETIC_DIALOGUE)
    result["conversation_id"] = conversation_id
    result.update(changes)
    return result


class SyntheticAdapter(MLAdapter):
    """Only tests can opt into this evaluator; there is no production fallback."""

    def __init__(self):
        super().__init__()
        self.available = True
        self.fail = False
        self.block = False
        self.entered = threading.Event()
        self.release = threading.Event()
        self.flag_answers = {}
        self.unknown_criteria = set()
        self.seen = []

    def availability(self):
        return (True, None) if self.available else (False, "Тестовый оценщик недоступен")

    def evaluate(self, dialogue, checklist):
        self.seen.append((deepcopy(dialogue), deepcopy(checklist)))
        self.entered.set()
        if self.block and not self.release.wait(5):
            raise RuntimeError("Synthetic test worker was not released")
        if self.fail:
            raise RuntimeError("secret-api-key https://example.invalid/audio?signature=secret")
        rows = []
        last_id = f"m{len(dialogue['transcript']):03d}"
        for criterion in checklist["criteria"]:
            flag = criterion["kind"] == "flag"
            answer = (
                self.flag_answers.get(dialogue["conversation_id"], "no")
                if flag else criterion["scale_max"] if criterion["kind"] == "scale" else "yes"
            )
            if criterion["id"] in self.unknown_criteria:
                answer = None
            rows.append({
                "criterion_id": criterion["id"],
                "status": "needs_review" if answer is None else "evaluated",
                "answer": answer,
                "reason": "Явно синтетический ответ интеграционного теста.",
                "evidence_message_ids": ["m002"],
                "checked_range": {"start_message_id": "m001", "end_message_id": last_id},
                "na_reason": None,
                "uncertainty": "Нужна ручная проверка." if answer is None else None,
                "violations": [{
                    "type": criterion["flag_types"][0],
                    "reason": "Синтетический эпизод для проверки HTTP-контракта.",
                    "evidence_message_ids": ["m002"],
                }] if flag and answer == "yes" else [],
            })
        assessment = {
            "conversation_id": dialogue["conversation_id"],
            "checklist_version": checklist["version"],
            "criteria": rows,
        }
        result = self.result_state(dialogue, checklist, assessment)
        result["metadata"] = {
            "model": None, "cache_hit": False, "api_calls": 0, "retry_count": 0,
            "latency_ms": 0, "usage": None, "estimated_cost_usd": None,
            "cost_note": "Явная тестовая зависимость, вызовов модели нет.",
            "dialogue_hash": None, "checklist_hash": None, "prompt_hash": None,
        }
        return result


class APITest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="doomsday-backend-test-")
        self.db_path = Path(self.tmp.name) / "test.sqlite3"
        self.adapter = SyntheticAdapter()
        self._open_client()

    def _open_client(self):
        self.app = create_app(db_path=self.db_path, dev_path=None, adapter=self.adapter)
        self.client = TestClient(self.app)
        self.client.__enter__()

    def tearDown(self):
        self.adapter.release.set()
        self.client.__exit__(None, None, None)
        self.tmp.cleanup()

    def assert_error(self, response, status):
        self.assertEqual(response.status_code, status, response.text)
        self.assertEqual(set(response.json()), {"error"})
        error = response.json()["error"]
        self.assertEqual(set(error), {"code", "message", "details"})
        self.assertIsInstance(error["code"], str)
        self.assertIsInstance(error["message"], str)
        self.assertTrue(error["details"] is None or isinstance(error["details"], list))
        return error

    def import_records(self, *records):
        response = self.client.post("/api/conversations/import", json=list(records))
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    def current_checklist(self):
        response = self.client.get("/api/checklists/current")
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    def start(self, conversation_id="test-001", version="1"):
        response = self.client.post(
            f"/api/conversations/{conversation_id}/evaluations",
            json={"checklist_version": version},
        )
        self.assertEqual(response.status_code, 202, response.text)
        evaluation = response.json()
        self.assertEqual(evaluation["status"], "queued")
        self.assertEqual(evaluation["revision"], 0)
        for field in ("original", "final", "metadata", "error"):
            self.assertIsNone(evaluation[field])
        self.assertEqual(evaluation["appeals"], [])
        return evaluation

    def wait_evaluation(self, evaluation_id):
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            response = self.client.get(f"/api/evaluations/{evaluation_id}")
            self.assertEqual(response.status_code, 200, response.text)
            result = response.json()
            if result["status"] not in {"queued", "running"}:
                return result
            time.sleep(0.01)
        self.fail(f"Evaluation {evaluation_id} did not finish")

    def evaluate(self, conversation_id="test-001", version="1"):
        return self.wait_evaluation(self.start(conversation_id, version)["evaluation_id"])

    def appeal(self, evaluation, criterion_id="07"):
        response = self.client.post(
            f"/api/evaluations/{evaluation['evaluation_id']}/appeals",
            json={"criterion_id": criterion_id, "comment": "Проверить синтетический ответ.",
                  "expected_revision": evaluation["revision"]},
        )
        self.assertEqual(response.status_code, 201, response.text)
        return response.json()

    def test_import_envelope_idempotence_and_original_messages(self):
        item = record(audio_url="https://example.invalid/recording")
        item["transcript"] = [
            {"timestamp": "2026-11-07T12:00:00+05:00", "speaker": speaker, "text": "  повтор  "}
            for speaker in ("bot", "system", "client", "operator", "operator")
        ]
        response = self.client.post("/api/conversations/import", json={
            "isSuccess": True, "data": {"records": [item]},
        })
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json(), {
            "imported": 1, "skipped": 0, "conversation_ids": [item["conversation_id"]],
        })
        reordered = dict(reversed(list(item.items())))
        second = self.client.post("/api/conversations/import", json=reordered)
        self.assertEqual(second.status_code, 200, second.text)
        self.assertEqual(second.json()["imported"], 0)
        self.assertEqual(second.json()["skipped"], 1)
        detail = self.client.get("/api/conversations/test-001").json()
        self.assertEqual(detail["audio_url"], item["audio_url"])
        self.assertTrue(detail["synthetic"])
        self.assertEqual(detail["evaluation_history"], [])
        self.assertIsNone(detail["latest_evaluation_id"])
        for index, message in enumerate(detail["messages"]):
            self.assertEqual(message, {
                **item["transcript"][index], "message_id": f"m{index + 1:03d}",
                "source_index": index, "audio_offset_seconds": None,
            })
        self.assertEqual(self.adapter.seen, [])

    def test_import_conflict_and_invalid_batch_are_atomic(self):
        original = record()
        self.import_records(original)
        changed = deepcopy(original)
        changed["transcript"][0]["text"] += " изменено"
        conflict = self.client.post("/api/conversations/import", json=[record("new"), changed])
        self.assert_error(conflict, 409)
        self.assertEqual(self.client.get("/api/conversations").json()["total"], 1)
        invalid = record("invalid", transcript=[])
        self.assert_error(self.client.post("/api/conversations/import", json=[record("new"), invalid]), 422)
        self.assert_error(self.client.post("/api/conversations/import", json={
            "isSuccess": False, "data": {"records": [record("new")]},
        }), 422)
        self.assertEqual(self.client.get("/api/conversations").json()["total"], 1)

    def test_validation_errors_use_contract_envelope(self):
        self.assert_error(self.client.post("/api/conversations/import", content="{broken",
                                          headers={"content-type": "application/json"}), 400)
        for query in ("limit=0", "limit=201", "offset=-1", "critical_error=no", "status=missing"):
            self.assert_error(self.client.get("/api/conversations?" + query), 422)
        self.assert_error(self.client.get("/api/conversations/missing"), 404)
        self.assert_error(self.client.get("/api/evaluations/missing"), 404)
        self.assert_error(self.client.get("/api/checklists/missing"), 404)

    def test_message_ids_continue_after_999(self):
        item = record()
        item["transcript"] = [deepcopy(item["transcript"][0]) for _ in range(1001)]
        self.import_records(item)
        messages = self.client.get("/api/conversations/test-001").json()["messages"]
        self.assertEqual(messages[999]["message_id"], "m1000")
        self.assertEqual(messages[1000]["source_index"], 1000)

    def test_encoded_conversation_id_with_slash_opens_detail_and_evaluation(self):
        conversation_id = "synthetic/новый диалог ?#"
        self.import_records(record(conversation_id))
        path = "/api/conversations/" + quote(conversation_id, safe="")
        response = self.client.get(path)
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["conversation_id"], conversation_id)
        queued = self.client.post(path + "/evaluations", json={"checklist_version": "1"})
        self.assertEqual(queued.status_code, 202, queued.text)
        completed = self.wait_evaluation(queued.json()["evaluation_id"])
        self.assertEqual(completed["conversation_id"], conversation_id)
        self.assertEqual(completed["status"], "completed", completed)

    def test_filters_intersect_before_pagination_and_accept_new_channels(self):
        self.import_records(record("z", language="EN"), record("b", channel="new channel"),
                            record("a", channel="new channel"), record("c", language="KZ"))
        page = self.client.get("/api/conversations", params={
            "language": "RU", "channel": "new channel", "status": "not_evaluated",
            "critical_error": "unknown", "limit": 1, "offset": 1,
        }).json()
        self.assertEqual(page["total"], 2)
        self.assertEqual(page["limit"], 1)
        self.assertEqual(page["offset"], 1)
        self.assertEqual([row["conversation_id"] for row in page["items"]], ["b"])
        self.assertEqual(self.client.get("/api/conversations?critical_error=false").json()["total"], 0)

    def test_checklist_versions_are_immutable_and_concurrent_edit_conflicts(self):
        before = self.current_checklist()
        edited = deepcopy(before)
        edited["title"] = "Синтетическая новая версия"
        new_criterion = deepcopy(edited["criteria"][0])
        new_criterion["id"] = "new-dynamic-criterion"
        edited["criteria"].append(new_criterion)
        response = self.client.post("/api/checklists", json={"base_version": before["version"],
                                                             "checklist": edited})
        self.assertEqual(response.status_code, 201, response.text)
        saved = response.json()
        self.assertNotEqual(saved["version"], before["version"])
        self.assertIsInstance(saved["version"], str)
        self.assertEqual(self.current_checklist(), saved)
        self.assertEqual(self.client.get(f"/api/checklists/{before['version']}").json(), before)
        self.assert_error(self.client.post("/api/checklists", json={
            "base_version": before["version"], "checklist": edited,
        }), 409)
        versions = self.client.get("/api/checklists").json()
        self.assertEqual(versions["current_version"], saved["version"])
        self.assertEqual([row["version"] for row in versions["items"]], [saved["version"], before["version"]])
        self.import_records(record())
        evaluation = self.evaluate(version=saved["version"])
        self.assertEqual(evaluation["status"], "completed", evaluation)
        self.assertEqual(evaluation["checklist_snapshot"], saved)
        self.assertIn("new-dynamic-criterion", [row["criterion_id"] for row in evaluation["final"]["assessment"]["criteria"]])

    def test_invalid_checklist_does_not_create_version(self):
        before = self.current_checklist()
        variants = []
        invalid = deepcopy(before)
        invalid["criteria"][0]["weight"] = -1
        variants.append(invalid)
        invalid = deepcopy(before)
        invalid["criteria"][0]["section_id"] = "unknown"
        variants.append(invalid)
        invalid = deepcopy(before)
        invalid["criteria"][3]["levels"][0]["value"] = 19
        variants.append(invalid)
        invalid = deepcopy(before)
        invalid["criteria"][1]["na_rule"] = "  "
        variants.append(invalid)
        for checklist in variants:
            self.assert_error(self.client.post("/api/checklists", json={
                "base_version": before["version"], "checklist": checklist,
            }), 422)
        self.assertEqual(self.current_checklist(), before)
        self.assertEqual(len(self.client.get("/api/checklists").json()["items"]), 1)

    def test_unavailable_evaluator_does_not_create_fake_evaluation(self):
        self.adapter.available = False
        self.import_records(record())
        health = self.client.get("/api/health")
        self.assertEqual(health.status_code, 200)
        self.assertEqual(health.json()["api_version"], "1")
        self.assertEqual(health.json()["evaluator_status"], "unavailable")
        self.assert_error(self.client.post("/api/conversations/test-001/evaluations",
                                          json={"checklist_version": "1"}), 503)
        self.assertEqual(self.client.get("/api/conversations/test-001").json()["evaluation_history"], [])
        self.assertEqual(self.adapter.seen, [])

    def test_missing_ml_package_keeps_import_read_and_checklist_edit_available(self):
        with patch.object(MLAdapter, "_modules", side_effect=AdapterUnavailable()):
            app = create_app(db_path=Path(self.tmp.name) / "missing-ml.sqlite3", dev_path=None,
                             adapter=MLAdapter())
            with TestClient(app) as client:
                response = client.post("/api/conversations/import", json=record())
                self.assertEqual(response.status_code, 200, response.text)
                self.assertEqual(client.get("/api/conversations").json()["total"], 1)
                self.assertEqual(client.get("/api/conversations/test-001").json()["conversation_id"], "test-001")
                checklist = client.get("/api/checklists/current").json()
                checklist["criteria"][0]["weight"] = 12
                response = client.post("/api/checklists", json={"base_version": checklist["version"],
                                                               "checklist": checklist})
                self.assertEqual(response.status_code, 201, response.text)
                self.assertEqual(client.get("/api/health").json()["evaluator_status"], "unavailable")
                self.assert_error(client.post("/api/conversations/test-001/evaluations", json={
                    "checklist_version": response.json()["version"],
                }), 503)
                self.assertEqual(client.get("/api/conversations/test-001").json()["evaluation_history"], [])

    def test_only_public_frontend_directory_is_served(self):
        frontend_root = Path(self.tmp.name) / "frontend"
        public = frontend_root / "public"
        public.mkdir(parents=True)
        (public / "index.html").write_text("<p>Synthetic public frontend</p>", encoding="utf-8")
        (frontend_root / "private.txt").write_text("private-root-content", encoding="utf-8")
        app = create_app(db_path=Path(self.tmp.name) / "static.sqlite3", dev_path=None,
                         adapter=self.adapter, frontend_path=public)
        with TestClient(app) as client:
            response = client.get("/")
            self.assertEqual(response.status_code, 200, response.text)
            self.assertIn("Synthetic public frontend", response.text)
            for path in ("/private.txt", "/.env", "/codex_context/checklist.json", "/backend/app.py"):
                response = client.get(path)
                self.assertEqual(response.status_code, 404, response.text)
                self.assertNotIn("private-root-content", response.text)
            self.assertEqual(client.get("/api/health").status_code, 200)

    def test_unexpected_api_exception_is_safely_formatted(self):
        self.client.__exit__(None, None, None)
        self.client = TestClient(self.app, raise_server_exceptions=False)
        self.client.__enter__()
        secret = "secret-api-key https://example.invalid?signature=private"
        with patch.object(self.app.state.service, "conversation", side_effect=RuntimeError(secret)):
            response = self.client.get("/api/conversations/test-001")
        self.assert_error(response, 500)
        self.assertNotIn("secret", response.text)
        self.assertNotIn("signature", response.text)

    def test_background_queue_active_conflict_and_pinned_snapshot(self):
        self.adapter.block = True
        self.import_records(record())
        queued = self.start()
        try:
            self.assertTrue(self.adapter.entered.wait(2))
            self.assert_error(self.client.post("/api/conversations/test-001/evaluations",
                                              json={"checklist_version": "1"}), 409)
            edited = self.current_checklist()
            edited["criteria"][0]["weight"] = 77
            response = self.client.post("/api/checklists", json={"base_version": "1", "checklist": edited})
            self.assertEqual(response.status_code, 201, response.text)
        finally:
            self.adapter.release.set()
        completed = self.wait_evaluation(queued["evaluation_id"])
        self.assertEqual(completed["status"], "completed", completed)
        self.assertEqual(completed["revision"], 1)
        self.assertEqual(completed["original"], completed["final"])
        self.assertEqual(completed["checklist_snapshot"]["version"], "1")
        self.assertEqual(completed["checklist_snapshot"]["criteria"][0]["weight"], 10)
        self.assertEqual(completed["metadata"]["api_calls"], 0)
        self.assertIsInstance(completed["final"]["calculation"]["total_score"], (int, float))
        repeat = self.evaluate()
        self.assertNotEqual(repeat["evaluation_id"], completed["evaluation_id"])

    def test_provider_failure_is_saved_and_does_not_erase_history(self):
        self.import_records(record())
        completed = self.evaluate()
        self.adapter.fail = True
        failed = self.evaluate()
        self.assertEqual(failed["status"], "failed", failed)
        self.assertIsNone(failed["original"])
        self.assertIsNone(failed["final"])
        self.assertIsNotNone(failed["error"])
        self.assertNotIn("secret", json.dumps(failed))
        detail = self.client.get("/api/conversations/test-001").json()
        self.assertEqual(detail["latest_evaluation_id"], failed["evaluation_id"])
        self.assertEqual([e["evaluation_id"] for e in detail["evaluation_history"]],
                         [failed["evaluation_id"], completed["evaluation_id"]])
        self.assertEqual(self.client.get(f"/api/evaluations/{completed['evaluation_id']}").json()["original"],
                         completed["original"])

    def test_critical_error_filters_distinguish_false_and_unknown(self):
        self.import_records(*(record(name) for name in ("a", "b", "c", "d")))
        self.adapter.flag_answers = {"a": None, "b": "no", "c": "yes"}
        unknown = self.evaluate("a")
        self.assertEqual(unknown["status"], "needs_review", unknown)
        self.assertIsNone(unknown["final"]["critical_error"])
        self.assertIsInstance(unknown["final"]["calculation"]["total_score"], (int, float))
        self.evaluate("b")
        self.evaluate("c")
        for value, expected in (("true", ["c"]), ("false", ["b"]), ("unknown", ["a", "d"])):
            page = self.client.get("/api/conversations", params={"critical_error": value}).json()
            self.assertEqual([row["conversation_id"] for row in page["items"]], expected)
            self.assertEqual(page["total"], len(expected))

    def test_unknown_weighted_answer_and_zero_applicable_weight_keep_null_scores(self):
        self.import_records(record())
        self.adapter.unknown_criteria = {"01"}
        reviewed = self.evaluate()
        self.assertEqual(reviewed["status"], "needs_review", reviewed)
        self.assertIsNone(reviewed["final"]["calculation"]["total_score"])
        self.adapter.unknown_criteria.clear()
        checklist = self.current_checklist()
        for criterion in checklist["criteria"]:
            if criterion["kind"] != "flag":
                criterion["weight"] = 0
        response = self.client.post("/api/checklists", json={"base_version": "1", "checklist": checklist})
        self.assertEqual(response.status_code, 201, response.text)
        completed = self.evaluate(version=response.json()["version"])
        self.assertEqual(completed["status"], "completed", completed)
        self.assertIsNone(completed["final"]["calculation"]["total_score"])
        self.assertTrue(completed["final"]["calculation"]["no_applicable_criteria"])

    def test_appeal_recalculates_final_and_preserves_original_metadata(self):
        self.import_records(record())
        evaluation = self.evaluate()
        original = deepcopy(evaluation["original"])
        metadata = deepcopy(evaluation["metadata"])
        appeal = self.appeal(evaluation)
        before = next(row for row in original["assessment"]["criteria"] if row["criterion_id"] == "07")
        self.assertEqual(appeal["before"], before)
        self.assertEqual(appeal["based_on_revision"], 1)
        self.assertEqual(self.client.get("/api/appeals").json()["total"], 1)
        self.assertTrue(self.client.get("/api/conversations").json()["items"][0]["has_open_appeals"])
        self.assert_error(self.client.post(f"/api/evaluations/{evaluation['evaluation_id']}/appeals", json={
            "criterion_id": "07", "comment": "Повтор", "expected_revision": 1,
        }), 409)
        replacement = deepcopy(before)
        replacement["answer"] = "no"
        replacement["reason"] = "Синтетическое исправление для проверки пересчёта."
        replacement["checked_range"] = {"start_message_id": "m001", "end_message_id": "m004"}
        request = {"decision": "change", "reason": "Проверено вручную в тесте.",
                   "replacement": replacement, "expected_revision": 1}
        resolved = self.client.post(f"/api/appeals/{appeal['appeal_id']}/resolve", json=request)
        self.assertEqual(resolved.status_code, 200, resolved.text)
        result = resolved.json()
        final_eval = result["evaluation"]
        self.assertEqual(final_eval["revision"], 2)
        self.assertEqual(final_eval["original"], original)
        self.assertEqual(final_eval["metadata"], metadata)
        expected_assessment = deepcopy(original["assessment"])
        expected_assessment["criteria"] = [replacement if row["criterion_id"] == "07" else row
                                            for row in expected_assessment["criteria"]]
        expected = self.adapter.result_state(record(), evaluation["checklist_snapshot"], expected_assessment)
        self.assertEqual(final_eval["final"], expected)
        self.assertLess(final_eval["final"]["calculation"]["total_score"], original["calculation"]["total_score"])
        self.assertEqual(result["appeal"]["status"], "resolved")
        self.assertEqual(result["appeal"]["resolution"]["resulting_revision"], 2)
        self.assert_error(self.client.post(f"/api/appeals/{appeal['appeal_id']}/resolve", json=request), 409)
        self.assertEqual(self.client.get("/api/appeals").json()["total"], 0)
        self.assertEqual(self.client.get("/api/appeals?status=resolved").json()["total"], 1)

    def test_appeal_validation_revision_and_flag_change_are_atomic(self):
        self.import_records(record())
        evaluation = self.evaluate()
        self.assert_error(self.client.post(f"/api/evaluations/{evaluation['evaluation_id']}/appeals", json={
            "criterion_id": "08", "comment": "Проверить", "expected_revision": 0,
        }), 409)
        appeal = self.appeal(evaluation, "08")
        replacement = deepcopy(appeal["before"])
        replacement["answer"] = "yes"
        request = {"decision": "change", "reason": "Проверка флага.", "replacement": replacement,
                   "expected_revision": 1}
        self.assert_error(self.client.post(f"/api/appeals/{appeal['appeal_id']}/resolve", json=request), 422)
        unchanged = self.client.get(f"/api/evaluations/{evaluation['evaluation_id']}").json()
        self.assertEqual(unchanged["revision"], 1)
        self.assertEqual(unchanged["final"], evaluation["final"])
        self.assertEqual(unchanged["appeals"][0]["status"], "open")
        replacement["violations"] = [{"type": "insult", "reason": "Синтетический эпизод.",
                                      "evidence_message_ids": ["m002"]}]
        request["expected_revision"] = 0
        self.assert_error(self.client.post(f"/api/appeals/{appeal['appeal_id']}/resolve", json=request), 409)
        request["expected_revision"] = 1
        resolved = self.client.post(f"/api/appeals/{appeal['appeal_id']}/resolve", json=request)
        self.assertEqual(resolved.status_code, 200, resolved.text)
        updated = resolved.json()["evaluation"]
        self.assertTrue(updated["final"]["critical_error"])
        self.assertEqual(updated["final"]["calculation"]["total_score"],
                         evaluation["final"]["calculation"]["total_score"])
        page = self.client.get("/api/conversations?critical_error=true").json()
        self.assertEqual(page["total"], 1)

    def test_confirm_preserves_result_and_invalidates_other_stale_resolution(self):
        self.import_records(record())
        evaluation = self.evaluate()
        first = self.appeal(evaluation, "01")
        second = self.appeal(evaluation, "07")
        request = {"decision": "confirm", "reason": "Основания подтверждены.",
                   "replacement": None, "expected_revision": 1}
        response = self.client.post(f"/api/appeals/{first['appeal_id']}/resolve", json=request)
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["evaluation"]["final"], evaluation["final"])
        self.assertEqual(response.json()["evaluation"]["revision"], 2)
        self.assertIsNone(response.json()["appeal"]["resolution"]["replacement"])
        self.assert_error(self.client.post(f"/api/appeals/{second['appeal_id']}/resolve", json=request), 409)
        request["expected_revision"] = 2
        response = self.client.post(f"/api/appeals/{second['appeal_id']}/resolve", json=request)
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["evaluation"]["revision"], 3)

    def test_unavailable_scorer_does_not_partially_resolve_appeal(self):
        self.import_records(record())
        evaluation = self.evaluate()
        appeal = self.appeal(evaluation)
        with patch.object(self.adapter, "result_state", side_effect=AdapterUnavailable()):
            response = self.client.post(f"/api/appeals/{appeal['appeal_id']}/resolve", json={
                "decision": "confirm", "reason": "Подтверждаю", "replacement": None,
                "expected_revision": 1,
            })
        self.assert_error(response, 503)
        persisted = self.client.get(f"/api/evaluations/{evaluation['evaluation_id']}").json()
        self.assertEqual(persisted["revision"], 1)
        self.assertEqual(persisted["final"], evaluation["final"])
        self.assertEqual(persisted["appeals"][0]["status"], "open")

    def test_data_versions_results_and_appeals_survive_restart(self):
        self.import_records(record())
        evaluation = self.evaluate()
        appeal = self.appeal(evaluation)
        edited = self.current_checklist()
        edited["title"] = "Версия после перезапуска"
        response = self.client.post("/api/checklists", json={"base_version": "1", "checklist": edited})
        self.assertEqual(response.status_code, 201, response.text)
        current = response.json()
        self.client.__exit__(None, None, None)
        self._open_client()
        self.assertEqual(self.client.get("/api/conversations").json()["total"], 1)
        self.assertEqual(self.current_checklist(), current)
        stored = self.client.get(f"/api/evaluations/{evaluation['evaluation_id']}").json()
        self.assertEqual(stored["original"], evaluation["original"])
        self.assertEqual(stored["checklist_snapshot"], evaluation["checklist_snapshot"])
        self.assertEqual(stored["appeals"][0], appeal)
        self.assertEqual(self.client.get("/api/appeals").json()["total"], 1)

    def test_restart_marks_persisted_unfinished_jobs_failed_without_model_retry(self):
        self.import_records(record("a"), record("b"))
        evaluations = [self.evaluate("a"), self.evaluate("b")]
        self.client.__exit__(None, None, None)
        # Reproduce exactly the persisted state left by a process crash, without
        # killing a worker in the test runner or issuing any provider requests.
        store = Store(self.db_path)
        with store.transaction() as db:
            for evaluation, status in zip(evaluations, ("queued", "running")):
                evaluation.update(status=status, original=None, final=None, metadata=None, revision=0)
                store.update_evaluation(db, evaluation)
        calls_before_restart = len(self.adapter.seen)
        self._open_client()
        for evaluation in evaluations:
            result = self.client.get(f"/api/evaluations/{evaluation['evaluation_id']}").json()
            self.assertEqual(result["status"], "failed", result)
            self.assertIsNotNone(result["error"])
            self.assertIsNone(result["original"])
            self.assertIsNone(result["final"])
        self.assertEqual(len(self.adapter.seen), calls_before_restart)


if __name__ == "__main__":
    unittest.main()
