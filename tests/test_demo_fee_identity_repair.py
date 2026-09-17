"""Explicit identity repair: isolated synthetic PostgreSQL; no live SDK or AI."""
from copy import deepcopy
from dataclasses import replace
import unittest
from uuid import uuid4

from sanzo_core.domain import ConflictError, CorrectionCommand, RevokeCommand, ScopeError
from sanzo_core.fee_identity_repair import (FeeIdentityRepairCommand,
    FeeIdentityRepairService, RepairAlias, RepairMember)
import test_demo_fee_rename_r3 as fixture


@unittest.skipUnless(fixture.DSN, "NOT_RUN: isolated SANZO_TEST_DSN required")
class FeeIdentityRepairTests(unittest.TestCase):
    # Reuse fixture helpers without inheriting/rediscovering the rename suite.
    for _name in ("new_case", "prepared_line", "commit_line", "import_line", "snapshot",
                  "projection", "current", "fee", "name_versions", "slot", "command",
                  "export_values", "assert_old_case_preserved", "database_snapshot"):
        locals()[_name] = getattr(fixture.FeeRenamePostgresR3Tests, _name)

    def setUp(self):
        fixture.FeeRenamePostgresR3Tests.setUp(self)
        self.new_case("editing")
        self.a1 = self.import_line("editing", "A", "DOC FEE", "8000")
        self.a2 = self.import_line("editing", "A", "DOC FEE", "8500")
        # Reproduce the old branch through the existing explicit reclassification
        # service; these facts are test evidence, never an automatic merge rule.
        split = self.rows.confirm(self.actor, self.command("editing",
            new_fee_name=fixture.NEW_NAME, intent="RECLASSIFY_MEMBERS"))
        self.branch_id = split["fee_id"]
        self.proofs = {m["item_id"]: m["event_id"] for m in split["members"]}
        self.b = self.import_line("editing", "B", "DOCUMENTATION FEE", "7500.00")
        self.repair = FeeIdentityRepairService(self.repo)
        self.plan = self.make_plan()

    def make_plan(self):
        snap = self.snapshot("editing")
        with self.repo.transaction() as conn:
            aliases = conn.execute("""SELECT memory_key,version,fee_id FROM sanzo_core.memory_slots
                WHERE tenant_id=%s AND status='ACTIVE' AND fee_id IN (%s,%s) ORDER BY memory_key""",
                (self.tenant, self.fee_id, self.branch_id)).fetchall()
        return FeeIdentityRepairCommand(repair_id=uuid4().hex, case_id="editing",
            expected_case_revision=snap["case"]["case_revision"], original_fee_id=self.fee_id,
            branch_fee_id=self.branch_id, expected_original_name_version=self.fee()["name_version"],
            expected_branch_name_version=self.fee(self.branch_id)["name_version"],
            target_name=fixture.NEW_NAME, expected_origin="SYNTHETIC_STRUCTURED",
            members=tuple(RepairMember(i["item_id"], i["version"], i["current_fee_id"],
                self.proofs.get(i["item_id"])) for i in snap["items"]),
            expected_active_aliases=tuple(RepairAlias(a["memory_key"], a["version"], a["fee_id"]) for a in aliases))

    def test_repair_three_prices_one_identity_single_alias_rewrite_preserves_history(self):
        before = self.snapshot("editing")
        b_slot = deepcopy(self.slot(self.b))
        self.assertEqual(len(self.projection("editing")["rows"]), 2)
        result = self.repair.apply(self.actor, self.plan)
        after = self.snapshot("editing")
        self.assertEqual(len(self.projection("editing")["rows"]), 1)
        self.assertEqual({i["current_fee_id"] for i in after["items"]}, {self.fee_id})
        self.assertEqual({i["current_fee_name"] for i in after["items"]}, {fixture.NEW_NAME})
        self.assertEqual(len(result["rewritten_alias_keys"]), 1)
        self.assertEqual(self.slot(self.b), b_slot, "B alias was already the stable identity")
        self.assertEqual(self.slot(self.a1)["version"],
            next(a.expected_version for a in self.plan.expected_active_aliases if a.expected_fee_id == self.branch_id) + 1)
        old_items = {i["item_id"]: i for i in before["items"]}
        for item in after["items"]:
            for field in ("observation", "source_ref", "currency", "current_price", "raw_value"):
                self.assertEqual(item[field], old_items[item["item_id"]][field])
        self.assertEqual(sorted(i["current_price"] for i in after["items"]), ["7500.00", "8000", "8500"])
        self.assert_old_case_preserved()
        self.assertEqual([v["display_name"] for v in self.name_versions()], [fixture.OLD_NAME, fixture.NEW_NAME])
        self.new_case("future")
        for company, label, price in (("A", "DOC FEE", "9900"), ("B", "DOCUMENTATION FEE", "9700")):
            item = self.import_line("future", company, label, price)
            self.assertEqual(item["current_fee_id"], self.fee_id)
            self.assertEqual(item["current_fee_name"], fixture.NEW_NAME)
            self.assertEqual(item["current_price"], price)
            self.assertEqual(item["initial_classification"]["kind"], "MEMORY_APPLIED")

    def test_exact_retry_is_noop_and_changed_payload_same_id_rejected(self):
        first = self.repair.apply(self.actor, self.plan)
        after = self.database_snapshot()
        self.assertEqual(self.repair.apply(self.actor, self.plan), first)
        self.assertEqual(self.database_snapshot(), after)
        with self.assertRaises(ConflictError):
            self.repair.apply(self.actor, replace(self.plan, target_name="Different explicit name"))
        self.assertEqual(self.database_snapshot(), after)

    def test_changed_case_item_and_name_versions_rejected_without_writes(self):
        before = self.database_snapshot()
        variants = [replace(self.plan, expected_case_revision=self.plan.expected_case_revision + 1),
                    replace(self.plan, expected_original_name_version=999),
                    replace(self.plan, expected_branch_name_version=999),
                    replace(self.plan, members=(replace(self.plan.members[0], expected_item_version=999),) + self.plan.members[1:])]
        for plan in variants:
            with self.subTest(plan=plan), self.assertRaises(ConflictError):
                self.repair.apply(self.actor, plan)
            self.assertEqual(self.database_snapshot(), before)

    def test_wrong_prior_event_cannot_authorize_name_similarity_merge(self):
        branch = next(i for i, m in enumerate(self.plan.members) if m.prior_correction_event_id)
        members = list(self.plan.members)
        members[branch] = replace(members[branch], prior_correction_event_id="NOT_AN_EVENT")
        before = self.database_snapshot()
        with self.assertRaises(ConflictError):
            self.repair.apply(self.actor, replace(self.plan, members=tuple(members)))
        self.assertEqual(self.database_snapshot(), before)

    def test_changed_active_alias_version_is_not_silently_accepted(self):
        aliases = list(self.plan.expected_active_aliases)
        aliases[0] = replace(aliases[0], expected_version=aliases[0].expected_version + 1)
        before = self.database_snapshot()
        with self.assertRaises(ConflictError):
            self.repair.apply(self.actor, replace(self.plan, expected_active_aliases=tuple(aliases)))
        self.assertEqual(self.database_snapshot(), before)

    def test_revocation_after_plan_stays_revoked_and_blocks_repair(self):
        item = self.current(self.history_b)
        self.service.revoke_memory(self.actor, RevokeCommand(item_id=item["item_id"],
            expected_item_version=item["version"], expected_case_revision=item["case_revision"],
            expected_memory_version=self.slot(item)["version"], idempotency_key=uuid4().hex,
            reason="INJECTED_TEST_CASE operator revoked after planning"))
        before = self.database_snapshot()
        with self.assertRaises(ConflictError):
            self.repair.apply(self.actor, self.plan)
        self.assertEqual(self.database_snapshot(), before)
        self.assertEqual(self.slot(self.history_b)["status"], "REVOKED")

    def test_later_reclassification_after_plan_is_not_pulled_back(self):
        item = self.current(self.history_b)
        self.service.correct(self.actor, CorrectionCommand(item_id=item["item_id"],
            expected_item_version=item["version"], expected_case_revision=item["case_revision"],
            expected_memory_version=self.slot(item)["version"], idempotency_key=uuid4().hex,
            new_fee_name="Deliberately different concept", replace_existing_mapping=True))
        before = self.database_snapshot()
        with self.assertRaises(ConflictError):
            self.repair.apply(self.actor, self.plan)
        self.assertEqual(self.database_snapshot(), before)

    def test_wrong_origin_or_foreign_tenant_is_rejected(self):
        before = self.database_snapshot()
        with self.assertRaises(ScopeError):
            self.repair.apply(self.actor, replace(self.plan, expected_origin="MOCK_SDK"))
        with self.assertRaises(ScopeError):
            self.repair.apply(replace(self.actor, tenant_id="foreign-test-tenant"), self.plan)
        self.assertEqual(self.database_snapshot(), before)

    def test_failure_after_name_and_before_audit_rolls_back_everything(self):
        before = self.database_snapshot()
        for failure in ("after_name", "after_item", "after_memory", "before_repair_audit"):
            def fault(stage):
                if stage == failure:
                    raise RuntimeError("INJECTED_TEST_CASE repair rollback")
            with self.subTest(stage=failure), self.assertRaisesRegex(RuntimeError, "INJECTED_TEST_CASE"):
                FeeIdentityRepairService(self.repo, fault_hook=fault).apply(self.actor, self.plan)
            self.assertEqual(self.database_snapshot(), before)

    def test_other_case_branch_snapshot_remains_original_historical_id_and_name(self):
        self.new_case("branch-history")
        past = self.import_line("branch-history", "A", "DOC FEE", "8700")
        before, exported = self.current(past), self.export_values("branch-history")
        self.repair.apply(self.actor, self.plan)
        after = self.current(past)
        for key in ("version", "current_fee_id", "current_fee_name", "current_price", "source_ref"):
            self.assertEqual(before[key], after[key])
        self.assertEqual(self.export_values("branch-history"), exported)

    def test_audit_records_explicit_identity_link_and_exact_prior_evidence(self):
        result = self.repair.apply(self.actor, self.plan)
        with self.repo.transaction() as conn:
            row = conn.execute("""SELECT * FROM sanzo_core.fee_identity_repair_events
                WHERE tenant_id=%s AND repair_id=%s""", (self.tenant, self.plan.repair_id)).fetchone()
            branch = conn.execute("SELECT retired FROM sanzo_core.fees WHERE tenant_id=%s AND fee_id=%s",
                                  (self.tenant, self.branch_id)).fetchone()
        self.assertTrue(branch["retired"])
        self.assertEqual(row["original_fee_id"], self.fee_id)
        self.assertEqual(row["branch_fee_id"], self.branch_id)
        self.assertEqual(row["result"], result)
        self.assertEqual({p["event_id"] for p in row["evidence"]["prior_corrections"]}, set(self.proofs.values()))
        self.assertEqual(len(row["evidence"]["items_before"]), 3)

    def test_live_ingestion_snapshot_excludes_retired_candidates_preserves_historical_items(self):
        from sanzo_core.ingestion_store import IngestionStore
        self.new_case("retired-branch-history")
        historical = self.import_line("retired-branch-history", "A", "DOC FEE", "8700")
        before = self.current(historical)
        self.assertEqual(before["current_fee_id"], self.branch_id)
        self.repair.apply(self.actor, self.plan)
        live_store = IngestionStore(self.repo)
        current = live_store.case_snapshot(self.actor, "editing")
        candidates = {fee["fee_id"]: fee for fee in current["fees"]}
        self.assertNotIn(self.branch_id, candidates,
                         "The actual Web snapshot must not offer the retired erroneous branch")
        self.assertEqual(candidates[self.fee_id]["display_name"], fixture.NEW_NAME)
        self.assertEqual(candidates[self.fee_id]["name_version"], 2)
        self.assertEqual(sum(fee["display_name"] == fixture.NEW_NAME for fee in current["fees"]), 1)
        past = live_store.case_snapshot(self.actor, "retired-branch-history")
        retained = next(item for item in past["items"] if item["item_id"] == historical["item_id"])
        for field in ("current_fee_id", "current_fee_name", "current_fee_name_version",
                      "current_price", "currency", "source_ref", "observation", "version"):
            self.assertEqual(retained[field], before[field], field)
        self.assertNotIn(self.branch_id, {fee["fee_id"] for fee in past["fees"]},
                         "Historical items remain readable without reviving retired selection candidates")
