"""Six explicit CLI scenarios. All defaults point only to codex_context."""
import argparse
from dataclasses import replace
from pathlib import Path
import sys
from .config import validate_checklist
from .core import calculate_score
from .data import InputError, dumps, load_dev, normalize_dialogue, read_json, select_dialogue, summary
from .evaluator import EvaluationError, PersistenceError, Settings, evaluate_dialogue, prepare_request
from .batch import atomic_write, collect_results, load_token_counter, preflight, run_batch
from .quality import compare_predictions, prepare_review
from .validation import extract_evidence, validate_response


def parser():
    p = argparse.ArgumentParser(description="CQA evaluator; canonical materials: codex_context")
    p.add_argument("--context", type=Path, default=Path("codex_context"), help="Canonical material directory")
    sub = p.add_subparsers(dest="command", required=True)
    for name, description in (("inspect-dev", "Inspect summary, IDs and selected transcript"),
                              ("dry-run", "Prepare model request without sending"),
                              ("validate", "Validate saved assessment offline"),
                              ("score", "Calculate validated answers offline"),
                              ("evaluate", "Evaluate exactly one selected dialogue via API"),
                              ("batch", "Process an entire DEV/PROD envelope with durable checkpoints"),
                              ("batch-collect", "Collect complete validated results for internal handoff, offline"),
                              ("eval", "Prepare independent review or compare saved predictions")):
        cmd = sub.add_parser(name, help=description, description=description)
        cmd.add_argument("--dev", "--input", dest="dev", type=Path, help="DEV/PROD envelope; default CONTEXT/data/dev.json")
        cmd.add_argument("--checklist", type=Path, help="Checklist; default CONTEXT/checklist.json")
        cmd.add_argument("--out", type=Path, help="Write JSON here instead of stdout")
        if name not in {"eval", "batch", "batch-collect"}:
            cmd.add_argument("--id", required=name in {"dry-run", "evaluate"}, help="Explicit conversation_id")
            cmd.add_argument("--dialogue", type=Path, help="Single dialogue JSON, e.g. synthetic fixture")
        if name in {"validate", "score"}:
            cmd.add_argument("--response", type=Path, required=True, help="Strict assessment or backend wrapper JSON")
        if name == "evaluate":
            # Require a destination so paid successes and processing errors persist.
            cmd.add_argument("--save", type=Path, required=True, help="Save result/error plus call metadata")
        if name in {"batch", "batch-collect"}:
            cmd.add_argument("--run-dir", type=Path, required=True)
            cmd.add_argument("--expected-count", type=int, help="Fail if input count differs; no fixed DEV/PROD size")
        if name == "batch":
            cmd.add_argument("--preflight", action="store_true", help="Inspect all requests offline; never calls API")
            cmd.add_argument("--tokenizer", type=Path, help="Local tokenizer directory matching the serving model/template")
            cmd.add_argument("--context-window", type=int, required=True, help="Actual server max-model-len")
            cmd.add_argument("--max-output-tokens", type=int, required=True, help="Explicit generation cap, including thinking")
            cmd.add_argument("--context-margin", type=int, default=512)
            cmd.add_argument("--resume", action="store_true")
            cmd.add_argument("--retry-errors", action="store_true", help="Explicitly retry previously failed records")
            cmd.add_argument("--retry-interrupted", action="store_true", help="May repeat an already paid in-flight call")
            cmd.add_argument("--limit", type=int, help="Limit newly processed dialogues, e.g. smoke=1")
        if name == "eval":
            cmd.add_argument("mode", choices=["prepare", "compare"])
            cmd.add_argument("--ids", nargs="+", help="Prepare: explicit IDs; default five review-template cases")
            cmd.add_argument("--labels", type=Path, help="Compare: reviewed labels (required)")
            cmd.add_argument("--predictions", nargs="*", type=Path, default=[], help="Saved prediction/error files")
    return p


def _write(value, destination=None):
    text = dumps(value, indent=2) + "\n"
    if destination:
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(text, encoding="utf-8")
    else:
        print(text, end="")


def _dialogue(args, records):
    if args.dialogue:
        dialogue = normalize_dialogue(read_json(args.dialogue))
        if args.id and args.id != dialogue["conversation_id"]:
            raise InputError("--id differs from --dialogue conversation_id")
        return dialogue
    if not args.id:
        raise InputError("Select --id or --dialogue")
    return select_dialogue(records, args.id)


def run(args):
    context = args.context
    checklist = validate_checklist(read_json(args.checklist or context / "checklist.json"))
    records = [] if getattr(args, "dialogue", None) else load_dev(args.dev or context / "data/dev.json")
    if args.command in {"batch", "batch-collect"}:
        if args.expected_count is not None and len(records) != args.expected_count:
            raise InputError(f"Expected {args.expected_count} records, found {len(records)}")
        if args.command == "batch-collect":
            if not args.out or args.out.exists():
                raise InputError("batch-collect requires --out pointing to a NEW file")
            bundle = collect_results(records, checklist, args.run_dir)
            atomic_write(args.out, bundle)
            # main must not write the output a second time.
            args.out = None
            return {"collected": len(bundle["results"]), "organizer_format_verified": False}
        settings = replace(Settings.from_env(), max_output_tokens=args.max_output_tokens)
        settings.validate(require_complete=False)
        counter = load_token_counter(args.tokenizer) if args.tokenizer else None
        options = dict(context_window=args.context_window, margin=args.context_margin, token_counter=counter)
        if args.preflight:
            return preflight(records, checklist, settings, **options)
        if args.out:
            raise InputError("Live batch writes into --run-dir; omit --out")
        return run_batch(records, checklist, settings, args.run_dir, **options,
                         resume=args.resume, retry_errors=args.retry_errors,
                         retry_interrupted=args.retry_interrupted, limit=args.limit,
                         progress=lambda value: print(dumps(value), file=sys.stderr, flush=True))
    if args.command == "inspect-dev":
        result = summary(records)
        if args.id or args.dialogue:
            dialogue = _dialogue(args, records)
            result["dialogue"] = {k: v for k, v in dialogue.items() if k != "audio_url"}
            result["dialogue"]["has_audio"] = bool(dialogue.get("audio_url"))
        return result
    if args.command == "eval":
        if args.mode == "prepare":
            ids = args.ids or [c["conversation_id"] for c in read_json(context / "eval/review_template.json")["cases"]]
            return prepare_review(records, checklist, ids)
        if not args.labels:
            raise InputError("eval compare requires --labels")
        predictions = [read_json(path) for path in args.predictions]
        return compare_predictions(records, checklist, read_json(args.labels), predictions)
    dialogue = _dialogue(args, records)
    if args.command == "dry-run":
        settings = Settings.from_env()
        return {"sent": False, "missing_settings": settings.missing(),
                "api_key_present": bool(settings.api_key), "api_style": settings.api_style or None,
                "model": settings.model or None, "structured_output": settings.structured_output,
                "prepared_request": prepare_request(dialogue, checklist)}
    if args.command == "evaluate":
        if args.out:
            raise InputError("evaluate uses --save; omit --out")
        try:
            result = evaluate_dialogue(dialogue, checklist)
        except EvaluationError as exc:
            _write({"conversation_id": dialogue["conversation_id"], "error": str(exc), "metadata": exc.metadata}, args.save)
            raise
        _write(result, args.save)
        return {"saved": str(args.save), "conversation_id": dialogue["conversation_id"],
                "call_count": result["metadata"]["call_count"], "total_score": result["calculation"]["total_score"]}
    saved = read_json(args.response)
    assessment = saved.get("assessment", saved)
    validated = validate_response(assessment, dialogue, checklist)
    if "metadata" in saved:
        from .data import fingerprint
        if saved["metadata"].get("checklist_hash") not in {None, fingerprint(checklist)}:
            raise InputError("Saved result checklist hash mismatch")
        if saved["metadata"].get("dialogue_hash") not in {None, fingerprint(dialogue)}:
            raise InputError("Saved result dialogue hash mismatch")
    if args.command == "validate":
        return {"valid": True, "conversation_id": dialogue["conversation_id"],
                "criteria": len(assessment["criteria"]), "evidence": extract_evidence(validated, dialogue)}
    return calculate_score(checklist, validated)


def main(argv=None):
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    args = parser().parse_args(argv)
    try:
        result = run(args)
        _write(result, args.out)
        if args.command == "batch":
            return 0 if result.get("ready", result.get("ready_for_handoff", False)) else 2
        return 0
    except (InputError, EvaluationError, PersistenceError) as exc:
        _write({"error": str(exc)}, None)
        return 2
    except KeyboardInterrupt:
        _write({"error": "Interrupted; saved batch results remain. Resume with the same configuration."})
        return 130
    except OSError as exc:
        _write({"error": "Cannot access local file", "path": str(exc.filename) if exc.filename else None})
        return 2
    except (KeyError, TypeError, ArithmeticError):
        # Never emit an untrusted provider body, secret or signed URL in a traceback.
        _write({"error": "Malformed input, inaccessible file, or invalid configuration; check command paths and JSON fields"})
        return 2
