"""Real PostgreSQL integration tests, using synthetic input only.
Missing SANZO_TEST_DSN is NOT_RUN. Each test owns a unique tenant; no database,
schema or table is cleared. This suite does not substitute SQLite or mocks.
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
import json
import os
import subprocess
import sys
from threading import Barrier
import unittest
from uuid import uuid4

from sanzo_core.domain import (
    Principal, CorrectionCommand, RevokeCommand, SyntheticLine, Observation,
    Context, ConflictError, ScopeError, DecisionKind,
)
from sanzo_core.postgres import PostgresRepository
from sanzo_core.service import ReviewService

DSN = os.environ.get("SANZO_TEST_DSN")


@unittest.skipUnless(DSN, "NOT_RUN: no dedicated SANZO_TEST_DSN configured")
class PostgresIntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.dsn = DSN
        PostgresRepository(cls.dsn).migrate()

    def setUp(self):
        self.tenant = "w1-test-" + uuid4().hex
        self.actor = Principal(self.tenant, "editor-1")
        self.repo = PostgresRepository(self.dsn)
        self.service = ReviewService(self.repo)
        self.context = Context("ROAD", "N/A", "PER_DELIVERY", "DELIVERY_ONLY", "N/A")
        self.add_line("first", ai_suggested_name="配送料")

    def add_line(self, suffix, *, actor=None, existing_case=None, **changes):
        actor = actor or self.actor
        case_id = existing_case or "case-" + suffix
        if existing_case is None:
            self.service.create_case(actor, case_id, "Synthetic " + suffix)
        values = dict(
            tenant_id=actor.tenant_id, item_id="item-" + suffix, case_id=case_id,
            document_id="document-" + suffix, source_id="source-" + suffix,
            forwarder_id="company-A", raw_label="送货钱", unit_price="10",
            currency="CNY", context=self.context, source_ready=True,
        )
        for field in list(changes):
            if field in values:
                values[field] = changes.pop(field)
        observation = Observation(**values)
        source = {
            "synthetic": True, "document_id": observation.document_id,
            "document_revision": 1, "document_sha256": "a" * 64,
            "source_block_ids": [observation.source_id], "kind": "EXCEL_RANGE",
            "locator_status": "VERIFIED",
            "locator": {"exact_sheet_name": "Synthetic", "A1_range": "A2:B2"},
        }
        source.update(changes.pop("source_ref", {}))
        line = SyntheticLine(
            observation=observation, source_ref=source,
            quotation_date=changes.pop("quotation_date", "2026-08-01"),
            valid_until=changes.pop("valid_until", "2026-08-31"),
            raw_value=changes.pop("raw_value", observation.unit_price), **changes,
        )
        self.service.add_observation(actor, line)
        return line

    def command(self, item_id="item-first", *, memory_version=0, **changes):
        item = self.service.get_item(self.actor, item_id)
        values = dict(
            item_id=item_id, expected_item_version=item["version"],
            expected_case_revision=item["case_revision"],
            expected_memory_version=memory_version, idempotency_key=uuid4().hex,
        )
        values.update(changes)
        return CorrectionCommand(**values)

    def learn(self):
        return self.service.correct(self.actor, self.command(new_fee_name="配送费"))

    def snapshot(self):
        """Read all application tables, restricted to this test's tenant."""
        import psycopg
        from psycopg import sql

        with psycopg.connect(self.dsn) as connection:
            tables = connection.execute(
                "SELECT table_name FROM information_schema.columns "
                "WHERE table_schema='sanzo_core' AND column_name='tenant_id' "
                "ORDER BY table_name"
            ).fetchall()
            self.assertTrue(tables, "Migration has no tenant-scoped tables")
            result = {}
            for (name,) in tables:
                query = sql.SQL(
                    "SELECT row_to_json(t)::text FROM {}.{} t "
                    "WHERE tenant_id=%s ORDER BY row_to_json(t)::text"
                ).format(sql.Identifier("sanzo_core"), sql.Identifier(name))
                result[name] = connection.execute(query, (self.tenant,)).fetchall()
            return result

    @staticmethod
    def knowledge(snapshot):
        return {key: rows for key, rows in snapshot.items()
                if "memory" in key or key == "fees"}

    def race(self, commands):
        barrier = Barrier(len(commands))

        def run(command):
            service = ReviewService(PostgresRepository(self.dsn))
            barrier.wait(timeout=10)
            try:
                return service.correct(self.actor, command)
            except ConflictError as error:
                return error

        with ThreadPoolExecutor(max_workers=len(commands)) as executor:
            futures = [executor.submit(run, command) for command in commands]
            return [future.result(timeout=30) for future in futures]

    def test_A_B_raw_label_reuse_keeps_new_document_facts(self):
        first = self.learn()
        line = self.add_line(
            "second", unit_price="20", currency="USD", quotation_date="2026-09-01",
            valid_until="2026-09-30", raw_value="20",
            source_ref={"document_sha256": "b" * 64},
        )
        before = self.service.get_item(self.actor, "item-second")
        decision = self.service.classify(self.actor, "item-second")
        self.assertEqual(decision.kind, DecisionKind.MEMORY_APPLIED)
        self.assertEqual((decision.fee_id, decision.fee_name), (first["fee_id"], "配送费"))
        self.assertEqual((decision.unit_price, decision.currency), ("20", "USD"))
        after = self.service.get_item(self.actor, "item-second")
        self.assertEqual(before, after, "Classification must not overwrite saved state")
        self.assertEqual(after["source_ref"], line.source_ref)
        self.assertEqual(after["quotation_date"], "2026-09-01")
        self.assertEqual(after["valid_until"], "2026-09-30")
        self.assertEqual(after["raw_value"], "20")
        self.assertEqual(json.loads(after["observation"])["raw_label"], "送货钱")
        self.assertEqual(self.service.get_item(self.actor, "item-first")["ai_suggested_name"], "配送料")

    def test_C_new_connection_and_separate_process_persist(self):
        self.learn()
        self.add_line("second", unit_price="20")
        fresh = ReviewService(PostgresRepository(self.dsn))
        self.assertEqual(fresh.classify(self.actor, "item-second").fee_name, "配送费")
        script = (
            "import json,os,sys;from dataclasses import asdict;"
            "from sanzo_core.domain import Principal;"
            "from sanzo_core.postgres import PostgresRepository;"
            "from sanzo_core.service import ReviewService;"
            "tenant=json.load(sys.stdin);"
            "s=ReviewService(PostgresRepository(os.environ['SANZO_TEST_DSN']));"
            "print(json.dumps(asdict(s.classify(Principal(tenant,'editor-1'),"
            "'item-second')),ensure_ascii=False))"
        )
        process = subprocess.run(
            [sys.executable, "-c", script], input=json.dumps(self.tenant),
            capture_output=True, text=True, timeout=30, check=False,
        )
        self.assertEqual(process.returncode, 0, process.stderr)
        output = json.loads(process.stdout)
        self.assertEqual(output["kind"], "MEMORY_APPLIED")
        self.assertEqual((output["fee_name"], output["unit_price"]), ("配送费", "20"))

    def test_D_scope_and_context_never_broaden(self):
        self.learn()
        variations = [
            {"forwarder_id": "company-B"},
            {"context": replace(self.context, equipment="20FT")},
            {"context": replace(self.context, equipment="40FT")},
            {"context": replace(self.context, billing_unit="PER_CONTAINER")},
            {"context": replace(self.context, charge_scope="ORIGIN")},
            {"context": replace(self.context, extra_terms="INCLUDES_INSTALLATION")},
        ]
        for number, changes in enumerate(variations):
            with self.subTest(changes=changes):
                suffix = "scope-" + str(number)
                self.add_line(suffix, **changes)
                self.assertEqual(self.service.classify(self.actor, "item-" + suffix).kind,
                                 DecisionKind.AI_REQUIRED)
        other = Principal("w1-other-" + uuid4().hex, "other")
        self.add_line("tenant-other", actor=other)
        self.assertEqual(self.service.classify(other, "item-tenant-other").kind,
                         DecisionKind.AI_REQUIRED)
        with self.assertRaises(ScopeError):
            self.service.get_item(other, "item-first")

    def test_D_20FT_rule_does_not_apply_to_40FT(self):
        self.add_line("20", context=replace(self.context, equipment="20FT"))
        self.service.correct(self.actor, self.command("item-20", new_fee_name="海运费"))
        self.add_line("40", context=replace(self.context, equipment="40FT"))
        self.assertEqual(self.service.classify(self.actor, "item-40").kind, DecisionKind.AI_REQUIRED)

    def test_E_ai_suggestion_is_not_confirmation_or_raw_alias(self):
        before = self.knowledge(self.snapshot())
        self.assertEqual(self.service.classify(self.actor, "item-first").kind, DecisionKind.AI_REQUIRED)
        self.assertEqual(self.knowledge(self.snapshot()), before)
        self.learn()
        self.add_line("wrong-alias", raw_label="配送料")
        self.assertEqual(self.service.classify(self.actor, "item-wrong-alias").kind,
                         DecisionKind.AI_REQUIRED)

    def test_F_amount_only_does_not_create_or_update_knowledge(self):
        before = self.knowledge(self.snapshot())
        result = self.service.correct(self.actor, self.command(unit_price="12"))
        self.assertEqual(result["memory_effect"], "UNCHANGED_AMOUNT_ONLY")
        self.assertEqual(self.knowledge(self.snapshot()), before)
        self.learn()
        learned = self.knowledge(self.snapshot())
        result = self.service.correct(self.actor, self.command(unit_price="15"))
        self.assertEqual(result["memory_effect"], "UNCHANGED_AMOUNT_ONLY")
        self.assertEqual(self.knowledge(self.snapshot()), learned)

    def test_G_idempotency_rejects_changed_payload_and_actor(self):
        command = self.command(new_fee_name="配送费")
        first = self.service.correct(self.actor, command)
        snapshot = self.snapshot()
        self.assertEqual(self.service.correct(self.actor, command), first)
        self.assertEqual(self.snapshot(), snapshot)
        with self.assertRaises(ConflictError):
            self.service.correct(self.actor, replace(command, new_fee_name="安装费"))
        with self.assertRaises(ConflictError):
            self.service.correct(Principal(self.tenant, "editor-2"), command)
        self.assertEqual(self.snapshot(), snapshot)

    def test_G_concurrent_identical_request_has_one_result_and_event(self):
        command = self.command(new_fee_name="配送费")
        results = self.race([command, command])
        self.assertIsInstance(results[0], dict)
        self.assertEqual(results[0], results[1])
        self.assertEqual(len(self.snapshot()["correction_events"]), 1)
        self.assertEqual(self.service.get_item(self.actor, "item-first")["version"], 2)

    def test_G_concurrent_changed_payload_same_key_is_rejected(self):
        command = self.command(unit_price="11")
        results = self.race([command, replace(command, unit_price="12")])
        self.assertEqual(sum(isinstance(value, dict) for value in results), 1)
        self.assertEqual(sum(isinstance(value, ConflictError) for value in results), 1)
        self.assertEqual(len(self.snapshot()["correction_events"]), 1)

    def test_H_concurrent_same_item_has_one_winner(self):
        results = self.race([self.command(unit_price=price) for price in ("11", "12")])
        self.assertEqual(sum(isinstance(value, dict) for value in results), 1)
        self.assertEqual(sum(isinstance(value, ConflictError) for value in results), 1)
        winner = next(value for value in results if isinstance(value, dict))
        saved = self.service.get_item(self.actor, "item-first")
        self.assertEqual(saved["version"], 2)
        self.assertEqual(saved["current_price"], winner["unit_price"])
        self.assertEqual(len(self.snapshot()["correction_events"]), 1)

    def test_H_concurrent_first_memory_slot_across_cases(self):
        self.add_line("second")
        results = self.race([
            self.command("item-first", new_fee_name="配送费"),
            self.command("item-second", new_fee_name="安装费"),
        ])
        self.assertEqual(sum(isinstance(value, dict) for value in results), 1)
        self.assertEqual(sum(isinstance(value, ConflictError) for value in results), 1)
        snapshot = self.snapshot()
        for table in ("fees", "memory_slots", "correction_events"):
            self.assertEqual(len(snapshot[table]), 1, table)
        versions = [self.service.get_item(self.actor, item)["version"]
                    for item in ("item-first", "item-second")]
        self.assertEqual(sorted(versions), [1, 2])

    def test_H_case_revision_conflict_rolls_back(self):
        command = self.command(unit_price="30")
        self.add_line("same-case", existing_case="case-first", raw_label="新费用")
        before = self.snapshot()
        with self.assertRaises(ConflictError):
            self.service.correct(self.actor, command)
        self.assertEqual(self.snapshot(), before)

    def test_H_memory_version_conflict_rolls_back(self):
        self.add_line("second")
        command = self.command("item-second", new_fee_name="安装费", unit_price="99")
        self.learn()
        before = self.snapshot()
        with self.assertRaises(ConflictError):
            self.service.correct(self.actor, command)
        self.assertEqual(self.snapshot(), before)

    def test_H_mid_transaction_failure_rolls_back_every_table(self):
        for stage in ("after_item", "after_memory", "before_command"):
            with self.subTest(stage=stage):
                suffix = "fault-" + stage
                self.add_line(suffix, raw_label="原文-" + stage)
                command = self.command("item-" + suffix, new_fee_name="费用-" + stage, unit_price="99")
                before = self.snapshot()

                def fail(reached):
                    if reached == stage:
                        raise RuntimeError("INJECTED_TEST_FAILURE:" + stage)

                failing = ReviewService(self.repo, fault_hook=fail)
                with self.assertRaisesRegex(RuntimeError, "INJECTED_TEST_FAILURE"):
                    failing.correct(self.actor, command)
                self.assertEqual(self.snapshot(), before)
                retry = self.service.correct(self.actor, command)
                self.assertEqual((retry["memory_effect"], retry["unit_price"]), ("ACTIVE", "99"))

    def test_I_conflict_needs_explicit_latest_version_reconfirmation(self):
        first = self.learn()
        self.add_line("second")
        conflict = self.service.correct(
            self.actor, self.command("item-second", memory_version=1, new_fee_name="安装费")
        )
        self.assertEqual(conflict["memory_effect"], "CONFLICTED")
        self.assertEqual(self.service.classify(self.actor, "item-second").reason, "MEMORY_CONFLICTED")
        ordinary = self.service.correct(
            self.actor, self.command("item-second", memory_version=2, target_fee_id=first["fee_id"])
        )
        self.assertEqual(ordinary["memory_effect"], "CONFLICTED")
        before = self.snapshot()
        with self.assertRaises(ConflictError):
            self.service.correct(
                self.actor, self.command("item-second", memory_version=2,
                    target_fee_id=first["fee_id"], replace_existing_mapping=True)
            )
        self.assertEqual(self.snapshot(), before)
        resolved = self.service.correct(
            self.actor, self.command("item-second", memory_version=3,
                target_fee_id=first["fee_id"], replace_existing_mapping=True)
        )
        self.assertEqual(resolved["memory_effect"], "ACTIVE")
        self.assertEqual(self.service.classify(self.actor, "item-second").fee_name, "配送费")

    def revoke_command(self, **changes):
        item = self.service.get_item(self.actor, "item-first")
        values = dict(
            item_id="item-first", expected_case_revision=item["case_revision"],
            expected_item_version=item["version"], expected_memory_version=1,
            idempotency_key=uuid4().hex, reason="Synthetic mistaken confirmation",
        )
        values.update(changes)
        return RevokeCommand(**values)

    def test_I_revoke_idempotency_no_fallback_explicit_reactivation(self):
        first = self.learn()
        historical = self.service.get_item(self.actor, "item-first")
        command = self.revoke_command()
        result = self.service.revoke_memory(self.actor, command)
        before = self.snapshot()
        self.assertEqual(self.service.revoke_memory(self.actor, command), result)
        self.assertEqual(self.snapshot(), before)
        with self.assertRaises(ConflictError):
            self.service.revoke_memory(self.actor, replace(command, reason="Other reason"))
        self.assertEqual(self.service.get_item(self.actor, "item-first"), historical)
        self.add_line("second")
        self.assertEqual(self.service.classify(self.actor, "item-second").reason, "MEMORY_REVOKED")
        ordinary = self.service.correct(
            self.actor, self.command("item-second", memory_version=2, target_fee_id=first["fee_id"])
        )
        self.assertEqual(ordinary["memory_effect"], "REVOKED")
        resolved = self.service.correct(
            self.actor, self.command("item-second", memory_version=3,
                target_fee_id=first["fee_id"], replace_existing_mapping=True)
        )
        self.assertEqual(resolved["memory_effect"], "ACTIVE")

    def test_J_rule_update_does_not_rewrite_old_case(self):
        first = self.learn()
        historical = self.service.get_item(self.actor, "item-first")
        self.add_line("second", unit_price="20")
        self.service.correct(
            self.actor, self.command("item-second", memory_version=1, new_fee_name="安装费")
        )
        changed = self.service.get_item(self.actor, "item-second")["current_fee_id"]
        self.service.correct(
            self.actor, self.command("item-second", memory_version=2,
                target_fee_id=changed, replace_existing_mapping=True)
        )
        self.assertEqual(self.service.get_item(self.actor, "item-first"), historical)
        self.assertEqual(historical["current_fee_id"], first["fee_id"])

    def test_incomplete_or_unverified_evidence_saves_without_learning(self):
        variations = [
            ({"context": replace(self.context, equipment=None)}, DecisionKind.REVIEW_REQUIRED),
            ({"context": replace(self.context, ready=False)}, DecisionKind.REVIEW_REQUIRED),
            ({"source_ready": False}, DecisionKind.READING_REQUIRED),
        ]
        for number, (changes, kind) in enumerate(variations):
            with self.subTest(changes=changes):
                suffix = "incomplete-" + str(number)
                self.add_line(suffix, **changes)
                self.assertEqual(self.service.classify(self.actor, "item-" + suffix).kind, kind)
                before = self.snapshot()["memory_slots"]
                result = self.service.correct(
                    self.actor, self.command("item-" + suffix, new_fee_name="确认费用-" + str(number))
                )
                self.assertTrue(result["memory_effect"].startswith("NOT_ELIGIBLE"))
                self.assertIsNotNone(self.service.get_item(self.actor, "item-" + suffix)["current_fee_id"])
                self.assertEqual(self.snapshot()["memory_slots"], before)

    def test_case_authorization_and_other_editor_reuse(self):
        self.learn()
        self.add_line("second")
        other = Principal(self.tenant, "editor-2", allowed_case_ids=frozenset({"case-second"}))
        self.assertEqual(self.service.classify(other, "item-second").kind, DecisionKind.MEMORY_APPLIED)
        with self.assertRaises(ScopeError):
            self.service.get_item(other, "item-first")
        with self.assertRaises(ScopeError):
            self.service.correct(other, self.command(unit_price="22"))
        with self.assertRaises(ScopeError):
            self.service.revoke_memory(other, self.revoke_command())
        with self.assertRaises(ScopeError):
            self.service.correct(Principal(self.tenant, "reader", can_edit=False),
                                 self.command(unit_price="22"))

    def test_foreign_tenant_fee_and_duplicate_display_name_rejected(self):
        first = self.learn()
        other = Principal("w1-foreign-" + uuid4().hex, "other")
        self.add_line("foreign", actor=other)
        item = self.service.get_item(other, "item-foreign")
        command = CorrectionCommand(
            item_id="item-foreign", expected_item_version=item["version"],
            expected_case_revision=item["case_revision"], expected_memory_version=0,
            idempotency_key=uuid4().hex, target_fee_id=first["fee_id"],
        )
        with self.assertRaises(ScopeError):
            self.service.correct(other, command)
        self.add_line("same-name", raw_label="运送钱")
        with self.assertRaises(ConflictError):
            self.service.correct(self.actor, self.command("item-same-name", new_fee_name="配送费"))


if __name__ == "__main__":
    unittest.main(verbosity=2)
