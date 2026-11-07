#!/usr/bin/env bash
# Remote-only compatibility launcher for the Brev driver actually observed.
# No provisioning, package installation, or driver changes.
set +x
set -euo pipefail
umask 077
if [[ $# -lt 2 || $# -gt 3 || "$1" != "--remote-brev" ]]; then
  printf '%s\n' 'On approved Brev only: bash serve_vllm_compat.sh --remote-brev /path/to/venv/bin/python [INFERENCE_KEY_FILE]' >&2
  exit 2
fi
exec "$2" - "${3:-${HOME}/.config/doomsday/inference.key}" <<'PY'
import csv
import hashlib
import importlib.metadata
import json
import os
from pathlib import Path
import shutil
import stat
import subprocess
import sys

MODEL = "Qwen/Qwen3-14B-AWQ"
REVISION = "31c69efc29464b6bb0aee1398b5a7b50a99340c3"


def fail(message):
    print(message, file=sys.stderr)
    raise SystemExit(2)


if sys.platform != "linux":
    fail("Run only on the approved Linux Brev GPU server.")
try:
    if importlib.metadata.version("vllm") != "0.8.5.post1":
        fail("Compatibility launcher requires exactly vLLM 0.8.5.post1.")
    if importlib.metadata.version("transformers") != "4.51.3":
        fail("Compatibility launcher requires transformers 4.51.3.")
except importlib.metadata.PackageNotFoundError:
    fail("Prepare the separate remote inference environment first; no packages were installed.")
smi = shutil.which("nvidia-smi")
if not smi:
    fail("No NVIDIA driver detected; do not run locally.")
try:
    result = subprocess.run(
        [smi, "--query-gpu=driver_version,name,memory.total", "--format=csv,noheader,nounits"],
        check=True, capture_output=True, text=True, timeout=15,
    )
    devices = list(csv.reader(result.stdout.strip().splitlines()))
    if len(devices) != 1 or len(devices[0]) != 3:
        fail("Review the remote GPU inventory: this candidate requires one L40S 48 GB.")
    driver, name, memory = [part.strip() for part in devices[0]]
    if tuple(int(part) for part in driver.split(".")) < (550, 54, 15):
        fail("CUDA 12.4.1 driver gate failed: require Linux driver >=550.54.15; no driver changes made.")
    if "L40S" not in name or int(float(memory)) < 45000:
        fail("This candidate is prepared for one L40S 48 GB only.")
except (OSError, ValueError, subprocess.SubprocessError):
    fail("Unable to verify remote GPU/driver; no model was started.")

for variable in tuple(os.environ):
    if variable.startswith("BREV_") or variable in {"DEBUG_HTTP", "LLM_API_KEY", "VLLM_API_KEY"}:
        os.environ.pop(variable, None)
os.environ.update({
    "DO_NOT_TRACK": "1", "VLLM_NO_USAGE_STATS": "1", "VLLM_DO_NOT_TRACK": "1",
    "HF_HUB_DISABLE_TELEMETRY": "1", "TOKENIZERS_PARALLELISM": "false",
    "VLLM_LOGGING_LEVEL": "INFO", "VLLM_USE_V1": "0",
})
key_file = Path(sys.argv[1]).expanduser()
try:
    metadata = key_file.lstat()
    if not stat.S_ISREG(metadata.st_mode) or metadata.st_uid != os.getuid() or metadata.st_mode & 0o077:
        fail("Inference key must be an owned regular file with mode 0600 or stricter.")
except OSError:
    fail("Separate inference key file is missing or inaccessible.")

import torch
if torch.__version__.split("+")[0] != "2.6.0" or torch.version.cuda != "12.4":
    fail("Require the pinned torch 2.6.0 CUDA 12.4 build; inspect the remote environment.")
if not torch.cuda.is_available():
    fail("Remote torch cannot access the GPU; no model was started.")

# Tokenizer downloads only, on the remote GPU host. Model weights are resolved
# by vLLM below. The custom template is shared by serving and batch preflight.
from transformers import AutoTokenizer
tokenizer = AutoTokenizer.from_pretrained(MODEL, revision=REVISION, trust_remote_code=False)
if not isinstance(tokenizer.chat_template, str) or "enable_thinking" not in tokenizer.chat_template:
    fail("Pinned tokenizer has an unexpected chat template; review it before serving.")
template = "{%- set enable_thinking = false -%}\n" + tokenizer.chat_template
tokenizer.chat_template = template
probe_messages = [{"role": "user", "content": "Return a JSON object."}]
rendered = tokenizer.apply_chat_template(probe_messages, tokenize=False, add_generation_prompt=True)
if not rendered.rstrip().endswith("<think>\n\n</think>"):
    fail("Non-thinking template did not produce the expected closed thinking prefix.")
directory = Path.home() / ".cache/doomsday/qwen3-nonthinking" / REVISION
template_file = directory / "chat_template.jinja"
if directory.exists():
    if not template_file.is_file() or template_file.read_text(encoding="utf-8") != template:
        fail("Existing tokenizer directory differs; preserve it and review manually.")
else:
    directory.mkdir(parents=True, mode=0o700)
    tokenizer.save_pretrained(directory)
    template_file.write_text(template, encoding="utf-8")
    (directory / "doomsday_serving.json").write_text(json.dumps({
        "model": MODEL, "revision": REVISION, "vllm": "0.8.5.post1",
        "transformers": "4.51.3", "thinking": False,
        "template_sha256": hashlib.sha256(template.encode()).hexdigest(),
    }, indent=2) + "\n", encoding="utf-8")
try:
    key = key_file.read_text(encoding="utf-8").strip()
except (OSError, UnicodeError):
    fail("Unable to read the separate inference key; contents not displayed.")
if len(key) < 32 or any(character.isspace() for character in key):
    fail("Use a separately generated random inference key, at least 32 characters without whitespace.")
environment = os.environ.copy()
environment["VLLM_API_KEY"] = key
command = [
    sys.executable, "-m", "vllm.entrypoints.openai.api_server",
    "--model", MODEL, "--revision", REVISION,
    "--tokenizer", str(directory), "--chat-template", str(template_file),
    "--served-model-name", "doomsday-qwen3-14b-awq",
    "--host", "127.0.0.1", "--port", "8000", "--dtype", "half",
    "--max-model-len", "32768", "--max-num-seqs", "1",
    "--gpu-memory-utilization", "0.90", "--enforce-eager",
    "--generation-config", "vllm",
    "--override-generation-config", '{"max_new_tokens":4096,"temperature":0.7,"top_p":0.8,"top_k":20,"min_p":0.0}',
    "--guided-decoding-backend", "xgrammar",
    "--disable-log-requests", "--disable-uvicorn-access-log",
]
print("Prepared pinned non-thinking tokenizer for serving and preflight: " + str(directory), flush=True)
os.execve(sys.executable, command, environment)
PY
