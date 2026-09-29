"""One local webpage over the existing W1 PostgreSQL service.

The verified fixed wheel and explicit Mock reader share the existing pipeline.
The Responses client is callable only through an explicitly authorized binding.
Manual source transcription is explicitly identified and never called parsing.
"""
from __future__ import annotations

from contextlib import asynccontextmanager
from hashlib import sha256
import logging
import os
from pathlib import Path
from uuid import uuid4
from typing import Literal

from fastapi import BackgroundTasks, FastAPI, File, HTTPException, Request, UploadFile
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict, Field, StrictBool, StrictInt
from starlette.middleware.trustedhost import TrustedHostMiddleware

from sanzo_core.demo_store import DemoStore
from sanzo_core.domain import ConflictError, CorrectionCommand, Principal, RevokeCommand, ScopeError
from sanzo_core.postgres import PostgresRepository
from sanzo_core.service import ReviewService
from .files import MAX_UPLOAD, excel_preview, original_path, validate_original
from .export import export_review_state, workbook_bytes
from .comparison import comparison_projection
from .row_confirmation import (RowConfirmationService, RowConfirmationCommand, RowMember,
    SingleMemberCorrectionService, SingleMemberCorrectionCommand)
from .pdf_preview import pdf_html, pdf_page_png
from .ingestion import IntegrationRuntime

STATIC = Path(__file__).parent / "static"


class DTO(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class CaseBody(DTO):
    name: str = Field(min_length=1, max_length=1000)


class CorrectionBody(DTO):
    expected_case_revision: StrictInt = Field(ge=1)
    expected_item_version: StrictInt = Field(ge=1)
    expected_memory_version: StrictInt = Field(ge=0)
    idempotency_key: str = Field(min_length=1, max_length=200)
    target_fee_id: str | None = Field(default=None, min_length=1, max_length=200)
    new_fee_name: str | None = Field(default=None, min_length=1, max_length=1024)
    unit_price: str | None = Field(default=None, max_length=64)
    replace_existing_mapping: StrictBool = False


class MemoryEvidenceBody(DTO):
    memory_key: str = Field(min_length=1, max_length=200)
    version: StrictInt = Field(ge=1)
    status: Literal["ACTIVE", "CONFLICTED", "REVOKED"]
    fee_id: str | None = Field(min_length=1, max_length=200)
    evidence_event_id: str = Field(min_length=1, max_length=200)


class RowMemberBody(DTO):
    item_id: str = Field(min_length=1, max_length=200)
    expected_item_version: StrictInt = Field(ge=1)
    expected_memory_version: StrictInt = Field(ge=0)
    expected_memory_evidence: list[MemoryEvidenceBody] | None = Field(default=None, max_length=1000)

class RowConfirmationBody(DTO):
    intent: Literal["CONFIRM_MEMBERS", "RENAME_FEE", "RECLASSIFY_MEMBERS"] = "CONFIRM_MEMBERS"
    expected_fee_id: str | None = Field(default=None, min_length=1, max_length=200)
    expected_name_version: StrictInt | None = Field(default=None, ge=1)
    expected_case_revision: StrictInt = Field(ge=1)
    members: list[RowMemberBody] = Field(min_length=1, max_length=100)
    idempotency_key: str = Field(min_length=1, max_length=200)
    target_fee_id: str | None = Field(default=None, min_length=1, max_length=200)
    new_fee_name: str | None = Field(default=None, min_length=1, max_length=1024)
    replace_existing_mapping: StrictBool = False


class SingleMemberCorrectionBody(DTO):
    expected_case_revision: StrictInt = Field(ge=1)
    expected_item_version: StrictInt = Field(ge=1)
    expected_memory_version: StrictInt = Field(ge=0)
    expected_memory_evidence: list[MemoryEvidenceBody] | None = Field(default=None, max_length=1000)
    idempotency_key: str = Field(min_length=1, max_length=200)
    target_fee_id: str | None = Field(default=None, min_length=1, max_length=200)
    new_fee_name: str | None = Field(default=None, min_length=1, max_length=1024)
    expected_fee_id: str | None = Field(default=None, min_length=1, max_length=200)
    expected_name_version: StrictInt | None = Field(default=None, ge=1)
    replace_existing_mapping: StrictBool = False


class ConditionCorrectionBody(DTO):
    expected_case_revision: StrictInt = Field(ge=1)
    expected_item_version: StrictInt = Field(ge=1)
    expected_memory_version: StrictInt = Field(ge=0)
    expected_memory_evidence: list[MemoryEvidenceBody] | None = Field(default=None, max_length=1000)
    idempotency_key: str = Field(min_length=1, max_length=200)
    field: Literal["equipment", "billing_unit"]
    value: str | None = Field(..., max_length=1024)


class ProcessBody(DTO):
    idempotency_key: str = Field(min_length=1, max_length=200)
    field_bindings: dict[str, str] = Field(default_factory=dict, max_length=2)
    pause_ai: StrictBool = False


class RevokeBody(DTO):
    expected_case_revision: StrictInt = Field(ge=1)
    expected_item_version: StrictInt = Field(ge=1)
    expected_memory_version: StrictInt = Field(ge=0)
    idempotency_key: str = Field(min_length=1, max_length=200)
    reason: str = Field(min_length=1, max_length=2000)


def public_document(row):
    return {k: v for k, v in row.items() if k not in {"storage_key", "tenant_id"}}


def create_app(dsn: str, storage_dir: str | Path, *, tenant_id: str = "local-demo",
               integrations=None, debug_manual: bool = False) -> FastAPI:
    repository = PostgresRepository(dsn)
    service, store = ReviewService(repository), DemoStore(repository)
    actor = Principal(tenant_id, "local-demo-operator")
    storage = Path(storage_dir)
    pipeline = integrations or IntegrationRuntime(repository, storage)

    @asynccontextmanager
    async def lifespan(app):
        storage.mkdir(parents=True, exist_ok=True, mode=0o700)
        repository.migrate()
        pipeline.store.interrupt_unfinished(actor)
        yield

    app = FastAPI(title="SANZO 原価単価一覧表 Demo", lifespan=lifespan,
                  docs_url=None, redoc_url=None, openapi_url=None)
    app.state.repository = repository
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=["127.0.0.1", "localhost", "testserver"])

    @app.middleware("http")
    async def local_boundary(request: Request, call_next):
        if request.method not in {"GET", "HEAD", "OPTIONS"}:
            origin = request.headers.get("origin")
            expected = str(request.base_url).rstrip("/")
            if (origin is not None and origin != expected) or request.headers.get("sec-fetch-site") == "cross-site":
                return JSONResponse({"detail": "同じローカル画面から操作してください"}, status_code=403)
            length = request.headers.get("content-length")
            if length and (not length.isdigit() or int(length) > MAX_UPLOAD + 1024 * 1024):
                return JSONResponse({"detail": "リクエストのサイズ上限を超えています"}, status_code=413)
            if not request.url.path.endswith("/documents") and length and int(length) > 65536:
                return JSONResponse({"detail": "JSON が大きすぎます"}, status_code=413)
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["Cache-Control"] = "no-store"
        if "Content-Security-Policy" not in response.headers:
            response.headers["Content-Security-Policy"] = (
                "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; "
                "img-src 'self' data:; frame-src 'self'; connect-src 'self'; "
                "object-src 'none'; base-uri 'none'; form-action 'self'; frame-ancestors 'self'"
            )
        return response

    @app.exception_handler(ConflictError)
    async def conflict(request, exc):
        return JSONResponse({"detail": str(exc), "code": "VERSION_OR_IDEMPOTENCY_CONFLICT"}, status_code=409)

    @app.exception_handler(ScopeError)
    async def scope(request, exc):
        return JSONResponse({"detail": "対象が見つかりません"}, status_code=404)

    @app.exception_handler(ValueError)
    async def invalid(request, exc):
        return JSONResponse({"detail": str(exc)}, status_code=422)

    @app.exception_handler(RequestValidationError)
    async def invalid_request(request, exc):
        # Do not echo supplied payloads or arbitrary unexpected fields.
        return JSONResponse({"detail": "入力項目・型・バージョンを確認してください"}, status_code=422)

    @app.exception_handler(Exception)
    async def unexpected(request, exc):
        logging.getLogger("sanzo_demo").error("Request failed: %s", type(exc).__name__)
        return JSONResponse({"detail": "処理に失敗しました。画面を再読込して保存状態を確認してください"}, status_code=500)

    @app.get("/api/status")
    def status():
        with repository.transaction() as conn:
            conn.execute("SELECT 1")
        capabilities = pipeline.capabilities()
        return {"mode": "LOCAL_DEMO", "database": "CONNECTED", **capabilities,
                "w0": "HISTORICAL_INCOMPLETE",
                "debug_manual": debug_manual, "manual_entry": debug_manual}

    @app.get("/api/cases")
    def cases():
        from .case_overview import case_summaries
        return case_summaries(actor, store, pipeline)

    @app.post("/api/cases", status_code=201)
    def new_case(body: CaseBody):
        return service.create_case(actor, str(uuid4()), body.name)

    @app.get("/api/cases/{case_id}")
    def case_detail(case_id: str):
        snapshot = pipeline.store.case_snapshot(actor, case_id)
        snapshot["reader_mode"] = pipeline.reader_mode
        snapshot["export_draft"] = export_review_state(
            snapshot["items"], snapshot["documents"], snapshot.get("tasks", []))[0]
        snapshot["comparison"] = comparison_projection(snapshot["items"], snapshot.get("fees", []), snapshot["documents"])
        snapshot["documents"] = [public_document(d) for d in snapshot["documents"]]
        snapshot["tasks"] = [public_task(t) for t in snapshot["tasks"]]
        return snapshot

    @app.get("/api/cases/{case_id}/fees/{fee_id}/rename-impact")
    def fee_rename_impact(case_id: str, fee_id: str):
        from .rename_impact import rename_impact
        return rename_impact(repository, actor, case_id, fee_id)

    @app.post("/api/cases/{case_id}/documents", status_code=201)
    async def upload(case_id: str, file: UploadFile = File(...)):
        store.get_case(actor, case_id)
        filename = (file.filename or "").replace("\\", "/").rsplit("/", 1)[-1]
        suffix = Path(filename).suffix.lower()
        if len(filename) > 240 or any(ord(c) < 32 for c in filename):
            raise HTTPException(422, "ファイル名を確認してください")
        document_id = str(uuid4())
        storage_key = document_id + suffix
        storage.mkdir(parents=True, exist_ok=True, mode=0o700)
        target = storage / storage_key
        digest, size = sha256(), 0
        try:
            with target.open("xb") as handle:
                while chunk := await file.read(1024 * 1024):
                    size += len(chunk)
                    if size > MAX_UPLOAD:
                        raise HTTPException(413, "ファイル上限は 20 MiB です")
                    digest.update(chunk)
                    handle.write(chunk)
                handle.flush()
                os.fsync(handle.fileno())
            target.chmod(0o600)
            media_type = validate_original(target, suffix)
            row = store.register_document(actor, case_id, document_id=document_id,
                     original_filename=filename, media_type=media_type, size_bytes=size,
                     sha256=digest.hexdigest(), storage_key=storage_key)
            return public_document({**row, "legacy_state": row["state"], "state": "RECEIVED", "blocked_reason": None})
        except BaseException:
            target.unlink(missing_ok=True)
            raise
        finally:
            await file.close()

    @app.get("/api/documents/{document_id}/original")
    def original(document_id: str):
        doc = store.get_document(actor, document_id)
        path = original_path(storage, doc)
        return FileResponse(path, media_type=doc["media_type"], filename=doc["original_filename"])

    @app.get("/api/documents/{document_id}/preview")
    def preview(document_id: str, page: int = 1, sheet: str | None = None,
                cell: str | None = None):
        doc = store.get_document(actor, document_id)
        path = original_path(storage, doc)
        if doc["media_type"] == "application/pdf":
            if sheet is not None or cell is not None:
                raise HTTPException(422, "PDF にはページ番号を指定してください")
            return HTMLResponse(pdf_html(path, doc["original_filename"], document_id, page))
        return HTMLResponse(excel_preview(path, doc["original_filename"], sheet_name=sheet, cell=cell))

    @app.get("/api/documents/{document_id}/pages/{page_number}.png")
    def page_image(document_id: str, page_number: int):
        doc = store.get_document(actor, document_id)
        if doc["media_type"] != "application/pdf":
            raise HTTPException(422, "PDF 原本を選択してください")
        return Response(pdf_page_png(original_path(storage, doc), page_number), media_type="image/png")

    def public_task(task):
        value = {k: v for k, v in task.items() if k not in {"tenant_id", "actor_id"}}
        value["status"] = task["state"]
        value["issues"] = (task.get("result_json") or {}).get("issues", [])
        return value

    @app.get("/api/documents/{document_id}/field-sources")
    def review_field_sources(document_id: str):
        return pipeline.review_field_sources(actor, document_id)

    @app.post("/api/documents/{document_id}/process", status_code=202)
    def process_document(document_id: str, body: ProcessBody, background_tasks: BackgroundTasks):
        task = pipeline.submit(actor, document_id, body.idempotency_key, field_bindings=body.field_bindings, pause_ai=body.pause_ai)
        if task["state"] == "QUEUED" and not task["replayed"]:
            background_tasks.add_task(pipeline.run, actor, task["task_id"])
        return public_task(task)

    @app.get("/api/tasks/{task_id}")
    def task_detail(task_id: str):
        return public_task(pipeline.store.get_task(actor, task_id))

    @app.get("/api/tasks/{task_id}/sources")
    def task_sources(task_id: str):
        return pipeline.store.get_task_sources(actor, task_id)

    @app.post("/api/documents/{document_id}/manual-lines", status_code=201)
    def manual_line(document_id: str, body: dict):
        if not debug_manual:
            raise HTTPException(403, "人工入力は内部デバッグ専用です")
        doc = store.get_document(actor, document_id)
        original_path(storage, doc)  # Source hash must still match when the human confirms.
        return store.add_manual_line(actor, document_id, body)

    @app.post("/api/cases/{case_id}/comparison/{row_id}/confirmation")
    def confirm_row(case_id: str, row_id: str, body: RowConfirmationBody):
        values = body.model_dump()
        values["members"] = tuple(RowMember(**m) for m in values["members"])
        return RowConfirmationService(repository).confirm(actor, RowConfirmationCommand(
            case_id=case_id, row_id=row_id, **values))

    @app.post("/api/cases/{case_id}/comparison/{row_id}/members/{item_id}/correction")
    def correct_member(case_id: str, row_id: str, item_id: str, body: SingleMemberCorrectionBody):
        return SingleMemberCorrectionService(repository).correct(actor, SingleMemberCorrectionCommand(
            case_id=case_id, row_id=row_id, item_id=item_id, **body.model_dump()))

    @app.post("/api/cases/{case_id}/comparison/{row_id}/members/{item_id}/conditions")
    def correct_conditions(case_id: str, row_id: str, item_id: str, body: ConditionCorrectionBody):
        from sanzo_core.condition_corrections import ConditionCorrectionService, ConditionCorrectionCommand
        return ConditionCorrectionService(repository).correct(actor, ConditionCorrectionCommand(
            case_id=case_id, row_id=row_id, item_id=item_id, **body.model_dump()))

    @app.post("/api/items/{item_id}/corrections")
    def correct(item_id: str, body: CorrectionBody):
        return service.correct(actor, CorrectionCommand(item_id=item_id, **body.model_dump()))

    @app.post("/api/items/{item_id}/revoke")
    def revoke(item_id: str, body: RevokeBody):
        return service.revoke_memory(actor, RevokeCommand(item_id=item_id, **body.model_dump()))

    @app.get("/api/cases/{case_id}/export.xlsx")
    def export(case_id: str):
        snapshot = pipeline.store.case_snapshot(actor, case_id)
        draft, _, _ = export_review_state(snapshot["items"], snapshot["documents"],
                                          snapshot.get("tasks", []))
        data = workbook_bytes(snapshot["case"], snapshot["items"], snapshot["documents"],
                              tasks=snapshot.get("tasks", []), fees=snapshot.get("fees", []), reader_mode=pipeline.reader_mode)
        prefix = "DRAFT-" if draft else ""
        return Response(data, media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                        headers={"Content-Disposition": f'attachment; filename="{prefix}sanzo-{case_id}.xlsx"'})

    @app.get("/")
    def index():
        return FileResponse(STATIC / "index.html")

    app.mount("/static", StaticFiles(directory=STATIC, check_dir=False), name="static")
    return app


# A missing DSN must fail startup clearly; no fallback database or credential lookup.
def from_environment():
    dsn = os.environ.get("SANZO_DATABASE_DSN")
    storage = os.environ.get("SANZO_UPLOAD_DIR")
    if not dsn or not storage:
        raise RuntimeError("Use tools/start_demo.py; dedicated PostgreSQL and storage settings required")
    from .configuration import AIAuthorization, assemble_runtime
    reader_mode = os.environ.get("SANZO_READER_MODE", "REAL_SDK")
    raw = os.environ.get("SANZO_AI_AUTHORIZATION_JSON")
    authorization = AIAuthorization.parse(raw) if raw is not None else None
    pipeline = assemble_runtime(PostgresRepository(dsn), storage, reader_mode=reader_mode,
                                authorization=authorization)
    tenant_id = "local-demo-mock-sdk" if reader_mode == "MOCK_SDK" else "local-demo"
    return create_app(dsn, storage, integrations=pipeline, tenant_id=tenant_id,
                      debug_manual=os.environ.get("SANZO_DEBUG_MANUAL") == "1")


# Uvicorn target: sanzo_demo.app:from_environment --factory.
