"""Opt-in live checks using public synthetic inputs; never provisions a GPU.

Default mode is offline. Live mode accepts only the agreed localhost tunnel and
requires a new output directory, keeping existing results intact.
"""
import argparse
from dataclasses import replace
import json
import os
from pathlib import Path
import sys
from urllib.parse import urlsplit
from urllib.error import HTTPError

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from backend.environment import load_environment
from cqa_eval import evaluate_dialogue, validate_response, calculate_score
from cqa_eval.data import dumps, read_json, parse_json
from cqa_eval.evaluator import Settings, _http_post, _text


MODEL = "doomsday-qwen3-14b-awq"


def save_private(path, value):
    # Exclusive creation also prevents accidental replacement of old evidence.
    with os.fdopen(os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), "w") as handle:
        handle.write(dumps(value, indent=2) + "\n")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--live", action="store_true", help="Actually call the local vLLM tunnel")
    parser.add_argument("--stage", choices=("small", "cqa"), default="small")
    parser.add_argument("--run-dir", type=Path, help="New private output directory; required with --live")
    args = parser.parse_args()

    # Reuse backend dotenv semantics; never print settings or secret values.
    load_environment()
    settings = Settings.from_env()
    dialogue = read_json(ROOT / "codex_context/eval/synthetic_dialogue.json")
    checklist = read_json(ROOT / "codex_context/checklist.json")
    fixture = read_json(ROOT / "codex_context/eval/synthetic_response.json")
    checked = validate_response(fixture, dialogue, checklist)
    calculate_score(checklist, checked)

    if not args.live:
        print(json.dumps({"mode": "offline", "model_calls": 0,
                          "synthetic_fixture_valid": True, "missing_settings": settings.missing()}))
        return 0

    settings.validate()
    url = urlsplit(settings.base_url)
    if settings.base_url.rstrip("/") != "http://127.0.0.1:8001/v1" or url.username or url.password:
        raise ValueError("Live checks require the agreed 127.0.0.1:8001/v1 SSH tunnel")
    if settings.model != MODEL or settings.api_style != "chat_completions" or settings.structured_output != "json_schema":
        raise ValueError("Unexpected model or API format; review the agreed server configuration")
    if settings.max_attempts != 1:
        raise ValueError("Set LLM_MAX_ATTEMPTS=1 before live checks")
    if not args.run_dir:
        parser.error("--run-dir is required with --live")
    args.run_dir.mkdir(mode=0o700, parents=True, exist_ok=False)

    if args.stage == "small":
        schema = {"type": "object", "properties": {"ok": {"type": "boolean"}},
                  "required": ["ok"], "additionalProperties": False}
        request = {"model": settings.model,
                   "messages": [{"role": "user", "content": "Return a JSON object with ok set to true."}],
                   "max_tokens": 64,
                   "response_format": {"type": "json_schema", "json_schema": {
                       "name": "synthetic_smoke", "strict": True, "schema": schema}}}
        response = _http_post(settings.base_url.rstrip("/") + "/chat/completions", request, settings)
        save_private(args.run_dir / "response.json", response)
        if response.get("model") != settings.model:
            raise ValueError("The server returned an unexpected served-model-name")
        content = parse_json(_text(response, settings.api_style))
        if content != {"ok": True}:
            raise ValueError("Synthetic JSON smoke did not return the expected object")
        summary = {"stage": "small", "model_calls": 1, "model": response.get("model"),
                   "finish_reason": response["choices"][0]["finish_reason"],
                   "clean_json": True, "usage": response.get("usage")}
    else:
        # No client token override: this checks the server-wide generation cap
        # used by the unchanged backend. Token prices intentionally stay unknown.
        def journal(event, value):
            save_private(args.run_dir / f"{event}-{value['attempt']:02d}.json", value)
        result = evaluate_dialogue(dialogue, checklist, settings=replace(
            settings, input_price=None, output_price=None), on_event=journal)
        if result["metadata"].get("model") != settings.model:
            raise ValueError("CQA returned an unexpected served-model-name")
        checked = validate_response(result["assessment"], dialogue, checklist)
        calculation = calculate_score(checklist, checked)
        if calculation != result["calculation"]:
            raise ValueError("Saved score differs from the shared scorer")
        save_private(args.run_dir / "evaluation.json", result)
        summary = {"stage": "cqa", "synthetic": True, "schema_valid": True,
                   "score_reproduced": True, "total_score": str(calculation["total_score"]),
                   "metadata": result["metadata"]}
    save_private(args.run_dir / "summary.json", summary)
    print(dumps(summary))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as error:
        # Exception bodies/provider output can contain credentials; report no
        # raw request, response, environment, or traceback in the terminal.
        print(json.dumps({"ok": False, "error_type": type(error).__name__,
                          "http_status": error.code if isinstance(error, HTTPError) else None,
                          "message": "Smoke failed; no automatic retry. Review configuration and private run files."}))
        raise SystemExit(2)
