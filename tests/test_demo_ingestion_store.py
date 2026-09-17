"""MECHANISM_TEST only: injected observations, real dedicated PostgreSQL.

No file parser or SDK/model call occurs. These tests do not establish live
three-file integration acceptance; all namespaces and inputs are synthetic.
"""
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, replace
import os
import unittest
from uuid import uuid4

from sanzo_core.demo_store import DemoStore, MANUAL_ACKNOWLEDGMENT
from sanzo_core.domain import Context, CorrectionCommand, Observation, Principal, ScopeError, ConflictError
from sanzo_core.ingestion_store import IngestionStore, _json
from sanzo_core.postgres import PostgresRepository
from sanzo_core.service import ReviewService

DSN = os.environ.get("SANZO_TEST_DSN")


@dataclass(frozen=True)
class InjectedObservation:
    """Internal repository seam, deliberately not the real SDK boundary validator."""
    observation: Observation
    source_ref: dict
    ai_suggested_name: str | None = "MECHANISM_AI_SUGGESTION"
    raw_value: str | None = "10"
    quotation_date: str | None = None
    valid_until: str | None = None
    input_kind: str = "SDK_OBSERVED"

    def validate(self):
        self.observation.validate()
        if self.input_kind != "SDK_OBSERVED":
            raise ValueError("Invalid mechanism input type")


class IngestionMetadataTests(unittest.TestCase):
    def test_credentials_are_not_persistable_metadata(self):
        for data in ({"api_key": "INJECTED_NOT_A_KEY"},
                     {"attempts": [{"Authorization": "INJECTED_NOT_A_KEY"}]}):
            with self.subTest(data=data), self.assertRaises(ValueError):
                _json(data, "test")

    def test_evidence_limit_is_enforced(self):
        with self.assertRaises(ValueError):
            _json({"text": "123456789"}, "test", maximum=5)


@unittest.skipUnless(DSN, "NOT_RUN: dedicated PostgreSQL SANZO_TEST_DSN missing")
class IngestionPostgresTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        PostgresRepository(DSN).migrate()

    def setUp(self):
        self.actor = Principal("ingestion-mechanism-" + uuid4().hex, "injected-operator")
        self.repo = PostgresRepository(DSN)
        self.demo = DemoStore(self.repo)
        self.service = ReviewService(self.repo)
        self.store = IngestionStore(self.repo)
        self.doc = self.document()

    def document(self):
        case_id = uuid4().hex
        self.service.create_case(self.actor, case_id, "MECHANISM_TEST_NO_REAL_SDK_AI")
        return self.demo.register_document(
            self.actor, case_id, document_id=uuid4().hex,
            original_filename="MECHANISM_NOT_AN_ACTUAL_FILE.xlsx",
            media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            size_bytes=99, sha256="a" * 64, storage_key=uuid4().hex + ".xlsx",
        )

    def fixture(self, doc=None, price="10", equipment="20FT"):
        doc = doc or self.doc
        source_id = uuid4().hex
        ref = {"origin": "SDK_OBSERVED", "document_id": doc["document_id"],
               "document_revision": 1, "document_sha256": doc["sha256"],
               "source_block_ids": [source_id], "kind": "EXCEL_RANGE",
               "locator_status": "UNAVAILABLE", "locator": None}
        observation = Observation(
            self.actor.tenant_id, uuid4().hex, doc["case_id"], doc["document_id"],
            source_id, "MECHANISM_COMPANY", "MECHANISM_RAW_LABEL", price, "JPY",
            Context("SEA", equipment, "PER_CONTAINER", "DESTINATION", "N/A"),
        )
        line = InjectedObservation(observation, ref, raw_value=price)
        evidence = {
            "origin": "MECHANISM_TEST", "document_sha256": doc["sha256"],
            "sources": [{"source_id": source_id, "raw_text": "MECHANISM_RAW_LABEL " + price,
                         "source_ref": ref}],
        }
        return line, evidence

    def saving_task(self, doc=None):
        task = self.store.start_task(self.actor, (doc or self.doc)["document_id"], uuid4().hex)
        for state in ("READING", "EXTRACTING", "SAVING"):
            self.store.transition(self.actor, task["task_id"], state)
        return task["task_id"]

    def finish(self, task_id, line, evidence):
        return self.store.finish(self.actor, task_id, [line], [], evidence,
                                 {"origin": "MECHANISM_TEST", "actual_api_calls": 0})

    def test_start_idempotency_replay_and_same_key_different_document_conflict(self):
        key = uuid4().hex
        task = self.store.start_task(self.actor, self.doc["document_id"], key)
        same = self.store.start_task(self.actor, self.doc["document_id"], key)
        self.assertEqual(task["task_id"], same["task_id"])
        self.assertTrue(same["replayed"])
        self.assertEqual(len(same["events"]), 1)
        other = self.document()
        with self.assertRaises(ConflictError):
            self.store.start_task(self.actor, other["document_id"], key)

    def test_concurrent_different_requests_create_only_one_active_task(self):
        with ThreadPoolExecutor(max_workers=2) as pool:
            calls = [pool.submit(self.store.start_task, self.actor, self.doc["document_id"], uuid4().hex)
                     for _ in range(2)]
            tasks = [future.result(timeout=10) for future in calls]
        self.assertEqual(tasks[0]["task_id"], tasks[1]["task_id"])
        self.assertEqual(len(self.store.list_tasks(self.actor, self.doc["case_id"])), 1)
        self.assertEqual(sum(not task["replayed"] for task in tasks), 1)

    def test_final_task_deduplicates_new_request_and_retains_original_document(self):
        line, evidence = self.fixture()
        task_id = self.saving_task()
        completed = self.finish(task_id, line, evidence)
        self.assertEqual(completed["state"], "REVIEW_REQUIRED")
        self.assertEqual([event["state"] for event in completed["events"]],
                         ["QUEUED", "READING", "EXTRACTING", "SAVING", "REVIEW_REQUIRED"])
        after = self.store.start_task(self.actor, self.doc["document_id"], uuid4().hex)
        self.assertEqual(after["task_id"], task_id)
        self.assertTrue(after["deduplicated"])
        self.assertEqual(len(self.demo.list_items(self.actor, self.doc["case_id"])), 1)
        self.assertEqual(self.demo.get_document(self.actor, self.doc["document_id"])["state"], "BLOCKED")
        self.assertEqual(self.store.document_states(self.actor, self.doc["case_id"])[self.doc["document_id"]]["state"], "REVIEW_REQUIRED")
        with self.assertRaises(ConflictError):
            self.store.transition(self.actor, task_id, "READING")

    def test_mid_import_failure_rolls_back_items_sources_snapshots_and_revision(self):
        task_id = self.saving_task()
        line, evidence = self.fixture()
        before = self.demo.get_case(self.actor, self.doc["case_id"])
        def fail(stage):
            if stage == "after_item":
                raise RuntimeError("INJECTED_MID_IMPORT_FAILURE")
        with self.assertRaisesRegex(RuntimeError, "INJECTED_MID_IMPORT_FAILURE"):
            IngestionStore(self.repo, fail).finish(self.actor, task_id, [line], [], evidence,
                                                 {"origin": "MECHANISM_TEST"})
        self.assertEqual(self.demo.list_items(self.actor, self.doc["case_id"]), [])
        self.assertEqual(self.store.get_task_sources(self.actor, task_id), [])
        self.assertEqual(self.demo.get_case(self.actor, self.doc["case_id"]), before)
        self.assertEqual(self.store.get_task(self.actor, task_id)["state"], "SAVING")
        completed = self.finish(task_id, line, evidence)
        self.assertEqual(completed["result_json"]["item_ids"], [line.observation.item_id])

    def test_sources_foreign_hash_and_missing_block_reject_without_writes(self):
        task_id = self.saving_task()
        line, evidence = self.fixture()
        for changed_line, changed_evidence in (
            (line, {**evidence, "document_sha256": "b" * 64}),
            (replace(line, source_ref={**line.source_ref, "source_block_ids": ["foreign"]}), evidence),
            (line, {**evidence, "sources": [{**evidence["sources"][0],
                "source_ref": {**line.source_ref, "document_id": "foreign"}}]}),
        ):
            with self.subTest(evidence=changed_evidence), self.assertRaises(ValueError):
                self.finish(task_id, changed_line, changed_evidence)
        self.assertEqual(self.demo.list_items(self.actor, self.doc["case_id"]), [])
        self.assertEqual(self.store.get_task_sources(self.actor, task_id), [])

    def test_restart_retains_failed_history_and_retry_uses_new_attempt(self):
        task = self.store.start_task(self.actor, self.doc["document_id"], uuid4().hex)
        self.store.transition(self.actor, task["task_id"], "READING")
        ids = IngestionStore(PostgresRepository(DSN)).interrupt_unfinished(self.actor)
        self.assertEqual(ids, [task["task_id"]])
        old = self.store.get_task(self.actor, task["task_id"])
        self.assertEqual(old["state"], "INTERRUPTED")
        retry = self.store.start_task(self.actor, self.doc["document_id"], uuid4().hex)
        self.assertNotEqual(retry["task_id"], task["task_id"])
        self.assertEqual(len(self.store.list_tasks(self.actor, self.doc["case_id"])), 2)
        self.store.transition(self.actor, retry["task_id"], "BLOCKED", error_code="SDK_CONTRACT_MISSING")
        self.assertEqual(self.store.get_task(self.actor, task["task_id"]), old)

    def test_actual_saved_learning_is_reused_with_new_price_and_old_case_unchanged(self):
        line, evidence = self.fixture()
        self.finish(self.saving_task(), line, evidence)
        item = self.demo.list_items(self.actor, self.doc["case_id"])[0]
        correction = self.service.correct(self.actor, CorrectionCommand(
            item_id=item["item_id"], expected_case_revision=item["case_revision"],
            expected_item_version=item["version"], expected_memory_version=0,
            idempotency_key=uuid4().hex, new_fee_name="MECHANISM_CONFIRMED_FEE",
        ))
        old = self.demo.case_snapshot(self.actor, self.doc["case_id"])
        newdoc = self.document()
        newline, newevidence = self.fixture(newdoc, price="20")
        completed = self.finish(self.saving_task(newdoc), newline, newevidence)
        self.assertEqual(completed["state"], "AVAILABLE")
        item2 = self.demo.list_items(self.actor, newdoc["case_id"])[0]
        self.assertEqual(item2["current_fee_id"], correction["fee_id"])
        self.assertEqual(item2["current_price"], "20")
        self.assertEqual(item2["initial_classification"]["memory_version"], 1)
        match = completed["result_json"]["classification_results"][0]
        self.assertEqual(match["memory_version"], 1)
        self.assertTrue(match["learning_event_id"])
        self.assertEqual(self.demo.case_snapshot(self.actor, self.doc["case_id"]), old)
        self.assertEqual(item["observation"]["raw_label"], "MECHANISM_RAW_LABEL")
        self.assertNotEqual(item["observation"]["raw_label"], item["ai_suggested_name"])

    def test_manual_existing_items_not_overwritten_by_import(self):
        manual = self.demo.add_manual_line(self.actor, self.doc["document_id"], {
            "raw_label": "MANUAL_ORIGINAL", "unit_price": "7", "currency": "JPY",
            "forwarder_id": "MANUAL_COMPANY",
            "context": {"transport_mode": "SEA", "equipment": "20FT", "billing_unit": "PER_CONTAINER",
                        "charge_scope": "DESTINATION", "extra_terms": "N/A"},
            "source_acknowledgment": MANUAL_ACKNOWLEDGMENT,
        })
        line, evidence = self.fixture()
        self.finish(self.saving_task(), line, evidence)
        rows = self.demo.list_items(self.actor, self.doc["case_id"])
        old = next(row for row in rows if row["item_id"] == manual["item_id"])
        self.assertEqual(old["observation"], manual["observation"])
        self.assertEqual(old["current_price"], "7")
        self.assertEqual(old["version"], 1)
        self.assertEqual(len(rows), 2)

    def test_task_and_sources_are_case_scoped_and_snapshot_has_current_states(self):
        line, evidence = self.fixture()
        task_id = self.saving_task()
        self.finish(task_id, line, evidence)
        forbidden = replace(self.actor, allowed_case_ids=frozenset())
        for action in (lambda: self.store.get_task(forbidden, task_id),
                       lambda: self.store.get_task_sources(forbidden, task_id),
                       lambda: self.store.case_snapshot(forbidden, self.doc["case_id"])):
            with self.assertRaises(ScopeError):
                action()
        snapshot = self.store.case_snapshot(self.actor, self.doc["case_id"])
        self.assertEqual(snapshot["documents"][0]["state"], "REVIEW_REQUIRED")
        self.assertEqual(snapshot["tasks"][0]["result_json"]["item_ids"],
                         [snapshot["items"][0]["item_id"]])
        self.assertEqual(snapshot["case"]["case_revision"], snapshot["items"][0]["case_revision"])
        self.assertEqual(self.store.get_task_sources(self.actor, task_id)[0]["raw_text"],
                         evidence["sources"][0]["raw_text"])

    def test_nonprice_issues_are_retained_without_fake_zero_items(self):
        task_id = self.saving_task()
        _, evidence = self.fixture()
        issue = {"code": "UNKNOWN_AMOUNT", "raw_value": "INCLUDED",
                 "source_id": evidence["sources"][0]["source_id"]}
        task = self.store.finish(self.actor, task_id, [], [issue], evidence,
                                 {"origin": "MECHANISM_TEST", "actual_api_calls": 0})
        self.assertEqual(task["state"], "REVIEW_REQUIRED")
        self.assertEqual(task["result_json"]["issues"], [issue])
        self.assertEqual(self.demo.list_items(self.actor, self.doc["case_id"]), [])

if __name__ == "__main__":
    unittest.main(verbosity=2)
