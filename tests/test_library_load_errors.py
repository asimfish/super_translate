"""Execute the real library error path in a browser, without live user data."""

from pathlib import Path

import pytest
from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize("kind,expected", [
    ("remote", "尚未配置远程访问"),
    ("network", "无法连接服务"),
    ("server", "数据库暂不可用"),
])
def test_library_error_explains_cause_and_retry_recovers(kind, expected):
    with sync_playwright() as pw:
        browser = pw.chromium.launch(
            executable_path="/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
            headless=True,
        )
        try:
            page = browser.new_page()
            page.route("http://library.test/", lambda route: route.fulfill(
                body='<input id="search-input"><input id="status-filter">'
                     '<div id="loading-skeleton" class="hidden"></div>'
                     '<div id="paper-list"></div>', content_type="text/html"))
            page.goto("http://library.test/")
            page.add_script_tag(path=str(ROOT / "app/static/js/app.js"))
            page.evaluate("""async kind => {
                api.listPapers = async () => {
                    if (kind === 'network') throw new TypeError('Failed to fetch');
                    throw new Error(kind === 'remote'
                        ? 'Remote API access requires PAPER_CHINA_API_TOKEN'
                        : '数据库暂不可用 <img src=x onerror="window.injected=true">');
                };
                await loadPapers();
            }""", kind)
            assert expected in page.locator("#paper-list").inner_text()
            assert page.locator("#paper-list img").count() == 0
            assert page.locator("#loading-skeleton").get_attribute("class") == "hidden"
            page.evaluate("""() => {
                api.listPapers = async () => ({papers: [], total: 0});
                renderPaperList = () => {
                    document.getElementById('paper-list').textContent = '恢复成功';
                };
                renderPagination = () => {};
                updateStats = () => {};
            }""")
            page.get_by_role("button", name="重试", exact=True).click()
            assert page.locator("#paper-list").inner_text() == "恢复成功"
        finally:
            browser.close()
