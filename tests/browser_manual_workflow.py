"""Offline browser acceptance: actual file controls + local HTTP stand-ins, never live API."""
from pathlib import Path
from datetime import datetime, timezone, timedelta
from email.parser import BytesParser
from email.policy import default
import argparse, hashlib, json, socket, time, threading
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from playwright.sync_api import sync_playwright, expect
import httpx
from sanzo_demo.http_errors import read_http_error
from sanzo_demo.comparison import comparison_projection

ROOT=Path(__file__).resolve().parents[1]
SAMPLES=ROOT/"demo_materials/samples"
NAMES=["01_A社_初回見積.xlsx","02_B社_初回見積.pdf","03_A社_次回見積.xlsx"]
HASHES=[hashlib.sha256((SAMPLES/n).read_bytes()).hexdigest() for n in NAMES]
def item(doc):
    return {"item_id":"offline-item","observation":{"raw_label":"OFFLINE TEST ONLY","context":{},"forwarder_id":"OFFLINE","document_id":doc["document_id"]},
        "current_price":"1","currency":"JPY","version":1,"current_fee_id":None,"ai_suggested_name":"OFFLINE suggestion",
        "input_kind":"SYNTHETIC_STRUCTURED","source_ref":{},"initial_classification":{"kind":"AI_REQUIRED"}}
class FakeHTTP:
    def __init__(self, kind):
        self.kind=kind;self.docs=[];self.tasks=[];self.items=[];self.calls=[];self.processed=[];self.polls={}
    def status(self):
        return {"reader_mode":"MOCK_SDK","reader":{"ready":True,"supported_sha256":HASHES},
            "sdk":{"ready":False},"ai":{"ready":True,"budget":{"requests_used":30 if self.kind=="budget" else 6,"max_requests":30}},
            "debug_manual":False,"missing_conditions":[]}
    def snapshot(self):
        return {"case":{"case_id":"offline-case","name":"OFFLINE HTTP STAND-IN — "+self.kind,"case_revision":1},
            "documents":self.docs,"tasks":self.tasks,"items":self.items,"fees":[],"comparison":comparison_projection(self.items)}
    def handle(self, route):
        req=route.request;path=req.url.split("/api/",1)[-1]
        self.calls.append({"method":req.method,"path":path})
        def respond(data,status=200):route.fulfill(status=status,content_type="application/json",body=json.dumps(data,ensure_ascii=False))
        if path=="status":return respond(self.status())
        if path=="cases":return respond([self.snapshot()["case"]])
        if path=="cases/offline-case":return respond(self.snapshot())
        if path.startswith("documents/") and path.endswith("/process"):
            doc_id=path.split("/")[1];self.processed.append(doc_id)
            task={"task_id":"task-"+str(len(self.tasks)+1),"document_id":doc_id,"status":"QUEUED",
                "created_at":str(len(self.tasks)+1).zfill(4),"events":[],"error_code":None,"issues":[]}
            self.tasks.append(task);return respond({"task":task},202)
        if path.startswith("tasks/"):
            task=next(t for t in self.tasks if t["task_id"]==path.split("/")[1])
            self.polls[task["task_id"]]=self.polls.get(task["task_id"],0)+1
            if self.polls[task["task_id"]]>=2:
                fail=(task["document_id"]=="doc-2" and self.processed.count("doc-2")==1) if self.kind=="temporary" else True
                if fail:
                    if not task["error_code"]:
                        code={"temporary":"rate_limit_exceeded","quota":"insufficient_quota"}.get(self.kind)
                        meta=read_http_error(httpx.Response(429,json={"error":{"code":code,"type":"insufficient_quota" if self.kind=="quota" else "rate_limit_error"}},
                            headers={"Retry-After":"6"} if self.kind=="temporary" else {}),4096)
                        if self.kind=="legacy":meta={}
                        meta.update(purpose="quotation_extraction",execution_kind="MECHANISM_TEST",http_status=429)
                        task.update(status="FAILED",error_code="AI_HTTP_429",
                            events=[{"state":"EXTRACTING","metadata":{"read_evidence":{"origin":"MOCK_SDK"}}},
                                    {"state":"FAILED","metadata":{"read_completed":True,"failed_ai_attempt":meta}}])
                else:
                    task["status"]="AVAILABLE"
                    if task["document_id"]=="doc-1" and not self.items:self.items=[item(self.docs[0])]
            return respond(task)
        raise AssertionError("Unexpected HTTP operation "+req.method+" "+path)

def run(evidence):
    evidence.mkdir(parents=True,exist_ok=False)
    with socket.socket() as s:s.bind(("127.0.0.1",0));port=s.getsockname()[1]
    class Handler(SimpleHTTPRequestHandler):
        def __init__(self,*args,**kwargs):
            super().__init__(*args,directory=str(ROOT/"backend/sanzo_demo/static"),**kwargs)
        def log_message(self,*args):pass
        def do_POST(self):
            assert self.path=="/api/cases/offline-case/documents"
            raw_body=self.rfile.read(int(self.headers["Content-Length"]))
            msg=BytesParser(policy=default).parsebytes(("Content-Type: "+self.headers["Content-Type"]+"\r\n\r\n").encode()+raw_body)
            part=next(p for p in msg.iter_parts() if p.get_filename())
            raw=part.get_payload(decode=True)
            assert raw, "Actual multipart upload must contain original bytes"
            digest=hashlib.sha256(raw).hexdigest()
            assert digest in HASHES
            fake=self.server.fake
            doc={"document_id":"doc-"+str(len(fake.docs)+1),"original_filename":part.get_filename(),"sha256":digest,"state":"RECEIVED"}
            fake.docs.append(doc)
            body=json.dumps(doc).encode()
            self.send_response(201);self.send_header("Content-Type","application/json")
            self.send_header("Content-Length",str(len(body)));self.end_headers();self.wfile.write(body)
    server=ThreadingHTTPServer(("127.0.0.1",port),Handler)
    thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
    base="http://127.0.0.1:"+str(port)
    result={"scope":"OFFLINE_BROWSER_HTTP_STANDINS","real_ai_calls":0,"checks":[],"scenarios":{}}
    def check(name,value=True):
        if not value:raise AssertionError(name)
        result["checks"].append(name);print("PASS: "+name,flush=True)
    try:
        time.sleep(.3)
        with sync_playwright() as pw:
            browser=pw.chromium.launch(executable_path="/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",headless=True)
            for kind in ["temporary","quota","unknown","legacy","budget","unsupported"]:
                ctx=browser.new_context(viewport={"width":1450,"height":1050})
                fake=FakeHTTP(kind);server.fake=fake;errors=[]
                def route(r):
                    if not r.request.url.startswith(base+"/"):return r.abort()
                    if r.request.method=="POST" and r.request.url.endswith("/api/cases/offline-case/documents"):
                        fake.calls.append({"method":"POST","path":"cases/offline-case/documents"})
                        return r.continue_()
                    if "/api/" in r.request.url:return fake.handle(r)
                    if "/static/" in r.request.url:
                        p=ROOT/"backend/sanzo_demo/static"/r.request.url.rsplit("/",1)[-1]
                        return r.fulfill(path=str(p))
                    return r.continue_()
                ctx.route("**/*",route)
                page=ctx.new_page();page.on("pageerror",lambda e:errors.append(str(e)))
                page.goto(base+"/index.html",wait_until="networkidle")
                page.locator("#case-select").select_option("offline-case")
                page.locator("#files").set_input_files([str(SAMPLES/NAMES[0]),str(SAMPLES/NAMES[1])])
                expect(page.locator("#pending-files li")).to_have_count(2)
                expect(page.locator("#pending-count")).to_have_text("2 件の待ちリスト")
                check(kind+": native multiple selection lists both names",all(n in page.locator("#pending-files").inner_text() for n in NAMES[:2]))
                if kind=="temporary":
                    page.locator("#files").set_input_files(str(SAMPLES/NAMES[2]))
                    expect(page.locator("#pending-files li")).to_have_count(3)
                    check("Second selection appends")
                    page.locator("#pending-files li").nth(2).get_by_role("button",name="削除").click()
                    expect(page.locator("#pending-files li")).to_have_count(2)
                    check("Remove before upload leaves no HTTP mutation",not any(c["method"]=="POST" for c in fake.calls))
                    page.locator("#files").set_input_files([str(SAMPLES/NAMES[0]),str(SAMPLES/NAMES[2])])
                    expect(page.locator("#pending-files li")).to_have_count(3)
                    check("Content duplicate does not duplicate queue")
                if kind=="unsupported":
                    for remove in range(2):
                        page.locator("#pending-files li").first.get_by_role("button",name="削除").click()
                    page.locator("#files").set_input_files({"name":NAMES[0],"mimeType":"application/vnd.openxmlformats-officedocument.spreadsheetml.sheet","buffer":b"changed-original-offline-test"})
                    expect(page.locator("#pending-files li")).to_have_count(1)
                    expect(page.locator("#pending-files")).to_contain_text("指定の三つのサンプルのみ")
                    expect(page.locator("#upload-button")).to_be_disabled()
                    check("Same filename with changed bytes is not supported",not fake.processed and not fake.docs)
                elif kind=="budget":
                    expect(page.locator("#upload-button")).to_be_disabled()
                    check("Local 30/30 prevents process before network mutation",not fake.processed)
                    expect(page.locator("#ai-pause")).to_contain_text("Demo")
                else:
                    page.locator("#upload-button").click()
                    # Synchronous repeated submit attempts must be ignored while busy.
                    page.locator("#upload-form").evaluate("(e)=>{e.requestSubmit();e.requestSubmit();}")
                    expect(page.locator('.document-state[data-status="FAILED"]')).to_have_count(1,timeout=15000)
                    expect(page.locator("#files")).to_be_enabled(timeout=5000)
                    expected_docs=3 if kind=="temporary" else 2
                    check(kind+": one click uploads every pending original once",len(fake.docs)==expected_docs)
                    uploads=[i for i,c in enumerate(fake.calls) if c["method"]=="POST" and c["path"].endswith("/documents")]
                    starts=[i for i,c in enumerate(fake.calls) if c["method"]=="POST" and c["path"].endswith("/process")]
                    check(kind+": upload all before sequential processing",max(uploads)<min(starts))
                    check(kind+": 429 pauses following documents",fake.processed==(["doc-1","doc-2"] if kind=="temporary" else ["doc-1"]))
                    expect(page.locator(".document-state").filter(has_text="AI の明細抽出に失敗")).to_have_count(1)
                    expect(page.locator("#ai-pause")).to_contain_text("読み取りは完了")
                    check(kind+": reading success distinct from AI failure")
                    if kind=="temporary":
                        expect(page.locator("#upload-button")).to_be_disabled()
                        expect(page.locator("#ai-pause")).to_contain_text("秒待って")
                        expect(page.locator(".quote-card")).to_have_count(1)
                        check("Completed first result preserved after later 429",len(fake.items)==1)
                        page.screenshot(path=str(evidence/"browser_wait_active.png"),full_page=True)
                        before=list(fake.processed)
                        page.reload(wait_until="networkidle")
                        page.locator("#case-select").select_option("offline-case")
                        expect(page.locator(".document-card")).to_have_count(3)
                        expect(page.locator(".process-button").first).to_be_disabled()
                        check("Reload preserves uploaded originals, completed result and cooldown",len(fake.items)==1)
                        page.locator("#files").set_input_files([str(SAMPLES/n) for n in NAMES])
                        expect(page.locator("#pending-files li")).to_have_count(3)
                        page.wait_for_timeout(6500)
                        expect(page.locator("#upload-button")).to_be_enabled()
                        check("Cooldown expiry never auto-retries",fake.processed==before)
                        page.screenshot(path=str(evidence/"browser_temporary_paused.png"),full_page=True)
                        page.locator("#upload-button").click()
                        expect(page.locator('.document-state[data-status="AVAILABLE"]')).to_have_count(3,timeout=15000)
                        expect(page.locator("#files")).to_be_enabled()
                        (evidence/"resume_state.json").write_text(json.dumps({"processed":fake.processed,"documents":fake.docs,"tasks":fake.tasks,"requests":fake.calls,"pending":page.locator("#pending-files").inner_text()},ensure_ascii=False,indent=2))
                        check("Explicit resume skips completed first file and resumes remaining",fake.processed==["doc-1","doc-2","doc-2","doc-3"] and len(fake.docs)==3)
                        page.locator("#upload-button").click();expect(page.locator("#files")).to_be_enabled()
                        check("Repeated completed batch does not upload or process again",len(fake.docs)==3 and len(fake.processed)==4)
                    elif kind=="quota":
                        expect(page.locator("#upload-button")).to_be_disabled()
                        expect(page.locator("#ai-pause")).to_contain_text("残高・使用上限")
                        before=list(fake.processed);page.wait_for_timeout(1200)
                        check("Account quota never retries on timer",fake.processed==before)
                        page.screenshot(path=str(evidence/"browser_quota_blocked.png"),full_page=True)
                        page.on("dialog",lambda d:d.accept())
                        page.locator("#ack-ai-account").click()
                        expect(page.locator("#upload-button")).to_be_enabled()
                        check("Account acknowledgement alone does not call AI",fake.processed==before)
                    else:
                        expect(page.locator("#ai-pause")).to_contain_text("原因を特定できません")
                        check(kind+": unknown response not relabeled as rate or quota", "一時的なレート" not in page.locator("#ai-pause").inner_text() and "残高・使用上限" not in page.locator("#ai-pause").inner_text())
                check(kind+": no browser exceptions",not errors)
                page.screenshot(path=str(evidence/("browser_"+kind+".png")),full_page=True)
                result["scenarios"][kind]={"requests":fake.calls,"processed":fake.processed,"documents":fake.docs,"items":fake.items,"errors":errors}
                ctx.close()
            browser.close()
        result["status"]="PASS";result["exit_code"]=0
    except Exception:
        import traceback
        result.update(status="FAIL",exit_code=1,error=traceback.format_exc())
        raise
    finally:
        server.shutdown();server.server_close();thread.join(timeout=10)
        (evidence/"result.json").write_text(json.dumps(result,ensure_ascii=False,indent=2))
    return result["exit_code"]
if __name__=="__main__":
    p=argparse.ArgumentParser();p.add_argument("--evidence",type=Path,required=True)
    raise SystemExit(run(p.parse_args().evidence))
