"""Manual review and quality metrics. Never imported by the production evaluator."""
from collections import Counter, defaultdict
from copy import deepcopy
from decimal import Decimal
import hashlib
from pathlib import Path
import re
from .config import nonempty, number, same_answer, validate_checklist
from .core import calculate_score
from .data import InputError, fingerprint, normalize_dialogue, read_json
from .validation import validate_response


def _records_by_id(records):
    normalized = [normalize_dialogue(record) for record in records]
    by_id = {record["conversation_id"]: record for record in normalized}
    if len(by_id) != len(normalized):
        raise InputError("Duplicate source conversation ID")
    return by_id


def prepare_review(records, checklist, ids):
    validate_checklist(checklist)
    by_id = _records_by_id(records)
    if not ids or len(ids) != len(set(ids)) or any(i not in by_id for i in ids):
        raise InputError("Review requires unique existing conversation IDs")
    return {"schema_version": "1", "checklist_version": checklist["version"],
            "checklist_hash": fingerprint(checklist), "checklist_snapshot": deepcopy(checklist),
            "purpose": "Independent human labels; fill without consulting predictions",
            "cases": [{"conversation_id": i, "language": by_id[i].get("language"),
                       "split": "debug", "annotation_status": "unreviewed", "reviewer": None,
                       "dialogue_hash": fingerprint(by_id[i]),
                       "criteria": [{"id": c["id"], "status": "evaluated", "answer": None,
                                     "reason": "", "evidence_message_ids": [], "checked_range": None,
                                     "na_reason": None, "uncertainty": None, "violations": [],
                                     "evidence_review": {"checked": False, "supported": None}}
                                    for c in checklist["criteria"]]}
                      for i in ids]}


def _assessment(case, checklist):
    criteria = []
    for row in case["criteria"]:
        item = {k: deepcopy(v) for k, v in row.items() if k not in {"id", "evidence_review"}}
        item["criterion_id"] = row.get("id", row.get("criterion_id"))
        criteria.append(item)
    return {"conversation_id": case["conversation_id"], "checklist_version": checklist["version"],
            "criteria": criteria}


def compare_predictions(records, checklist, labels, predictions):
    """Missing/invalid predictions remain in accuracy and coverage denominators."""
    validate_checklist(checklist)
    if not isinstance(labels, dict) or labels.get("checklist_version") != checklist["version"]:
        raise InputError("Labels checklist version mismatch")
    if labels.get("checklist_hash") not in {None, fingerprint(checklist)}:
        raise InputError("Labels checklist hash mismatch")
    records = _records_by_id(records)
    cases = labels.get("cases")
    if not isinstance(cases, list) or any(not isinstance(c, dict) or not nonempty(c.get("conversation_id")) or c["conversation_id"] not in records for c in cases):
        raise InputError("Labels contain unknown/malformed conversations")
    requested = [c["conversation_id"] for c in cases]
    if len(set(requested)) != len(requested):
        raise InputError("Duplicate labeled conversation")
    by_prediction = {}
    for prediction in predictions:
        if not isinstance(prediction, dict):
            raise InputError("Prediction must be a JSON object with a conversation ID")
        assessment = prediction.get("assessment", prediction)
        cid = assessment.get("conversation_id", prediction.get("conversation_id")) if isinstance(assessment, dict) else prediction.get("conversation_id")
        if not nonempty(cid) or cid not in requested:
            raise InputError("Prediction contains an unknown or unrequested conversation ID")
        if cid in by_prediction:
            raise InputError("Duplicate prediction for conversation")
        by_prediction[cid] = prediction
    valid_predictions, failures = {}, []
    coverage = defaultdict(lambda: {"requested": 0, "processed": 0, "completed": 0, "needs_review": 0})
    unresolved = []
    for cid in requested:
        language = records[cid].get("language", "unknown")
        coverage[language]["requested"] += 1
        p = by_prediction.get(cid)
        if p is None:
            failures.append({"conversation_id": cid, "error": "missing_prediction"})
            continue
        try:
            if "error" in p:
                state = p.get("processing_state")
                category = {"pending": "unprocessed_prediction", "interrupted": "interrupted_prediction"}.get(state, "saved_processing_error")
                failures.append({"conversation_id": cid, "error": category})
                continue
            meta = p.get("metadata", {})
            if meta.get("checklist_hash") not in {None, fingerprint(checklist)}:
                raise InputError("prediction_checklist_hash_mismatch")
            if meta.get("dialogue_hash") not in {None, fingerprint(records[cid])}:
                raise InputError("prediction_dialogue_hash_mismatch")
            valid_predictions[cid] = validate_response(p.get("assessment", p), records[cid], checklist)
            coverage[language]["processed"] += 1
            needs_review = any(row["status"] == "needs_review" for row in valid_predictions[cid].to_dict()["criteria"])
            coverage[language]["needs_review" if needs_review else "completed"] += 1
            if needs_review:
                unresolved.append(cid)
        except (InputError, KeyError, TypeError, AttributeError):
            failures.append({"conversation_id": cid, "error": "invalid_prediction"})
    gold, invalid_labels = {}, []
    for case in cases:
        if case.get("annotation_status") != "human_reviewed" or not nonempty(case.get("reviewer")):
            continue
        cid = case["conversation_id"]
        try:
            if case.get("dialogue_hash") not in {None, fingerprint(records[cid])}:
                raise InputError("label_dialogue_hash_mismatch")
            validated = validate_response(_assessment(case, checklist), records[cid], checklist)
            if any(r["status"] != "evaluated" for r in validated.to_dict()["criteria"]):
                raise InputError("Incomplete reviewed labels")
            gold[cid] = validated
        except (InputError, KeyError, TypeError, AttributeError):
            invalid_labels.append({"conversation_id": cid, "error": "invalid_or_incomplete_reviewed_labels"})
    buckets = defaultdict(lambda: {"correct": 0, "total": 0})
    na = {"correct": 0, "total": 0, "na_instead_of_applicable": 0, "applicable_instead_of_na": 0,
          "missing_or_unknown": 0}
    flags = {c["id"]: {"tp": 0, "fp": 0, "fn": 0, "positive_examples": 0,
                       "missing_or_unknown": 0, "explicit_false_negatives": 0,
                       "unresolved_positive_examples": 0,
                       "fn_note": "fn counts unconfirmed gold positives, including missing/unknown; explicit no and unresolved positives are reported separately"}
             for c in checklist["criteria"] if c["kind"] == "flag"}
    evidence = {"total": 0, "checked": 0, "unsupported": 0, "ignored_unbound_or_stale": 0}
    errors = []
    score_details = []
    for cid, validated in gold.items():
        prediction = valid_predictions.get(cid)
        rows = {r["criterion_id"]: r for r in prediction.to_dict()["criteria"]} if prediction else {}
        language = records[cid].get("language", "unknown")
        for row in validated.to_dict()["criteria"]:
            key = row["criterion_id"]
            predicted = rows.get(key)
            answer = predicted["answer"] if predicted and predicted["status"] == "evaluated" else None
            match = answer is not None and same_answer(answer, row["answer"])
            for bucket in ("all", f"criterion:{key}", f"language:{language}", f"criterion_language:{key}:{language}"):
                buckets[bucket]["total"] += 1
                buckets[bucket]["correct"] += int(match)
            na["total"] += 1
            if answer is None:
                na["missing_or_unknown"] += 1
            elif (answer == "na") == (row["answer"] == "na"):
                na["correct"] += 1
            elif answer == "na":
                na["na_instead_of_applicable"] += 1
            else:
                na["applicable_instead_of_na"] += 1
            if key in flags:
                f = flags[key]
                positive = row["answer"] == "yes"
                f["positive_examples"] += int(positive)
                f["tp"] += int(positive and answer == "yes")
                f["fp"] += int(not positive and answer == "yes")
                f["fn"] += int(positive and answer != "yes")
                f["missing_or_unknown"] += int(answer is None)
                f["explicit_false_negatives"] += int(positive and answer == "no")
                f["unresolved_positive_examples"] += int(positive and answer is None)
            if predicted:
                evidence["total"] += 1
        gold_total = calculate_score(checklist, validated)["total_score"]
        pred_total = calculate_score(checklist, prediction)["total_score"] if prediction else None
        if gold_total is not None and pred_total is not None:
            errors.append(abs(gold_total - pred_total))
        score_details.append({"conversation_id": cid, "reference_score": gold_total,
                              "prediction_score": pred_total,
                              "absolute_error": abs(gold_total - pred_total) if gold_total is not None and pred_total is not None else None})
        # Explicit human judgement of prediction evidence, not inferred from ID existence.
        case = next(c for c in cases if c["conversation_id"] == cid)
        for row in case["criteria"]:
            review = row.get("evidence_review", {})
            if cid in valid_predictions and isinstance(review, dict) and review.get("checked") is True and type(review.get("supported")) is bool:
                if review.get("prediction_hash") == fingerprint(by_prediction[cid]):
                    evidence["checked"] += 1
                    evidence["unsupported"] += int(not review["supported"])
                else:
                    evidence["ignored_unbound_or_stale"] += 1
    for b in buckets.values():
        b["accuracy"] = b["correct"] / b["total"] if b["total"] else None
    for f in flags.values():
        f["precision"] = f["tp"] / (f["tp"] + f["fp"]) if f["tp"] + f["fp"] else None
        f["recall"] = f["tp"] / (f["tp"] + f["fn"]) if f["tp"] + f["fn"] else None
    for c in coverage.values():
        c["fraction"] = c["processed"] / c["requested"] if c["requested"] else None
    return {"requested": len(requested), "processed": len(valid_predictions),
            "coverage": len(valid_predictions) / len(requested) if requested else None,
            "completed": len(valid_predictions) - len(unresolved),
            "completion_coverage": (len(valid_predictions) - len(unresolved)) / len(requested) if requested else None,
            "needs_review_records": unresolved,
            "needs_review_rate": len(unresolved) / len(requested) if requested else None,
            "processing_error_count": len(failures),
            "processing_error_rate": len(failures) / len(requested) if requested else None,
            "coverage_by_language": dict(coverage), "processing_errors": failures,
            "reviewed_records": len(gold), "invalid_labels": invalid_labels,
            "quality_metrics": ({"agreement": dict(buckets), "na": na, "flags": flags,
                                 "score_mae": sum(errors, Decimal(0)) / len(errors) if errors else None,
                                 "score_pairs": len(errors),
                                 "score_pair_ids": [row["conversation_id"] for row in score_details if row["absolute_error"] is not None],
                                 "score_details": score_details,
                                 "evidence": {**evidence,
                                 "checked_fraction": evidence["checked"] / evidence["total"] if evidence["total"] else None}}
                                if gold else None),
            "quality_note": "No complete validated human_reviewed records; quality metrics unavailable" if not gold else "Internal metrics; no claim about organizer ground truth",
            "prediction_hashes": {cid: fingerprint(by_prediction[cid]) for cid in valid_predictions},
            "checklist_hash": fingerprint(checklist)}


def _hash(value):
    return isinstance(value, str) and re.fullmatch(r"[0-9a-f]{64}", value) is not None


def _serving_conditions(provenance, manifest):
    """Require explicit captured settings; never infer historical server defaults."""
    if not isinstance(provenance, dict) or provenance.get("format") != "cqa-paired-provenance-1":
        raise InputError("Paired comparison requires explicit cqa-paired-provenance-1 metadata")
    if provenance.get("manifest_hash") != fingerprint(manifest):
        raise InputError("Paired provenance is not bound to this batch manifest")
    if type(manifest.get("max_attempts")) is not int or manifest["max_attempts"] != 1:
        raise InputError("Strict paired comparison requires max_attempts=1 without retry selection")
    if any(not _hash(provenance.get(key)) for key in ("instruction_hash", "response_schema_hash")):
        raise InputError("Paired provenance requires instruction and response-schema hashes")
    if not nonempty(provenance.get("served_model_name")) or provenance["served_model_name"] != manifest.get("model"):
        raise InputError("Paired provenance served model differs from the batch manifest")
    conditions = provenance.get("serving_conditions")
    if not isinstance(conditions, dict):
        raise InputError("Paired comparison requires explicit serving_conditions")
    for key in ("model_name", "model_revision", "quantization", "vllm_version"):
        if not nonempty(conditions.get(key)):
            raise InputError(f"Paired serving_conditions requires {key}")
    if re.fullmatch(r"(?:[0-9a-f]{40}|[0-9a-f]{64})", conditions["model_revision"]) is None:
        raise InputError("Paired model_revision must pin an immutable commit hash")
    for key in ("tokenizer_sha256", "template_sha256"):
        if not _hash(conditions.get(key)):
            raise InputError(f"Paired serving_conditions requires {key}")
    if type(conditions.get("context_window")) is not int or conditions["context_window"] <= 0:
        raise InputError("Paired serving_conditions requires a positive context_window")
    decoding = conditions.get("decoding")
    if not isinstance(decoding, dict):
        raise InputError("Paired serving_conditions requires explicit decoding")
    for key in ("temperature", "top_p", "min_p"):
        if not number(decoding.get(key)):
            raise InputError(f"Paired decoding requires finite {key}")
    if decoding["temperature"] < 0 or not 0 < decoding["top_p"] <= 1 or not 0 <= decoding["min_p"] <= 1:
        raise InputError("Paired decoding contains invalid sampling settings")
    if type(decoding.get("top_k")) is not int or decoding["top_k"] < -1:
        raise InputError("Paired decoding requires an explicit integer top_k")
    if type(decoding.get("max_output_tokens")) is not int or decoding["max_output_tokens"] <= 0:
        raise InputError("Paired decoding requires positive max_output_tokens")
    if "seed" not in decoding or (decoding["seed"] is not None and type(decoding["seed"]) is not int):
        raise InputError("Paired decoding requires an explicit seed (integer or null)")
    if type(decoding.get("enable_thinking")) is not bool:
        raise InputError("Paired decoding requires explicit enable_thinking")
    # Check all additional declared conditions are JSON values, including finite numbers.
    fingerprint(conditions)
    report = manifest["preflight"]
    if conditions["context_window"] != report.get("context_window") or decoding["max_output_tokens"] != report.get("max_output_tokens"):
        raise InputError("Paired serving limits differ from the batch manifest")
    template_kwargs = report.get("chat_template_kwargs", {})
    if "enable_thinking" in template_kwargs and template_kwargs["enable_thinking"] != decoding["enable_thinking"]:
        raise InputError("Paired thinking mode differs from the tokenizer preflight")
    return conditions


def _run_snapshot(directory):
    """Hash immutable inputs without copying response/transcript content to reports."""
    output = {}
    for path in sorted(directory.rglob("*")):
        if path.is_symlink():
            raise InputError("Paired run artifacts must not contain symbolic links")
        if path.is_file():
            with path.open("rb") as stream:
                output[str(path.relative_to(directory))] = hashlib.file_digest(stream, "sha256").hexdigest()
    return output


def _paired_metadata(saved, record, checklist, item, manifest, provenance, *, success):
    metadata = saved.get("metadata")
    if not isinstance(metadata, dict):
        raise InputError("Paired saved attempt lacks provenance metadata")
    expected = {"dialogue_hash": item["dialogue_hash"], "prompt_hash": item["prompt_hash"],
                "request_hash": item["request_hash"], "checklist_hash": fingerprint(checklist),
                "checklist_version": checklist["version"], "instruction_hash": provenance["instruction_hash"],
                "response_schema_hash": provenance["response_schema_hash"],
                "api_style": manifest["api_style"], "requested_model": manifest["model"]}
    if any(metadata.get(key) != value for key, value in expected.items()):
        raise InputError("Paired attempt metadata differs from its version's pinned provenance")
    actual = metadata.get("model")
    if (success and actual != manifest["model"]) or actual not in (None, manifest["model"]):
        raise InputError("Paired attempt actual model differs from its pinned provenance")
    generation = metadata.get("generation_settings")
    if not isinstance(generation, dict) or generation.get("structured_output") != manifest["structured_output"] or generation.get("max_output_tokens") != manifest["preflight"]["max_output_tokens"]:
        raise InputError("Paired attempt generation settings differ from its pinned provenance")
    history = metadata.get("attempts")
    calls = metadata.get("call_count")
    if type(calls) is not int or not 0 <= calls <= 1 or not isinstance(history, list) or len(history) != calls:
        raise InputError("Strict paired comparison requires at most one recorded model call")
    if success and calls != 1:
        raise InputError("Paired successful result must record its single model call")
    for attempt in history:
        if attempt.get("requested_model") != manifest["model"] or attempt.get("model") not in (None, manifest["model"]):
            raise InputError("Paired attempt history contains mixed models")
    if saved.get("conversation_id", record["conversation_id"]) != record["conversation_id"]:
        raise InputError("Paired saved error conversation ID mismatch")


def _load_paired_run(records, checklist, requested, directory, provenance):
    # Import only offline batch readers. Never collect_results (which excludes failures).
    from .batch import _record_state
    directory = Path(directory)
    if not directory.is_dir():
        raise InputError("Paired comparison requires existing durable batch directories")
    snapshot = _run_snapshot(directory)
    manifest = read_json(directory / "manifest.json")
    if manifest.get("format") != "cqa-batch-1" or manifest.get("checklist_hash") != fingerprint(checklist):
        raise InputError("Paired batch manifest format/checklist mismatch")
    report = manifest.get("preflight", {})
    items = report.get("items")
    if not isinstance(items, list) or report.get("records") != len(records) or len(items) != len(records):
        raise InputError("Paired batch dataset coverage differs from the source")
    for record, item in zip(records, items):
        if item.get("conversation_id") != record["conversation_id"] or item.get("dialogue_hash") != fingerprint(record):
            raise InputError("Paired batch IDs/order/data differ from the source")
        if any(not _hash(item.get(key)) for key in ("dialogue_hash", "prompt_hash", "request_hash")):
            raise InputError("Paired batch manifest lacks request identity hashes")
    conditions = _serving_conditions(provenance, manifest)
    expected_dirs = {f"{index:04d}" for index in range(1, len(records) + 1)}
    root = directory / "records"
    if root.exists() and any(not path.is_dir() or path.name not in expected_dirs for path in root.iterdir()):
        raise InputError("Paired batch contains extra/unrecognized record artifacts")
    predictions, states = [], {}
    for index, (record, item) in enumerate(zip(records, items), 1):
        cid = record["conversation_id"]
        if cid not in requested:
            continue
        folder = root / f"{index:04d}"
        attempts = sorted(folder.iterdir()) if folder.exists() else []
        if len(attempts) > 1:
            raise InputError("Strict paired comparison refuses multiple attempts for one conversation")
        for attempt in attempts:
            if not attempt.is_dir() or re.fullmatch(r"attempt-[0-9]{4,}", attempt.name) is None:
                raise InputError("Paired batch contains unrecognized attempt artifacts")
            started = read_json(attempt / "started.json")
            if started.get("conversation_id") != cid or started.get("request_hash") != item["request_hash"]:
                raise InputError("Paired attempt ID/request differs from the manifest")
            if (attempt / "result.json").exists() and (attempt / "error.json").exists():
                raise InputError("Paired attempt contains both a result and an error")
            for name in ("result.json", "error.json"):
                path = attempt / name
                if path.exists():
                    _paired_metadata(read_json(path), record, checklist, item, manifest, provenance, success=name == "result.json")
        state = _record_state(folder, record, checklist, item)
        if state["state"] == "validated":
            prediction = read_json(state["result"])
            states[cid] = "needs_review" if state["assessment_status"] == "needs_review" else "completed"
        else:
            states[cid] = state["state"]
            prediction = {"conversation_id": cid, "error": "saved_processing_error",
                          "processing_state": state["state"]}
        predictions.append(prediction)
    if snapshot != _run_snapshot(directory):
        raise InputError("Paired run artifacts changed while being read")
    batch_conditions = {key: manifest[key] for key in ("api_style", "structured_output", "max_attempts", "timeout")}
    return predictions, states, {"manifest_hash": fingerprint(manifest),
        "provenance_hash": fingerprint(provenance), "instruction_hash": provenance["instruction_hash"],
        "response_schema_hash": provenance["response_schema_hash"],
        "serving_conditions_hash": fingerprint(conditions), "batch_conditions": batch_conditions,
        "artifact_sha256": snapshot, "state_counts": dict(Counter(states.values()))}


def compare_paired_runs(records, checklist, labels, baseline_dir, candidate_dir,
                        baseline_provenance, candidate_provenance):
    """Compare immutable runs on fixed IDs; failures are never survivor-filtered.

    Explicit serving conditions are checked against saved metadata, not remotely
    verified. Prompt hashes may differ between versions; instruction identity is
    constant within each version. Sampling is not claimed to be bitwise replayable.
    """
    validate_checklist(checklist)
    source = list(_records_by_id(records).values())
    if not isinstance(labels, dict) or labels.get("checklist_hash") != fingerprint(checklist) or labels.get("checklist_snapshot") != checklist:
        raise InputError("Paired comparison requires labels pinned to the exact checklist snapshot")
    # Reuse the existing gate/schema checks; AI or blank annotations remain non-GT.
    validation = compare_predictions(source, checklist, labels, [])
    requested = [case["conversation_id"] for case in labels["cases"]]
    by_record = {record["conversation_id"]: record for record in source}
    if len(requested) != len(source) or set(requested) != set(by_record):
        raise InputError("Paired labels must contain exactly the IDs of both complete run datasets")
    if any(case.get("dialogue_hash") != fingerprint(by_record[case["conversation_id"]]) for case in labels["cases"]):
        raise InputError("Paired labels must pin each source dialogue hash")
    try:
        baseline, baseline_states, baseline_meta = _load_paired_run(source, checklist, requested, baseline_dir, baseline_provenance)
        candidate, candidate_states, candidate_meta = _load_paired_run(source, checklist, requested, candidate_dir, candidate_provenance)
    except (OSError, KeyError, TypeError, AttributeError, IndexError):
        raise InputError("Paired run artifacts/provenance are incomplete or malformed") from None
    if baseline_meta["serving_conditions_hash"] != candidate_meta["serving_conditions_hash"] or baseline_meta["batch_conditions"] != candidate_meta["batch_conditions"]:
        raise InputError("Paired comparison requires identical explicit serving and batch conditions")
    if baseline_meta["response_schema_hash"] != candidate_meta["response_schema_hash"]:
        raise InputError("Paired comparison requires the same response schema")
    # Evidence annotation is a subsequent review of one specific prediction. The
    # blind GT form cannot supply a shared evidence review for two versions.
    independent = deepcopy(labels)
    for case in independent["cases"]:
        for row in case.get("criteria", []) if isinstance(case.get("criteria"), list) else []:
            if isinstance(row, dict):
                row["evidence_review"] = {"checked": False, "supported": None}
    reports = {"baseline": compare_predictions(source, checklist, independent, baseline),
               "candidate": compare_predictions(source, checklist, independent, candidate)}
    reports["baseline"]["state_counts"] = baseline_meta["state_counts"]
    reports["candidate"]["state_counts"] = candidate_meta["state_counts"]
    paired_metrics = None
    if validation["reviewed_records"]:
        a = {row["conversation_id"]: row for row in reports["baseline"]["quality_metrics"]["score_details"]}
        b = {row["conversation_id"]: row for row in reports["candidate"]["quality_metrics"]["score_details"]}
        common = [cid for cid in requested if cid in a and a[cid]["absolute_error"] is not None and b[cid]["absolute_error"] is not None]
        mae_a = sum((a[cid]["absolute_error"] for cid in common), Decimal(0)) / len(common) if common else None
        mae_b = sum((b[cid]["absolute_error"] for cid in common), Decimal(0)) / len(common) if common else None
        paired_metrics = {"score_pairs": len(common), "score_pair_ids": common,
            "baseline_mae": mae_a, "candidate_mae": mae_b,
            "mae_delta_candidate_minus_baseline": mae_b - mae_a if common else None,
            "score_details": [{"conversation_id": cid, "baseline_absolute_error": a[cid]["absolute_error"],
                               "candidate_absolute_error": b[cid]["absolute_error"]} for cid in common]}
    transitions = Counter(baseline_states[cid] + "->" + candidate_states[cid] for cid in requested)
    return {"format": "cqa-paired-comparison-1", "requested": len(requested),
        "requested_ids": requested, "reviewed_records": validation["reviewed_records"],
        "fixed_reviewed_criterion_denominator": validation["quality_metrics"]["agreement"]["all"]["total"] if validation["quality_metrics"] else None,
        "dataset_hash": fingerprint(source), "labels_hash": fingerprint(labels),
        "checklist_hash": fingerprint(checklist), "baseline": reports["baseline"], "candidate": reports["candidate"],
        "paired_quality_metrics": paired_metrics, "state_transitions": dict(transitions),
        "states": [{"conversation_id": cid, "baseline": baseline_states[cid], "candidate": candidate_states[cid]} for cid in requested],
        "provenance": {"baseline": baseline_meta, "candidate": candidate_meta},
        "evidence_review_policy": "Evidence reviews from the shared label form are not reused for either version",
        "quality_note": validation["quality_note"],
        "reproducibility_note": "Saved metadata and explicit serving declarations checked offline; no remote verification or bitwise sampling reproducibility claim"}
