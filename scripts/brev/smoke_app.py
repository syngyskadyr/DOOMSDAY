"""Opt-in synthetic API journey through the real backend; never provisions a GPU.

No flags: show help, no HTTP. --self-check: public fixtures and scorer, no HTTP.
--live: mutate an isolated localhost backend and request exactly two evaluations.
--verify-saved: GET-only persistence check, suitable after a backend restart.
This checks the HTTP API, not browser rendering or human annotation quality.
"""
import argparse
from copy import deepcopy
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import sys
import time
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlsplit
from urllib.request import HTTPRedirectHandler, ProxyHandler, Request, build_opener
from uuid import uuid4


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from backend.ml_adapter import MLAdapter
from cqa_eval.config import validate_checklist
from cqa_eval.data import dumps, read_json


DEFAULT_MODEL = "doomsday-qwen3-14b-awq"
CUSTOM_ID = "synthetic-custom"
POLL_SECONDS = 330


class SmokeFailure(RuntimeError):
    """Messages are controlled locally and contain no server responses."""


def require(condition, message):
    if not condition:
        raise SmokeFailure(message)


def checked_base_url(value):
    parsed = urlsplit(value)
    require(parsed.scheme == "http" and parsed.hostname in {"localhost", "127.0.0.1", "::1"},
            "Only an HTTP localhost backend is allowed")
    require(not parsed.username and not parsed.password and not parsed.query and not parsed.fragment
            and parsed.path in {"", "/"} and parsed.port is not None,
            "Use a plain localhost origin with an explicit port")
    return value.rstrip("/")


def private_write(path, value):
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
        stream.write(dumps(value, indent=2) + "\n")


def replace_state(directory, state):
    temporary = directory / (".state-" + uuid4().hex + ".json")
    private_write(temporary, state)
    os.replace(temporary, directory / "state.json")


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, request, response, code, message, headers, new_url):
        return None


class API:
    def __init__(self, base_url, directory, read_only=False):
        self.base_url = checked_base_url(base_url)
        self.directory = directory
        self.read_only = read_only
        self.sequence = 0
        # A localhost check must neither use an external proxy nor follow redirects.
        self.opener = build_opener(ProxyHandler({}), NoRedirect())

    def request(self, method, path, body=None, expected=200, label=None, timeout=15):
        require(not self.read_only or method == "GET", "Persistence checks permit only GET")
        require(path.startswith("/api/"), "Unexpected backend API path")
        if label:
            self.sequence += 1
            prefix = f"{self.sequence:03d}-{label}"
            private_write(self.directory / (prefix + "-request.json"),
                          {"method": method, "path": path, "body": body,
                           "synthetic": True, "may_mutate": method != "GET"})
        encoded = None if body is None else dumps(body).encode("utf-8")
        request = Request(self.base_url + path, data=encoded, method=method,
                          headers={"Content-Type": "application/json"})
        try:
            with self.opener.open(request, timeout=timeout) as response:
                code = response.status
                raw = response.read(4 * 1024 * 1024 + 1)
        except HTTPError as error:
            raise SmokeFailure(f"Backend HTTP {error.code}; no automatic retry") from None
        except (URLError, TimeoutError, OSError):
            raise SmokeFailure("Backend transport failed; request outcome may be unknown; no automatic retry") from None
        require(code == expected, f"Unexpected backend HTTP status; expected {expected}")
        require(len(raw) <= 4 * 1024 * 1024, "Backend response exceeded the smoke limit")
        try:
            result = json.loads(raw)
        except (ValueError, UnicodeError):
            raise SmokeFailure("Backend response was not JSON") from None
        if label:
            private_write(self.directory / (prefix + "-response.json"), result)
        return result


def fixture_dialogue(conversation_id):
    record = read_json(ROOT / "codex_context/eval/synthetic_dialogue.json")
    require(record.get("synthetic") is True and record.get("audio_url") is None,
            "Expected the public synthetic fixture without audio")
    record["conversation_id"] = conversation_id
    return record


def with_custom_criterion(checklist):
    edited = deepcopy(checklist)
    require(not any(row["id"] == CUSTOM_ID for row in edited["criteria"]),
            "The isolated checklist already contains the smoke criterion")
    template = next((row for row in edited["criteria"]
                     if row["kind"] == "binary" and not row["na_allowed"]), None)
    require(template is not None, "No suitable binary template in checklist")
    criterion = deepcopy(template)
    criterion.update(
        id=CUSTOM_ID, title="Синтетический: оператор указал раздел «Помощь»", weight=3,
        instruction=("СИНТЕТИЧЕСКИЙ ПРОВЕРОЧНЫЙ КРИТЕРИЙ. Проверьте только наличие слова «Помощь» "
                     "в исходных репликах оператора без учёта регистра. yes — есть; no — нет. "
                     "Не оценивайте продуктовые факты. Укажите реплику-доказательство."),
        allowed_answers=["yes", "no"], na_allowed=False, na_rule=None,
        scale_max=None, levels=[], flag_types=[],
    )
    edited["criteria"].append(criterion)
    validate_checklist(edited)
    return edited


def check_messages(detail, dialogue):
    require(detail.get("synthetic") is True, "Backend lost the synthetic data label")
    messages = detail["messages"]
    require(len(messages) == len(dialogue["transcript"]), "Transcript length changed")
    for index, (message, source) in enumerate(zip(messages, dialogue["transcript"])):
        require(message["message_id"] == f"m{index + 1:03d}" and message["source_index"] == index,
                "Message IDs or source order changed")
        require(all(message[key] == source[key] for key in ("timestamp", "speaker", "text")),
                "Backend altered synthetic transcript text")
    return messages


def verify_evidence(assessment, messages):
    positions = {message["message_id"]: index for index, message in enumerate(messages)}
    for row in assessment["criteria"]:
        ids = list(row["evidence_message_ids"])
        for violation in row["violations"]:
            ids.extend(violation["evidence_message_ids"])
        require(all(message_id in positions for message_id in ids), "Evidence points outside the transcript")
        span = row["checked_range"]
        if span is not None:
            start, end = span["start_message_id"], span["end_message_id"]
            require(start in positions and end in positions and positions[start] <= positions[end],
                    "Evidence range is invalid")


def verify_evaluation(evaluation, dialogue, checklist, messages, model):
    require(evaluation.get("synthetic") is True, "Evaluation lost the synthetic label")
    require(evaluation["status"] in {"completed", "needs_review"}, "Evaluation did not yield a valid result")
    require(evaluation["checklist_snapshot"] == checklist, "Evaluation snapshot changed")
    require(evaluation["checklist_version"] == checklist["version"], "Evaluation uses an unexpected version")
    metadata = evaluation.get("metadata") or {}
    require(metadata.get("model") == model, "Evaluation did not report the expected live model")
    require(metadata.get("api_calls") == 1 and metadata.get("retry_count") == 0,
            "Expected one real model call, without retry or test fallback")
    adapter = MLAdapter()
    for name in ("original", "final"):
        state = evaluation[name]
        reproduced = adapter.result_state(dialogue, checklist, state["assessment"])
        require(reproduced == state, "Shared validator/scorer did not reproduce the stored result")
        verify_evidence(state["assessment"], messages)


def evaluate(api, dialogue, checklist, model, state):
    state["evaluation_posts_attempted"] += 1
    replace_state(api.directory, state)
    queued = api.request("POST", f"/api/conversations/{quote(dialogue['conversation_id'], safe='')}/evaluations",
                         {"checklist_version": checklist["version"]}, expected=202,
                         label="evaluate-" + str(state["evaluation_posts_attempted"]))
    require(queued["checklist_snapshot"] == checklist, "Queued task lost its checklist snapshot")
    evaluation_id = queued["evaluation_id"]
    state.setdefault("evaluation_ids", []).append(evaluation_id)
    replace_state(api.directory, state)
    deadline = time.monotonic() + POLL_SECONDS
    while time.monotonic() < deadline:
        remaining = deadline - time.monotonic()
        evaluation = api.request("GET", "/api/evaluations/" + quote(evaluation_id, safe=""),
                                 timeout=min(15, max(0.01, remaining)))
        if evaluation["status"] not in {"queued", "running"}:
            private_write(api.directory / f"evaluation-{len(state['evaluation_ids'])}.json", evaluation)
            return evaluation
        time.sleep(min(1, max(0, deadline - time.monotonic())))
    raise SmokeFailure("Evaluation remained pending after 330 seconds; no resubmission was made")


def compact_summary(state, mode):
    evaluations = state.get("evaluations", [])
    failed = state.get("failed_evaluations", [])
    metadatas = [item.get("metadata") or {} for item in evaluations + failed]
    usage = {}
    for key in ("input_tokens", "output_tokens"):
        values = [(metadata.get("usage") or {}).get(key) for metadata in metadatas]
        usage[key] = sum(values) if values and all(type(value) is int for value in values) else None
    return {"ok": True, "mode": mode, "synthetic": True, "browser_verified": False,
            "conversation_id": state["dialogue"]["conversation_id"],
            "model": state["expected_model"], "model_calls_recorded": sum(item.get("api_calls", 0) for item in metadatas),
            "evaluation_posts_attempted": state.get("evaluation_posts_attempted", 0),
            "failed_evaluations_retained": len(failed),
            "scores": [item["final"]["calculation"]["total_score"] for item in evaluations],
            "statuses": [item["status"] for item in evaluations], "usage": usage,
            "schema_and_scorer_verified": True, "appeal_confirmed": True,
            "new_criterion_verified": True, "historical_snapshot_preserved": True,
            "persistence_verified": mode == "verify-saved", "estimated_token_cost_usd": None,
            "gpu_cost_usd": None, "note": "Automated synthetic API check, not human quality assessment"}


def run_live(args):
    require(args.run_dir is not None, "--run-dir is required with --live")
    directory = args.run_dir
    # Only a new run directory is accepted; existing evidence is never overwritten.
    directory.mkdir(mode=0o700, parents=True, exist_ok=False)
    os.chmod(directory, 0o700)
    api = API(args.base_url, directory)
    state = {"format": "doomsday-synthetic-app-smoke-1", "synthetic": True,
             "created_at": datetime.now(timezone.utc).isoformat(), "base_url": api.base_url,
             "expected_model": args.expected_model, "evaluation_posts_attempted": 0,
             "complete": False, "dialogue": fixture_dialogue("synthetic-brev-app-" + uuid4().hex[:12])}
    replace_state(directory, state)
    health = api.request("GET", "/api/health", label="health-config-only")
    require(health.get("evaluator_status") == "ready", "Backend settings are not ready")
    registry = api.request("GET", "/api/conversations?limit=1")
    require(registry.get("total") == 0, "Use a new isolated SQLite database; conversations already exist")
    dialogue = state["dialogue"]
    imported = api.request("POST", "/api/conversations/import", dialogue, label="import-synthetic")
    require(imported == {"imported": 1, "skipped": 0, "conversation_ids": [dialogue["conversation_id"]]},
            "Unexpected synthetic import result")
    path = "/api/conversations/" + quote(dialogue["conversation_id"], safe="")
    detail = api.request("GET", path, label="synthetic-conversation")
    messages = check_messages(detail, dialogue)
    first_checklist = api.request("GET", "/api/checklists/current", label="checklist-v1")
    state["checklists"] = [first_checklist]
    replace_state(directory, state)
    first = evaluate(api, dialogue, first_checklist, args.expected_model, state)
    verify_evaluation(first, dialogue, first_checklist, messages, args.expected_model)
    require(first["original"] == first["final"], "Initial original and final differ")
    criterion_id = next((row["criterion_id"] for row in first["final"]["assessment"]["criteria"]
                         if row["status"] == "evaluated"), None)
    require(criterion_id is not None, "No evaluated criterion is available for a confirm smoke")
    appeal = api.request("POST", f"/api/evaluations/{quote(first['evaluation_id'], safe='')}/appeals",
                         {"criterion_id": criterion_id, "expected_revision": first["revision"],
                          "comment": "Синтетическая автоматическая проверка механизма апелляции; не экспертная разметка."},
                         expected=201, label="appeal-create")
    resolved = api.request("POST", f"/api/appeals/{quote(appeal['appeal_id'], safe='')}/resolve",
                           {"decision": "confirm", "replacement": None, "expected_revision": first["revision"],
                            "reason": "Автоматический синтетический smoke подтверждения; человеческая экспертиза не выполнялась."},
                           label="appeal-confirm")
    confirmed = resolved["evaluation"]
    require(confirmed["original"] == first["original"] and confirmed["final"] == first["final"]
            and confirmed["metadata"] == first["metadata"], "Confirm changed the original result or metadata")
    require(confirmed["revision"] == first["revision"] + 1 and resolved["appeal"]["status"] == "resolved",
            "Confirm did not persist its revision and decision")
    verify_evaluation(confirmed, dialogue, first_checklist, messages, args.expected_model)
    state["appeal_id"] = appeal["appeal_id"]
    state["evaluations"] = [confirmed]
    replace_state(directory, state)
    edited = with_custom_criterion(first_checklist)
    second_checklist = api.request("POST", "/api/checklists",
                                   {"base_version": first_checklist["version"], "checklist": edited},
                                   expected=201, label="checklist-v2-create")
    require(second_checklist["version"] != first_checklist["version"], "Checklist version did not advance")
    state["checklists"].append(second_checklist)
    replace_state(directory, state)
    second = evaluate(api, dialogue, second_checklist, args.expected_model, state)
    verify_evaluation(second, dialogue, second_checklist, messages, args.expected_model)
    require(second["original"] == second["final"], "Second original and final differ")
    added = next((row for row in second["final"]["assessment"]["criteria"] if row["criterion_id"] == CUSTOM_ID), None)
    require(added is not None and added["answer"] == "yes", "Model did not apply the explicit synthetic custom criterion")
    require(api.request("GET", "/api/evaluations/" + quote(first["evaluation_id"], safe="")) == confirmed,
            "A new checklist or evaluation altered the historical evaluation")
    state["evaluations"].append(second)
    state["conversation_detail"] = api.request("GET", path, label="final-synthetic-conversation")
    check_messages(state["conversation_detail"], dialogue)
    require(state["conversation_detail"]["latest_evaluation_id"] == second["evaluation_id"], "Latest evaluation is incorrect")
    state["complete"] = True
    replace_state(directory, state)
    summary = compact_summary(state, "live")
    private_write(directory / "summary.json", summary)
    print(dumps(summary))
    return 0


def verify_saved(args):
    require(args.run_dir is not None and args.run_dir.is_dir(), "--run-dir must name the completed saved smoke")
    state = read_json(args.run_dir / "state.json")
    require(state.get("format") == "doomsday-synthetic-app-smoke-1" and state.get("synthetic") is True
            and state.get("complete") is True, "A completed synthetic smoke state is required")
    require(state["dialogue"] == fixture_dialogue(state["dialogue"]["conversation_id"]),
            "Saved input differs from the public synthetic fixture")
    require(re.fullmatch(r"synthetic-brev-app-[0-9a-f]{12}", state["dialogue"]["conversation_id"]) is not None,
            "Unexpected saved synthetic conversation ID")
    require(checked_base_url(args.base_url) == state["base_url"], "Persistence checks require the original backend origin")
    require(len(state["evaluations"]) == 2 and len(state["checklists"]) == 2, "Incomplete saved journey")
    # No writes and no POSTs: repeated checks leave both database and run files intact.
    api = API(args.base_url, args.run_dir, read_only=True)
    conversation_id = state["dialogue"]["conversation_id"]
    detail = api.request("GET", "/api/conversations/" + quote(conversation_id, safe=""))
    require(detail == state["conversation_detail"], "Synthetic conversation/history did not survive restart unchanged")
    messages = check_messages(detail, state["dialogue"])
    for checklist, expected in zip(state["checklists"], state["evaluations"]):
        stored = api.request("GET", "/api/evaluations/" + quote(expected["evaluation_id"], safe=""))
        require(stored == expected, "Saved evaluation/appeal changed after restart")
        require(api.request("GET", "/api/checklists/" + quote(checklist["version"], safe="")) == checklist,
                "Saved checklist changed after restart")
        verify_evaluation(stored, state["dialogue"], checklist, messages, state["expected_model"])
    for expected in state.get("failed_evaluations", []):
        stored = api.request("GET", "/api/evaluations/" + quote(expected["evaluation_id"], safe=""))
        require(stored == expected, "Failed evaluation history changed after restart")
    require(api.request("GET", "/api/checklists/current") == state["checklists"][-1],
            "The new checklist is no longer current")
    summary = compact_summary(state, "verify-saved")
    summary["new_model_calls"] = 0
    summary["note"] = "GET-only persistence verification; caller must actually restart backend before claiming restart verification"
    print(dumps(summary))
    return 0


def self_check():
    dialogue = read_json(ROOT / "codex_context/eval/synthetic_dialogue.json")
    checklist = read_json(ROOT / "codex_context/checklist.json")
    fixture = read_json(ROOT / "codex_context/eval/synthetic_response.json")
    state = MLAdapter().result_state(dialogue, checklist, fixture)
    with_custom_criterion(checklist)
    require(checked_base_url("http://127.0.0.1:8000/") == "http://127.0.0.1:8000", "URL guard failed")
    for blocked in ("https://example.com", "http://127.0.0.1:8000@evil.test", "http://127.0.0.1:8000/api",
                    "http://user:secret@127.0.0.1:8000", "http://127.0.0.1:8000?secret=test"):
        try:
            checked_base_url(blocked)
        except (SmokeFailure, ValueError):
            continue
        raise SmokeFailure("URL guard accepted an invalid origin")
    print(dumps({"ok": True, "mode": "offline-self-check", "synthetic": True, "http_calls": 0,
                 "model_calls": 0, "schema_and_scorer_verified": True,
                 "custom_checklist_valid": True, "total_score": state["calculation"]["total_score"]}))
    return 0


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    modes = parser.add_mutually_exclusive_group()
    modes.add_argument("--live", action="store_true", help="Opt into two real model calls on an isolated backend")
    modes.add_argument("--verify-saved", action="store_true", help="GET-only check of a completed saved journey")
    modes.add_argument("--self-check", action="store_true", help="Public fixture/scorer and URL guard; no HTTP")
    parser.add_argument("--base-url", default="http://127.0.0.1:8000", help="Plain localhost backend origin")
    parser.add_argument("--run-dir", type=Path, help="New private directory for --live; existing directory for --verify-saved")
    parser.add_argument("--expected-model", default=DEFAULT_MODEL, help="Expected real served-model-name")
    args = parser.parse_args()
    if args.self_check:
        return self_check()
    checked_base_url(args.base_url)
    if args.verify_saved:
        return verify_saved(args)
    if args.live:
        return run_live(args)
    parser.print_help()
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except SmokeFailure as error:
        print(dumps({"ok": False, "error": str(error), "no_automatic_retry": True}))
        raise SystemExit(2)
    except Exception as error:
        # Never echo backend bodies, request contents, environment or tracebacks.
        print(dumps({"ok": False, "error_type": type(error).__name__,
                     "message": "Synthetic app smoke failed; inspect private run files; no automatic retry"}))
        raise SystemExit(2)
