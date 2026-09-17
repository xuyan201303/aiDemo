"""Fee-name-v2: actual fixed SDK reads, isolated PostgreSQL, offline semantic AI.

Generated legacy rows below are explicitly injected historical test fixtures.
No live database, API credential, or OpenAI request is used by these tests.
"""
from copy import deepcopy
from dataclasses import replace
from io import BytesIO
import json
import os
from pathlib import Path
import unittest
from unittest.mock import patch
from uuid import uuid4
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

from fastapi.testclient import TestClient
from openpyxl import load_workbook
from sanzo_core.domain import (CorrectionCommand, RevokeCommand, Observation, Context,
    SDKObservedLine, ConflictError, memory_key, canonical_json)
from sanzo_core.fee_policy import signature, name_evidence, line_conditions, PREFIX
from sanzo_core.postgres import PostgresRepository
from sanzo_core.service import ReviewService
from sanzo_demo.app import create_app
from sanzo_demo.document_sdk_adapter import DocumentSdkAdapter
from sanzo_demo.facts import structure_facts
from sanzo_demo.ingestion import IntegrationRuntime
from sanzo_demo.row_confirmation import RowConfirmationService, RowConfirmationCommand, RowMember
from sanzo_demo.comparison import comparison_projection
import test_demo_real_sdk_pipeline as helpers
from test_demo_real_adapter import document, SDK_READY


@unittest.skipUnless(SDK_READY, "NOT_RUN: fixed wheel in Python 3.12 required")
class FeePolicyFactsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.reader = DocumentSdkAdapter()
        path = helpers.SAMPLES / helpers.FIRST
        doc = document(path)
        cls.lines, _ = structure_facts(doc, "fee-policy-facts", cls.reader.read(path, doc))

    def line(self):
        return next(l for l in self.lines if l.observation.raw_label == "配送手数料")

    def test_company_transport_currency_price_are_not_name_signature(self):
        line = self.line(); obs = line.observation
        key = memory_key(obs, line.source_ref, line.input_kind)
        self.assertTrue(key.startswith(PREFIX))
        self.assertIsNone(obs.forwarder_id); self.assertIsNone(obs.context.transport_mode)
        self.assertFalse(obs.source_ready); self.assertFalse(obs.context.complete())
        for company in (None, "Company A", "Company B"):
            changed = replace(obs, forwarder_id=company, currency="USD", unit_price="4132",
                              context=replace(obs.context, transport_mode="NEW DOCUMENT ROLE"))
            self.assertEqual(memory_key(changed, line.source_ref, line.input_kind), key)
        self.assertNotEqual(memory_key(replace(obs, tenant_id="another"), line.source_ref, line.input_kind), key)

    def test_complete_label_source_required_even_when_document_roles_optional(self):
        line = self.line()
        for mutation in (lambda s:s.update(text_verification="UNVERIFIED"),
                         lambda s:s.update(document_id="foreign-document"),
                         lambda s:s["field_sources"]["raw_label"].update(value="配送"),
                         lambda s:s["field_sources"]["raw_label"].update(source_block_id="another-row"),
                         lambda s:s["field_sources"]["raw_label"].update(record_key=["EXCEL_RANGE", "sheet", "other-record"])):
            source=deepcopy(line.source_ref); mutation(source)
            self.assertIsNone(memory_key(line.observation,source,line.input_kind))

    def test_line_conditions_exact_unknown_not_wildcard_20_40_distinct(self):
        line = next(l for l in self.lines if l.observation.context.equipment == "20FT")
        first = signature(line.observation,line.source_ref,line.input_kind)
        source=deepcopy(line.source_ref)
        source["field_sources"]["equipment"].update(value="40FT", original_value="40FT")
        obs=replace(line.observation,context=replace(line.observation.context,equipment="40FT"))
        other=signature(obs,source,line.input_kind)
        missing=signature(replace(line.observation,context=replace(line.observation.context,equipment=None)),line.source_ref,line.input_kind)
        self.assertNotEqual(first,other); self.assertNotEqual(first,missing); self.assertNotEqual(other,missing)
        self.assertEqual(first["raw_label"],other["raw_label"])

    def test_document_scope_and_cross_row_condition_not_in_signature(self):
        line=self.line(); original=signature(line.observation,line.source_ref,line.input_kind)
        self.assertEqual(original["conditions"]["charge_scope"],line.observation.context.charge_scope)
        document_source=deepcopy(line.source_ref)
        document_source["field_sources"]["charge_scope"]["evidence_unit"]["scope"]="DOCUMENT"
        document_signature=signature(line.observation,document_source,line.input_kind)
        self.assertIsNone(document_signature["conditions"]["charge_scope"])
        changed=replace(line.observation,context=replace(line.observation.context,charge_scope="DIFFERENT DOCUMENT SCOPE"))
        self.assertEqual(signature(changed,document_source,line.input_kind),document_signature)
        source=deepcopy(line.source_ref)
        source["field_sources"]["billing_unit"]["evidence_unit"]["record_id"]="FOREIGN ROW"
        self.assertIsNone(line_conditions(vars(line.observation.context),source)["billing_unit"])

    def test_new_name_evidence_does_not_copy_amount_or_currency(self):
        line=self.line(); value=name_evidence(line.observation,line.source_ref)
        self.assertNotIn("raw_value",value["fields"]); self.assertNotIn("currency",value["fields"])
        self.assertNotIn("8000",canonical_json(value)); self.assertNotIn("unit_price",canonical_json(value))
        self.assertEqual(value["fields"]["raw_label"]["original_value"],"配送手数料")

    def test_normal_upload_has_no_field_source_preflight_or_role_modal(self):
        root=Path(__file__).resolve().parents[1]
        workflow=(root/"backend/sanzo_demo/static/workflow.js").read_text()
        page=(root/"backend/sanzo_demo/static/index.html").read_text()
        self.assertNotIn("reviewDocumentFieldSources",workflow)
        self.assertNotIn("field-sources",workflow)
        self.assertNotIn("field_review.js",page)
        self.assertNotIn('id="field-review-dialog"',page)


@unittest.skipUnless(helpers.DSN and SDK_READY, "NOT_RUN: isolated PostgreSQL and actual fixed SDK required")
class FeePolicyPostgresTests(unittest.TestCase):
    setUpClass=classmethod(helpers.RealSDKPipelineTests.setUpClass.__func__)
    setUp=helpers.RealSDKPipelineTests.setUp
    new_case=helpers.RealSDKPipelineTests.new_case
    upload=helpers.RealSDKPipelineTests.upload
    process=helpers.RealSDKPipelineTests.process
    items=helpers.RealSDKPipelineTests.items
    slot_count=helpers.RealSDKPipelineTests.slot_count
    read_evidence=helpers.RealSDKPipelineTests.read_evidence
    human_bindings=helpers.RealSDKPipelineTests.human_bindings

    def current(self,item):
        return next(i for i in self.demo.list_items(self.actor,item["case_id"]) if i["item_id"]==item["item_id"])

    def command(self,item,**kw):
        current=self.current(item)
        return CorrectionCommand(item_id=item["item_id"],expected_case_revision=current["case_revision"],
            expected_item_version=current["version"],expected_memory_version=current["current_memory_version"],idempotency_key=uuid4().hex,**kw)

    def confirm(self,item,name):
        fees=self.demo.list_fees(self.actor)
        existing=next((f for f in fees if f["display_name"]==name),None)
        return self.service.correct(self.actor,self.command(item,**({"target_fee_id":existing["fee_id"]} if existing else {"new_fee_name":name})))

    def target(self,doc,label="配送手数料"):
        return next(i for i in self.items(doc) if i["observation"]["raw_label"]==label)

    def test_cold_unknown_company_human_save_and_new_price_memory_only_target(self):
        first=self.upload(helpers.FIRST); task=self.process(first)
        self.assertEqual(len(self.classifier.calls),1)
        self.assertEqual(len(self.classifier.calls[0]["blocks"]),5)
        self.assertEqual(self.slot_count(),0)
        item=self.target(first)
        self.assertIsNone(item["observation"]["forwarder_id"])
        self.assertIsNone(item["observation"]["context"]["transport_mode"])
        confirmed=self.confirm(item,"配送費");self.assertEqual(confirmed["memory_effect"],"ACTIVE")
        self.assertEqual(self.slot_count(),1)
        old=self.current(item)
        with self.repo.transaction() as conn:
            version=conn.execute("SELECT * FROM sanzo_core.memory_versions WHERE tenant_id=%s",(self.actor.tenant_id,)).fetchone()
        self.assertEqual(version["context_json"]["policy"],"fee-name-v2")
        self.assertNotIn("8000",canonical_json(version["source_ref"]))
        self.assertNotIn("unit_price",canonical_json(version["context_json"]))
        self.classifier.calls.clear()
        second=self.upload(helpers.SECOND); task2=self.process(second)
        target=self.target(second)
        self.assertEqual((target["current_fee_name"],target["current_price"],target["classification_source"]),("配送費","9500","MEMORY"))
        self.assertEqual(target["source_ref"]["field_sources"]["raw_value"]["original_value"],"9500")
        self.assertEqual(target["source_ref"]["fact_sources"]["amount"],"REAL_SDK")
        self.assertEqual(len(self.classifier.calls),1)
        self.assertEqual(len(self.classifier.calls[0]["blocks"]),4)
        self.assertNotIn(target["item_id"],{b["item_id"] for b in self.classifier.calls[0]["blocks"]})
        self.assertEqual(task2["result_json"]["ai_metadata"]["quotation_extraction_requests"],0)
        self.assertEqual(self.current(item),old)

    def test_all_five_new_connection_no_key_ai_paused_no_bindings_still_preview_export(self):
        first=self.upload(helpers.FIRST);self.process(first)
        for item in self.items(first): self.confirm(item,"配送費" if item["observation"]["raw_label"]=="配送手数料" else "Human "+item["observation"]["raw_label"])
        before={i["item_id"]:i["current_price"] for i in self.items(first)}
        restarted=IntegrationRuntime(PostgresRepository(helpers.DSN),self.storage,reader=DocumentSdkAdapter(),extractor=None,reader_mode="REAL_SDK")
        second=self.upload(helpers.SECOND,runtime=restarted)
        with TestClient(create_app(helpers.DSN,self.storage,tenant_id=self.actor.tenant_id,integrations=restarted)) as client:
            response=client.post(f"/api/documents/{second['document_id']}/process",json={"idempotency_key":uuid4().hex,"pause_ai":True})
            self.assertEqual(response.status_code,202)
            task=client.get("/api/tasks/"+response.json()["task_id"]).json()
            self.assertIn(task["state"],{"AVAILABLE","REVIEW_REQUIRED"})
            meta=task["result_json"]["ai_metadata"]
            self.assertEqual((meta["calls"],meta["classification_requested_item_ids"],meta["quotation_extraction_requests"]),([],[],0))
            self.assertEqual(len(meta["classification_skipped_item_ids"]),5)
            self.assertTrue(all(i["classification_source"]=="MEMORY" for i in self.items(second)))
            target=self.target(second);loc=target["source_ref"]["locator"]
            preview=client.get(f"/api/documents/{second['document_id']}/preview",params={"sheet":loc["sheet_name"],"cell":loc["A1_range"]})
            self.assertEqual(preview.status_code,200)
            export=client.get(f"/api/cases/{second['case_id']}/export.xlsx")
            book=load_workbook(BytesIO(export.content),read_only=True)
            try:self.assertTrue(any("9500 JPY" in str(row) for row in book["原価単価一覧表"].values))
            finally:book.close()
        self.assertEqual({i["item_id"]:i["current_price"] for i in self.items(first)},before)

    def test_pdf_unknown_document_roles_can_learn_all_five_independently(self):
        doc=self.upload(helpers.PDF);task=self.process(doc)
        self.assertEqual(len(self.items(doc)),5);self.assertEqual(self.slot_count(),0)
        self.assertEqual(len(self.classifier.calls[0]["blocks"]),5)
        for item in self.items(doc):
            self.assertIsNone(item["observation"]["forwarder_id"])
            self.assertIsNone(item["observation"]["context"]["transport_mode"])
            result=self.confirm(item,"Human "+item["observation"]["raw_label"])
            self.assertEqual(result["memory_effect"],"ACTIVE")
        self.assertEqual(self.slot_count(origin="REAL_SDK"),5)

    def test_row_confirmation_unknown_document_columns_and_shared_five_row_export(self):
        first=self.upload(helpers.FIRST);self.process(first)
        pdf=self.upload(helpers.PDF,case_id=first["case_id"]);self.process(pdf)
        # Human test choices only: not parsing rules, AI payload or runtime answers.
        names={"O/F 20'":"Ocean", "O/F 40'":"Ocean", "DOC FEE":"Document", "通関手数料":"Customs", "配送手数料":"Delivery",
               "OCEAN FREIGHT 20'":"Ocean", "OCEAN FREIGHT 40'":"Ocean", "DOCUMENTATION FEE":"Document", "CUSTOMS CLEARANCE":"Customs", "DELIVERY CHARGE":"Delivery"}
        # Existing item confirmation API remains compatible; then confirm actual
        # grouped row atomically with existing row service and per-member memory.
        for item in self.items(first)+self.items(pdf):self.confirm(item,names[item["observation"]["raw_label"]])
        docs=self.demo.list_documents(self.actor,first["case_id"])
        items=self.demo.list_items(self.actor,first["case_id"])
        projection=comparison_projection(items,self.demo.list_fees(self.actor),docs)
        self.assertEqual(len(projection["rows"]),5)
        self.assertEqual(set(projection["companies"]),{helpers.FIRST,helpers.PDF})
        self.assertTrue(all(len(r["member_items"])==2 for r in projection["rows"]))
        self.assertEqual({r["differentiating_conditions"].get("equipment") for r in projection["rows"] if r["label"]=="Ocean"},{"20FT","40FT"})
        row=next(r for r in projection["rows"] if r["label"]=="Delivery")
        cmd=RowConfirmationCommand(first["case_id"],row["row_id"],row["member_items"][0]["case_revision"],
            tuple(RowMember(i["item_id"],i["version"],i["current_memory_version"]) for i in row["member_items"]),uuid4().hex,
            target_fee_id=row["member_items"][0]["current_fee_id"],
            expected_fee_id=row["member_items"][0]["current_fee_id"],
            expected_name_version=row["member_items"][0]["effective_fee_name_version"])
        before={i["item_id"]:i["current_price"] for i in items}
        result=RowConfirmationService(self.repo).confirm(self.actor,cmd)
        self.assertEqual(len(result["members"]),2)
        self.assertTrue(all(m["memory_effect"]=="ACTIVE" for m in result["members"]))
        self.assertEqual(RowConfirmationService(self.repo).confirm(self.actor,cmd),result)
        self.assertEqual({i["item_id"]:i["current_price"] for i in self.demo.list_items(self.actor,first["case_id"])},before)

    def test_conflict_revoke_and_explicit_reconfirm_block_automatic_reuse(self):
        first=self.upload(helpers.FIRST);self.process(first);item=self.target(first)
        one=self.confirm(item,"First human name");two=self.confirm(item,"Other human name")
        self.assertEqual(two["memory_effect"],"CONFLICTED")
        second=self.upload(helpers.SECOND);self.classifier.calls.clear();task=self.process(second)
        target=self.target(second);meta=task["result_json"]["ai_metadata"]
        self.assertEqual(meta["memory_lookup_results"][target["item_id"]]["decision"],"REVIEW_REQUIRED")
        self.assertNotIn(target["item_id"],meta["classification_requested_item_ids"])
        self.assertIsNone(target["current_fee_id"])
        command=self.command(item,target_fee_id=one["fee_id"],replace_existing_mapping=True)
        self.assertEqual(self.service.correct(self.actor,command)["memory_effect"],"ACTIVE")
        now=self.current(item)
        revoked=self.service.revoke_memory(self.actor,RevokeCommand(item["item_id"],now["case_revision"],now["version"],now["current_memory_version"],uuid4().hex,"TEST explicit revoke"))
        self.assertEqual(revoked["memory_effect"],"REVOKED")
        self.assertEqual(self.service.classify(self.actor,target["item_id"]).kind.value,"REVIEW_REQUIRED")
        with self.repo.transaction() as conn:
            n=conn.execute("SELECT count(*) AS n FROM sanzo_core.memory_versions WHERE tenant_id=%s",(self.actor.tenant_id,)).fetchone()["n"]
        self.assertEqual(n,4)

    def test_atomic_failure_rollback_idempotency_and_concurrent_version(self):
        doc=self.upload(helpers.FIRST);self.process(doc);item=self.target(doc)
        before=self.current(item);cmd=self.command(item,new_fee_name="Atomic human")
        def failure(stage):
            if stage=="after_memory":raise RuntimeError("INJECTED_TEST_CASE")
        with self.assertRaisesRegex(RuntimeError,"INJECTED_TEST_CASE"): ReviewService(self.repo,fault_hook=failure).correct(self.actor,cmd)
        self.assertEqual(self.current(item),before);self.assertEqual(self.slot_count(),0)
        result=self.service.correct(self.actor,cmd)
        self.assertEqual(self.service.correct(self.actor,cmd),result)
        with self.assertRaises(ConflictError):self.service.correct(self.actor,replace(cmd,new_fee_name="changed request"))
        first=self.command(item,target_fee_id=result["fee_id"]);second=replace(first,idempotency_key=uuid4().hex)
        barrier=Barrier(2)
        def apply(command):
            barrier.wait()
            try: self.service.correct(self.actor,command);return "saved"
            except ConflictError:return "conflict"
        with ThreadPoolExecutor(2) as pool: outcomes=list(pool.map(apply,[first,second]))
        self.assertCountEqual(outcomes,["saved","conflict"])

    def legacy_seed(self,doc,name,company):
        # Real SDK line + explicit historical role fixture. Seed only isolated DB;
        # bypass new policy just while reproducing old persisted key semantics.
        reading=self.reader.read(self.storage/doc["storage_key"],doc)
        from sanzo_demo.human_field_bindings import apply_human_bindings
        reading=apply_human_bindings(reading,doc,self.actor,self.human_bindings(doc))
        lines,_=structure_facts(doc,self.actor.tenant_id,reading)
        line=next(l for l in lines if l.observation.raw_label=="配送手数料")
        line=replace(line,observation=replace(line.observation,forwarder_id=company))
        old=lambda obs,source=None,kind=None: obs.memory_key()
        with patch("sanzo_core.fee_policy.uses_fee_policy",return_value=False),patch("sanzo_core.domain.memory_key",old),patch("sanzo_core.postgres.memory_key",old),patch("sanzo_core.service.memory_key",old),patch("sanzo_core.demo_store.memory_key",old):
            with self.repo.transaction() as conn:
                self.demo._insert_line(conn,self.actor,doc,line);self.repo.advance_case(conn,self.actor.tenant_id,doc["case_id"])
            item=self.target(doc)
            result=self.confirm(item,name)
        return item,result,line.observation.memory_key()

    def test_legacy_unique_human_projection_is_read_only_company_independent(self):
        first=self.upload(helpers.FIRST);old,result,key=self.legacy_seed(first,"Legacy human","Historical company A")
        with self.repo.transaction() as conn: before=conn.execute("SELECT row_to_json(v) AS v FROM sanzo_core.memory_versions v WHERE tenant_id=%s",(self.actor.tenant_id,)).fetchall()
        second=self.upload(helpers.SECOND);task=self.process(second)
        self.assertIn(task["state"],{"AVAILABLE","REVIEW_REQUIRED"}, {"state":task["state"],"error_code":task.get("error_code"),"events":[e["state"] for e in task["events"]]})
        target=self.target(second)
        self.assertEqual(target["current_fee_id"],result["fee_id"])
        self.assertEqual(target["current_price"],"9500")
        match=task["result_json"]["ai_metadata"]["memory_lookup_results"][target["item_id"]]
        self.assertEqual(match["memory_key"],key);self.assertEqual(match["memory_resolution"],"LEGACY_PROJECTION")
        self.assertEqual(target["current_memory_version"],0)
        with self.repo.transaction() as conn: after=conn.execute("SELECT row_to_json(v) AS v FROM sanzo_core.memory_versions v WHERE tenant_id=%s",(self.actor.tenant_id,)).fetchall()
        self.assertEqual(before,after)
        # Revocation shadows the projected legacy key with v2 version 1;
        # neither old quote nor historical version is deleted/reassigned.
        now=self.current(target)
        self.service.revoke_memory(self.actor,RevokeCommand(target["item_id"],now["case_revision"],now["version"],0,uuid4().hex,"Legacy projected revoke"))
        self.assertEqual(self.service.classify(self.actor,target["item_id"]).kind.value,"REVIEW_REQUIRED")
        with self.repo.transaction() as conn: self.assertEqual(self.repo.slot(conn,self.actor.tenant_id,key)["status"],"ACTIVE")

    def test_two_legacy_company_keys_conflict_never_choose_or_send_target_to_ai(self):
        self.legacy_seed(self.upload(helpers.FIRST),"Legacy X","Company A")
        self.legacy_seed(self.upload(helpers.FIRST),"Legacy Y","Company B")
        second=self.upload(helpers.SECOND);task=self.process(second)
        self.assertIn(task["state"],{"AVAILABLE","REVIEW_REQUIRED"}, {"state":task["state"],"error_code":task.get("error_code"),"events":[e["state"] for e in task["events"]]})
        target=self.target(second)
        meta=task["result_json"]["ai_metadata"];hit=meta["memory_lookup_results"][target["item_id"]]
        self.assertEqual(hit["decision"],"REVIEW_REQUIRED")
        self.assertEqual(len(hit["memory_evidence_versions"]),2)
        self.assertNotIn(target["item_id"],meta["classification_requested_item_ids"])
        self.assertIsNone(target["current_fee_id"])

    def test_legacy_revoked_key_cannot_resurrect_old_confirmation(self):
        first=self.upload(helpers.FIRST);item,result,key=self.legacy_seed(first,"Legacy human","Company A")
        old=lambda obs,source=None,kind=None:obs.memory_key()
        with patch("sanzo_core.fee_policy.uses_fee_policy",return_value=False),patch("sanzo_core.domain.memory_key",old),patch("sanzo_core.postgres.memory_key",old),patch("sanzo_core.service.memory_key",old),patch("sanzo_core.demo_store.memory_key",old):
            now=self.current(item)
            self.service.revoke_memory(self.actor,RevokeCommand(item["item_id"],now["case_revision"],now["version"],now["current_memory_version"],uuid4().hex,"historical revoke fixture"))
        second=self.upload(helpers.SECOND);task=self.process(second)
        self.assertIn(task["state"],{"AVAILABLE","REVIEW_REQUIRED"}, {"state":task["state"],"error_code":task.get("error_code"),"events":[e["state"] for e in task["events"]]})
        target=self.target(second)
        meta=task["result_json"]["ai_metadata"]
        self.assertEqual(meta["memory_lookup_results"][target["item_id"]]["decision"],"REVIEW_REQUIRED")
        self.assertNotIn(target["item_id"],meta["classification_requested_item_ids"])
