#!/usr/bin/env bash
# Remote Brev only. Never installs weights or GPU packages locally.
set +x
set -euo pipefail
umask 077
export DO_NOT_TRACK=1 VLLM_NO_USAGE_STATS=1 HF_HUB_DISABLE_TELEMETRY=1
unset DEBUG_HTTP
work="$HOME/workspace/doomsday-brev"
mkdir -p "$work" "$HOME/.config/doomsday"
chmod 700 "$work" "$HOME/.config/doomsday"
sudo -n apt-get update -qq
sudo -n apt-get install -y --no-install-recommends python3-venv python3-dev
python3 -c 'from pathlib import Path; import sysconfig; assert (Path(sysconfig.get_paths()["include"])/"Python.h").is_file()'
python3 -m venv "$work/venv"
"$work/venv/bin/python" -m pip install --no-cache-dir --only-binary=vllm,torch \
  'vllm==0.8.5.post1' 'transformers==4.51.3' 'huggingface-hub==0.30.2'
"$work/venv/bin/python" -m pip check
