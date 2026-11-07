"""Check the runnable API documentation and public-directory startup contract."""
from contextlib import redirect_stderr
import io
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient

from backend.app import FrontendDirectoryError, create_app


class FrontendHandoffTests(unittest.TestCase):
    def test_explicit_missing_public_directory_is_a_safe_startup_error(self):
        with tempfile.TemporaryDirectory() as directory:
            missing = Path(directory) / "private-marker"
            with self.assertRaises(FrontendDirectoryError) as caught:
                create_app(frontend_path=missing, load_env_file=False)
            self.assertNotIn("private-marker", str(caught.exception))
            from backend.__main__ import main
            stderr = io.StringIO()
            with patch("sys.argv", ["backend", "--no-env-file", "--frontend", str(missing)]), \
                    patch("backend.__main__.uvicorn.run") as run, redirect_stderr(stderr), \
                    self.assertRaises(SystemExit) as exit_result:
                main()
            self.assertEqual(exit_result.exception.code, 2)
            self.assertNotIn("private-marker", stderr.getvalue())
            run.assert_not_called()

    def test_swagger_import_example_can_be_submitted_and_all_writes_accept_json(self):
        with tempfile.TemporaryDirectory() as directory:
            app = create_app(db_path=Path(directory) / "test.sqlite3", dev_path=None, load_env_file=False)
            with TestClient(app) as client:
                schema = client.get("/openapi.json").json()
                post_paths = [path for path, operations in schema["paths"].items() if "post" in operations]
                self.assertEqual(len(post_paths), 5)
                for path in post_paths:
                    body = schema["paths"][path]["post"]["requestBody"]
                    self.assertTrue(body["required"])
                    self.assertIn("application/json", body["content"])
                request_schema = schema["paths"]["/api/conversations/import"]["post"]["requestBody"]["content"]["application/json"]["schema"]
                example = request_schema["oneOf"][0]["example"]
                self.assertTrue(example["synthetic"])
                imported = client.post("/api/conversations/import", json=example)
                self.assertEqual(imported.status_code, 200, imported.text)
                self.assertEqual(imported.json()["imported"], 1)
                detail = client.get("/api/conversations/" + example["conversation_id"]).json()
                self.assertEqual(detail["messages"][0]["text"], example["transcript"][0]["text"])
                self.assertEqual(detail["evaluation_history"], [])


if __name__ == "__main__":
    unittest.main()
