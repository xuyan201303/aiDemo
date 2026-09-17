"""Offline UI-only rename/reclassification intent checks with intercepted HTTP.

Synthetic source cards and HTTP responses prove the browser request boundary;
this is not SDK, AI, PostgreSQL, or real business-flow evidence.
"""
import argparse
import copy
import json
import traceback
from pathlib import Path

from playwright.sync_api import expect, sync_playwright
from browser_fee_panel import fixture

ROOT = Path(__file__).resolve().parents[1]
BASE = "http://127.0.0.1:8765"


def confirmed_fixture():
    snapshot = fixture()
    next(fee for fee in snapshot["fees"] if fee["fee_id"] == "f1")["name_version"] = 2
    for item in snapshot["items"]:
        item.update(current_fee_id="f1", current_fee_name="Delivery fee",
                    current_fee_name_version=2, effective_fee_name="Delivery fee",
                    effective_fee_name_version=2)
    row = snapshot["comparison"]["rows"][0]
    row.update(rename_allowed=True, rename_fee_id="f1", rename_name_version=2,
               confirmation_status="✓ 確認済み", label="Delivery fee",
               display_name="Delivery fee")
    for member in row["member_items"]:
        source = next(item for item in snapshot["items"] if item["item_id"] == member["item_id"])
        member.update({key: source[key] for key in ("current_fee_id", "current_fee_name", "current_fee_name_version", "effective_fee_name", "effective_fee_name_version")})
    return snapshot


def run(output):
    output.mkdir(parents=True, exist_ok=False)
    result = {"scope": "OFFLINE_BROWSER_HTTP_SUBSTITUTES", "ai_calls": 0,
              "database_writes": 0, "checks": []}
    requests = []
    errors = []

    def check(label, condition=True):
        assert condition, label
        result["checks"].append(label)
        print("PASS " + label, flush=True)

    try:
        snapshot = confirmed_fixture()
        response = {"abort": False}
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(
                executable_path="/Applications/Google Chrome.app/Contents/MacOS/Google Chrome", headless=True)
            page = browser.new_page(viewport={"width": 1366, "height": 768})
            page.on("pageerror", lambda error: errors.append(str(error)))

            def route(request_route):
                request = request_route.request
                path = request.url.removeprefix(BASE)
                if request.method == "POST":
                    assert path.endswith("/confirmation") and "/comparison/" in path
                    requests.append(request.post_data_json)
                    if response["abort"]:
                        return request_route.abort("connectionreset")
                    return request_route.fulfill(status=409, content_type="application/json",
                                                 body=json.dumps({"detail": "OFFLINE_VERSION_CONFLICT"}))
                assert request.method == "GET", request.method
                if path == "/":
                    return request_route.fulfill(path=str(ROOT / "backend/sanzo_demo/static/index.html"))
                if path.startswith("/static/"):
                    return request_route.fulfill(path=str(ROOT / "backend/sanzo_demo/static" / path.rsplit("/", 1)[-1]))
                if "/preview" in path:
                    return request_route.fulfill(content_type="text/html", body="<p>Explicit synthetic preview</p>")
                if path == "/api/status":
                    data = {"reader_mode": "REAL_SDK", "reader": {"ready": True}, "ai": {"ready": False}}
                elif path == "/api/cases":
                    data = [snapshot["case"]]
                elif path == "/api/cases/case":
                    data = snapshot
                else:
                    raise AssertionError(path)
                return request_route.fulfill(content_type="application/json", body=json.dumps(data))

            page.route("**/*", route)

            def open_panel():
                page.goto(BASE, wait_until="networkidle")
                page.locator("#case-select").select_option("case")
                page.locator(".row-confirm-button").first.click()
                expect(page.locator("#edit-dialog")).to_be_visible()

            def save():
                page.locator("#save-correction").click()
                expect(page.locator("#edit-error")).to_be_visible()

            open_panel()
            expect(page.locator("#edit-intent-help")).not_to_be_visible()
            check("default retains one visible name input and readonly price cards",
                  page.locator("#edit-dialog input:visible").count() == 1
                  and page.locator("#edit-dialog input[name=unit_price],#edit-dialog input[name=currency]").count() == 0)
            save()
            check("unchanged existing identity explicitly confirms members",
                  requests[-1]["intent"] == "CONFIRM_MEMBERS" and requests[-1]["target_fee_id"] == "f1"
                  and requests[-1]["expected_fee_id"] == "f1" and requests[-1]["expected_name_version"] == 2)
            page.locator("#edit-fee-name").fill("Renamed delivery")
            expect(page.locator("#edit-intent-help")).to_have_text("この費目の統一名称を変更します。学習済みの表記にも次回から反映されます。")
            before_preview = len(requests)
            page.locator(".member-preview").first.click()
            expect(page.locator("#preview-dialog")).to_be_visible()
            page.screenshot(path=str(output / "rename_preview.png"))
            page.locator("#preview-dialog .close-dialog").click()
            expect(page.locator("#edit-fee-name")).to_have_value("Renamed delivery")
            check("preview preserves typed rename and issues no write", len(requests) == before_preview)
            page.screenshot(path=str(output / "rename_intent.png"))
            save()
            request = requests[-1]
            check("typed changed name emits explicit rename with pinned fee and name version",
                  request["intent"] == "RENAME_FEE" and request["expected_fee_id"] == "f1"
                  and request["expected_name_version"] == 2 and request["new_fee_name"] == "Renamed delivery"
                  and "target_fee_id" not in request)
            check("rename request retains case item memory versions and no price facts",
                  request["expected_case_revision"] == 1
                  and len(request["members"]) == 2
                  and all(member["expected_item_version"] == 1 and member["expected_memory_version"] == 0 for member in request["members"])
                  and not {"price", "amount", "unit_price", "currency", "source_ref"}.intersection(request))
            page.locator("#edit-fee-name").fill("Café fee")
            count = len(requests)
            save()
            expect(page.locator("#edit-error")).to_contain_text("候補から選択")
            check("typing different existing name does not silently reclassify or merge", len(requests) == count)
            page.locator('[data-fee-id="accent"]').click()
            expect(page.locator("#edit-intent-help")).to_have_text("この明細の分類だけを変更します。")
            save()
            check("explicit different candidate emits reclassify",
                  requests[-1]["intent"] == "RECLASSIFY_MEMBERS" and requests[-1]["target_fee_id"] == "accent"
                  and requests[-1]["expected_fee_id"] == "accent" and requests[-1]["expected_name_version"] == 1)
            page.locator("#edit-fee-name").fill("New category")
            page.locator("#reclassify-members").click()
            expect(page.locator("#reclassify-members")).to_have_attribute("aria-pressed", "true")
            save()
            check("lightweight explicit new-category path is reclassification",
                  requests[-1]["intent"] == "RECLASSIFY_MEMBERS" and requests[-1]["new_fee_name"] == "New category")
            page.screenshot(path=str(output / "reclassify_intent.png"))
            page.locator("#reclassify-members").click()
            expect(page.locator("#edit-intent-help")).to_contain_text("統一名称を変更")
            response["abort"] = True
            save()
            lost = copy.deepcopy(requests[-1])
            expect(page.locator("#edit-fee-name")).to_be_disabled()
            expect(page.locator("#reclassify-members")).to_be_disabled()
            save()
            check("lost response retains exact rename intent payload versions and idempotency key", requests[-1] == lost)
            response["abort"] = False
            save()
            # A historical table keeps its snapshot, but save must visibly confirm
            # the current effective name instead of silently adopting it on submit.
            snapshot = confirmed_fixture()
            for item in snapshot["items"] + snapshot["comparison"]["rows"][0]["member_items"]:
                item["current_fee_name"] = "Historical delivery"
                item["current_fee_name_version"] = 1
            snapshot["comparison"]["rows"][0].update(label="Historical delivery", display_name="Historical delivery")
            count = len(requests)
            open_panel()
            expect(page.locator(".fee-name").first).to_have_text("Historical delivery")
            expect(page.locator("#edit-fee-name")).to_have_value("Delivery fee")
            expect(page.locator("#edit-intent-help")).not_to_be_visible()
            check("historical table unchanged while dialog visibly adopts latest effective name", len(requests) == count)
            save()
            check("historical confirmation submits visible current identity without hidden rename",
                  requests[-1]["intent"] == "CONFIRM_MEMBERS" and requests[-1]["target_fee_id"] == "f1"
                  and requests[-1]["expected_fee_id"] == "f1" and requests[-1]["expected_name_version"] == 2
                  and "new_fee_name" not in requests[-1])
            # Backend capability denial must never be guessed from current_fee_id alone.
            snapshot["comparison"]["rows"][0]["rename_allowed"] = False
            open_panel()
            expect(page.locator("#reclassify-members")).not_to_be_visible()
            page.locator("#edit-fee-name").fill("Conflict recovery name")
            expect(page.locator("#edit-intent-help")).not_to_be_visible()
            save()
            check("non-renameable conflict or mixed row preserves ordinary confirmation",
                  requests[-1]["intent"] == "CONFIRM_MEMBERS" and "expected_name_version" not in requests[-1])
            snapshot = fixture()
            open_panel()
            page.locator("#edit-fee-name").fill("First human name")
            save()
            check("first AI provisional confirmation never expands to rename", requests[-1]["intent"] == "CONFIRM_MEMBERS")
            check("no JS errors", not errors)
            result["status"] = "PASS"
            browser.close()
    except Exception:
        result["status"] = "FAIL"
        result["error"] = traceback.format_exc()
        print(result["error"], flush=True)
    finally:
        (output / "result.json").write_text(json.dumps(result, ensure_ascii=False, indent=2))
        (output / "requests.json").write_text(json.dumps(requests, ensure_ascii=False, indent=2))
    return 0 if result["status"] == "PASS" else 1


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--evidence", type=Path, required=True)
    raise SystemExit(run(parser.parse_args().evidence))
