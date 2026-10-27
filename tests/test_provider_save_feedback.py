"""Exercise actual credential controls: saving and refreshing have separate outcomes."""
from pathlib import Path

import pytest
from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[1]

@pytest.mark.parametrize("refresh", ["warning", "http_error", "ok", "save_error"])
def test_saving_key_only_reports_save_failures_as_error(refresh):
    with sync_playwright() as pw:
        browser = pw.chromium.launch(
            executable_path="/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
            headless=True,
        )
        try:
            page = browser.new_page()
            page.route("http://feedback.test/", lambda r: r.fulfill(
                path=str(ROOT / "app/static/index.html")))
            page.route("**/static/**", lambda r: r.fulfill(
                path=str(ROOT / "app" / r.request.url.split("feedback.test/", 1)[1]
                         .split("?", 1)[0])))
            page.route("**/api/**", lambda r: r.fulfill(json={"papers": [], "total": 0}))
            page.goto("http://feedback.test/")
            result = page.evaluate('''async refresh => {
                window.messages = [];
                toastSuccess = text => messages.push(['success', text]);
                toastError = text => messages.push(['error', text]);
                const row = document.querySelector('[data-provider="deepseek"]');
                ensureProviderModel(row.querySelector('.provider-model'), 'deepseek-v4-pro');
                row.querySelector('.provider-key').value = 'test-key';
                api.saveProviderCredential = async () => {
                    if (refresh === 'save_error') throw new Error('保存失败');
                    return {provider:'deepseek', label:'DeepSeek', model:'deepseek-v4-pro',
                            configured:true, source:'personal'};
                };
                api.refreshProviderModels = async () => {
                    if (refresh === 'http_error') throw new Error('HTTP 503');
                    return {provider:'deepseek', models:['deepseek-v4-pro'],
                            source:'curated',
                            warning:refresh === 'warning' ? '内置列表：实时更新失败 401' : ''};
                };
                const button = row.querySelector('[data-action="save-provider-credential"]');
                await saveProviderCredential({target:button});
                return {messages, hint:row.querySelector('.provider-model-meta').textContent,
                        key:row.querySelector('.provider-key').value,
                        model:row.querySelector('.provider-model').value};
            }''', refresh)
            if refresh == "save_error":
                assert result["messages"] == [["error", "保存失败"]]
                assert result["key"] == "test-key"
            else:
                assert result["messages"] == [["success", "DeepSeek API 配置已保存"]]
                assert result["key"] == ""
                assert result["model"] == "deepseek-v4-pro"
                if refresh != "ok":
                    assert "更新失败" in result["hint"]
        finally:
            browser.close()


def test_reader_submits_optional_cache_as_offline_multipart(tmp_path):
    import json
    import threading
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
    from urllib.parse import urlsplit

    cache = tmp_path / "specimen.jsonl"
    cache.write_text('{"source":"offline specimen"}\n')
    submitted = []
    class Receiver(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_POST(self):
            payload = self.rfile.read(int(self.headers["Content-Length"]))
            submitted.append((self.path, self.headers["Content-Type"], payload))
            response = json.dumps({"ok": True, "status": "translating"}).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(response)))
            self.end_headers()
            self.wfile.write(response)

    server = ThreadingHTTPServer(("127.0.0.1", 0), Receiver)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        with sync_playwright() as pw:
            browser = pw.chromium.launch(
                executable_path="/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
                headless=True,
            )
            try:
                page = browser.new_page()
                def route(request):
                    path = urlsplit(request.request.url).path
                    if path.startswith("/static/"):
                        request.fulfill(path=str(ROOT / "app" / path.lstrip("/")))
                    elif path.endswith("/translate"):
                        request.continue_()
                    elif path == "/":
                        request.fulfill(path=str(ROOT / "app/static/index.html"))
                    else:
                        request.fulfill(json={"papers": [], "total": 0})
                page.route(f"http://127.0.0.1:{server.server_port}/**", route)
                page.goto(f"http://127.0.0.1:{server.server_port}/")
                page.set_input_files("#translation-cache-file", str(cache))
                page.evaluate('''() => {
                    currentPaper = {id:'cace00000001',title:'Offline specimen'};
                    document.getElementById('cache-only').checked = true;
                    pollTranslationStatus = () => {};
                    startTranslate();
                }''')
                page.wait_for_function("translating === false")
                assert len(submitted) == 1
                path, content_type, payload = submitted[0]
                assert "cache_only=true" in path
                assert "multipart/form-data" in content_type
                assert b'name="cache_file"' in payload and b'offline specimen' in payload
            finally:
                browser.close()
    finally:
        server.shutdown()
        server.server_close()


@pytest.mark.parametrize("width", [1440, 390])
def test_offline_options_panel_does_not_resize_or_escape_toolbar(width):
    from urllib.parse import urlsplit

    with sync_playwright() as pw:
        browser = pw.chromium.launch(
            executable_path="/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
            headless=True,
        )
        try:
            page = browser.new_page(viewport={"width": width, "height": 1000})
            def route(request):
                path = urlsplit(request.request.url).path
                if path.startswith("/static/"):
                    request.fulfill(path=str(ROOT / "app" / path.lstrip("/")))
                elif path == "/":
                    request.fulfill(path=str(ROOT / "app/static/index.html"))
                else:
                    request.fulfill(json={"papers": [], "total": 0})
            page.route("http://options.test/**", route)
            page.goto("http://options.test/")
            page.evaluate("showView('reader')")
            toolbar = page.locator(".reader-toolbar")
            before = toolbar.bounding_box()["height"]
            page.locator(".translation-options summary").click()
            assert toolbar.bounding_box()["height"] == pytest.approx(before, abs=1)
            panel = page.locator(".translation-options-panel").bounding_box()
            upload = page.locator("#translation-cache-file").bounding_box()
            assert 0 <= panel["x"] and panel["x"] + panel["width"] <= width
            assert panel["width"] >= 280
            assert panel["y"] <= upload["y"] < panel["y"] + panel["height"]
        finally:
            browser.close()
