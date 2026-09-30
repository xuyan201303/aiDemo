"""Disposable browser fixture for page routes and sidebar acceptance only.

Requires a dedicated PostgreSQL container on an isolated Docker network.
It does not initialize a reader, SDK adapter, AI client or credentials.
"""
from __future__ import annotations

from hashlib import sha256
from io import BytesIO
import os
from pathlib import Path
from urllib.parse import urlsplit

from openpyxl import Workbook

from sanzo_core.demo_store import DemoStore
from sanzo_core.domain import Context, Observation, Principal, SyntheticLine
from sanzo_core.postgres import PostgresRepository
from sanzo_core.service import ReviewService
from sanzo_demo.app import create_app
from sanzo_demo.ingestion import IntegrationRuntime


DSN = os.environ["SANZO_TEST_DSN"]
parsed = urlsplit(DSN)
if (os.environ.get("SANZO_BROWSER_PORT") != "18770"
        or parsed.hostname is None or not parsed.hostname.startswith("aidemo-route-pg-")
        or parsed.path != "/sanzo_route_acceptance" or parsed.port != 5432):
    raise RuntimeError("Only the disposable 18770 route fixture database is accepted")

TENANT = "aidemo-route-sidebar-synthetic"
CASE_ID = "synthetic-route-case"
STORAGE = Path("/tmp/aidemo-route-originals")
STORAGE.mkdir(parents=True, exist_ok=True)
repo = PostgresRepository(DSN)
repo.migrate()
store = DemoStore(repo)
service = ReviewService(repo)
actor = Principal(TENANT, "synthetic-route-operator")


def seed_document(index: int, company: str, raw_label: str, price: str) -> None:
    document_id = f"route-doc-{index}"
    item_id = f"route-item-{index}"
    source_id = f"route-source-{index}"
    filename = f"SYNTHETIC_ROUTE_{index}.xlsx"
    book = Workbook()
    sheet = book.active
    sheet.title = "Synthetic"
    sheet.append(["原文費目", "単価", "通貨"])
    sheet.append([raw_label, price, "USD"])
    output = BytesIO()
    book.save(output)
    book.close()
    data = output.getvalue()
    (STORAGE / filename).write_bytes(data)
    digest = sha256(data).hexdigest()
    store.register_document(actor, CASE_ID, document_id=document_id,
                            original_filename=filename,
                            media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                            size_bytes=len(data), sha256=digest, storage_key=filename,
                            blocked_reason="SYNTHETIC_FIXTURE_NO_READER")
    observation = Observation(TENANT, item_id, CASE_ID, document_id, source_id,
                              company, raw_label, price, "USD",
                              Context("SEA", "40FT", "CONTAINER", "ORIGIN", "STANDARD"), True)
    source_ref = {"synthetic": True, "document_id": document_id,
                  "document_revision": 1, "document_sha256": digest,
                  "source_block_ids": [source_id], "kind": "EXCEL_RANGE",
                  "locator_status": "VERIFIED", "locator": {"sheet_name": "Synthetic", "A1_range": "A2:C2"}}
    store.add_synthetic_lines(actor, document_id, [SyntheticLine(
        observation=observation, source_ref=source_ref,
        ai_suggested_name=None, raw_value=price)])


if not any(case["case_id"] == CASE_ID for case in store.list_cases(actor)):
    service.create_case(actor, CASE_ID, "合成見積・URL確認")
    for number, company, label, amount in [
        (1, "A社", "O/F 40'", "1900"),
        (2, "B社", "OCEAN FREIGHT 40'", "1850"),
        (3, "C社", "海上運賃 40'", "2050"),
    ]:
        seed_document(number, company, label, amount)

runtime = IntegrationRuntime(repo, STORAGE, reader=None, extractor=None, reader_mode="REAL_SDK")
app = create_app(DSN, STORAGE, tenant_id=TENANT, integrations=runtime)
