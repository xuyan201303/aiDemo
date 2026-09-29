"""Disposable browser acceptance fixture for the current aiDemo Web UI.

Runs against an isolated PostgreSQL container and synthetic local workbooks.
The ordinary application routes perform the actual browser saves/exports.
Neither a reader nor an AI extractor is assembled. This module is never used
by the live 8765 application. The fixture-only next-quote endpoint is called
*after* browser correction so its learning outcome cannot be preloaded.
"""
from __future__ import annotations

from contextlib import asynccontextmanager
from hashlib import sha256
import os
from pathlib import Path
from urllib.parse import urlsplit
from uuid import uuid4

from fastapi import HTTPException
from openpyxl import Workbook

from sanzo_core.demo_store import DemoStore
from sanzo_core.domain import Context, Observation, Principal, SyntheticLine
from sanzo_core.postgres import PostgresRepository
from sanzo_core.service import ReviewService
from sanzo_demo.app import create_app
from sanzo_demo.comparison import comparison_projection
from sanzo_demo.ingestion import IntegrationRuntime
from sanzo_demo.row_confirmation import RowConfirmationCommand, RowConfirmationService, RowMember


DSN = os.environ["SANZO_TEST_DSN"]
PORT = os.environ.get("SANZO_BROWSER_PORT")
parsed = urlsplit(DSN)
if (PORT != "18768" or parsed.hostname is None or not parsed.hostname.startswith("aidemo-ui-pg-")
        or parsed.path != "/sanzo_ui_acceptance" or parsed.port != 5432):
    raise RuntimeError("Dedicated 18768 browser fixture and PostgreSQL required")

TENANT = "aidemo-ui-acceptance-synthetic"
STORAGE = Path("/tmp/aidemo-ui-acceptance-originals")
repo = PostgresRepository(DSN)
store = DemoStore(repo)
service = ReviewService(repo)
rows = RowConfirmationService(repo)
actor = Principal(TENANT, "synthetic-test-operator")
runtime = IntegrationRuntime(repo, STORAGE, reader=None, extractor=None, reader_mode="REAL_SDK")
app = create_app(DSN, STORAGE, tenant_id=TENANT, integrations=runtime)


def add_line(case_id, suffix, company, raw_label, price, equipment="20FT", *, billing_unit="CONTAINER"):
    """Create a source-linked synthetic quotation with a real local XLSX file."""
    document_id = "synthetic-doc-" + suffix
    item_id = "synthetic-item-" + suffix
    source_id = "synthetic-source-" + suffix
    filename = "SYNTHETIC_" + suffix + ".xlsx"
    path = STORAGE / filename
    book = Workbook()
    sheet = book.active
    sheet.title = "Synthetic"
    sheet.append(["原文の費目表記", "今回の単価", "通貨"])
    sheet.append([raw_label, price, "USD"])
    book.save(path)
    book.close()
    digest = sha256(path.read_bytes()).hexdigest()
    store.register_document(actor, case_id, document_id=document_id,
                            original_filename=filename,
                            media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                            size_bytes=path.stat().st_size, sha256=digest, storage_key=filename,
                            blocked_reason="SYNTHETIC_FIXTURE_NO_READER")
    observation = Observation(TENANT, item_id, case_id, document_id, source_id,
                              company, raw_label, price, "USD",
                              Context("SEA", equipment, billing_unit, "ORIGIN", "STANDARD"), True)
    source_ref = {"synthetic": True, "document_id": document_id,
                  "document_revision": 1, "document_sha256": digest,
                  "source_block_ids": [source_id], "kind": "EXCEL_RANGE",
                  "locator_status": "VERIFIED", "locator": {"sheet_name": "Synthetic", "A1_range": "A2:C2"}}
    store.add_synthetic_lines(actor, document_id, [SyntheticLine(observation=observation,
        source_ref=source_ref, ai_suggested_name="海上運賃", raw_value=price)])
    return document_id, item_id


def confirm_row(case_id, item_id, *, fee_id=None, new_name=None):
    snapshot = store.case_snapshot(actor, case_id)
    projection = comparison_projection(snapshot["items"], snapshot["fees"], snapshot["documents"])
    row = next(row for row in projection["rows"] if item_id in row["item_ids"])
    members = tuple(RowMember(item["item_id"], item["version"],
                              item["current_memory_version"],
                              (item["memory_resolution"]["evidence_versions"]
                               if (item.get("memory_resolution") or {}).get("kind") == "LEGACY_PROJECTION" else None))
                    for item in row["member_items"])
    selected = next((fee for fee in snapshot["fees"] if fee["fee_id"] == fee_id), None)
    result = rows.confirm(actor, RowConfirmationCommand(
        case_id, row["row_id"], snapshot["case"]["case_revision"], members,
        "synthetic-initial-confirm-" + case_id + "-" + item_id,
        target_fee_id=fee_id, new_fee_name=new_name,
        expected_fee_id=fee_id, expected_name_version=selected["name_version"] if selected else None))
    return result["fee_id"]


def set_fixture_task_state(document_id, status, item_ids=()):
    """Inject a labelled task status only in this disposable synthetic DB."""
    task = runtime.store.start_task(actor, document_id, "synthetic-task-" + document_id)
    if status == "QUEUED":
        return task
    with repo.transaction() as conn:
        current = runtime.store._task(conn, actor, task["task_id"], lock=True)
        runtime.store._advance(conn, actor, current, status,
            metadata={"origin": "INJECTED_TEST_CASE", "sdk_calls": 0, "ai_calls": 0},
            result={"item_ids": list(item_ids), "issues": [],
                    "origin": "INJECTED_TEST_CASE"})
    return task


def seed_initial():
    if store.list_cases(actor):
        # The real app correctly marks an unfinished task INTERRUPTED during
        # startup. Re-establish this labelled synthetic list-state fixture,
        # without invoking any reader or extractor.
        processing = runtime.store.document_states(actor, "case-processing")["synthetic-doc-processing"]
        if processing["state"] != "QUEUED":
            runtime.store.start_task(actor, "synthetic-doc-processing",
                                     "synthetic-requeue-" + uuid4().hex)
        return
    STORAGE.mkdir(parents=True, exist_ok=True, mode=0o700)
    for case_id, name in [
        ("case-empty", "合成・資料なし"),
        ("case-processing", "合成・解析中"),
        ("case-review", "合成・A/B/C 明細修正"),
        ("case-confirmed", "合成・確認済み"),
        ("case-delivery", "合成・配送費の参照先"),
    ]:
        service.create_case(actor, case_id, name)
    processing_doc, _ = add_line("case-processing", "processing", "P社", "WAITING QUOTE", "500")
    # A/B/C/E start in one 20FT row. C's current classification is an
    # explicitly erroneous synthetic baseline; the browser must correct it.
    for suffix, company, label, price in [
        ("a", "A社", "O/F 20'", "1200"),
        ("b", "B社", "OCEAN FREIGHT 20'", "1150"),
        ("c", "C社", "LOCAL DELIVERY", "1180"),
        ("e", "E社", "SEA CHARGE 20'", "1230"),
    ]:
        add_line("case-review", suffix, company, label, price)
    add_line("case-review", "d", "D社", "OCEAN FREIGHT 40'", "1900", "40FT")
    sea_fee = confirm_row("case-review", "synthetic-item-a", new_name="海上運賃")
    confirm_row("case-review", "synthetic-item-d", fee_id=sea_fee)
    confirmed_doc, confirmed_item = add_line("case-confirmed", "confirmed", "F社",
                                              "OCEAN FREIGHT 40'", "2050", "40FT")
    confirm_row("case-confirmed", confirmed_item, fee_id=sea_fee)
    add_line("case-delivery", "delivery", "X社", "DELIVERY FEE", "900")
    confirm_row("case-delivery", "synthetic-item-delivery", new_name="配送費")
    # Do this after the application's startup interruption sweep. The
    # task states are fixture evidence, never represented as SDK outcomes.
    set_fixture_task_state(processing_doc, "QUEUED")
    set_fixture_task_state(confirmed_doc, "AVAILABLE", [confirmed_item])


base_lifespan = app.router.lifespan_context


@asynccontextmanager
async def fixture_lifespan(current_app):
    async with base_lifespan(current_app):
        seed_initial()
        yield


app.router.lifespan_context = fixture_lifespan


@app.get("/__test/fixture")
def fixture_info():
    return {"scope": "INJECTED_TEST_CASE", "tenant": TENANT,
            "browser_port": 18768, "reader_attached": False, "ai_attached": False,
            "initial_cases": [case["case_id"] for case in store.list_cases(actor)],
            "next_quote_seeded": any(case["case_id"] == "case-next" for case in store.list_cases(actor))}


@app.post("/__test/seed-next")
def seed_next_quotes():
    """Only after UI correction: fresh prices probe saved name learning."""
    if any(case["case_id"] == "case-next" for case in store.list_cases(actor)):
        raise HTTPException(409, "Synthetic next quotes were already seeded")
    service.create_case(actor, "case-next", "合成・次回の新しい見積")
    for suffix, company, raw_label, price in [
        ("next-a", "A社", "O/F 20'", "1300"),
        ("next-b", "B社", "OCEAN FREIGHT 20'", "1250"),
        ("next-c", "C社", "LOCAL DELIVERY", "1380"),
    ]:
        add_line("case-next", suffix, company, raw_label, price)
    snapshot = store.case_snapshot(actor, "case-next")
    return {"case_id": "case-next", "origin": "INJECTED_TEST_CASE",
            "reader_calls": 0, "ai_calls": 0,
            "items": [{"item_id": item["item_id"], "raw_label": item["observation"]["raw_label"],
                       "price": item["current_price"], "currency": item["currency"],
                       "decision": item["initial_classification"], "fee_id": item["current_fee_id"]}
                      for item in snapshot["items"]]}
