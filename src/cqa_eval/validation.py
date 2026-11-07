"""Strict schema plus configuration and transcript reference validation."""
from dataclasses import dataclass
from importlib.resources import files
from jsonschema import Draft202012Validator
from .config import validate_checklist, same_answer, nonempty
from .data import InputError, dumps, parse_json, normalize_dialogue, fingerprint


class ValidationError(InputError):
    pass


def response_schema():
    return parse_json(files("cqa_eval").joinpath("resources/llm_response.schema.json").read_text(encoding="utf-8-sig"))


@dataclass(frozen=True)
class ValidatedAssessment:
    """Immutable validation receipt pinned to the exact checklist snapshot.

    Construct via validate_response; do not construct receipts in backend code.
    """
    _json: str
    checklist_hash: str
    dialogue_hash: str

    def to_dict(self):
        return parse_json(self._json)


def validate_response(response, dialogue, checklist):
    validate_checklist(checklist)
    dialogue = normalize_dialogue(dialogue)
    if isinstance(response, str):
        response = parse_json(response)
    errors = list(Draft202012Validator(response_schema()).iter_errors(response))
    if errors:
        # Do not echo arbitrary model strings, URLs or secrets through error messages.
        path = ".".join(map(str, errors[0].absolute_path)) or "<root>"
        raise ValidationError(f"Schema violation at {path}: {errors[0].validator}")
    if response["conversation_id"] != dialogue["conversation_id"]:
        raise ValidationError("conversation_id mismatch")
    if response["checklist_version"] != checklist["version"]:
        raise ValidationError("checklist_version mismatch")
    config = {c["id"]: c for c in checklist["criteria"]}
    rows = response["criteria"]
    ids = [r["criterion_id"] for r in rows]
    if len(ids) != len(set(ids)) or set(ids) != set(config):
        raise ValidationError("Expected exactly one result for every configured criterion")
    positions = {m["message_id"]: i for i, m in enumerate(dialogue["transcript"])}

    def check_ids(values):
        if any(value not in positions for value in values):
            raise ValidationError("Evidence ID does not exist in this conversation")

    for row in rows:
        c = config[row["criterion_id"]]
        if not nonempty(row["reason"]):
            raise ValidationError("reason must not be blank")
        answer = row["answer"]
        check_ids(row["evidence_message_ids"])
        span = row["checked_range"]
        if span is not None:
            check_ids([span["start_message_id"], span["end_message_id"]])
            if positions[span["start_message_id"]] > positions[span["end_message_id"]]:
                raise ValidationError("checked_range is reversed")
        if row["status"] == "needs_review":
            if answer is not None or not nonempty(row["uncertainty"]):
                raise ValidationError("needs_review requires null answer and uncertainty")
        else:
            if not any(same_answer(answer, allowed) for allowed in c["allowed_answers"]):
                raise ValidationError(f"Invalid answer for criterion {c['id']}")
            if not row["evidence_message_ids"] and span is None:
                raise ValidationError("Evaluated result requires evidence or checked_range")
            # Every failed positive requirement may represent an omitted action.
            # Requiring a range for all binary failures safely covers omissions
            # without guessing semantics from free-form Russian explanations.
            if c["kind"] == "binary" and answer == "no" and span is None:
                raise ValidationError("Failed binary requirement requires checked_range")
        if answer == "na":
            if not c["na_allowed"] or not nonempty(c.get("na_rule")) or not nonempty(row["na_reason"]):
                raise ValidationError("N/A requires permitted condition and na_reason")
        elif row["na_reason"] is not None:
            raise ValidationError("na_reason is only allowed for na")
        if row["uncertainty"] is not None and not nonempty(row["uncertainty"]):
            raise ValidationError("uncertainty must not be blank")
        violations = row["violations"]
        if c["kind"] != "flag" and violations:
            raise ValidationError("Only flags may contain violations")
        if c["kind"] == "flag":
            if (answer == "yes" and not violations) or (answer == "no" and violations):
                raise ValidationError("Flag answer and violations disagree")
            for violation in violations:
                if violation["type"] not in c["flag_types"] or not nonempty(violation["reason"]):
                    raise ValidationError("Invalid violation type or reason")
                check_ids(violation["evidence_message_ids"])
    return ValidatedAssessment(dumps(response, sort_keys=True), fingerprint(checklist), fingerprint(dialogue))


def extract_evidence(validated, dialogue):
    """Exact source quotes/timestamps, separate from the strict model JSON."""
    dialogue = normalize_dialogue(dialogue)
    if fingerprint(dialogue) != validated.dialogue_hash:
        raise ValidationError("Evidence dialogue differs from validated input")
    messages = {m["message_id"]: m for m in dialogue["transcript"]}
    output = {}
    for row in validated.to_dict()["criteria"]:
        ids = list(row["evidence_message_ids"])
        for violation in row["violations"]:
            ids.extend(violation["evidence_message_ids"])
        output[row["criterion_id"]] = [messages[i] for i in dict.fromkeys(ids)]
    return output
