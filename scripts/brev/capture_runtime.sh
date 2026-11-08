#!/usr/bin/env bash
set +x
set -euo pipefail
umask 077
work="$HOME/workspace/doomsday-brev"
"$work/venv/bin/python" -m pip freeze >"$work/requirements.actual.txt"
"$work/venv/bin/python" - <<'PY'
from datetime import datetime, timezone
from importlib import metadata
import json, platform, subprocess, tarfile
from pathlib import Path
work = Path.home()/'workspace/doomsday-brev'
tokenizer = Path.home()/'.cache/doomsday/qwen3-nonthinking/31c69efc29464b6bb0aee1398b5a7b50a99340c3'
record = {'captured_at': datetime.now(timezone.utc).isoformat(), 'python': platform.python_version(),
          'packages': {name: metadata.version(name) for name in ('vllm','torch','transformers','huggingface-hub','tokenizers','jinja2')},
          'gpu': subprocess.check_output(['nvidia-smi','--query-gpu=name,driver_version,memory.total','--format=csv,noheader'], text=True).strip(),
          'serving': json.loads((tokenizer/'doomsday_serving.json').read_text()),
          'context_window':32768, 'max_new_tokens':4096}
(work/'runtime-audit.json').write_text(json.dumps(record, indent=2)+'\n')
with tarfile.open(work/'runtime-audit.tar.gz', 'w:gz') as archive:
    for name in ('runtime-audit.json','requirements.actual.txt','serve_vllm_compat.sh','vllm.log'):
        archive.add(work/name, arcname=name, recursive=False)
print('Saved runtime audit without weights or credentials.')
PY
