"""R2 synthetic browser fixture on 18769; never import for the formal Demo.

All quotation documents are processed through IntegrationRuntime.submit/run
with explicit MECHANISM_TEST ports. Source XLSX files are synthetic and local.
There is no document-table UPDATE, direct task-state injection, SDK, or AI.
"""
from __future__ import annotations

from contextlib import asynccontextmanager
from copy import deepcopy
from hashlib import sha256
import os
from pathlib import Path
from urllib.parse import urlsplit
from uuid import uuid4

from fastapi import HTTPException
from openpyxl import Workbook

from sanzo_core.demo_store import DemoStore
from sanzo_core.domain import Principal
from sanzo_core.domain import CorrectionCommand, RevokeCommand
from sanzo_core.condition_corrections import ConditionCorrectionCommand, ConditionCorrectionService
from sanzo_core.postgres import PostgresRepository
from sanzo_core.service import ReviewService
from sanzo_demo.ai import ExtractionResult
from sanzo_demo.app import create_app
from sanzo_demo.comparison import comparison_projection
from sanzo_demo.row_confirmation import (RowConfirmationCommand, RowConfirmationService,
    RowMember, SingleMemberCorrectionCommand, SingleMemberCorrectionService)
from sanzo_demo.extraction import ReadResult
from sanzo_demo.ingestion import IntegrationRuntime


DSN = os.environ["SANZO_TEST_DSN"]
parsed = urlsplit(DSN)
if (os.environ.get("SANZO_BROWSER_PORT") != "18769"
        or parsed.hostname is None or not parsed.hostname.startswith("aidemo-r2-pg-")
        or parsed.path != "/sanzo_r2_browser" or parsed.port != 5432):
    raise RuntimeError("Dedicated R2 18769 fixture and PostgreSQL required")

TENANT = "aidemo-r2-mechanism-only"
STORAGE = Path("/tmp/aidemo-r2-originals")
FIELDS = ("raw_label", "raw_value", "currency", "company", "transport_mode",
          "equipment", "billing_unit", "charge_scope", "extra_terms")
CONTEXT = FIELDS[4:]
repo = PostgresRepository(DSN)
store = DemoStore(repo)
service = ReviewService(repo)
actor = Principal(TENANT, "r2-synthetic-browser-operator")


class MechanismReader:
    def __init__(self):
        self.readings = {}
        self.calls = []

    def read(self, path, document):
        digest = sha256(Path(path).read_bytes()).hexdigest()
        if digest != document["sha256"]:
            raise AssertionError("Synthetic original SHA changed")
        self.calls.append(document["document_id"])
        return self.readings[digest]


class MechanismExtractor:
    def __init__(self):
        self.calls = []

    def extract(self, digest, blocks, schema, *, purpose="quotation_extraction"):
        if purpose != "fee_classification":
            raise AssertionError("Facts must come from the injected reader")
        self.calls.append({"sha256": digest, "purpose": purpose,
                           "item_ids": [block["item_id"] for block in blocks]})
        return ExtractionResult({"suggestions": [
            {"item_id": block["item_id"], "matched_fee_id": None,
             "suggested_fee_name": "海上運賃"} for block in blocks
        ]}, {"execution_kind": "MECHANISM_TEST", "purpose": purpose,
             "actual_api_calls": 0})


reader = MechanismReader()
extractor = MechanismExtractor()
runtime = IntegrationRuntime(repo, STORAGE, reader=reader, extractor=extractor,
                             allow_mechanism_tests=True, reader_mode="REAL_SDK")
app = create_app(DSN, STORAGE, tenant_id=TENANT, integrations=runtime)


def register_quote(case_id: str, code: str, label: str, price: str, *, company="A社") -> dict:
    """Register a real synthetic XLSX and paired cell-level mechanism evidence."""
    STORAGE.mkdir(parents=True, exist_ok=True, mode=0o700)
    document_id = "synthetic-r2-" + code
    filename = "SYNTHETIC_R2_" + code + ".xlsx"
    original = STORAGE / filename
    values = {"raw_label": label, "raw_value": price, "currency": "USD",
              "company": company, "transport_mode": "SEA", "equipment": "20FT",
              "billing_unit": "PER_CONTAINER", "charge_scope": "ORIGIN", "extra_terms": "STANDARD"}
    book = Workbook()
    sheet = book.active
    sheet.title = "Synthetic"
    sheet.append(list(FIELDS))
    sheet.append([values[field] for field in FIELDS])
    book.save(original)
    book.close()
    digest = sha256(original.read_bytes()).hexdigest()
    store.register_document(actor, case_id, document_id=document_id,
                            original_filename=filename,
                            media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                            size_bytes=original.stat().st_size, sha256=digest,
                            storage_key=filename)
    blocks = []
    for column, field in enumerate(FIELDS):
        source_id = "source-r2-" + code + "-" + field
        cell = chr(65 + column) + "2"
        ref = {"document_id": document_id, "document_sha256": digest,
               "source_block_ids": [source_id], "kind": "EXCEL_RANGE",
               "locator_status": "VERIFIED",
               "locator": {"sheet_name": "Synthetic", "A1_range": cell},
               "text_verification": "VERIFIED",
               "evidence_unit": {"kind": "EXCEL_CELL", "field_name": field,
                                 "record_id": "record-r2-" + code, "scope": "LINE"}}
        blocks.append({"source_id": source_id, "raw_text": values[field], "source_ref": ref})
    reader.readings[digest] = ReadResult(
        digest, tuple(blocks), {"test_adapter": "MECHANISM_TEST_CELL_FIXTURE"},
        origin="MECHANISM_TEST")
    return {"document_id": document_id, "sha256": digest, "filename": filename}


def process_case(case_id: str, name: str, code: str, label: str, price: str,
                 *, company="A社") -> dict:
    service.create_case(actor, case_id, name)
    document = register_quote(case_id, code, label, price, company=company)
    submitted = runtime.submit(actor, document["document_id"], "r2-process-" + code)
    task = runtime.run(actor, submitted["task_id"])
    snapshot = runtime.store.case_snapshot(actor, case_id)
    if len(snapshot["items"]) != 1 or task["state"] not in {"AVAILABLE", "REVIEW_REQUIRED"}:
        raise AssertionError(f"Synthetic processing failed: {task['state']}")
    item = snapshot["items"][0]
    return {"case_id": case_id, "document_id": document["document_id"],
            "document_state": snapshot["documents"][0]["state"],
            "task_state": task["state"], "item_id": item["item_id"],
            "saved_status": item["saved_status"],
            "initial_kind": item["initial_classification"]["kind"],
            "price": item["current_price"], "currency": item["currency"],
            "reader_origin": "MECHANISM_TEST", "reader_calls": len(reader.calls),
            "extractor_injected_calls": len(extractor.calls), "real_sdk_calls": 0,
            "openai_calls": 0}


def current(case_id: str) -> dict:
    return runtime.store.case_snapshot(actor, case_id)


base_lifespan = app.router.lifespan_context


@asynccontextmanager
async def fixture_lifespan(application):
    async with base_lifespan(application):
        if not store.list_cases(actor):
            process_case("r2-f1-original", "合成F1・条件修正前", "f1-original",
                         "AMBIGUOUS O/F", "100")
            process_case("r2-f2-seed", "合成F2・事前に確認する見積", "f2-seed",
                         "TRUSTED O/F", "110")
        yield


app.router.lifespan_context = fixture_lifespan


@app.get("/__test/fixture")
def fixture_info():
    return {"scope": "MECHANISM_TEST_SYNTHETIC", "browser_port": 18769,
            "reader_attached": True, "reader_origin": "MECHANISM_TEST",
            "extractor_attached": True, "extractor_origin": "MECHANISM_TEST",
            "real_sdk_calls": 0, "openai_calls": 0,
            "reader_calls": len(reader.calls), "extractor_injected_calls": len(extractor.calls),
            "cases": [case["case_id"] for case in store.list_cases(actor)]}


@app.post("/__test/seed-f1-next")
def seed_f1_next():
    original = current("r2-f1-original")["items"][0]
    if original["effective_context"]["equipment"] != "40FT":
        raise HTTPException(409, "First correct original equipment in browser")
    if any(case["case_id"] == "r2-f1-next" for case in store.list_cases(actor)):
        raise HTTPException(409, "F1 next quote already processed")
    return process_case("r2-f1-next", "合成F1・次の見積を確認", "f1-next",
                        "AMBIGUOUS O/F", "200")


@app.post("/__test/seed-f1-future")
def seed_f1_future():
    next_item = current("r2-f1-next")["items"][0]
    if not next_item["current_fee_id"] or next_item["saved_status"] != "HUMAN_CONFIRMED":
        raise HTTPException(409, "First confirm the next quote in browser")
    if any(case["case_id"] == "r2-f1-future" for case in store.list_cases(actor)):
        raise HTTPException(409, "F1 future quote already processed")
    return process_case("r2-f1-future", "合成F1・さらに次の見積", "f1-future",
                        "AMBIGUOUS O/F", "300")


@app.post("/__test/seed-f2-target")
def seed_f2_target():
    seed = current("r2-f2-seed")["items"][0]
    if not seed["current_fee_id"] or seed["current_memory_status"] != "ACTIVE":
        raise HTTPException(409, "First confirm the seed quote in browser")
    if any(case["case_id"] == "r2-f2-target" for case in store.list_cases(actor)):
        raise HTTPException(409, "F2 target quote already processed")
    result = process_case("r2-f2-target", "合成F2・本次資料の確認", "f2-target",
                          "TRUSTED O/F", "220")
    if result["task_state"] != "AVAILABLE" or result["saved_status"] != "MEMORY_APPLIED":
        raise AssertionError("F2 target must be AVAILABLE via actual mechanism pipeline")
    return result


@app.post("/__test/seed-r2-shared-after-guard")
def seed_r2_shared_after_guard():
    """Synthetic review setup; all changes stay in this disposable fixture."""
    if any(case["case_id"] == "r2-shared-chosen" for case in store.list_cases(actor)):
        raise HTTPException(409, "R2 shared-key fixture already exists")
    first = process_case("r2-shared-first", "合成R2・条件保護", "shared-first", "R2 SAME SOURCE", "100")
    item = current(first["case_id"])["items"][0]
    fee_a = service.correct(actor, CorrectionCommand(
        item_id=item["item_id"], expected_case_revision=item["case_revision"],
        expected_item_version=item["version"],
        expected_memory_version=item["current_memory_version"],
        idempotency_key=uuid4().hex, new_fee_name="費用甲"))["fee_id"]
    snapshot = current(first["case_id"])
    row = comparison_projection(snapshot["items"], snapshot["fees"], snapshot["documents"])["rows"][0]
    item = snapshot["items"][0]
    ConditionCorrectionService(repo).correct(actor, ConditionCorrectionCommand(
        case_id=first["case_id"], row_id=row["row_id"], item_id=item["item_id"],
        expected_case_revision=snapshot["case"]["case_revision"],
        expected_item_version=item["version"],
        expected_memory_version=item["current_memory_version"],
        idempotency_key=uuid4().hex, field="equipment", value="40FT"))
    for case_id, code, price in (("r2-shared-peer", "shared-peer", "200"),
                                 ("r2-shared-chosen", "shared-chosen", "300")):
        process_case(case_id, "合成R2・同じ学習キー", code, "R2 SAME SOURCE", price)
        item = current(case_id)["items"][0]
        service.correct(actor, CorrectionCommand(
            item_id=item["item_id"], expected_case_revision=item["case_revision"],
            expected_item_version=item["version"],
            expected_memory_version=item["current_memory_version"],
            idempotency_key=uuid4().hex, target_fee_id=fee_a))
    snapshot = current("r2-shared-chosen")
    row = comparison_projection(snapshot["items"], snapshot["fees"], snapshot["documents"])["rows"][0]
    item = snapshot["items"][0]
    result = SingleMemberCorrectionService(repo).correct(actor, SingleMemberCorrectionCommand(
        case_id="r2-shared-chosen", row_id=row["row_id"], item_id=item["item_id"],
        expected_case_revision=snapshot["case"]["case_revision"],
        expected_item_version=item["version"],
        expected_memory_version=item["current_memory_version"],
        idempotency_key=uuid4().hex, new_fee_name="費用乙"))
    return {"case_id": "r2-shared-chosen", "response": result,
            "reader_origin": "MECHANISM_TEST", "real_sdk_calls": 0, "openai_calls": 0}


@app.post("/__test/seed-r3-plain-conflict")
def seed_r3_plain_conflict():
    if any(case["case_id"] == "r3-plain-chosen" for case in store.list_cases(actor)):
        raise HTTPException(409, "R3 plain conflict fixture already exists")
    process_case("r3-plain-first", "合成R3・通常の分類競合", "plain-first", "R3 SAME SOURCE", "100")
    item = current("r3-plain-first")["items"][0]
    fee_a = service.correct(actor, CorrectionCommand(
        item_id=item["item_id"], expected_case_revision=item["case_revision"],
        expected_item_version=item["version"], expected_memory_version=item["current_memory_version"],
        idempotency_key=uuid4().hex, new_fee_name="費用甲"))["fee_id"]
    for case_id, code, price in (("r3-plain-peer", "plain-peer", "200"),
                                 ("r3-plain-chosen", "plain-chosen", "300")):
        process_case(case_id, "合成R3・通常の分類競合", code, "R3 SAME SOURCE", price)
        item = current(case_id)["items"][0]
        service.correct(actor, CorrectionCommand(
            item_id=item["item_id"], expected_case_revision=item["case_revision"],
            expected_item_version=item["version"], expected_memory_version=item["current_memory_version"],
            idempotency_key=uuid4().hex, target_fee_id=fee_a))
    snapshot = current("r3-plain-chosen")
    row = comparison_projection(snapshot["items"], snapshot["fees"], snapshot["documents"])["rows"][0]
    item = snapshot["items"][0]
    result = SingleMemberCorrectionService(repo).correct(actor, SingleMemberCorrectionCommand(
        case_id="r3-plain-chosen", row_id=row["row_id"], item_id=item["item_id"],
        expected_case_revision=snapshot["case"]["case_revision"],
        expected_item_version=item["version"], expected_memory_version=item["current_memory_version"],
        idempotency_key=uuid4().hex, new_fee_name="費用乙"))
    return {"case_id": "r3-plain-chosen", "response": result,
            "reader_origin": "MECHANISM_TEST", "real_sdk_calls": 0, "openai_calls": 0}


@app.post("/__test/seed-r3-revoked-after-reconfirm")
def seed_r3_revoked_after_reconfirm():
    if any(case["case_id"] == "r3-revoked" for case in store.list_cases(actor)):
        raise HTTPException(409, "R3 revocation fixture already exists")
    process_case("r3-revoked", "合成R3・条件再確認後に撤回", "revoked", "R3 REVOKE SOURCE", "220")
    item = current("r3-revoked")["items"][0]
    fee = service.correct(actor, CorrectionCommand(
        item_id=item["item_id"], expected_case_revision=item["case_revision"],
        expected_item_version=item["version"], expected_memory_version=item["current_memory_version"],
        idempotency_key=uuid4().hex, new_fee_name="撤回対象費目"))["fee_id"]
    snapshot = current("r3-revoked")
    row = comparison_projection(snapshot["items"], snapshot["fees"], snapshot["documents"])["rows"][0]
    item = snapshot["items"][0]
    ConditionCorrectionService(repo).correct(actor, ConditionCorrectionCommand(
        case_id="r3-revoked", row_id=row["row_id"], item_id=item["item_id"],
        expected_case_revision=snapshot["case"]["case_revision"],
        expected_item_version=item["version"], expected_memory_version=item["current_memory_version"],
        idempotency_key=uuid4().hex, field="equipment", value="40FT"))
    snapshot = current("r3-revoked")
    row = comparison_projection(snapshot["items"], snapshot["fees"], snapshot["documents"])["rows"][0]
    item = snapshot["items"][0]
    fee_version = next(x["name_version"] for x in snapshot["fees"] if x["fee_id"] == fee)
    RowConfirmationService(repo).confirm(actor, RowConfirmationCommand(
        case_id="r3-revoked", row_id=row["row_id"],
        expected_case_revision=snapshot["case"]["case_revision"],
        members=(RowMember(item["item_id"], item["version"], item["current_memory_version"]),),
        idempotency_key=uuid4().hex, target_fee_id=fee,
        expected_fee_id=fee, expected_name_version=fee_version))
    item = current("r3-revoked")["items"][0]
    result = service.revoke_memory(actor, RevokeCommand(
        item_id=item["item_id"], expected_case_revision=item["case_revision"],
        expected_item_version=item["version"], expected_memory_version=item["current_memory_version"],
        idempotency_key=uuid4().hex, reason="R3_SYNTHETIC_EXPLICIT_REVOCATION"))
    return {"case_id": "r3-revoked", "response": result,
            "reader_origin": "MECHANISM_TEST", "real_sdk_calls": 0, "openai_calls": 0}
