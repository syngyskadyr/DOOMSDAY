"""Lossless dialogue loading and stable message identifiers."""
from collections import Counter
from copy import deepcopy
import hashlib
import json
from pathlib import Path


class InputError(ValueError):
    pass


def dumps(value, **kwargs):
    from decimal import Decimal
    return json.dumps(value, ensure_ascii=False, allow_nan=False,
                      default=lambda x: str(x) if isinstance(x, Decimal) else _unsupported(x),
                      **kwargs)


def _unsupported(value):
    raise TypeError(f"Cannot serialize {type(value).__name__}")


def _unique_object(pairs):
    out = {}
    for key, value in pairs:
        if key in out:
            raise InputError(f"Duplicate JSON key: {key}")
        out[key] = value
    return out


def parse_json(text):
    def bad_constant(value):
        raise InputError(f"Non-finite JSON number: {value}")
    try:
        return json.loads(text, object_pairs_hook=_unique_object, parse_constant=bad_constant)
    except json.JSONDecodeError as exc:
        raise InputError(f"Invalid JSON at line {exc.lineno}, column {exc.colno}") from None


def read_json(path):
    return parse_json(Path(path).read_text(encoding="utf-8-sig"))


def fingerprint(value):
    return hashlib.sha256(dumps(value, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def normalize_dialogue(dialogue):
    if not isinstance(dialogue, dict) or not isinstance(dialogue.get("conversation_id"), str) or not dialogue["conversation_id"].strip():
        raise InputError("dialogue.conversation_id must be a nonempty string")
    if not isinstance(dialogue.get("transcript"), list) or not dialogue["transcript"]:
        raise InputError("dialogue.transcript must be a nonempty array")
    result = deepcopy(dialogue)
    for index, message in enumerate(result["transcript"]):
        if not isinstance(message, dict) or any(not isinstance(message.get(k), str) for k in ("timestamp", "speaker", "text")):
            raise InputError(f"transcript[{index}] needs string timestamp, speaker and text")
        if message["speaker"] not in {"client", "operator", "bot", "system"}:
            raise InputError(f"Unsupported speaker at transcript[{index}]")
        message["message_id"] = f"m{index + 1:03d}"
        message["source_index"] = index
    return result


def load_dev(path):
    data = read_json(path)
    if not isinstance(data, dict) or data.get("isSuccess") is not True:
        raise InputError(f"{path}: expected isSuccess=true")
    records = data.get("data", {}).get("records")
    if not isinstance(records, list):
        raise InputError(f"{path}: expected data.records array")
    records = [normalize_dialogue(row) for row in records]
    ids = [r["conversation_id"] for r in records]
    if len(set(ids)) != len(ids):
        raise InputError(f"{path}: duplicate conversation_id")
    return records


def select_dialogue(records, conversation_id):
    for record in records:
        if record["conversation_id"] == conversation_id:
            return record
    raise InputError(f"Unknown conversation_id: {conversation_id}")


def summary(records):
    return {"records": len(records),
            "languages": dict(Counter(r.get("language") for r in records)),
            "channels": dict(Counter(r.get("channel") for r in records)),
            "conversation_ids": [r["conversation_id"] for r in records]}
