"""Actual sample upload/fixture/preview HTTP checks; OpenAI and SDK NOT_RUN.

Only first-round originals enter tasks here. No AI result, correction or round2
answer is injected. The expected outcome is persistent BLOCKED after raw read.
"""
from hashlib import sha256
from io import BytesIO
import os
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from uuid import uuid4

from fastapi.testclient import TestClient
from openpyxl import load_workbook
from sanzo_core.postgres import PostgresRepository
from sanzo_demo.app import create_app
from sanzo_demo.configuration import assemble_runtime
from sanzo_demo.ingestion import IntegrationRuntime
from test_demo_http import xlsx_bytes

DSN = os.environ.get("SANZO_TEST_DSN")
ROOT = Path(__file__).resolve().parents[1]


@unittest.skipUnless(DSN, "NOT_RUN: isolated PostgreSQL required")
class MockHTTPBoundaryTests(unittest.TestCase):
    def setUp(self):
        self.folder = TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        self.tenant = "mock-http-" + uuid4().hex
        self.repo = PostgresRepository(DSN)
        self.repo.migrate()

    def client(self, mode="MOCK_SDK", *, missing_real_reader=False):
        # Explicit missing-port mechanism tests still verify no fixture fallback;
        # normal Real assembly now supplies the installed DocumentSdkAdapter.
        pipeline = (IntegrationRuntime(self.repo, self.folder.name, reader_mode=mode)
                    if missing_real_reader else
                    assemble_runtime(self.repo, self.folder.name, reader_mode=mode))
        return TestClient(create_app(DSN, self.folder.name, tenant_id=self.tenant,
                                    integrations=pipeline))

    def upload(self, client, case, filename, content):
        r = client.post(f"/api/cases/{case}/documents",
                        files={"file": (filename, content)})
        self.assertEqual(r.status_code, 201, r.text)
        return r.json()

    def process(self, client, document):
        r = client.post(f"/api/documents/{document['document_id']}/process",
                        json={"idempotency_key": uuid4().hex})
        self.assertEqual(r.status_code, 202, r.text)
        return client.get("/api/tasks/" + r.json()["task_id"]).json()

    def test_first_round_raw_reads_previews_export_and_restart_keep_ai_not_run(self):
        with self.client() as client:
            status = client.get("/api/status").json()
            self.assertTrue(status["reader"]["ready"])
            self.assertFalse(status["sdk"]["ready"])
            self.assertFalse(status["ai"]["ready"])
            self.assertFalse(status["debug_manual"])
            self.assertNotIn("SDK の絶対パス", str(status["missing_conditions"]))
            case = client.post("/api/cases", json={"name": "MOCK_RAW_BOUNDARY_NO_AI"}).json()["case_id"]
            task_ids = []
            for name in ("01_A社_初回見積.xlsx", "02_B社_初回見積.pdf"):
                raw = (ROOT / "demo_materials/samples" / name).read_bytes()
                document = self.upload(client, case, name, raw)
                task = self.process(client, document)
                task_ids.append(task["task_id"])
                self.assertEqual(task["state"], "BLOCKED", task)
                self.assertEqual(task["error_code"], "OPENAI_AUTHORIZATION_NOT_CONFIRMED")
                event = next(e for e in task["events"] if e["state"] == "EXTRACTING")
                evidence = event["metadata"]["read_evidence"]
                self.assertEqual(evidence["origin"], "MOCK_SDK")
                self.assertEqual(event["metadata"]["real_sdk_calls"], 0)
                self.assertFalse(event["metadata"]["ai_call_requested"])
                self.assertEqual(task["events"][-1]["metadata"]["ai_calls"], 0)
                self.assertEqual(sha256(client.get(f"/api/documents/{document['document_id']}/original").content).hexdigest(), document["sha256"])
                source = next(b["source_ref"] for b in evidence["sources"]
                              if b["source_ref"]["evidence_unit"]["field_name"] == "raw_label")
                loc = source["locator"]
                params = {"page": loc["page_number"]} if name.endswith(".pdf") else {
                    "sheet": loc["sheet_name"], "cell": loc["A1_range"]}
                preview = client.get(f"/api/documents/{document['document_id']}/preview", params=params)
                self.assertEqual(preview.status_code, 200)
                if name.endswith(".pdf"):
                    image = client.get(f"/api/documents/{document['document_id']}/pages/{loc['page_number']}.png")
                    self.assertTrue(image.content.startswith(b"\x89PNG"))
                else:
                    self.assertIn(loc["sheet_name"], preview.text)
                    self.assertIn('class="source-cell" data-cell="' + loc["A1_range"] + '"', preview.text)
                self.assertEqual(client.post(f"/api/documents/{document['document_id']}/manual-lines", json={}).status_code, 403)
            snapshot = client.get(f"/api/cases/{case}").json()
            self.assertEqual(snapshot["items"], [])
            data = client.get(f"/api/cases/{case}/export.xlsx").content
            book = load_workbook(BytesIO(data), read_only=True)
            try:
                text = "\n".join(str(c.value) for s in book for row in s for c in row)
                self.assertIn("デモ用解析データ／SDK未接続", text)
                self.assertIn("BLOCKED", text)
            finally:
                book.close()
            with self.repo.transaction() as conn:
                self.assertEqual(conn.execute("SELECT count(*) AS n FROM sanzo_core.memory_slots WHERE tenant_id=%s", (self.tenant,)).fetchone()["n"], 0)
        # New app/repository connections preserve the tasks and zero learning.
        with self.client() as reopened:
            snapshot = reopened.get(f"/api/cases/{case}").json()
            self.assertEqual({t["task_id"] for t in snapshot["tasks"]}, set(task_ids))
            self.assertTrue(all(t["state"] == "BLOCKED" for t in snapshot["tasks"]))
            self.assertEqual(snapshot["items"], [])

    def test_unsupported_valid_upload_is_explicitly_rejected_without_ai(self):
        with self.client() as client:
            case = client.post("/api/cases", json={"name": "UNSUPPORTED_MOCK_BOUNDARY"}).json()["case_id"]
            doc = self.upload(client, case, "renamed.xlsx", xlsx_bytes())
            task = self.process(client, doc)
            self.assertEqual(task["state"], "FAILED")
            self.assertIn("当前模拟模式仅支持三份指定样例", task["error_code"])
            self.assertEqual(client.get(f"/api/cases/{case}").json()["items"], [])

    def test_real_mode_keeps_sdk_missing_and_never_loads_fixture(self):
        with self.client("REAL_SDK", missing_real_reader=True) as client:
            case = client.post("/api/cases", json={"name": "REAL_NO_FALLBACK"}).json()["case_id"]
            raw = (ROOT / "demo_materials/samples/01_A社_初回見積.xlsx").read_bytes()
            task = self.process(client, self.upload(client, case, "sample.xlsx", raw))
            self.assertEqual(task["state"], "BLOCKED")
            self.assertFalse(any(e["state"] == "EXTRACTING" for e in task["events"]))
            self.assertEqual(client.get(f"/api/cases/{case}").json()["items"], [])


if __name__ == "__main__":
    unittest.main()
