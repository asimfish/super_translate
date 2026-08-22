"""Regression tests for progress ETA and reader UI wiring."""

import json
import os
import socket
import subprocess
import sys
import time
from pathlib import Path
from urllib.request import urlopen

import fitz
import pytest
from playwright.sync_api import expect, sync_playwright

from app.api.papers import _format_duration

ROOT = Path(__file__).resolve().parents[1]


def test_format_duration_for_progress_eta():
    assert _format_duration(12) == "12秒"
    assert _format_duration(75) == "1分15秒"
    assert _format_duration(3665) == "1小时01分"


def test_reader_sync_scroll_maps_page_fraction_and_renders_target_panel():
    js = (ROOT / "app/static/js/app.js").read_text(encoding="utf-8")

    assert "function syncScrollFromPanel(panel)" in js
    assert "function pageScrollPosition(panel, scrollTop)" in js
    assert "function targetScrollTop(panel, pageIdx, fraction)" in js
    assert "const fraction = pageHeight > 0" in js
    assert "const mirroredTop = targetScrollTop(otherPanel, pageIdx, fraction);" in js
    assert "otherContainer.scrollTop = mirroredTop;" in js
    assert "requestAnimationFrame(() => syncScrollFromPanel('original'));" in js


def test_reader_sync_scroll_does_not_lock_source_panel_during_mirror_update():
    js = (ROOT / "app/static/js/app.js").read_text(encoding="utf-8")

    assert "let mirroredScrollTops = { original: null, translated: null };" in js
    assert "const mirroredTop = mirroredScrollTops[panel];" in js
    assert "Math.abs(container.scrollTop - mirroredTop) <= 1" in js
    assert "scrollSyncTargetPanel" not in js
    assert "releaseScrollSyncAfterPaint" not in js
    assert "let scrollRafIds = { original: null, translated: null };" in js
    assert "if (scrollRafIds[panel]) return;" in js
    assert "let scrollSyncing = false;" not in js
    assert "scrollSyncing = true;" not in js
    assert "|| scrollSyncing" not in js


def test_reader_sync_scroll_clamps_target_scroll_top_to_panel_bounds():
    js = (ROOT / "app/static/js/app.js").read_text(encoding="utf-8")

    assert (
        "const maxScrollTop = Math.max(0, container.scrollHeight - container.clientHeight);"
        in js
    )
    assert "return Math.max(0, Math.min(maxScrollTop, rawScrollTop));" in js


def test_reader_sync_scroll_defers_mirror_render_to_avoid_jank():
    """Sync scroll must not render the mirrored panel on every scroll frame."""
    js = (ROOT / "app/static/js/app.js").read_text(encoding="utf-8")
    css = (ROOT / "app/static/css/style.css").read_text(encoding="utf-8")

    # The heavy render is debounced behind a timer, not called inline in sync.
    assert "function scheduleOtherPanelRender(otherPanel, otherContainer)" in js
    assert "scheduleOtherPanelRender(otherPanel, otherContainer);" in js
    assert "syncRenderTimer" in js
    # Programmatic scrollTop must not be animated by CSS smooth scrolling.
    assert "scroll-behavior: smooth" not in css
    assert "overflow-anchor: none" in css


def test_reader_image_mode_builds_scroll_metrics_and_can_drive_sync():
    """Whalent's page-image mode must participate in the same sync protocol."""
    js = (ROOT / "app/static/js/app.js").read_text(encoding="utf-8")

    assert "function refreshImagePageMetrics(panel)" in js
    assert "pageWrappers[panel] = wrappers;" in js
    assert "refreshImagePageMetrics(panel);" in js
    assert "setupSmoothScrollSync(panel);" in js
    assert "!pdfDocs[otherPanel] && !pageWrappers[otherPanel]?.length" in js
    assert "await imageLoaded;" in js
    assert "await new Promise(resolve => requestAnimationFrame(resolve));" in js
    assert "refreshImagePageMetrics(panel);" in js
    assert "wrapper._previewLoadId === loadId" in js
    assert "return wrapper._previewLoadPromise;" in js
    assert "window.addEventListener('resize'" in js
    assert "refreshImagePageMetrics('original')" not in js


def _available_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _paper_payload(paper_id: str) -> dict:
    translating = paper_id == "paper-a"
    return {
        "id": paper_id,
        "title": f"Browser E2E {paper_id}",
        "page_count": 19 if translating else 7,
        "file_size": 1024,
        "tags": [],
        "translation_status": "translating" if translating else "completed",
        "translation_progress": 0.42 if translating else 1.0,
        "translation_stage": "翻译正文" if translating else "已完成",
        "translation_eta": 80 if translating else None,
        "translation_log": [],
        "has_translated": True,
        "has_dual": False,
        "has_qa_report": False,
        "created_at": "2026-08-10T00:00:00",
    }


def _scroll_panel_to(page, panel: str, page_index: int, fraction: float) -> None:
    page.evaluate(
        """([panel, pageIndex, fraction]) => {
          const container = document.getElementById(`pdf-container-${panel}`);
          const metric = pageMetrics[panel][pageIndex];
          container.scrollTop = metric.top + metric.height * fraction;
          container.dispatchEvent(new Event('scroll'));
        }""",
        [panel, page_index, fraction],
    )


def _panel_position(page, panel: str) -> dict:
    return page.evaluate(
        """panel => {
          const container = document.getElementById(`pdf-container-${panel}`);
          return pageScrollPosition(panel, container.scrollTop);
        }""",
        panel,
    )


@pytest.mark.e2e
def test_reader_image_mode_sync_scroll_real_browser(tmp_path):
    """Exercise bidirectional sync and listener/progress isolation in Chromium."""
    port = _available_port()
    base_url = f"http://127.0.0.1:{port}"
    env = os.environ.copy()
    env.update(
        {
            "PAPER_CHINA_BASE_DIR": str(tmp_path / "server-data"),
            "PAPER_CHINA_DB_PATH": "paper-china.db",
            "PAPER_CHINA_ALLOW_UNAUTHENTICATED_REMOTE": "true",
        }
    )
    server_log_path = tmp_path / "uvicorn.log"
    server_log = server_log_path.open("w+", encoding="utf-8")
    server = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "uvicorn",
            "app.main:app",
            "--host",
            "127.0.0.1",
            "--port",
            str(port),
        ],
        cwd=ROOT,
        env=env,
        stdout=server_log,
        stderr=subprocess.STDOUT,
    )
    deadline = time.monotonic() + 60.0
    ready = False
    while time.monotonic() < deadline:
        returncode = server.poll()
        if returncode is not None:
            break
        try:
            with urlopen(f"{base_url}/health", timeout=1) as response:
                if response.status == 200:
                    ready = True
                    break
        except OSError:
            time.sleep(0.1)
    else:
        returncode = server.poll()
    if not ready:
        if server.poll() is None:
            server.terminate()
            try:
                server.wait(timeout=5)
            except subprocess.TimeoutExpired:
                server.kill()
                server.wait(timeout=5)
        server_log.flush()
        server_log.seek(0)
        logs = server_log.read()[-4000:]
        server_log.close()
        raise AssertionError(
            "E2E server did not become ready "
            f"(exit={returncode!r}); uvicorn log:\n{logs}"
        )

    artifact_dir = Path(
        os.environ.get("PAPER_CHINA_E2E_ARTIFACT_DIR", str(tmp_path / "artifacts"))
    )
    artifact_dir.mkdir(parents=True, exist_ok=True)
    try:
        with sync_playwright() as playwright:
            chrome = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
            launch_options = {"headless": True}
            if Path(chrome).is_file():
                launch_options["executable_path"] = chrome
            browser = playwright.chromium.launch(**launch_options)
            context = browser.new_context(
                viewport={"width": 1440, "height": 1000},
                record_video_dir=str(artifact_dir),
            )
            page = context.new_page()
            page.add_init_script(
                "localStorage.setItem('paperChinaReaderMode', 'image');"
            )

            def route_api(route):
                url = route.request.url
                if "/preview/" in url:
                    translated = "/translated/" in url
                    height = 920 if translated else 800
                    svg = (
                        f'<svg xmlns="http://www.w3.org/2000/svg" width="600" '
                        f'height="{height}"><rect width="100%" height="100%" fill="white"/>'
                        '<text x="30" y="60" font-size="28">E2E PDF page</text></svg>'
                    )
                    route.fulfill(status=200, content_type="image/svg+xml", body=svg)
                    return
                if url.endswith("/api/stats"):
                    route.fulfill(
                        status=200,
                        content_type="application/json",
                        body=json.dumps({"total_papers": 2, "completed_translations": 1}),
                    )
                    return
                if "/api/papers/paper-" in url:
                    paper_id = "paper-b" if "paper-b" in url else "paper-a"
                    route.fulfill(
                        status=200,
                        content_type="application/json",
                        body=json.dumps(_paper_payload(paper_id), ensure_ascii=False),
                    )
                    return
                if "/api/papers" in url:
                    route.fulfill(
                        status=200,
                        content_type="application/json",
                        body=json.dumps(
                            {
                                "papers": [_paper_payload("paper-a"), _paper_payload("paper-b")],
                                "total": 2,
                            },
                            ensure_ascii=False,
                        ),
                    )
                    return
                route.continue_()

            page.route("**/api/**", route_api)
            page.goto(base_url, wait_until="domcontentloaded")
            page.evaluate("openReader('paper-a')")
            page.wait_for_function(
                "pageWrappers.original.length === 19 && pageWrappers.translated.length === 19"
            )
            page.evaluate(
                """async () => {
                  await Promise.all(['original', 'translated'].flatMap(panel =>
                    pageWrappers[panel].map(wrapper =>
                      loadPreviewImage(panel, wrapper, currentLoadId))));
                  await new Promise(resolve => requestAnimationFrame(() =>
                    requestAnimationFrame(resolve)));
                  refreshImagePageMetrics('original');
                  refreshImagePageMetrics('translated');
                }"""
            )
            page.wait_for_function(
                "pageMetrics.original.length === 19 && pageMetrics.translated.length === 19"
            )
            page.wait_for_function(
                "!document.getElementById('translation-progress').classList.contains('hidden')"
            )

            for source_panel, target_panel, page_index, fraction in (
                ("original", "translated", 3, 0.40),
                ("translated", "original", 15, 0.35),
                ("original", "translated", 0, 0.0),
                ("translated", "original", 18, 0.95),
            ):
                _scroll_panel_to(page, source_panel, page_index, fraction)
                page.wait_for_timeout(180)
                source_position = _panel_position(page, source_panel)
                target_position = _panel_position(page, target_panel)
                diagnostics = page.evaluate(
                    """([sourcePanel, targetPanel, pageIndex, fraction]) => {
                      const source = document.getElementById(`pdf-container-${sourcePanel}`);
                      const target = document.getElementById(`pdf-container-${targetPanel}`);
                      return {
                        syncScrollEnabled,
                        mirroredScrollTops,
                        scrollRafIds,
                        sourceTop: source.scrollTop,
                        targetTop: target.scrollTop,
                        sourceMax: source.scrollHeight - source.clientHeight,
                        targetMax: target.scrollHeight - target.clientHeight,
                        targetExpected: targetScrollTop(targetPanel, pageIndex, fraction),
                        sourceHeight: source.clientHeight,
                        targetHeight: target.clientHeight,
                        targetHidden: target.classList.contains('hidden'),
                        sourceMetric: pageMetrics[sourcePanel][pageIndex],
                        targetMetric: pageMetrics[targetPanel][pageIndex],
                        sourceWrapper: {
                          top: pageWrappers[sourcePanel][pageIndex].offsetTop,
                          height: pageWrappers[sourcePanel][pageIndex].offsetHeight,
                        },
                        targetWrapper: {
                          top: pageWrappers[targetPanel][pageIndex].offsetTop,
                          height: pageWrappers[targetPanel][pageIndex].offsetHeight,
                        },
                        sourcePosition: pageScrollPosition(sourcePanel, source.scrollTop),
                        targetPosition: pageScrollPosition(targetPanel, target.scrollTop),
                      };
                    }""",
                    [source_panel, target_panel, page_index, fraction],
                )
                if page_index == 18:
                    assert abs(diagnostics["sourceTop"] - diagnostics["sourceMax"]) <= 1
                    assert abs(diagnostics["targetTop"] - diagnostics["targetMax"]) <= 1
                    continue
                assert source_position["pageIdx"] == page_index, diagnostics
                assert abs(source_position["fraction"] - fraction) <= 0.02, diagnostics
                assert target_position["pageIdx"] == source_position["pageIdx"], diagnostics
                assert (
                    abs(target_position["fraction"] - source_position["fraction"])
                    <= 0.05
                ), diagnostics

            page.screenshot(path=artifact_dir / "sync-page16.png", full_page=True)
            page.set_viewport_size({"width": 1100, "height": 820})
            page.wait_for_timeout(350)
            dimensions = page.evaluate(
                """() => ['original', 'translated'].map(panel => ({
                  metric: pageMetrics[panel][0].height,
                  actual: pageWrappers[panel][0].offsetHeight,
                }))"""
            )
            assert all(abs(item["metric"] - item["actual"]) <= 1 for item in dimensions)

            page.click("#btn-sync-scroll")
            frozen = page.evaluate(
                "document.getElementById('pdf-container-translated').scrollTop"
            )
            _scroll_panel_to(page, "original", 2, 0.25)
            page.wait_for_timeout(180)
            assert abs(
                page.evaluate(
                    "document.getElementById('pdf-container-translated').scrollTop"
                )
                - frozen
            ) <= 1
            page.click("#btn-sync-scroll")
            _scroll_panel_to(page, "original", 4, 0.5)
            page.wait_for_timeout(180)
            assert _panel_position(page, "translated")["pageIdx"] == 4

            cdp = context.new_cdp_session(page)

            def scroll_listener_count() -> int:
                result = cdp.send(
                    "Runtime.evaluate",
                    {
                        "expression": (
                            "getEventListeners(document.getElementById("
                            "'pdf-container-original')).scroll.length"
                        ),
                        "includeCommandLineAPI": True,
                        "returnByValue": True,
                    },
                )
                return int(result["result"]["value"])

            assert scroll_listener_count() == 1
            old_generation = page.evaluate("translationPollGeneration")
            page.evaluate("openReader('paper-b')")
            page.wait_for_function(
                "currentPaper?.id === 'paper-b' && pageWrappers.original.length === 7"
            )
            page.wait_for_function("pageWrappers.translated.length === 7")
            assert scroll_listener_count() == 1
            assert page.evaluate("translationPollGeneration") > old_generation
            assert page.evaluate("translationPollPaperId") is None
            assert page.locator("#translation-progress").evaluate(
                "element => element.classList.contains('hidden')"
            )
            page.screenshot(path=artifact_dir / "sync-paper-switch.png", full_page=True)
            context.close()
            browser.close()
    finally:
        server.terminate()
        try:
            server.wait(timeout=5)
        except subprocess.TimeoutExpired:
            server.kill()
            server.wait(timeout=5)
        server_log.close()


def test_reader_pdf_open_renders_first_page_before_background_work():
    js = (ROOT / "app/static/js/app.js").read_text(encoding="utf-8")
    css = (ROOT / "app/static/css/style.css").read_text(encoding="utf-8")

    assert "function buildPageMetricsFromFirstPage(pdf, firstViewport, adaptiveScale)" in js
    assert "void refinePdfMetrics(panel, pdf, adaptiveScale);" in js
    assert (
        "await renderVisiblePages(panel, container, { awaitFirst: true, deferRest: true });"
        in js
    )
    assert "wrapper.innerHTML = '<div class=\"pdf-page-loading\">加载中...</div>';" in js
    assert ".pdf-page-loading" in css
    assert "void loadPanelDocument('translated'" in js
    assert "await loadPanelDocument('translated'" not in js


def test_reader_render_work_is_bounded_and_cancelled_when_paper_changes():
    js = (ROOT / "app/static/js/app.js").read_text(encoding="utf-8")

    assert "const MAX_CANVAS_DPR = 1.5;" in js
    assert "Math.min(window.devicePixelRatio || 1, MAX_CANVAS_DPR)" in js
    assert "let activeRenderTasks = { original: new Map(), translated: new Map() };" in js
    assert "renderTask.cancel();" in js
    assert "loadId !== currentLoadId" in js
    assert "const OVERSCAN_PX = 320;" in js


def test_mobile_reader_panels_keep_pdf_pages_inside_scroll_containers():
    js = (ROOT / "app/static/js/app.js").read_text(encoding="utf-8")
    css = (ROOT / "app/static/css/style.css").read_text(encoding="utf-8")

    assert "window.scrollTo({ top: 0, left: 0, behavior: 'auto' });" in js
    assert "min-width: 0; min-height: 0;" in css
    assert ".pdf-container {" in css
    assert "min-height: 0;" in css
    assert ".pdf-panel { flex: 0 0 50vh; height: 50vh; min-height: 0; }" in css


def test_translation_progress_ui_has_client_eta_smoothing():
    js = (ROOT / "app/static/js/app.js").read_text(encoding="utf-8")
    html = (ROOT / "app/static/index.html").read_text(encoding="utf-8")
    css = (ROOT / "app/static/css/style.css").read_text(encoding="utf-8")

    assert "let smoothedRate = 0;" in js
    assert "预计剩余" in js
    assert "paper.translation_eta" in js
    assert "paper.translation_stage" in js
    assert "function formatEta(seconds)" in js
    assert 'id="trans-percent"' in html
    assert "async function refreshTranslationStatus()" in js
    assert "refreshTranslationStatus();" in js
    assert "const POLL_INTERVAL_MS = 2000;" in js
    assert "setInterval(refreshTranslationStatus, POLL_INTERVAL_MS)" in js
    assert "let statusRequestInFlight = false;" in js
    assert "if (statusRequestInFlight) return;" in js
    assert "setInterval(refreshTranslationStatus, 1000)" not in js
    assert "等待首批进度" in js
    assert "progress-fill-pending" in js
    assert ".progress-fill-active" in css
    assert "@keyframes progress-stripes" in css


def test_translation_repairing_state_is_visible_without_leaking_live_polling():
    js = (ROOT / "app/static/js/app.js").read_text(encoding="utf-8")
    css = (ROOT / "app/static/css/style.css").read_text(encoding="utf-8")
    html = (ROOT / "app/static/index.html").read_text(encoding="utf-8")

    assert "repairing: '等待系统修复'" in js
    assert "paper.translation_status === 'repairing'" in js
    assert "p.translation_status === 'repairing'" in js
    assert "currentPaper.translation_status === 'repairing'" in js
    assert ".status-repairing" in css
    assert '<option value="repairing">等待系统修复</option>' in html


def test_translation_progress_is_scoped_to_the_active_paper():
    js = (ROOT / "app/static/js/app.js").read_text(encoding="utf-8")

    assert "let translationPollPaperId = null;" in js
    assert "let translationPollGeneration = 0;" in js
    assert "translationPollPaperId !== paperId" in js
    assert "const pollGeneration = ++translationPollGeneration;" in js
    assert "pollGeneration !== translationPollGeneration" in js
    assert "currentPaper = papers.find(p => p.id === paperId) || null;" not in js


def test_translation_start_shows_progress_before_request_finishes():
    js = (ROOT / "app/static/js/app.js").read_text(encoding="utf-8")

    assert "function showTranslationSubmitting(paperId)" in js
    assert "正在提交翻译任务，服务器确认后立即开始排队..." in js
    assert "function showTranslationStartFailure(message)" in js
    assert "showTranslationSubmitting(paperId);" in js
    assert "await api.translatePaper(paperId, backend, quality, normalizedOptions);" in js
    assert js.index("showTranslationSubmitting(paperId);") < js.index(
        "await api.translatePaper(paperId, backend, quality, normalizedOptions);"
    )


def test_translation_start_records_early_backend_stages():
    api = (ROOT / "app/api/papers.py").read_text(encoding="utf-8")

    assert 'translation_stage="已提交"' in api
    assert '_set_translation_stage(paper_id, loop, "解析 PDF")' in api


def test_translation_ui_exposes_qa_and_ocr_controls():
    js = (ROOT / "app/static/js/app.js").read_text(encoding="utf-8")
    html = (ROOT / "app/static/index.html").read_text(encoding="utf-8")
    css = (ROOT / "app/static/css/style.css").read_text(encoding="utf-8")

    assert 'id="qa-mode"' in html
    assert 'value="single" selected' in html
    assert 'value="iterative"' in html
    assert 'id="ocr-mode"' in html
    assert "params.set('qa_mode'" in js
    assert "params.set('ocr_mode'" in js
    assert "api.translatePaper(p.id, '', quality, options)" in js
    assert 'id="btn-qa-report"' in html
    assert 'id="qa-report-panel"' in html
    assert "async getQaReport(id)" in js
    assert "function renderQaReport(report)" in js
    assert "report.pass_history" in js
    assert "qa-pass-history" in js
    assert "已触发修复" in js
    assert ".qa-pass-history" in css
    assert ".qa-pass-item" in css
    assert "'show-qa-report': showQaReport" in js


def test_translation_ui_preserves_graphics_text_by_default():
    js = (ROOT / "app/static/js/app.js").read_text(encoding="utf-8")
    html = (ROOT / "app/static/index.html").read_text(encoding="utf-8")

    assert 'id="preserve-graphics-text" checked' in html
    assert "function getPreserveGraphicsTextOption()" in js
    assert "const normalizedOptions = { preserve_graphics_text: true, ...options };" in js
    assert "options.preserve_graphics_text ? 'true' : 'false'" in js


def test_upload_view_stages_files_until_user_confirms_upload():
    js = (ROOT / "app/static/js/app.js").read_text(encoding="utf-8")
    html = (ROOT / "app/static/index.html").read_text(encoding="utf-8")
    css = (ROOT / "app/static/css/style.css").read_text(encoding="utf-8")

    assert 'id="upload-summary"' in html
    assert "待上传列表" in html
    assert "确认上传" in html
    assert "let selectedFileKeys = new Set();" in js
    assert "function uploadFileKey(file)" in js
    assert "selectedFileKeys.has(key)" in js
    assert "已跳过 ${duplicates} 个重复文件" in js
    assert "document.getElementById('drop-zone').classList.remove('hidden');" in js
    assert "const filesToUpload = [...selectedFiles];" in js
    assert "`确认上传 ${selectedFiles.length} 篇`" in js
    assert "RESUMABLE_UPLOAD_THRESHOLD" in js
    assert "/api/papers/uploads/init" in js
    assert "crypto.subtle.digest('SHA-256'" in js
    assert "uploadChunkWithRetry" in js
    assert "selectedFiles = failedFiles;" in js
    assert "pendingUploadSessions" in js
    assert ".upload-queue-header" in css


@pytest.mark.e2e
def test_large_upload_recovers_when_completion_response_is_lost(tmp_path):
    """A committed upload must survive a dropped proxy response without duplication."""
    pdf_path = tmp_path / "large-proxy-test.pdf"
    with fitz.open() as document:
        page = document.new_page()
        page.insert_text((72, 72), "Proxy-safe resumable upload")
        document.save(pdf_path)
    with pdf_path.open("ab") as output:
        output.write((b"%" + b"x" * 1022 + b"\n") * (9 * 1024))
        output.write(b"%%EOF\n")
    assert pdf_path.stat().st_size > 8 * 1024 * 1024

    port = _available_port()
    base_url = f"http://127.0.0.1:{port}"
    env = os.environ.copy()
    env.update(
        {
            "PAPER_CHINA_BASE_DIR": str(tmp_path / "upload-server"),
            "PAPER_CHINA_DB_PATH": "paper-china.db",
            "PAPER_CHINA_ALLOW_UNAUTHENTICATED_REMOTE": "true",
        }
    )
    server = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "uvicorn",
            "app.main:app",
            "--host",
            "127.0.0.1",
            "--port",
            str(port),
        ],
        cwd=ROOT,
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    deadline = time.monotonic() + 20
    while time.monotonic() < deadline:
        try:
            with urlopen(f"{base_url}/health", timeout=1) as response:
                if response.status == 200:
                    break
        except OSError:
            time.sleep(0.1)
    else:
        server.terminate()
        raise AssertionError("Upload E2E server did not become ready")

    completion_attempts = 0
    chunk_sizes: list[int] = []
    try:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True)
            page = browser.new_page(viewport={"width": 1280, "height": 900})

            def observe_request(request):
                if request.method == "PUT" and "/chunks/" in request.url:
                    chunk_sizes.append(len(request.post_data_buffer or b""))

            def drop_first_completion(route):
                nonlocal completion_attempts
                completion_attempts += 1
                if completion_attempts == 1:
                    response = route.fetch()
                    assert response.ok
                    response.dispose()
                    route.abort("failed")
                    return
                route.continue_()

            page.on("request", observe_request)
            page.route("**/api/papers/uploads/*/complete", drop_first_completion)
            page.goto(base_url, wait_until="domcontentloaded")
            page.locator('[data-action="show-upload"]').first.click()
            page.set_input_files("#file-input", str(pdf_path))
            page.click("#btn-do-upload")
            expect(page.locator("#upload-status")).to_contain_text("完成", timeout=90_000)
            listing = page.evaluate(
                "async () => (await fetch('/api/papers/?offset=0&limit=50')).json()"
            )
            pending = page.evaluate(
                "localStorage.getItem('paperChinaPendingUploads') || '[]'"
            )
            page.screenshot(path=str(tmp_path / "resumable-upload.png"), full_page=True)
            browser.close()

        assert completion_attempts >= 2
        assert len(chunk_sizes) == 3
        assert max(chunk_sizes) <= 4 * 1024 * 1024
        assert listing["total"] == 1
        assert len(listing["papers"]) == 1
        assert json.loads(pending) == []
    finally:
        server.terminate()
        try:
            server.wait(timeout=10)
        except subprocess.TimeoutExpired:
            server.kill()
            server.wait(timeout=5)


def test_reader_renders_pdf_text_layer_for_selection():
    js = (ROOT / "app/static/js/app.js").read_text(encoding="utf-8")
    html = (ROOT / "app/static/index.html").read_text(encoding="utf-8")
    css = (ROOT / "app/static/css/style.css").read_text(encoding="utf-8")

    assert "async function renderTextLayer(page, wrapper, viewport)" in js
    assert "pdfjsLib.renderTextLayer" in js
    assert "textContentSource: textContent" in js
    assert "void renderTextLayer(page, wrapper, viewport);" in js
    assert "scheduleIdleWork(() => {" in js
    assert "textLayer.style.setProperty('--scale-factor', viewport.scale);" in js
    assert "async function copyPdfSelection()" in js
    assert "'copy-pdf-selection': copyPdfSelection" in js
    assert 'id="btn-copy-selection"' in html
    assert ".textLayer" in css
    assert "--scale-factor: 1" in css
    assert "user-select: text" in css
    assert ".textLayer ::selection" in css
    assert ".textLayer span::selection" in css


def test_reader_uses_authenticated_pdf_worker_on_whalent_preview():
    js = (ROOT / "app/static/js/app.js").read_text(encoding="utf-8")

    assert "function isWhalentForwardedPreview()" in js
    assert "endsWith('.fwd.memory.whalent.com')" in js
    assert "async function prepareForwardedPdfWorker(workerSrc)" in js
    assert "credentials: 'include'" in js
    assert "cache: 'no-store'" in js
    assert "GlobalWorkerOptions.workerPort = new Worker(workerUrl)" in js
    assert "await preloadPdfWorkerOnMainThread(workerSrc)" in js
    assert "await pdfWorkerReady;" in js


def test_reader_ui_exposes_editable_figure_manifest_workflow():
    js = (ROOT / "app/static/js/app.js").read_text(encoding="utf-8")
    html = (ROOT / "app/static/index.html").read_text(encoding="utf-8")

    assert 'id="btn-editable-figures"' in html
    assert 'id="editable-figures-panel"' in html
    assert "async getEditableFigureManifest(id)" in js
    assert "async function extractEditableFigures()" in js
    assert "function renderEditableFigureManifest(manifest)" in js
    assert "'show-editable-figures': showEditableFigures" in js
    assert "'extract-editable-figures': extractEditableFigures" in js


def test_frontend_accessibility_and_lazy_pdf_contracts_are_wired():
    js = (ROOT / "app/static/js/app.js").read_text(encoding="utf-8")
    html = (ROOT / "app/static/index.html").read_text(encoding="utf-8")
    css = (ROOT / "app/static/css/style.css").read_text(encoding="utf-8")

    assert '<a class="skip-link" href="#main-content">' in html
    assert 'id="main-content" tabindex="-1"' in html
    assert '<script src="/static/js/pdf.min.js" defer></script>' not in html
    assert "function ensurePdfLibrary()" in js
    assert "await ensurePdfLibrary();" in js
    assert 'class="paper-card-link"' in js
    assert 'role="progressbar"' in html
    assert "trapModalFocus(providerModal, e)" in js
    assert "providerSettingsReturnFocus.focus();" in js
    assert "toast.setAttribute('role', type === 'error' ? 'alert' : 'status')" in js
    assert "resizer.addEventListener('keydown'" in js
    assert "@media (prefers-reduced-motion: reduce)" in css
    assert ".header-right" in css
    assert "flex: 1 1 100%;" in css


@pytest.mark.e2e
def test_frontend_accessibility_mobile_and_lazy_pdf_real_browser(tmp_path):
    """Verify keyboard, mobile, reduced-motion, and lazy-load behavior in Chromium."""
    port = _available_port()
    base_url = f"http://127.0.0.1:{port}"
    env = os.environ.copy()
    env.update(
        {
            "PAPER_CHINA_BASE_DIR": str(tmp_path / "frontend-server"),
            "PAPER_CHINA_DB_PATH": "paper-china.db",
            "PAPER_CHINA_ALLOW_UNAUTHENTICATED_REMOTE": "true",
        }
    )
    server = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "uvicorn",
            "app.main:app",
            "--host",
            "127.0.0.1",
            "--port",
            str(port),
        ],
        cwd=ROOT,
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
        try:
            with urlopen(f"{base_url}/health", timeout=1) as response:
                if response.status == 200:
                    break
        except OSError:
            time.sleep(0.1)
    else:
        server.terminate()
        raise AssertionError("Frontend E2E server did not become ready")

    paper = {
        **_paper_payload("paper-a"),
        "tags": "attention, benchmark",
        "updated_at": "2026-08-10T00:00:00",
    }
    requested_urls: list[str] = []
    browser_errors: list[str] = []
    try:
        with sync_playwright() as playwright:
            chrome = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
            launch_options = {"headless": True}
            if Path(chrome).is_file():
                launch_options["executable_path"] = chrome
            browser = playwright.chromium.launch(**launch_options)
            context = browser.new_context(viewport={"width": 1440, "height": 1000})
            page = context.new_page()
            page.on("request", lambda request: requested_urls.append(request.url))
            page.on("console", lambda message: (
                browser_errors.append(message.text) if message.type == "error" else None
            ))
            page.on("pageerror", lambda error: browser_errors.append(str(error)))

            def route_api(route):
                url = route.request.url
                if url.endswith("/api/provider-credentials/models"):
                    body = []
                elif url.endswith("/api/provider-credentials"):
                    body = []
                elif url.endswith("/api/stats"):
                    body = {"total_papers": 1, "completed_translations": 0}
                elif "/api/papers" in url:
                    body = {"papers": [paper], "total": 1}
                else:
                    body = {}
                route.fulfill(
                    status=200,
                    content_type="application/json",
                    body=json.dumps(body, ensure_ascii=False),
                )

            page.route("**/api/**", route_api)
            page.goto(base_url, wait_until="load")
            expect(page.locator(".paper-card-link")).to_have_count(1)
            assert not any(url.endswith("/static/js/pdf.min.js") for url in requested_urls)

            title_button = page.locator(".paper-card-link")
            title_button.focus()
            assert page.evaluate("document.activeElement.className") == "paper-card-link"
            assert page.locator(".tag-chip").first.evaluate(
                "element => element.tagName"
            ) == "BUTTON"
            expect(page.locator('.paper-card [role="progressbar"]')).to_have_attribute(
                "aria-valuenow", "42"
            )

            settings_button = page.locator("#btn-provider-settings")
            settings_button.focus()
            settings_button.click()
            modal = page.locator("#provider-settings-modal")
            expect(modal).to_be_visible()
            assert page.evaluate(
                "document.getElementById('provider-settings-modal').contains(document.activeElement)"
            )
            page.locator("#provider-settings-modal button:not([disabled])").last.focus()
            page.keyboard.press("Tab")
            assert page.evaluate(
                "document.activeElement === "
                "document.querySelector('#provider-settings-modal button')"
            )
            page.keyboard.press("Escape")
            expect(modal).to_be_hidden()
            assert page.evaluate("document.activeElement.id") == "btn-provider-settings"

            page.evaluate("showToast('浏览器可访问性测试')")
            expect(page.locator("#toast-container")).to_have_attribute("aria-live", "polite")
            expect(page.locator(".toast").last).to_have_attribute("role", "status")

            page.evaluate("showView('reader')")
            resizer = page.locator("#resizer")
            resizer.focus()
            page.keyboard.press("ArrowRight")
            expect(resizer).to_have_attribute("aria-valuenow", "55")

            for width in (390, 320):
                page.set_viewport_size({"width": width, "height": 844})
                page.evaluate("showView('library')")
                assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
            expect(resizer).to_have_attribute("aria-orientation", "horizontal")

            page.evaluate("ensurePdfLibrary()")
            page.wait_for_function("typeof pdfjsLib !== 'undefined'")
            assert any(url.endswith("/static/js/pdf.min.js") for url in requested_urls)
            assert browser_errors == []
            context.close()

            reduced_context = browser.new_context(
                viewport={"width": 390, "height": 844}, reduced_motion="reduce"
            )
            reduced_page = reduced_context.new_page()
            reduced_page.route("**/api/**", route_api)
            reduced_page.goto(base_url, wait_until="load")
            animation_duration = reduced_page.locator(".skeleton-card").first.evaluate(
                "element => getComputedStyle(element).animationDuration"
            )
            assert animation_duration in {"0s", "1e-05s", "0.00001s"}
            reduced_context.close()
            browser.close()
    finally:
        server.terminate()
        try:
            server.wait(timeout=10)
        except subprocess.TimeoutExpired:
            server.kill()
            server.wait(timeout=5)
