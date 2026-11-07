"""Configuration validation, independent of criterion IDs and provider."""
from decimal import Decimal
from .data import InputError


def nonempty(value):
    return isinstance(value, str) and bool(value.strip())


def number(value):
    return type(value) in (int, float, Decimal) and Decimal(str(value)).is_finite()


def same_answer(left, right):
    if number(left) and number(right):
        return Decimal(str(left)) == Decimal(str(right))
    return type(left) is type(right) and left == right


def validate_checklist(checklist):
    if not isinstance(checklist, dict) or not nonempty(checklist.get("version")):
        raise InputError("checklist.version must be a nonempty string")
    criteria = checklist.get("criteria")
    if not isinstance(criteria, list) or not criteria:
        raise InputError("checklist.criteria must be a nonempty array")
    ids = set()
    sections = checklist.get("sections", [])
    if not isinstance(sections, list) or any(not isinstance(s, dict) or not nonempty(s.get("id")) for s in sections):
        raise InputError("Invalid sections")
    section_ids = {s["id"] for s in sections}
    if len(section_ids) != len(sections):
        raise InputError("Duplicate section ID")
    for c in criteria:
        if not isinstance(c, dict) or not nonempty(c.get("id")) or c["id"] in ids:
            raise InputError("Missing or duplicate criterion ID")
        ids.add(c["id"])
        label = f"criterion {c['id']}"
        if not nonempty(c.get("instruction")) or not nonempty(c.get("title")):
            raise InputError(f"{label}: title/instruction required")
        if c.get("section_id") is not None and c["section_id"] not in section_ids:
            raise InputError(f"{label}: unknown section")
        kind = c.get("kind")
        if kind not in {"binary", "scale", "flag"}:
            raise InputError(f"{label}: unsupported kind")
        if type(c.get("na_allowed")) is not bool:
            raise InputError(f"{label}: na_allowed must be boolean")
        allowed = c.get("allowed_answers")
        if not isinstance(allowed, list) or not allowed:
            raise InputError(f"{label}: allowed_answers required")
        if any(not (type(a) is str and a in {"yes", "no", "na"}) and not number(a) for a in allowed):
            raise InputError(f"{label}: answers must be permitted strings or finite numbers")
        for i, value in enumerate(allowed):
            if any(same_answer(value, old) for old in allowed[:i]):
                raise InputError(f"{label}: duplicate allowed answer")
        if ("na" in allowed) != c["na_allowed"] or (c["na_allowed"] and not nonempty(c.get("na_rule"))):
            raise InputError(f"{label}: inconsistent N/A configuration")
        values = [a for a in allowed if a != "na"]
        if kind == "scale":
            maximum = c.get("scale_max")
            if not number(maximum) or maximum <= 0 or not values or any(not number(a) or a < 0 or a > maximum for a in values):
                raise InputError(f"{label}: invalid discrete scale")
            if not any(same_answer(a, maximum) for a in values):
                raise InputError(f"{label}: maximum absent from scale")
            levels = c.get("levels")
            if not isinstance(levels, list) or len(levels) != len(values) or any(not isinstance(l, dict) or not nonempty(l.get("description")) for l in levels):
                raise InputError(f"{label}: levels must describe every discrete value")
            if any(not number(l.get("value")) for l in levels):
                raise InputError(f"{label}: scale levels need finite numeric values")
            if sorted(Decimal(str(l["value"])) for l in levels) != sorted(Decimal(str(a)) for a in values):
                raise InputError(f"{label}: levels differ from allowed_answers")
        elif set(values) != {"yes", "no"} or any(type(a) is not str for a in values) or c.get("scale_max") is not None:
            raise InputError(f"{label}: binary/flag requires yes/no and no scale")
        if kind == "flag":
            types = c.get("flag_types")
            if c.get("weight") is not None or not isinstance(types, list) or not types or any(not nonempty(t) for t in types) or len(set(types)) != len(types):
                raise InputError(f"{label}: flag requires null weight and unique flag_types")
        elif not number(c.get("weight")) or c["weight"] < 0:
            raise InputError(f"{label}: weight must be finite and nonnegative")
    return checklist
