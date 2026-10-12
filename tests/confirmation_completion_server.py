"""Explicitly injected synthetic browser fixtures on isolated PostgreSQL/18772.

No SDK reader, model client, credential loading, or HTTP injection route exists.
Original workbook bytes are synthetic and written only to the external test disk.
"""
from __future__ import annotations

from hashlib import sha256
from io import BytesIO
import os
from pathlib import Path
from uuid import uuid4
from urllib.parse import urlsplit

from openpyxl import Workbook

from sanzo_core.demo_store import DemoStore
from sanzo_core.domain import Context, CorrectionCommand, Observation, Principal, SyntheticLine, SDKObservedLine, RevokeCommand
from sanzo_core.ingestion_store import IngestionStore
from sanzo_core.postgres import PostgresRepository
from sanzo_core.service import ReviewService
from sanzo_demo.ingestion import IntegrationRuntime
import sanzo_demo.app as web_module

DSN = os.environ["SANZO_TEST_DSN"]
parsed = urlsplit(DSN)
if (os.environ.get("SANZO_BROWSER_PORT") != "18772" or parsed.hostname != "127.0.0.1"
        or parsed.port != 55472 or parsed.path not in {"/confirmation_completion", "/confirmation_completion_browser_final", "/confirmation_completion_browser_r2"}
        or parsed.username != "confirmation_test"):
    raise RuntimeError("Only dedicated confirmation_completion PostgreSQL/18772 accepted")
ROOT = Path(os.environ["SANZO_CONFIRMATION_OUTPUT"]).resolve()
if str(ROOT) != "/Volumes/SANZO_TEST/private/tmp/confirmation-completion-20260930":
    raise RuntimeError("External test output root required")
RUN_ID = os.environ.get("SANZO_CONFIRMATION_RUN_ID", "debug")
if not RUN_ID.isalnum() or len(RUN_ID) > 30:
    raise RuntimeError("Bounded explicit test run ID required")
STORAGE = ROOT / "browser" / ("storage-" + RUN_ID)
STORAGE.mkdir(parents=True, exist_ok=True)
TENANT = "confirmation-completion-browser-synthetic-" + RUN_ID
repo = PostgresRepository(DSN)
repo.migrate()
store = DemoStore(repo)
review = ReviewService(repo)
actor = Principal(TENANT, "synthetic-fixture-seed")


def seed_case(case_id, specs):
    if not any(c["case_id"] == case_id for c in store.list_cases(actor)):
        review.create_case(actor, case_id, "合成検証・" + case_id)
    fee_id = next((f["fee_id"] for f in store.list_fees(actor) if f["display_name"] == "合成海上運賃"), None)
    for number, spec in enumerate(specs, 1):
        document_id = f"{case_id}-doc-{number}"
        item_id, source_id = f"{case_id}-item-{number}", f"{case_id}-source-{number}"
        existing = next((i for i in store.list_items(actor, case_id) if i["item_id"] == item_id), None)
        if existing and existing.get("current_fee_id"):
            continue
        filename = f"SYNTHETIC_{case_id}_{number}.xlsx"
        book = Workbook()
        sheet = book.active
        sheet.title = "Synthetic"
        sheet.append(["合成データ／実SDK・AI不使用", "", "", ""])
        sheet.append(["原文費目", "機材", "単価", "通貨"])
        sheet.append([spec.get("label", "合成海上運賃"), spec.get("original_equipment", spec.get("equipment", "40FT")), spec.get("price", "1900"), "USD"])
        output = BytesIO(); book.save(output); book.close()
        data = output.getvalue(); (STORAGE / filename).write_bytes(data)
        digest = sha256(data).hexdigest()
        if not existing:
            store.register_document(actor, case_id, document_id=document_id,
                original_filename=filename, media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                size_bytes=len(data), sha256=digest, storage_key=filename)
        context = Context("SEA", spec.get("equipment", "40FT"), "CONTAINER", "ORIGIN", "STANDARD")
        observation = Observation(TENANT, item_id, case_id, document_id, source_id,
            "合成" + str(number) + "社", spec.get("label", "合成海上運賃"), spec.get("price", "1900"), "USD",
            context, spec.get("source_ready", True))
        source = {"synthetic": True, "document_id": document_id, "document_revision": 1,
            "document_sha256": digest, "source_block_ids": [source_id], "kind": "EXCEL_RANGE",
            "locator_status": "VERIFIED", "locator": {"sheet_name": "Synthetic", "A1_range": "A3:D3"},
            "test_notice": "INJECTED_SYNTHETIC_NOT_REAL_SDK_OR_AI"}
        item = existing or store.add_synthetic_lines(actor, document_id, [SyntheticLine(observation, source)])[0]
        saved = review.correct(actor, CorrectionCommand(item_id=item_id,
            expected_case_revision=item["case_revision"], expected_item_version=item["version"],
            expected_memory_version=item["current_memory_version"], idempotency_key=uuid4().hex,
            **({"target_fee_id": fee_id} if fee_id else {"new_fee_name": "合成海上運賃"})))
        fee_id = saved["fee_id"]


def synthetic_pdf(label, equipment, price):
    """Small generated one-page fixture, not an SDK output or customer source."""
    from pypdf import PdfWriter
    from pypdf.generic import DictionaryObject, NameObject, DecodedStreamObject
    writer = PdfWriter()
    page = writer.add_blank_page(width=595, height=842)
    font = DictionaryObject({NameObject("/Type"): NameObject("/Font"),
        NameObject("/Subtype"): NameObject("/Type1"), NameObject("/BaseFont"): NameObject("/Helvetica")})
    page[NameObject("/Resources")] = DictionaryObject({NameObject("/Font"):
        DictionaryObject({NameObject("/F1"): writer._add_object(font)})})
    stream = DecodedStreamObject()
    text = f"BT /F1 14 Tf 50 780 Td (INJECTED SYNTHETIC - NOT SDK OR AI) Tj 0 -40 Td ({label} / {equipment} / {price} USD) Tj ET"
    stream.set_data(text.encode("ascii"))
    page[NameObject("/Contents")] = writer._add_object(stream)
    output = BytesIO(); writer.write(output)
    return output.getvalue()


def final_case(case_id, specs, *, issues=(), revoke=False, conflict=False, classify=True):
    """All origin flags here are deliberately injected to exercise saved-result policies.

    The explicit test_notice and sdk_identity identify them as simulated records.
    There is no reader/extractor invocation; this does not measure SDK/AI quality.
    """
    if any(c["case_id"] == case_id for c in store.list_cases(actor)):
        return
    review.create_case(actor, case_id, "合成・" + case_id)
    ingest = IngestionStore(repo)
    fee_id = None
    for number, spec in enumerate(specs, 1):
        document_id = f"{case_id}-doc-{number}"
        item_id, source_id = f"{case_id}-item-{number}", f"{case_id}-source-{number}"
        label = spec.get("label", case_id + " OCEAN FREIGHT")
        price = spec.get("price", "1900")
        equipment = spec.get("equipment", "40FT")
        page_only = spec.get("page_only", False)
        filename = f"INJECTED_SYNTHETIC_{case_id}_{number}." + ("pdf" if page_only else "xlsx")
        if page_only:
            data = synthetic_pdf(label, equipment or "UNKNOWN", price)
        else:
            book = Workbook(); sheet = book.active; sheet.title = "Synthetic"
            sheet.append(["合成データ／実SDK・AI不使用", "", "", "", "", ""])
            sheet.append(["原文費目", "機材", "単価", "通貨", "課金単位", "適用範囲", "追加条件"])
            sheet.append([label, spec.get("original_equipment", equipment), price, "USD", "CONTAINER", "ORIGIN", "STANDARD"])
            output = BytesIO(); book.save(output); book.close(); data = output.getvalue()
        (STORAGE / filename).write_bytes(data)
        digest = sha256(data).hexdigest()
        doc = store.register_document(actor, case_id, document_id=document_id,
            original_filename=filename, media_type="application/pdf" if page_only else
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            size_bytes=len(data), sha256=digest, storage_key=filename)
        real_policy = spec.get("real_policy", False)
        context = Context(None if real_policy else "SEA", equipment, "CONTAINER", "ORIGIN", "STANDARD", ready=spec.get("ready", True))
        observation = Observation(TENANT, item_id, case_id, document_id, source_id,
            None if real_policy else "SYNTHETIC_SHARED_CO", label, price, "USD", context, spec.get("source_ready", True))
        source = {"origin": "SDK_OBSERVED", "extraction_origin": "REAL_SDK" if real_policy else "MECHANISM_TEST",
            "document_id": document_id, "document_revision": 1, "document_sha256": digest,
            "source_block_ids": [source_id], "kind": "PDF_PAGE" if page_only else "EXCEL_RANGE",
            "locator_status": "PAGE_ONLY" if page_only else "VERIFIED", "locator": {"page_number": 1} if page_only else
            {"sheet_name": "Synthetic", "A1_range": "A3:G3"}, "text_verification": "VERIFIED",
            "test_notice": "INJECTED_SYNTHETIC_NOT_REAL_SDK_OR_AI"}
        if spec.get("unavailable"):
            source.update(locator_status="UNAVAILABLE", locator=None)
        if real_policy:
            record_id = item_id + "-record"
            fields = {"raw_label": label, "equipment": equipment, "billing_unit": "CONTAINER", "charge_scope": "ORIGIN", "extra_terms": "STANDARD"}
            source["field_sources"] = {name: {"value": value, "original_value": value,
                "source_block_id": source_id, "record_key": [document_id, record_id],
                "evidence_unit": {"scope": "LINE", "field_name": name, "record_id": record_id}}
                for name, value in fields.items() if value is not None}
            source["evidence_unit"] = source["field_sources"]["raw_label"]["evidence_unit"]
        line = SDKObservedLine(observation, source, raw_value=price,
            ai_suggested_name=None if classify else "合成AI仮判定")
        task = ingest.start_task(actor, document_id, "synthetic-seed-" + document_id)
        for state in ["READING", "SAVING"]:
            ingest.transition(actor, task["task_id"], state)
        block = {"source_id": source_id, "raw_text": f"{label} {equipment} {price} USD", "source_ref": source}
        ingest.finish(actor, task["task_id"], [line], list(issues),
            {"origin": "MECHANISM_TEST", "document_sha256": digest, "sources": [block],
             "sdk_identity": {"test_notice": "INJECTED_SYNTHETIC_NOT_REAL_SDK_OR_AI", "sdk_calls": 0}},
            {"test_notice": "INJECTED_SYNTHETIC_NOT_REAL_SDK_OR_AI", "ai_calls": 0})
        item = next(i for i in store.list_items(actor, case_id) if i["item_id"] == item_id)
        if classify:
            saved = review.correct(actor, CorrectionCommand(item_id=item_id,
                expected_case_revision=item["case_revision"], expected_item_version=item["version"],
                expected_memory_version=item["current_memory_version"], idempotency_key=uuid4().hex,
                **({"target_fee_id": fee_id} if fee_id and not conflict else {"new_fee_name": f"合成 {case_id} " + str(number if conflict else 1)})))
            fee_id = saved["fee_id"]
    if revoke:
        item = store.list_items(actor, case_id)[0]
        review.revoke_memory(actor, RevokeCommand(item["item_id"], item["case_revision"], item["version"],
            item["current_memory_version"], uuid4().hex, "INJECTED_SYNTHETIC 明示撤回"))


if os.environ.get("SANZO_BASELINE_STATIC") == "1":
    seed_case("confirm-current", [{}])
    seed_case("correct-one", [{"equipment": "20FT", "original_equipment": "40FT"}, {"price": "1850"}])
    seed_case("source-review", [{"source_ready": False}, {"source_ready": False, "price": "1850"}])
else:
    final_case("cc-current", [{"ready": False}])
    final_case("cc-retry", [{}])
    final_case("cc-stale", [{}])
    final_case("cc-correct", [{"equipment": "20FT", "original_equipment": "40FT"}, {"equipment": "20FT", "price": "1850", "label": "cc-correct SECOND FREIGHT"}])
    final_case("cc-source", [{"source_ready": False, "page_only": True}, {"source_ready": False, "page_only": True, "price": "1850"}])
    final_case("cc-missing", [{"equipment": None, "unavailable": True, "source_ready": False}])
    final_case("cc-conflict", [{}, {"price": "1850"}], conflict=True)
    final_case("cc-revoked", [{}], revoke=True)
    final_case("cc-profile", [{"real_policy": True}], issues=[{"code": "EXTRACTION_UNRESOLVED", "reason": "FIELD_HEADER_NOT_BOUND", "field": "company"}, {"code": "EXTRACTION_UNRESOLVED", "reason": "FIELD_HEADER_NOT_BOUND", "field": "transport_mode"}])
    final_case("cc-coverage", [{"page_only": True, "source_ready": False}], issues=[{"code": "SDK_LIMITATION", "limitation": "OCR_NOT_EXECUTED"}])
    final_case("cc-unknown-document", [{}], issues=[{"code": "SOURCE_LINE_NOT_EXTRACTED"}])
    final_case("cc-ai", [{}], classify=False)

if os.environ.get("SANZO_BASELINE_STATIC") == "1":
    web_module.STATIC = ROOT / "baseline-source" / "static"
runtime = IntegrationRuntime(repo, STORAGE, reader=None, extractor=None, reader_mode="REAL_SDK")
app = web_module.create_app(DSN, STORAGE, tenant_id=TENANT, integrations=runtime)
