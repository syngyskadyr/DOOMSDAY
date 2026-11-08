# Final backend integration — 2026-11-08

## Jury demo with saved real Qwen results (no GPU)

The urgent follow-up adds `--results`: it imports the existing official DEV
submission into SQLite and serves 64 completed assessments plus one failed record
through the existing `/api` endpoints. Startup is idempotent: the next import
skips all 65 records, preserving IDs, timestamps and history. All eight original
criterion values, rationale, evidence_turns, score and error are preserved in
`import_source.result`; display evidence links point to the real DEV messages.
Missing raw-model fields are not invented. Imported evaluations are read-only;
native model evaluations retain the existing appeal workflow.

Exact launch in the local integration worktree:

```powershell
Set-Location 'C:\Users\Человечина\AppData\Local\Programs\Microsoft VS Code\DOOMSDAY\artifacts\worktrees\final-backend'
$env:PYTHONPATH = "$PWD/src"
$env:LLM_BASE_URL = ''
$env:LLM_MODEL = ''
& '../../../.venv/Scripts/python.exe' -m backend --host 127.0.0.1 --port 8000 --dev codex_context/data/dev.json --results results/dev/results.json --db backend/data/demo.sqlite3 --no-env-file
```

Open `http://127.0.0.1:8000/`; no tunnel or model service is required for saved
results. The API is `http://127.0.0.1:8000/api`, docs at `/docs`. The actual demo
database is local/ignored: `backend/data/demo.sqlite3`. Do not run a second server
on the same port or database. The real DEV source is also local/ignored.

Verified after combining frontend commit `7adfe11` with the importer: **73 backend
tests**, **21 frontend unit tests**, frontend build, and **131 real HTTP requests
using the actual frontend api.js** all pass. The served root returns 200 and the
served API/dialogue JavaScript matches this checkout. There are no mocked HTTP
responses, invented transcripts or model POST requests in that served-app check.
All saved results and evidence links were compared with the official JSON and
the actual messages returned by FastAPI. Source/reserve SHA-256 is unchanged.

A new visual browser check was blocked by Computer Use because it could not
reliably determine the Windows browser URL. This is not reported as a browser
pass; the previously reported six frontend browser scenarios were not rerun here.

## Git and scope

Integration branch: `integration/final-backend`, based on `origin/main` at
`59d8530` (PR #8). GitHub confirms PR #3 (backend), #4 (Frontend), #6 (ML batch),
#7 (delivery/results) and #8 (batch/export compatibility) are merged.
No additional component merge was necessary. The original working tree and
`feat/ml-prompt-calibration` were left in place. This branch must be reviewed;
it is not automatically merged into main.

Changes: opt-in CORS with exact origins; `LLM_MAX_OUTPUT_TOKENS` passed through
the existing Settings/request path; official-score arithmetic checked during
`validate-results`; an explicitly skipped POSIX-only Brev test module on Windows;
regression tests and a repeatable real HTTP/SQLite smoke script.
Existing endpoints, editable checklists, evaluation history and appeal contracts
are retained. Analytics remains computed by the frontend from paginated summaries.

## Windows launch from this integration worktree

```powershell
Set-Location 'C:\Users\Человечина\AppData\Local\Programs\Microsoft VS Code\DOOMSDAY\artifacts\worktrees\final-backend'
$python = (Resolve-Path '..\..\..\.venv\Scripts\python.exe').Path
$env:PYTHONPATH = (Join-Path $PWD 'src')
$env:LLM_BASE_URL = 'http://127.0.0.1:8001/v1'
$env:LLM_MODEL = 'qwen3-14b-awq'
$env:LLM_API_STYLE = 'chat_completions'
$env:LLM_STRUCTURED_OUTPUT = 'json_schema'
$env:LLM_API_KEY = 'local' # For vLLM without auth; use the real key if auth is enabled.
$env:LLM_MAX_ATTEMPTS = '1'
$env:LLM_TIMEOUT_SECONDS = '300'
$env:LLM_MAX_OUTPUT_TOKENS = '4096'
& $python -m backend --host 127.0.0.1 --port 8000 --dev codex_context/data/dev.json --db backend/data/demo.sqlite3 --no-env-file
```

The private DEV file was copied into this worktree for local verification; it is
ignored and not committed. On another checkout, supply the real DEV file with
`--dev PATH`. Install dependencies there with `python -m pip install -r
requirements-backend.txt`; use that checkout's Python and `PYTHONPATH=src`.
Always run one backend process/worker against a given SQLite file.

Alternatively copy `backend/env.example` into the checkout's `.env`, fill the
same values, and omit `--no-env-file`. `create_app()` loads that exact file before
constructing the service; process environment wins. `MLAdapter._settings()` calls
`Settings.from_env()`, and `evaluate_dialogue(..., settings=settings)` receives it.
The ML library does not read `.env` itself. Never commit credentials.

In another PowerShell terminal, use the **existing** Brev SSH configuration:

```powershell
$BrevHost = 'REPLACE_WITH_EXISTING_BREV_SSH_ALIAS'
ssh -N -o ExitOnForwardFailure=yes -o ServerAliveInterval=30 -L 127.0.0.1:8001:127.0.0.1:8000 $BrevHost
```

Do not launch another vLLM or batch. Keep this tunnel terminal running. The
placeholder is intentional: this session did not discover or invent an SSH
hostname/key. The tunnel forwards remote loopback; no public model port is needed.
The server context window is 20480. The output cap includes thinking; input plus
output must still fit that window. Backend evaluation does not implement the
batch tokenizer preflight: a too-long request becomes a recorded provider error.

URLs: UI `http://127.0.0.1:8000/`, API `http://127.0.0.1:8000/api`,
OpenAPI `http://127.0.0.1:8000/docs`.
`/api/health` checks configuration only. `/api/model-health` uses GET probes and
does not generate tokens. While the GPU batch is running, do not click Evaluate.
No new inference or SSH/Brev lifecycle operation was performed in this integration.

## Frontend connection

The committed frontend already uses `/api/...`; opening the backend root needs
no frontend changes. For a separate dev server, proxy `/api` to
`http://127.0.0.1:8000`, preserving the path. Alternatively set an absolute API
base in the frontend and configure before backend startup:

```powershell
$env:DOOMSDAY_CORS_ORIGINS = 'http://127.0.0.1:5173,http://localhost:5173'
```

CORS alone does not redirect relative frontend URLs. Origins are exact; no
wildcard or cross-origin credentials. CORS headers also cover safe 500 responses.
POST `/api/conversations/{id}/evaluations` takes `{"checklist_version":"1"}` and
returns 202. Poll `/api/evaluations/{evaluation_id}` until terminal status.
Use `final.assessment.criteria` (`criterion_id`, `reason`, `evidence_message_ids`,
`checked_range`) and `final.calculation.total_score`, plus `final.critical_error`.
Internal IDs `01`…`08` are deliberately different from official export codes.
The existing frontend already implements this mapping and handles revisions for
appeals. See `codex_context/team/HTTP_API_CONTRACT.md`.

## Verification and boundaries

Observed: backend **68/68 passed**; ML/export discovery **117 passed, 1 POSIX-only
module skipped**; frontend **18/18 passed**. After adding the arithmetic regression,
the exporter module was rerun: **25/25 passed**. Real HTTP smoke passed with
`evaluation_source=synthetic_fixture`, `remote_model_calls=0`.

```powershell
& $python -m unittest discover -s backend/tests
& $python -m unittest discover -s tests
node --test frontend/tests/*.test.js
& $python scripts/smoke_backend.py --dev codex_context/data/dev.json --synthetic
& $python -m cqa_eval validate-results --input codex_context/data/dev.json --dataset dev --response results/dev/results.json
```

The smoke starts an actual Uvicorn TCP listener on an ephemeral loopback port,
imports all 65 real DEV records into temporary SQLite, verifies all messages,
filters (60 chats / 5 calls), duplicate import, static frontend, CORS, evaluation,
appeal confirmation and persistence after restarting the server.
`--synthetic` explicitly uses the repository's synthetic dialogue/assessment at
the provider transport boundary. `MLAdapter`, evaluator parsing, validation,
scoring, HTTP and SQLite are real. No real Qwen generation is claimed.
The saved real official submission is validated independently (64 ok / 1 error).
Its lossy export cannot reconstruct original checked ranges or violation objects.
Private original inference journals were not available locally.

When a saved real `cqa_eval` result becomes available, run the same smoke with
`--saved-result PATH/records/0001/attempt-0001/result.json` instead of `--synthetic`.
This replays its unchanged assessment through the evaluator into a temporary DB,
still with zero remote model calls and explicit transport mocking. It does not
verify the authenticity of raw inference. Neither mode writes submission files.

Windows skips `test_brev_session`: the cloud lifecycle script requires POSIX
`fcntl` and should be tested on Linux. Do not substitute a fake Windows lock.
Starlette reports an httpx deprecation warning; the pinned existing dependencies
remain unchanged.

## Safe DEV delivery

The current tracked DEV is already valid, with 65 unique IDs, 64 ok and error
`p1-ru-023`. Keep it as the fallback until a replacement has passed validation.
No PROD file is created before the real PROD dataset/results exist.
Immutable local reserve:
`../../../artifacts/submission-reserve/dev-64ok-1error-20261108T042525011732Z/`.
Reserve `results/dev/results.json` SHA-256 (CRLF working copy):
`5c31ffbbe660c29a37d27772baabb7adaf3cb718a839043b347361633ea36fe4`.
Committed LF blob SHA-256:
`a6f19e245208c599165217d5f4ce64553ceec043e5ed2737b27d677ee7974611`.
Both encodings represent the same JSON; neither reserve file was modified.

After the existing batch finishes, copy its complete durable run into a NEW
local artifact directory. Do not collect/export a live run or remove its lock.
Set the actual source path below. Every native-command failure stops this flow:

```powershell
function Assert-Success { if ($LASTEXITCODE -ne 0) { throw "Previous command failed: $LASTEXITCODE" } }
$DevRun = (Resolve-Path 'artifacts/cqa/COMPLETED-DEV-RUN').Path
$ServedModel = (Get-Content -LiteralPath (Join-Path $DevRun 'manifest.json') -Raw | ConvertFrom-Json).model
$stamp = Get-Date -Format 'yyyyMMddTHHmmssfff'
$candidate = "artifacts/submission-candidates/dev-$stamp.json"
& $python -m cqa_eval export-results --dataset dev --input codex_context/data/dev.json --expected-count 65 --run-dir $DevRun --team-name DOOMSDAY --model-name Qwen/Qwen3-14B-AWQ --served-model-name $ServedModel --model-mode local --out $candidate
Assert-Success
& $python -m cqa_eval validate-results --dataset dev --input codex_context/data/dev.json --response $candidate
Assert-Success
# Review ok/error counts and provenance before this explicit promotion.
$backup = "artifacts/submission-candidates/dev-before-$stamp.json"
if (Test-Path -LiteralPath $backup) { throw 'Backup already exists' }
Copy-Item -LiteralPath results/dev/results.json -Destination $backup
if ((Get-FileHash $backup).Hash -ne (Get-FileHash results/dev/results.json).Hash) { throw 'Backup mismatch' }
Copy-Item -LiteralPath $candidate -Destination results/dev/results.json
& $python -m cqa_eval validate-results --dataset dev --input codex_context/data/dev.json --response results/dev/results.json
Assert-Success
if ((git branch --show-current) -ne 'integration/final-backend') { throw 'Wrong branch' }
git add -- results/dev/results.json
Assert-Success
git diff --cached --stat
git commit -m 'Update DEV submission from completed real batch'
Assert-Success
git push origin integration/final-backend
Assert-Success
```

If the completed run has errors, export from `--run-dir`; `batch-collect` only
accepts complete successes. Do not fabricate successes or use `--allow-unprocessed`
to disguise an unfinished run. A complete collected package may instead be passed
with `--collected FILE` (mutually exclusive with `--run-dir`). See `SUBMISSION.md`.

## PROD delivery, only after its release and real evaluation

Use the released PROD envelope and completed PROD run; the exporter requires
exactly 10 unique chat IDs and refuses an existing destination.

```powershell
$ProdInput = (Resolve-Path 'codex_context/data/prod.json').Path
$ProdRun = (Resolve-Path 'artifacts/cqa/COMPLETED-PROD-RUN').Path
$ServedModel = (Get-Content -LiteralPath (Join-Path $ProdRun 'manifest.json') -Raw | ConvertFrom-Json).model
if (Test-Path -LiteralPath results/prod/results.json) { throw 'PROD already exists; preserve and review it first' }
& $python -m cqa_eval export-results --dataset prod --input $ProdInput --expected-count 10 --run-dir $ProdRun --team-name DOOMSDAY --model-name Qwen/Qwen3-14B-AWQ --served-model-name $ServedModel --model-mode local --out results/prod/results.json
Assert-Success
& $python -m cqa_eval validate-results --dataset prod --input $ProdInput --response results/prod/results.json
Assert-Success
if ((git branch --show-current) -ne 'integration/final-backend') { throw 'Wrong branch' }
git add -f -- results/prod/results.json
Assert-Success
git diff --cached --stat
git commit -m 'Add validated real PROD submission'
Assert-Success
git push origin integration/final-backend
Assert-Success
```

Review the PR and merge manually before the organizer deadline if they read main.
A push to an integration branch is not an automatic main submission. Never stage
the private source data, SQLite, `.env`, raw responses or reserve archive.
