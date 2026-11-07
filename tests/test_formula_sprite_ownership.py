"""Inflated formula boxes must not restore the next prose row's pixels."""

import fitz

from pdf_zh_translator.pdf_layout import _formula_atom_masked_pixmap


def test_math_box_does_not_reopen_excluded_neighbour_row():
    doc = fitz.open()
    page = doc.new_page(width=140, height=100)
    page.insert_text((40, 50), "^", fontsize=10)
    page.insert_text((40, 63), "foreign", fontsize=10)
    pix = _formula_atom_masked_pixmap(
        doc, 0, (38, 40, 82, 68), [(40, 44, 60, 65)], 5,
        exclude_bboxes=[(40, 54, 80, 66)],
    )
    alpha = pix.samples[pix.n - 1::pix.n]
    top = round(54 * 5) - pix.y
    bottom = round(66 * 5) - pix.y
    assert any(alpha[:top * pix.width]), "the formula itself must remain visible"
    assert not any(alpha[top * pix.width:bottom * pix.width]), (
        "foreign prose leaked into the sprite"
    )
    doc.close()


def test_overlapping_same_row_prose_box_does_not_erase_math():
    doc = fitz.open()
    page = doc.new_page(width=140, height=100)
    page.insert_text((40, 50), "x", fontsize=10)
    pix = _formula_atom_masked_pixmap(
        doc, 0, (38, 38, 62, 55), [(40, 40, 60, 53)], 5,
        exclude_bboxes=[(40, 40, 60, 53)],
    )
    assert any(pix.samples[pix.n - 1::pix.n]), "the same-row math owner must survive"
    doc.close()
