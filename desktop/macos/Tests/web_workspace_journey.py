"""Exercise the shipped web UI with bounded in-browser paper fixtures."""

from __future__ import annotations

import argparse
import mimetypes
from pathlib import Path

from playwright.sync_api import sync_playwright


def run() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--artifacts", type=Path, required=True)
    parser.add_argument("--baseline", type=Path)
    parser.add_argument("--assets", type=Path)
    options = parser.parse_args()
    repo = Path(__file__).resolve().parents[3]
    artifacts = options.artifacts
    artifacts.mkdir(parents=True, exist_ok=True)
    entries = [
        ("Optimal Flow Transport and Its Entropic Regularization", "completed", 19),
        ("Attention Is All You Need", "translating", 15),
        ("Denoising Diffusion Probabilistic Models", "pending", 16),
        ("A Survey of Vision-Language-Action Models", "failed", 24),
        ("Learning Transferable Visual Models From Natural Language Supervision", "completed", 12),
        ("Deep Residual Learning for Image Recognition", "pending", 8),
    ]
    papers = [
        {
            "id": f"abcde000000{i}",
            "title": title,
            "page_count": pages,
            "file_size": 1600000,
            "created_at": "2026-10-10T00:00:00",
            "updated_at": "2026-10-10T00:00:00",
            "translation_status": status,
            "translation_progress": 0.42 if status == "translating" else 0,
            "translation_stage": "翻译正文",
            "translation_error": "连接中断，请重试" if status == "failed" else None,
            "has_translated": status == "completed",
            "has_dual": status == "completed",
            "has_qa_report": False,
            "tags": "机器学习, 论文精读",
        }
        for i, (title, status, pages) in enumerate(entries)
    ]
    errors: list[str] = []
    with sync_playwright() as pw:
        browser = pw.chromium.launch(
            executable_path="/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
            headless=True,
        )
        page = browser.new_page(viewport={"width": 1440, "height": 1000})
        page.on("pageerror", lambda error: errors.append(str(error)))

        def asset(route):
            path = route.request.url.split("workspace.test/")[1]
            file = repo / "app" / path if path else repo / "app/static/index.html"
            override = options.assets or options.baseline
            if override and file.name in {"index.html", "style.css", "app.js"}:
                file = override / file.name
            mime = mimetypes.guess_type(file.name)[0] or "application/octet-stream"
            route.fulfill(path=str(file), content_type=mime)

        page.route("http://workspace.test/**", asset)

        def api(route):
            if "/api/stats" in route.request.url:
                route.fulfill(json={"total_papers": 6, "completed_translations": 2})
            elif "/api/provider-credentials/models" in route.request.url:
                catalog = {
                    "provider": "deepseek",
                    "label": "DeepSeek",
                    "source": "fixture",
                    "default_model": "deepseek-v4-pro",
                    "selected_model": "deepseek-v4-pro",
                    "models": ["deepseek-v4-pro", "deepseek-v4-flash"],
                    "model_options": [],
                }
                route.fulfill(json=catalog if route.request.method == "POST" else [catalog])
            elif "/api/provider-credentials" in route.request.url:
                route.fulfill(json=[])
            else:
                route.fulfill(json={"papers": papers, "total": 6, "offset": 0, "limit": 50})

        page.route("http://workspace.test/api/**", api)
        page.goto("http://workspace.test/")
        page.locator("#paper-list .paper-card").first.wait_for()
        assert page.locator("#paper-list .paper-card").count() == 6
        page.screenshot(path=str(artifacts / "library-desktop.png"), full_page=True)
        if options.baseline:
            assert not errors, errors
            browser.close()
            print("Baseline screenshot saved")
            return
        for width in [768, 390]:
            page.set_viewport_size({"width": width, "height": 900})
            assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth"), width
            page.screenshot(path=str(artifacts / f"library-{width}.png"), full_page=True)
        page.set_viewport_size({"width": 1440, "height": 1000})
        page.evaluate("papers[0].tags = ['数组标签']; renderPaperList()")
        assert page.locator(".tag-chip").first.inner_text() == "数组标签"
        if options.assets:
            page.evaluate("papers[0].translation_status = 'repairing'; renderPaperList()")
            first = page.locator("#paper-list .paper-card").first
            assert "已保留最佳译文" in first.inner_text()
            assert first.locator('[data-action="quick-translate"]').count() == 0
            page.evaluate("papers[0].translation_status = 'completed'; renderPaperList()")
        page.evaluate("papers[0].title = '<img src=x onerror=alert(1)>'; renderPaperList()")
        assert page.locator("#paper-list img").count() == 0
        page.evaluate(
            "window.keyboardPaper = null; openReader = async id => { window.keyboardPaper = id; }"
        )
        page.locator("#paper-list .paper-card").first.focus()
        page.keyboard.press("Enter")
        assert page.evaluate("window.keyboardPaper") == papers[0]["id"]
        page.locator('.workspace-nav [data-action="show-upload"]').click()
        assert page.locator("#upload-view").is_visible()
        assert page.locator("#drop-zone").is_visible()
        page.screenshot(path=str(artifacts / "upload-desktop.png"), full_page=True)
        page.evaluate("showView('reader')")
        assert not page.locator("#ocr-mode").is_visible()
        page.locator(".translation-options summary").click()
        assert page.locator("#ocr-mode").is_visible()
        assert page.locator("#preserve-graphics-text").is_checked()
        page.locator(".translation-options summary").click()
        page.locator("#reader-title").evaluate(
            "el => el.textContent = 'Optimal Flow Transport and Its Entropic Regularization'"
        )
        page.screenshot(path=str(artifacts / "reader-desktop.png"), full_page=True)
        page.locator("#btn-provider-settings").click()
        assert page.locator("#provider-settings-modal").is_visible()
        if options.assets:
            row = page.locator('.provider-row[data-provider="deepseek"]')
            row.locator('.provider-model option[value="deepseek-v4-flash"]').wait_for(
                state="attached"
            )
            row.locator('[data-action="refresh-provider-models"]').click()
            page.wait_for_function("!document.querySelector('.provider-model-refresh').disabled")
        assert page.locator("#provider-settings-error").inner_text() == ""
        page.screenshot(path=str(artifacts / "settings-desktop.png"), full_page=True)
        page.set_viewport_size({"width": 390, "height": 900})
        assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
        page.screenshot(path=str(artifacts / "settings-mobile.png"), full_page=True)
        assert not errors, errors
        browser.close()
    print("Web journey passed: layouts, navigation, options, keyboard, tags and escaped titles")


if __name__ == "__main__":
    run()
