"""Offline export of durable batch results to the organizer's JSON 1.0 format.

This adapter deliberately accepts only the unchanged official checklist. It does
not change the configurable application/ML contract, run a model, or publish files.
Rationales are retained for review; source evidence objects and recordings are
never copied into the submission.
"""
from collections import Counter
from contextlib import contextmanager, ExitStack
from decimal import Decimal
from importlib.resources import files
from pathlib import Path
import hashlib
import re

from jsonschema import Draft202012Validator

from .batch import _record_state, _validated_saved, run_lock
from .config import nonempty, validate_checklist
from .data import InputError, fingerprint, normalize_dialogue, parse_json, read_json
from .evaluator import _text


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


def _official_result(saved, record, score):
    """One mapping shared by durable-run and collected-package exports."""
    if score["status"] != "completed" or score["total_score"] is None or type(score["gross_error"]) is not bool:
        return _error(record, "unresolved")
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
    return {"conversation_id": record["conversation_id"], "status": "ok",
            "criteria": criteria, "score": {
                "final_score": float(score["total_score"]), "critical_flag": score["gross_error"]}}


@contextmanager
def _checked_artifacts():
    try:
        yield
    except (OSError, KeyError, TypeError, AttributeError, IndexError):
        # Never echo file contents, provider errors or arbitrary artifact values.
        raise InputError("Saved batch artifacts are incomplete or malformed") from None


def _snapshot(directory):
    """Bind provenance to bytes, without copying transcript/provider content."""
    result = {}
    for path in sorted(directory.rglob("*")):
        if path.is_symlink():
            raise InputError("Submission runs must not contain symbolic links")
        if path == directory / ".lock":
            # Coordination byte, not an assessment artifact. Windows forbids
            # reading the region held by our own mandatory run_lock.
            continue
        if path.is_file():
            with path.open("rb") as stream:
                result[str(path.relative_to(directory))] = hashlib.file_digest(stream, "sha256").hexdigest()
    return result


def _selected_provenance(directory, state):
    attempts = sorted(directory.glob("attempt-*")) if directory.exists() else []
    selected = Path(state["result"]) if state["state"] == "validated" else (
        attempts[-1] / "error.json" if attempts and (attempts[-1] / "error.json").is_file() else None)
    saved = read_json(selected) if selected else {}
    metadata = saved.get("metadata", {})
    return {"attempt_directories": len(attempts),
            "selected_file": str(selected.relative_to(directory)) if selected else None,
            "selected_sha256": hashlib.sha256(selected.read_bytes()).hexdigest() if selected else None,
            "instruction_hash": metadata.get("instruction_hash"),
            "prompt_hash": metadata.get("prompt_hash"), "request_hash": metadata.get("request_hash"),
            "api_calls_in_selected_attempt": metadata.get("call_count"),
            "state": state["state"]}


def _recovery_attempt(directory, record, item, checklist, model_name, manifest):
    """A recovery is one new observation, never best-of-N or JSON repair."""
    _attempts(directory, record, item, checklist, model_name)
    attempts = sorted(directory.glob("attempt-*")) if directory.exists() else []
    if len(attempts) > 1:
        raise InputError("Recovery requires at most one fresh attempt per failed record")
    if not attempts:
        return None
    attempt = attempts[0]
    if attempt.name != "attempt-0001":
        raise InputError("Recovery requires the first fresh attempt, not a selected later attempt")
    starts = sorted(attempt.glob("attempt_started-*.json"))
    finishes = sorted(attempt.glob("attempt_finished-*.json"))
    responses = sorted(attempt.glob("response_received-*.json"))
    result = attempt / "result.json"
    error = attempt / "error.json"
    if not starts and not finishes and not responses and not result.exists() and not error.exists():
        # A crash before the API-start journal is still an explicit interruption.
        return None
    if len(starts) != 1 or type(read_json(starts[0]).get("attempt")) is not int or read_json(starts[0])["attempt"] != 1 or len(finishes) > 1 or len(responses) > 1:
        raise InputError("Recovery must retain exactly one API-start journal")
    if not result.is_file() and not error.is_file():
        # An interrupted request is an explicit unknown outcome, not a score.
        return None
    saved = read_json(result if result.is_file() else error)
    metadata = saved["metadata"]
    history = metadata.get("attempts")
    if type(metadata.get("call_count")) is not int or metadata["call_count"] != 1 or type(metadata.get("retry_count")) is not int or metadata["retry_count"] != 0 or not isinstance(history, list) or len(history) != 1:
        raise InputError("Recovery requires one recorded API call without retries")
    if history[0].get("attempt") != 1 or len(finishes) != 1 or fingerprint(read_json(finishes[0])) != fingerprint(history[0]):
        raise InputError("Recovery completion journal differs from saved API history")
    instruction = metadata.get("instruction_hash")
    if not isinstance(instruction, str) or re.fullmatch(r"[0-9a-f]{64}", instruction) is None:
        raise InputError("Recovery lacks the actual instruction hash")
    generation = metadata.get("generation_settings")
    if metadata.get("api_style") != manifest.get("api_style") or not isinstance(generation, dict) or generation.get("structured_output") != manifest.get("structured_output") or generation.get("max_output_tokens") != manifest["preflight"].get("max_output_tokens"):
        raise InputError("Recovery settings differ from the saved manifest")
    if result.is_file():
        if len(responses) != 1:
            raise InputError("Recovery success requires the original provider response journal")
        response = read_json(responses[0])
        if response.get("attempt") != 1:
            raise InputError("Recovery response has a different attempt number")
        envelope = response["response"]
        if envelope.get("model") != model_name or fingerprint(parse_json(_text(envelope, metadata["api_style"]))) != fingerprint(saved["assessment"]):
            raise InputError("Recovery assessment differs from the original provider response")
    return instruction


def export_submission_with_provenance(records, checklist, run_dir, team_name, model_name,
                      model_mode, dataset, allow_unprocessed=False, *,
                      served_model_name=None, recovery_run_dir=None):
    """Return (official JSON, private provenance), without model calls.

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
    recovery = Path(recovery_run_dir) if recovery_run_dir is not None else None
    if recovery is not None:
        if not recovery.is_dir() or not (recovery / "manifest.json").is_file():
            raise InputError("Recovery requires an existing durable batch run")
        if recovery.resolve().is_relative_to(directory.resolve()) or directory.resolve().is_relative_to(recovery.resolve()):
            raise InputError("Recovery and original runs must be separate directories")
    results = []
    provenance_rows = []
    runs = {"baseline": directory, **({"recovery": recovery} if recovery is not None else {})}
    with ExitStack() as locks, _checked_artifacts():
        for path in sorted(runs.values(), key=lambda p: str(p.resolve())):
            locks.enter_context(run_lock(path))
        snapshots = {name: _snapshot(path) for name, path in runs.items()}
        items = _manifest(directory, source, checklist, served_name)
        original_states = []
        for index, (record, item) in enumerate(zip(source, items), 1):
            record_dir = directory / "records" / f"{index:04d}"
            _attempts(record_dir, record, item, checklist, served_name)
            # Reuse the existing response validator and shared scorer for every
            # saved success; ambiguous or tampered histories are hard failures.
            state = _record_state(record_dir, record, checklist, item)
            original_states.append(state)
        replacements = {}
        if recovery is not None:
            failed_records = [record for record, state in zip(source, original_states) if state["state"] == "error"]
            if not failed_records:
                raise InputError("Recovery requires original processing-error records; successes cannot be replaced")
            recovery_items = _manifest(recovery, failed_records, checklist, served_name)
            original_manifest, recovery_manifest = read_json(directory / "manifest.json"), read_json(recovery / "manifest.json")
            if type(recovery_manifest.get("max_attempts")) is not int or recovery_manifest["max_attempts"] != 1:
                raise InputError("Recovery manifest must limit API attempts to one")
            if any(original_manifest.get(key) != recovery_manifest.get(key) for key in ("api_style", "structured_output")):
                raise InputError("Recovery model API format differs from the original run")
            instruction_hashes = set()
            for index, (record, item) in enumerate(zip(failed_records, recovery_items), 1):
                record_dir = recovery / "records" / f"{index:04d}"
                instruction = _recovery_attempt(record_dir, record, item, checklist, served_name, recovery_manifest)
                if instruction is not None:
                    instruction_hashes.add(instruction)
                state = _record_state(record_dir, record, checklist, item)
                replacements[record["conversation_id"]] = (record_dir, item, state)
            if len(instruction_hashes) > 1:
                raise InputError("Recovery run mixes instruction versions")
        for index, (record, item, state) in enumerate(zip(source, items, original_states), 1):
            record_dir = directory / "records" / f"{index:04d}"
            origin = "baseline"
            if record["conversation_id"] in replacements:
                record_dir, item, state = replacements[record["conversation_id"]]
                origin = "recovery"
            provenance_rows.append({"conversation_id": record["conversation_id"],
                "dialogue_hash": fingerprint(record), "source": origin,
                **_selected_provenance(record_dir, state)})
            if state["state"] == "pending" and not allow_unprocessed:
                raise InputError("Submission has unprocessed records; explicit allow_unprocessed is required")
            if state["state"] != "validated":
                results.append(_error(record, state["state"]))
                continue
            saved = read_json(state["result"])
            score = _validated_saved(saved, record, checklist, item)
            results.append(_official_result(saved, record, score))
        if any(_snapshot(path) != snapshots[name] for name, path in runs.items()):
            raise InputError("Submission run artifacts changed during export")
        manifests = {name: {"path": str(path.resolve()),
            "manifest_hash": fingerprint(read_json(path / "manifest.json")),
            "artifact_sha256": snapshots[name]} for name, path in runs.items()}
    submission = {"schema_version": "1.0", "team_name": team_name,
                  "model": {"mode": model_mode, "name": model_name}, "results": results}
    validate_submission(submission, source, dataset)
    provenance = {"format": "cqa-submission-composition-1", "dataset": dataset,
        "dataset_hash": fingerprint(source), "checklist_hash": fingerprint(checklist),
        "model": {"name": model_name, "served_name": served_name, "mode": model_mode},
        "submission_hash": fingerprint(submission), "runs": manifests,
        "results": [{**row, "exported_status": result["status"]} for row, result in zip(provenance_rows, results)],
        "policy": "Preserve original successes; exactly one new recovery observation for every original processing error. Different instruction hashes remain separate; no unified run manifest is fabricated."}
    return submission, provenance


def export_submission(records, checklist, run_dir, team_name, model_name,
                      model_mode, dataset, allow_unprocessed=False, *,
                      served_model_name=None, recovery_run_dir=None):
    """Build official JSON; use the provenance companion when composing runs."""
    return export_submission_with_provenance(records, checklist, run_dir, team_name,
        model_name, model_mode, dataset, allow_unprocessed=allow_unprocessed,
        served_model_name=served_model_name, recovery_run_dir=recovery_run_dir)[0]


def export_collected_submission(records, checklist, bundle, team_name, model_name,
                                model_mode, dataset, *, served_model_name=None):
    """Consume the unchanged batch-collect cqa-batch-results-1 contract offline.

    Embedded paths are never followed. A bundle has no raw journals/manifest:
    its manifest_hash is a reference, not independently verified provenance.
    Failed/incomplete runs must use export_submission(run_dir), since collect
    intentionally refuses them. Missing IDs are never fabricated as errors.
    """
    if not nonempty(team_name) or not nonempty(model_name):
        raise InputError("Submission requires nonempty team and actual model names")
    if not isinstance(model_mode, str) or model_mode not in {"local", "external_api", "hybrid"}:
        raise InputError("Submission model mode must be local, external_api or hybrid")
    served_name = model_name if served_model_name is None else served_model_name
    if not nonempty(served_name):
        raise InputError("Served model name must be nonempty")
    validate_checklist(checklist)
    if fingerprint(checklist) != OFFICIAL_CHECKLIST_HASH:
        raise InputError("Organizer submission requires the unchanged official eight-criterion checklist")
    source = _dataset_records(records, dataset)
    with _checked_artifacts():
        if not isinstance(bundle, dict) or bundle.get("format") != "cqa-batch-results-1":
            raise InputError("Expected a batch-collect cqa-batch-results-1 package")
        if bundle.get("organizer_format_verified") is not False or not isinstance(bundle.get("manifest_hash"), str) or re.fullmatch(r"[0-9a-f]{64}", bundle["manifest_hash"]) is None:
            raise InputError("Invalid collected-package header")
        saved_results = bundle.get("results")
        if not isinstance(saved_results, list):
            raise InputError("Collected package requires a results array")
        ids = [saved["assessment"]["conversation_id"] for saved in saved_results]
        expected_ids = {record["conversation_id"] for record in source}
        if len(ids) != len(set(ids)) or set(ids) != expected_ids:
            raise InputError("Collected package must contain every source ID exactly once")
        summary = bundle["summary"]
        if (summary.get("records") != len(source) or summary.get("complete") is not True
                or summary.get("ready_for_handoff") is not True
                or summary.get("needs_review") != 0 or summary.get("not_calculable") != 0
                or summary.get("counts") != {"validated": len(source), "error": 0, "interrupted": 0, "pending": 0}):
            raise InputError("Collected summary is incomplete; use --run-dir to export processing errors")
        summary_rows = summary["items"]
        summary_ids = [row["conversation_id"] for row in summary_rows]
        if len(summary_ids) != len(set(summary_ids)) or set(summary_ids) != expected_ids:
            raise InputError("Collected summary must contain every source ID exactly once")
        if any(row.get("state") != "validated" for row in summary_rows):
            raise InputError("Collected summary contains an unvalidated record")
        by_id = dict(zip(ids, saved_results))
        summary_by_id = dict(zip(summary_ids, summary_rows))
        results = []
        for record in source:
            saved = by_id[record["conversation_id"]]
            item = {"dialogue_hash": fingerprint(record),
                    "prompt_hash": saved["metadata"]["prompt_hash"],
                    "request_hash": saved["metadata"]["request_hash"]}
            if any(not isinstance(value, str) or re.fullmatch(r"[0-9a-f]{64}", value) is None for value in item.values()):
                raise InputError("Collected result lacks valid request identity hashes")
            _metadata(saved, item, checklist, served_name, success=True)
            score = _validated_saved(saved, record, checklist, item)
            result = _official_result(saved, record, score)
            reported = summary_by_id[record["conversation_id"]]
            if (result["status"] != "ok" or reported.get("assessment_status") != score["status"]
                    or fingerprint(reported.get("total_score")) != fingerprint(score["total_score"])):
                raise InputError("Collected summary differs from validated results; use --run-dir for unresolved records")
            results.append(result)
    output = {"schema_version": "1.0", "team_name": team_name,
              "model": {"mode": model_mode, "name": model_name}, "results": results}
    return validate_submission(output, source, dataset)
