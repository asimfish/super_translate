"""Regression coverage for the layered text-over-figure QA and repair path."""

from __future__ import annotations

import fitz


def _make_overlap_pdf(path):
    doc = fitz.open()
    page = doc.new_page(width=612, height=792)
    pix = fitz.Pixmap(fitz.csRGB, fitz.IRect(0, 0, 20, 20))
    pix.clear_with(220)
    page.insert_image(fitz.Rect(300, 100, 550, 400), pixmap=pix)
    page.insert_text(
        (100, 180),
        "这是翻译后的正文段落，错误地压到了右侧图片区域。",
        fontname="china-s",
        fontsize=10,
    )
    doc.save(str(path))
    doc.close()


def _text_image_overlap(path):
    with fitz.open(str(path)) as doc:
        page = doc[0]
        image = fitz.Rect(page.get_image_info()[0]["bbox"])
        spans = [
            span
            for block in page.get_text("dict")["blocks"]
            if block.get("type") == 0
            for line in block.get("lines", [])
            for span in line.get("spans", [])
            if span.get("text", "").strip()
        ]
        return any(
            min(span["bbox"][2], image.x1) > max(span["bbox"][0], image.x0)
            and min(span["bbox"][3], image.y1) > max(span["bbox"][1], image.y0)
            for span in spans
        )


def test_visual_overlap_page_is_repaired_after_draft(tmp_path):
    from app.services.layout_fix import fix_translated_layout

    path = tmp_path / "overlap.pdf"
    _make_overlap_pdf(path)
    assert _text_image_overlap(path)

    assert fix_translated_layout(path, visual_overlap_pages={1}) is True
    assert not _text_image_overlap(path)


def test_visual_overlap_repair_is_opt_in(tmp_path):
    from app.services.layout_fix import fix_translated_layout

    path = tmp_path / "overlap.pdf"
    _make_overlap_pdf(path)
    assert fix_translated_layout(path) is False
    assert _text_image_overlap(path)


def _make_positioned_pdf(path, text_x1, text):
    doc = fitz.open()
    page = doc.new_page(width=612, height=792)
    pix = fitz.Pixmap(fitz.csRGB, fitz.IRect(0, 0, 20, 20))
    pix.clear_with(220)
    page.insert_image(fitz.Rect(300, 100, 550, 400), pixmap=pix)
    page.insert_text(
        (100, 180),
        text,
        fontname="china-s" if any("中" <= char <= "鿿" for char in text) else "helv",
        fontsize=10,
    )
    doc.save(str(path))
    doc.close()


def test_visual_overlap_is_reported_by_inspector(tmp_path):
    from pdf_zh_translator.page_inspector import inspect_translation

    source = tmp_path / "source.pdf"
    translated = tmp_path / "translated.pdf"
    _make_positioned_pdf(source, 250, "Source body paragraph stays left of the figure.")
    _make_positioned_pdf(translated, 500, "这是翻译后的正文段落，错误地压到了右侧图片区域。")

    issues = inspect_translation(source, translated)
    assert any(issue.code == "text_visual_overlap" for issue in issues)


def test_qa_repair_scope_is_limited_to_reported_pages(tmp_path, monkeypatch):
    from unittest.mock import patch

    from app.api.papers import _fix_translated_outputs
    from app.services.quality_agent import QualityAction, create_quality_agent
    from app.services.translator import TranslationResult

    issue = type("Issue", (), {"code": "text_visual_overlap", "severity": "error", "page": 2})()
    assert create_quality_agent().plan([issue]).action is QualityAction.REPAIR_LAYOUT
    mono = tmp_path / "mono.pdf"
    dual = tmp_path / "dual.pdf"
    source = tmp_path / "source.pdf"
    for path in (mono, dual, source):
        path.write_bytes(b"%PDF-1.4\n%%EOF\n")
    result = TranslationResult(mono_path=mono, dual_path=dual)
    with (
        patch("app.services.layout_fix.fix_translated_layout", return_value=True) as repair,
        patch("pdf_zh_translator.pdf_layout.create_dual_pdf"),
    ):
        assert _fix_translated_outputs(
            source,
            result,
            visual_overlap_pages={issue.page},
        )
    repair.assert_called_once_with(mono, visual_overlap_pages={2})


def test_post_translation_qa_rechecks_visual_overlap_after_repair(tmp_path):
    from unittest.mock import MagicMock, patch

    from app.api.papers import _run_post_translation_qa
    from app.services.translator import TranslationResult

    source = tmp_path / "source.pdf"
    mono = tmp_path / "mono.pdf"
    dual = tmp_path / "dual.pdf"
    for path in (source, mono, dual):
        path.write_bytes(b"%PDF-1.4\n%%EOF\n")
    issue = type(
        "Issue",
        (),
        {
            "code": "text_visual_overlap",
            "severity": "error",
            "page": 1,
            "message": "body over figure",
        },
    )()
    with (
        patch("app.api.papers._append_log"),
        patch("app.api.papers._set_translation_stage"),
        patch("app.api.papers._record_terminology_candidates"),
        patch("app.api.papers._audit_terminology_usage"),
        patch("app.api.papers._is_cancel_requested", return_value=False),
        patch("app.api.papers._verify_translation_isolated", side_effect=[[issue], []]) as verify,
        patch("app.services.layout_fix.fix_translated_layout", return_value=True) as repair,
        patch("pdf_zh_translator.pdf_layout.create_dual_pdf"),
    ):
        unresolved = _run_post_translation_qa(
            "paper123456",
            MagicMock(),
            source,
            TranslationResult(mono_path=mono, dual_path=dual),
            qa_mode="iterative",
            max_passes=3,
        )
    assert unresolved == []
    assert verify.call_count == 2
    repair.assert_called_once_with(mono, visual_overlap_pages={1})
