"""Backend .env integration tests. No model or network requests are made."""
import asyncio
from contextlib import redirect_stderr
import io
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from backend import environment
from backend.app import create_app
from backend.environment import EnvironmentFileError, load_environment
from backend.ml_adapter import MLAdapter


def health(app):
    return next(route.endpoint for route in app.routes if getattr(route, "path", None) == "/api/health")()


class EnvironmentTests(unittest.TestCase):
    def setUp(self):
        # load_environment intentionally fills process defaults. Restore every
        # variable after each test so no credentials/settings reach other tests.
        self.env_patch = patch.dict(os.environ, {}, clear=True)
        self.env_patch.start()
        self.addCleanup(self.env_patch.stop)
        self.tmp = tempfile.TemporaryDirectory(prefix="doomsday-environment-")
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.default = self.root / ".env"
        self.default_patch = patch.object(environment, "DEFAULT_ENV_FILE", self.default)
        self.default_patch.start()
        self.addCleanup(self.default_patch.stop)

    def write(self, text, name=".env"):
        path = self.root / name
        path.write_text(text, encoding="utf-8")
        return path

    def configured_file(self, extra="", name=".env"):
        return self.write("LLM_API_KEY='private-test-key'\n"
                          "LLM_BASE_URL=https://provider.example/v1\n"
                          "LLM_MODEL=test-model\n"
                          "LLM_API_STYLE=chat_completions\n" + extra, name)

    def test_default_file_is_optional_and_exact(self):
        self.assertIsNone(load_environment())
        self.assertEqual(dict(os.environ), {})
        self.write("DOOMSDAY_TEST_VALUE=from-default\n")
        self.assertEqual(load_environment(), self.default)
        self.assertEqual(os.environ["DOOMSDAY_TEST_VALUE"], "from-default")

    def test_explicit_file_selected_and_missing_file_is_safe(self):
        self.write("DOOMSDAY_TEST_VALUE=ignored-default\n")
        explicit = self.write("DOOMSDAY_TEST_VALUE=from-explicit\n", "custom.env")
        self.assertEqual(load_environment(explicit), explicit)
        self.assertEqual(os.environ["DOOMSDAY_TEST_VALUE"], "from-explicit")
        with self.assertRaises(EnvironmentFileError) as caught:
            load_environment(self.root / "private-key-in-filename.env")
        self.assertNotIn("private-key-in-filename", str(caught.exception))

    def test_disabled_loading_does_not_read_even_explicit_file(self):
        self.write("DOOMSDAY_TEST_VALUE=must-not-load\n")
        with patch.object(Path, "read_text", side_effect=AssertionError("unexpected file read")):
            self.assertIsNone(load_environment(enabled=False))
            self.assertIsNone(load_environment(self.root / "missing.env", enabled=False))
        self.assertEqual(dict(os.environ), {})

    def test_process_values_including_empty_strings_win(self):
        self.write("LLM_MODEL=file-model\nLLM_API_KEY=file-key\nNEW_VALUE=file-default\n")
        os.environ.update(LLM_MODEL="process-model", LLM_API_KEY="")
        load_environment()
        self.assertEqual(os.environ["LLM_MODEL"], "process-model")
        self.assertEqual(os.environ["LLM_API_KEY"], "")
        self.assertEqual(os.environ["NEW_VALUE"], "file-default")

    def test_special_characters_are_literal_and_bom_is_supported(self):
        key = 'sk-#= ${NOT_EXPANDED}-$NOT_EXPANDED-`literal`-"quote"-\\path'
        path = self.write("\ufeffLLM_API_KEY='" + key + "'\n"
                          "'ODD.KEY-01'='literal # = $ ${NOT_EXPANDED}'\n"
                          "EMPTY=\nUNASSIGNED\n")
        target = {"NOT_EXPANDED": "must-not-appear"}
        load_environment(path, environ=target)
        self.assertEqual(target["LLM_API_KEY"], key)
        self.assertEqual(target["ODD.KEY-01"], "literal # = $ ${NOT_EXPANDED}")
        self.assertEqual(target["EMPTY"], "")
        self.assertNotIn("UNASSIGNED", target)
        self.assertNotIn("LLM_API_KEY", os.environ)

    def test_invalid_environment_entries_are_rejected_before_mutation(self):
        for invalid in ("'BAD=KEY'='private-marker'", "BAD='private\0marker'"):
            with self.subTest(invalid=invalid):
                path = self.write("SAFE_BEFORE=must-not-load\n" + invalid + "\n")
                with self.assertRaises(EnvironmentFileError) as caught:
                    load_environment(path)
                self.assertNotIn("private", str(caught.exception))
                self.assertNotIn("SAFE_BEFORE", os.environ)

    def test_malformed_lines_fail_safely_without_partial_environment_or_logs(self):
        for malformed in ("BAD='private-marker", "invalid statement private-marker"):
            with self.subTest(malformed=malformed):
                path = self.write("SAFE_BEFORE=must-not-load\n" + malformed + "\n")
                with self.assertNoLogs("dotenv.main", level="WARNING"), \
                        self.assertRaises(EnvironmentFileError) as caught:
                    load_environment(path)
                self.assertIn("2", str(caught.exception))
                self.assertNotIn("private-marker", str(caught.exception))
                self.assertNotIn("SAFE_BEFORE", os.environ)

    def test_direct_factory_loads_default_before_db_and_readiness_without_network(self):
        db_path = self.root / "from-file.sqlite3"
        self.configured_file(f"DOOMSDAY_DB_PATH='{db_path}'\n")
        with patch("cqa_eval.evaluator._http_post") as post:
            app = create_app(dev_path=None)
            self.assertEqual(health(app)["evaluator_status"], "ready")

            async def check_lifespan():
                async with app.router.lifespan_context(app):
                    self.assertEqual(app.state.store.path, db_path)
                    self.assertEqual(app.state.service.checklist()["version"], "1")

            asyncio.run(check_lifespan())
            post.assert_not_called()

    def test_factory_explicit_file_and_existing_dependency_injection(self):
        self.write("LLM_MODEL=ignored-default\n")
        explicit = self.configured_file(name="chosen.env")
        adapter = MLAdapter()
        with patch("cqa_eval.evaluator._http_post") as post:
            app = create_app(db_path=self.root / "injected.sqlite3", dev_path=None,
                             adapter=adapter, env_file=explicit)
            self.assertEqual(os.environ["LLM_MODEL"], "test-model")
            with patch.object(adapter, "availability", return_value=(True, "injected")):
                self.assertEqual(health(app)["evaluator_message"], "injected")
            post.assert_not_called()

    def test_disabled_factory_keeps_evaluator_unavailable(self):
        self.configured_file()
        with patch("cqa_eval.evaluator._http_post") as post:
            app = create_app(dev_path=None, load_env_file=False)
            result = health(app)
            self.assertEqual(result["evaluator_status"], "unavailable")
            self.assertNotIn("LLM_API_KEY", os.environ)
            post.assert_not_called()

    def test_invalid_llm_settings_are_unavailable_without_secret_disclosure(self):
        self.configured_file("LLM_TIMEOUT_SECONDS=private-marker\n")
        with patch("cqa_eval.evaluator._http_post") as post:
            result = health(create_app(dev_path=None))
            self.assertEqual(result["evaluator_status"], "unavailable")
            serialized = json.dumps(result)
            self.assertNotIn("private-marker", serialized)
            self.assertNotIn("private-test-key", serialized)
            post.assert_not_called()

    def test_cli_passes_default_explicit_and_disabled_options(self):
        from backend.__main__ import main
        variants = [([], None, True), (["--env-file", "chosen.env"], Path("chosen.env"), True),
                    (["--no-env-file"], None, False)]
        for arguments, expected_path, enabled in variants:
            with self.subTest(arguments=arguments), \
                    patch("sys.argv", ["backend", "--no-dev", *arguments]), \
                    patch("backend.__main__.create_app") as factory, \
                    patch("backend.__main__.uvicorn.run") as run:
                main()
                self.assertEqual(factory.call_args.kwargs["env_file"], expected_path)
                self.assertEqual(factory.call_args.kwargs["load_env_file"], enabled)
                self.assertIsNone(factory.call_args.kwargs["dev_path"])
                run.assert_called_once()

    def test_cli_conflicting_flags_and_file_errors_are_safe(self):
        from backend.__main__ import main
        with patch("sys.argv", ["backend", "--env-file", "chosen.env", "--no-env-file"]), \
                redirect_stderr(io.StringIO()), self.assertRaises(SystemExit) as caught:
            main()
        self.assertEqual(caught.exception.code, 2)
        stderr = io.StringIO()
        with patch("sys.argv", ["backend", "--env-file", str(self.root / "private-marker.env")]), \
                patch("backend.__main__.uvicorn.run") as run, redirect_stderr(stderr), \
                self.assertRaises(SystemExit) as caught:
            main()
        self.assertEqual(caught.exception.code, 2)
        self.assertNotIn("private-marker", stderr.getvalue())
        run.assert_not_called()


if __name__ == "__main__":
    unittest.main()
