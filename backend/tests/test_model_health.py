"""Offline HTTP probes against a local stub, never a generation endpoint."""
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import tempfile
import threading
import time
from types import SimpleNamespace
import unittest

import httpx
from fastapi.testclient import TestClient

from backend.app import create_app
from backend.ml_adapter import AdapterUnavailable, MLAdapter
from backend.model_health import ModelReadiness


@contextmanager
def server(*, health_status=200, models_status=200, models=None, invalid=False, delay=0):
    calls = []

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_):
            pass

        def do_GET(self):
            calls.append((self.command, self.path, self.headers.get("Authorization")))
            if delay:
                time.sleep(delay)
            self.send_response(health_status if self.path == "/health" else models_status)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            body = b"invalid" if invalid else json.dumps({"data": models if models is not None else [{"id": "test-model"}]}).encode()
            try:
                self.wfile.write(body)
            except ConnectionError:
                pass

    httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    settings = SimpleNamespace(base_url=f"http://127.0.0.1:{httpd.server_port}/v1",
                               api_key="private-inference-test-key", model="test-model", api_style="chat_completions")
    try:
        yield settings, calls
    finally:
        httpd.shutdown()
        httpd.server_close()
        thread.join()


class ModelHealthTests(unittest.TestCase):
    def probe(self, settings, **kwargs):
        return ModelReadiness(lambda: settings, **kwargs)

    def assert_safe(self, result):
        self.assertIs(result["generation_tested"], False)
        self.assertIn("checked_at", result)
        self.assertNotIn("private-inference-test-key", json.dumps(result))
        self.assertNotIn("Authorization", json.dumps(result))

    def test_ready_checks_health_and_exact_served_alias_with_authentication(self):
        with server() as (settings, calls):
            result = self.probe(settings).check()
        self.assertEqual(result["model_status"], "ready")
        self.assertEqual([call[:2] for call in calls], [("GET", "/health"), ("GET", "/v1/models")])
        self.assertTrue(all(call[2] == "Bearer " + settings.api_key for call in calls))
        self.assert_safe(result)

    def test_missing_alias_is_unavailable_even_when_server_is_healthy(self):
        with server(models=[{"id": "other-model"}]) as (settings, _):
            result = self.probe(settings).check()
        self.assertEqual(result["reason"], "model_not_loaded")
        self.assertEqual(result["model_status"], "unavailable")

    def test_authentication_failure_does_not_expose_credentials(self):
        with server(models_status=401) as (settings, calls):
            result = self.probe(settings).check()
        self.assertEqual(result["reason"], "authentication_failed")
        self.assertEqual(len(calls), 2)
        self.assert_safe(result)

    def test_not_ready_and_unsupported_health_do_not_continue_to_models(self):
        for status, expected in [(503, "unavailable"), (404, "unknown")]:
            with self.subTest(status=status), server(health_status=status) as (settings, calls):
                result = self.probe(settings).check()
                self.assertEqual(result["model_status"], expected)
                self.assertEqual(len(calls), 1)

    def test_timeout_is_bounded_and_explicit(self):
        with server(delay=.2) as (settings, _):
            started = time.monotonic()
            result = self.probe(settings, timeout=.05).check()
            self.assertLess(time.monotonic() - started, .5)
        self.assertEqual(result["reason"], "timeout")

    def test_connection_failure_is_unavailable(self):
        # A closed loopback port can time out on Windows before refusing the
        # connection. Exercise the refusal branch without OS timing assumptions.
        settings = SimpleNamespace(base_url="http://127.0.0.1:1/v1", api_key="private-inference-test-key",
                                   model="test-model", api_style="chat_completions")
        def refused(request):
            raise httpx.ConnectError("Synthetic connection refusal", request=request)
        def client_factory(**kwargs):
            return httpx.Client(transport=httpx.MockTransport(refused), **kwargs)
        result = self.probe(settings, client_factory=client_factory).check()
        self.assertEqual(result["reason"], "connection_failed")
        self.assertEqual(result["model_status"], "unavailable")
        self.assert_safe(result)

    def test_invalid_model_list_is_unknown(self):
        with server(invalid=True) as (settings, _):
            result = self.probe(settings).check()
        self.assertEqual(result["model_status"], "unknown")
        self.assertEqual(result["reason"], "invalid_probe_response")

    def test_remote_provider_is_unknown_and_never_contacted(self):
        settings = SimpleNamespace(base_url="https://example.invalid/v1", api_style="chat_completions")
        def forbidden_client(**_):
            self.fail("Unsupported provider must not be contacted")
        result = self.probe(settings, client_factory=forbidden_client).check()
        self.assertEqual(result["reason"], "provider_unsupported")
        self.assertEqual(result["model_status"], "unknown")

    def test_configuration_missing_never_contacts_server(self):
        def unavailable():
            raise AdapterUnavailable("Never expose private configuration text")
        result = ModelReadiness(unavailable).check()
        self.assertEqual(result["model_status"], "unconfigured")
        self.assertNotIn("private configuration", result["message"])

    def test_cache_deduplicates_probes_and_expires(self):
        now = [10.0]
        with server() as (settings, calls):
            probe = self.probe(settings, clock=lambda: now[0], cache_seconds=4)
            first = probe.check()
            probe.check()["model_status"] = "changed-by-caller"
            self.assertEqual(probe.check(), first)
            self.assertEqual(len(calls), 2)
            now[0] += 4.1
            probe.check()
            self.assertEqual(len(calls), 4)

    def test_route_keeps_configuration_health_independent(self):
        class ConfiguredAdapter(MLAdapter):
            def availability(self):
                return True, None
        with server(models=[]) as (settings, calls), tempfile.TemporaryDirectory() as directory:
            app = create_app(db_path=Path(directory) / "test.sqlite3", dev_path=None,
                             load_env_file=False, adapter=ConfiguredAdapter(), model_probe=self.probe(settings))
            with TestClient(app) as client:
                self.assertEqual(client.get("/api/health").json()["evaluator_status"], "ready")
                self.assertEqual(calls, [])
                response = client.get("/api/model-health")
                self.assertEqual(response.status_code, 200)
                self.assertEqual(response.headers["Cache-Control"], "no-store")
                self.assertEqual(response.json()["model_status"], "unavailable")
                self.assert_safe(response.json())


if __name__ == "__main__":
    unittest.main()
