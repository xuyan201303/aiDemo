"""Run the compact-panel browser against a dedicated temporary PostgreSQL app.
No real Demo volumes, credentials, SDK, or AI calls. Test endpoints exist only
in tests/panel_browser_server.py. PostgreSQL has no published host port.
"""
import argparse,json,sys,time,urllib.request,uuid
from pathlib import Path
from start_demo import Runtime,PROJECT,PREFIX,OWNER_KEY,OWNER,PG_IMAGE

def run(evidence):
    runtime=Runtime(evidence);image=runtime.build();token=uuid.uuid4().hex[:10]
    network,webnet,pg,web=[PREFIX+'-panel-'+token+s for s in ['-net','-webnet','-pg','-web']]
    created=[];networks=[]
    try:
        for name,internal in [(network,True),(webnet,False)]:
            runtime.dk('panel_network','network','create',*(['--internal'] if internal else []),'--label',OWNER_KEY+'='+OWNER,name);networks.append(name)
        runtime.dk('panel_pg_create','create','--pull=never','--name',pg,'--label',OWNER_KEY+'='+OWNER,'--network',network,'--tmpfs','/var/lib/postgresql/data:rw,nosuid,size=512m','-e','POSTGRES_USER=sanzo_demo','-e','POSTGRES_DB=sanzo_panel_test','-e','POSTGRES_HOST_AUTH_METHOD=trust',PG_IMAGE);created.append(pg)
        runtime.dk('panel_pg_start','start',pg);runtime.wait_pg(pg)
        runtime.dk('panel_web_create','create','--pull=never','--name',web,'--label',OWNER_KEY+'='+OWNER,'--network',webnet,'--publish','127.0.0.1:18765:8765','--read-only','--tmpfs','/tmp:rw,nosuid,size=128m','--cap-drop=ALL','--security-opt','no-new-privileges','--mount',f'type=bind,source={PROJECT / "tests"},target=/workspace/tests,readonly','-e',f'SANZO_TEST_DSN=postgresql://sanzo_demo@{pg}:5432/sanzo_panel_test',image,'python','-B','tests/panel_browser_server.py');created.append(web)
        runtime.dk('panel_connect','network','connect',network,web);runtime.dk('panel_web_start','start',web)
        opener=urllib.request.build_opener(urllib.request.ProxyHandler({}))
        for _ in range(60):
            try:
                with opener.open('http://127.0.0.1:18765/api/status',timeout=2) as r:
                    if r.status==200:break
            except OSError:time.sleep(.3)
        else:raise RuntimeError('Dedicated test server did not become ready')
        test=runtime.command('panel_browser',[sys.executable,'-B',str(PROJECT/'tests/browser_fee_panel_postgres.py'),'--evidence',str(evidence/'browser_postgres')],required=False)
        if test.returncode:return test.returncode
        # Restart only this disposable test process. Same dedicated DB persists.
        before=opener.open('http://127.0.0.1:18765/__test/audit').read()
        runtime.dk('panel_restart','restart',web)
        for _ in range(60):
            try:
                with opener.open('http://127.0.0.1:18765/__test/audit',timeout=2) as r:
                    after=r.read();break
            except OSError:time.sleep(.3)
        else:raise RuntimeError('Dedicated restarted server unavailable')
        assert json.loads(before)==json.loads(after),'Restart changed persisted test state'
        (evidence/'restart.json').write_text(json.dumps({'status':'PASS','same_database_state':True,'ai_calls':0,'scope':'DEDICATED_TEST_SERVER'}))
        return 0
    finally:
        if web in created:runtime.dk('panel_web_logs','logs',web,required=False)
        if pg in created:runtime.dk('panel_pg_logs','logs',pg,required=False)
        for name in reversed(created):runtime.remove_owned_container(name)
        for name in reversed(networks):
            if runtime.owned('network',name):runtime.dk('panel_network_cleanup','network','rm',name)
if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--evidence',type=Path,required=True);raise SystemExit(run(p.parse_args().evidence))
