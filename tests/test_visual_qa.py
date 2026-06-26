"""Tests for render-based visual QA scoring."""

import fitz

from pdf_zh_translator.visual_qa import score_visual_layout


def _save_pdf(path, *, filled: bool) -> None:
    document = fitz.open()
    page = document.new_page(width=240, height=240)
    if filled:
        page.draw_rect(fitz.Rect(40, 40, 200, 180), color=(0, 0, 0), fill=(0, 0, 0))
        page.insert_text((50, 210), "Dense academic content", fontsize=10)
    document.save(path)
    document.close()


def _save_corner_pdf(path, *, corner: str) -> None:
    document = fitz.open()
    page = document.new_page(width=240, height=240)
    rect = fitz.Rect(20, 20, 100, 100) if corner == "top" else fitz.Rect(140, 140, 220, 220)
    page.draw_rect(rect, color=(0, 0, 0), fill=(0, 0, 0))
    page.insert_text((50, 210), "Academic content", fontsize=10)
    document.save(path)
    document.close()


def test_visual_score_is_high_for_identical_pages(tmp_path):
    source = tmp_path / "source.pdf"
    _save_pdf(source, filled=True)

    score = score_visual_layout(source, source)

    assert score.overall_score > 0.98
    assert score.pages[0].page == 1


def test_visual_score_is_low_when_translation_is_blank(tmp_path):
    original = tmp_path / "original.pdf"
    translated = tmp_path / "translated.pdf"
    _save_pdf(original, filled=True)
    _save_pdf(translated, filled=False)

    score = score_visual_layout(original, translated)

    assert score.overall_score < 0.35


def test_visual_score_flags_local_region_shift(tmp_path):
    original = tmp_path / "original.pdf"
    translated = tmp_path / "translated.pdf"
    _save_corner_pdf(original, corner="top")
    _save_corner_pdf(translated, corner="bottom")

    score = score_visual_layout(original, translated)

    assert score.min_zone_score < 0.25
    assert score.risk_level == "high"
