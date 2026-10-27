import fitz
import pytest

from pdf_zh_translator import pdf_layout as layout


def test_cross_cell_span_is_not_allowed_to_hide_added_characters():
    cells = [(0.0, 0.0, 10.0, 10.0), (20.0, 0.0, 30.0, 10.0)]
    complete_span = [((0.0, 0.0, 30.0, 10.0), "新增中文")]
    assert all(not layout._text_overlapping_region(complete_span, cell) for cell in cells)
    glyphs = [((0.0, 0.0, 8.0, 10.0), "新"), ((22.0, 0.0, 30.0, 10.0), "文")]
    assert all(layout._has_added_preserved_cjk_glyphs([], glyphs, cell) for cell in cells)


@pytest.mark.parametrize("source,output,expected", [
    ("中文", "中文", False),
    ("中文", "中问", True),
    ("中文", "中文文", True),
    ("", "alpha123", False),
])
def test_existing_chinese_and_latin_contents_keep_their_contract(source, output, expected):
    bbox = (0.0, 0.0, 10.0, 10.0)
    assert layout._has_added_preserved_cjk_glyphs(
        [(bbox, source)], [(bbox, output)], bbox,
    ) is expected


@pytest.mark.parametrize("y,expected", [(4.4, True), (4.6, False), (12.0, False)])
def test_glyph_check_keeps_original_coverage_threshold_and_excludes_notes(y, expected):
    assert layout._has_added_preserved_cjk_glyphs(
        [], [((0.0, y, 10.0, y + 10.0), "文")], (0.0, 0.0, 10.0, 10.0),
    ) is expected


def test_raw_pdf_extracts_individual_cjk_glyph_geometry():
    with fitz.open() as document:
        page = document.new_page()
        page.insert_text((50, 100), "中文", fontname="china-s", fontsize=10)
        entries = layout._cjk_glyph_entries(page)
        assert [text for _, text in entries] == ["中", "文"]
        assert entries[0][0][2] <= entries[1][0][0]
