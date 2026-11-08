"""Real loopback HTTP/SQLite smoke, with no remote inference.

Imports real DEV and validates the saved official submission. Evaluation uses
either an explicitly synthetic fixture or an unchanged saved ML assessment.
Only the provider HTTP transport is replaced. Never use this as a demo server
or official-results generator; every database lives in a temporary directory.
"""
import argparse
from contextlib import contextmanager
from decimal import Decimal
import json
import os
from pathlib import Path
import socket
import sys
import tempfile
import threading
import time
from unittest.mock import patch
from urllib.parse import quote

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / "src")]

import httpx
import uvicorn
from backend.app import create_app
from cqa_eval.data import load_dev, read_json
from cqa_eval.submission import validate_submission


@contextmanager
def serve(app):
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        server = uvicorn.Server(uvicorn.Config(app, log_level="error", access_log=False))
        thread = threading.Thread(target=server.run, kwargs={"sockets": [listener]}, daemon=True)
        thread.start()
        try:
            deadline = time.monotonic() + 15
            while not server.started:
                if not thread.is_alive() or time.monotonic() >= deadline:
                    raise RuntimeError("Smoke server did not start")
                time.sleep(0.02)
            with httpx.Client(base_url=f"http://127.0.0.1:{listener.getsockname()[1]}",
                              timeout=15, trust_env=False) as client:
                yield client
        finally:
            server.should_exit = True
            thread.join(timeout=20)
            if thread.is_alive():
                raise RuntimeError("Smoke server failed to stop")


def checked(response, status=200):
    if response.status_code != status:
        raise AssertionError(f"HTTP {response.status_code}; expected {status}")
    return response.json()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dev", type=Path, required=True)
    parser.add_argument("--submission", type=Path, default=ROOT / "results/dev/results.json")
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--synthetic", action="store_true", help="Explicitly use the bundled synthetic model response")
    mode.add_argument("--saved-result", type=Path, help="Replay an unmodified real cqa_eval result.json for a DEV record")
    args = parser.parse_args()
    records = load_dev(args.dev)
    submission = validate_submission(read_json(args.submission), records, "dev")
    if args.synthetic:
        dialogue = read_json(ROOT / "codex_context/eval/synthetic_dialogue.json")
        assessment = read_json(ROOT / "codex_context/eval/synthetic_response.json")
    else:
        saved = read_json(args.saved_result)
        assessment = saved["assessment"]
        dialogue = next(r for r in records if r["conversation_id"] == assessment["conversation_id"])
        if dialogue.get("synthetic") or saved.get("checklist_snapshot") != read_json(ROOT / "codex_context/checklist.json"):
            raise ValueError("Saved replay needs a real DEV record and the official checklist snapshot")
    envelope = {"model": "offline-replay", "choices": [{"finish_reason": "stop",
                "message": {"content": json.dumps(assessment, ensure_ascii=False)}}]}
    environment = {"LLM_API_KEY": "offline-test-only", "LLM_BASE_URL": "http://127.0.0.1:1/v1",
                   "LLM_MODEL": "offline-replay", "LLM_API_STYLE": "chat_completions",
                   "LLM_STRUCTURED_OUTPUT": "json_schema", "LLM_MAX_ATTEMPTS": "1",
                   "LLM_TIMEOUT_SECONDS": "90", "LLM_INPUT_USD_PER_MILLION": "", "LLM_OUTPUT_USD_PER_MILLION": "",
                   "LLM_MAX_OUTPUT_TOKENS": "4096", "DOOMSDAY_CORS_ORIGINS": "http://localhost:5173"}
    with tempfile.TemporaryDirectory(prefix="doomsday-http-smoke-") as directory, \
            patch.dict(os.environ, environment), \
            patch("cqa_eval.evaluator._http_post", return_value=envelope) as transport:
        db = Path(directory) / "smoke.sqlite3"
        def app():
            return create_app(db_path=db, dev_path=args.dev, load_env_file=False)
        with serve(app()) as client:
            checked(client.get("/api/health"))
            assert client.get("/").status_code == 200
            listing = checked(client.get("/api/conversations?limit=200"))
            assert listing["total"] == 65
            assert len({r["conversation_id"] for r in listing["items"]}) == 65
            assert checked(client.get("/api/conversations", params={"channel": "voice call"}))["total"] == 5
            assert checked(client.get("/api/conversations", params={"channel": "agent chat"}))["total"] == 60
            assert checked(client.post("/api/conversations/import", json=read_json(args.dev)))["skipped"] == 65
            for record in records:
                detail = checked(client.get("/api/conversations/" + quote(record["conversation_id"], safe="")))
                assert len(detail["messages"]) == len(record["transcript"])
                for index, message in enumerate(detail["messages"]):
                    assert message["source_index"] == index
                    assert message["text"] == record["transcript"][index]["text"]
            preflight = client.options("/api/conversations/import", headers={"Origin": "http://localhost:5173",
                "Access-Control-Request-Method": "POST", "Access-Control-Request-Headers": "content-type"})
            assert preflight.status_code == 200
            assert preflight.headers["access-control-allow-origin"] == "http://localhost:5173"
            if args.synthetic:
                checked(client.post("/api/conversations/import", json=dialogue))
            queued = checked(client.post("/api/conversations/" + quote(dialogue["conversation_id"], safe="") +
                "/evaluations", json={"checklist_version": "1"}), 202)
            path = "/api/evaluations/" + queued["evaluation_id"]
            deadline = time.monotonic() + 15
            while time.monotonic() < deadline:
                result = checked(client.get(path))
                if result["status"] not in {"queued", "running"}:
                    break
                time.sleep(0.02)
            assert result["status"] in {"completed", "needs_review"}, result["error"]
            assert result["original"]["assessment"] == assessment
            assert len(result["final"]["assessment"]["criteria"]) == 8
            if not args.synthetic:
                actual = result["final"]["calculation"]["total_score"]
                expected = saved["calculation"]["total_score"]
                assert (actual is None and expected is None) or Decimal(str(actual)) == Decimal(str(expected))
            row = assessment["criteria"][0]
            appeal = checked(client.post(path + "/appeals", json={"criterion_id": row["criterion_id"],
                "comment": "Offline smoke confirmation", "expected_revision": result["revision"]}), 201)
            resolved = checked(client.post("/api/appeals/" + appeal["appeal_id"] + "/resolve", json={
                "decision": "confirm", "reason": "Offline smoke; no model or score changes",
                "replacement": None, "expected_revision": result["revision"]}))
            result = resolved["evaluation"]
            assert result["original"] == result["final"]
            assert checked(client.get("/api/appeals?status=resolved"))["total"] == 1
        with serve(app()) as client:
            assert checked(client.get(path)) == result
            assert checked(client.get("/api/conversations?limit=200"))["total"] == (66 if args.synthetic else 65)
        transport.assert_called_once()
    print(json.dumps({"ok": True, "real_http": True, "sqlite_restart": True, "dev_imported": 65,
                      "saved_official_ok": sum(r["status"] == "ok" for r in submission["results"]),
                      "saved_official_errors": sum(r["status"] == "error" for r in submission["results"]),
                      "evaluation_source": "synthetic_fixture" if args.synthetic else "saved_real_assessment_replay",
                      "remote_model_calls": 0, "provider_transport_mocked": True,
                      "raw_inference_provenance_verified": False,
                      "cors": True, "appeal": True}, indent=2))


if __name__ == "__main__":
    main()
