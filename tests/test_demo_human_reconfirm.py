"""P0 contract: explicit row reconfirmation, never AI arbitration.

Unit probes are offline. PostgreSQL tests use unique tenants in the dedicated
SANZO_TEST_DSN only. Actual-SDK tests use the three approved fictional originals
and an explicitly offline semantic substitute; no paid AI call is performed.
"""
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from dataclasses import asdict, replace
import hashlib
import os
from threading import Barrier
import unittest
from uuid import uuid4

from sanzo_core import domain
from sanzo_core.domain import (ConflictError, Context, CorrectionCommand,
    DecisionKind, Observation, Principal, RevokeCommand, canonical_json, memory_key)
from sanzo_core.demo_store import DemoStore
from sanzo_core.postgres import PostgresRepository
from sanzo_core.service import ReviewService
from sanzo_demo.comparison import comparison_projection
from sanzo_demo.row_confirmation import (RowConfirmationCommand,
    RowConfirmationService, RowMember)
import test_postgres as core_tests

DSN = os.environ.get("SANZO_TEST_DSN")


class HumanReconfirmTransitionTests(unittest.TestCase):
    def human(self, slot, target="new", replace_revoked=False):
        return domain.transition_human_reconfirmation(slot, target, replace_revoked)

    def test_current_human_target_supersedes_active_without_replace_flag(self):
        self.assertEqual(self.human({"status":"ACTIVE","fee_id":"old"}), "ACTIVE")
        self.assertEqual(self.human(None), "ACTIVE")
        self.assertEqual(self.human({"status":"ACTIVE","fee_id":"new"}), "ACTIVE")

    def test_explicit_human_can_reconfirm_conflicted_but_not_silently_revoked(self):
        self.assertEqual(self.human({"status":"CONFLICTED","fee_id":None}), "ACTIVE")
        self.assertEqual(self.human({"status":"REVOKED","fee_id":None}), "REVOKED")
        self.assertEqual(self.human({"status":"REVOKED","fee_id":None},replace_revoked=True), "ACTIVE")

    def test_projected_revocation_does_not_look_like_ordinary_recoverable_conflict(self):
        slot={"status":"CONFLICTED","fee_id":None,"resolution":"LEGACY_PROJECTION",
              "evidence_versions":[{"status":"REVOKED"}]}
        self.assertEqual(self.human(slot),"REVOKED")
        self.assertEqual(self.human(slot,replace_revoked=True),"ACTIVE")

    def test_old_item_correction_contract_keeps_true_conflict_branch(self):
        self.assertEqual(domain.transition_memory({"status":"ACTIVE","fee_id":"old"},"new",False), "CONFLICTED")
        self.assertEqual(domain.transition_memory({"status":"CONFLICTED","fee_id":None},"new",False), "CONFLICTED")

    def test_absent_optional_evidence_keeps_previous_row_digest(self):
        actor=Principal("digest-tenant","human")
        cmd=RowConfirmationCommand("case","a"*64,1,(RowMember("item",1,0),),"existing-request",new_fee_name="Human")
        legacy=asdict(cmd)
        for key in ("intent", "expected_fee_id", "expected_name_version"): legacy.pop(key)
        for member in legacy["members"]:member.pop("expected_memory_evidence")
        payload={"actor":asdict(actor),"command_type":"RowConfirmationCommand","command":legacy}
        expected=hashlib.sha256(canonical_json(payload).encode()).hexdigest()
        self.assertEqual(domain.request_digest(actor,cmd),expected)

    def test_supplied_legacy_evidence_is_bound_to_idempotency_digest(self):
        actor=Principal("digest-tenant","human")
        evidence=[{"memory_key":"legacy","version":1,"status":"ACTIVE","fee_id":"fee","evidence_event_id":"event"}]
        cmd=RowConfirmationCommand("case","a"*64,1,(RowMember("item",1,0,evidence),),"request",new_fee_name="Human")
        modified=deepcopy(evidence);modified[0]["version"]=2
        other=replace(cmd,members=(replace(cmd.members[0],expected_memory_evidence=modified),))
        self.assertNotEqual(domain.request_digest(actor,cmd),domain.request_digest(actor,other))
        self.assertNotEqual(domain.request_digest(actor,cmd),domain.request_digest(actor,replace(cmd,members=(RowMember("item",1,0),))))

    def test_human_intent_does_not_accept_invalid_state_target_or_replace(self):
        for slot,target,replace_flag in [(None,"",False),(None,"new",1),({"status":"UNKNOWN","fee_id":None},"new",False)]:
            with self.assertRaises(ValueError):self.human(slot,target,replace_flag)


@unittest.skipUnless(DSN, "NOT_RUN: dedicated real PostgreSQL required")
class HumanReconfirmPostgresTests(unittest.TestCase):
    add_line = core_tests.PostgresIntegrationTests.add_line
    database_snapshot = core_tests.PostgresIntegrationTests.snapshot

    def setUp(self):
        self.dsn=DSN; self.tenant="human-reconfirm-test-"+uuid4().hex
        self.actor=Principal(self.tenant,"explicit-human-test")
        self.repo=PostgresRepository(DSN);self.repo.migrate()
        self.service=ReviewService(self.repo);self.store=DemoStore(self.repo)
        self.rows=RowConfirmationService(self.repo)
        self.context=Context("SEA","20FT","CONTAINER","ORIGIN","STANDARD")
        self.add_line("a",raw_label="通関手数料",unit_price="12000",currency="JPY",ai_suggested_name="Unified")
        self.add_line("b",existing_case="case-a",forwarder_id="company-B",raw_label="CUSTOMS CLEARANCE",
                      unit_price="11500",currency="JPY",ai_suggested_name="Unified")

    def row_command(self, case="case-a", **changes):
        snapshot=self.store.case_snapshot(self.actor,case)
        projection=comparison_projection(snapshot["items"],snapshot["fees"])
        self.assertEqual(len(projection["rows"]),1,projection)
        row=projection["rows"][0]
        values=dict(case_id=case,row_id=row["row_id"],expected_case_revision=snapshot["case"]["case_revision"],
            members=tuple(RowMember(i["item_id"],i["version"],i["current_memory_version"],
                i["memory_resolution"]["evidence_versions"] if (i.get("memory_resolution") or {}).get("kind")=="LEGACY_PROJECTION" else None)
                for i in row["member_items"]),idempotency_key=uuid4().hex,new_fee_name="通関手数料")
        values.update(changes)
        if values.get("target_fee_id") is not None:
            selected = next(fee for fee in snapshot["fees"] if fee["fee_id"] == values["target_fee_id"])
            values.setdefault("expected_fee_id", selected["fee_id"])
            values.setdefault("expected_name_version", selected["name_version"])
        return RowConfirmationCommand(**values)

    def ordinary_save(self, **changes):
        command=self.row_command(**changes)
        self.assertFalse(command.replace_existing_mapping)
        return self.rows.confirm(self.actor,command)

    def item_command(self, item_id, **changes):
        item=next(i for i in self.store.list_items(self.actor,"case-a") if i["item_id"]==item_id)
        return CorrectionCommand(item_id=item_id,expected_case_revision=item["case_revision"],
            expected_item_version=item["version"],expected_memory_version=item["current_memory_version"],idempotency_key=uuid4().hex,**changes)

    def versions(self):
        with self.repo.transaction() as conn:
            return conn.execute("SELECT memory_key,version,status,fee_id,raw_label,context_json,source_ref FROM sanzo_core.memory_versions WHERE tenant_id=%s ORDER BY memory_key,version",(self.tenant,)).fetchall()

    def test_p0_01_02_latest_new_name_updates_both_slots_preserves_facts_and_history(self):
        old=self.ordinary_save();before=self.store.list_items(self.actor,"case-a")
        history=deepcopy(self.versions())
        result=self.ordinary_save(new_fee_name="通関手数料_v1")
        self.assertTrue(result["memory_reusable"])
        self.assertEqual([m["memory_effect"] for m in result["members"]],["ACTIVE","ACTIVE"])
        self.assertEqual([m["memory_action"] for m in result["members"]],["ACTIVE_SUPERSEDED","ACTIVE_SUPERSEDED"])
        self.assertEqual([m["memory_version"] for m in result["members"]],[2,2])
        self.assertNotEqual(old["fee_id"],result["fee_id"])
        after=self.store.list_items(self.actor,"case-a")
        for previous,current in zip(before,after):
            self.assertEqual(current["current_fee_id"],result["fee_id"])
            for field in ("observation","source_ref","raw_value","current_price","currency"):
                self.assertEqual(current[field],previous[field])
        versions=self.versions()
        self.assertEqual(len(versions),4)
        self.assertTrue(all(v in versions for v in history))
        with self.repo.transaction() as conn:
            self.assertEqual(conn.execute("SELECT count(*) AS n FROM sanzo_core.fees WHERE tenant_id=%s AND display_name=%s",(self.tenant,"通関手数料_v1")).fetchone()["n"],1)
            reasons=conn.execute("SELECT reason FROM sanzo_core.memory_events WHERE tenant_id=%s",(self.tenant,)).fetchall()
        self.assertTrue(all(r["reason"]=="EXPLICIT_ROW_HUMAN_RECONFIRM" for r in reasons))
        self.add_line("next",raw_label="通関手数料",unit_price="13000",currency="USD")
        decision=ReviewService(PostgresRepository(DSN)).classify(self.actor,"item-next")
        self.assertEqual((decision.kind,decision.fee_id,decision.fee_name,decision.unit_price,decision.currency),
            (DecisionKind.MEMORY_APPLIED,result["fee_id"],"通関手数料_v1","13000","USD"))
        self.assertEqual(self.service.get_item(self.actor,"item-a")["current_price"],"12000")

    def test_p0_03_existing_fee_target_supersedes_without_duplicate_fee(self):
        original=self.ordinary_save()
        newer=self.ordinary_save(new_fee_name="Another human name")
        before=self.database_snapshot()
        result=self.ordinary_save(new_fee_name=None,target_fee_id=original["fee_id"])
        self.assertEqual(result["fee_id"],original["fee_id"])
        self.assertTrue(result["memory_reusable"])
        self.assertTrue(all(m["memory_action"]=="ACTIVE_SUPERSEDED" for m in result["members"]))
        self.assertEqual(self.database_snapshot()["fees"],before["fees"])
        self.assertNotEqual(newer["fee_id"],result["fee_id"])

    def test_p0_04_ordinary_confirmation_restores_existing_conflicted_history(self):
        original=self.ordinary_save()
        # Unchanged legacy item API deliberately preserves a true conflict path.
        changed=self.service.correct(self.actor,self.item_command("item-a",new_fee_name="Recovered human"))
        self.assertEqual(changed["memory_effect"],"CONFLICTED")
        self.service.correct(self.actor,self.item_command("item-b",target_fee_id=changed["fee_id"]))
        blocked=deepcopy(self.versions())
        self.assertEqual(sum(v["status"]=="CONFLICTED" for v in blocked),2)
        result=self.ordinary_save(new_fee_name=None,target_fee_id=changed["fee_id"])
        self.assertTrue(result["memory_reusable"])
        self.assertTrue(all(m["memory_action"]=="ACTIVE_RECONFIRMED" for m in result["members"]))
        self.assertTrue(all(m["memory_version"]==3 for m in result["members"]))
        self.assertTrue(all(v in self.versions() for v in blocked))

    def test_p0_05_stale_memory_from_other_case_cannot_cover_latest_target(self):
        self.ordinary_save()
        stale=self.row_command(new_fee_name="Stale writer")
        self.add_line("other",raw_label="通関手数料",ai_suggested_name="Unified")
        other=self.rows.confirm(self.actor,self.row_command(case="case-other",new_fee_name="Other latest"))
        before=self.database_snapshot()
        with self.assertRaises(ConflictError):self.rows.confirm(self.actor,stale)
        self.assertEqual(self.database_snapshot(),before)
        decision=self.service.classify(self.actor,"item-a")
        self.assertEqual(decision.fee_id,other["fee_id"])
        # Learning changes do not rewrite the old displayed case.
        self.assertEqual(self.store.list_items(self.actor,"case-a")[0]["current_fee_name"],"通関手数料")

    def test_p0_06_repeated_reconfirm_is_idempotent_changed_payload_rejected(self):
        self.ordinary_save();command=self.row_command(new_fee_name="通関手数料_v1")
        first=self.rows.confirm(self.actor,command);before=self.database_snapshot()
        self.assertEqual(self.rows.confirm(self.actor,command),first)
        self.assertEqual(self.database_snapshot(),before)
        with self.assertRaises(ConflictError):self.rows.confirm(self.actor,replace(command,new_fee_name="Different"))
        self.assertEqual(self.database_snapshot(),before)

    def test_pre_fix_conflicted_response_replay_remains_original_not_reapplied(self):
        self.ordinary_save();command=self.row_command(new_fee_name="Old conflicted request")
        # Inject the pre-fix row intent in the isolated test tenant only. This
        # creates the actual old transition and command result through services.
        with patch("sanzo_core.service.transition_human_reconfirmation",side_effect=domain.transition_memory):
            old_result=self.rows.confirm(self.actor,command)
        self.assertTrue(all(m["memory_effect"]=="CONFLICTED" for m in old_result["members"]))
        before=self.database_snapshot()
        self.assertEqual(self.rows.confirm(self.actor,command),old_result)
        self.assertEqual(self.database_snapshot(),before)
        fresh=self.ordinary_save(new_fee_name=None,target_fee_id=old_result["fee_id"])
        self.assertTrue(fresh["memory_reusable"])

    def test_p0_07_revoked_mapping_stays_blocked_until_existing_advanced_action(self):
        first=self.ordinary_save();self.ordinary_save(new_fee_name="通関手数料_v1")
        item=self.store.list_items(self.actor,"case-a")[0]
        self.service.revoke_memory(self.actor,RevokeCommand(item["item_id"],item["case_revision"],item["version"],item["current_memory_version"],uuid4().hex,"TEST explicit revocation"))
        self.assertEqual(self.service.classify(self.actor,item["item_id"]).kind,DecisionKind.REVIEW_REQUIRED)
        current=self.ordinary_save(new_fee_name=None,target_fee_id=first["fee_id"])
        self.assertFalse(current["memory_reusable"])
        self.assertIn("REVOKED",[m["memory_effect"] for m in current["members"]])
        command=self.row_command(new_fee_name=None,target_fee_id=first["fee_id"],replace_existing_mapping=True)
        self.assertTrue(self.rows.confirm(self.actor,command)["memory_reusable"])
        self.assertTrue(any(v["status"]=="REVOKED" for v in self.versions()))

    def test_stale_item_or_memory_rejected_even_with_advanced_replace(self):
        self.ordinary_save();cmd=self.row_command(new_fee_name="No overwrite",replace_existing_mapping=True)
        before=self.database_snapshot()
        for bad in (replace(cmd.members[0],expected_memory_version=0),replace(cmd.members[0],expected_item_version=1)):
            with self.assertRaises(ConflictError):self.rows.confirm(self.actor,replace(cmd,members=(bad,cmd.members[1])))
            self.assertEqual(self.database_snapshot(),before)

    def test_concurrent_cross_case_reconfirm_has_one_winner_no_partial_save(self):
        self.ordinary_save();self.add_line("other",raw_label="通関手数料",ai_suggested_name="Unified")
        commands=[self.row_command(new_fee_name="Winner A"),self.row_command(case="case-other",new_fee_name="Winner B")]
        barrier=Barrier(2)
        def save(cmd):
            barrier.wait(timeout=10)
            try:return RowConfirmationService(PostgresRepository(DSN)).confirm(self.actor,cmd)
            except ConflictError:return "CONFLICT"
        with ThreadPoolExecutor(2) as pool:results=list(pool.map(save,commands))
        self.assertEqual(sum(isinstance(r,dict) for r in results),1)
        self.assertEqual(results.count("CONFLICT"),1)
        versions=self.versions()
        self.assertTrue(all(v["status"]=="ACTIVE" for v in versions))
        self.assertEqual(max(v["version"] for v in versions),2)

    def test_second_member_failure_rolls_back_supersede_fee_events_and_commands(self):
        self.ordinary_save();before=self.database_snapshot();calls=[]
        def fail(stage):
            if stage=="after_memory":
                calls.append(stage)
                if len(calls)==2:raise RuntimeError("INJECTED_TEST_CASE_RECONFIRM_SECOND_MEMBER")
        with self.assertRaisesRegex(RuntimeError,"INJECTED_TEST_CASE"):
            RowConfirmationService(self.repo,fail).confirm(self.actor,self.row_command(new_fee_name="Never saved"))
        self.assertEqual(len(calls),2)
        self.assertEqual(self.database_snapshot(),before)

    def test_ineligible_member_name_can_save_without_claiming_future_reuse(self):
        self.add_line("ineligible",existing_case="case-a",raw_label="UNVERIFIED",ai_suggested_name="Unified",
                      forwarder_id="company-C",context=replace(self.context,transport_mode=None,ready=False))
        result=self.ordinary_save()
        self.assertFalse(result["memory_reusable"])
        self.assertEqual([m["memory_effect"] for m in result["members"]],["ACTIVE","ACTIVE","NOT_ELIGIBLE_CONTEXT_OR_SOURCE"])
        self.assertEqual(len(self.versions()),2)

    def test_unknown_or_forged_legacy_evidence_cannot_bypass_version(self):
        command=self.row_command()
        fake=[{"memory_key":"untrusted","version":1,"status":"ACTIVE","fee_id":"fake","evidence_event_id":"fake"}]
        before=self.database_snapshot()
        with self.assertRaises(ConflictError):self.rows.confirm(self.actor,replace(command,members=(replace(command.members[0],expected_memory_evidence=fake),command.members[1])))
        self.assertEqual(self.database_snapshot(),before)


# The fixed wheel is used only through the existing application adapter.
# These helpers do not access the SDK development repository.
import test_demo_fee_policy as policy_helpers
import test_demo_real_sdk_pipeline as real_helpers
from test_demo_real_adapter import SDK_READY
from sanzo_demo.document_sdk_adapter import DocumentSdkAdapter
from sanzo_demo.ingestion import IntegrationRuntime
from unittest.mock import patch


@unittest.skipUnless(DSN and SDK_READY, "NOT_RUN: isolated PostgreSQL and fixed SDK required")
class HumanReconfirmRealSDKTests(unittest.TestCase):
    setUpClass=classmethod(real_helpers.RealSDKPipelineTests.setUpClass.__func__)
    setUp=real_helpers.RealSDKPipelineTests.setUp
    new_case=real_helpers.RealSDKPipelineTests.new_case
    upload=real_helpers.RealSDKPipelineTests.upload
    process=real_helpers.RealSDKPipelineTests.process
    items=real_helpers.RealSDKPipelineTests.items
    slot_count=real_helpers.RealSDKPipelineTests.slot_count
    human_bindings=real_helpers.RealSDKPipelineTests.human_bindings
    current=policy_helpers.FeePolicyPostgresTests.current
    command=policy_helpers.FeePolicyPostgresTests.command
    confirm=policy_helpers.FeePolicyPostgresTests.confirm
    target=policy_helpers.FeePolicyPostgresTests.target
    legacy_seed=policy_helpers.FeePolicyPostgresTests.legacy_seed

    def row_command(self,item,**changes):
        snapshot=self.demo.case_snapshot(self.actor,item["case_id"])
        row=next(r for r in comparison_projection(snapshot["items"],snapshot["fees"],snapshot["documents"])["rows"] if item["item_id"] in r["item_ids"])
        values=dict(case_id=item["case_id"],row_id=row["row_id"],expected_case_revision=snapshot["case"]["case_revision"],
            members=tuple(RowMember(i["item_id"],i["version"],i["current_memory_version"],
                i["memory_resolution"]["evidence_versions"] if (i.get("memory_resolution") or {}).get("kind")=="LEGACY_PROJECTION" else None)
                for i in row["member_items"]),idempotency_key=uuid4().hex,new_fee_name="New explicit human")
        values.update(changes)
        if values.get("target_fee_id") is not None:
            selected = next(fee for fee in snapshot["fees"] if fee["fee_id"] == values["target_fee_id"])
            values.setdefault("expected_fee_id", selected["fee_id"])
            values.setdefault("expected_name_version", selected["name_version"])
        return RowConfirmationCommand(**values)

    def test_p0_08_new_sdk_price_all_hit_without_any_ai_and_no_memory_price(self):
        first=self.upload(real_helpers.FIRST);self.process(first)
        for item in self.items(first):self.confirm(item,"Original human "+item["observation"]["raw_label"])
        target=self.target(first)
        before=self.current(target)
        saved=RowConfirmationService(self.repo).confirm(self.actor,self.row_command(target,new_fee_name="配送費"))
        self.assertTrue(saved["memory_reusable"])
        self.assertEqual(saved["members"][0]["memory_action"],"ACTIVE_SUPERSEDED")
        self.assertEqual(self.current(target)["current_price"],"8000")
        self.assertEqual(self.current(target)["source_ref"],before["source_ref"])
        self.classifier.calls.clear()
        restarted=IntegrationRuntime(PostgresRepository(DSN),self.storage,reader=DocumentSdkAdapter(),extractor=None,reader_mode="REAL_SDK")
        second=self.upload(real_helpers.SECOND,runtime=restarted);task=self.process(second,runtime=restarted)
        target2=self.target(second)
        self.assertEqual((target2["current_fee_name"],target2["current_price"],target2["currency"],target2["classification_source"]),("配送費","9500","JPY","MEMORY"))
        self.assertEqual(target2["current_fee_id"],saved["fee_id"])
        self.assertEqual(target2["source_ref"]["field_sources"]["raw_value"]["original_value"],"9500")
        self.assertEqual(target2["fact_sources"]["amount"],"REAL_SDK")
        self.assertEqual(self.current(target)["current_price"],"8000")
        meta=task["result_json"]["ai_metadata"]
        self.assertEqual((meta["quotation_extraction_requests"],meta["calls"],meta["classification_requested_item_ids"]),(0,[],[]))
        self.assertEqual(len(meta["classification_skipped_item_ids"]),5)
        self.assertEqual(self.classifier.calls,[])
        with self.repo.transaction() as conn:
            versions=conn.execute("SELECT context_json,source_ref FROM sanzo_core.memory_versions WHERE tenant_id=%s",(self.actor.tenant_id,)).fetchall()
        def keys(value):
            if isinstance(value,dict):
                for k,v in value.items():yield k;yield from keys(v)
            elif isinstance(value,list):
                for v in value:yield from keys(v)
        forbidden={"amount","price","unit_price","current_price","currency","raw_value"}
        for version in versions:self.assertFalse(forbidden & set(keys(version)))

    def test_legacy_version_zero_needs_current_snapshot_and_stale_projection_rejected(self):
        first=self.upload(real_helpers.FIRST)
        legacy,result,legacy_key=self.legacy_seed(first,"Legacy old","Historical company")
        second=self.upload(real_helpers.SECOND);self.process(second)
        current=self.target(second)
        self.assertEqual(current["current_memory_version"],0)
        cmd=self.row_command(current)
        self.assertTrue(cmd.members[0].expected_memory_evidence)
        service=RowConfirmationService(self.repo)
        missing=replace(cmd,members=tuple(replace(m,expected_memory_evidence=None) for m in cmd.members))
        with self.assertRaises(ConflictError):service.confirm(self.actor,missing)
        # Simulate an old writer solely in this isolated test tenant, preserving
        # the actual historical write contract. No production history is edited.
        old=lambda obs,source=None,kind=None:obs.memory_key()
        with patch("sanzo_core.fee_policy.uses_fee_policy",return_value=False),patch("sanzo_core.domain.memory_key",old),patch("sanzo_core.postgres.memory_key",old),patch("sanzo_core.service.memory_key",old),patch("sanzo_core.demo_store.memory_key",old):
            changed=self.confirm(legacy,"Legacy changed")
        self.assertEqual(changed["memory_effect"],"CONFLICTED")
        with self.assertRaises(ConflictError):service.confirm(self.actor,cmd)
        before=self.current(current)
        self.assertEqual(before["version"],current["version"])
        fresh=self.row_command(current,new_fee_name="Explicit current human")
        restored=service.confirm(self.actor,fresh)
        self.assertTrue(restored["memory_reusable"])
        self.assertEqual(restored["members"][0]["memory_version"],1)
        with self.repo.transaction() as conn:
            legacy_slot=self.repo.slot(conn,self.actor.tenant_id,legacy_key)
        self.assertEqual((legacy_slot["status"],legacy_slot["version"]),("CONFLICTED",2))

    def test_projected_revocation_stays_revoked_across_two_ordinary_saves(self):
        first=self.upload(real_helpers.FIRST)
        legacy,original,legacy_key=self.legacy_seed(first,"Legacy revoked human","Historical company")
        old=lambda obs,source=None,kind=None:obs.memory_key()
        with patch("sanzo_core.fee_policy.uses_fee_policy",return_value=False),patch("sanzo_core.domain.memory_key",old),patch("sanzo_core.postgres.memory_key",old),patch("sanzo_core.service.memory_key",old),patch("sanzo_core.demo_store.memory_key",old):
            current=self.current(legacy)
            self.service.revoke_memory(self.actor,RevokeCommand(legacy["item_id"],current["case_revision"],current["version"],current["current_memory_version"],uuid4().hex,"TEST legacy explicit revocation"))
        second=self.upload(real_helpers.SECOND);self.process(second)
        item=self.target(second);service=RowConfirmationService(self.repo)
        first_save=service.confirm(self.actor,self.row_command(item,new_fee_name="Current saved without revive"))
        self.assertFalse(first_save["memory_reusable"])
        self.assertEqual(first_save["members"][0]["memory_effect"],"REVOKED")
        second_save=service.confirm(self.actor,self.row_command(item,new_fee_name=None,target_fee_id=first_save["fee_id"]))
        self.assertFalse(second_save["memory_reusable"])
        self.assertEqual(second_save["members"][0]["memory_effect"],"REVOKED")
        self.assertEqual(self.service.classify(self.actor,item["item_id"]).kind,DecisionKind.REVIEW_REQUIRED)
        recovered=service.confirm(self.actor,self.row_command(item,new_fee_name=None,target_fee_id=first_save["fee_id"],replace_existing_mapping=True))
        self.assertTrue(recovered["memory_reusable"])
        self.assertEqual(recovered["members"][0]["memory_version"],3)
        with self.repo.transaction() as conn:
            legacy_slot=self.repo.slot(conn,self.actor.tenant_id,legacy_key)
            key=memory_key(Observation.from_json(canonical_json(item["observation"])),item["source_ref"],item["input_kind"])
            versions=conn.execute("SELECT version,status FROM sanzo_core.memory_versions WHERE tenant_id=%s AND memory_key=%s ORDER BY version",(self.actor.tenant_id,key)).fetchall()
        self.assertEqual((legacy_slot["status"],legacy_slot["version"]),("REVOKED",2))
        self.assertEqual(versions,[{"version":1,"status":"REVOKED"},{"version":2,"status":"REVOKED"},{"version":3,"status":"ACTIVE"}])
