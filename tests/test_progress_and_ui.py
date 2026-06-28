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


def test_translation_progress_ui_has_client_eta_smoothing():
    js = (ROOT / "app/static/js/app.js").read_text(encoding="utf-8")
    html = (ROOT / "app/static/index.html").read_text(encoding="utf-8")

    assert "let smoothedRate = 0;" in js
    assert "预计剩余" in js
    assert "paper.translation_eta" in js
    assert "paper.translation_stage" in js
    assert "function formatEta(seconds)" in js
    assert 'id="trans-percent"' in html


def test_translation_ui_exposes_qa_and_ocr_controls():
    js = (ROOT / "app/static/js/app.js").read_text(encoding="utf-8")
    html = (ROOT / "app/static/index.html").read_text(encoding="utf-8")
    css = (ROOT / "app/static/css/style.css").read_text(encoding="utf-8")

    assert 'id="qa-mode"' in html
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
