"""Bounded, cached availability probes for the local vLLM deployment.

These GET requests never generate tokens. Readiness is an observation of the
server and served alias, not a successful inference or a quality measurement.
"""
from datetime import datetime, timezone
import threading
import time
from urllib.parse import urlsplit

import httpx

from .ml_adapter import AdapterUnavailable


class ModelReadiness:
    def __init__(self, settings_provider, *, timeout=2.0, cache_seconds=4.0,
                 client_factory=httpx.Client, clock=time.monotonic):
        self.settings_provider = settings_provider
        self.timeout = timeout
        self.cache_seconds = cache_seconds
        self.client_factory = client_factory
        self.clock = clock
        self._lock = threading.Lock()
        self._cached = None
        self._expires = 0

    def check(self):
        with self._lock:
            if self._cached is not None and self.clock() < self._expires:
                return dict(self._cached)
            result = self._probe()
            result.update(checked_at=datetime.now(timezone.utc).isoformat(), generation_tested=False)
            self._cached = result
            self._expires = self.clock() + self.cache_seconds
            return dict(result)

    @staticmethod
    def _result(status, reason, message):
        return {"model_status": status, "reason": reason, "message": message}

    def _probe(self):
        try:
            settings = self.settings_provider()
        except AdapterUnavailable:
            return self._result("unconfigured", "configuration_missing",
                                "Модель не настроена. Новое оценивание недоступно.")
        parsed = urlsplit(settings.base_url)
        # No guessed health APIs or arbitrary remote probes for other providers.
        if (parsed.scheme not in {"http", "https"}
                or parsed.hostname not in {"127.0.0.1", "localhost", "::1"}
                or parsed.path.rstrip("/") != "/v1"
                or parsed.username or parsed.password or parsed.query or parsed.fragment
                or settings.api_style != "chat_completions"):
            return self._result("unknown", "provider_unsupported",
                                "Для этого подключения готовность модели не проверяется. Статус неизвестен.")
        origin = f"{parsed.scheme}://{parsed.netloc}"
        deadline = self.clock() + self.timeout
        headers = {"Authorization": "Bearer " + settings.api_key}
        try:
            with self.client_factory(trust_env=False, follow_redirects=False) as client:
                for path in ("/health", "/v1/models"):
                    remaining = deadline - self.clock()
                    if remaining <= 0:
                        raise httpx.TimeoutException("Probe deadline")
                    response = client.get(origin + path, headers=headers, timeout=remaining)
                    if response.status_code in {401, 403}:
                        return self._result("unavailable", "authentication_failed",
                                            "Сервер модели отклонил доступ. Новое оценивание недоступно.")
                    if response.status_code in {404, 405}:
                        return self._result("unknown", "probe_unsupported",
                                            "Сервер не поддерживает проверку готовности. Статус модели неизвестен.")
                    if response.status_code != 200:
                        return self._result("unavailable", "server_not_ready",
                                            "Сервер модели не готов. Новое оценивание недоступно.")
                if len(response.content) > 1_000_000:
                    raise ValueError("Oversized model list")
                payload = response.json()
                models = payload.get("data") if isinstance(payload, dict) else None
                if not isinstance(models, list) or any(not isinstance(item, dict) for item in models):
                    raise ValueError("Invalid model list")
                if not any(item.get("id") == settings.model for item in models):
                    return self._result("unavailable", "model_not_loaded",
                                        "Настроенная модель отсутствует на сервере. Новое оценивание недоступно.")
        except httpx.TimeoutException:
            return self._result("unavailable", "timeout",
                                "Сервер модели не ответил вовремя. Повторите проверку позже.")
        except httpx.HTTPError:
            return self._result("unavailable", "connection_failed",
                                "Нет связи с сервером модели. Сохранённые оценки доступны.")
        except (ValueError, TypeError):
            return self._result("unknown", "invalid_probe_response",
                                "Не удалось подтвердить готовность модели по ответу сервера.")
        return self._result("ready", "server_and_model_available",
                            "Сервер отвечает, настроенная модель доступна. Генерация при проверке не запускалась.")
