"""Offline export of durable batch results to the organizer's JSON 1.0 format.

This adapter deliberately accepts only the unchanged official checklist. It does
not change the configurable application/ML contract, run a model, or publish files.
Rationales are retained for review; source evidence objects and recordings are
never copied into the submission.
"""
from collections import Counter
from contextlib import contextmanager
from decimal import Decimal
from importlib.resources import files
from pathlib import Path
import re

from jsonschema import Draft202012Validator

from .batch import _record_state, _validated_saved, run_lock
from .config import nonempty, validate_checklist
from .data import InputError, fingerprint, normalize_dialogue, parse_json, read_json


CRITERION_CODES = {
    "01": "contact", "02": "discovery", "03": "identification",
    "04": "consultation", "05": "empathy", "06": "dialogue",
    "07": "closure", "08": "critical",
}
# Pin the original snapshot, including conditions, discrete scales and weights.
OFFICIAL_CHECKLIST_HASH = "7e57b9aa24a3ef1bef9c6c7c64ad977402e2986cd710d491376a697fe0a0d13e"
_ERRORS = {
    "error": "Не удалось получить валидную оценку модели.",
    "interrupted": "Оценивание было прервано.",
    "pending": "Оценивание ещё не выполнено.",
    "unresolved": "Оценка требует проверки; итог не подтверждён.",
}


def submission_schema():
    """Return the packaged organizer-output schema (not the model schema)."""
    return parse_json(files("cqa_eval").joinpath("resources/submission.schema.json").read_text(encoding="utf-8"))


def _dataset_records(records, dataset):
    if not isinstance(dataset, str) or dataset not in {"dev", "prod"}:
        raise InputError("Submission dataset must be dev or prod")
    if not isinstance(records, list):
        raise InputError("Submission records must be an array")
    expected = 65 if dataset == "dev" else 10
    if len(records) != expected:
        raise InputError(f"{dataset} submission requires exactly {expected} source records")
    normalized = [normalize_dialogue(record) for record in records]
    ids = [record["conversation_id"] for record in normalized]
    if len(set(ids)) != len(ids):
        raise InputError("Source dataset contains duplicate conversation IDs")
    expected_channels = {"agent chat": 60, "voice call": 5} if dataset == "dev" else {"agent chat": 10}
    if any(record.get("channel") not in ("agent chat", "voice call") for record in normalized) or Counter(record.get("channel") for record in normalized) != Counter(expected_channels):
        raise InputError("Submission source channel counts differ from the organizer dataset")
    return normalized


def validate_submission(value, records, dataset):
    """Validate the output schema, exact source coverage, evidence and score flag."""
    source = _dataset_records(records, dataset)
    errors = list(Draft202012Validator(submission_schema()).iter_errors(value))
    if errors:
        path = ".".join(map(str, errors[0].absolute_path)) or "<root>"
        raise InputError(f"Submission schema violation at {path}: {errors[0].validator}")
    expected = {record["conversation_id"]: record for record in source}
    ids = [result["conversation_id"] for result in value["results"]]
    if len(ids) != len(set(ids)) or set(ids) != set(expected):
        raise InputError("Submission must contain every source ID exactly once")
    for result in value["results"]:
        if result["status"] == "error":
            continue
        limit = len(expected[result["conversation_id"]]["transcript"])
        for row in result["criteria"]:
            if any(turn > limit for turn in row["evidence_turns"]):
                raise InputError("Submission evidence turn is outside the source transcript")
        critical = next(row["value"] for row in result["criteria"] if row["criterion_id"] == "critical")
        if result["score"]["critical_flag"] is not critical:
            raise InputError("Submission critical_flag differs from the critical criterion")
        score = Decimal(str(result["score"]["final_score"]))
        if not score.is_finite() or score != score.quantize(Decimal("0.01")):
            raise InputError("Submission final_score must have at most two decimal places")
    return value


def _manifest(directory, records, checklist, model_name):
    manifest = read_json(directory / "manifest.json")
    if not isinstance(manifest, dict) or manifest.get("format") != "cqa-batch-1":
        raise InputError("Unsupported batch manifest")
    if manifest.get("checklist_hash") != fingerprint(checklist):
        raise InputError("Submission checklist differs from the batch manifest")
    if manifest.get("model") != model_name:
        raise InputError("Declared model name differs from the batch manifest")
    report = manifest.get("preflight")
    if not isinstance(report, dict) or report.get("records") != len(records):
        raise InputError("Submission dataset differs from the batch manifest")
    items = report.get("items")
    if not isinstance(items, list) or len(items) != len(records):
        raise InputError("Submission dataset coverage differs from the batch manifest")
    for record, item in zip(records, items):
        if not isinstance(item, dict) or item.get("conversation_id") != record["conversation_id"] or item.get("dialogue_hash") != fingerprint(record):
            raise InputError("Submission source IDs/order/content differ from the batch manifest")
        if any(not nonempty(item.get(key)) for key in ("dialogue_hash", "prompt_hash", "request_hash")):
            raise InputError("Batch manifest lacks saved request identity")
    expected_dirs = {f"{index:04d}" for index in range(1, len(records) + 1)}
    root = directory / "records"
    if root.exists() and any(not entry.is_dir() or entry.name not in expected_dirs for entry in root.iterdir()):
        raise InputError("Batch contains extra or unrecognized record artifacts")
    return items


def _metadata(saved, item, checklist, model_name, *, success):
    metadata = saved.get("metadata")
    if not isinstance(metadata, dict):
        raise InputError("Saved attempt lacks model metadata")
    for key in ("dialogue_hash", "prompt_hash", "request_hash"):
        if metadata.get(key) != item[key]:
            raise InputError("Saved attempt identity differs from the batch manifest")
    if metadata.get("checklist_hash") != fingerprint(checklist) or metadata.get("checklist_version") != checklist["version"]:
        raise InputError("Saved attempt checklist identity differs from the official snapshot")
    if metadata.get("requested_model") != model_name:
        raise InputError("Saved attempt requested a different model")
    actual = metadata.get("model")
    if (success and actual != model_name) or (actual is not None and actual != model_name):
        raise InputError("Declared model name differs from actual saved model metadata")
    if success and saved.get("critical_error") is not None and type(saved["critical_error"]) is not bool:
        raise InputError("Saved critical_error must be boolean or unresolved")
    for attempt in metadata.get("attempts", []):
        if attempt.get("requested_model") != model_name or attempt.get("model") not in (None, model_name):
            raise InputError("Saved attempt contains inconsistent model metadata")


def _attempts(directory, record, item, checklist, model_name):
    if not directory.exists():
        return
    for attempt in directory.iterdir():
        if not attempt.is_dir() or not re.fullmatch(r"attempt-[0-9]{4,}", attempt.name):
            raise InputError("Batch contains unrecognized attempt artifacts")
        started = read_json(attempt / "started.json")
        if started.get("conversation_id") != record["conversation_id"] or started.get("request_hash") != item["request_hash"]:
            raise InputError("Saved attempt ID/request differs from the batch manifest")
        if (attempt / "result.json").exists() and (attempt / "error.json").exists():
            raise InputError("Saved attempt has both a result and an error")
        if (attempt / "error.json").exists():
            error = read_json(attempt / "error.json")
            if error.get("conversation_id") != record["conversation_id"]:
                raise InputError("Saved error conversation ID differs from the source")
            _metadata(error, item, checklist, model_name, success=False)
        if (attempt / "result.json").exists():
            saved = read_json(attempt / "result.json")
            _metadata(saved, item, checklist, model_name, success=True)


def _evidence_turns(row, positions):
    ids = list(row["evidence_message_ids"])
    span = row["checked_range"]
    if span is not None:
        start, end = positions[span["start_message_id"]], positions[span["end_message_id"]]
        ids.extend(message_id for message_id, turn in positions.items() if start <= turn <= end)
    for violation in row["violations"]:
        ids.extend(violation["evidence_message_ids"])
    return list(dict.fromkeys(positions[message_id] for message_id in ids))


def _rationale(row, record):
    reason = row["reason"].strip()
    na_reason = row["na_reason"]
    if na_reason and na_reason.strip() not in reason:
        reason += " N/A: " + na_reason.strip()
    # Keep the model's explanation faithful for the required publication review.
    # Do not copy a source recording link if the model echoed it in its reason.
    audio_url = record.get("audio_url")
    if isinstance(audio_url, str) and audio_url:
        reason = reason.replace(audio_url, "[ссылка на запись]")
    return reason


def _error(record, state):
    return {"conversation_id": record["conversation_id"], "status": "error",
            "criteria": [], "score": None, "error": _ERRORS[state]}


@contextmanager
def _checked_artifacts():
    try:
        yield
    except (OSError, KeyError, TypeError, AttributeError, IndexError):
        # Never echo file contents, provider errors or arbitrary artifact values.
        raise InputError("Saved batch artifacts are incomplete or malformed") from None


def export_submission(records, checklist, run_dir, team_name, model_name,
                      model_mode, dataset, allow_unprocessed=False, *,
                      served_model_name=None):
    """Build a complete organizer-format dict without model calls or publication.

    Failed/interrupted assessments remain explicit error records. Never-started
    records require allow_unprocessed=True; otherwise a partial run is rejected.
    A valid but unresolved assessment becomes an error, never N/A or zero.
    served_model_name permits a separately verified canonical display name while
    binding saved identities to the model identifier returned by the server.
    """
    if not nonempty(team_name) or not nonempty(model_name):
        raise InputError("Submission requires nonempty team and actual model names")
    if not isinstance(model_mode, str) or model_mode not in {"local", "external_api", "hybrid"}:
        raise InputError("Submission model mode must be local, external_api or hybrid")
    served_name = model_name if served_model_name is None else served_model_name
    if not nonempty(served_name):
        raise InputError("Served model name must be nonempty")
    if type(allow_unprocessed) is not bool:
        raise InputError("allow_unprocessed must be boolean")
    validate_checklist(checklist)
    if fingerprint(checklist) != OFFICIAL_CHECKLIST_HASH:
        raise InputError("Organizer submission requires the unchanged official eight-criterion checklist")
    source = _dataset_records(records, dataset)
    directory = Path(run_dir)
    if not directory.is_dir() or not (directory / "manifest.json").is_file():
        raise InputError("Submission requires an existing durable batch run")
    results = []
    with run_lock(directory), _checked_artifacts():
        items = _manifest(directory, source, checklist, served_name)
        for index, (record, item) in enumerate(zip(source, items), 1):
            record_dir = directory / "records" / f"{index:04d}"
            _attempts(record_dir, record, item, checklist, served_name)
            # Reuse the existing response validator and shared scorer for every
            # saved success; ambiguous or tampered histories are hard failures.
            state = _record_state(record_dir, record, checklist, item)
            if state["state"] == "pending" and not allow_unprocessed:
                raise InputError("Submission has unprocessed records; explicit allow_unprocessed is required")
            if state["state"] != "validated":
                results.append(_error(record, state["state"]))
                continue
            saved = read_json(state["result"])
            score = _validated_saved(saved, record, checklist, item)
            if score["status"] != "completed" or score["total_score"] is None or type(score["gross_error"]) is not bool:
                results.append(_error(record, "unresolved"))
                continue
            rows = {row["criterion_id"]: row for row in saved["assessment"]["criteria"]}
            positions = {message["message_id"]: index for index, message in enumerate(record["transcript"], 1)}
            criteria = []
            for criterion_id, code in CRITERION_CODES.items():
                row = rows[criterion_id]
                answer = row["answer"]
                value = {"yes": True, "no": False, "na": None}[answer] if isinstance(answer, str) else int(answer)
                criteria.append({"criterion_id": code, "value": value,
                                 "rationale": _rationale(row, record),
                                 "evidence_turns": _evidence_turns(row, positions)})
            results.append({"conversation_id": record["conversation_id"], "status": "ok",
                            "criteria": criteria, "score": {
                                "final_score": float(score["total_score"]),
                                "critical_flag": score["gross_error"]}})
    submission = {"schema_version": "1.0", "team_name": team_name,
                  "model": {"mode": model_mode, "name": model_name}, "results": results}
    return validate_submission(submission, source, dataset)
