"""Sequential, durable batch processing using the existing evaluator and validator."""
from collections import Counter
from collections.abc import Mapping
from contextlib import contextmanager
from copy import deepcopy
from decimal import Decimal
import json
import os
from numbers import Integral
from pathlib import Path
import tempfile

from .core import calculate_score
from .data import InputError, dumps, fingerprint, read_json
from .evaluator import EvaluationError, evaluate_dialogue, prepare_request, request_body
from .validation import validate_response


def atomic_write(path, value):
    """Flush a complete sibling temporary file before atomic replacement."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix=".pending-", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as stream:
            stream.write(dumps(value, indent=2) + "\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(name, path)
    finally:
        if os.path.exists(name):
            os.unlink(name)


@contextmanager
def run_lock(directory):
    """OS advisory lock is released even on process death; no stale PID removal."""
    directory.mkdir(parents=True, exist_ok=True)
    with (directory / ".lock").open("a+b") as stream:
        stream.seek(0, os.SEEK_END)
        if stream.tell() == 0:
            stream.write(b"0")
            stream.flush()
        stream.seek(0)
        try:
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            raise InputError("Run directory is locked by another process") from None
        try:
            yield
        finally:
            stream.seek(0)
            if os.name == "nt":
                msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(stream.fileno(), fcntl.LOCK_UN)


def validate_chat_template_kwargs(value):
    """Template variables only; never override tokenizer/counting controls."""
    if not isinstance(value, dict) or any(not isinstance(key, str) for key in value):
        raise InputError("--chat-template-kwargs must be a JSON object")
    reserved = {"conversation", "messages", "tools", "documents", "chat_template",
                "add_generation_prompt", "continue_final_message", "tokenize", "padding",
                "truncation", "max_length", "return_tensors", "return_dict",
                "return_assistant_tokens_mask", "tokenizer_kwargs"}
    if reserved.intersection(value):
        raise InputError("--chat-template-kwargs contains reserved tokenizer/counting options")
    try:
        json.dumps(value, allow_nan=False)
    except (TypeError, ValueError):
        raise InputError("--chat-template-kwargs must contain JSON values only") from None
    return deepcopy(value)


def _count_input_ids(encoded):
    """Count one complete token sequence, never mapping keys or batch rows."""
    if isinstance(encoded, Mapping):
        if "input_ids" not in encoded:
            raise InputError("Tokenizer output has no input_ids")
        encoded = encoded["input_ids"]
    shape = getattr(encoded, "shape", None)
    if shape is not None:
        # Tensor/ndarray: the final dimension is the sequence length.
        # This caller sends one conversation, so multiple batch rows are invalid.
        if len(shape) not in (1, 2) or (len(shape) == 2 and shape[0] != 1):
            raise InputError("Tokenizer must return exactly one token sequence")
        count = shape[-1]
    elif isinstance(encoded, (list, tuple)):
        if len(encoded) == 1 and isinstance(encoded[0], (list, tuple)):
            encoded = encoded[0]
        if any(isinstance(token, bool) or not isinstance(token, Integral) or token < 0 for token in encoded):
            raise InputError("Tokenizer returned invalid input_ids")
        count = len(encoded)
    else:
        raise InputError("Tokenizer returned unsupported input_ids")
    if isinstance(count, bool) or not isinstance(count, Integral) or count <= 0:
        raise InputError("Tokenizer returned an invalid token count")
    return int(count)


def load_token_counter(path, *, chat_template_kwargs=None):
    """Local tokenizer only. No model weights, downloads or remote Python code."""
    kwargs = validate_chat_template_kwargs({} if chat_template_kwargs is None else chat_template_kwargs)
    if not Path(path).is_dir():
        raise InputError("--tokenizer must point to the local serving tokenizer directory")
    try:
        from transformers import AutoTokenizer
    except ImportError:
        raise InputError("Token preflight requires transformers in this environment") from None
    try:
        tokenizer = AutoTokenizer.from_pretrained(str(path), local_files_only=True, trust_remote_code=False)
    except (OSError, ValueError):
        raise InputError("Cannot load local tokenizer; check files and transformers version") from None
    def count(prepared):
        messages = [{"role": key, "content": prepared[key]} for key in ("system", "user")]
        try:
            encoded = tokenizer.apply_chat_template(messages, tokenize=True, add_generation_prompt=True, **kwargs)
        except (ValueError, TypeError, ImportError):
            raise InputError("Cannot apply local chat template; it must match the serving template") from None
        return _count_input_ids(encoded)
    # The same variables used to count are bound into the durable run identity.
    count.chat_template_kwargs = kwargs
    return count


def preflight(records, checklist, settings, *, context_window, margin=512, token_counter=None):
    if not records:
        raise InputError("Input dataset is empty")
    if context_window <= 0 or margin < 0 or settings.max_output_tokens is None:
        raise InputError("Positive context-window/max-output-tokens and nonnegative margin are required")
    template_kwargs = validate_chat_template_kwargs(getattr(token_counter, "chat_template_kwargs", {}))
    rows = []
    for dialogue in records:
        prepared = prepare_request(dialogue, checklist)
        # Diagnostic estimate only; never grants permission for a live run.
        count = (token_counter(prepared) if token_counter else
                 len((prepared["system"] + prepared["user"]).encode("utf-8")))
        if type(count) is not int or count <= 0:
            raise InputError("Tokenizer returned an invalid token count")
        required = count + settings.max_output_tokens + margin
        rows.append({"conversation_id": dialogue["conversation_id"], "input_tokens": count,
                     "required_context": required, "fits": required <= context_window,
                     "dialogue_hash": fingerprint(dialogue), "prompt_hash": fingerprint(prepared),
                     "request_hash": fingerprint(request_body(prepared, settings))})
    report = {"records": len(rows), "token_count_method": "local_chat_template" if token_counter else "utf8_bytes_estimate_only",
            "context_window": context_window, "max_output_tokens": settings.max_output_tokens,
            "margin": margin, "missing_settings": settings.missing(),
            "max_input_tokens": max(r["input_tokens"] for r in rows),
            "oversized_ids": [r["conversation_id"] for r in rows if not r["fits"]],
            "ready": bool(token_counter) and settings.api_style == "chat_completions" and not settings.missing() and all(r["fits"] for r in rows),
            "items": rows}
    # Preserve legacy manifests when no template variables were overridden.
    if template_kwargs:
        report["chat_template_kwargs"] = template_kwargs
    return report


def _validated_saved(saved, dialogue, checklist, item):
    validated = validate_response(saved["assessment"], dialogue, checklist)
    for key in ("dialogue_hash", "prompt_hash", "request_hash"):
        if saved["metadata"].get(key) != item[key]:
            raise InputError("Saved result identity mismatch; use a new run directory")
    if saved.get("checklist_snapshot") != checklist:
        raise InputError("Saved checklist snapshot mismatch")
    score = calculate_score(checklist, validated)
    # Decimal is serialized as a string by the existing CLI contract.
    if fingerprint(saved["calculation"]) != fingerprint(score) or saved["critical_error"] != score["gross_error"]:
        raise InputError("Saved calculation differs from the validated assessment")
    return score


def _record_state(directory, dialogue, checklist, item):
    attempts = sorted(directory.glob("attempt-*")) if directory.exists() else []
    successes = []
    for attempt in attempts:
        if (attempt / "result.json").exists():
            saved = read_json(attempt / "result.json")
            score = _validated_saved(saved, dialogue, checklist, item)
            successes.append((attempt, score))
    if len(successes) > 1:
        raise InputError("Multiple successful results for one dialogue; refusing ambiguous resume")
    if successes:
        path, score = successes[0]
        return {"state": "validated", "result": str(path / "result.json"),
                "assessment_status": score["status"], "total_score": score["total_score"]}
    if not attempts:
        return {"state": "pending"}
    if (attempts[-1] / "error.json").exists():
        return {"state": "error"}
    return {"state": "interrupted"}


def _summary(directory, records, checklist, report, *, states=None):
    items = states if states is not None else []
    if states is None:
        for index, (dialogue, item) in enumerate(zip(records, report["items"]), 1):
            state = _record_state(directory / "records" / f"{index:04d}", dialogue, checklist, item)
            items.append({"conversation_id": dialogue["conversation_id"], **state})
    counts = Counter(r["state"] for r in items)
    needs_review = sum(i.get("assessment_status") == "needs_review" for i in items)
    not_calculable = sum(i.get("assessment_status") == "not_calculable" for i in items)
    # Each started request remains in the accounting, including interrupted requests.
    starts = list(directory.glob("records/*/attempt-*/attempt_started-*.json"))
    finishes = list(directory.glob("records/*/attempt-*/attempt_finished-*.json"))
    completed_attempts = [read_json(path) for path in finishes]
    unknown = len(starts) != len(finishes)
    costs = [a.get("cost_usd") for a in completed_attempts]
    usage = {}
    for key in ("input_tokens", "output_tokens", "total_tokens"):
        values = [(a.get("usage") or {}).get(key) for a in completed_attempts]
        usage[key] = None if unknown or any(v is None for v in values) else sum(values)
    return {"records": len(records), "counts": {s: counts[s] for s in ("validated", "error", "interrupted", "pending")},
            "complete": counts["validated"] == len(records),
            "ready_for_handoff": counts["validated"] == len(records) and not needs_review and not not_calculable,
            "needs_review": needs_review, "not_calculable": not_calculable,
            "api_attempts_started": len(starts), "api_attempts_finished": len(finishes),
            "usage": usage,
            "cost_usd": None if unknown or any(c is None for c in costs) else sum((Decimal(str(c)) for c in costs), Decimal(0)),
            "submission_format_verified": False, "items": items}


def run_batch(records, checklist, settings, directory, *, context_window, margin=512,
              token_counter=None, resume=False, retry_errors=False, retry_interrupted=False,
              limit=None, progress=None, initialize_only=False):
    settings.validate()
    if settings.api_style != "chat_completions":
        raise InputError("Batch token preflight currently requires chat_completions")
    if limit is not None and limit <= 0:
        raise InputError("--limit must be positive")
    if initialize_only and (resume or retry_errors or retry_interrupted or limit is not None):
        raise InputError("Initialization requires a new run without resume, retry or limit options")
    if (retry_errors or retry_interrupted) and not resume:
        raise InputError("Retry flags require --resume")
    report = preflight(records, checklist, settings, context_window=context_window,
                       margin=margin, token_counter=token_counter)
    if not report["ready"]:
        raise InputError("Batch preflight failed: use the serving tokenizer and check context budget/settings")
    # No secrets or URLs in the manifest. Endpoint identity is hashed.
    identity = {"format": "cqa-batch-1", "checklist_hash": fingerprint(checklist),
                "endpoint_hash": fingerprint(settings.base_url), "model": settings.model,
                "api_style": settings.api_style, "structured_output": settings.structured_output,
                "max_attempts": settings.max_attempts, "timeout": settings.timeout,
                "prices": [settings.input_price, settings.output_price],
                "preflight": {k: v for k, v in report.items() if k != "missing_settings"}}
    directory = Path(directory)
    with run_lock(directory):
        manifest = directory / "manifest.json"
        if manifest.exists():
            if not resume:
                raise InputError("Run exists; use --resume or a new run directory")
            if fingerprint(read_json(manifest)) != fingerprint(identity):
                raise InputError("Input, model, prompt, tokenizer budget or settings changed; use a new run directory")
        else:
            if resume:
                raise InputError("Cannot resume: manifest.json is missing")
            if any(p.name != ".lock" for p in directory.iterdir()):
                raise InputError("New run requires an empty directory")
            atomic_write(manifest, identity)
        # Validate all existing successes before issuing any further API calls.
        status = _summary(directory, records, checklist, report)
        if initialize_only:
            # Persist every input as pending before the inference window opens.
            # Normal resume uses the same manifest and existing validation.
            atomic_write(directory / "summary.json", status)
            return status
        processed = 0
        for index, (dialogue, item, current) in enumerate(zip(records, report["items"], status["items"]), 1):
            state = current["state"]
            if state == "validated" or (state == "error" and not retry_errors) or (state == "interrupted" and not retry_interrupted):
                continue
            if limit is not None and processed >= limit:
                break
            record_dir = directory / "records" / f"{index:04d}"
            number = len(list(record_dir.glob("attempt-*"))) + 1
            attempt_dir = record_dir / f"attempt-{number:04d}"
            attempt_dir.mkdir(parents=True, exist_ok=False)
            atomic_write(attempt_dir / "started.json", {"conversation_id": dialogue["conversation_id"], "request_hash": item["request_hash"]})
            def journal(event, value):
                atomic_write(attempt_dir / f"{event}-{value['attempt']:02d}.json", value)
            try:
                result = evaluate_dialogue(dialogue, checklist, settings=settings, on_event=journal)
                _validated_saved(result, dialogue, checklist, item)
            except EvaluationError as exc:
                atomic_write(attempt_dir / "error.json", {"conversation_id": dialogue["conversation_id"],
                                                         "error": str(exc), "metadata": exc.metadata})
                fatal = any(a.get("error") in {"http_400", "http_401", "http_403", "http_404", "http_422"}
                            for a in exc.metadata.get("attempts", []))
            else:
                atomic_write(attempt_dir / "result.json", result)
                fatal = False
            processed += 1
            status["items"][index - 1] = {"conversation_id": dialogue["conversation_id"],
                                          **_record_state(record_dir, dialogue, checklist, item)}
            status = _summary(directory, records, checklist, report, states=status["items"])
            atomic_write(directory / "summary.json", status)
            if progress:
                progress({"processed_this_run": processed, "counts": status["counts"]})
            if fatal:
                break
        status = _summary(directory, records, checklist, report, states=status["items"])
        atomic_write(directory / "summary.json", status)
        return status


def collect_results(records, checklist, directory):
    """Internal handoff bundle, deliberately NOT an organizer submission format."""
    directory = Path(directory)
    if not directory.is_dir():
        raise InputError("Run directory does not exist")
    with run_lock(directory):
        manifest = read_json(directory / "manifest.json")
        report = manifest["preflight"]
        if manifest["checklist_hash"] != fingerprint(checklist) or len(report["items"]) != len(records):
            raise InputError("Dataset/checklist differs from the batch manifest")
        for record, item in zip(records, report["items"]):
            if item["dialogue_hash"] != fingerprint(record):
                raise InputError("Dataset differs from the batch manifest")
        status = _summary(directory, records, checklist, report)
        if not status["complete"] or status["needs_review"] or status["not_calculable"]:
            raise InputError("Cannot collect: missing, failed or unresolved assessments; inspect summary.json")
        return {"format": "cqa-batch-results-1", "organizer_format_verified": False,
                "manifest_hash": fingerprint(manifest),
                "results": [read_json(item["result"]) for item in status["items"]],
                "summary": status}
