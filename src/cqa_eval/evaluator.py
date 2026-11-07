"""Explicitly configured REST adapter, no SDK or hidden automatic retries."""
from copy import deepcopy
from dataclasses import dataclass, field
from decimal import Decimal
from importlib.resources import files
import os
import socket
import time
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import Request, build_opener, HTTPRedirectHandler

from .config import validate_checklist, nonempty
from .core import calculate_score
from .data import InputError, dumps, fingerprint, normalize_dialogue, parse_json
from .validation import validate_response, response_schema, extract_evidence


class EvaluationError(RuntimeError):
    def __init__(self, message, metadata=None):
        super().__init__(message)
        self.metadata = metadata or {}


class PersistenceError(RuntimeError):
    """A journal failure must stop processing, never trigger an API retry."""


@dataclass(frozen=True)
class Settings:
    api_key: str = field(default="", repr=False)
    base_url: str = ""
    model: str = ""
    api_style: str = ""
    structured_output: str = "json_schema"
    timeout: float = 90
    max_attempts: int = 2
    input_price: Decimal | None = None
    output_price: Decimal | None = None
    max_output_tokens: int | None = None

    @classmethod
    def from_env(cls):
        def price(name):
            value = os.getenv(name, "").strip()
            return Decimal(value) if value else None
        try:
            result = cls(api_key=os.getenv("LLM_API_KEY", "").strip(),
                         base_url=os.getenv("LLM_BASE_URL", "").strip(),
                         model=os.getenv("LLM_MODEL", "").strip(),
                         api_style=os.getenv("LLM_API_STYLE", "").strip(),
                         structured_output=os.getenv("LLM_STRUCTURED_OUTPUT", "json_schema").strip(),
                         timeout=float(os.getenv("LLM_TIMEOUT_SECONDS", "90")),
                         max_attempts=int(os.getenv("LLM_MAX_ATTEMPTS", "2")),
                         input_price=price("LLM_INPUT_USD_PER_MILLION"),
                         output_price=price("LLM_OUTPUT_USD_PER_MILLION"))
            result.validate(require_complete=False)
            return result
        except (ValueError, ArithmeticError):
            raise InputError("Invalid numeric LLM configuration") from None

    def missing(self):
        return [name for name, value in (("LLM_API_KEY", self.api_key), ("LLM_BASE_URL", self.base_url),
                ("LLM_MODEL", self.model), ("LLM_API_STYLE", self.api_style)) if not value]

    def validate(self, require_complete=True):
        if require_complete and self.missing():
            raise InputError("Missing settings: " + ", ".join(self.missing()))
        if self.api_style and self.api_style not in {"chat_completions", "responses"}:
            raise InputError("LLM_API_STYLE must be chat_completions or responses")
        if self.structured_output not in {"json_schema", "json_object"}:
            raise InputError("LLM_STRUCTURED_OUTPUT must be json_schema or json_object")
        if not 0 < self.timeout <= 300 or not 1 <= self.max_attempts <= 2:
            raise InputError("Timeout must be 0..300 seconds; attempts must be 1 or 2")
        if self.max_output_tokens is not None and (type(self.max_output_tokens) is not int or self.max_output_tokens <= 0):
            raise InputError("max_output_tokens must be a positive integer")
        for value in (self.input_price, self.output_price):
            if value is not None and (not value.is_finite() or value < 0):
                raise InputError("Token prices must be finite and nonnegative")
        if self.base_url:
            url = urlsplit(self.base_url)
            if url.scheme not in {"https", "http"} or not url.hostname or url.username or url.password or url.query or url.fragment:
                raise InputError("LLM_BASE_URL needs a plain API base URL without credentials/query/fragment")
            if url.scheme == "http" and url.hostname not in {"localhost", "127.0.0.1", "::1"}:
                raise InputError("Remote API requires HTTPS")


def provider_schema(checklist):
    """Compatible structural subset; full conditional contract is checked locally."""
    schema = response_schema()
    # Strict structured-output providers accept only a subset of JSON Schema.
    # Remove unsupported constraints, not fields; local validation stays strict.
    def subset(value):
        if isinstance(value, list):
            return [subset(v) for v in value]
        if not isinstance(value, dict):
            return value
        out = {k: subset(v) for k, v in value.items()
               if k not in {"$schema", "title", "allOf", "minLength", "minItems", "uniqueItems", "pattern"}}
        if "enum" in out and "type" not in out:
            out["type"] = "string"
        # The only property-level allOf is the evidence IDs reference.
        if "allOf" in value and "$ref" in value["allOf"][0]:
            out.update(subset(value["allOf"][0]))
        return out
    schema = subset(schema)
    schema["$defs"]["criterion"]["properties"]["criterion_id"]["enum"] = [c["id"] for c in checklist["criteria"]]
    return schema


def prepare_request(dialogue, checklist):
    validate_checklist(checklist)
    dialogue = normalize_dialogue(dialogue)
    system = files("cqa_eval").joinpath("resources/evaluator_system.txt").read_text(encoding="utf-8-sig")
    system += ("\nДля любого binary=no укажи checked_range: он покрывает и пропущенные действия. "
               "Для низкой шкальной оценки за пропущенное действие также укажи checked_range. "
               "Порог первой реакции исходного регламента — 3 минуты; теги и вложения исключены. "
               "ISO-время звонка не является смещением в аудио. Применение чатового чек-листа к звонкам — временное допущение. "
               "Условия и изменённые формулировки текущего checklist имеют приоритет.\n")
    conversation = {k: deepcopy(v) for k, v in dialogue.items() if k != "audio_url"}
    user = dumps({"checklist": checklist, "response_schema": response_schema(),
                  "conversation": conversation})
    return {"system": system, "user": user, "provider_schema": provider_schema(checklist)}


def request_body(prepared, settings):
    fmt = {"type": settings.structured_output}
    if settings.structured_output == "json_schema":
        fmt.update(name="cqa_assessment", strict=True, schema=prepared["provider_schema"])
    if settings.api_style == "chat_completions":
        chat_format = ({"type": "json_schema", "json_schema": {k: v for k, v in fmt.items() if k != "type"}}
                       if settings.structured_output == "json_schema" else fmt)
        body = {"model": settings.model,
                "messages": [{"role": "system", "content": prepared["system"]},
                             {"role": "user", "content": prepared["user"]}],
                "response_format": chat_format}
        if settings.max_output_tokens is not None:
            body["max_tokens"] = settings.max_output_tokens
        return body
    body = {"model": settings.model, "instructions": prepared["system"],
            "input": [{"role": "user", "content": prepared["user"]}],
            "text": {"format": fmt}}
    if settings.max_output_tokens is not None:
        body["max_output_tokens"] = settings.max_output_tokens
    return body


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def _http_post(url, body, settings):
    request = Request(url, data=dumps(body).encode("utf-8"), method="POST",
                      headers={"Authorization": "Bearer " + settings.api_key,
                               "Content-Type": "application/json"})
    with build_opener(_NoRedirect()).open(request, timeout=settings.timeout) as response:
        return parse_json(response.read().decode("utf-8"))


def _text(response, style):
    try:
        if style == "chat_completions":
            choice = response["choices"][0]
            message = choice["message"]
            if message.get("refusal") or choice.get("finish_reason") != "stop":
                raise ValueError()
            value = message["content"]
        else:
            if response.get("status") != "completed":
                raise ValueError()
            parts = [p for item in response["output"] if item.get("type") == "message"
                     for p in item.get("content", [])]
            if any(p.get("type") == "refusal" for p in parts):
                raise ValueError()
            value = "".join(p["text"] for p in parts if p.get("type") == "output_text")
        if not nonempty(value):
            raise ValueError()
        return value
    except (KeyError, IndexError, TypeError, ValueError, AttributeError):
        raise InputError("API returned no complete assessment (refusal, truncation or invalid envelope)") from None


def _usage(response):
    usage = response.get("usage")
    if not isinstance(usage, dict):
        return None
    def count(*keys):
        for key in keys:
            value = usage.get(key)
            if type(value) is int and value >= 0:
                return value
        return None
    return {"input_tokens": count("input_tokens", "prompt_tokens"),
            "output_tokens": count("output_tokens", "completion_tokens"),
            "total_tokens": count("total_tokens")}


def _cost(usage, settings):
    if (usage is None or any(usage.get(k) is None for k in ("input_tokens", "output_tokens"))
            or settings.input_price is None or settings.output_price is None):
        return None
    return (Decimal(usage["input_tokens"]) * settings.input_price
            + Decimal(usage["output_tokens"]) * settings.output_price) / Decimal(1000000)


def evaluate_dialogue(dialogue, checklist, *, settings=None, on_event=None):
    """Return backend wrapper; errors raise EvaluationError with attempt metadata.

    No labels, fixtures, data files, or predictions are read by this function.
    """
    settings = settings or Settings.from_env()
    settings.validate()
    def emit(event, value):
        if on_event is not None:
            try:
                on_event(event, value)
            except OSError:
                raise PersistenceError("Cannot persist API journal; processing stopped") from None
    dialogue = normalize_dialogue(dialogue)
    prepared = prepare_request(dialogue, checklist)
    body = request_body(prepared, settings)
    suffix = "chat/completions" if settings.api_style == "chat_completions" else "responses"
    url = settings.base_url.rstrip("/") + "/" + suffix
    metadata = {"component_version": "0.1.0", "checklist_version": checklist["version"],
                "checklist_schema_version": checklist.get("schema_version"),
                "dialogue_hash": fingerprint(dialogue), "checklist_hash": fingerprint(checklist),
                "instruction_hash": fingerprint(prepared["system"]),
                "response_schema_hash": fingerprint(response_schema()),
                "prompt_hash": fingerprint(prepared), "request_hash": fingerprint(body),
                "api_style": settings.api_style, "requested_model": settings.model,
                "generation_settings": {"structured_output": settings.structured_output,
                                        "max_output_tokens": settings.max_output_tokens},
                "cache_hit": False, "attempts": [], "cost_usd": None}
    started = time.monotonic()

    def finish():
        metadata["call_count"] = len(metadata["attempts"])
        metadata["retry_count"] = max(0, metadata["call_count"] - 1)
        metadata["duration_seconds"] = time.monotonic() - started
        metadata["model"] = metadata["attempts"][-1]["model"] if metadata["attempts"] else None
        costs = [a["cost_usd"] for a in metadata["attempts"]]
        metadata["cost_usd"] = sum(costs, Decimal(0)) if costs and all(c is not None for c in costs) else None
        metadata["cost_note"] = "Configured token-rate estimate; excludes other provider charges" if metadata["cost_usd"] is not None else "Usage and/or configured token prices unavailable; cost is unknown"

    for attempt_number in range(1, settings.max_attempts + 1):
        attempt_start = time.monotonic()
        attempt = {"attempt": attempt_number, "requested_model": settings.model, "model": None,
                   "usage": None, "cost_usd": None, "error": None}
        metadata["attempts"].append(attempt)
        emit("attempt_started", {"attempt": attempt_number})
        retry = False
        try:
            response = _http_post(url, body, settings)
            # Persist the returned JSON before parsing/semantic validation.
            emit("response_received", {"attempt": attempt_number, "response": response})
            if not isinstance(response, dict):
                raise InputError("Invalid API envelope")
            attempt["model"] = response.get("model") if isinstance(response.get("model"), str) else None
            attempt["usage"] = _usage(response)
            attempt["cost_usd"] = _cost(attempt["usage"], settings)
            assessment = validate_response(_text(response, settings.api_style), dialogue, checklist)
        except HTTPError as exc:
            attempt["error"] = f"http_{exc.code}"
            retry = exc.code in {408, 429, 500, 502, 503, 504}
        except (URLError, TimeoutError, socket.timeout, ConnectionError, OSError):
            attempt["error"] = "transport_error"
            retry = True
        except (InputError, UnicodeError):
            attempt["error"] = "invalid_model_response"
            # No paid format-repair retry. Save every actual attempt.
        finally:
            attempt["duration_seconds"] = time.monotonic() - attempt_start
            emit("attempt_finished", attempt)
        if attempt["error"] is None:
            finish()
            calculation = calculate_score(checklist, assessment)
            return {"assessment": assessment.to_dict(), "calculation": calculation,
                    "critical_error": calculation["gross_error"],
                    "checklist_snapshot": deepcopy(checklist),
                    "evidence": extract_evidence(assessment, dialogue), "metadata": metadata}
        if not retry or attempt_number == settings.max_attempts:
            finish()
            raise EvaluationError("Evaluation failed: " + attempt["error"], metadata)
        time.sleep(min(attempt_number, 2))
    raise AssertionError("unreachable")
