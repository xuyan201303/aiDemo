#!/usr/bin/env python3
"""Real Chrome rehearsal with synthetic documents and explicitly MANUAL input.

This is business-flow validation, NOT real SDK reading / AI identification.
Creates two visible labeled cases in the dedicated local Demo database.
"""
from __future__ import annotations
import argparse
from datetime import datetime, timezone
from io import BytesIO
import json
from pathlib import Path
import subprocess
import sys
import traceback
from uuid import uuid4

from openpyxl import Workbook, load_workbook
from playwright.sync_api import sync_playwright, expect

PROJECT = Path(__file__).resolve().parents[1]
BASE = "http://127.0.0.1:8765"


def workbook(path, company, price):
    book = Workbook()
    sheet = book.active
    sheet.title = "Synthetic manual input"
    sheet.append(["SYNTHETIC MANUAL REHEARSAL - NOT SDK OR AI"])
    sheet.append(["Company", "Original label", "Current price", "Currency", "Transport",
                  "Equipment", "Billing unit", "Scope", "Extra terms"])
    sheet.append([company, "送货钱", price, "JPY", "SEA", "20FT", "CONTAINER", "DESTINATION", "NONE"])
    book.save(path)
    book.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--evidence-dir", type=Path, required=True)
    parser.add_argument("--chrome", type=Path, default=Path("/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"))
    parser.add_argument("--skip-restart", action="store_true")
    args = parser.parse_args()
    root = args.evidence_dir.resolve()
    root.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    evidence = root / ("browser_" + stamp)
    evidence.mkdir()
    inputs = evidence / "synthetic_inputs"
    inputs.mkdir()
    company_a, company_b = "A社（合成 " + stamp + "）", "B社（合成 " + stamp + "）"
    first_xlsx, second_xlsx, first_pdf = [inputs / name for name in ("SYNTHETIC_A_first.xlsx", "SYNTHETIC_A_followup.xlsx", "SYNTHETIC_B.pdf")]
    workbook(first_xlsx, company_a, 10)
    workbook(second_xlsx, company_a, 20)
    result = {"status": "FAIL", "input_mode": "SYNTHETIC_DOCUMENTS_WITH_MANUAL_TRANSCRIPTION",
              "real_sdk_reading": "NOT_RUN", "real_ai": "NOT_RUN", "real_sample_rounds": "NOT_RUN",
              "browser_url": BASE, "checks": [], "screenshots": [], "cases": []}
    errors, requests = [], []
    page = None
    def check(name, ok=True):
        if not ok:
            raise AssertionError(name)
        result["checks"].append(name)
        print("PASS: " + name, flush=True)

    try:
        with sync_playwright() as pw:
            browser = pw.chromium.launch(executable_path=str(args.chrome), headless=True)
            context = browser.new_context(viewport={"width": 1500, "height": 1120}, accept_downloads=True)
            # Only the local Demo receives requests. Fixture creation is offline.
            context.route("**/*", lambda route: route.continue_() if route.request.url.startswith(BASE + "/") else route.abort())
            page = context.new_page()
            page.on("pageerror", lambda error: errors.append(str(error)))
            page.on("console", lambda msg: errors.append("console: " + msg.text) if msg.type == "error" else None)
            page.on("request", lambda req: requests.append({"method": req.method, "url": req.url,
                                                           "body": req.post_data if req.url.endswith("/corrections") else None}))
            page.set_content("<!doctype html><meta charset=utf-8><h1>SYNTHETIC MANUAL REHEARSAL</h1>"
                             "<p>NOT SDK / AI output</p><p>Company: " + company_b + "</p>"
                             "<p>Original label: Delivery charge</p><p>Current price: 12 JPY</p>"
                             "<p>SEA / 20FT / CONTAINER / DESTINATION / NONE</p>")
            page.pdf(path=str(first_pdf), format="A4")
            page.goto(BASE)
            expect(page.locator("#connection-panel")).to_contain_text("未接続")
            check("UI truthfully shows SDK and AI unavailable")
            status = context.request.get(BASE + "/api/status").json()
            check("API readiness false", not status["sdk"]["ready"] and not status["ai"]["ready"])

            def screenshot(name):
                target = evidence / (name + ".png")
                page.screenshot(path=str(target), full_page=True)
                result["screenshots"].append(str(target))

            def create_case(name):
                page.locator("#new-case-button").click()
                page.locator("#case-name").fill(name)
                page.locator("#new-case-form button[type=submit]").click()
                expect(page.locator("#new-case-form")).to_be_hidden()
                case_id = page.locator("#case-select").input_value()
                check("case created: " + name, bool(case_id))
                result["cases"].append(case_id)
                return case_id

            def upload(paths, count):
                page.locator("#files").set_input_files([str(path) for path in paths])
                page.locator("#upload-button").click()
                expect(page.locator(".document-card")).to_have_count(count)
                expect(page.locator("#upload-button")).to_be_enabled()

            def preview(index, screenshot_name):
                page.locator(".document-card").nth(index).get_by_role("button", name="原文をプレビュー", exact=False).click()
                expect(page.locator("#preview-dialog")).to_be_visible()
                frame = page.locator("#preview-frame")
                url = frame.get_attribute("src")
                response = context.request.get(BASE + url)
                check("original preview HTTP 200: " + screenshot_name, response.status == 200)
                if "xlsx" in screenshot_name:
                    expect(page.frame_locator("#preview-frame").locator("body")).to_contain_text("送货钱")
                else:
                    img = page.frame_locator("#preview-frame").locator("img").first
                    expect(img).to_be_visible()
                    expect(img).to_have_js_property("complete", True)
                    check("PDF raster preview visibly loaded", img.evaluate("element => element.naturalWidth > 0 && element.naturalHeight > 0"))
                page.wait_for_timeout(700)
                screenshot(screenshot_name)
                page.locator("#preview-dialog .close-dialog").click()

            def manual(index, company, price, raw="送货钱"):
                before = page.locator(".quote-card").count()
                page.locator(".document-card").nth(index).get_by_role("button", name="＋ 人工入力", exact=True).click()
                values = {"raw_label": raw, "unit_price": str(price), "currency": "JPY",
                          "forwarder_id": company, "transport_mode": "SEA", "equipment": "20FT",
                          "billing_unit": "CONTAINER", "charge_scope": "DESTINATION", "extra_terms": "NONE"}
                for name, value in values.items():
                    page.locator('#manual-form [name="' + name + '"]').fill(value)
                page.locator("#source-acknowledgment").check()
                page.locator("#manual-submit").click()
                expect(page.locator("#manual-dialog")).to_be_hidden()
                expect(page.locator(".quote-card")).to_have_count(before + 1)

            def snapshot(case):
                return context.request.get(BASE + "/api/cases/" + case).json()

            first = create_case("合成・人工彩排 1 / " + stamp)
            upload([first_xlsx, first_pdf], 2)
            check("upload does not manufacture parsed rows", snapshot(first)["items"] == [])
            preview(0, "01_xlsx_original_preview")
            preview(1, "02_pdf_original_preview")
            manual(0, company_a, 10)
            manual(1, company_b, 12, "Delivery charge")
            check("both rows explicitly manual", all(row["input_kind"] == "MANUAL_CONFIRMED" for row in snapshot(first)["items"]))
            screenshot("03_round1_before_confirmation")
            first_row = next(row for row in snapshot(first)["items"] if row["observation"]["forwarder_id"] == company_a)
            page.locator('.quote-card[data-item-id="' + first_row["item_id"] + '"]').get_by_role("button", name="確認・修正", exact=True).click()
            options = page.locator("#edit-fee-select option").all_text_contents()
            if "配送费" in options:
                page.locator("#edit-fee-select").select_option(label="配送费")
            else:
                page.locator("#edit-fee-name").fill("配送费")
            screenshot("04_correction_bubble")
            page.locator("#save-correction").click()
            expect(page.locator("#edit-dialog")).to_be_hidden()
            saved = next(row for row in snapshot(first)["items"] if row["item_id"] == first_row["item_id"])
            check("original label to human fee saved", saved["current_fee_name"] == "配送费" and saved["observation"]["raw_label"] == "送货钱")
            check("first current amount stays 10", saved["current_price"] == "10")
            fee_id = saved["current_fee_id"]
            b_row = next(row for row in snapshot(first)["items"] if row["observation"]["forwarder_id"] == company_b)
            page.locator('.quote-card[data-item-id="' + b_row["item_id"] + '"]').get_by_role("button", name="確認・修正", exact=True).click()
            page.locator("#edit-fee-select").select_option(fee_id)
            page.locator("#save-correction").click()
            expect(page.locator("#edit-dialog")).to_be_hidden()
            expect(page.locator(".compare-table tbody tr")).to_have_count(1)
            screenshot("05_round1_comparison_saved")
            result["round1_snapshot"] = snapshot(first)

            if not args.skip_restart:
                argv = [sys.executable, str(PROJECT / "tools/start_demo.py"), "--evidence-dir", str(evidence / "restart")]
                restart = subprocess.run(argv, cwd=PROJECT, capture_output=True, text=True, timeout=180)
                (evidence / "restart_command.txt").write_text("COMMAND: " + json.dumps(argv) + "\nEXIT_CODE: "
                    + str(restart.returncode) + "\n" + restart.stdout + "\n" + restart.stderr)
                check("web process restarted with persistent PostgreSQL and uploads", restart.returncode == 0)
                page.goto(BASE)
                page.locator("#case-select").select_option(first)
                expect(page.locator(".quote-card")).to_have_count(2)
                check("saved rows survive a new web process", snapshot(first)["items"][0]["current_fee_name"] == "配送费")
                result["process_restart"] = "PASS"
            else:
                result["process_restart"] = "NOT_RUN"
            second = create_case("合成・人工彩排 2 / " + stamp)
            upload([second_xlsx], 1)
            manual(0, company_a, 20)
            current = snapshot(second)["items"][0]
            check("round2 reads persisted fee identity", current["current_fee_id"] == fee_id and current["current_fee_name"] == "配送费")
            check("round2 retains new price20", current["current_price"] == "20")
            check("round2 reports memory application", current["saved_status"] == "MEMORY_APPLIED")
            check("round2 source belongs to new file", current["source_ref"]["document_id"] != saved["source_ref"]["document_id"])
            check("old case stays price10", next(row for row in snapshot(first)["items"] if row["item_id"] == saved["item_id"])["current_price"] == "10")
            screenshot("06_round2_reuse_current_price20")
            with page.expect_download() as download_info:
                page.locator("#export-button").click()
            download = download_info.value
            export = evidence / "SYNTHETIC_round2_DRAFT.xlsx"
            download.save_as(str(export))
            book = load_workbook(export, read_only=True, data_only=False)
            values = [str(cell.value) for sheet in book for row in sheet for cell in row]
            book.close()
            check("download contains humanname and current20", "配送费" in values and "20" in values)
            check("download clearly marked draft", any("DRAFT" in value for value in values))
            result["round2_snapshot"] = snapshot(second)
            # Confirm browser state after reload, not just immediate response rendering.
            page.reload()
            page.locator("#case-select").select_option(second)
            expect(page.locator(".quote-status")).to_contain_text("確認記録を適用")
            expect(page.locator(".quote-price")).to_contain_text("20")
            check("saved result renders after page reload")
            # Browser retry/concurrency cases use the second synthetic row and
            # restore its displayed price to20. No fee learning is changed.
            page.locator(".quote-card").get_by_role("button", name="確認・修正", exact=True).click()
            page.locator("#amount-only").check()
            page.locator("#edit-price").fill("21")
            intercepted = []
            def lose_first_response(route):
                intercepted.append(route.request.post_data_json)
                if len(intercepted) == 1:
                    response = route.fetch()
                    check("lost-response request really committed", response.status == 200)
                    route.abort("failed")
                else:
                    route.continue_()
            page.route("**/corrections", lose_first_response)
            page.locator("#save-correction").click()
            expect(page.locator("#edit-error")).to_be_visible()
            page.locator("#save-correction").click()
            expect(page.locator("#edit-dialog")).to_be_hidden()
            page.unroute("**/corrections", lose_first_response)
            check("unchanged retry uses identical idempotency key", len(intercepted) == 2 and intercepted[0] == intercepted[1])
            replayed = snapshot(second)["items"][0]
            check("lost-response retry creates only one item version", replayed["version"] == current["version"] + 1)
            check("amount-only retry leaves learning unchanged", replayed["current_memory_version"] == current["current_memory_version"])
            page.locator(".quote-card").get_by_role("button", name="確認・修正", exact=True).click()
            page.locator("#amount-only").check()
            page.locator("#edit-price").fill("22")
            side = context.request.post(BASE + "/api/items/" + replayed["item_id"] + "/corrections", data={
                "expected_case_revision": replayed["case_revision"],
                "expected_item_version": replayed["version"],
                "expected_memory_version": replayed["current_memory_version"],
                "idempotency_key": str(uuid4()), "unit_price": "23"})
            check("concurrent browser-tab equivalent saves", side.status == 200)
            page.locator("#save-correction").click()
            expect(page.locator("#edit-error")).to_be_visible()
            expect(page.locator("#edit-price")).to_have_value("22")
            page.locator("#refresh-item").click()
            expect(page.locator("#edit-price")).to_have_value("22")
            expect(page.locator("#amount-only")).to_be_checked()
            page.locator("#save-correction").click()
            expect(page.locator("#edit-dialog")).to_be_hidden()
            changed = snapshot(second)["items"][0]
            check("explicit conflict refresh preserves draft and saves new version", changed["current_price"] == "22")
            restore = context.request.post(BASE + "/api/items/" + changed["item_id"] + "/corrections", data={
                "expected_case_revision": changed["case_revision"], "expected_item_version": changed["version"],
                "expected_memory_version": changed["current_memory_version"],
                "idempotency_key": str(uuid4()), "unit_price": "20"})
            check("edge-test amount restored to original round2 price20", restore.status == 200)
            page.reload()
            page.locator("#case-select").select_option(second)
            expect(page.locator(".quote-price")).to_contain_text("20")
            result["round2_after_edge_tests"] = snapshot(second)
            result["expected_network_failures"] = ["One response deliberately lost after commit", "One stale correction HTTP409"]
            unknown_case = create_case("合成境界テスト・条件未確認 / " + stamp)
            upload([first_xlsx], 1)
            page.locator(".document-card").get_by_role("button", name="＋ 人工入力", exact=True).click()
            for name, value in {"raw_label": "送货钱", "unit_price": "10", "currency": "JPY"}.items():
                page.locator('#manual-form [name="' + name + '"]').fill(value)
            page.locator("#source-acknowledgment").check()
            page.locator("#manual-submit").click()
            expect(page.locator("#manual-dialog")).to_be_hidden()
            expect(page.locator(".quote-card")).to_have_count(1)
            unknown_row = snapshot(unknown_case)["items"][0]
            check("blank company and conditions stay null", unknown_row["observation"]["forwarder_id"] is None
                  and all(unknown_row["observation"]["context"][key] is None for key in ["transport_mode", "equipment", "billing_unit", "charge_scope", "extra_terms"]))
            page.locator(".quote-card").get_by_role("button", name="確認・修正", exact=True).click()
            page.locator("#edit-fee-select").select_option(fee_id)
            page.locator("#save-correction").click()
            expect(page.locator("#edit-dialog")).to_be_hidden()
            expect(page.locator("#notice")).to_contain_text("確認記録は作成していません")
            unknown_saved = snapshot(unknown_case)["items"][0]
            check("ineligible local confirmation does not create reusable memory", unknown_saved["current_memory_version"] == 0 and unknown_saved["current_fee_name"] == "配送费")
            result["unknown_condition_case"] = unknown_case
            page.locator("#case-select").select_option(second)
            expect(page.locator(".quote-price")).to_contain_text("20")
            result["browser_version"] = browser.version
            result["status"] = "PASS"
            context.close()
            browser.close()
    except Exception as exc:
        result["error"] = str(exc)
        result["traceback"] = traceback.format_exc()
        print(result["traceback"], file=sys.stderr)
        if page:
            try:
                page.screenshot(path=str(evidence / "failure.png"), full_page=True)
            except Exception:
                pass
    finally:
        result["browser_errors"] = errors
        (evidence / "browser_requests.json").write_text(json.dumps(requests, ensure_ascii=False, indent=2))
        (evidence / "rehearsal_result.json").write_text(json.dumps(result, ensure_ascii=False, indent=2))
        print(json.dumps({"status": result["status"], "checks": len(result["checks"]),
                          "result": str(evidence / "rehearsal_result.json")}, ensure_ascii=False))
    return 0 if result["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
