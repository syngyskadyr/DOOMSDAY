"""Bounded Brev lifecycle for the pinned DOOMSDAY model. No implicit creation.

Secrets travel only in child-process environments or private files over SSH.
All run directories must be below artifacts/. No Git operation is performed.
"""
import argparse
from datetime import datetime, timezone
from decimal import Decimal
import fcntl
import json
import os
from pathlib import Path
import re
import secrets
import subprocess
import sys
import time
from uuid import uuid4

from dotenv import dotenv_values, set_key

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
REVISION = "31c69efc29464b6bb0aee1398b5a7b50a99340c3"
MODEL = "doomsday-qwen3-14b-awq"
PUBLIC = "https://api.brev.dev/devplaneapi.v1.InstanceService/ListPublicInstanceType"
ENVIRONMENT = "https://api.brev.dev/devplaneapi.v1.EnvironmentService/GetEnvironment"


def timestamp():
    return datetime.now(timezone.utc).isoformat()


def private_write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    with os.fdopen(os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), "w") as out:
        out.write(json.dumps(value, ensure_ascii=False, indent=2) + "\n")


def child_environment():
    values = dotenv_values(ROOT / ".env", interpolate=False)
    key = (values.get("BREV_API_KEY") or "").strip()
    if not key:
        raise ValueError("BREV_API_KEY is missing in local .env")
    environment = os.environ.copy()
    environment.update(BREV_API_KEY=key, DO_NOT_TRACK="1", BREV_NO_ANALYTICS="1",
                       BREV_FEATURE_DEBUG="false", BREV_FEATURE_SHOW_VERSION_ON_RUN="false")
    environment.pop("DEBUG_HTTP", None)
    return environment, [v for k, v in values.items() if v and (k.endswith("_KEY") or k.endswith("_TOKEN"))]


def rpc_child(url, body):
    import urllib.error
    import urllib.request
    request = urllib.request.Request(url, data=json.dumps(body).encode(), method="POST", headers={
        "Content-Type": "application/json", "Connect-Protocol-Version": "1",
        "Authorization": "Bearer " + os.environ["BREV_API_KEY"],
    })
    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            return json.load(response)
    except urllib.error.HTTPError as error:
        return {"http_status": error.code}


def rpc(operation, body):
    environment, _ = child_environment()
    result = subprocess.run([sys.executable, __file__, "_rpc", "--operation", operation],
                            input=json.dumps(body), text=True, capture_output=True,
                            env=environment, cwd=ROOT, timeout=30)
    if result.returncode:
        raise ValueError("Read-only Brev API operation failed; no raw body displayed")
    return json.loads(result.stdout)


def current_quote():
    payload = rpc("catalog", {"options": {"includeCpu": False, "skipAccessFilter": False}})
    rows = [row for row in payload.get("items", []) if row.get("type") == "l40s-48gb.1x"
            and row.get("provider") == "crusoe" and row.get("isAvailable")]
    if not rows:
        raise ValueError("No available Crusoe L40S 48 GB in the current catalog")
    quotes = []
    for row in rows:
        base = row["basePrice"]
        if base["currency"] != "USD":
            raise ValueError("Unknown catalog currency")
        storage = row.get("supportedStorage") or []
        fixed = [item for item in storage if item.get("size") == "128GiB"]
        if len(fixed) != 1 or int(fixed[0].get("count", 1)) != 1:
            raise ValueError("The pinned plan requires one fixed 128 GiB disk")
        disk = fixed[0]["pricePerGbHr"] if "pricePerGbHr" in fixed[0] else fixed[0].get("pricePerGbHour")
        # The current public API uses pricePerGbHr; refuse unknown storage pricing.
        if not isinstance(disk, dict) or disk.get("currency") != "USD":
            raise ValueError("Cannot confirm storage price; no resource created")
        base_amount, disk_amount = Decimal(base["amount"]), Decimal(disk["amount"])
        if any(not amount.is_finite() or amount < 0 for amount in (base_amount, disk_amount)):
            raise ValueError("Invalid catalog pricing; no resource created")
        hourly = base_amount + Decimal(128) * disk_amount
        quotes.append({"location": row.get("location"), "base_per_hour": base["amount"],
                       "disk_unit_per_gb_hour": disk["amount"], "conservative_per_hour": str(hourly)})
    # Creation selects the location; reserve the largest price among available matches.
    result = max(quotes, key=lambda item: Decimal(item["conservative_per_hour"]))
    return {"observed_utc": timestamp(), "source": PUBLIC, "provider": "crusoe",
            "type": "l40s-48gb.1x", "gpu_count": 1, "vram_gb": 48, "fixed_disk_gb": 128,
            **result, "note": "Fixed disk may be included in base; conservatively counted again. Not an invoice."}


class Session:
    def __init__(self, directory):
        self.directory = Path(directory).resolve()
        if not self.directory.is_relative_to((ROOT / "artifacts").resolve()):
            raise ValueError("Run directory must be inside repository artifacts/")
        self.directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.path = self.directory / "session.json"

    def state(self):
        value = json.loads(self.path.read_text())
        if value.get("format") != "doomsday-brev-session-1" or not re.fullmatch(
                r"doomsday-run-[0-9]{8}-[0-9]{6}-[0-9a-f]{6}", value.get("name", "")):
            raise ValueError("Resource ownership ledger is invalid")
        return value

    def update(self, **changes):
        lock = self.directory / "state.lock"
        with os.fdopen(os.open(lock, os.O_RDWR | os.O_CREAT, 0o600), "r+") as stream:
            fcntl.flock(stream, fcntl.LOCK_EX)
            state = json.loads(self.path.read_text()) if self.path.exists() else {}
            state.update(changes)
            temporary = self.directory / (".state-" + uuid4().hex)
            private_write(temporary, state)
            os.replace(temporary, self.path)

    def cli(self, args, timeout=90):
        environment, hidden = child_environment()
        key = self.directory / "inference.key"
        if key.exists():
            hidden.append(key.read_text().strip())
        executable = ROOT / "backend/.venv/bin/brev"
        if not executable.is_file():
            raise ValueError("Install official Brev CLI in backend/.venv/bin/brev first")
        result = subprocess.run([str(executable), *args], env=environment, cwd=ROOT,
                                text=True, capture_output=True, timeout=timeout)
        output = result.stdout + result.stderr
        for secret in hidden:
            output = output.replace(secret, "[REDACTED]")
        private_write(self.directory / ("cli-" + uuid4().hex + ".json"),
                      {"utc": timestamp(), "operation": args[0], "exit_code": result.returncode, "output": output})
        if result.returncode:
            raise ValueError("Brev command failed; inspect private run logs")
        return output

    def inventory(self):
        return json.loads(self.cli(["ls", "--json"])).get("workspaces") or []

    def create(self, minutes, budget):
        if self.path.exists():
            raise ValueError("Existing session ledger; inspect/reuse it instead of creating again")
        if self.inventory():
            raise ValueError("Existing organization resources found; inspect them before creating another")
        if not 20 <= minutes <= 180 or not 0 < budget <= 50:
            raise ValueError("Require 20..180 minutes and a positive budget no greater than USD50")
        quote = current_quote()
        estimate = Decimal(quote["conservative_per_hour"]) * Decimal(minutes) / 60
        if estimate > budget:
            raise ValueError("GPU plus storage estimate exceeds the chosen session budget")
        name = "doomsday-run-" + datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S-") + uuid4().hex[:6]
        self.update(format="doomsday-brev-session-1", name=name, created_request_utc=timestamp(),
                    quote=quote, max_minutes=minutes, budget_usd=str(budget),
                    cleanup_epoch=time.time() + (minutes - 15) * 60, deleted=False)
        clean = os.environ.copy()
        for key in ("BREV_API_KEY", "DEBUG_HTTP"):
            clean.pop(key, None)
        log = self.directory / "watchdog.log"
        with os.fdopen(os.open(log, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), "w") as stream:
            watcher = subprocess.Popen([sys.executable, __file__, "watch", "--run-dir", str(self.directory)],
                                       env=clean, cwd=ROOT, stdout=stream, stderr=stream, start_new_session=True)
        self.update(watchdog_pid=watcher.pid)
        deadline = time.monotonic() + 5
        while not self.state().get("watchdog_ready_utc"):
            if watcher.poll() is not None or time.monotonic() >= deadline:
                self.update(deleted=True, create_not_attempted=True)
                raise ValueError("Watchdog did not confirm startup; no paid resource was created")
            time.sleep(0.05)
        if watcher.poll() is not None:
            self.update(deleted=True, create_not_attempted=True)
            raise ValueError("Watchdog exited before creation; no paid resource was created")
        try:
            self.cli(["create", name, "--type", "l40s-48gb.1x", "--provider", "crusoe",
                      "--min-disk", "128", "--stoppable", "--count", "1", "--parallel", "1",
                      "--jupyter=false", "--timeout", "600"], timeout=660)
            matches = [row for row in self.inventory() if row.get("name") == name]
            if len(matches) != 1 or not matches[0].get("id"):
                raise ValueError("Unable to resolve the new owned environment")
            self.update(environment_id=matches[0]["id"])
        except Exception:
            self.update(create_failed_utc=timestamp(), deletion_attempt_utc=timestamp())
            try:
                self.cleanup()
            except Exception:
                pass  # The confirmed watchdog continues resolving/cleaning this exact name.
            raise
        return self.state()

    def status(self):
        state = self.state()
        identifier = state.get("environment_id")
        if not identifier:
            matches = [row for row in self.inventory() if row.get("name") == state["name"]]
            if len(matches) != 1:
                return {"status": "unknown", "reason": "environment identity not resolved"}
            identifier = matches[0]["id"]
            self.update(environment_id=identifier)
        result = rpc("environment", {"environmentId": identifier, "attachedDataOptions": {"instance": True}})
        resource = result.get("environment", {})
        if resource and (resource.get("name") != state["name"] or
                         resource.get("environmentId", identifier) != identifier):
            raise ValueError("Owned environment name mismatch")
        instance = resource.get("instance") or {}
        proof = {"observed_utc": timestamp(), "target": identifier, "http_status": result.get("http_status"),
                 "environment_status": resource.get("status"),
                 "owned_name_verified": bool(resource and resource.get("name") == state["name"]),
                 "instance_lifecycle": (instance.get("status") or {}).get("lifecycleStatus")}
        if proof["instance_lifecycle"] in {"stopped", "terminated"}:
            self.update(compute_status=proof["instance_lifecycle"],
                        compute_stop_confirmed_utc=self.state().get("compute_stop_confirmed_utc") or proof["observed_utc"])
        if proof["http_status"] == 404 and self.state().get("deletion_attempt_utc"):
            matches = [row for row in self.inventory() if row.get("name") == state["name"]]
            if matches:
                raise ValueError("Environment absent by ID but named resource still exists; inspect identity")
            self.update(deleted=True, deletion_confirmed_utc=proof["observed_utc"])
            proof["environment_absent"] = True
        private_write(self.directory / ("lifecycle-" + uuid4().hex + ".json"), proof)
        return proof

    def cleanup(self):
        lock = self.directory / "cleanup.lock"
        with os.fdopen(os.open(lock, os.O_RDWR | os.O_CREAT, 0o600), "r+") as stream:
            try:
                fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                return {"status": "cleanup already running"}
            state = self.state()
            if state.get("deleted"):
                return {"deleted": True}
            # Confirm the exact name/ID before any stop or delete. An accepted
            # DELETE is never treated as a deletion proof.
            self.update(deletion_attempt_utc=state.get("deletion_attempt_utc") or timestamp())
            proof = self.status()
            if self.state().get("deleted"):
                return proof
            if not proof.get("owned_name_verified"):
                raise ValueError("Ownership not confirmed; cleanup will retry without mutating unknown resources")
            state = self.state()  # status may have resolved the environment ID.
            age = time.time() - state.get("last_delete_epoch", 0)
            retry = (not state.get("delete_accepted") and age >= 60) or age >= 480
            if (not state.get("deletion_attempt_utc") or retry) and state.get("cleanup_attempts", 0) < 3:
                self.update(deletion_attempt_utc=state.get("deletion_attempt_utc") or timestamp(),
                            last_delete_epoch=time.time(), cleanup_attempts=state.get("cleanup_attempts", 0) + 1)
                try:
                    self.cli(["stop", state["name"]])
                except (ValueError, subprocess.TimeoutExpired):
                    pass
                try:
                    self.cli(["delete", state.get("environment_id") or state["name"]])
                    self.update(delete_accepted=True)
                except (ValueError, subprocess.TimeoutExpired):
                    self.update(delete_accepted=False)
            return self.status()

    def active(self):
        state = self.state()
        if state.get("deleted") or state.get("deletion_attempt_utc") or time.time() >= state["cleanup_epoch"]:
            raise ValueError("Session is ending; no further compute actions")
        return state

    def prepare(self):
        state = self.active()
        install = ROOT / "scripts/brev/install_vllm_compat.sh"
        self.cli(["exec", state["name"], "@" + str(install)], timeout=900)
        key = self.directory / "inference.key"
        if key.is_symlink():
            raise ValueError("Inference credential must not be a symlink")
        if not key.exists():
            with os.fdopen(os.open(key, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), "w") as stream:
                stream.write(secrets.token_urlsafe(48) + "\n")
        if key.stat().st_uid != os.getuid() or key.stat().st_mode & 0o077:
            raise ValueError("Inference credential must be owned and private")
        remote = "/home/ubuntu/workspace/doomsday-brev/"
        self.cli(["copy", str(ROOT / "scripts/brev/serve_vllm_compat.sh"), state["name"] + ":" + remote + "serve_vllm_compat.sh"])
        self.cli(["copy", str(key), state["name"] + ":/home/ubuntu/.config/doomsday/inference.key"])
        self.active()
        self.cli(["exec", state["name"], "@" + str(ROOT / "scripts/brev/start_vllm.sh")])
        return {"process_started": True, "ready": "not yet checked; use forward then synthetic smoke"}

    def forward(self):
        state = self.active()
        result = subprocess.run(["ssh", "-fN", "-o", "ExitOnForwardFailure=yes",
                                 "-o", "ServerAliveInterval=30", "-o", "ServerAliveCountMax=3",
                                 "-L", "127.0.0.1:8001:127.0.0.1:8000", state["name"]],
                                cwd=ROOT, capture_output=True, text=True, timeout=30)
        if result.returncode:
            raise ValueError("SSH forward failed; no secret/SSH diagnostic displayed")
        return {"forward": "127.0.0.1:8001 -> remote 127.0.0.1:8000", "readiness_checked": False}

    def configure(self):
        self.active()
        values = {"LLM_BASE_URL": "http://127.0.0.1:8001/v1", "LLM_MODEL": MODEL,
                  "LLM_API_KEY": (self.directory / "inference.key").read_text().strip(),
                  "LLM_API_STYLE": "chat_completions", "LLM_STRUCTURED_OUTPUT": "json_schema",
                  "LLM_TIMEOUT_SECONDS": "300", "LLM_MAX_ATTEMPTS": "1",
                  "LLM_INPUT_USD_PER_MILLION": "", "LLM_OUTPUT_USD_PER_MILLION": ""}
        before = dotenv_values(ROOT / ".env", interpolate=False)
        if values["LLM_API_KEY"] == before.get("BREV_API_KEY"):
            raise ValueError("Inference credential must be separate")
        for name, value in values.items():
            set_key(ROOT / ".env", name, value, quote_mode="always")
        (ROOT / ".env").chmod(0o600)
        return {"env_updated": True, "keys_displayed": False}

    def ready(self):
        from backend.environment import load_environment
        from backend.ml_adapter import MLAdapter
        from backend.model_health import ModelReadiness
        load_environment()
        return ModelReadiness(MLAdapter()._settings).check()

    def capture(self):
        state = self.active()
        self.cli(["exec", state["name"], "@" + str(ROOT / "scripts/brev/capture_runtime.sh")])
        self.cli(["copy", state["name"] + ":/home/ubuntu/workspace/doomsday-brev/runtime-audit.tar.gz",
                  str(self.directory / "remote-runtime-audit.tar.gz")])
        (self.directory / "remote-runtime-audit.tar.gz").chmod(0o600)
        return {"runtime_saved": True}

    def tokenizer(self):
        state = self.active()
        self.cli(["copy", state["name"] + ":/home/ubuntu/.cache/doomsday/qwen3-nonthinking/" + REVISION,
                  str(self.directory / "tokenizer")], timeout=120)
        return {"tokenizer_saved": True, "model_weights_copied": False}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=["quote", "list", "create", "prepare", "forward", "configure-env", "ready",
                                           "tokenizer", "capture", "status", "delete", "watch", "_rpc"])
    parser.add_argument("--run-dir", type=Path)
    parser.add_argument("--minutes", type=int, default=60)
    parser.add_argument("--budget-usd", type=Decimal, default=Decimal("3"))
    parser.add_argument("--operation", choices=["catalog", "environment"], help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    if args.action == "_rpc":
        print(json.dumps(rpc_child(PUBLIC if args.operation == "catalog" else ENVIRONMENT, json.load(sys.stdin))))
        return 0
    if args.action == "quote":
        result = current_quote()
    else:
        if not args.run_dir:
            parser.error("--run-dir is required to keep lifecycle and credential files private")
        session = Session(args.run_dir)
        if args.action == "list":
            result = {"instances": [{k: row.get(k) for k in ("id", "name", "status")} for row in session.inventory()]}
        elif args.action == "create":
            result = session.create(args.minutes, args.budget_usd)
        elif args.action == "configure-env":
            result = session.configure()
        elif args.action == "delete":
            result = session.cleanup()
        elif args.action == "watch":
            session.update(watchdog_ready_utc=timestamp(), watchdog_heartbeat_utc=timestamp())
            while not session.state().get("deleted"):
                session.update(watchdog_heartbeat_utc=timestamp())
                if session.state().get("deletion_attempt_utc") or time.time() >= session.state()["cleanup_epoch"]:
                    try:
                        session.cleanup()
                    except Exception:
                        pass
                time.sleep(15)
            return 0
        else:
            result = getattr(session, args.action)()
    print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as error:
        print(json.dumps({"ok": False, "error_type": type(error).__name__,
                          "message": str(error) if isinstance(error, ValueError) else "Session failed; inspect private logs. No implicit create retry."}))
        raise SystemExit(2)
