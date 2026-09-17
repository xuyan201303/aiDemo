"""HUMAN role confirmation boundary: synthetic fields, no SDK/AI calls.

PostgreSQL cases use only SANZO_TEST_DSN and isolated per-test tenants.
These mechanisms never count as real source or browser demonstration evidence.
"""
from copy import deepcopy
from dataclasses import replace
import unittest
from uuid import uuid4

from sanzo_core.domain import Principal, ConflictError, ScopeError, memory_key
from sanzo_core.ingestion_store import IngestionStore, task_field_bindings, task_ai_paused
from sanzo_core.postgres import PostgresRepository
from sanzo_demo.facts import structure_facts
from sanzo_demo.human_field_bindings import apply_human_bindings
from test_demo_evidence_grounding import field_fixture
import test_demo_ingestion_store as store_tests


def binding_fixture(amount="9500"):
    document, reading, _ = field_fixture(kind="EXCEL", amount=amount)
    blocks = [deepcopy(b) for b in reading.blocks
              if b["source_ref"]["evidence_unit"]["field_name"] not in {"company", "transport_mode"}]
    candidates = []
    for index, (field, text) in enumerate((("company", "INJECTED UNLABELLED COMPANY"),
            ("transport_mode", "対象：海上輸送と付帯サービス。各行に記載した条件での単価です。")), 1):
        source_id = "INJECTED_HEADER_" + field
        candidates.append({"source_id": source_id, "raw_text": text,
            "source_ref": {"origin": "REAL_SDK", "document_id": document["document_id"],
                "document_sha256": document["sha256"], "source_block_ids": [source_id],
                "kind": "EXCEL_RANGE", "locator_status": "VERIFIED", "text_verification": "VERIFIED",
                "locator": {"sheet_name": "MECHANISM_見積", "A1_range": "A" + str(index)},
                "evidence_unit": {"kind": "EXCEL_CELL", "scope": "DOCUMENT",
                    "record_id": "INJECTED_HEADER_" + field,
                    "applies_to_record_ids": ["MECHANISM_LINE_A"]}}})
    # Deliberately synthetic application envelope marked as the Real port to
    # test its boundary. No actual SDK result is claimed by this test fixture.
    reading = replace(reading, origin="REAL_SDK", blocks=tuple(blocks), sdk_identity={
        "execution_kind": "MECHANISM_TEST", "field_binding_candidates": candidates})
    actor = Principal("HUMAN_BINDING_TEST", "explicit-test-operator")
    bindings = {field: "INJECTED_HEADER_" + field for field in ("company", "transport_mode")}
    return document, reading, actor, bindings


class HumanFieldBindingsTests(unittest.TestCase):
    def bind(self, reading=None, bindings=None):
        document, original, actor, default_bindings = binding_fixture()
        return apply_human_bindings(reading or original, document, actor,
                                    default_bindings if bindings is None else bindings)

    def test_ai_pause_restriction_is_a_strict_boolean_with_legacy_false_default(self):
        task = {"events": [{"version": 1, "state": "QUEUED", "metadata": {}}]}
        self.assertFalse(task_ai_paused(task))
        for value in (True, False):
            task["events"][0]["metadata"]["pause_ai"] = value
            self.assertIs(task_ai_paused(task), value)
        for value in (1, 0, "false", "true", None, []):
            task["events"][0]["metadata"]["pause_ai"] = value
            with self.subTest(value=value), self.assertRaisesRegex(ValueError, "INVALID_TASK_AI_PAUSE"):
                task_ai_paused(task)
            # Validation happens before any DB connection or side effect.
            with self.assertRaisesRegex(ValueError, "INVALID_TASK_AI_PAUSE"):
                IngestionStore(object()).start_task(Principal("TEST", "TEST"), "DOC", "KEY", pause_ai=value)

    def test_no_binding_preserves_original_and_cannot_create_memory(self):
        document, reading, actor, _ = binding_fixture()
        self.assertIs(apply_human_bindings(reading, document, actor, {}), reading)
        lines, issues = structure_facts(document, actor.tenant_id, reading)
        self.assertEqual(len(lines), 1)
        self.assertIsNone(memory_key(lines[0].observation, lines[0].source_ref, lines[0].input_kind))
        self.assertTrue(issues)

    def test_complete_roles_preserve_full_source_fact_price_and_human_origin(self):
        document, reading, actor, bindings = binding_fixture()
        original = deepcopy(reading)
        bound = apply_human_bindings(reading, document, actor, bindings)
        self.assertEqual(reading, original)
        self.assertEqual(bound.blocks[:len(reading.blocks)], reading.blocks)
        lines, issues = structure_facts(document, actor.tenant_id, bound)
        self.assertEqual(issues, [])
        self.assertEqual(len(lines), 1)
        line = lines[0]
        self.assertEqual((line.observation.raw_label, line.observation.unit_price,
                          line.observation.currency), ("配送手数料", "9500", "JPY"))
        self.assertEqual(line.observation.forwarder_id, "INJECTED UNLABELLED COMPANY")
        self.assertEqual(line.observation.context.transport_mode,
            "対象：海上輸送と付帯サービス。各行に記載した条件での単価です。")
        self.assertIsNone(line.ai_suggested_name)
        self.assertIsNotNone(memory_key(line.observation, line.source_ref, line.input_kind))
        self.assertEqual(line.source_ref["fact_sources"]["amount"], "REAL_SDK")
        for field in bindings:
            unit = line.source_ref["field_sources"][field]["evidence_unit"]
            self.assertEqual(unit["role_origin"], "HUMAN")
            self.assertEqual(unit["role_confirmed_by"], actor.actor_id)
        self.assertEqual(len(bound.sdk_identity["human_field_confirmations"]), 2)

    def test_partial_confirmation_does_not_fill_other_unknown_condition(self):
        document, reading, actor, bindings = binding_fixture()
        bound = apply_human_bindings(reading, document, actor, {"company": bindings["company"]})
        lines, _ = structure_facts(document, actor.tenant_id, bound)
        self.assertIsNone(lines[0].observation.context.transport_mode)
        self.assertIsNone(memory_key(lines[0].observation, lines[0].source_ref, lines[0].input_kind))

    def test_same_raw_context_produces_same_memory_key_and_new_price(self):
        first_doc, first, actor, bindings = binding_fixture("8000")
        first_line = structure_facts(first_doc, actor.tenant_id,
            apply_human_bindings(first, first_doc, actor, bindings))[0][0]
        second_doc, second, _, second_bindings = binding_fixture("9500")
        second_doc = {**second_doc, "document_id": "INJECTED_SECOND_DOCUMENT", "case_id": "INJECTED_SECOND_CASE", "sha256": "b" * 64}
        second_blocks, second_identity = deepcopy(second.blocks), deepcopy(second.sdk_identity)
        for block in (*second_blocks, *second_identity["field_binding_candidates"]):
            block["source_ref"].update(document_id=second_doc["document_id"], document_sha256=second_doc["sha256"])
        second = replace(second, document_sha256=second_doc["sha256"], blocks=second_blocks, sdk_identity=second_identity)
        second_line = structure_facts(second_doc, actor.tenant_id,
            apply_human_bindings(second, second_doc, actor, second_bindings))[0][0]
        self.assertEqual(first_line.observation.memory_key(), second_line.observation.memory_key())
        self.assertEqual((first_line.observation.unit_price, second_line.observation.unit_price), ("8000", "9500"))

    def test_only_company_transport_source_ids_are_allowed(self):
        for bindings in ({"raw_label": "INJECTED_HEADER_company"}, {"amount": "9500"},
                {"company": {"value": "INJECTED"}}, {"company": ""}, [],
                {"company": "INJECTED_HEADER_company", "transport_mode": "INJECTED_HEADER_company"}):
            with self.subTest(bindings=bindings), self.assertRaises(ValueError):
                self.bind(bindings=bindings)

    def test_mock_and_mechanism_envelopes_cannot_receive_real_human_binding(self):
        for origin in ("MOCK_SDK", "MECHANISM_TEST"):
            _, reading, _, _ = binding_fixture()
            with self.subTest(origin=origin), self.assertRaisesRegex(ValueError, "REQUIRES_REAL_SDK"):
                self.bind(reading=replace(reading, origin=origin))

    def test_foreign_document_hash_and_actor_scope_are_rejected(self):
        for field, value in (("document_id", "FOREIGN"), ("document_sha256", "b" * 64)):
            _, reading, _, _ = binding_fixture()
            reading.sdk_identity["field_binding_candidates"][0]["source_ref"][field] = value
            with self.subTest(field=field), self.assertRaisesRegex(ValueError, "DOCUMENT_MISMATCH"):
                self.bind(reading=reading)
        document, reading, actor, bindings = binding_fixture()
        with self.assertRaises(ScopeError):
            apply_human_bindings(reading, document, replace(actor, allowed_case_ids=frozenset()), bindings)

    def test_pdf_page_unverified_or_multi_cell_candidate_rejected(self):
        changes = (lambda ref: ref.update(kind="PDF_PAGE", locator_status="PAGE_ONLY", locator={"page_number": 1}),
                   lambda ref: ref.update(text_verification="UNVERIFIED"),
                   lambda ref: ref["locator"].update(A1_range="A1:H1"))
        for change in changes:
            _, reading, _, _ = binding_fixture()
            change(reading.sdk_identity["field_binding_candidates"][0]["source_ref"])
            with self.subTest(change=change), self.assertRaises(ValueError):
                self.bind(reading=reading)

    def test_foreign_sheet_missing_or_duplicate_target_rejected(self):
        changes = (lambda ref: ref["locator"].update(sheet_name="FOREIGN_SHEET"),
                   lambda ref: ref["evidence_unit"].update(applies_to_record_ids=["FOREIGN_RECORD"]),
                   lambda ref: ref["evidence_unit"].update(applies_to_record_ids=[]),
                   lambda ref: ref["evidence_unit"].update(applies_to_record_ids=["MECHANISM_LINE_A"] * 2))
        for change in changes:
            _, reading, _, _ = binding_fixture()
            change(reading.sdk_identity["field_binding_candidates"][0]["source_ref"])
            with self.subTest(change=change), self.assertRaises(ValueError):
                self.bind(reading=reading)

    def test_unlisted_duplicate_prebound_or_blank_candidate_rejected(self):
        with self.assertRaisesRegex(ValueError, "SOURCE_UNAVAILABLE"):
            self.bind(bindings={"company": "UNLISTED_SOURCE"})
        for change in (lambda candidates: candidates.append(deepcopy(candidates[0])),
                lambda candidates: candidates[0]["source_ref"]["evidence_unit"].update(field_name="company"),
                lambda candidates: candidates[0].update(raw_text=" ")):
            _, reading, _, _ = binding_fixture()
            change(reading.sdk_identity["field_binding_candidates"])
            with self.subTest(change=change), self.assertRaises(ValueError):
                self.bind(reading=reading)

    def test_resolved_issues_keep_original_finding_and_human_actor(self):
        document, reading, actor, bindings = binding_fixture()
        original_issues = ({"code": "EXTRACTION_UNRESOLVED", "reason": "FIELD_HEADER_NOT_BOUND",
                            "sheet_name": "MECHANISM_見積", "field": "company"},
                           {"code": "EXTRACTION_UNRESOLVED", "reason": "SDK_LIMITATION"})
        reading = replace(reading, issues=original_issues)
        bound = apply_human_bindings(reading, document, actor, bindings)
        self.assertEqual(bound.issues[0]["reason"], "FIELD_HEADER_NOT_BOUND")
        self.assertEqual(bound.issues[0]["resolution"], "HUMAN_BOUND")
        self.assertEqual(bound.issues[0]["resolved_by"], actor.actor_id)
        self.assertEqual(bound.issues[1], original_issues[1])
        self.assertEqual(reading.issues, original_issues)

    def test_existing_fact_role_cannot_be_overridden(self):
        document, reading, actor, bindings = binding_fixture()
        bound = apply_human_bindings(reading, document, actor, {"company": bindings["company"]})
        with self.assertRaises(ValueError):
            apply_human_bindings(bound, document, actor, bindings)

    def test_f1_cross_row_price_mismatch_is_not_fixed_by_human_roles(self):
        document, reading, actor, bindings = binding_fixture()
        blocks = deepcopy(reading.blocks)
        next(b for b in blocks if b["source_ref"]["evidence_unit"]["field_name"] == "raw_value")["source_ref"]["evidence_unit"]["record_id"] = "FOREIGN_ROW"
        bound = apply_human_bindings(replace(reading, blocks=blocks), document, actor, bindings)
        lines, issues = structure_facts(document, actor.tenant_id, bound)
        self.assertFalse(lines)
        self.assertTrue(issues)


@unittest.skipUnless(store_tests.DSN, "NOT_RUN: isolated PostgreSQL required")
class HumanBindingPostgresTests(unittest.TestCase):
    setUpClass = classmethod(lambda cls: PostgresRepository(store_tests.DSN).migrate())
    setUp = store_tests.IngestionPostgresTests.setUp
    document = store_tests.IngestionPostgresTests.document

    def test_binding_event_survives_new_repository_and_idempotent_replay(self):
        key = uuid4().hex
        bindings = {"company": "INJECTED_COMPANY_CELL", "transport_mode": "INJECTED_SCOPE_CELL"}
        task = self.store.start_task(self.actor, self.doc["document_id"], key, field_bindings=bindings)
        fresh = IngestionStore(PostgresRepository(store_tests.DSN)).get_task(self.actor, task["task_id"])
        self.assertEqual(task_field_bindings(fresh), bindings)
        self.assertEqual(fresh["events"][0]["metadata"]["field_bindings_confirmed_by"], self.actor.actor_id)
        replay = self.store.start_task(self.actor, self.doc["document_id"], key, field_bindings=bindings)
        self.assertTrue(replay["replayed"])
        self.assertEqual(len(replay["events"]), 1)

    def test_same_key_changed_binding_conflicts_and_original_remains(self):
        key = uuid4().hex
        task = self.store.start_task(self.actor, self.doc["document_id"], key,
                                     field_bindings={"company": "INJECTED_COMPANY_CELL"})
        for changed in ({}, {"company": "OTHER_CELL"}):
            with self.subTest(changed=changed), self.assertRaises(ConflictError):
                self.store.start_task(self.actor, self.doc["document_id"], key, field_bindings=changed)
        self.assertEqual(task_field_bindings(self.store.get_task(self.actor, task["task_id"])),
                         {"company": "INJECTED_COMPANY_CELL"})

    def test_new_key_cannot_replace_active_task_binding(self):
        task = self.store.start_task(self.actor, self.doc["document_id"], uuid4().hex,
                                     field_bindings={"company": "INJECTED_COMPANY_CELL"})
        with self.assertRaises(ConflictError):
            self.store.start_task(self.actor, self.doc["document_id"], uuid4().hex,
                                 field_bindings={"company": "OTHER_CELL"})
        self.assertEqual(len(self.store.list_tasks(self.actor, self.doc["case_id"])), 1)
        self.assertEqual(task_field_bindings(task), {"company": "INJECTED_COMPANY_CELL"})

    def test_empty_binding_retains_legacy_digest_and_default_metadata(self):
        key = uuid4().hex
        original = self.store.start_task(self.actor, self.doc["document_id"], key)
        self.assertEqual(task_field_bindings(original), {})
        replay = self.store.start_task(self.actor, self.doc["document_id"], key, field_bindings={})
        self.assertTrue(replay["replayed"])
        self.assertEqual(original["events"][0]["metadata"], {"document_sha256": self.doc["sha256"]})

    def test_bounded_read_evidence_accepts_over_two_mib_and_rejects_over_eight(self):
        task = self.store.start_task(self.actor, self.doc["document_id"], uuid4().hex)
        permitted = {"read_evidence": {"origin": "MECHANISM_TEST", "raw": "x" * (2 * 1024 * 1024)}}
        reading = self.store.transition(self.actor, task["task_id"], "READING", metadata=permitted)
        self.assertEqual(reading["events"][-1]["metadata"], permitted)
        for metadata in ({"ordinary": "x" * (2 * 1024 * 1024)},
                         {"read_evidence": {"raw": "x" * (8 * 1024 * 1024)}}):
            with self.subTest(contains_read_evidence="read_evidence" in metadata), self.assertRaises(ValueError):
                self.store.transition(self.actor, task["task_id"], "EXTRACTING", metadata=metadata)
        unchanged = self.store.get_task(self.actor, task["task_id"])
        self.assertEqual((unchanged["state"], unchanged["version"]), ("READING", reading["version"]))

    def test_ai_pause_is_durable_and_changes_conflict_without_mutating_task(self):
        key = uuid4().hex
        task = self.store.start_task(self.actor, self.doc["document_id"], key, pause_ai=True)
        fresh = IngestionStore(PostgresRepository(store_tests.DSN)).get_task(self.actor, task["task_id"])
        self.assertTrue(task_ai_paused(fresh))
        replay = self.store.start_task(self.actor, self.doc["document_id"], key, pause_ai=True)
        self.assertTrue(replay["replayed"])
        self.assertEqual(len(replay["events"]), 1)
        for request_key in (key, uuid4().hex):
            with self.subTest(same_key=request_key == key), self.assertRaises(ConflictError):
                self.store.start_task(self.actor, self.doc["document_id"], request_key, pause_ai=False)
        after = self.store.get_task(self.actor, task["task_id"])
        self.assertTrue(task_ai_paused(after))
        self.assertEqual((after["state"], after["version"]), ("QUEUED", 1))
