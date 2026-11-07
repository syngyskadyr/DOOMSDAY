"""Provider-independent checklist validation for the offline editor.

This module only validates configuration. Scoring and assessment validation
remain exclusively in cqa_eval, which is optional until evaluation is requested.
"""
from decimal import Decimal


def _nonempty(value):
    return isinstance(value, str) and bool(value.strip())


def _number(value):
    return type(value) in (int, float, Decimal) and Decimal(str(value)).is_finite()


def _require(condition):
    if not condition:
        raise ValueError("Invalid checklist configuration")


def validate_checklist(checklist):
    """Validate every configuration field without importing the ML package."""
    _require(isinstance(checklist, dict))
    _require(all(_nonempty(checklist.get(key)) for key in
                 ("schema_version", "checklist_id", "version", "title")))
    sections = checklist.get("sections")
    _require(isinstance(sections, list) and bool(sections))
    _require(all(isinstance(section, dict) and _nonempty(section.get("id"))
                 and _nonempty(section.get("title")) for section in sections))
    section_ids = {section["id"] for section in sections}
    _require(len(section_ids) == len(sections))
    criteria = checklist.get("criteria")
    _require(isinstance(criteria, list) and bool(criteria))
    criterion_ids = set()
    required = {"id", "section_id", "title", "kind", "weight", "allowed_answers",
                "instruction", "na_allowed", "na_rule", "scale_max", "levels", "flag_types"}
    for criterion in criteria:
        _require(isinstance(criterion, dict) and required.issubset(criterion))
        _require(all(_nonempty(criterion[key]) for key in ("id", "section_id", "title", "instruction")))
        _require(criterion["id"] not in criterion_ids)
        criterion_ids.add(criterion["id"])
        _require(criterion["section_id"] in section_ids)
        kind = criterion["kind"]
        _require(isinstance(kind, str) and kind in {"binary", "scale", "flag"})
        _require(type(criterion["na_allowed"]) is bool)
        allowed = criterion["allowed_answers"]
        _require(isinstance(allowed, list) and bool(allowed))
        _require(all((type(answer) is str and answer in {"yes", "no", "na"})
                     or _number(answer) for answer in allowed))
        # Numeric equality is deliberate: 1 and 1.0 are the same scale step.
        unique_answers = [("number", Decimal(str(answer))) if _number(answer)
                          else ("string", answer) for answer in allowed]
        _require(len(set(unique_answers)) == len(allowed))
        _require(("na" in allowed) == criterion["na_allowed"])
        _require(_nonempty(criterion["na_rule"]) if criterion["na_allowed"]
                 else criterion["na_rule"] is None)
        values = [answer for answer in allowed if answer != "na"]
        if kind == "scale":
            maximum = criterion["scale_max"]
            _require(_number(maximum) and maximum > 0)
            _require(bool(values) and all(_number(answer) and 0 <= answer <= maximum for answer in values))
            numeric_values = sorted(Decimal(str(answer)) for answer in values)
            _require(Decimal(str(maximum)) in numeric_values)
            levels = criterion["levels"]
            _require(isinstance(levels, list) and len(levels) == len(values))
            _require(all(isinstance(level, dict) and _number(level.get("value"))
                         and _nonempty(level.get("description")) for level in levels))
            _require(sorted(Decimal(str(level["value"])) for level in levels) == numeric_values)
        else:
            _require(all(type(answer) is str for answer in values) and set(values) == {"yes", "no"})
            _require(criterion["scale_max"] is None and criterion["levels"] == [])
        if kind == "flag":
            _require(criterion["weight"] is None)
            flag_types = criterion["flag_types"]
            _require(isinstance(flag_types, list) and bool(flag_types))
            _require(all(_nonempty(value) for value in flag_types))
            _require(len(set(flag_types)) == len(flag_types))
        else:
            _require(_number(criterion["weight"]) and criterion["weight"] >= 0)
            _require(criterion["flag_types"] == [])
