"""Cost/credential/ownership guards. No cloud, SSH, or model calls."""
from decimal import Decimal
import importlib.util
import json
from pathlib import Path
import subprocess
import tempfile
import time
import unittest
from unittest.mock import Mock, patch

if importlib.util.find_spec("fcntl") is None:
    raise unittest.SkipTest("Brev lifecycle uses POSIX fcntl; run these tests on Linux")

FILE = Path(__file__).resolve().parents[1] / "scripts/brev/session.py"
spec = importlib.util.spec_from_file_location("brev_session_tool", FILE)
tool = importlib.util.module_from_spec(spec)
spec.loader.exec_module(tool)


class BrevSessionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.patch = patch.object(tool, "ROOT", self.root)
        self.patch.start()
        self.addCleanup(self.patch.stop)
        self.session = tool.Session(self.root / "artifacts/run")

    def ledger(self, **extra):
        self.session.update(format="doomsday-brev-session-1",
                            name="doomsday-run-20261108-010000-abcdef",
                            environment_id="owned-id", cleanup_epoch=time.time() + 600,
                            deleted=False, **extra)

    def test_private_run_scope_and_ownership_before_destructive_actions(self):
        with self.assertRaises(ValueError):
            tool.Session(self.root / "public")
        self.session.update(format="doomsday-brev-session-1", name="someone-elses-server")
        with patch.object(self.session, "cli") as cli, self.assertRaises(ValueError):
            self.session.cleanup()
        cli.assert_not_called()

    def test_existing_resource_and_unaffordable_price_prevent_creation(self):
        with patch.object(self.session, "inventory", return_value=[{"id": "existing"}]), \
                patch.object(tool, "current_quote") as quote, self.assertRaises(ValueError):
            self.session.create(60, Decimal(3))
        quote.assert_not_called()
        with patch.object(self.session, "inventory", return_value=[]), \
                patch.object(tool, "current_quote", return_value={"conservative_per_hour": "4"}), \
                patch.object(self.session, "cli") as cli, self.assertRaises(ValueError):
            self.session.create(60, Decimal(3))
        cli.assert_not_called()
        self.assertFalse(self.session.path.exists())

    def test_environment_secret_is_not_argv_or_log_and_http_debug_removed(self):
        (self.root / ".env").write_text("BREV_API_KEY=real-control-secret\nLLM_API_KEY=real-infer-secret\n")
        executable = self.root / "backend/.venv/bin/brev"
        executable.parent.mkdir(parents=True)
        executable.touch()
        fake = subprocess.CompletedProcess([], 0, "real-control-secret real-infer-secret", "")
        with patch.dict(tool.os.environ, {"DEBUG_HTTP": "false"}), \
                patch.object(tool.subprocess, "run", return_value=fake) as run:
            output = self.session.cli(["ls", "--json"])
        args, kwargs = run.call_args
        self.assertNotIn("real-control-secret", str(args))
        self.assertEqual(kwargs["env"]["BREV_API_KEY"], "real-control-secret")
        self.assertEqual(kwargs["env"]["DO_NOT_TRACK"], "1")
        self.assertNotIn("DEBUG_HTTP", kwargs["env"])
        self.assertNotIn("real-control-secret", output)
        for file in self.session.directory.glob("cli-*.json"):
            self.assertNotIn("real-infer-secret", file.read_text())
            self.assertEqual(file.stat().st_mode & 0o777, 0o600)

    def test_no_false_deleted_from_early_404_or_cli_acceptance(self):
        self.ledger()
        with patch.object(tool, "rpc", return_value={"http_status": 404}):
            self.session.status()
        self.assertFalse(self.session.state()["deleted"])
        running = {"environment": {"name": self.session.state()["name"], "status": "TERMINATING",
                                   "instance": {"status": {"lifecycleStatus": "running"}}}}
        with patch.object(self.session, "cli", return_value="") as cli, \
                patch.object(tool, "rpc", return_value=running):
            self.session.cleanup()
            self.session.cleanup()
        self.assertEqual(cli.call_count, 2)  # one stop and one delete, no immediate duplicate
        self.assertFalse(self.session.state()["deleted"])
        with patch.object(tool, "rpc", return_value={"http_status": 404}), \
                patch.object(self.session, "inventory", return_value=[]):
            self.session.status()
        self.assertTrue(self.session.state()["deleted"])

    def test_failed_delete_is_retried_on_exact_owned_id(self):
        self.ledger()
        terminating = {"environment": {"name": self.session.state()["name"], "status": "TERMINATING"}}
        with patch.object(self.session, "cli", side_effect=["", ValueError("controlled")]), \
                patch.object(tool, "rpc", return_value=terminating):
            self.session.cleanup()
        first = self.session.state()
        self.assertFalse(first["delete_accepted"])
        self.session.update(last_delete_epoch=time.time() - 61)
        with patch.object(self.session, "cli", return_value="") as cli, \
                patch.object(tool, "rpc", return_value=terminating):
            self.session.cleanup()
        self.assertEqual(cli.call_args_list[-1].args[0], ["delete", "owned-id"])
        self.assertEqual(self.session.state()["cleanup_attempts"], 2)

    def test_expired_or_deleting_session_blocks_compute_actions(self):
        self.ledger()
        self.session.update(cleanup_epoch=time.time() - 1)
        with patch.object(self.session, "cli") as cli, self.assertRaises(ValueError):
            self.session.prepare()
        cli.assert_not_called()

    def test_lifecycle_name_mismatch_is_rejected(self):
        self.ledger()
        with patch.object(tool, "rpc", return_value={"environment": {"name": "not-ours"}}), \
                self.assertRaises(ValueError):
            self.session.status()

    def test_mismatched_id_is_blocked_before_stop_or_delete(self):
        self.ledger()
        with patch.object(tool, "rpc", return_value={"environment": {"name": "foreign-resource"}}), \
                patch.object(self.session, "cli") as cli, self.assertRaises(ValueError):
            self.session.cleanup()
        cli.assert_not_called()

    def test_dead_watchdog_prevents_paid_create(self):
        dead = Mock(pid=123)
        dead.poll.return_value = 1
        with patch.object(self.session, "inventory", return_value=[]), \
                patch.object(tool, "current_quote", return_value={"conservative_per_hour": "1.756896"}), \
                patch.object(tool.subprocess, "Popen", return_value=dead), \
                patch.object(self.session, "cli") as cli, self.assertRaisesRegex(ValueError, "Watchdog"):
            self.session.create(60, Decimal(3))
        cli.assert_not_called()
        self.assertTrue(self.session.state()["create_not_attempted"])

    def test_partial_create_failure_starts_cleanup_without_new_create(self):
        watcher = Mock(pid=123)
        watcher.poll.return_value = None
        original_update = self.session.update
        def update(**changes):
            if "watchdog_pid" in changes:
                changes["watchdog_ready_utc"] = tool.timestamp()
            original_update(**changes)
        with patch.object(self.session, "inventory", return_value=[]), \
                patch.object(tool, "current_quote", return_value={"conservative_per_hour": "1.756896"}), \
                patch.object(tool.subprocess, "Popen", return_value=watcher), \
                patch.object(self.session, "update", side_effect=update), \
                patch.object(self.session, "cli", side_effect=ValueError("creation timeout")) as cli, \
                patch.object(self.session, "cleanup") as cleanup, self.assertRaises(ValueError):
            self.session.create(60, Decimal(3))
        cleanup.assert_called_once()
        self.assertEqual(cli.call_count, 1)
        self.assertTrue(self.session.state()["deletion_attempt_utc"])


if __name__ == "__main__":
    unittest.main()
