from __future__ import annotations
from dataclasses import replace
from pathlib import Path
import tempfile
import unittest

from quote_framework.domain import (
    Actor, ConflictError, Context, Correction, DecisionKind, Observation,
    ScopeError, decimal_text, normalize_label,
)
from quote_framework.ports import (
    IntegrationNotReady, UnconfiguredOpenAIAdapter, UnconfiguredSdkAdapter,
)
from quote_framework.repository import ReferenceStore


class LearningTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / "test.sqlite3"
        self.store = ReferenceStore(self.path)
        self.actor = Actor("tenant-A", "editor-1")
        self.base = Observation(
            "tenant-A", "item-1", "case-1", "doc-1", "source-1", "forwarder-A",
            "送货钱", "10.00", "CNY",
            Context("ROAD", "N/A", "PER_DELIVERY", "DELIVERY_ONLY", "N/A"),
        )
        self.store.add_observation(self.base)

    def tearDown(self):
        self.store.close()
        self.tmp.cleanup()

    def learn(self):
        return self.store.correct(self.actor, Correction(
            "item-1", 1, 0, "learn-1", new_fee_name="配送费"
        ))

    def second(self, **changes):
        defaults = dict(item_id="item-2", case_id="case-2", document_id="doc-2", source_id="source-2", unit_price="20.00")
        defaults.update(changes)
        observation = replace(self.base, **defaults)
        self.store.add_observation(observation)
        return observation

    def test_unseen_label_requests_classification(self):
        self.assertEqual(self.store.classify(self.actor, "item-1").kind, DecisionKind.AI_REQUIRED)

    def test_raw_label_to_human_label_not_ai_output(self):
        self.learn()
        self.second()
        result = self.store.classify(self.actor, "item-2")
        self.assertEqual(result.kind, DecisionKind.MEMORY_APPLIED)
        self.assertEqual(result.fee_name, "配送费")
        self.assertNotEqual(result.fee_name, "配送料")

    def test_wrong_ai_candidate_is_not_a_learned_alias(self):
        self.learn()
        self.second(raw_label="配送料")
        self.assertEqual(self.store.classify(self.actor, "item-2").kind, DecisionKind.AI_REQUIRED)

    def test_price_change_uses_current_price(self):
        self.learn()
        self.second(unit_price="1500.50")
        result = self.store.classify(self.actor, "item-2")
        self.assertEqual(result.kind, DecisionKind.MEMORY_APPLIED)
        self.assertEqual(result.unit_price, "1500.50")

    def test_price_and_currency_not_part_of_classification_key(self):
        other = replace(self.base, unit_price="1500", currency="USD")
        self.assertEqual(self.base.memory_key(), other.memory_key())

    def test_new_currency_retained_not_reused(self):
        self.learn()
        self.second(unit_price="20", currency="USD")
        self.assertEqual(self.store.classify(self.actor, "item-2").currency, "USD")

    def test_restart_persists_learning(self):
        self.learn()
        self.store.close()
        self.store = ReferenceStore(self.path)
        self.second()
        self.assertEqual(self.store.classify(self.actor, "item-2").fee_name, "配送费")

    def test_different_user_same_tenant_can_reuse(self):
        self.learn()
        self.second()
        self.assertEqual(self.store.classify(Actor("tenant-A", "editor-2"), "item-2").kind, DecisionKind.MEMORY_APPLIED)

    def test_different_tenant_cannot_reuse(self):
        self.learn()
        second = replace(self.base, tenant_id="tenant-B", item_id="item-B")
        self.store.add_observation(second)
        self.assertEqual(self.store.classify(Actor("tenant-B", "user-B"), "item-B").kind, DecisionKind.AI_REQUIRED)

    def test_cross_tenant_item_denied(self):
        with self.assertRaises(ScopeError):
            self.store.get_item(Actor("tenant-B", "editor-B"), "item-1")

    def test_different_forwarder_not_automatically_global(self):
        self.learn()
        self.second(forwarder_id="forwarder-B")
        self.assertEqual(self.store.classify(self.actor, "item-2").kind, DecisionKind.AI_REQUIRED)

    def test_unknown_forwarder_cannot_exact_reuse(self):
        self.learn()
        self.second(forwarder_id=None)
        self.assertEqual(self.store.classify(self.actor, "item-2").reason, "UNKNOWN_FORWARDER_NO_EXACT_REUSE")

    def test_equipment_change_no_reuse(self):
        self.learn()
        self.second(context=replace(self.base.context, equipment="40FT"))
        self.assertEqual(self.store.classify(self.actor, "item-2").kind, DecisionKind.AI_REQUIRED)

    def test_20ft_and_40ft_distinct(self):
        a = replace(self.base, raw_label="O/F", context=replace(self.base.context, equipment="20FT"))
        b = replace(a, context=replace(a.context, equipment="40FT"))
        self.assertNotEqual(a.memory_key(), b.memory_key())

    def test_condition_added_is_not_dropped(self):
        self.learn()
        self.second(context=replace(self.base.context, extra_terms="INCLUDES_INSTALLATION"))
        self.assertEqual(self.store.classify(self.actor, "item-2").kind, DecisionKind.AI_REQUIRED)

    def test_unknown_context_not_wildcard(self):
        self.learn()
        self.second(context=replace(self.base.context, equipment=None))
        self.assertEqual(self.store.classify(self.actor, "item-2").kind, DecisionKind.REVIEW_REQUIRED)

    def test_unvalidated_context_not_exact_match(self):
        self.learn()
        self.second(context=replace(self.base.context, ready=False))
        self.assertEqual(self.store.classify(self.actor, "item-2").kind, DecisionKind.REVIEW_REQUIRED)

    def test_missing_source_triggers_reading_gate(self):
        self.learn()
        self.second(source_ready=False)
        self.assertEqual(self.store.classify(self.actor, "item-2").kind, DecisionKind.READING_REQUIRED)

    def test_whitespace_only_normalization(self):
        self.learn()
        self.second(raw_label="  送货钱\n")
        self.assertEqual(self.store.classify(self.actor, "item-2").kind, DecisionKind.MEMORY_APPLIED)

    def test_new_name_needs_ai_not_fuzzy_auto_accept(self):
        self.learn()
        self.second(raw_label="运输服务费")
        self.assertEqual(self.store.classify(self.actor, "item-2").kind, DecisionKind.AI_REQUIRED)

    def test_classification_decision_is_not_training(self):
        self.store.classify(self.actor, "item-1")
        count = self.store.conn.execute("SELECT COUNT(*) FROM memory_slots").fetchone()[0]
        self.assertEqual(count, 0)

    def test_amount_only_correction_does_not_learn(self):
        saved = self.store.correct(self.actor, Correction("item-1", 1, 0, "amount-1", unit_price="20.00"))
        self.assertEqual(saved["memory_effect"], "UNCHANGED_AMOUNT_ONLY")
        self.assertEqual(self.store.classify(self.actor, "item-1").kind, DecisionKind.AI_REQUIRED)

    def test_idempotent_retry_does_not_duplicate_event(self):
        command = Correction("item-1", 1, 0, "same-request", new_fee_name="配送费")
        a = self.store.correct(self.actor, command)
        b = self.store.correct(self.actor, command)
        self.assertEqual(a, b)
        self.assertEqual(self.store.conn.execute("SELECT COUNT(*) FROM correction_events").fetchone()[0], 1)

    def test_changed_payload_same_idempotency_rejected(self):
        self.learn()
        with self.assertRaises(ConflictError):
            self.store.correct(self.actor, Correction("item-1", 1, 0, "learn-1", new_fee_name="安装费"))

    def test_stale_item_version_is_rejected(self):
        self.learn()
        with self.assertRaises(ConflictError):
            self.store.correct(self.actor, Correction("item-1", 1, 1, "new-id", unit_price="99"))
        self.assertEqual(self.store.get_item(self.actor, "item-1")["current_price"], "10.00")

    def test_stale_memory_rolls_back_item_and_category(self):
        self.learn()
        self.second()
        with self.assertRaises(ConflictError):
            self.store.correct(self.actor, Correction("item-2", 1, 0, "stale-memory", new_fee_name="安装费"))
        self.assertEqual(self.store.get_item(self.actor, "item-2")["version"], 1)
        self.assertEqual(self.store.conn.execute("SELECT COUNT(*) FROM fees").fetchone()[0], 1)

    def test_conflicting_human_labels_block_reuse(self):
        self.learn()
        self.second()
        saved = self.store.correct(self.actor, Correction("item-2", 1, 1, "conflicting", new_fee_name="安装费"))
        self.assertEqual(saved["memory_effect"], "CONFLICTED")
        result = self.store.classify(self.actor, "item-2")
        self.assertEqual(result.kind, DecisionKind.REVIEW_REQUIRED)
        self.assertIsNone(result.fee_id)

    def test_explicit_conflict_resolution_reactivates(self):
        first = self.learn()
        self.second()
        self.store.correct(self.actor, Correction("item-2", 1, 1, "conflicting", new_fee_name="安装费"))
        saved = self.store.correct(self.actor, Correction(
            "item-2", 2, 2, "resolve", target_fee_id=first["fee_id"], replace_existing_mapping=True,
        ))
        self.assertEqual(saved["memory_effect"], "ACTIVE")
        self.assertEqual(self.store.classify(self.actor, "item-2").fee_name, "配送费")

    def test_no_implicit_reactivation_after_conflict(self):
        first = self.learn()
        self.second()
        self.store.correct(self.actor, Correction("item-2", 1, 1, "conflicting", new_fee_name="安装费"))
        saved = self.store.correct(self.actor, Correction("item-2", 2, 2, "again", target_fee_id=first["fee_id"]))
        self.assertEqual(saved["memory_effect"], "CONFLICTED")

    def test_revoke_blocks_fallback_to_old_confirmations(self):
        self.learn()
        self.store.revoke_memory(self.actor, "item-1", 1, "Wrong past correction")
        self.second()
        self.assertEqual(self.store.classify(self.actor, "item-2").reason, "MEMORY_REVOKED")

    def test_revocation_does_not_rewrite_historical_quote(self):
        first = self.learn()
        before = self.store.get_item(self.actor, "item-1")
        self.store.revoke_memory(self.actor, "item-1", 1, "Bad rule")
        after = self.store.get_item(self.actor, "item-1")
        self.assertEqual(before, after)
        self.assertEqual(after["current_fee_id"], first["fee_id"])

    def test_current_correction_saved_without_eligible_memory(self):
        self.second(context=replace(self.base.context, equipment=None))
        saved = self.store.correct(self.actor, Correction("item-2", 1, 0, "partial", new_fee_name="配送费"))
        self.assertEqual(saved["memory_effect"], "NOT_ELIGIBLE_CONTEXT_OR_SOURCE")
        self.assertIsNotNone(self.store.get_item(self.actor, "item-2")["current_fee_id"])

    def test_cross_tenant_target_fee_rejected(self):
        first = self.learn()
        other = replace(self.base, tenant_id="tenant-B", item_id="item-B")
        self.store.add_observation(other)
        with self.assertRaises(ScopeError):
            self.store.correct(Actor("tenant-B", "editor-B"), Correction("item-B", 1, 0, "attack", target_fee_id=first["fee_id"]))

    def test_unauthorized_editor_rejected(self):
        with self.assertRaises(ScopeError):
            self.store.correct(Actor("tenant-A", "reader", can_edit=False), Correction("item-1", 1, 0, "forbidden", unit_price="20"))

    def test_existing_display_name_requires_explicit_id(self):
        self.learn()
        self.second(raw_label="运送钱")
        with self.assertRaises(ConflictError):
            self.store.correct(self.actor, Correction("item-2", 1, 0, "duplicate-label", new_fee_name="配送费"))

    def test_empty_correction_rejected(self):
        with self.assertRaises(ValueError):
            self.store.correct(self.actor, Correction("item-1", 1, 0, "empty"))

    def test_price_edit_and_learning_same_transaction(self):
        saved = self.store.correct(self.actor, Correction("item-1", 1, 0, "combined", new_fee_name="配送费", unit_price="12.00"))
        self.assertEqual(saved["unit_price"], "12.00")
        self.second(unit_price="28.00")
        self.assertEqual(self.store.classify(self.actor, "item-2").unit_price, "28.00")

    def test_real_sdk_adapter_is_fail_closed(self):
        with self.assertRaises(IntegrationNotReady):
            UnconfiguredSdkAdapter().read_document("doc-1", "private/not-read")

    def test_real_ai_adapter_is_fail_closed(self):
        with self.assertRaises(IntegrationNotReady):
            UnconfiguredOpenAIAdapter().propose(self.base, ())

    def test_normalizer_does_not_remove_digits_or_punctuation(self):
        self.assertNotEqual(normalize_label("O/F 20'"), normalize_label("OF 20'"))
        self.assertNotEqual(normalize_label("O/F 20'"), normalize_label("O/F 40'"))
        self.assertNotEqual(normalize_label("DELIVERY"), normalize_label("delivery"))

    def test_ambiguous_prices_rejected(self):
        for raw in ("1,500", "1e3", "NaN", "Infinity", " 12", "0012", "12元"):
            with self.subTest(raw=raw), self.assertRaises(ValueError):
                decimal_text(raw)

    def test_two_connections_stale_memory_detected(self):
        other = ReferenceStore(self.path)
        try:
            self.second()
            self.learn()
            with self.assertRaises(ConflictError):
                other.correct(self.actor, Correction("item-2", 1, 0, "stale-other-connection", new_fee_name="配送费2"))
        finally:
            other.close()

    def test_permission_string_is_not_authorization(self):
        with self.assertRaises(ScopeError):
            self.store.get_item(Actor("tenant-A", "reader", can_edit="false"), "item-1")

    def test_context_ready_string_rejected(self):
        with self.assertRaises(ValueError):
            replace(self.base.context, ready="false")

    def test_source_ready_string_rejected(self):
        with self.assertRaises(ValueError):
            self.second(source_ready="false")

    def test_boolean_version_rejected(self):
        with self.assertRaises(ValueError):
            self.store.correct(self.actor, Correction("item-1", True, 0, "boolean-version", unit_price="20"))


if __name__ == "__main__":
    unittest.main(verbosity=2)
