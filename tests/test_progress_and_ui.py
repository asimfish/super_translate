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
    assert "void renderVisiblePages(otherPanel, otherContainer);" in js
    assert "releaseScrollSyncAfterPaint(token);" in js
    assert "requestAnimationFrame(() => syncScrollFromPanel('original'));" in js


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
