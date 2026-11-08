"""HTTP API v1. The app factory permits explicit test dependencies only."""
from concurrent.futures import ThreadPoolExecutor
from contextlib import asynccontextmanager
import logging
import os
from pathlib import Path
from typing import Literal

from fastapi import FastAPI, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.middleware.cors import CORSMiddleware
from starlette.exceptions import HTTPException
from starlette.staticfiles import StaticFiles

from .environment import load_environment
from .importing import InvalidJSON, ImportConflictError, ImportValidationError, parse_json
from .ml_adapter import AdapterUnavailable, AdapterValidationError, MLAdapter
from .model_health import ModelReadiness
from . import openapi
from .service import APIError, Service, invalid
from .store import Store

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_DEV = ROOT / "codex_context/data/dev.json"
DEFAULT_CHECKLIST = ROOT / "codex_context/checklist.json"
logger = logging.getLogger("doomsday.backend")


class FrontendDirectoryError(ValueError):
    """Safe startup failure for an explicitly requested public directory."""


def error_response(status, code, message, details=None):
    return JSONResponse(status_code=status, content={"error": {"code": code, "message": message, "details": details}})


async def json_body(request):
    try:
        return parse_json(await request.body())
    except InvalidJSON:
        raise APIError(400, "INVALID_JSON", "Тело запроса должно содержать корректный JSON без повторных ключей и неконечных чисел.") from None


async def object_body(request, keys):
    body = await json_body(request)
    if not isinstance(body, dict) or set(body) != set(keys):
        invalid("Обязательные поля запроса: " + ", ".join(keys))
    return body


def string_field(body, key):
    value = body[key]
    if not isinstance(value, str) or not value.strip():
        invalid(f"{key} должен быть непустой строкой")
    return value


def revision_field(body):
    value = body["expected_revision"]
    if type(value) is not int or value < 0:
        invalid("expected_revision должен быть неотрицательным целым числом")
    return value


def create_app(*, db_path=None, dev_path=DEFAULT_DEV, checklist_path=DEFAULT_CHECKLIST, adapter=None, frontend_path=None,
               env_file=None, load_env_file=True, model_probe=None):
    load_environment(env_file, enabled=load_env_file)
    adapter = adapter if adapter is not None else MLAdapter()
    model_probe = model_probe if model_probe is not None else ModelReadiness(adapter._settings)
    db_path = db_path if db_path is not None else os.environ.get("DOOMSDAY_DB_PATH", ROOT / "backend/data/doomsday.sqlite3")
    public_path = Path(frontend_path) if frontend_path is not None else ROOT / "frontend/public"
    if frontend_path is not None and not public_path.is_dir():
        raise FrontendDirectoryError("Указанный публичный каталог frontend не найден или не является каталогом.")

    @asynccontextmanager
    async def lifespan(app):
        store = Store(db_path)
        service = Service(store, adapter)
        app.state.store, app.state.service = store, service
        service.seed_checklist(parse_json(Path(checklist_path).read_bytes()))
        store.recover()
        if dev_path is not None:
            path = Path(dev_path)
            if path.is_file():
                imported = service.import_conversations(parse_json(path.read_bytes()))
                logger.info("DEV import: imported=%s skipped=%s", imported["imported"], imported["skipped"])
            else:
                logger.warning("DEV file absent; import JSON with POST /api/conversations/import.")
        executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="cqa-evaluation")
        app.state.executor = executor
        try:
            yield
        finally:
            # Do not silently cancel an in-flight paid request. Queued jobs that
            # never start are marked SERVER_RESTARTED at the next startup.
            executor.shutdown(wait=True, cancel_futures=True)

    app = FastAPI(title="DOOMSDAY CQA Backend", version="1", lifespan=lifespan)
    # Same-origin frontend needs no CORS. Separate local dev servers can opt in
    # with exact origins; never expose this unauthenticated API to every origin.
    origins = [origin.strip() for origin in os.environ.get("DOOMSDAY_CORS_ORIGINS", "").split(",")
               if origin.strip()]
    if "*" in origins:
        raise ValueError("DOOMSDAY_CORS_ORIGINS requires explicit origins, not '*'")

    @app.exception_handler(APIError)
    async def api_error(request, exc):
        return error_response(exc.status, exc.code, exc.message, exc.details)

    @app.exception_handler(ImportValidationError)
    async def import_validation(request, exc):
        return error_response(422, exc.code, exc.message, exc.details)

    @app.exception_handler(ImportConflictError)
    async def import_conflict(request, exc):
        return error_response(409, exc.code, exc.message, exc.details)

    @app.exception_handler(AdapterValidationError)
    async def adapter_validation(request, exc):
        return error_response(422, exc.code, exc.message, exc.details)

    @app.exception_handler(AdapterUnavailable)
    async def adapter_unavailable(request, exc):
        return error_response(503, exc.code, exc.message)

    @app.exception_handler(RequestValidationError)
    async def request_validation(request, exc):
        details = [{"field": ".".join(str(p) for p in err["loc"]), "message": "Некорректное значение параметра"} for err in exc.errors()]
        return error_response(422, "VALIDATION_ERROR", "Проверьте параметры запроса", details)

    @app.exception_handler(HTTPException)
    async def http_error(request, exc):
        return error_response(exc.status_code, "NOT_FOUND" if exc.status_code == 404 else "HTTP_ERROR",
                              "Ресурс не найден" if exc.status_code == 404 else "HTTP-запрос не поддерживается")

    @app.middleware("http")
    async def safe_errors(request, call_next):
        try:
            return await call_next(request)
        except Exception as exc:
            # Catch before ServerErrorMiddleware: otherwise it re-raises after
            # responding and Uvicorn logs arbitrary exception text/URLs.
            logger.error("Backend operation failed (%s)", type(exc).__name__)
            return error_response(500, "INTERNAL_ERROR", "Внутренняя ошибка сервера")

    @app.get("/api/health")
    def health():
        ready, message = adapter.availability()
        return {"status": "ok", "api_version": "1", "evaluator_status": "ready" if ready else "unavailable",
                "evaluator_message": message}

    @app.get("/api/model-health")
    def model_health():
        """Observe local vLLM without generation; independent of configuration health."""
        return JSONResponse(model_probe.check(), headers={"Cache-Control": "no-store"})

    @app.post("/api/conversations/import", openapi_extra=openapi.IMPORT)
    async def import_conversations(request: Request):
        return app.state.service.import_conversations(await json_body(request))

    @app.get("/api/conversations")
    def conversations(language: str | None = None, channel: str | None = None,
                      status: Literal["queued", "running", "completed", "needs_review", "failed", "not_evaluated"] | None = None,
                      critical_error: Literal["true", "false", "unknown"] | None = None,
                      limit: int = Query(100, ge=1, le=200), offset: int = Query(0, ge=0)):
        return app.state.service.conversations(language=language, channel=channel, status=status,
                                               critical_error=critical_error, limit=limit, offset=offset)

    @app.get("/api/checklists/current")
    def current_checklist():
        return app.state.service.checklist()

    @app.get("/api/checklists")
    def checklists():
        return app.state.service.checklists()

    @app.get("/api/checklists/{version:path}")
    def checklist(version: str):
        return app.state.service.checklist(version)

    @app.post("/api/checklists", status_code=201, openapi_extra=openapi.CHECKLIST)
    async def save_checklist(request: Request):
        body = await object_body(request, ("base_version", "checklist"))
        return app.state.service.save_checklist(string_field(body, "base_version"), body["checklist"])

    @app.post("/api/conversations/{conversation_id:path}/evaluations", status_code=202, openapi_extra=openapi.EVALUATION)
    async def evaluate(conversation_id: str, request: Request):
        body = await object_body(request, ("checklist_version",))
        document = app.state.service.queue_evaluation(conversation_id, string_field(body, "checklist_version"))
        app.state.executor.submit(app.state.service.run_evaluation, document["evaluation_id"])
        return document

    @app.get("/api/conversations/{conversation_id:path}")
    def conversation(conversation_id: str):
        return app.state.service.conversation(conversation_id)

    @app.get("/api/evaluations/{evaluation_id}")
    def evaluation(evaluation_id: str):
        return app.state.service.evaluation(evaluation_id)

    @app.get("/api/appeals")
    def appeals(status: Literal["open", "resolved", "all"] = "open"):
        return app.state.service.appeals(status)

    @app.post("/api/evaluations/{evaluation_id}/appeals", status_code=201, openapi_extra=openapi.APPEAL)
    async def create_appeal(evaluation_id: str, request: Request):
        body = await object_body(request, ("criterion_id", "comment", "expected_revision"))
        return app.state.service.create_appeal(evaluation_id, string_field(body, "criterion_id"),
                                               string_field(body, "comment"), revision_field(body))

    @app.post("/api/appeals/{appeal_id}/resolve", openapi_extra=openapi.RESOLUTION)
    async def resolve_appeal(appeal_id: str, request: Request):
        body = await object_body(request, ("decision", "reason", "replacement", "expected_revision"))
        decision = string_field(body, "decision")
        if decision not in ("confirm", "change"):
            invalid("decision должен быть confirm или change")
        return app.state.service.resolve_appeal(appeal_id, decision, string_field(body, "reason"),
                                                body["replacement"], revision_field(body))

    # Only explicitly public build output is served, never repository/context.
    if public_path.is_dir():
        app.mount("/", StaticFiles(directory=public_path, html=True), name="frontend")
    # Outermost user middleware also decorates safe_errors responses.
    if origins:
        app.add_middleware(CORSMiddleware, allow_origins=origins,
                           allow_methods=["GET", "POST"],
                           allow_headers=["Accept", "Content-Type"], allow_credentials=False)
    return app
