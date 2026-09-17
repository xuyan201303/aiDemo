"""Demo extension checks. All inputs here are INJECTED_TEST_CASE data.

PG tests run only against the caller's dedicated disposable SANZO_TEST_DSN.
No real PDF/Excel is read and no SDK or AI is called by this suite.
"""
from __future__ import annotations

from dataclasses import replace
import os
import unittest
from uuid import uuid4

from sanzo_core.demo_store import DemoStore, MANUAL_ACKNOWLEDGMENT
from sanzo_core.domain import (
    Context, CorrectionCommand, ManualConfirmedLine, Observation, Principal,
    RevokeCommand, ScopeError, SyntheticLine,
)
from sanzo_core.postgres import PostgresRepository
from sanzo_core.service import ReviewService

DSN = os.environ.get("SANZO_TEST_DSN")


def manual_data(**changes):
    value = {
        "raw_label": "送货钱", "unit_price": "10", "currency": "CNY",
        "forwarder_id": "INJECTED_COMPANY_A",
        "context": {
            "transport_mode": "ROAD", "equipment": "N/A",
            "billing_unit": "PER_DELIVERY", "charge_scope": "DELIVERY_ONLY",
            "extra_terms": "N/A",
        },
        "source_acknowledgment": MANUAL_ACKNOWLEDGMENT,
    }
    value.update(changes)
    return value


class DemoInputTests(unittest.TestCase):
    def setUp(self):
        obs = Observation(
            "injected-tenant", "item", "case", "document", "source",
            "INJECTED_COMPANY_A", "送货钱", "20", "CNY",
            Context("ROAD", "N/A", "PER_DELIVERY", "DELIVERY_ONLY", "N/A"),
        )
        self.line = ManualConfirmedLine(obs, {
            "origin": "MANUAL_CONFIRMED", "document_id": "document",
            "source_block_ids": ["source"], "document_sha256": "a" * 64,
            "manual_confirmed_by": "injected-editor", "locator_status": "UNAVAILABLE",
            "locator": None,
        })

    def test_manual_origin_and_original_facts_are_explicit(self):
        self.line.validate()
        self.assertEqual(self.line.input_kind, "MANUAL_CONFIRMED")
        self.assertEqual(self.line.observation.unit_price, "20")
        self.assertIsNone(self.line.ai_suggested_name)

    def test_manual_cannot_claim_sdk_ai_or_invent_coordinates(self):
        for line in (
            replace(self.line, input_kind="SDK_PARSED"),
            replace(self.line, ai_suggested_name="Manufactured AI"),
            replace(self.line, source_ref={**self.line.source_ref, "locator": {"page_number": 1}}),
            replace(self.line, source_ref={**self.line.source_ref, "document_id": "foreign"}),
        ):
            with self.subTest(line=line), self.assertRaises(ValueError):
                line.validate()

    def test_client_ready_and_source_fields_rejected_before_database_access(self):
        store = DemoStore(None)
        actor = Principal("injected", "editor")
        for changes in (
            {"source_ready": True}, {"tenant_id": "foreign"},
            {"source_ref": {}}, {"item_id": "client"},
            {"source_acknowledgment": ""},
            {"context": {**manual_data()["context"], "ready": True}},
        ):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                store.add_manual_line(actor, "document", manual_data(**changes))


@unittest.skipUnless(DSN, "NOT_RUN: no dedicated SANZO_TEST_DSN configured")
class DemoStorePostgresTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        PostgresRepository(DSN).migrate()

    def setUp(self):
        self.actor = Principal("demo-test-" + uuid4().hex, "injected-editor")
        self.repo = PostgresRepository(DSN)
        self.service = ReviewService(self.repo)
        self.store = DemoStore(self.repo)
        self.document = self.new_document()

    def new_document(self, *, actor=None, case_id=None, sha256="a" * 64):
        actor = actor or self.actor
        case_id = case_id or uuid4().hex
        self.service.create_case(actor, case_id, "INJECTED_TEST_CASE")
        return self.store.register_document(
            actor, case_id, document_id=uuid4().hex,
            original_filename="INJECTED_TEST_CASE.xlsx",
            media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            size_bytes=1024, sha256=sha256, storage_key=uuid4().hex + ".xlsx",
        )

    def add(self, document=None, **changes):
        return self.store.add_manual_line(
            self.actor, (document or self.document)["document_id"], manual_data(**changes)
        )

    def correct(self, item, **changes):
        saved = self.service.get_item(self.actor, item["item_id"])
        current = self.store.list_items(self.actor, saved["case_id"])
        current = next(row for row in current if row["item_id"] == item["item_id"])
        return self.service.correct(self.actor, CorrectionCommand(
            item_id=item["item_id"], expected_item_version=saved["version"],
            expected_case_revision=saved["case_revision"],
            expected_memory_version=current["current_memory_version"],
            idempotency_key=uuid4().hex, **changes,
        ))

    def test_document_stays_sdk_blocked_after_manual_confirmation(self):
        item = self.add()
        self.assertEqual(item["input_kind"], "MANUAL_CONFIRMED")
        self.assertEqual(item["saved_status"], "AI_REQUIRED")
        self.assertEqual(item["source_ref"]["origin"], "MANUAL_CONFIRMED")
        self.assertEqual(item["source_ref"]["document_sha256"], self.document["sha256"])
        self.assertEqual(item["source_ref"]["locator_status"], "UNAVAILABLE")
        self.assertIsNone(item["source_ref"]["locator"])
        self.assertEqual(self.store.get_document(self.actor, self.document["document_id"])["state"], "BLOCKED")

    def test_two_rounds_persist_current_price_currency_source_and_saved_name(self):
        first = self.add()
        learned = self.correct(first, new_fee_name="配送费")
        second_document = self.new_document(sha256="b" * 64)
        second = self.add(
            second_document, unit_price="20.00", currency="USD",
            quotation_date="2026-09-11", valid_until="2026-10-11",
        )
        self.assertEqual(second["current_fee_id"], learned["fee_id"])
        self.assertEqual(second["current_fee_name"], "配送费")
        self.assertEqual(second["current_price"], "20.00")
        self.assertEqual(second["currency"], "USD")
        self.assertEqual(second["source_ref"]["document_sha256"], "b" * 64)
        self.assertEqual(second["quotation_date"], "2026-09-11")
        self.assertEqual(second["saved_status"], "MEMORY_APPLIED")
        self.assertEqual(second["initial_classification"]["memory_version"], 1)
        restarted = DemoStore(PostgresRepository(DSN)).list_items(
            self.actor, second_document["case_id"]
        )[0]
        self.assertEqual(restarted, second)
        self.assertEqual(self.service.get_item(self.actor, first["item_id"])["current_price"], "10")

    def test_rule_change_cannot_reclassify_saved_historical_table(self):
        first = self.add()
        learned = self.correct(first, new_fee_name="配送费")
        second_document = self.new_document()
        second = self.add(second_document, unit_price="20")
        self.correct(first, new_fee_name="配送及安装费", replace_existing_mapping=True)
        saved = self.store.list_items(self.actor, second_document["case_id"])[0]
        self.assertEqual(saved["current_fee_id"], learned["fee_id"])
        self.assertEqual(saved["current_fee_name"], "配送费")
        self.assertEqual(saved["current_price"], "20")
        self.assertEqual(saved["initial_classification"], second["initial_classification"])
        self.assertEqual(saved["current_memory_version"], 2)
        third = self.add(self.new_document(), unit_price="30")
        self.assertEqual(third["current_fee_name"], "配送及安装费")

    def test_incomplete_context_never_becomes_wildcard_or_learning(self):
        item = self.add(context={**manual_data()["context"], "equipment": None})
        self.assertEqual(item["saved_status"], "REVIEW_REQUIRED")
        result = self.correct(item, new_fee_name="本次人工费目")
        self.assertEqual(result["memory_effect"], "NOT_ELIGIBLE_CONTEXT_OR_SOURCE")
        next_item = self.add(self.new_document())
        self.assertIsNone(next_item["current_fee_id"])
        self.assertEqual(next_item["current_memory_version"], 0)

    def test_company_equipment_and_tenant_scopes_remain_separate(self):
        learned = self.correct(self.add(), new_fee_name="配送费")
        for changes in (
            {"forwarder_id": "INJECTED_COMPANY_B"},
            {"context": {**manual_data()["context"], "equipment": "40FT"}},
        ):
            with self.subTest(changes=changes):
                item = self.add(self.new_document(), **changes)
                self.assertIsNone(item["current_fee_id"])
                self.assertEqual(item["saved_status"], "AI_REQUIRED")
        other = Principal("demo-other-" + uuid4().hex, "injected-other")
        document = self.new_document(actor=other)
        item = self.store.add_manual_line(other, document["document_id"], manual_data())
        self.assertIsNone(item["current_fee_id"])
        with self.assertRaises(ScopeError):
            self.store.get_document(other, self.document["document_id"])
        with self.assertRaises(ScopeError):
            self.store.add_manual_line(other, self.document["document_id"], manual_data())
        restricted = replace(self.actor, allowed_case_ids=frozenset())
        self.assertEqual(self.store.list_cases(restricted), [])
        with self.assertRaises(ScopeError):
            self.store.list_items(restricted, self.document["case_id"])
        self.assertIn(learned["fee_id"], [fee["fee_id"] for fee in self.store.list_fees(self.actor)])

    def test_amount_only_and_unconfirmed_manual_entry_do_not_create_learning(self):
        first = self.add()
        self.correct(first, unit_price="12")
        self.assertEqual(self.store.list_fees(self.actor), [])
        second = self.add(self.new_document(), unit_price="20")
        self.assertEqual(second["saved_status"], "AI_REQUIRED")
        self.assertEqual(second["current_price"], "20")
        self.assertEqual(second["current_memory_version"], 0)

    def test_revocation_blocks_new_reuse_but_preserves_old_snapshot(self):
        first = self.add()
        self.correct(first, new_fee_name="配送费")
        document = self.new_document()
        second = self.add(document)
        saved = self.service.get_item(self.actor, first["item_id"])
        self.service.revoke_memory(self.actor, RevokeCommand(
            item_id=first["item_id"], expected_case_revision=saved["case_revision"],
            expected_item_version=saved["version"], expected_memory_version=1,
            idempotency_key=uuid4().hex, reason="INJECTED_TEST_CASE revoke",
        ))
        third = self.add(self.new_document())
        self.assertEqual(third["saved_status"], "REVIEW_REQUIRED")
        self.assertEqual(third["initial_classification"]["reason"], "MEMORY_REVOKED")
        self.assertIsNone(third["current_fee_id"])
        old = self.store.list_items(self.actor, document["case_id"])[0]
        self.assertEqual(old["current_fee_id"], second["current_fee_id"])
        self.assertEqual(old["current_memory_status"], "REVOKED")

    def synthetic_line(self, item_id):
        observation = Observation(
            self.actor.tenant_id, item_id, self.document["case_id"],
            self.document["document_id"], uuid4().hex, "INJECTED_COMPANY_A",
            "送货钱", "10", "CNY", Context(**manual_data()["context"]),
        )
        return SyntheticLine(observation, {
            "synthetic": True, "document_id": self.document["document_id"],
            "document_sha256": self.document["sha256"],
            "source_block_ids": [observation.source_id],
        }, ai_suggested_name="配送料")

    def test_synthetic_batch_failure_rolls_back_rows_snapshots_and_case_revision(self):
        before = self.store.get_case(self.actor, self.document["case_id"])
        good = self.synthetic_line(uuid4().hex)
        bad = replace(self.synthetic_line(uuid4().hex), input_kind="SDK_PARSED")
        with self.assertRaises(ValueError):
            self.store.add_synthetic_lines(self.actor, self.document["document_id"], [good, bad])
        self.assertEqual(self.store.list_items(self.actor, self.document["case_id"]), [])
        self.assertEqual(self.store.get_case(self.actor, self.document["case_id"]), before)
        rows = self.store.add_synthetic_lines(self.actor, self.document["document_id"], [good])
        self.assertEqual(rows[0]["input_kind"], "SYNTHETIC_STRUCTURED")
        self.assertEqual(rows[0]["ai_suggested_name"], "配送料")
        self.assertIsNone(rows[0]["current_fee_id"])

    def test_duplicate_batch_is_rejected_without_second_case_revision(self):
        from sanzo_core.domain import ConflictError
        good = self.synthetic_line(uuid4().hex)
        self.store.add_synthetic_lines(self.actor, self.document["document_id"], [good])
        before = self.store.get_case(self.actor, self.document["case_id"])
        with self.assertRaises(ConflictError):
            self.store.add_synthetic_lines(self.actor, self.document["document_id"], [good])
        self.assertEqual(self.store.get_case(self.actor, self.document["case_id"]), before)
        self.assertEqual(len(self.store.list_items(self.actor, self.document["case_id"])), 1)

    def test_case_snapshot_excludes_upload_waiting_on_its_case_lock(self):
        from concurrent.futures import ThreadPoolExecutor, TimeoutError
        from threading import Event

        self.add()
        reader_holds_lock, release_reader, writer_started = Event(), Event(), Event()
        owner = self

        class PausedSnapshot(DemoStore):
            def _item(self, conn, actor, item_id):
                reader_holds_lock.set()
                if not release_reader.wait(timeout=10):
                    raise RuntimeError("INJECTED_TEST_CASE snapshot coordination timeout")
                return super()._item(conn, actor, item_id)

        def register_more():
            writer_started.set()
            return owner.store.register_document(
                owner.actor, owner.document["case_id"], document_id=uuid4().hex,
                original_filename="INJECTED_CONCURRENT_UPLOAD.pdf", media_type="application/pdf",
                size_bytes=100, sha256="c" * 64, storage_key=uuid4().hex + ".pdf",
            )

        with ThreadPoolExecutor(max_workers=2) as executor:
            reading = executor.submit(
                PausedSnapshot(self.repo).case_snapshot, self.actor, self.document["case_id"]
            )
            writing = None
            try:
                self.assertTrue(reader_holds_lock.wait(timeout=10))
                writing = executor.submit(register_more)
                self.assertTrue(writer_started.wait(timeout=10))
                with self.assertRaises(TimeoutError, msg="Upload cannot commit while snapshot owns case lock"):
                    writing.result(timeout=0.2)
            finally:
                release_reader.set()
            snapshot = reading.result(timeout=10)
            writing.result(timeout=10)
        self.assertEqual(len(snapshot["documents"]), 1)
        self.assertEqual(snapshot["documents"][0]["document_id"], self.document["document_id"])
        self.assertEqual(snapshot["case"]["case_revision"], snapshot["items"][0]["case_revision"])
        after = self.store.case_snapshot(self.actor, self.document["case_id"])
        self.assertEqual(len(after["documents"]), 2)
        self.assertEqual(after["case"]["case_revision"], snapshot["case"]["case_revision"] + 1)



if __name__ == "__main__":
    unittest.main(verbosity=2)
