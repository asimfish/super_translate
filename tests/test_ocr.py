"""Tests for scanned-PDF OCR detection helpers."""

import fitz

from pdf_zh_translator.ocr import count_text_pages, is_scanned_pdf


def test_text_pdf_is_not_marked_as_scanned(tmp_path):
    pdf_path = tmp_path / "text.pdf"
    document = fitz.open()
    page = document.new_page(width=300, height=300)
    page.insert_text((36, 72), "This page contains enough selectable academic text.", fontsize=10)
    document.save(pdf_path)
    document.close()

    assert count_text_pages(pdf_path) == 1
    assert is_scanned_pdf(pdf_path) is False


def test_image_only_pdf_is_marked_as_scanned(tmp_path):
    pdf_path = tmp_path / "image-only.pdf"
    document = fitz.open()
    page = document.new_page(width=300, height=300)
    page.draw_rect(fitz.Rect(40, 40, 260, 220), color=(0, 0, 0), fill=(0.2, 0.2, 0.2))
    document.save(pdf_path)
    document.close()

    assert count_text_pages(pdf_path) == 0
    assert is_scanned_pdf(pdf_path) is True
