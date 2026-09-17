"""MECHANISM_TEST: fixture-origin isolation, no SDK, AI or quotation samples.

The Postgres tests use only the caller's disposable SANZO_TEST_DSN. Trusted
in-process observed lines below are synthetic mechanism inputs, not live AI proof.
"""
from copy import deepcopy
from dataclasses import replace
import os
import unittest
from uuid import uuid4

import psycopg

from sanzo_core.demo_store import DemoStore
from sanzo_core.domain import (
    Context, CorrectionCommand, DecisionKind, Observation, Principal, RevokeCommand,
    SDKObservedLine, memory_key, memory_origin,
)
from sanzo_core.ingestion_store import IngestionStore
from sanzo_core.postgres import PostgresRepository
from sanzo_core.service import ReviewService

DSN = os.environ.get("SANZO_TEST_DSN")


def mock_source(document_id="document", source_id="source", digest="a" * 64):
    return {
        "origin": "MOCK_SDK", "extraction_origin": "MOCK_SDK",
        "fixture_version": "mechanism-origin-v1", "fixture_sha256": "b" * 64,
        "fixture_integrity": "VERIFIED", "document_id": document_id,
        "document_sha256": digest, "source_block_ids": [source_id],
        "kind": "PDF_PAGE", "locator_status": "PAGE_ONLY", "locator": {"page_number": 1},
    }


def observation():
    return Observation(
        "mechanism-tenant", "item", "case", "document", "source",
        "MECHANISM_COMPANY", "MECHANISM_RAW_LABEL", "17", "JPY",
        Context("SEA", "20FT", "PER_CONTAINER", "DESTINATION", "MECHANISM_TERMS"),
    )


def complete_mechanism_real_source(obs):
    """Explicit offline F1-shaped source, no actual SDK invocation claimed."""
    source={k:v for k,v in mock_source(obs.document_id,obs.source_id).items() if not k.startswith("fixture_")}
    source.update(origin="SDK_OBSERVED",extraction_origin="REAL_SDK",text_verification="VERIFIED")
    source["field_sources"]={}
    for name,value in {"raw_label":obs.raw_label,**vars(obs.context)}.items():
        if name=="ready":continue
        sid=obs.source_id if name=="raw_label" else obs.source_id+":"+name
        unit={"kind":"SDK_FIELD","scope":"LINE","field_name":name,"record_id":"explicit-mechanism-row"}
        source["field_sources"][name]={"value":value,"original_value":value,"source_block_id":sid,"evidence_unit":unit,"record_key":["PDF_PAGE",1,"explicit-mechanism-row"]}
    source["evidence_unit"]=source["field_sources"]["raw_label"]["evidence_unit"]
    return source


class MockOriginContractTests(unittest.TestCase):
    def test_mock_scope_keeps_v1_rules_and_real_uses_distinct_grounded_fee_policy(self):
        obs = observation()
        source = mock_source()
        key = memory_key(obs, source, "MOCK_SDK")
        self.assertEqual(key, "MOCK_SDK:v1:" + obs.memory_key())
        self.assertIsNone(memory_key(obs, {"origin": "SDK_OBSERVED",
                                          "extraction_origin": "REAL_SDK"}, "SDK_OBSERVED"))
        real_key=memory_key(obs,complete_mechanism_real_source(obs),"SDK_OBSERVED")
        self.assertTrue(real_key.startswith("fee-name-v2:"))
        self.assertNotEqual(real_key,key)
        changed = replace(obs, unit_price="23", currency="USD", item_id="next-item",
                          document_id="next-document", case_id="next-case")
        self.assertEqual(memory_key(changed, source, "MOCK_SDK"), key)
        for changed in (replace(obs, tenant_id="other"), replace(obs, forwarder_id="other"),
                        replace(obs, context=replace(obs.context, equipment="40FT"))):
            self.assertNotEqual(memory_key(changed, source, "MOCK_SDK"), key)

    def test_unknown_conditions_and_unverified_source_still_have_no_key(self):
        obs = observation()
        for changed in (replace(obs, source_ready=False),
                        replace(obs, context=replace(obs.context, equipment=None)),
                        replace(obs, context=replace(obs.context, ready=False)),
                        replace(obs, forwarder_id=None)):
            self.assertIsNone(memory_key(changed, mock_source(), "MOCK_SDK"))

    def test_mock_markers_and_fixture_proof_must_all_agree(self):
        source = mock_source()
        self.assertEqual(memory_origin(source, "MOCK_SDK"), "MOCK_SDK")
        for key in ("origin", "extraction_origin", "fixture_version",
                    "fixture_sha256", "fixture_integrity"):
            altered = deepcopy(source)
            del altered[key]
            with self.subTest(missing=key), self.assertRaises(ValueError):
                memory_origin(altered, "MOCK_SDK")
        for mode in (None, "SDK_OBSERVED", "MANUAL_CONFIRMED"):
            with self.subTest(mode=mode), self.assertRaises(ValueError):
                memory_origin(source, mode)
        source["fixture_integrity"] = "SDK_VERIFIED"
        with self.assertRaises(ValueError):
            memory_origin(source, "MOCK_SDK")

    def test_observed_line_preserves_explicit_mock_input_and_rejects_real_disguise(self):
        line = SDKObservedLine(observation(), mock_source(), raw_value="17",
                               input_kind="MOCK_SDK")
        line.validate()
        with self.assertRaises(ValueError):
            replace(line, input_kind="SDK_OBSERVED").validate()
        forged = deepcopy(line.source_ref)
        forged["origin"] = "SDK_OBSERVED"
        with self.assertRaises(ValueError):
            replace(line, source_ref=forged, input_kind="SDK_OBSERVED").validate()


@unittest.skipUnless(DSN, "NOT_RUN: dedicated PostgreSQL SANZO_TEST_DSN missing")
class MockOriginPostgresTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        PostgresRepository(DSN).migrate()

    def setUp(self):
        self.actor = Principal("mock-origin-mechanism-" + uuid4().hex, "mechanism-editor")
        self.repo = PostgresRepository(DSN)
        self.service = ReviewService(self.repo)
        self.demo = DemoStore(self.repo)

    def registered_line(self, *, mode="MOCK_SDK", price="17"):
        case_id, document_id, source_id = uuid4().hex, uuid4().hex, uuid4().hex
        self.service.create_case(self.actor, case_id, "MECHANISM_ORIGIN_ISOLATION")
        doc = self.demo.register_document(
            self.actor, case_id, document_id=document_id,
            original_filename="MECHANISM_NO_ACTUAL_FILE.pdf", media_type="application/pdf",
            size_bytes=123, sha256="a" * 64, storage_key=document_id + ".pdf")
        source = mock_source(document_id, source_id)
        if mode != "MOCK_SDK":
            source = {key: value for key, value in source.items() if not key.startswith("fixture_")}
            source.update(origin="SDK_OBSERVED", extraction_origin="REAL_SDK")
        obs = replace(observation(), tenant_id=self.actor.tenant_id, item_id=uuid4().hex,
                      case_id=case_id, document_id=document_id, source_id=source_id,
                      unit_price=price)
        if mode != "MOCK_SDK": source=complete_mechanism_real_source(obs)
        return doc, SDKObservedLine(obs, source, raw_value=price,
                                   ai_suggested_name="MECHANISM_UNCONFIRMED_AI", input_kind=mode)

    def insert(self, **kwargs):
        doc, line = self.registered_line(**kwargs)
        with self.repo.transaction() as conn:
            self.repo.case(conn, self.actor.tenant_id, doc["case_id"], lock=True)
            self.demo._insert_line(conn, self.actor, doc, line)
            self.repo.advance_case(conn, self.actor.tenant_id, doc["case_id"])
        return self.demo.list_items(self.actor, doc["case_id"])[0]

    def correct(self, item, **kwargs):
        current = self.demo.list_items(self.actor, item["case_id"])[0]
        defaults = dict(
            item_id=item["item_id"], expected_case_revision=current["case_revision"],
            expected_item_version=current["version"],
            expected_memory_version=current["current_memory_version"],
            idempotency_key=uuid4().hex,
        )
        defaults.update(kwargs)
        return self.service.correct(self.actor, CorrectionCommand(**defaults))

    def count(self, table):
        with self.repo.transaction() as conn:
            return conn.execute("SELECT count(*) AS n FROM sanzo_core." + table +
                                " WHERE tenant_id=%s", (self.actor.tenant_id,)).fetchone()["n"]

    def test_confirmed_mock_reuses_across_connection_but_never_real_same_tenant(self):
        real = self.insert(mode="SDK_OBSERVED", price="17")
        real_confirmation = self.correct(real, new_fee_name="MECHANISM_REAL_CONFIRMATION")
        first = self.insert(price="23")
        self.assertEqual(first["saved_status"], "AI_REQUIRED")
        self.assertIsNone(first["current_fee_id"])
        self.assertEqual(self.count("memory_slots"), 1)  # AI suggestion created none.
        mock_confirmation = self.correct(first, new_fee_name="MECHANISM_MOCK_CONFIRMATION")
        old = self.demo.case_snapshot(self.actor, first["case_id"])

        second = self.insert(price="31")
        restarted = DemoStore(PostgresRepository(DSN))
        saved = restarted.list_items(self.actor, second["case_id"])[0]
        self.assertEqual(saved["current_fee_id"], mock_confirmation["fee_id"])
        self.assertEqual(saved["current_price"], "31")
        self.assertEqual(saved["saved_status"], "MEMORY_APPLIED")
        self.assertEqual(saved["current_memory_origin"], "MOCK_SDK")
        self.assertEqual(self.demo.case_snapshot(self.actor, first["case_id"]), old)
        real_next = self.insert(mode="SDK_OBSERVED", price="47")
        self.assertEqual(real_next["current_fee_id"], real_confirmation["fee_id"])
        self.assertNotEqual(real_next["current_fee_id"], mock_confirmation["fee_id"])
        self.assertEqual(real_next["current_price"], "47")
        for table in ("memory_slots", "memory_versions", "memory_events", "correction_events"):
            with self.repo.transaction() as conn:
                origins = {row["origin"] for row in conn.execute(
                    "SELECT origin FROM sanzo_core." + table + " WHERE tenant_id=%s",
                    (self.actor.tenant_id,)).fetchall()}
            self.assertEqual(origins, {"REAL_SDK", "MOCK_SDK"})

    def test_amount_only_and_injected_rollback_preserve_mock_learning_atomicity(self):
        item = self.insert()
        amount = self.correct(item, unit_price="23")
        self.assertEqual(amount["memory_effect"], "UNCHANGED_AMOUNT_ONLY")
        self.assertEqual(self.count("memory_slots"), 0)
        item = self.demo.list_items(self.actor, item["case_id"])[0]
        events_before = self.count("correction_events")
        def fail(stage):
            if stage == "after_memory":
                raise RuntimeError("INJECTED_TEST_CASE_MOCK_ROLLBACK")
        broken = ReviewService(self.repo, fault_hook=fail)
        command = CorrectionCommand(
            item_id=item["item_id"], expected_case_revision=item["case_revision"],
            expected_item_version=item["version"], expected_memory_version=0,
            idempotency_key=uuid4().hex, new_fee_name="MECHANISM_ROLLBACK_TARGET")
        with self.assertRaisesRegex(RuntimeError, "INJECTED_TEST_CASE"):
            broken.correct(self.actor, command)
        self.assertEqual(self.demo.list_items(self.actor, item["case_id"])[0], item)
        for table in ("memory_slots", "memory_versions", "memory_events", "fees"):
            self.assertEqual(self.count(table), 0)
        self.assertEqual(self.count("correction_events"), events_before)
        saved = self.service.correct(self.actor, command)
        self.assertEqual(saved, self.service.correct(self.actor, command))
        self.assertEqual(self.count("memory_slots"), 1)
        self.assertEqual(self.count("memory_versions"), 1)

    def test_revoke_conflict_and_reconfirmation_stay_in_mock_scope(self):
        first = self.insert()
        original = self.correct(first, new_fee_name="MECHANISM_ORIGINAL_CONFIRMATION")
        second = self.insert(price="23")
        conflicted = self.correct(second, new_fee_name="MECHANISM_DIFFERENT_CONFIRMATION")
        self.assertEqual(conflicted["memory_effect"], "CONFLICTED")
        next_item = self.insert(price="31")
        self.assertEqual(next_item["saved_status"], "REVIEW_REQUIRED")
        self.assertIsNone(next_item["current_fee_id"])
        resolved = self.correct(next_item, target_fee_id=original["fee_id"],
                                replace_existing_mapping=True)
        current = self.demo.list_items(self.actor, next_item["case_id"])[0]
        revoked = self.service.revoke_memory(self.actor, RevokeCommand(
            item_id=current["item_id"], expected_case_revision=current["case_revision"],
            expected_item_version=current["version"],
            expected_memory_version=resolved["memory_version"],
            idempotency_key=uuid4().hex, reason="MECHANISM_TEST_REVOKE"))
        self.assertEqual(revoked["memory_effect"], "REVOKED")
        last = self.insert(price="47")
        self.assertEqual(last["saved_status"], "REVIEW_REQUIRED")
        self.assertEqual(last["current_memory_origin"], "MOCK_SDK")
        self.assertIsNone(last["current_fee_id"])
        old = self.demo.list_items(self.actor, first["case_id"])[0]
        self.assertEqual((old["current_price"], old["current_fee_id"]),
                         ("17", original["fee_id"]))

    def test_import_mode_binding_and_persisted_match_origin(self):
        first = self.insert()
        confirmed = self.correct(first, new_fee_name="MECHANISM_IMPORT_CONFIRMATION")
        doc, line = self.registered_line(price="23")
        store = IngestionStore(self.repo)
        task = store.start_task(self.actor, doc["document_id"], uuid4().hex)
        for state in ("READING", "EXTRACTING", "SAVING"):
            store.transition(self.actor, task["task_id"], state)
        read_evidence = {
            "origin": "MOCK_SDK", "document_sha256": doc["sha256"],
            "sources": [{"source_id": line.observation.source_id,
                         "raw_text": line.observation.raw_label, "source_ref": line.source_ref}],
        }
        wrong = deepcopy(read_evidence)
        wrong["origin"] = "REAL_SDK"
        with self.assertRaisesRegex(ValueError, "mode must agree"):
            store.finish(self.actor, task["task_id"], [line], [], wrong,
                         {"execution_kind": "MECHANISM_TEST", "actual_api_calls": 0})
        result = store.finish(self.actor, task["task_id"], [line], [], read_evidence,
                              {"execution_kind": "MECHANISM_TEST", "actual_api_calls": 0})
        self.assertEqual(result["state"], "AVAILABLE")
        match = result["result_json"]["classification_results"][0]
        self.assertEqual(match["memory_origin"], "MOCK_SDK")
        self.assertTrue(match["memory_key"].startswith("MOCK_SDK:v1:"))
        self.assertEqual(match["decision"]["fee_id"], confirmed["fee_id"])
        self.assertEqual(match["decision"]["unit_price"], "23")

    def test_database_rejects_relabeling_mock_memory_as_real(self):
        first = self.insert()
        self.correct(first, new_fee_name="MECHANISM_SOURCE_PROTECTION")
        with self.assertRaises(psycopg.errors.CheckViolation):
            with self.repo.transaction() as conn:
                conn.execute("UPDATE sanzo_core.memory_slots SET origin='REAL_SDK' WHERE tenant_id=%s",
                             (self.actor.tenant_id,))
        self.assertEqual(self.demo.list_items(self.actor, first["case_id"])[0]
                         ["current_memory_origin"], "MOCK_SDK")
