"""Browser tests only: isolated SQLite and explicitly synthetic saved assessment.

Never used by dev/preview or by the jury demo. No provider or GPU requests.
"""
from contextlib import asynccontextmanager
from pathlib import Path
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT), str(ROOT / "src")]

import uvicorn
from backend.app import create_app
from backend.tests.test_api import SyntheticAdapter, record


class BrowserAdapter(SyntheticAdapter):
    def evaluate(self, dialogue, checklist):
        result = super().evaluate(dialogue, checklist)
        row = next(r for r in result["assessment"]["criteria"] if r["criterion_id"] == "02")
        row.update(answer="na", na_reason="Синтетическая проверка N/A: запрос уже сформулирован.")
        return result


with tempfile.TemporaryDirectory(prefix="doomsday-ui-test-") as directory:
    adapter = BrowserAdapter()
    app = create_app(db_path=Path(directory) / "test.sqlite3", dev_path=None,
                     adapter=adapter, load_env_file=False, frontend_path=ROOT / "frontend/dist")
    original_lifespan = app.router.lifespan_context

    @asynccontextmanager
    async def lifespan(application):
        async with original_lifespan(application):
            service = application.state.service
            service.import_conversations(record("synthetic-browser-001"))
            job = service.queue_evaluation("synthetic-browser-001", "1")
            service.run_evaluation(job["evaluation_id"])
            service.import_conversations(record("synthetic-critical-002"))
            adapter.flag_answers["synthetic-critical-002"] = "yes"
            critical = service.queue_evaluation("synthetic-critical-002", "1")
            service.run_evaluation(critical["evaluation_id"])
            adapter.available = False
            yield

    app.router.lifespan_context = lifespan
    uvicorn.run(app, host="127.0.0.1", port=8012, access_log=False)
