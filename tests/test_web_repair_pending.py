"""The real web UI must stop active-translation polling on a parked failure."""

from pathlib import Path

from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[1]


def test_repair_pending_is_visible_and_stops_active_polling(tmp_path):
    paper = {
        "id": "abcdef123456", "title": "Parked recovery specimen", "page_count": 1,
        "file_size": 1024, "tags": "", "created_at": "2026-10-12T00:00:00",
        "translation_status": "repairing", "translation_progress": 0.99,
        "translation_stage": "等待系统修复", "translation_error": "Renderer unavailable",
        "translation_log": "等待系统修复: Renderer unavailable", "has_translated": True,
        "has_dual": False, "has_qa_report": False,
    }
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(
            executable_path="/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
            headless=True,
        )
        try:
            page = browser.new_page(viewport={"width": 1280, "height": 900})

            def route(request):
                path = request.request.url.split("workspace.test", 1)[-1].split("?", 1)[0]
                if path == "/api/stats":
                    request.fulfill(json={"total_papers": 1, "completed_translations": 0})
                elif path.startswith("/api/provider-credentials"):
                    request.fulfill(json=[])
                elif path == "/api/papers/abcdef123456":
                    request.fulfill(json=paper)
                elif path.startswith("/api/"):
                    request.fulfill(json={"papers": [paper], "total": 1, "offset": 0, "limit": 50})
                elif path.startswith("/static/"):
                    request.fulfill(path=str(ROOT / "app" / path.lstrip("/")))
                elif path == "/":
                    request.fulfill(path=str(ROOT / "app/static/index.html"))
                else:
                    request.abort()

            page.route("**/*", route)
            page.goto("http://workspace.test/")
            card = page.locator("#paper-list .paper-card").first
            card.wait_for()
            assert "未通过" in card.inner_text()
            assert "Renderer unavailable" in card.inner_text()
            assert card.locator('[data-action="quick-translate"]').count() == 0
            page.evaluate("pollTranslationStatus('abcdef123456')")
            page.wait_for_function("translationPollId === null", timeout=8000)
            assert "修复已暂停" in page.locator("#trans-status").inner_text()
            assert "引擎更新后自动重试" in page.locator("#trans-status").inner_text()
            assert "完成" not in page.locator("#trans-status").inner_text()
            page.screenshot(path=str(tmp_path / "repair-pending-desktop.png"), full_page=True)
        finally:
            browser.close()
