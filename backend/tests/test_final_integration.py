"""Offline integration regressions. Model responses in this file are synthetic."""
import json
import os
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient
from backend.app import create_app
from backend.ml_adapter import MLAdapter
from cqa_eval.evaluator import Settings

ROOT = Path(__file__).resolve().parents[2]


class FinalIntegrationTests(unittest.TestCase):
    def test_cors_explicit_origin_preflight_and_actual_response(self):
        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, {
            "DOOMSDAY_CORS_ORIGINS": "http://localhost:5173, http://127.0.0.1:5173"
        }, clear=True):
            app = create_app(db_path=Path(directory) / "test.sqlite3", dev_path=None, load_env_file=False)
            with TestClient(app) as client:
                for origin in ("http://localhost:5173", "http://127.0.0.1:5173"):
                    response = client.options("/api/conversations/import", headers={
                        "Origin": origin, "Access-Control-Request-Method": "POST",
                        "Access-Control-Request-Headers": "content-type"})
                    self.assertEqual(response.status_code, 200)
                    self.assertEqual(response.headers["access-control-allow-origin"], origin)
                    response = client.get("/api/health", headers={"Origin": origin})
                    self.assertEqual(response.headers["access-control-allow-origin"], origin)
                    self.assertNotIn("access-control-allow-credentials", response.headers)
                response = client.options("/api/conversations/import", headers={
                    "Origin": "https://untrusted.example", "Access-Control-Request-Method": "POST"})
                self.assertEqual(response.status_code, 400)
                self.assertNotIn("access-control-allow-origin", response.headers)
                with patch.object(app.state.service, "conversations", side_effect=RuntimeError("test-only")):
                    response = client.get("/api/conversations", headers={"Origin": "http://localhost:5173"})
                    self.assertEqual(response.status_code, 500)
                    self.assertEqual(response.headers["access-control-allow-origin"], "http://localhost:5173")

    def test_cors_disabled_by_default_and_wildcard_rejected(self):
        with patch.dict(os.environ, {}, clear=True):
            app = create_app(dev_path=None, load_env_file=False)
            self.assertFalse(any(m.cls.__name__ == "CORSMiddleware" for m in app.user_middleware))
            os.environ["DOOMSDAY_CORS_ORIGINS"] = "*"
            with self.assertRaises(ValueError):
                create_app(dev_path=None, load_env_file=False)

    def test_dotenv_through_real_evaluator_and_sqlite_with_synthetic_transport(self):
        dialogue = json.loads((ROOT / "codex_context/eval/synthetic_dialogue.json").read_text(encoding="utf-8"))
        assessment = json.loads((ROOT / "codex_context/eval/synthetic_response.json").read_text(encoding="utf-8"))
        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, {}, clear=True):
            directory = Path(directory)
            env = directory / ".env"
            env.write_text("LLM_BASE_URL=http://127.0.0.1:8001/v1\n"
                           "LLM_MODEL=qwen3-14b-awq\nLLM_API_KEY=local-test-only\n"
                           "LLM_API_STYLE=chat_completions\nLLM_STRUCTURED_OUTPUT=json_schema\n"
                           "LLM_MAX_OUTPUT_TOKENS=4096\nLLM_MAX_ATTEMPTS=1\n", encoding="utf-8")
            response = {"model": "qwen3-14b-awq", "choices": [{"finish_reason": "stop", "message": {"content": json.dumps(assessment)}}]}
            with patch("cqa_eval.evaluator._http_post", return_value=response) as transport:
                app = create_app(db_path=directory / "test.sqlite3", dev_path=None, env_file=env)
                with TestClient(app) as client:
                    self.assertEqual(client.post("/api/conversations/import", json=dialogue).status_code, 200)
                    queued = client.post(f"/api/conversations/{dialogue['conversation_id']}/evaluations",
                                         json={"checklist_version": "1"})
                    self.assertEqual(queued.status_code, 202, queued.text)
                    path = "/api/evaluations/" + queued.json()["evaluation_id"]
                    deadline = time.monotonic() + 10
                    while time.monotonic() < deadline:
                        result = client.get(path).json()
                        if result["status"] not in {"queued", "running"}:
                            break
                        time.sleep(0.01)
                    self.assertEqual(result["status"], "completed", result)
                    self.assertEqual(len(result["final"]["assessment"]["criteria"]), 8)
                    self.assertEqual(result["final"]["assessment"], assessment)
                    self.assertIsInstance(result["final"]["calculation"]["total_score"], (int, float))
                transport.assert_called_once()
                url, body, settings = transport.call_args.args
                self.assertEqual(url, "http://127.0.0.1:8001/v1/chat/completions")
                self.assertEqual(body["model"], "qwen3-14b-awq")
                self.assertEqual(body["max_tokens"], 4096)
                self.assertEqual(body["response_format"]["type"], "json_schema")
                self.assertEqual(settings.max_attempts, 1)
                with TestClient(create_app(db_path=directory / "test.sqlite3", dev_path=None, env_file=env)) as client:
                    self.assertEqual(client.get(path).json(), result)
                transport.assert_called_once()

    def test_bad_output_limits_rejected_before_transport(self):
        for value in ("0", "-1", "1.5", "private-invalid-marker"):
            with self.subTest(value=value), patch.dict(os.environ, {"LLM_MAX_OUTPUT_TOKENS": value}, clear=True):
                with self.assertRaises(ValueError):
                    Settings.from_env()
                ready, message = MLAdapter().availability()
                self.assertFalse(ready)
                self.assertNotIn(value, message)
