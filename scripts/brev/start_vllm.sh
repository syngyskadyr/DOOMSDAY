#!/usr/bin/env bash
set +x
set -euo pipefail
umask 077
work="$HOME/workspace/doomsday-brev"
if [[ -f "$work/vllm.pid" ]] && kill -0 "$(cat "$work/vllm.pid")" 2>/dev/null; then
  printf '%s\n' 'Existing vLLM process; no second process started.'
  exit 0
fi
python3 - <<'PY'
import socket
with socket.socket() as probe:
    probe.bind(('127.0.0.1', 8000))
PY
nohup bash "$work/serve_vllm_compat.sh" --remote-brev "$work/venv/bin/python" \
  >"$work/vllm.log" 2>&1 </dev/null &
printf '%s\n' "$!" >"$work/vllm.pid"
sleep 2
kill -0 "$(cat "$work/vllm.pid")"
printf '%s\n' 'vLLM process started; loading is not readiness.'
