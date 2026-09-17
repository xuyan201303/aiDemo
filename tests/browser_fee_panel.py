"""Offline real-browser interaction probes. HTTP responses are explicit substitutes.
No AI, SDK, uploads, or real Demo database writes. PostgreSQL saves are separate.
"""
import argparse, copy, json, threading, traceback
from pathlib import Path
from playwright.sync_api import sync_playwright, expect
from sanzo_demo.comparison import comparison_projection
ROOT=Path(__file__).resolve().parents[1]
BASE='http://127.0.0.1:8765'

def fixture():
    def item(i,company):
        return {'item_id':i,'version':1,'case_revision':1,'current_memory_version':0,'current_fee_id':None,
            'current_price':'8000.123400','currency':'JPY','ai_suggested_name':'Delivery fee','input_kind':'SYNTHETIC_STRUCTURED',
            'observation':{'raw_label':'Raw '+i,'forwarder_id':company,'document_id':'doc-'+i,'source_ready':True,
                'context':{'equipment':'20FT','transport_mode':'SEA','billing_unit':'CONTAINER','charge_scope':'ORIGIN','extra_terms':'STANDARD','ready':True}},
            'source_ref':{'kind':'EXCEL_RANGE','locator_status':'VERIFIED','document_id':'doc-'+i,'locator':{'sheet_name':'Synthetic','A1_range':'B2'}}}
    items=[item('a','A社'),item('b','B社')]
    snapshot={'case':{'case_id':'case','name':'Synthetic browser probes','case_revision':1},'items':items,'documents':[{'document_id':'doc-'+i,'original_filename':'Original-'+i+'.xlsx','state':'AVAILABLE'} for i in ['a','b']],'tasks':[],
        'fees':[{'fee_id':'f1','display_name':'Delivery fee','name_version':1},{'fee_id':'dup1','display_name':'同名','name_version':1},{'fee_id':'dup2','display_name':'同名','name_version':1},{'fee_id':'accent','display_name':'Café fee','name_version':1}]}
    snapshot['comparison']=comparison_projection(items,snapshot['fees'])
    return snapshot

def run(out):
    out.mkdir(parents=True,exist_ok=False);result={'scope':'OFFLINE_BROWSER_HTTP_SUBSTITUTES','ai_calls':0,'database_writes':0,'checks':[]};requests=[]
    def check(label,value=True):
        assert value,label
        result['checks'].append(label);print('PASS '+label,flush=True)
    try:
        snap=fixture(); mode={'post':'conflict','preview_fail':False};errors=[]
        with sync_playwright() as pw:
            browser=pw.chromium.launch(executable_path='/Applications/Google Chrome.app/Contents/MacOS/Google Chrome',headless=True)
            page=browser.new_page(viewport={'width':1366,'height':768});page.on('pageerror',lambda e:errors.append(str(e)))
            def route(r):
                path=r.request.url.removeprefix(BASE)
                if r.request.method=='POST':
                    assert '/comparison/' in path and path.endswith('/confirmation')
                    requests.append(r.request.post_data_json)
                    if mode['post']=='abort':return r.abort('connectionreset')
                    if mode['post']=='slow':
                        import time;time.sleep(.25)
                    return r.fulfill(status=409,content_type='application/json',body=json.dumps({'detail':'BOUNDARY_CONFLICT'}))
                assert r.request.method=='GET'
                if path=='/':return r.fulfill(path=str(ROOT/'backend/sanzo_demo/static/index.html'))
                if path.startswith('/static/'):return r.fulfill(path=str(ROOT/'backend/sanzo_demo/static'/path.rsplit('/',1)[-1]))
                if '/preview' in path:return r.fulfill(status=404 if mode['preview_fail'] else 200,content_type='text/html',body='<p>Explicit offline preview substitute</p>')
                if path=='/api/status':data={'reader_mode':'MOCK_SDK','reader':{'ready':True},'sdk':{'ready':False},'ai':{'ready':False},'missing_conditions':[]}
                elif path=='/api/cases':data=[snap['case']]
                elif path=='/api/cases/case':data=snap
                else:raise AssertionError(path)
                r.fulfill(content_type='application/json',body=json.dumps(data))
            page.route('**/*',route)
            def open_panel():
                page.goto(BASE,wait_until='networkidle');page.locator('#case-select').select_option('case')
                page.locator('.row-confirm-button').first.click();expect(page.locator('#edit-dialog')).to_be_visible()
            def name(v):page.locator('#edit-fee-name').fill(v)
            def save():page.locator('#save-correction').click()
            def refresh():
                if not page.locator('#edit-details').evaluate('(e)=>e.open'):page.locator('#edit-details > summary').click()
                page.locator('#refresh-item').click()
            open_panel()
            check('compact desktop all controls visible',page.locator('#edit-dialog').bounding_box()['height']<650)
            expect(page.locator('.row-member')).to_have_count(2)
            expect(page.locator('#edit-details')).not_to_have_attribute('open','')
            check('one visible name field no price/currency inputs',page.locator('#edit-dialog input:visible').count()==1 and page.locator('#edit-price,#edit-currency,#amount-only').count()==0)
            expect(page.locator('#edit-heading')).to_have_text('統一費目を確認')
            expect(page.locator('#edit-details > summary')).to_have_text('詳細を見る')
            for control in ['#edit-warning','#edit-dimensions','#refresh-item','#replace-mapping','#revoke-button','#edit-status','#edit-unified-name']:
                expect(page.locator(control)).not_to_be_visible()
            check('default business view hides all audit controls',page.locator('#edit-dialog select:visible,#edit-dialog input[type=checkbox]:visible').count()==0)
            check('source prices retain precision','8,000.123400 JPY' in page.locator('.row-member').first.inner_text())
            page.screenshot(path=str(out/'browser_normal.png'))
            name('下書き');page.locator('.member-preview').first.click();expect(page.locator('#preview-dialog')).to_be_visible()
            check('preview uses correct id and locator','doc-a/preview?sheet=Synthetic&cell=B2' in page.locator('#preview-frame').get_attribute('src'))
            page.locator('#preview-dialog .close-dialog').click();expect(page.locator('#edit-fee-name')).to_have_value('下書き')
            check('preview roundtrip preserves input',len(requests)==0)
            mode['preview_fail']=True;page.locator('.member-preview').last.click();page.locator('#preview-dialog .close-dialog').click()
            expect(page.locator('#edit-fee-name')).to_have_value('下書き');check('failed preview preserves draft and does not save',len(requests)==0)
            name('');save();expect(page.locator('#edit-error')).to_have_text('統一費目名を入力してください。')
            name(' \u3000 ');save();expect(page.locator('#edit-error')).to_have_text('統一費目名を入力してください。')
            name('長'*1025);save();expect(page.locator('#edit-error')).to_contain_text('1024文字');expect(page.locator('#edit-fee-name')).to_have_value('長'*1025)
            check('empty whitespace and overlength rejected without truncation',len(requests)==0)
            name('IME');page.locator('#edit-fee-name').dispatch_event('compositionstart');page.locator('#edit-fee-name').press('Enter');page.locator('#edit-form').evaluate('(f)=>f.requestSubmit()')
            check('IME Enter and composition submit do not save',len(requests)==0)
            page.locator('#edit-fee-name').dispatch_event('compositionend');page.wait_for_timeout(120)
            name('同名');save();expect(page.locator('#edit-error')).to_contain_text('候補が複数');check('same-name candidates never pick first',len(requests)==0)
            page.locator('[data-fee-id="dup2"]').click();save();expect(page.locator('#edit-error')).to_contain_text('更新があります')
            check('explicit duplicate candidate uses selected ID',requests[-1]['target_fee_id']=='dup2' and 'new_fee_name' not in requests[-1])
            expect(page.locator('#edit-fee-name')).to_have_value('同名');expect(page.locator('.row-member')).to_have_count(2)
            page.screenshot(path=str(out/'browser_conflict.png'))
            name(' Cafe\u0301\u3000fee ');save();expect(page.locator('#edit-error')).to_contain_text('更新があります')
            check('NFC whitespace exact existing ID',requests[-1]['target_fee_id']=='accent')
            name('長'*1024);save();expect(page.locator('#edit-error')).to_contain_text('更新があります');check('API max 1024 accepted',len(requests[-1]['new_fee_name'])==1024)
            name('<img src=x onerror=alert(1)>');save();expect(page.locator('#edit-error')).to_contain_text('更新があります');check('name text safely encoded',requests[-1]['new_fee_name']=='<img src=x onerror=alert(1)>' and page.locator('#edit-dialog img').count()==0)
            # Mutate the global refreshed snapshot after opening; the request must retain pinned members/versions.
            page.evaluate('state.snapshot.comparison.rows[0].member_items.push({...state.snapshot.items[0],item_id:"unseen"});state.snapshot.case.case_revision=999')
            name('Pinned');save();expect(page.locator('#edit-error')).to_contain_text('更新があります')
            check('opened item set and revision stay pinned',[m['item_id'] for m in requests[-1]['members']]==['a','b'] and requests[-1]['expected_case_revision']==1)
            check('name-only request ignores DOM price edits',not {'unit_price','currency'} & requests[-1].keys())
            mode['post']='abort';name('Lost response');save();expect(page.locator('#edit-error')).to_contain_text('保存結果を確認できません')
            first=copy.deepcopy(requests[-1]);expect(page.locator('#edit-fee-name')).to_be_disabled();expect(page.locator('#revoke-button')).to_be_disabled()
            refresh();expect(page.locator('#edit-error')).to_contain_text('要求の版とキーは保持')
            save();expect(page.locator('#edit-error')).to_contain_text('保存結果を確認できません')
            check('lost response retry and refresh retain exact request/key',requests[-1]==first)
            page.locator('#edit-dialog .close-dialog').click();expect(page.locator('#edit-dialog')).to_be_visible();check('unknown outcome not discarded or reported success')
            mode['post']='conflict';save();expect(page.locator('#edit-fee-name')).to_be_enabled()
            # Explicit conflict refresh must keep draft and show current source cards.
            refresh();expect(page.locator('#edit-fee-name')).to_have_value('Lost response')
            check('409 refresh retains draft without automatic save')
            name('Dirty');dialogs=[]
            def dismiss(d):dialogs.append(d.message);d.dismiss()
            page.on('dialog',dismiss);page.locator('#edit-dialog .close-dialog').click();expect(page.locator('#edit-dialog')).to_be_visible()
            page.keyboard.press('Escape');expect(page.locator('#edit-dialog')).to_be_visible();check('dirty close and Escape ask before discard',len(dialogs)==2)
            page.remove_listener('dialog',dismiss);page.once('dialog',lambda d:d.accept());page.locator('#edit-dialog .close-dialog').click()
            expect(page.locator('.row-confirm-button').first).to_be_focused();check('close restores row control focus')
            # Full member cardinality and company order, unknown price/source, long labels.
            snap=fixture();a=copy.deepcopy(snap['items'][0]);a['item_id']='a2';a['observation']['document_id']='doc-a';a['current_price']=None
            c=copy.deepcopy(a);c['item_id']='c';c['observation']['forwarder_id']='C社';c['observation']['raw_label']='長い原文 <script> '+('配送名称'*30);c['source_ref']['locator_status']='UNAVAILABLE'
            snap['items'] += [a,c];snap['comparison']=comparison_projection(snap['items'],snap['fees']);snap['comparison']['companies']=['C社','B社','A社']
            open_panel();expect(page.locator('.row-member')).to_have_count(4)
            check('three companies duplicate company preserved in column order',page.locator('.row-member').evaluate_all('(es)=>es.map(e=>e.dataset.memberId)')==['c','b','a','a2'])
            expect(page.locator('[data-member-id=a2] .member-price')).to_contain_text('—');expect(page.locator('[data-member-id=c] .member-source')).to_contain_text('位置未取得')
            check('unknown price not zero and unknown position honest')
            page.set_viewport_size({'width':390,'height':844});name('長い統一費目名'*20)
            page.screenshot(path=str(out/'browser_narrow_long.png'))
            check('narrow dialog no horizontal overflow',page.locator('#edit-dialog').evaluate('(e)=>e.scrollWidth<=e.clientWidth'))
            check('long cards scroll but save visible',page.locator('#save-correction').bounding_box()['y']+46<844)
            mode['post']='slow';before=len(requests)
            page.locator('#edit-form').evaluate('(f)=>{f.requestSubmit();f.requestSubmit();}')
            expect(page.locator('#edit-error')).to_contain_text('更新があります');check('double submit sends once',len(requests)==before+1)
            # Inconsistent confirmed labels cannot silently initialize from the first.
            page.set_viewport_size({'width':1366,'height':768});snap=fixture()
            for n,i in enumerate(snap['items']):
                i['current_fee_id']='f1';i['current_fee_name']=['Confirmed English','異なる確認名称'][n]
            snap['comparison']=comparison_projection(snap['items'],snap['fees'])
            open_panel();expect(page.locator('#edit-fee-name')).to_have_value('');expect(page.locator('#edit-warning')).to_contain_text('確認済み名称が一致していません')
            check('conflicting confirmed names not silently chosen')
            page.screenshot(path=str(out/'browser_name_conflict.png'))
            # A malformed projection with known condition conflict is visibly flagged.
            page.evaluate("state.snapshot.comparison.rows[0].member_items[1].observation.context.equipment='40FT';openRowEdit(state.snapshot.comparison.rows[0]);")
            expect(page.locator('#edit-warning')).to_contain_text('適用条件が一致しません');check('known condition conflict warning before save')
            check('no JS errors',not errors)
            result['status']='PASS';browser.close()
    except Exception:
        result['status']='FAIL';result['error']=traceback.format_exc();print(result['error'])
    finally:
        (out/'requests.json').write_text(json.dumps(requests,ensure_ascii=False,indent=2))
        (out/'result.json').write_text(json.dumps(result,ensure_ascii=False,indent=2))
    return 0 if result['status']=='PASS' else 1
if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--evidence',type=Path,required=True);raise SystemExit(run(p.parse_args().evidence))
