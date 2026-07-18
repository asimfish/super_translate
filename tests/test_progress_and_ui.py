"""Regression tests for progress ETA and reader UI wiring."""

from pathlib import Path

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
    assert "otherContainer.scrollTop = targetScrollTop(otherPanel, pageIdx, fraction);" in js
    assert "releaseScrollSyncAfterPaint(token, otherPanel);" in js
    assert "requestAnimationFrame(() => syncScrollFromPanel('original'));" in js


def test_reader_sync_scroll_does_not_lock_source_panel_during_mirror_update():
    js = (ROOT / "app/static/js/app.js").read_text(encoding="utf-8")

    assert "let scrollSyncTargetPanel = null;" in js
    assert "scrollSyncTargetPanel === panel" in js
    assert "scrollSyncTargetPanel = otherPanel;" in js
    assert "scrollSyncTargetPanel = null;" in js
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
    assert "void loadPdfDocument('translated'" in js
    assert "await loadPdfDocument('translated'" not in js


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
    assert ".upload-queue-header" in css


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
