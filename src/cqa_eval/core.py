"""Pure Decimal arithmetic. No file reads, model calls, or network access."""
from decimal import Decimal, localcontext, ROUND_HALF_UP
from .config import validate_checklist
from .data import fingerprint
from .validation import ValidatedAssessment, ValidationError


def calculate_score(checklist, results):
    validate_checklist(checklist)
    if not isinstance(results, ValidatedAssessment):
        raise ValidationError("calculate_score requires validate_response(...) receipt")
    if fingerprint(checklist) != results.checklist_hash:
        raise ValidationError("Checklist changed; validate answers against the intended snapshot first")
    with localcontext() as context:
        context.prec = 50
        return _calculate(checklist, results.to_dict())


def _calculate(checklist, assessment):
    config = {c["id"]: c for c in checklist["criteria"]}
    denominator = Decimal(0)
    weighted = Decimal(0)
    rows, violations = [], []
    unknown_weighted = False
    unknown_flag = False
    any_review = False
    for result in assessment["criteria"]:
        c = config[result["criterion_id"]]
        answer = result["answer"]
        review = result["status"] == "needs_review"
        any_review |= review
        flag = c["kind"] == "flag"
        weight = None if flag else Decimal(str(c["weight"]))
        fraction = None
        if flag:
            unknown_flag |= review
            violations.extend({"criterion_id": c["id"], **v} for v in result["violations"])
        elif answer != "na":
            denominator += weight
            if review:
                unknown_weighted = True
            else:
                fraction = (Decimal(str(answer)) / Decimal(str(c["scale_max"]))
                            if c["kind"] == "scale" else Decimal(int(answer == "yes")))
                weighted += weight * fraction
        rows.append({"criterion_id": c["id"], "section_id": c.get("section_id"),
                     "answer": answer, "status": result["status"], "base_weight": weight,
                     "effective_weight": None, "fraction": fraction, "earned_points": None})
    sections = {}
    for row in rows:
        if row["base_weight"] is None:
            continue
        section = sections.setdefault(row["section_id"], {"earned_points": Decimal(0), "applicable_max": Decimal(0)})
        if row["answer"] == "na":
            row["effective_weight"] = Decimal(0)
            row["earned_points"] = Decimal(0)
        elif denominator:
            row["effective_weight"] = 100 * row["base_weight"] / denominator
            section["applicable_max"] += row["effective_weight"]
            if row["fraction"] is not None:
                row["earned_points"] = row["effective_weight"] * row["fraction"]
            else:
                section["earned_points"] = None
        if section["earned_points"] is not None:
            if row["earned_points"] is None:
                section["earned_points"] = None
            else:
                section["earned_points"] += row["earned_points"]
    total = None if not denominator or unknown_weighted else (100 * weighted / denominator).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    gross = True if violations else (None if unknown_flag else False)
    return {"total_score": total, "applicable_weight": denominator,
            "weighted_points_before_normalization": None if unknown_weighted else weighted,
            "no_applicable_weight": denominator == 0,
            "status": "needs_review" if any_review else ("not_calculable" if not denominator else "completed"),
            "gross_error": gross, "violations": violations, "rows": rows,
            "sections": [{"section_id": key, **value} for key, value in sections.items()]}
