"""Dedicated browser-test server, never the application startup entrypoint.
Only the disposable SANZO panel-test database is accepted. Synthetic records and
fault controls live here, not in application routes. No SDK / AI / credentials.
"""
import os
from dataclasses import replace
from hashlib import sha256
from pathlib import Path
from unittest.mock import patch
from openpyxl import Workbook
from fastapi import HTTPException
import uvicorn
from sanzo_core.domain import Context,Observation,Principal,SyntheticLine
from sanzo_core.postgres import PostgresRepository
from sanzo_core.service import ReviewService
from sanzo_core.demo_store import DemoStore
from sanzo_demo.app import create_app
from sanzo_demo.row_confirmation import RowConfirmationService

DSN=os.environ['SANZO_TEST_DSN']
if '-panel-' not in DSN or not DSN.endswith('/sanzo_panel_test'):
    raise RuntimeError('Only the dedicated disposable panel database is accepted')
repo=PostgresRepository(DSN);repo.migrate();store=DemoStore(repo);service=ReviewService(repo)
actor=Principal('panel-browser-test','local-demo-operator');storage=Path('/tmp/panel-originals');storage.mkdir(exist_ok=True)
ctx=Context('ROAD','TRUCK','PER_DELIVERY','DELIVERY_ONLY','STANDARD')

def seed(case_id, count=2, price='8000'):
    service.create_case(actor,case_id,'専用テスト・合成データ '+case_id)
    for n in range(count):
        suffix=case_id+'-'+str(n);doc='doc-'+suffix;source='source-'+suffix
        company=['A社','B社','C社','A社'][n];raw=['配送原文A','DELIVERY RAW B','Delivery raw C','配送原文A'][n]
        amount=price if n==0 else str(9000+n-1)
        path=storage/(suffix+'.xlsx');book=Workbook();book.active.title='Synthetic';book.active.append(['原文','今回単価','通貨']);book.active.append([raw,amount,'JPY']);book.save(path);book.close()
        digest=sha256(path.read_bytes()).hexdigest()
        store.register_document(actor,case_id,document_id=doc,original_filename='SYNTHETIC_'+suffix+'.xlsx',media_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',size_bytes=path.stat().st_size,sha256=digest,storage_key=path.name)
        obs=Observation(actor.tenant_id,'item-'+suffix,case_id,doc,source,company,raw,amount,'JPY',replace(ctx,transport_mode=None,ready=False) if n==1 else ctx,True)
        ref={'synthetic':True,'document_id':doc,'document_revision':1,'document_sha256':digest,'source_block_ids':[source],'kind':'EXCEL_RANGE','locator_status':'VERIFIED','locator':{'sheet_name':'Synthetic','A1_range':'A2:C2'}}
        store.add_synthetic_lines(actor,doc,[SyntheticLine(observation=obs,source_ref=ref,ai_suggested_name='Delivery proposal',raw_value=amount)])

if not store.list_cases(actor):
    seed('first');seed('rollback');seed('lost');seed('many',4)
app=create_app(DSN,storage,tenant_id=actor.tenant_id)
fault={'enabled':False,'count':0}
@app.middleware('http')
async def test_fault(request,call_next):
    if fault['enabled'] and request.method=='POST' and request.url.path.endswith('/confirmation'):
        def checkpoint(stage):
            if stage=='after_item':
                fault['count']+=1
                if fault['count']==2:
                    fault['enabled']=False
                    raise RuntimeError('INJECTED_SECOND_MEMBER_FAILURE')
        with patch('sanzo_demo.app.RowConfirmationService',lambda repository:RowConfirmationService(repository,checkpoint)):
            return await call_next(request)
    return await call_next(request)
@app.post('/__test/fault')
def enable_fault():
    fault.update(enabled=True,count=0);return {'test_fault':'second_member_once'}
@app.post('/__test/next')
def next_case():
    if any(c['case_id']=='next' for c in store.list_cases(actor)):raise HTTPException(409)
    seed('next',1,'9500');return {'case_id':'next'}
@app.get('/__test/audit')
def audit():
    # Read only this explicitly synthetic tenant. No application/AI credentials.
    with repo.transaction() as conn:
        names=conn.execute("SELECT DISTINCT table_name FROM information_schema.columns WHERE table_schema='sanzo_core' AND column_name='tenant_id' ORDER BY table_name").fetchall()
        from psycopg import sql
        return {r['table_name']:conn.execute(sql.SQL('SELECT * FROM sanzo_core.{} WHERE tenant_id=%s').format(sql.Identifier(r['table_name'])),(actor.tenant_id,)).fetchall() for r in names}
if __name__=='__main__':uvicorn.run(app,host='0.0.0.0',port=8765,access_log=False)
