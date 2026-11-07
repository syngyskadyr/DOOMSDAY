"""Translate the existing CQA Python component into the public HTTP contract.

All validation and scoring use cqa_eval. There is no model fallback, cache, or
second retry loop here. Importing this module does not require model settings.
"""
from copy import deepcopy
from decimal import Decimal
import importlib
import math

from .checklist_validation import validate_checklist as validate_local_checklist


class AdapterError(RuntimeError):
    def __init__(self, code, message, *, details=None, metadata=None):
        super().__init__(message)
        self.code = code
        self.message = message
        self.details = details
        self.metadata = metadata


class AdapterUnavailable(AdapterError):
    def __init__(self, message="Оценщик недоступен: установите зависимости backend и cqa_eval."):
        super().__init__("EVALUATOR_UNAVAILABLE", message)


class AdapterValidationError(AdapterError):
    def __init__(self, message="Данные не соответствуют чек-листу или схеме оценки.", *, details=None):
        super().__init__("VALIDATION_ERROR", message, details=details)


class AdapterEvaluationError(AdapterError):
    def __init__(self, code="EVALUATION_FAILED", message="Не удалось получить корректную оценку модели.", *, metadata=None):
        super().__init__(code, message, metadata=metadata)


def to_json_compatible(value):
    """HTTP uses JSON numbers; the ML CLI deliberately emits Decimal as strings."""
    if isinstance(value, Decimal):
        value = float(value)
    if isinstance(value, float) and not math.isfinite(value):
        raise AdapterValidationError("Результат содержит неконечное число.")
    if isinstance(value, dict):
        return {key: to_json_compatible(item) for key, item in value.items()}
    if isinstance(value, (tuple, list)):
        return [to_json_compatible(item) for item in value]
    return value


def _nonempty(value):
    return isinstance(value, str) and bool(value.strip())


class MLAdapter:
    def _modules(self):
        try:
            return (importlib.import_module("cqa_eval"),
                    importlib.import_module("cqa_eval.config"),
                    importlib.import_module("cqa_eval.evaluator"))
        except ImportError:
            raise AdapterUnavailable() from None

    def _settings(self):
        _, _, evaluator = self._modules()
        try:
            settings = evaluator.Settings.from_env()
            missing = settings.missing()
            if missing:
                raise AdapterUnavailable("Оценщик не настроен: задайте " + ", ".join(missing) + ".")
            settings.validate()
            return settings
        except AdapterUnavailable:
            raise
        except (ValueError, ArithmeticError):
            # Never expose environment values or raw exceptions through health.
            raise AdapterUnavailable("Некорректные настройки LLM; проверьте переменные окружения сервера.") from None

    def availability(self):
        """Configuration-only readiness check: never contacts the provider."""
        try:
            self._settings()
            return True, None
        except AdapterUnavailable as exc:
            return False, exc.message

    def validate_checklist(self, checklist):
        try:
            validate_local_checklist(checklist)
            try:
                _, config, _ = self._modules()
            except (AdapterUnavailable, ImportError):
                # Import, storage, and the editor also work without ML installed.
                config = None
            if config is not None:
                config.validate_checklist(checklist)
            # Validate all HTTP numbers as finite, including unused extra fields.
            to_json_compatible(checklist)
        except (ValueError, TypeError, KeyError, ArithmeticError):
            raise AdapterValidationError(
                "Некорректный чек-лист: проверьте обязательные поля, секции, веса, шкалы, N/A и типы флагов."
            ) from None

    def result_state(self, dialogue, checklist, assessment):
        """Offline validation/recalculation, also used after an appeal decision."""
        component, _, _ = self._modules()
        self.validate_checklist(checklist)
        try:
            validated = component.validate_response(assessment, dialogue, checklist)
            calculated = component.calculate_score(checklist, validated)
            calculation = {
                "total_score": calculated["total_score"],
                "applicable_weight": calculated["applicable_weight"],
                "no_applicable_criteria": calculated["no_applicable_weight"],
                "rows": [{key: row[key] for key in
                          ("criterion_id", "base_weight", "effective_weight", "earned_points")}
                         for row in calculated["rows"]],
                "sections": [{"section_id": section["section_id"],
                              "earned_points": section["earned_points"],
                              "possible_points": section["applicable_max"]}
                             for section in calculated["sections"]],
            }
            return to_json_compatible({"assessment": validated.to_dict(),
                                       "calculation": calculation,
                                       "critical_error": calculated["gross_error"]})
        except (ValueError, TypeError, KeyError, ArithmeticError):
            raise AdapterValidationError() from None

    def metadata(self, metadata):
        """Whitelist fields and aggregate usage over every actual attempt.

        Missing usage from a failed call makes that total unknown; a later
        successful retry must not conceal it by reporting only the last usage.
        """
        metadata = metadata or {}
        attempts = metadata.get("attempts") or []
        usage = None
        if any(isinstance(attempt.get("usage"), dict) for attempt in attempts):
            usage = {}
            for key in ("input_tokens", "output_tokens"):
                values = [(attempt.get("usage") or {}).get(key) for attempt in attempts]
                usage[key] = sum(values) if all(type(value) is int and value >= 0 for value in values) else None
        cost = metadata.get("cost_usd")
        cost_note = metadata.get("cost_note")
        if cost is None and not _nonempty(cost_note):
            cost_note = "Стоимость неизвестна: нет usage или настроенных тарифов провайдера."
        duration = metadata.get("duration_seconds")
        return to_json_compatible({
            "model": metadata.get("model"),
            "cache_hit": metadata.get("cache_hit", False),
            "api_calls": metadata.get("call_count", len(attempts)),
            "retry_count": metadata.get("retry_count", max(0, len(attempts) - 1)),
            "latency_ms": duration * 1000 if duration is not None else None,
            "usage": usage,
            "estimated_cost_usd": cost,
            "cost_note": cost_note,
            "dialogue_hash": metadata.get("dialogue_hash"),
            "checklist_hash": metadata.get("checklist_hash"),
            "prompt_hash": metadata.get("prompt_hash"),
        })

    def evaluate(self, dialogue, checklist):
        """Run the real synchronous evaluator; the server schedules this in a worker."""
        settings = self._settings()
        component, _, evaluator = self._modules()
        self.validate_checklist(checklist)
        try:
            # Isolate job snapshots from any incidental component mutations.
            result = component.evaluate_dialogue(deepcopy(dialogue), deepcopy(checklist), settings=settings)
            state = self.result_state(dialogue, checklist, result["assessment"])
            return {**state, "metadata": self.metadata(result["metadata"])}
        except evaluator.EvaluationError as exc:
            metadata = self.metadata(exc.metadata)
            attempts = exc.metadata.get("attempts") or []
            error = attempts[-1].get("error") if attempts else None
            code = {"transport_error": "PROVIDER_TRANSPORT_ERROR",
                    "invalid_model_response": "INVALID_MODEL_RESPONSE"}.get(error, "PROVIDER_ERROR")
            raise AdapterEvaluationError(code, metadata=metadata) from None
        except AdapterUnavailable:
            raise
        except (ValueError, TypeError, KeyError, ArithmeticError, AdapterValidationError):
            raise AdapterEvaluationError("INVALID_MODEL_RESPONSE") from None
