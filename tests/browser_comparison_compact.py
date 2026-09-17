"""Display-only browser checks. No backend writes or real AI."""
import argparse, copy, json
from pathlib import Path
from sanzo_demo.comparison import comparison_projection, comparison_status
from playwright.sync_api import sync_playwright, expect
ROOT=Path(__file__).resolve().parents[1]
def run(out):
    out.mkdir(parents=True,exist_ok=False)
    ctx={"ready":True,"equipment":"20FT","transport_mode":"海上","billing_unit":"コンテナ","charge_scope":"港間","extra_terms":"共通"}
    def quote(i,company,equipment="20FT"):
        return {"item_id":i,"current_price":"1200" if company=="A社" else "1150","currency":"USD","version":1,"current_fee_id":None,"ai_suggested_name":"海上運賃","input_kind":"MOCK_SDK","initial_classification":{"kind":"AI_REQUIRED"},"observation":{"raw_label":"原文だけの名称","forwarder_id":company,"document_id":"doc","source_ready":True,"context":dict(ctx,equipment=equipment)},"source_ref":{"kind":"EXCEL_RANGE","locator_status":"VERIFIED","locator":{"sheet_name":"見積","A1_range":"B7"},"document_id":"doc"}}
    items=[quote("a","A社"),quote("b","B社"),quote("c","A社","40FT")]
    items[1]["observation"]["context"]["ready"]=False
    items[1]["observation"]["context"]["transport_mode"]=None
    snapshot={"case":{"case_id":"case","name":"表示検証（オフライン）","case_revision":1},"items":items,"documents":[{"document_id":"doc","original_filename":"offline.xlsx","state":"AVAILABLE"}],"tasks":[],"fees":[]}
    snapshot["comparison"]=comparison_projection(items)
    before=copy.deepcopy(snapshot); calls=[]; errors=[]
    with sync_playwright() as pw:
        browser=pw.chromium.launch(executable_path="/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",headless=True)
        page=browser.new_page(viewport={"width":1440,"height":1100})
        page.on("pageerror",lambda e:errors.append(str(e)))
        def route(r):
            path=r.request.url.split("127.0.0.1:8765")[-1]
            calls.append({"method":r.request.method,"path":path})
            assert r.request.method=="GET"
            if path in ["/","/index.html"]:return r.fulfill(path=str(ROOT/"backend/sanzo_demo/static/index.html"))
            if path.startswith("/static/"):return r.fulfill(path=str(ROOT/"backend/sanzo_demo/static"/path.rsplit("/",1)[-1]))
            if path=="/api/status":data={"reader_mode":"MOCK_SDK","reader":{"ready":True},"sdk":{"ready":False},"ai":{"ready":False},"missing_conditions":[]}
            elif path=="/api/cases":data=[snapshot["case"]]
            elif path=="/api/cases/case":data=snapshot
            elif "/preview" in path:return r.fulfill(content_type="text/html",body="<p>Offline preview boundary only</p>")
            else:raise AssertionError(path)
            r.fulfill(content_type="application/json",body=json.dumps(data))
        page.route("**/*",route)
        page.goto("http://127.0.0.1:8765",wait_until="networkidle")
        page.locator("#case-select").select_option("case")
        expect(page.locator(".compare-table tbody tr")).to_have_count(2)
        expect(page.locator(".compare-table tbody tr").first).to_contain_text("1,200")
        expect(page.locator(".compare-table tbody tr").first).to_contain_text("1,150")
        expect(page.locator(".compare-table tbody tr").first).to_contain_text("● AI仮判定")
        expect(page.locator(".compare-table tbody tr").first).to_contain_text("AI仮判定")
        text=page.locator("#comparison-table").inner_text()
        for forbidden in ["原文だけの名称","offline.xlsx","B7","transport_mode","共通","SDK","AI 提案："]:
            assert forbidden not in text,forbidden
        assert "20FT" in text and "40FT" in text
        page.locator("#comparison-table").screenshot(path=str(out/"browser_comparison.png"))
        page.locator(".row-confirm-button").first.click()
        expect(page.locator("#edit-dialog")).to_be_visible()
        expect(page.locator("#edit-unified-name")).to_have_text("海上運賃（20FT）")
        expect(page.locator(".row-member")).to_have_count(2)
        for card in page.locator(".row-member").all():
            expect(card).to_contain_text("原文だけの名称")
            expect(card).to_contain_text("見積!B7")
        assert page.locator("#edit-price,#edit-currency,#amount-only").count() == 0
        for cell in page.locator(".price-cell").all():
            assert cell.locator("button,input,.quote-status,.row-status").count() == 0
            assert not any(x in cell.inner_text() for x in ["AI仮判定","確認済み","確認・修正"])
        expect(page.locator(".row-status")).to_have_count(2)
        page.locator("#edit-dialog").screenshot(path=str(out/"browser_details.png"))
        page.locator(".member-preview").first.click()
        expect(page.locator("#preview-dialog")).to_be_visible()
        assert page.locator("#preview-frame").get_attribute("src")=="/api/documents/doc/preview?sheet=%E8%A6%8B%E7%A9%8D&cell=B7"
        page.locator("#preview-dialog .close-dialog").click()
        sent=[]
        def reject_correction(r):
            sent.append(r.request.post_data_json)
            r.fulfill(status=409,content_type="application/json",body=json.dumps({"detail":"OFFLINE_NO_WRITE"}))
        page.route("**/api/cases/*/comparison/*/confirmation",reject_correction)
        page.locator(".member-price").first.evaluate("(e)=>{e.textContent='99999 JPY';}")
        page.locator("#edit-fee-name").fill("確認名称")
        page.locator("#save-correction").click()
        expect(page.locator("#edit-error")).to_contain_text("更新があります")
        assert len(sent)==1 and sent[0]["new_fee_name"]=="確認名称"
        assert "unit_price" not in sent[0] and "currency" not in sent[0]
        assert len(sent[0]["members"]) == 2 and all("unit_price" not in m for m in sent[0]["members"])
        (out/"name_only_request.json").write_text(json.dumps(sent[0],ensure_ascii=False,indent=2))
        # Pure display routines must not mutate the input or promote fee IDs.
        assert page.evaluate("state.snapshot.items.every(i=>i.current_fee_id===null)")
        assert snapshot==before
        result=[comparison_status(dict(items[0],current_fee_id="f",saved_status=status)) for status in ["HUMAN_CONFIRMED","MEMORY_APPLIED"]]
        result.append(comparison_status(dict(items[0],current_fee_id="f",current_memory_status="REVOKED")))
        assert result==["✓ 確認済み","✓ 確認済み","⚠ 確認必要"]
        assert not errors,errors
        browser.close()
    (out/"result.json").write_text(json.dumps({"status":"PASS","exit_code":0,"scope":"OFFLINE_DISPLAY_ONLY","requests":calls,"mutations":0,"real_ai":0,"checks":["AI cross-forwarder grouping","20FT/40FT separation","unknown conditions warning","compact table exclusion","detail fields","preview locator","no fee promotion","confirmed/memory/revoked statuses","readonly price and currency","DOM price tampering never enters name-confirmation request"],"limitations":["Preview content is a stand-in; existing preview URL/position wiring checked.","Row save intercepted with HTTP409; no real DB writes requested."]},ensure_ascii=False,indent=2))
if __name__=="__main__":
    p=argparse.ArgumentParser();p.add_argument("--evidence",type=Path,required=True);run(p.parse_args().evidence)
