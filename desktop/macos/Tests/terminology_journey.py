"""Browser acceptance against a real isolated terminology API and SQLite DB."""

from __future__ import annotations

import argparse
import json
import time
from pathlib import Path

import fitz
from playwright.sync_api import expect, sync_playwright


def run() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base", default="http://127.0.0.1:19543")
    parser.add_argument("--artifacts", type=Path, required=True)
    options = parser.parse_args()
    options.artifacts.mkdir(parents=True, exist_ok=True)
    label = str(time.time_ns())[-8:]
    document = fitz.open()
    page = document.new_page()
    page.insert_textbox(
        fitz.Rect(55, 50, 540, 780),
        "Terminology acceptance paper\n\n"
        "A cell is studied under controlled experimental conditions. "
        "The measurements describe a repeatable comparison of material properties. "
        "We report the activation energy and reaction rate in a chemical experiment. "
        "A second experiment studies gene expression in living cells. "
        "Each discipline uses a specific terminology library to avoid ambiguous translations.",
        fontsize=12,
    )
    pdf = document.tobytes()
    document.close()
    checks = []
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(channel="chrome", headless=True)
        context = browser.new_context(
            viewport={"width": 1440, "height": 1100}, accept_downloads=True
        )
        result = context.request.post(
            options.base + "/api/papers/upload",
            multipart={
                "file": {
                    "name": f"terminology-{label}.pdf",
                    "mimeType": "application/pdf",
                    "buffer": pdf,
                },
            },
        )
        assert result.status == 200, result.text()
        paper_id = result.json()["id"]
        page = context.new_page()
        errors = []
        page.on("pageerror", lambda error: errors.append(str(error)))
        page.on("dialog", lambda dialog: dialog.accept())
        page.goto(options.base)
        page.locator("#btn-terminology").click()
        expect(page.locator("#terminology-view")).to_have_class("view active")
        expect(page.locator("#term-entry-count")).to_contain_text("964")
        page.locator('[data-template="chemistry"]').click()
        expect(page.locator("#term-library-title")).to_have_text("化学入门术语")
        page.locator('[data-template="biology"]').click()
        expect(page.locator("#term-library-title")).to_have_text("生物学入门术语")
        checks.append("chemistry and biology templates create editable category libraries")
        page.get_by_text("管理学科 / 领域", exact=True).click()
        page.locator("#term-category-name").fill(f"材料科学 {label}")
        page.locator('[data-term-action="create-category"]').click()
        expect(page.locator("#term-status")).to_have_text("分类已创建。")
        root = (
            page.locator("#term-category-parent option")
            .filter(has_text=f"材料科学 {label}")
            .get_attribute("value")
        )
        page.locator("#term-category-parent").select_option(root)
        page.locator("#term-category-name").fill("电化学")
        page.locator('[data-term-action="create-category"]').click()
        expect(page.locator("#term-status")).to_have_text("分类已创建。")
        domain_option = page.locator("#term-category-filter option").filter(
            has_text=f"材料科学 {label} / 电化学"
        )
        expect(domain_option).to_have_count(1)
        domain = domain_option.get_attribute("value")
        assert page.evaluate("""() => {
            const event = new MouseEvent('click', { bubbles: true, cancelable: true });
            return document.getElementById('term-category-filter').dispatchEvent(event);
        }"""), "The category picker must retain its native mouse click behavior"
        page.locator("#term-category-filter").select_option(domain)
        expect(page.locator("#term-library-list .term-library")).to_have_count(0)
        page.locator("#term-new-library-name").fill(f"材料实验术语 {label}")
        page.locator('[data-term-action="create-library"]').click()
        expect(page.locator("#term-library-title")).to_have_text(f"材料实验术语 {label}")
        page.locator("#term-source").fill("cell")
        page.locator("#term-translation").fill("电池模块")
        page.locator("#term-aliases").fill("CE | cell unit")
        page.locator("#term-notes").fill("材料论文中的电池语境")
        page.locator("#term-reference").fill("验收示例")
        page.locator("#term-save-entry").click()
        expect(page.locator("#term-entry-body")).to_contain_text("电池模块")
        page.locator("#term-query").fill("CE")
        expect(page.locator("#term-entry-count")).to_contain_text("1 个词条")
        checks.append("new discipline/domain, reusable library, aliases and search")
        page.get_by_text("批量导入 CSV / JSON", exact=True).click()
        page.locator("#term-import-content").fill(
            "source,translation\nactivation energy,活化能\nreaction rate,反应速率"
        )
        page.locator('[data-term-action="import-entries"]').click()
        expect(page.locator("#term-status")).to_contain_text("新增 2")
        expect(page.locator("#term-entry-count")).to_contain_text("3 个词条")
        with page.expect_download() as download_info:
            page.locator('[data-term-action="export"]').click()
        download = download_info.value
        exported_path = options.artifacts / "exported-library.json"
        download.save_as(exported_path)
        exported = json.loads(exported_path.read_text())
        assert len(exported["entries"]) == 3 and exported["schema_version"] == 1
        assert exported["entries"][1]["source"] == "cell"
        checks.append("CSV atomic import and versioned JSON export")
        page.locator("#term-profile-category").select_option(domain)
        expect(page.locator("#term-use-builtin")).not_to_be_checked()
        page.locator('[data-term-action="save-profile"]').click()
        expect(page.locator("#term-preview")).to_contain_text("5 个有效术语")
        page.locator("#term-profile-paper").select_option(paper_id)
        expect(page.locator("#term-profile-inherited")).to_contain_text("继承默认设置")
        page.locator("#term-new-library-name").fill(f"本篇材料译法 {label}")
        page.locator('[data-term-action="create-paper-library"]').click()
        expect(page.locator("#term-library-title")).to_have_text(f"本篇材料译法 {label}")
        page.locator("#term-source").fill("cell")
        page.locator("#term-translation").fill("电化学单元")
        page.locator("#term-save-entry").click()
        expect(page.locator("#term-preview")).to_contain_text("电池模块")
        expect(page.locator("#term-preview")).to_contain_text("电化学单元")
        checks.append("workspace default inherited by new paper and paper override conflict shown")
        page.locator("#term-query").fill("cell")
        expect(page.locator("#term-entry-body tr")).to_have_count(1)
        page.locator('[data-term-action="edit-entry"]').click()
        page.locator("#term-notes").fill('<img src=x onerror="window.termInjected=true">')
        page.locator("#term-save-entry").click()
        expect(page.locator("#term-entry-body")).to_contain_text("<img src=x")
        assert page.evaluate("window.termInjected === undefined")
        assert page.locator("#term-entry-body img").count() == 0
        checks.append("stored markup rendered as text")
        page.screenshot(path=str(options.artifacts / "desktop-1440.png"), full_page=True)
        page.set_viewport_size({"width": 390, "height": 844})
        page.locator("#term-profile-title").scroll_into_view_if_needed()
        assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth + 1")
        page.screenshot(path=str(options.artifacts / "mobile-390.png"), full_page=True)
        checks.append("mobile layout has no page overflow")
        page.set_viewport_size({"width": 1440, "height": 1100})
        page.evaluate("document.getElementById('btn-logout').classList.remove('hidden')")
        pending = []
        page.route("**/api/terminology/libraries/*/entries?*", lambda route: pending.append(route))
        with page.expect_request("**/api/terminology/libraries/*/entries?*"):
            page.locator("#term-query").fill("late response")
        page.locator("#btn-logout").click()
        assert len(pending) == 1
        pending[0].fulfill(
            json={
                "total": 1,
                "entries": [
                    {
                        "id": "expired-row",
                        "source": "old account term",
                        "translation": "旧账号内容",
                        "aliases": [],
                        "notes": "",
                        "reference": "",
                    }
                ],
            }
        )
        expect(page.locator("#term-status")).to_contain_text("账号或页面已切换")
        expect(page.locator("#term-entry-body")).to_be_empty()
        expect(page.locator("#term-preview")).to_be_empty()
        expect(page.locator("#term-source")).to_have_value("")
        checks.append(
            "logout clears glossary rows, previews and drafts; late responses are discarded"
        )
        assert not errors, errors
        (options.artifacts / "report.json").write_text(
            json.dumps(
                {"checks": checks, "count": len(checks), "page_errors": errors},
                ensure_ascii=False,
                indent=2,
            )
        )
        print(json.dumps({"checks": len(checks), "page_errors": len(errors)}))
        browser.close()


if __name__ == "__main__":
    run()
