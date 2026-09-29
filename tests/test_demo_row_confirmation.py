"""Row confirmation mechanisms: synthetic input, real PostgreSQL; no AI calls."""
import json
import os
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, replace
from threading import Barrier
from uuid import uuid4
from io import BytesIO
from fastapi.testclient import TestClient
from openpyxl import load_workbook

from sanzo_core.domain import Context, Principal, ConflictError, RevokeCommand
from sanzo_core.postgres import PostgresRepository
from sanzo_core.service import ReviewService
from sanzo_core.demo_store import DemoStore
from sanzo_demo.app import create_app
from sanzo_demo.comparison import comparison_projection
from sanzo_demo.row_confirmation import RowConfirmationCommand, RowConfirmationService, RowMember
import test_postgres as core_tests
from test_demo_comparison_projection import quote

DSN = os.environ.get('SANZO_TEST_DSN')

class RowProjectionTests(unittest.TestCase):
    def test_row_confirmation_separate_from_members_and_price_cells(self):
        a, b = quote('a', 'A', fee_id='fee'), quote('b', 'B', transport_mode=None, ready=False)
        row = comparison_projection([a, b])['rows'][0]
        self.assertEqual(row['confirmation_status'], '● AI仮判定')
        self.assertEqual(len(row['member_items']), 2)
        self.assertFalse(row['member_items'][1]['learning_eligible'])
        self.assertIsNone(b['current_fee_id'])
        for cell in row['cells_by_forwarder']['B']:
            self.assertEqual(set(cell), {'price', 'currency', 'item_id', 'source_ref'})
        self.assertEqual(row['differentiating_conditions'], {'equipment': '20FT'})

    def test_revoked_memory_blocks_row_despite_saved_fee_name(self):
        a = quote('a','A',fee_id='fee')
        a['current_memory_status'] = 'REVOKED'
        row = comparison_projection([a])['rows'][0]
        self.assertEqual(row['confirmation_status'], '⚠ 確認必要')
        self.assertEqual(row['cells']['A'][0]['status'], '⚠ 確認必要')
        self.assertEqual(row['member_items'][0]['current_memory_status'], 'REVOKED')

    def test_one_blocked_member_keeps_confirmed_identity_row_pending(self):
        first = quote('a', 'A', fee_id='fee')
        second = quote('b', 'B', fee_id='fee', ready=False)
        row = comparison_projection([first, second])['rows'][0]
        self.assertEqual(set(row['item_ids']), {'a', 'b'})
        self.assertEqual(row['cells']['A'][0]['status'], '✓ 確認済み')
        self.assertEqual(row['cells']['B'][0]['status'], '⚠ 確認必要')
        self.assertEqual(row['confirmation_status'], '⚠ 確認必要')

    def test_ai_and_ambiguous_row_states(self):
        a,b,c = quote('a','A'),quote('b','B',equipment=None,ready=False),quote('c','C',equipment='40FT')
        self.assertEqual(comparison_projection([a])['rows'][0]['confirmation_status'],'● AI仮判定')
        rows = comparison_projection([a,b,c])['rows']
        self.assertTrue(any(r['confirmation_status']=='⚠ 確認必要' for r in rows))
        self.assertEqual(len(rows),3)


@unittest.skipUnless(DSN, 'NOT_RUN: dedicated real PostgreSQL required')
class RowConfirmationPostgresTests(unittest.TestCase):
    add_line = core_tests.PostgresIntegrationTests.add_line
    database_snapshot = core_tests.PostgresIntegrationTests.snapshot

    def setUp(self):
        self.dsn=DSN
        self.tenant='row-test-'+uuid4().hex
        self.actor=Principal(self.tenant,'local-demo-operator')
        self.repo=PostgresRepository(DSN); self.repo.migrate()
        self.service=ReviewService(self.repo); self.store=DemoStore(self.repo)
        self.context=Context('SEA','20FT','CONTAINER','ORIGIN','STANDARD')
        self.add_line('a',raw_label='ORIGINAL-A',ai_suggested_name='Unified')
        self.add_line('b',existing_case='case-a',forwarder_id='company-B',raw_label='ORIGINAL-B',
                      unit_price='9',ai_suggested_name='Unified',context=replace(self.context,transport_mode=None,ready=False))
        self.row_service=RowConfirmationService(self.repo)

    def command(self, **changes):
        snap=self.store.case_snapshot(self.actor,'case-a')
        row=comparison_projection(snap['items'],snap['fees'])['rows'][0]
        values=dict(case_id='case-a',row_id=row['row_id'],expected_case_revision=snap['case']['case_revision'],
            members=tuple(RowMember(i['item_id'],i['version'],i['current_memory_version']) for i in row['member_items']),
            idempotency_key=uuid4().hex,new_fee_name='Confirmed name')
        values.update(changes)
        if values.get("target_fee_id") is not None:
            selected = next(fee for fee in snap["fees"] if fee["fee_id"] == values["target_fee_id"])
            values.setdefault("expected_fee_id", selected["fee_id"])
            values.setdefault("expected_name_version", selected["name_version"])
        return RowConfirmationCommand(**values)

    def test_all_members_confirm_but_only_eligible_learning_and_new_price(self):
        before=self.store.list_items(self.actor,'case-a')
        result=self.row_service.confirm(self.actor,self.command())
        self.assertEqual([x['memory_effect'] for x in result['members']],['ACTIVE','NOT_ELIGIBLE_CONTEXT_OR_SOURCE'])
        after=self.store.list_items(self.actor,'case-a')
        for old,item in zip(before,after):
            for k in ['observation','source_ref','raw_value','current_price','currency','ai_suggested_name']:
                self.assertEqual(old[k],item[k])
            self.assertEqual(item['current_fee_id'],result['fee_id'])
        with self.repo.transaction() as conn:
            versions=conn.execute('SELECT raw_label,fee_id FROM sanzo_core.memory_versions WHERE tenant_id=%s',(self.tenant,)).fetchall()
        self.assertEqual(versions,[{'raw_label':'ORIGINAL-A','fee_id':result['fee_id']}])
        self.add_line('next',raw_label='ORIGINAL-A',unit_price='20',ai_suggested_name='Another guess')
        decision=ReviewService(PostgresRepository(DSN)).classify(self.actor,'item-next')
        self.assertEqual((decision.fee_id,decision.unit_price),(result['fee_id'],'20'))
        self.assertEqual(self.service.get_item(self.actor,'item-a')['current_price'],'10')

    def test_two_eligible_raw_labels_each_get_scoped_memory(self):
        self.add_line('c',existing_case='case-a',forwarder_id='company-C',raw_label='ORIGINAL-C',ai_suggested_name='Unified')
        result=self.row_service.confirm(self.actor,self.command())
        self.assertEqual([m['memory_effect'] for m in result['members']],['ACTIVE','NOT_ELIGIBLE_CONTEXT_OR_SOURCE','ACTIVE'])
        with self.repo.transaction() as conn:
            rows=conn.execute('SELECT raw_label,fee_id FROM sanzo_core.memory_versions WHERE tenant_id=%s ORDER BY raw_label',(self.tenant,)).fetchall()
        self.assertEqual([r['raw_label'] for r in rows],['ORIGINAL-A','ORIGINAL-C'])
        self.assertTrue(all(r['fee_id']==result['fee_id'] for r in rows))

    def test_duplicate_alias_members_keep_each_event_without_stale_self_conflict(self):
        self.add_line('a2',existing_case='case-a',raw_label='ORIGINAL-A',ai_suggested_name='Unified',unit_price='11')
        result=self.row_service.confirm(self.actor,self.command())
        self.assertEqual([m['memory_version'] for m in result['members']],[1,2,0])
        self.assertEqual(len(result['members']),3)
        self.assertEqual([i['current_price'] for i in self.store.list_items(self.actor,'case-a')],['10','9','11'])

    def test_idempotency_and_changed_payload(self):
        command=self.command(); result=self.row_service.confirm(self.actor,command)
        after=self.database_snapshot()
        self.assertEqual(self.row_service.confirm(self.actor,command),result)
        self.assertEqual(self.database_snapshot(),after)
        with self.assertRaises(ConflictError):self.row_service.confirm(self.actor,replace(command,new_fee_name='DIFFERENT'))
        self.assertEqual(self.database_snapshot(),after)

    def test_failure_on_second_member_rolls_back_everything(self):
        calls=[]
        def fault(stage):
            if stage=='after_item':
                calls.append(stage)
                if len(calls)==2:raise RuntimeError('INJECTED_TEST_CASE_SECOND_MEMBER')
        before=self.database_snapshot()
        with self.assertRaisesRegex(RuntimeError,'INJECTED_TEST_CASE'):
            RowConfirmationService(self.repo,fault).confirm(self.actor,self.command())
        self.assertEqual(len(calls),2)
        self.assertEqual(self.database_snapshot(),before)

    def test_stale_member_or_memory_saves_nothing(self):
        command=self.command(); before=self.database_snapshot()
        for member in [replace(command.members[1],expected_item_version=9),replace(command.members[1],expected_memory_version=9)]:
            with self.assertRaises(ConflictError):
                self.row_service.confirm(self.actor,replace(command,members=(command.members[0],member)))
            self.assertEqual(self.database_snapshot(),before)

    def test_foreign_subset_duplicate_or_wrong_row_rejected(self):
        command=self.command(); before=self.database_snapshot()
        bad=[replace(command,members=command.members[:1]),replace(command,row_id='0'*64),
             replace(command,members=(command.members[0],command.members[0])),
             replace(command,members=(command.members[0],replace(command.members[1],item_id='foreign')))]
        for attempt in bad:
            with self.assertRaises((ValueError,ConflictError)):self.row_service.confirm(self.actor,attempt)
            self.assertEqual(self.database_snapshot(),before)

    def test_concurrent_row_save_one_winner_and_no_partial_result(self):
        command=self.command(); barrier=Barrier(2)
        def run(n):
            barrier.wait()
            try:return self.row_service.confirm(self.actor,replace(command,idempotency_key='concurrent-'+str(n)))
            except ConflictError:return 'CONFLICT'
        with ThreadPoolExecutor(2) as pool:results=list(pool.map(run,[1,2]))
        self.assertEqual(sum(isinstance(r,dict) for r in results),1)
        self.assertEqual(results.count('CONFLICT'),1)
        self.assertEqual([i['version'] for i in self.store.list_items(self.actor,'case-a')],[2,2])

    def test_revoked_memory_requires_explicit_reconfirmation(self):
        saved=self.row_service.confirm(self.actor,self.command())
        a=next(i for i in self.store.list_items(self.actor,'case-a') if i['item_id']=='item-a')
        self.service.revoke_memory(self.actor,RevokeCommand('item-a',a['case_revision'],a['version'],a['current_memory_version'],uuid4().hex,'Test revocation'))
        command=self.command(new_fee_name=None,target_fee_id=saved['fee_id'])
        result=self.row_service.confirm(self.actor,command)
        self.assertEqual(result['members'][0]['memory_effect'],'REVOKED')
        command=self.command(new_fee_name=None,target_fee_id=saved['fee_id'],replace_existing_mapping=True)
        self.assertEqual(self.row_service.confirm(self.actor,command)['members'][0]['memory_effect'],'ACTIVE')

    def test_http_rejects_price_fields_and_shared_export_has_row_audit(self):
        with tempfile.TemporaryDirectory() as storage,TestClient(create_app(DSN,storage,tenant_id=self.tenant)) as client:
            command=self.command();body=asdict(command);body.pop('case_id');body.pop('row_id')
            url=f'/api/cases/case-a/comparison/{command.row_id}/confirmation'
            for field in ['unit_price','currency','amount_only']:
                self.assertEqual(client.post(url,json={**body,field:'20'}).status_code,422)
            bad=json.loads(json.dumps(body));bad['members'][0]['unit_price']='20'
            self.assertEqual(client.post(url,json=bad).status_code,422)
            response=client.post(url,json=body)
            self.assertEqual(response.status_code,200,response.text)
            data=client.get('/api/cases/case-a').json();row=data['comparison']['rows'][0]
            # B's applicability remains incomplete, even after both members
            # adopt the row fee name; the row and XLSX must remain pending.
            self.assertEqual(row['confirmation_status'],'⚠ 確認必要')
            book=load_workbook(BytesIO(client.get('/api/cases/case-a/export.xlsx').content))
            self.assertEqual(book['原価単価一覧表']['B4'].value,'10 CNY')
            self.assertEqual(book['原価単価一覧表']['C4'].value,'9 CNY')
            self.assertEqual(book['確認情報']['B2'].value,'⚠ 確認必要')
            book.close()
