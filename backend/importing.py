"""Validate organizer exports without changing their transcript contents."""

from copy import deepcopy
import json
import math


class InvalidJSON(ValueError):
    code = "INVALID_JSON"
    message = "Тело запроса должно содержать корректный JSON без повторных ключей."
    details = None


class ImportValidationError(ValueError):
    code = "INVALID_IMPORT"
    message = "Некорректные данные для импорта диалогов."

    def __init__(self, field, message):
        self.details = [{"field": field, "message": message}]
        super().__init__(self.message)


class ImportConflictError(ValueError):
    code = "CONVERSATION_CONFLICT"
    message = "Один ID диалога содержит разные данные. Импорт отменён."
    details = None


def parse_json(data):
    """Reject duplicate keys and JavaScript's non-JSON numeric constants."""
    def unique_object(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise InvalidJSON()
            result[key] = value
        return result

    def invalid_constant(_value):
        raise InvalidJSON()

    def finite_float(value):
        result = float(value)
        if not math.isfinite(result):
            raise InvalidJSON()
        return result

    try:
        return json.loads(data, object_pairs_hook=unique_object,
                          parse_constant=invalid_constant, parse_float=finite_float)
    except (json.JSONDecodeError, UnicodeDecodeError, TypeError, ValueError):
        raise InvalidJSON() from None


def canonical(record):
    """Object key order is irrelevant; array order and all values are retained."""
    try:
        return json.dumps(record, ensure_ascii=True, allow_nan=False,
                          sort_keys=True, separators=(",", ":"))
    except (TypeError, ValueError):
        raise ImportValidationError("records", "Ожидаются конечные JSON-значения.") from None


def _nonempty(value):
    return isinstance(value, str) and bool(value.strip())


def _validate_record(record, index):
    prefix = f"records[{index}]"
    if not isinstance(record, dict):
        raise ImportValidationError(prefix, "Диалог должен быть объектом.")
    for field in ("conversation_id", "channel", "language"):
        if not _nonempty(record.get(field)):
            raise ImportValidationError(f"{prefix}.{field}", "Нужна непустая строка.")
    if "audio_url" in record and record["audio_url"] is not None and not isinstance(record["audio_url"], str):
        raise ImportValidationError(f"{prefix}.audio_url", "Нужна строка или null.")
    if "synthetic" in record and type(record["synthetic"]) is not bool:
        raise ImportValidationError(f"{prefix}.synthetic", "Нужно логическое значение.")
    transcript = record.get("transcript")
    if not isinstance(transcript, list) or not transcript:
        raise ImportValidationError(f"{prefix}.transcript", "Нужен непустой массив реплик.")
    for message_index, message in enumerate(transcript):
        message_prefix = f"{prefix}.transcript[{message_index}]"
        if not isinstance(message, dict):
            raise ImportValidationError(message_prefix, "Реплика должна быть объектом.")
        for field in ("timestamp", "speaker", "text"):
            if not isinstance(message.get(field), str):
                raise ImportValidationError(f"{message_prefix}.{field}", "Нужна строка.")
        offset = message.get("audio_offset_seconds")
        if offset is not None and (type(offset) not in (int, float) or offset < 0 or
                                   (type(offset) is float and not math.isfinite(offset))):
            raise ImportValidationError(f"{message_prefix}.audio_offset_seconds", "Нужно неотрицательное конечное число или null.")


def normalize_import(payload):
    """Validate the entire batch and return unique, lossless source records.

    Duplicate identical IDs inside one batch collapse into one accepted record.
    The storage layer decides which accepted records already exist in SQLite.
    """
    if isinstance(payload, dict) and "conversation_id" not in payload and ("isSuccess" in payload or "data" in payload):
        if payload.get("isSuccess") is not True:
            raise ImportValidationError("isSuccess", "Выгрузка должна иметь isSuccess=true.")
        data = payload.get("data")
        if not isinstance(data, dict) or not isinstance(data.get("records"), list):
            raise ImportValidationError("data.records", "Нужен массив диалогов.")
        records = data["records"]
    elif isinstance(payload, list):
        records = payload
    elif isinstance(payload, dict):
        records = [payload]
    else:
        raise ImportValidationError("records", "Нужны выгрузка, массив или один диалог.")

    accepted = {}
    fingerprints = {}
    for index, record in enumerate(records):
        _validate_record(record, index)
        fingerprint = canonical(record)
        conversation_id = record["conversation_id"]
        if conversation_id in fingerprints and fingerprints[conversation_id] != fingerprint:
            raise ImportConflictError()
        if conversation_id not in accepted:
            accepted[conversation_id] = deepcopy(record)
            fingerprints[conversation_id] = fingerprint
    return list(accepted.values())


def messages(record):
    """Stable source indices include every role; timestamps never imply a seek."""
    return [{"message_id": f"m{index + 1:03d}", "source_index": index,
             "timestamp": message["timestamp"], "speaker": message["speaker"],
             "text": message["text"],
             "audio_offset_seconds": message.get("audio_offset_seconds")}
            for index, message in enumerate(record["transcript"])]
