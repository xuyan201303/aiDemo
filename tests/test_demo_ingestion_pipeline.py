"""MECHANISM_TEST: injected SDK/AI ports plus real isolated PostgreSQL.

No sample quotation file, actual SDK, network, credentials or model is used.
These boundary checks are not evidence of the requested live three-file rounds.
"""
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from dataclasses import asdict, replace
from hashlib import sha256
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Barrier, Event, Lock
from types import SimpleNamespace
from unittest import TestCase, skipUnless
import os
import unittest
from uuid import uuid4

from sanzo_core.demo_store import DemoStore
from sanzo_core.domain import CorrectionCommand, Principal, decide, memory_key
from sanzo_core.ingestion_store import IngestionStore
from sanzo_core.postgres import PostgresRepository
from sanzo_core.service import ReviewService
from sanzo_demo.ai import ExtractionError, ExtractionResult
from sanzo_demo.extraction import ReadResult, materialize
from sanzo_demo.ingestion import IntegrationRuntime, classification_reason, comparison_candidates

DSN = os.environ.get("SANZO_TEST_DSN")
CONTEXT = {"transport_mode": "SEA", "equipment": "20FT", "billing_unit": "PER_CONTAINER",
           "charge_scope": "DESTINATION", "extra_terms": "N/A"}


class MechanismReader:
    def __init__(self, readings):
        self.readings = readings
        self.calls = []
        self.entered = None
        self.release = None

    def read(self, path, document):
        self.calls.append(document["document_id"])
        if sha256(Path(path).read_bytes()).hexdigest() != document["sha256"]:
            raise AssertionError("Mechanism source bytes differ")
        if self.entered is not None:
            self.entered.set()
            if not self.release.wait(timeout=10):
                raise RuntimeError("MECHANISM_WAIT_TIMEOUT")
        return self.readings[document["sha256"]]


class MechanismExtractor:
    def __init__(self, payloads, *, failure=None, malformed_classification=False):
        self.payloads = payloads
        self.failure = failure
        self.malformed_classification = malformed_classification
        self.calls = []

    def extract(self, digest, blocks, schema, *, purpose="quotation_extraction"):
        self.calls.append({"purpose": purpose, "document_sha256": digest,
                           "blocks": deepcopy(blocks)})
        metadata = {"execution_kind": "MECHANISM_TEST", "purpose": purpose,
                    "actual_api_calls": 0, "injected_attempt": len(self.calls)}
        if self.failure == purpose:
            raise ExtractionError("INJECTED_AI_FAILURE", metadata)
        if purpose == "fee_classification":
            payload = {"suggestions": [{"item_id": block["item_id"],
                        "matched_fee_id": None, "suggested_fee_name": "MECHANISM_AI_ONLY"} for block in blocks]}
            if self.malformed_classification:
                payload["suggestions"][0]["item_id"] = "FOREIGN_MECHANISM_ITEM"
        else:
            payload = deepcopy(self.payloads[digest])
        return ExtractionResult(payload, metadata)


class TentativeClassificationEligibilityTests(TestCase):
    """Real F1 materialization plus pure routing checks; no model or database."""

    def line(self, *, missing=None, unverified=None):
        from test_demo_evidence_grounding import field_fixture, changed_block
        document, reading, payload = field_fixture()
        if missing:
            payload["lines"][0]["context"][missing] = {"value": None, "source_block_id": None}
        if unverified:
            reading = changed_block(reading, unverified, lambda block:
                block["source_ref"].update(text_verification="UNVERIFIED"))
        lines, issues = materialize(document, "PROVISIONAL_UNIT_TENANT", reading, payload)
        self.assertEqual(len(lines), 1)
        return lines[0], issues

    def test_missing_transport_allows_only_provisional_name_and_preserves_unknown(self):
        line, issues = self.line(missing="transport_mode")
        before = deepcopy(line)
        decision = decide(line.observation, line.observation.unit_price, None, None)
        self.assertEqual(decision.reason, "INCOMPLETE_CONTEXT")
        self.assertEqual(classification_reason(line, decision), "PROVISIONAL_NAME_INCOMPLETE_CONTEXT")
        self.assertIsNone(memory_key(line.observation, line.source_ref, line.input_kind))
        self.assertIsNone(line.observation.context.transport_mode)
        self.assertIn("APPLICABILITY_INCOMPLETE", [issue["code"] for issue in issues])
        self.assertEqual(line, before)

    def test_unverified_condition_allows_only_name_suggestion(self):
        line, _ = self.line(unverified="transport_mode")
        decision = decide(line.observation, line.observation.unit_price, None, None)
        self.assertEqual(classification_reason(line, decision), "PROVISIONAL_NAME_INCOMPLETE_CONTEXT")
        self.assertFalse(line.observation.context.ready)
        self.assertIsNone(memory_key(line.observation, line.source_ref, line.input_kind))

    def test_other_unverified_fields_do_not_disqualify_verified_original_label(self):
        for field in ("company", "raw_value", "currency"):
            with self.subTest(field=field):
                line, _ = self.line(unverified=field)
                decision = decide(line.observation, line.observation.unit_price, None, None)
                self.assertEqual(classification_reason(line, decision),
                                 "PROVISIONAL_NAME_OTHER_FIELDS_UNVERIFIED")
                self.assertFalse(line.observation.source_ready)
                self.assertIsNone(memory_key(line.observation, line.source_ref, line.input_kind))

    def test_unverified_original_label_still_disallows_classification(self):
        line, _ = self.line(unverified="raw_label")
        decision = decide(line.observation, line.observation.unit_price, None, None)
        self.assertIsNone(classification_reason(line, decision))

    def test_f1_rejected_label_never_reaches_classification_eligibility(self):
        from test_demo_evidence_grounding import field_fixture
        document, reading, payload = field_fixture()
        payload["lines"][0]["raw_label"]["value"] = "配送"
        lines, issues = materialize(document, "PROVISIONAL_UNIT_TENANT", reading, payload)
        self.assertEqual(lines, [])
        self.assertIn("AI_VALUE_NOT_IN_SDK_ORIGINAL", [issue["code"] for issue in issues])

    def test_exact_memory_conflict_and_revocation_cannot_be_replaced_by_ai(self):
        line, _ = self.line()
        for status in ("ACTIVE", "CONFLICTED", "REVOKED"):
            with self.subTest(status=status):
                slot = {"status": status, "fee_id": "HUMAN_FEE", "version": 2}
                fee = {"fee_id": "HUMAN_FEE", "display_name": "HUMAN_CONFIRMATION"}
                decision = decide(line.observation, line.observation.unit_price, slot, fee)
                self.assertIsNone(classification_reason(line, decision))


class ComparisonCandidateBoundaryTests(TestCase):
    """Only whitelisted saved names/conditions can enter another file's name call."""

    def fixture(self):
        from test_demo_evidence_grounding import field_fixture
        document, reading, payload = field_fixture()
        line = materialize(document, "CANDIDATE_TENANT", reading, payload)[0][0]
        obs = asdict(replace(line.observation, item_id="SAVED_ITEM", document_id="SAVED_DOCUMENT"))
        source = deepcopy(line.source_ref)
        source.update(document_id=obs["document_id"], document_sha256="b" * 64)
        item = {"tenant_id": obs["tenant_id"], "case_id": obs["case_id"],
                "item_id": obs["item_id"], "observation": obs, "source_ref": source,
                "input_kind": line.input_kind, "current_fee_id": None,
                "current_fee_name": None, "ai_suggested_name": "ACTUAL_PRIOR_AI_NAME",
                "saved_status": "AI_REQUIRED", "current_price": "SECRET_PRICE_NOT_SENT",
                "raw_value": "RAW_QUOTATION_NOT_SENT"}
        prior_doc = {"tenant_id": obs["tenant_id"], "case_id": obs["case_id"],
                     "document_id": obs["document_id"], "sha256": "b" * 64}
        return line, item, prior_doc, frozenset({"a" * 64, "b" * 64})

    def test_existing_same_case_suggestion_exports_only_name_and_conditions(self):
        line, item, doc, allowed = self.fixture()
        candidates = comparison_candidates(line, [item], [doc], allowed)
        self.assertEqual(len(candidates), 1)
        self.assertEqual(candidates[0]["name"], "ACTUAL_PRIOR_AI_NAME")
        self.assertEqual(set(candidates[0]), {"name", "context"})
        self.assertEqual(set(candidates[0]["context"]), set(CONTEXT))
        for forbidden in ("raw_label", "fee_id", "item_id", "current_price",
                          "RAW_QUOTATION_NOT_SENT", "SECRET_PRICE_NOT_SENT", "SAVED_DOCUMENT"):
            self.assertNotIn(forbidden, repr(candidates))

    def test_only_authorized_same_tenant_case_document_and_origin_can_supply_names(self):
        for defect in ("unauthorized_hash", "item_case", "observation_case", "document_case",
                       "item_tenant", "document_tenant", "document_identity",
                       "source_hash", "source_origin", "manual_origin"):
            with self.subTest(defect=defect):
                line, item, doc, allowed = self.fixture()
                if defect == "unauthorized_hash":
                    allowed = frozenset({"a" * 64})
                elif defect == "item_case":
                    item["case_id"] = "OTHER_CASE"
                elif defect == "observation_case":
                    item["observation"]["case_id"] = "OTHER_CASE"
                elif defect == "document_case":
                    doc["case_id"] = "OTHER_CASE"
                elif defect == "item_tenant":
                    item["tenant_id"] = "OTHER_TENANT"
                elif defect == "document_tenant":
                    doc["tenant_id"] = "OTHER_TENANT"
                elif defect == "document_identity":
                    item["source_ref"]["document_id"] = "OTHER_DOCUMENT"
                elif defect == "source_hash":
                    item["source_ref"]["document_sha256"] = "c" * 64
                elif defect == "source_origin":
                    item["source_ref"]["origin"] = "MOCK_SDK"
                else:
                    item["input_kind"] = "MANUAL_CONFIRMED"
                self.assertEqual(comparison_candidates(line, [item], [doc], allowed), [])

    def test_unknown_condition_remains_null_and_known_conflicts_are_excluded(self):
        line, item, doc, allowed = self.fixture()
        line = replace(line, observation=replace(line.observation,
            context=replace(line.observation.context, transport_mode=None, ready=False)))
        candidates = comparison_candidates(line, [item], [doc], allowed)
        self.assertEqual(len(candidates), 1)
        self.assertIsNone(line.observation.context.transport_mode)
        for key, value in (("equipment", "40FT"), ("billing_unit", "PER_DOCUMENT"),
                           ("charge_scope", "OTHER_SCOPE"), ("extra_terms", "OTHER_TERMS")):
            changed = deepcopy(item)
            changed["observation"]["context"][key] = value
            with self.subTest(key=key):
                self.assertEqual(comparison_candidates(line, [changed], [doc], allowed), [])

    def test_confirmed_name_used_exactly_as_candidate_without_fee_id_or_old_ai_name(self):
        line, item, doc, allowed = self.fixture()
        item.update(current_fee_id="DO_NOT_SEND_FEE_ID", current_fee_name="EXACT Existing 20FT Name",
                    saved_status="HUMAN_CONFIRMED")
        candidates = comparison_candidates(line, [item], [doc], allowed)
        self.assertEqual(candidates[0]["name"], "EXACT Existing 20FT Name")
        self.assertNotIn("DO_NOT_SEND_FEE_ID", repr(candidates))
        self.assertNotIn("ACTUAL_PRIOR_AI_NAME", repr(candidates))
        for status in ("CONFLICTED", "REVOKED"):
            item["current_memory_status"] = status
            with self.subTest(status=status):
                self.assertEqual(comparison_candidates(line, [item], [doc], allowed), [])

    def test_oversized_candidate_set_is_not_silently_truncated_to_an_arbitrary_match(self):
        line, item, doc, allowed = self.fixture()
        items = [dict(item, ai_suggested_name="DISTINCT_PROPOSAL_" + str(i)) for i in range(51)]
        self.assertEqual(comparison_candidates(line, items, [doc], allowed), [])


@skipUnless(DSN, "NOT_RUN: dedicated PostgreSQL SANZO_TEST_DSN missing")
class IngestionPipelinePostgresTests(TestCase):
    @classmethod
    def setUpClass(cls):
        PostgresRepository(DSN).migrate()

    def setUp(self):
        self.folder = TemporaryDirectory(prefix="sanzo-pipeline-mechanism-")
        self.addCleanup(self.folder.cleanup)
        self.storage = Path(self.folder.name)
        self.actor = Principal("pipeline-mechanism-" + uuid4().hex, "mechanism-editor")
        self.repo = PostgresRepository(DSN)
        self.demo = DemoStore(self.repo)
        self.service = ReviewService(self.repo)
        self.readings = {}
        self.payloads = {}
        self.reader = MechanismReader(self.readings)
        self.extractor = MechanismExtractor(self.payloads)
        self.runtime = IntegrationRuntime(self.repo, self.storage, reader=self.reader,
                                          extractor=self.extractor, allow_mechanism_tests=True)

    def document(self, *, price="10", verified=True, unverified_company=False, case_id=None):
        document_id, source_id = uuid4().hex, uuid4().hex
        if case_id is None:
            case_id = uuid4().hex
            self.service.create_case(self.actor, case_id, "MECHANISM_TEST_NO_REAL_SAMPLE")
        # Not an actual PDF parser fixture: the injected reader returns test blocks.
        raw = ("MECHANISM_TEST_NOT_A_CUSTOMER_FILE " + document_id).encode()
        digest = sha256(raw).hexdigest()
        storage_key = document_id + ".pdf"
        (self.storage / storage_key).write_bytes(raw)
        document = self.demo.register_document(
            self.actor, case_id, document_id=document_id, original_filename="MECHANISM_ONLY.pdf",
            media_type="application/pdf", size_bytes=len(raw), sha256=digest, storage_key=storage_key,
        )
        values = {"raw_label": "MECHANISM原始费目", "raw_value": price,
                  "currency": "円", "company": "MECHANISM_COMPANY_A", **CONTEXT}
        # F1: field boundaries come from this explicitly synthetic reader,
        # never from the model's substrings. Each value is a complete SDK field
        # in one declared record; the production SDK adapter remains unbound.
        record_id = uuid4().hex
        blocks, field_ids = [], {}
        for key, value in values.items():
            field_id = source_id if key == "raw_label" else uuid4().hex
            field_ids[key] = field_id
            source_ref = {
                "document_id": document_id, "document_sha256": digest,
                "source_block_ids": [field_id], "kind": "PDF_PAGE",
                "locator_status": "PAGE_ONLY", "locator": {"page_number": 1},
                "text_verification": "VERIFIED" if verified and not (
                    key == "company" and unverified_company) else "UNVERIFIED",
                "evidence_unit": {"kind": "SDK_FIELD", "field_name": key,
                                  "record_id": record_id, "scope": "LINE"},
            }
            blocks.append({"source_id": field_id, "raw_text": value, "source_ref": source_ref})
        field = lambda key: {"value": values[key], "source_block_id": field_ids[key]}
        entry = {key: field(key) for key in ("raw_label", "raw_value", "currency", "company")}
        entry["context"] = {key: field(key) for key in CONTEXT}
        self.readings[digest] = ReadResult(
            digest, tuple(blocks), {"test_adapter": "MECHANISM_TEST_NO_SDK"}, origin="MECHANISM_TEST")
        self.payloads[digest] = {"lines": [entry], "document_issues": []}
        return document

    def run_document(self, document, runtime=None):
        runtime = runtime or self.runtime
        task = runtime.submit(self.actor, document["document_id"], uuid4().hex)
        return runtime.run(self.actor, task["task_id"])

    def rows(self, document):
        return self.demo.list_items(self.actor, document["case_id"])

    def test_missing_preflight_is_persisted_and_identical_request_does_not_duplicate(self):
        document = self.document()
        runtime = IntegrationRuntime(self.repo, self.storage)
        key = uuid4().hex
        first = runtime.submit(self.actor, document["document_id"], key)
        again = runtime.submit(self.actor, document["document_id"], key)
        self.assertEqual(first["state"], "BLOCKED")
        self.assertEqual(first["task_id"], again["task_id"])
        self.assertEqual([event["state"] for event in again["events"]], ["QUEUED", "BLOCKED"])
        self.assertEqual(again["events"][-1]["metadata"]["sdk_calls"], 0)
        self.assertEqual(again["events"][-1]["metadata"]["ai_calls"], 0)
        self.assertEqual(runtime.run(self.actor, first["task_id"])["state"], "BLOCKED")
        self.assertEqual(self.rows(document), [])
        self.assertEqual(self.reader.calls, [])
        self.assertEqual(self.extractor.calls, [])

    def test_read_success_ai_failure_retains_read_trace_and_no_partial_items(self):
        document = self.document()
        self.extractor.failure = "fee_classification"
        task = self.run_document(document)
        self.assertEqual(task["state"], "FAILED")
        self.assertEqual(task["error_code"], "INJECTED_AI_FAILURE")
        read_event = next(event for event in task["events"] if event["state"] == "EXTRACTING")
        evidence = read_event["metadata"]["read_evidence"]
        self.assertEqual(evidence["origin"], "MECHANISM_TEST")
        self.assertEqual(evidence["document_sha256"], document["sha256"])
        self.assertTrue(read_event["metadata"]["read_output_sha256"])
        self.assertTrue(task["events"][-1]["metadata"]["read_completed"])
        self.assertFalse(task["events"][-1]["metadata"]["sdk_read_completed"])
        self.assertEqual(self.rows(document), [])
        self.assertEqual(len(self.reader.calls), 1)
        self.assertEqual(len(self.extractor.calls), 1)

    def test_ai_extraction_payload_is_never_used_to_supply_facts(self):
        document = self.document()
        self.payloads[document["sha256"]]["lines"][0]["raw_label"]["value"] = "INVENTED_LABEL"
        task = self.run_document(document)
        self.assertEqual(task["state"], "REVIEW_REQUIRED")
        self.assertEqual(self.rows(document)[0]["observation"]["raw_label"], "MECHANISM原始费目")
        self.assertEqual([call["purpose"] for call in self.extractor.calls], ["fee_classification"])
        self.assertEqual(task["result_json"]["ai_metadata"]["quotation_extraction_requests"], 0)

    def test_two_mechanism_imports_reuse_human_original_mapping_and_new_price(self):
        first = self.document(price="10")
        task1 = self.run_document(first)
        item1 = self.rows(first)[0]
        self.assertEqual(task1["state"], "REVIEW_REQUIRED")
        self.assertEqual(item1["ai_suggested_name"], "MECHANISM_AI_ONLY")
        self.assertIsNone(item1["current_fee_id"])
        self.assertEqual(item1["current_memory_version"], 0)
        self.assertEqual([call["purpose"] for call in self.extractor.calls],
                         ["fee_classification"])
        self.assertEqual(set(self.extractor.calls[0]["blocks"][0]), {"item_id", "raw_label", "context"})
        confirmed = self.service.correct(self.actor, CorrectionCommand(
            item_id=item1["item_id"], expected_case_revision=item1["case_revision"],
            expected_item_version=item1["version"], expected_memory_version=0,
            idempotency_key=uuid4().hex, new_fee_name="MECHANISM_HUMAN_CONFIRMED",
        ))
        before = self.demo.case_snapshot(self.actor, first["case_id"])
        second = self.document(price="20")
        task2 = self.run_document(second)
        item2 = self.rows(second)[0]
        self.assertEqual(task2["state"], "AVAILABLE")
        self.assertEqual(item2["current_fee_id"], confirmed["fee_id"])
        self.assertEqual(item2["current_fee_name"], "MECHANISM_HUMAN_CONFIRMED")
        self.assertEqual(item2["current_price"], "20")
        self.assertEqual(item2["currency"], "JPY")
        self.assertEqual(item2["saved_status"], "MEMORY_APPLIED")
        self.assertEqual(item2["source_ref"]["document_sha256"], second["sha256"])
        self.assertEqual(item2["source_ref"]["locator"], {"page_number": 1})
        self.assertIsNone(item2["ai_suggested_name"])
        self.assertEqual([call["purpose"] for call in self.extractor.calls],
                         ["fee_classification"])
        self.assertEqual(task2["result_json"]["ai_metadata"]["classification_requested_item_ids"], [])
        self.assertEqual(self.demo.case_snapshot(self.actor, first["case_id"]), before)
        with self.repo.transaction() as conn:
            memory = conn.execute(
                "SELECT raw_label,target_fee_id FROM sanzo_core.memory_versions WHERE tenant_id=%s",
                (self.actor.tenant_id,)).fetchall()
        self.assertEqual(memory, [{"raw_label": "MECHANISM原始费目", "target_fee_id": confirmed["fee_id"]}])

    def test_unknown_source_verification_never_becomes_confirmed_learning(self):
        document = self.document(verified=False)
        task = self.run_document(document)
        item = self.rows(document)[0]
        self.assertEqual(task["state"], "REVIEW_REQUIRED")
        self.assertFalse(item["observation"]["source_ready"])
        self.assertFalse(item["observation"]["context"]["ready"])
        self.assertEqual([call["purpose"] for call in self.extractor.calls], [])
        saved = self.service.correct(self.actor, CorrectionCommand(
            item_id=item["item_id"], expected_case_revision=item["case_revision"],
            expected_item_version=item["version"], expected_memory_version=0,
            idempotency_key=uuid4().hex, new_fee_name="MECHANISM_THIS_CASE_ONLY",
        ))
        self.assertEqual(saved["memory_effect"], "NOT_ELIGIBLE_CONTEXT_OR_SOURCE")

    def test_unverified_company_cannot_create_an_exact_learning_key(self):
        document = self.document(unverified_company=True)
        task = self.run_document(document)
        item = self.rows(document)[0]
        self.assertEqual(task["state"], "REVIEW_REQUIRED")
        self.assertFalse(item["observation"]["source_ready"])
        saved = self.service.correct(self.actor, CorrectionCommand(
            item_id=item["item_id"], expected_case_revision=item["case_revision"],
            expected_item_version=item["version"], expected_memory_version=0,
            idempotency_key=uuid4().hex, new_fee_name="MECHANISM_UNVERIFIED_COMPANY",
        ))
        self.assertEqual(saved["memory_effect"], "NOT_ELIGIBLE_CONTEXT_OR_SOURCE")
        self.assertEqual([call["purpose"] for call in self.extractor.calls],
                         ["fee_classification"])
        self.assertEqual(item["ai_suggested_name"], "MECHANISM_AI_ONLY")

    def test_missing_applicability_classifies_name_without_confirming_or_learning(self):
        document = self.document()
        reading = self.readings[document["sha256"]]
        self.readings[document["sha256"]] = replace(reading, blocks=tuple(b for b in reading.blocks
            if b["source_ref"]["evidence_unit"]["field_name"] != "transport_mode"))
        task = self.run_document(document)
        item = self.rows(document)[0]
        self.assertEqual(task["state"], "REVIEW_REQUIRED")
        self.assertEqual(item["ai_suggested_name"], "MECHANISM_AI_ONLY")
        self.assertIsNone(item["current_fee_id"])
        self.assertIsNone(item["observation"]["context"]["transport_mode"])
        self.assertFalse(item["observation"]["context"]["ready"])
        self.assertEqual(item["current_memory_version"], 0)
        self.assertEqual(self.demo.list_fees(self.actor), [])
        self.assertIsNone(task["result_json"]["classification_results"][0]["memory_key"])
        metadata = task["result_json"]["ai_metadata"]
        self.assertEqual(metadata["classification_requested_item_ids"], [item["item_id"]])
        self.assertEqual(metadata["classification_requested_reasons"],
                         {item["item_id"]: "PROVISIONAL_NAME_INCOMPLETE_CONTEXT"})
        self.assertEqual([call["purpose"] for call in self.extractor.calls],
                         ["fee_classification"])
        self.assertIsNone(self.extractor.calls[0]["blocks"][0]["context"]["transport_mode"])
        with self.repo.transaction() as conn:
            count = conn.execute(
                "SELECT count(*) AS n FROM sanzo_core.memory_versions WHERE tenant_id=%s",
                (self.actor.tenant_id,)).fetchone()["n"]
        self.assertEqual(count, 0)
        saved = self.service.correct(self.actor, CorrectionCommand(
            item_id=item["item_id"], expected_case_revision=item["case_revision"],
            expected_item_version=item["version"], expected_memory_version=0,
            idempotency_key=uuid4().hex, new_fee_name="MECHANISM_HUMAN_THIS_CASE_ONLY",
        ))
        self.assertEqual(saved["memory_effect"], "NOT_ELIGIBLE_CONTEXT_OR_SOURCE")
        with self.repo.transaction() as conn:
            count = conn.execute(
                "SELECT count(*) AS n FROM sanzo_core.memory_versions WHERE tenant_id=%s",
                (self.actor.tenant_id,)).fetchone()["n"]
        self.assertEqual(count, 0)

    def test_same_case_saved_proposal_is_sent_as_name_hint_without_confirmed_promotion(self):
        first = self.document()
        self.run_document(first)
        second = self.document(price="20", case_id=first["case_id"])
        reading = self.readings[second["sha256"]]
        self.readings[second["sha256"]] = replace(reading, blocks=tuple(b for b in reading.blocks
            if b["source_ref"]["evidence_unit"]["field_name"] != "transport_mode"))
        self.extractor.config = SimpleNamespace(
            authorized_sample_sha256=frozenset({first["sha256"], second["sha256"]}))
        task = self.run_document(second)
        self.assertEqual(task["state"], "REVIEW_REQUIRED")
        second_item = next(item for item in self.rows(second)
                           if item["observation"]["document_id"] == second["document_id"])
        request = self.extractor.calls[-1]
        self.assertEqual(request["purpose"], "fee_classification")
        self.assertEqual(request["blocks"][0]["comparison_candidates"], [{
            "name": "MECHANISM_AI_ONLY", "context": CONTEXT}])
        self.assertEqual(task["result_json"]["ai_metadata"]["comparison_candidate_counts"],
                         {second_item["item_id"]: 1})
        self.assertEqual(second_item["current_price"], "20")
        self.assertIsNone(second_item["current_fee_id"])
        self.assertEqual(second_item["current_memory_version"], 0)
        self.assertEqual(self.demo.list_fees(self.actor), [])
        with self.repo.transaction() as conn:
            count = conn.execute(
                "SELECT count(*) AS n FROM sanzo_core.memory_versions WHERE tenant_id=%s",
                (self.actor.tenant_id,)).fetchone()["n"]
        self.assertEqual(count, 0)

    def test_mechanism_reader_is_rejected_without_explicit_test_constructor_flag(self):
        document = self.document()
        runtime = IntegrationRuntime(self.repo, self.storage, reader=self.reader, extractor=self.extractor)
        task = self.run_document(document, runtime)
        self.assertEqual(task["state"], "FAILED")
        self.assertEqual(task["error_code"], "MECHANISM_READER_DISABLED")
        self.assertEqual(self.extractor.calls, [])
        self.assertEqual(self.rows(document), [])

    def test_foreign_classification_ids_fail_without_persisting_ai_as_confirmation(self):
        document = self.document()
        self.extractor.malformed_classification = True
        task = self.run_document(document)
        self.assertEqual(task["state"], "FAILED")
        self.assertEqual(task["error_code"], "AI_CLASSIFICATION_ID_MISMATCH")
        self.assertEqual(self.rows(document), [])
        self.assertEqual(self.demo.list_fees(self.actor), [])
        self.assertEqual(len(task["events"][-1]["metadata"]["ai_metadata"]["calls"]), 1)

    def test_original_changed_after_extraction_cannot_commit_old_evidence(self):
        document = self.document()
        original_extract = self.extractor.extract
        def change_original(*args, **kwargs):
            result = original_extract(*args, **kwargs)
            (self.storage / document["storage_key"]).write_bytes(b"MECHANISM_CHANGED_ORIGINAL")
            return result
        self.extractor.extract = change_original
        task = self.run_document(document)
        self.assertEqual(task["state"], "FAILED")
        self.assertEqual(self.rows(document), [])
        self.assertIsNone(task["result_json"])

    def test_explicit_unknown_markers_never_become_learning_scope(self):
        # These are schema-valid AI field citations, not fabricated absent text.
        # The marker remains in field evidence while normalized context/company
        # is unknown. N/A in the other fixture conditions remains explicit N/A.
        for field_name, marker in (("equipment", "UNKNOWN"), ("equipment", "不明"),
                                   ("company", "UNKNOWN"), ("company", "不明")):
            with self.subTest(field=field_name, marker=marker):
                document = self.document()
                digest = document["sha256"]
                entry = self.payloads[digest]["lines"][0]
                field = entry[field_name] if field_name == "company" else entry["context"][field_name]
                previous = field["value"]
                field["value"] = marker
                reading = self.readings[digest]
                blocks = deepcopy(reading.blocks)
                source = next(block for block in blocks
                              if block["source_id"] == field["source_block_id"])
                self.assertEqual(source["raw_text"], previous)
                source["raw_text"] = marker
                self.readings[digest] = replace(reading, blocks=blocks)
                task = self.run_document(document)
                self.assertEqual(task["state"], "REVIEW_REQUIRED")
                item = self.rows(document)[0]
                if field_name == "company":
                    self.assertIsNone(item["observation"]["forwarder_id"])
                else:
                    self.assertIsNone(item["observation"]["context"][field_name])
                self.assertEqual(item["observation"]["context"]["extra_terms"], "N/A")
                self.assertEqual(item["source_ref"]["field_sources"][field_name]["value"], marker)
                self.assertIsNone(task["result_json"]["classification_results"][0]["memory_key"])
                saved = self.service.correct(self.actor, CorrectionCommand(
                    item_id=item["item_id"], expected_case_revision=item["case_revision"],
                    expected_item_version=item["version"], expected_memory_version=0,
                    idempotency_key=uuid4().hex, new_fee_name="MECHANISM_UNKNOWN_" + uuid4().hex,
                ))
                self.assertEqual(saved["memory_effect"], "NOT_ELIGIBLE_CONTEXT_OR_SOURCE")
        with self.repo.transaction() as conn:
            count = conn.execute(
                "SELECT count(*) AS n FROM sanzo_core.memory_versions WHERE tenant_id=%s",
                (self.actor.tenant_id,)).fetchone()["n"]
        self.assertEqual(count, 0)

    def test_f1_truncation_or_foreign_record_cannot_persist_items_or_learning(self):
        for defect in ("amount_prefix", "label_prefix", "condition_suffix", "foreign_record"):
            with self.subTest(defect=defect):
                document = self.document(price="12000")
                digest = document["sha256"]
                entry = self.payloads[digest]["lines"][0]
                if defect == "amount_prefix":
                    entry["raw_value"]["value"] = "1200"
                elif defect == "label_prefix":
                    entry["raw_label"]["value"] = "MECHANISM"
                elif defect == "condition_suffix":
                    entry["context"]["charge_scope"]["value"] = "INATION"
                else:
                    reading = self.readings[digest]
                    blocks = deepcopy(reading.blocks)
                    price_source = next(b for b in blocks
                        if b["source_id"] == entry["raw_value"]["source_block_id"])
                    price_source["source_ref"]["evidence_unit"]["record_id"] = "MECHANISM_OTHER_RECORD"
                    self.readings[digest] = replace(reading, blocks=blocks)
                # Preserve the historical F1 adversarial check even though AI
                # can no longer submit factual fields to the default pipeline.
                rejected, violations = materialize(document, self.actor.tenant_id,
                    self.readings[digest], self.payloads[digest])
                self.assertEqual(rejected, [])
                self.assertTrue(violations)
                task = self.run_document(document)
                self.assertEqual(task["state"], "REVIEW_REQUIRED")
                if defect == "foreign_record":
                    self.assertEqual(self.rows(document), [])
                    self.assertEqual(task["result_json"]["ai_metadata"]["classification_requested_item_ids"], [])
                else:
                    original = self.rows(document)[0]
                    self.assertEqual(original["current_price"], "12000")
                    self.assertEqual(original["observation"]["raw_label"], "MECHANISM原始费目")
                    self.assertEqual(original["observation"]["context"]["charge_scope"], "DESTINATION")
                    self.assertIsNone(original["current_fee_id"])
                self.assertEqual(task["result_json"]["ai_metadata"]["quotation_extraction_requests"], 0)
                sources = self.runtime.store.get_task_sources(self.actor, task["task_id"])
                self.assertTrue(any(source["raw_text"] == "12000" for source in sources))
                self.assertEqual(task["result_json"]["read_evidence"]["document_sha256"], digest)
        with self.repo.transaction() as conn:
            count = conn.execute(
                "SELECT count(*) AS n FROM sanzo_core.memory_versions WHERE tenant_id=%s",
                (self.actor.tenant_id,)).fetchone()["n"]
        self.assertEqual(count, 0)

    def test_arbitrary_reader_exception_text_never_becomes_public_task_error(self):
        document = self.document()
        def fail_reader(path, metadata):
            raise ValueError("POTENTIALCONFIDENTIALTOKEN123")
        self.reader.read = fail_reader
        task = self.run_document(document)
        self.assertEqual(task["state"], "FAILED")
        self.assertEqual(task["error_code"], "PROCESSING_FAILED")
        self.assertNotIn("POTENTIALCONFIDENTIALTOKEN123", str(task))
        self.assertEqual(self.extractor.calls, [])
        self.assertEqual(self.rows(document), [])

    def test_duplicate_worker_cannot_mark_the_owner_failed_or_execute_reader_twice(self):
        document = self.document()
        task = self.runtime.submit(self.actor, document["document_id"], uuid4().hex)
        barrier, counter_lock = Barrier(2), Lock()
        original_get = self.runtime.store.get_task
        count = 0
        def coordinated_get(actor, task_id):
            nonlocal count
            value = original_get(actor, task_id)
            with counter_lock:
                count += 1
                initial = count <= 2
            if initial:
                barrier.wait(timeout=10)
            return value
        self.runtime.store.get_task = coordinated_get
        self.reader.entered, self.reader.release = Event(), Event()
        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = [pool.submit(self.runtime.run, self.actor, task["task_id"]) for _ in range(2)]
            try:
                self.assertTrue(self.reader.entered.wait(timeout=10))
            finally:
                self.reader.release.set()
            results = [future.result(timeout=10) for future in futures]
        final = original_get(self.actor, task["task_id"])
        self.assertEqual(final["state"], "REVIEW_REQUIRED")
        self.assertEqual(len(self.reader.calls), 1)
        self.assertEqual(len(self.rows(document)), 1)
        self.assertTrue(all(result["state"] != "FAILED" for result in results))


if __name__ == "__main__":
    unittest.main(verbosity=2)
