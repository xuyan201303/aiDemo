"""008 -> 009 upgrade on a dedicated, empty synthetic database only."""
import hashlib
import os
from pathlib import Path
import unittest
from uuid import uuid4

from psycopg import sql

from sanzo_core.demo_store import DemoStore
from sanzo_core.domain import Context, CorrectionCommand, Observation, Principal, SyntheticLine
from sanzo_core.postgres import PostgresRepository
from sanzo_core.service import ReviewService


@unittest.skipUnless(os.environ.get("SANZO_MIGRATION_TEST_DSN"), "Dedicated empty upgrade database required")
class ConfirmationMigrationTests(unittest.TestCase):
    def test_upgrade_preserves_008_business_and_history_and_is_incremental(self):
        dsn = os.environ["SANZO_MIGRATION_TEST_DSN"]
        self.assertIn("confirmation_completion_upgrade", dsn)
        repo = PostgresRepository(dsn)
        directory = Path(__file__).resolve().parents[1] / "backend/sanzo_core/migrations"
        with repo.transaction() as conn:
            self.assertIsNone(conn.execute("SELECT to_regnamespace('sanzo_core') AS n").fetchone()["n"])
            conn.execute("CREATE SCHEMA sanzo_core")
            conn.execute("CREATE TABLE sanzo_core.schema_migrations(version text PRIMARY KEY,sha256 text NOT NULL,applied_at timestamptz NOT NULL DEFAULT current_timestamp)")
            for path in sorted(directory.glob("00[1-8]_*.sql")):
                data = path.read_bytes()
                conn.execute(data.decode(), prepare=False)
                conn.execute("INSERT INTO sanzo_core.schema_migrations(version,sha256) VALUES(%s,%s)",
                             (path.name, hashlib.sha256(data).hexdigest()))
        actor = Principal("synthetic-upgrade", "migration-test")
        review, store = ReviewService(repo), DemoStore(repo)
        review.create_case(actor, "case", "SYNTHETIC 008 UPGRADE")
        doc = store.register_document(actor, "case", document_id="doc", original_filename="SYNTHETIC.xlsx",
            media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            size_bytes=1, sha256="a" * 64, storage_key="SYNTHETIC.xlsx")
        obs = Observation(actor.tenant_id, "item", "case", "doc", "source", "Synthetic A", "FEE", "1900", "USD",
                          Context("SEA", "40FT", "CONTAINER", "PORT", "NONE"), True)
        source = {"synthetic": True, "document_id": "doc", "document_revision": 1,
                  "document_sha256": "a" * 64, "source_block_ids": ["source"], "kind": "EXCEL_RANGE",
                  "locator_status": "VERIFIED", "locator": {"sheet_name": "SYNTHETIC", "A1_range": "B2"}}
        with repo.transaction() as conn:
            store._insert_line(conn, actor, doc, SyntheticLine(obs, source))
            revision = repo.advance_case(conn, actor.tenant_id, "case")
        review.correct(actor, CorrectionCommand(item_id="item", expected_case_revision=revision,
            expected_item_version=1, expected_memory_version=0, idempotency_key=uuid4().hex,
            new_fee_name="移行前に人が確認した費目"))
        with repo.transaction() as conn:
            tables = conn.execute("SELECT table_name FROM information_schema.tables WHERE table_schema='sanzo_core' ORDER BY table_name").fetchall()
            old = {}
            for row in tables:
                table = row["table_name"]
                columns = [r["column_name"] for r in conn.execute("SELECT column_name FROM information_schema.columns WHERE table_schema='sanzo_core' AND table_name=%s ORDER BY ordinal_position", (table,))]
                query = sql.SQL("SELECT {} FROM sanzo_core.{} ORDER BY 1,2").format(sql.SQL(",").join(map(sql.Identifier, columns)), sql.Identifier(table))
                old[table] = (query, conn.execute(query).fetchall())
        result = repo.migrate()
        self.assertEqual(result["applied"], ["009_quote_confirmations.sql"])
        with repo.transaction() as conn:
            for name, (query, before) in old.items():
                after = conn.execute(query).fetchall()
                if name == "schema_migrations":
                    after = [r for r in after if r["version"] != "009_quote_confirmations.sql"]
                self.assertEqual(after, before, name)
        self.assertEqual(repo.migrate()["applied"], [])


if __name__ == "__main__":
    unittest.main()
