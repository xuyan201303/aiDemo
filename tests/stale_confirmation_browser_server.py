"""Dedicated real-app/real-PG stale-name browser fixture server.

Only a disposable `*-stale-*` PostgreSQL host and sanzo_stale_test database are
accepted. Seed inputs are explicitly synthetic and use normal existing services;
no SDK, provider, credentials, original Demo files, or production database.
"""
import os
from hashlib import sha256
from pathlib import Path
from urllib.parse import urlsplit

import uvicorn
from openpyxl import Workbook
from psycopg import sql

from sanzo_core.demo_store import DemoStore
from sanzo_core.domain import Context, CorrectionCommand, Observation, Principal, SyntheticLine
from sanzo_core.postgres import PostgresRepository
from sanzo_core.service import ReviewService
from sanzo_demo.app import create_app

DSN = os.environ["SANZO_TEST_DSN"]
parsed = urlsplit(DSN)
if "-stale-" not in (parsed.hostname or "") or parsed.path != "/sanzo_stale_test":
    raise RuntimeError("Only the dedicated disposable stale-name test database is accepted")
repo = PostgresRepository(DSN)
repo.migrate()
store, service = DemoStore(repo), ReviewService(repo)
actor = Principal("stale-name-browser-test", "local-demo-operator")
storage = Path("/tmp/stale-name-originals")
storage.mkdir(exist_ok=True)
context = Context("ROAD", "TRUCK", "PER_DELIVERY", "DELIVERY_ONLY", "STANDARD")


def seed(case_id, *, fee_id=None, fee_name=None):
    service.create_case(actor, case_id, "合成データ・名称同時確認 " + case_id)
    document_id, item_id = "document-" + case_id, "item-" + case_id
    source = "source-" + case_id
    price = "9500" if case_id.endswith("-b") else "8000"
    raw = "SYNTHETIC ORIGINAL " + case_id
    path = storage / (case_id + ".xlsx")
    book = Workbook()
    book.active.title = "Synthetic"
    book.active.append(["Original label", "Current amount", "Currency"])
    book.active.append([raw, price, "JPY"])
    book.save(path)
    book.close()
    digest = sha256(path.read_bytes()).hexdigest()
    store.register_document(actor, case_id, document_id=document_id,
        original_filename="SYNTHETIC_" + case_id + ".xlsx",
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        size_bytes=path.stat().st_size, sha256=digest, storage_key=path.name)
    observation = Observation(actor.tenant_id, item_id, case_id, document_id,
        source, "SYNTHETIC COMPANY " + case_id, raw, price, "JPY", context, True)
    source_ref = {"synthetic": True, "document_id": document_id,
        "document_revision": 1, "document_sha256": digest, "source_block_ids": [source],
        "kind": "EXCEL_RANGE", "locator_status": "VERIFIED",
        "locator": {"sheet_name": "Synthetic", "A1_range": "A2"}}
    store.add_synthetic_lines(actor, document_id, [SyntheticLine(observation=observation,
        source_ref=source_ref, raw_value=price, ai_suggested_name="Synthetic unconfirmed proposal")])
    item = store.list_items(actor, case_id)[0]
    command = CorrectionCommand(item_id=item_id, expected_case_revision=item["case_revision"],
        expected_item_version=item["version"], expected_memory_version=0,
        idempotency_key="synthetic-seed-" + case_id, target_fee_id=fee_id, new_fee_name=fee_name)
    return service.correct(actor, command)["fee_id"]


if not store.list_cases(actor):
    for family, name in [("confirm", "確認対象 X"), ("cycle", "循環対象 X"), ("replay", "再送対象 X")]:
        identity = seed(family + "-a", fee_name=name)
        seed(family + "-b", fee_id=identity)
    seed("reclass-a", fee_name="分類先 X")
    seed("reclass-b", fee_name="元の分類")

app = create_app(DSN, storage, tenant_id=actor.tenant_id)


@app.get("/__test/audit")
def audit():
    # Read-only full logical snapshot restricted to this synthetic tenant.
    with repo.transaction() as conn:
        conn.execute("SET TRANSACTION READ ONLY")
        tables = conn.execute("""SELECT DISTINCT table_name FROM information_schema.columns
            WHERE table_schema='sanzo_core' AND column_name='tenant_id' ORDER BY table_name""").fetchall()
        return {row["table_name"]: conn.execute(sql.SQL(
            "SELECT * FROM sanzo_core.{} AS row_data WHERE tenant_id=%s ORDER BY to_jsonb(row_data)::text"
        ).format(sql.Identifier(row["table_name"])),
            (actor.tenant_id,)).fetchall() for row in tables}


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8765, access_log=False)
