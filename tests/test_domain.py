"""Offline W1 domain tests; these are not PostgreSQL acceptance."""
from dataclasses import asdict, replace
import json
from pathlib import Path
import unittest
from sanzo_core.domain import (
    Principal, CorrectionCommand, RevokeCommand, SyntheticLine, Observation,
    Context, ScopeError, DecisionKind, decide, transition_memory,
    request_digest, normalize_label, decimal_text,
)
FW = Path(__file__).resolve().parents[1] / "sanzo_cost_table_framework_v1_1_0"
def observation():
    return Observation.from_json((FW / "contracts/observed_line.synthetic.json").read_text())
def source(obs):
    return {"synthetic": True, "document_id": obs.document_id, "source_block_ids": [obs.source_id]}

class IndependentDomainTests(unittest.TestCase):
    def test_reference_rules_reused_and_new_document_facts_excluded_from_key(self):
        obs = observation()
        from quote_framework.domain import Observation as ReferenceObservation
        self.assertIs(Observation, ReferenceObservation)
        other = replace(obs, case_id="new", document_id="new", item_id="new",
                        source_id="new", unit_price="20", currency="USD")
        self.assertEqual(obs.memory_key(), other.memory_key())

    def test_every_scope_dimension_is_significant_and_unknown_never_wildcard(self):
        obs = observation()
        for field in ("tenant_id", "forwarder_id", "raw_label"):
            with self.subTest(field=field):
                self.assertNotEqual(obs.memory_key(), replace(obs, **{field: "other"}).memory_key())
        for field in ("transport_mode", "equipment", "billing_unit", "charge_scope", "extra_terms"):
            with self.subTest(field=field):
                self.assertNotEqual(obs.memory_key(), replace(obs, context=replace(obs.context, **{field: "other"})).memory_key())
                self.assertIsNone(replace(obs, context=replace(obs.context, **{field: None})).memory_key())

    def test_normalization_preserves_business_meaning(self):
        self.assertEqual(normalize_label(" 送货钱\t\n"), "送货钱")
        self.assertEqual(normalize_label("e\u0301"), normalize_label("é"))
        for a, b in (("20FT", "40FT"), ("A/B", "AB"), ("fee", "Fee"), ("fee'1", "fee1")):
            self.assertNotEqual(normalize_label(a), normalize_label(b))

    def test_decimal_never_coerced_or_rounded(self):
        for value in ("20.00", "-0.01", "999999999999999999999999999999"):
            self.assertEqual(decimal_text(value), value)
        for value in (20, 1.25, "1,200", "1e2", "NaN", "", None):
            with self.subTest(value=value), self.assertRaises((ValueError, TypeError)):
                decimal_text(value)

    def test_synthetic_source_cannot_claim_sdk_or_foreign_evidence(self):
        obs = observation()
        SyntheticLine(obs, source(obs)).validate()
        for line in (SyntheticLine(obs, source(obs), input_kind="SDK_PARSED"),
                     SyntheticLine(obs, {**source(obs), "synthetic": False}),
                     SyntheticLine(obs, {**source(obs), "document_id": "other"}),
                     SyntheticLine(obs, {**source(obs), "source_block_ids": ["other"]}),
                     SyntheticLine(obs, None)):
            with self.subTest(line=line), self.assertRaises(ValueError):
                line.validate()

    def test_missing_evidence_is_saveable_but_not_reusable(self):
        obs = replace(observation(), source_ready=False)
        SyntheticLine(obs, None).validate()
        self.assertIsNone(obs.memory_key())
        self.assertEqual(decide(obs, "20", None, None).kind, DecisionKind.READING_REQUIRED)

    def test_raw_text_and_iso_dates_preserved(self):
        line = SyntheticLine(observation(), source(observation()), raw_value="10.00 CNY",
                             quotation_date="2026-09-11", valid_until="2026-12-31")
        line.validate()
        self.assertEqual(line.raw_value, "10.00 CNY")
        for bad in ("2026-02-30", "09/11/2026", 20260911):
            with self.subTest(bad=bad), self.assertRaises((ValueError, TypeError)):
                replace(line, quotation_date=bad).validate()

    def test_reference_correction_requires_added_case_revision(self):
        raw = json.loads((FW / "contracts/correction_first.synthetic.json").read_text())
        with self.assertRaises(TypeError):
            CorrectionCommand(**raw)
        cmd = CorrectionCommand(**raw, expected_case_revision=2)
        cmd.validate()
        for bad in (True, 0, "2"):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                replace(cmd, expected_case_revision=bad).validate()

    def test_amount_only_cannot_replace_mapping(self):
        cmd = CorrectionCommand("item", 1, 0, "req", unit_price="20", expected_case_revision=2)
        cmd.validate()
        with self.assertRaises(ValueError):
            replace(cmd, replace_existing_mapping=True).validate()

    def test_actor_and_case_scope_enforced(self):
        actor = Principal("tenant", "actor", allowed_case_ids=frozenset({"allowed"}))
        actor.require_case("allowed")
        with self.assertRaises(ScopeError):
            actor.require_case("other")
        for invalid in (Principal("tenant", "actor", can_edit=False),
                        Principal("tenant", "actor", can_edit="true"),
                        Principal("tenant", "actor", allowed_case_ids="allowed")):
            with self.subTest(actor=invalid), self.assertRaises(ScopeError):
                invalid.require_case("allowed")

    def test_idempotency_digest_binds_actor_payload_and_scope(self):
        cmd = CorrectionCommand("item", 1, 0, "req", new_fee_name="配送费", expected_case_revision=2)
        actor = Principal("tenant", "actor", allowed_case_ids=frozenset({"a", "b"}))
        self.assertEqual(request_digest(actor, cmd), request_digest(replace(actor, allowed_case_ids=frozenset({"b", "a"})), cmd))
        for changed in (replace(cmd, unit_price="20"), replace(cmd, expected_memory_version=1)):
            self.assertNotEqual(request_digest(actor, cmd), request_digest(actor, changed))
        self.assertNotEqual(request_digest(actor, cmd), request_digest(replace(actor, actor_id="other"), cmd))

    def test_revoke_requires_reason_versions_idempotency(self):
        cmd = RevokeCommand("item", 2, 1, 1, "revoke", "wrong context")
        cmd.validate()
        for changes in ({"reason": ""}, {"idempotency_key": ""}, {"expected_memory_version": 0},
                        {"expected_item_version": True}, {"expected_case_revision": "2"}):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                replace(cmd, **changes).validate()

    def test_blocked_states_require_explicit_reconfirmation(self):
        for state in ("CONFLICTED", "REVOKED"):
            slot = {"status": state, "fee_id": None, "version": 2}
            self.assertEqual(transition_memory(slot, "confirmed", False), state)
            self.assertEqual(transition_memory(slot, "confirmed", True), "ACTIVE")
        self.assertEqual(transition_memory({"status": "ACTIVE", "fee_id": "first"}, "second", False), "CONFLICTED")

    def test_reading_and_context_gates_precede_ai_or_memory(self):
        obs = observation()
        self.assertEqual(decide(obs, "20", None, None).kind, DecisionKind.AI_REQUIRED)
        self.assertEqual(decide(replace(obs, forwarder_id=None), "20", None, None).reason, "UNKNOWN_FORWARDER_NO_EXACT_REUSE")
        incomplete = replace(obs, context=replace(obs.context, equipment=None))
        self.assertEqual(decide(incomplete, "20", None, None).kind, DecisionKind.REVIEW_REQUIRED)
        self.assertEqual(decide(replace(incomplete, source_ready=False), "20", None, None).kind, DecisionKind.READING_REQUIRED)
        for state in ("CONFLICTED", "REVOKED"):
            result = decide(obs, "20", {"status": state, "fee_id": None, "version": 3}, None)
            self.assertEqual(result.kind, DecisionKind.REVIEW_REQUIRED)
            self.assertIsNone(result.fee_id)

    def test_decision_json_matches_unchanged_contract_examples(self):
        obs = replace(observation(), unit_price="20")
        result = decide(obs, "20", {"status": "ACTIVE", "fee_id": "example-fee-id", "version": 1},
                        {"fee_id": "example-fee-id", "display_name": "配送费"})
        self.assertEqual(asdict(result), json.loads((FW / "contracts/decision_reuse.synthetic.json").read_text()))
        self.assertEqual(asdict(decide(obs, "20", None, None)),
                         json.loads((FW / "contracts/decision_unknown.synthetic.json").read_text()))

if __name__ == "__main__":
    unittest.main()
