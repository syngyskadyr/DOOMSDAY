"""Manual review and quality metrics. Never imported by the production evaluator."""
from collections import defaultdict
from copy import deepcopy
from decimal import Decimal
from .config import nonempty, same_answer, validate_checklist
from .core import calculate_score
from .data import InputError, fingerprint, normalize_dialogue
from .validation import validate_response


def prepare_review(records, checklist, ids):
    validate_checklist(checklist)
    by_id = {r["conversation_id"]: normalize_dialogue(r) for r in records}
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
    if labels.get("checklist_version") != checklist["version"]:
        raise InputError("Labels checklist version mismatch")
    if labels.get("checklist_hash") not in {None, fingerprint(checklist)}:
        raise InputError("Labels checklist hash mismatch")
    records = {r["conversation_id"]: normalize_dialogue(r) for r in records}
    cases = labels.get("cases")
    if not isinstance(cases, list) or any(not isinstance(c, dict) or c.get("conversation_id") not in records for c in cases):
        raise InputError("Labels contain unknown/malformed conversations")
    requested = [c["conversation_id"] for c in cases]
    if len(set(requested)) != len(requested):
        raise InputError("Duplicate labeled conversation")
    by_prediction = {}
    for prediction in predictions:
        assessment = prediction.get("assessment", prediction)
        cid = assessment.get("conversation_id", prediction.get("conversation_id"))
        if cid in by_prediction:
            raise InputError("Duplicate prediction for conversation")
        by_prediction[cid] = prediction
    valid_predictions, failures = {}, []
    coverage = defaultdict(lambda: {"requested": 0, "processed": 0})
    for cid in requested:
        language = records[cid].get("language", "unknown")
        coverage[language]["requested"] += 1
        p = by_prediction.get(cid)
        if p is None:
            failures.append({"conversation_id": cid, "error": "missing_prediction"})
            continue
        try:
            if "error" in p:
                raise InputError("saved_processing_error")
            meta = p.get("metadata", {})
            if meta.get("checklist_hash") not in {None, fingerprint(checklist)}:
                raise InputError("prediction_checklist_hash_mismatch")
            if meta.get("dialogue_hash") not in {None, fingerprint(records[cid])}:
                raise InputError("prediction_dialogue_hash_mismatch")
            valid_predictions[cid] = validate_response(p.get("assessment", p), records[cid], checklist)
            coverage[language]["processed"] += 1
        except (InputError, KeyError, TypeError):
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
        except (InputError, KeyError, TypeError):
            invalid_labels.append({"conversation_id": cid, "error": "invalid_or_incomplete_reviewed_labels"})
    buckets = defaultdict(lambda: {"correct": 0, "total": 0})
    na = {"correct": 0, "total": 0, "na_instead_of_applicable": 0, "applicable_instead_of_na": 0,
          "missing_or_unknown": 0}
    flags = {c["id"]: {"tp": 0, "fp": 0, "fn": 0, "positive_examples": 0,
                       "missing_or_unknown": 0} for c in checklist["criteria"] if c["kind"] == "flag"}
    evidence = {"total": 0, "checked": 0, "unsupported": 0}
    errors = []
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
            if predicted:
                evidence["total"] += 1
        gold_total = calculate_score(checklist, validated)["total_score"]
        pred_total = calculate_score(checklist, prediction)["total_score"] if prediction else None
        if gold_total is not None and pred_total is not None:
            errors.append(abs(gold_total - pred_total))
        # Explicit human judgement of prediction evidence, not inferred from ID existence.
        case = next(c for c in cases if c["conversation_id"] == cid)
        for row in case["criteria"]:
            review = row.get("evidence_review", {})
            if cid in valid_predictions and review.get("checked") is True and type(review.get("supported")) is bool:
                evidence["checked"] += 1
                evidence["unsupported"] += int(not review["supported"])
    for b in buckets.values():
        b["accuracy"] = b["correct"] / b["total"] if b["total"] else None
    for f in flags.values():
        f["precision"] = f["tp"] / (f["tp"] + f["fp"]) if f["tp"] + f["fp"] else None
        f["recall"] = f["tp"] / (f["tp"] + f["fn"]) if f["tp"] + f["fn"] else None
    for c in coverage.values():
        c["fraction"] = c["processed"] / c["requested"] if c["requested"] else None
    return {"requested": len(requested), "processed": len(valid_predictions),
            "coverage": len(valid_predictions) / len(requested) if requested else None,
            "coverage_by_language": dict(coverage), "processing_errors": failures,
            "reviewed_records": len(gold), "invalid_labels": invalid_labels,
            "quality_metrics": ({"agreement": dict(buckets), "na": na, "flags": flags,
                                 "score_mae": sum(errors, Decimal(0)) / len(errors) if errors else None,
                                 "score_pairs": len(errors), "evidence": {**evidence,
                                 "checked_fraction": evidence["checked"] / evidence["total"] if evidence["total"] else None}}
                                if gold else None),
            "quality_note": "No complete validated human_reviewed records; quality metrics unavailable" if not gold else "Internal metrics; no claim about organizer ground truth",
            "checklist_hash": fingerprint(checklist)}
