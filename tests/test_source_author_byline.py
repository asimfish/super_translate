"""Verified publisher bylines retain names and symbol ownership."""

from pathlib import Path

import fitz

from pdf_zh_translator import pdf_layout as layout

FIXTURE = Path(__file__).parent / "fixtures/scientificreports62361_p1.pdf"


def test_actual_four_author_line_is_not_translated():
    with fitz.open(FIXTURE) as doc:
        units, _, _ = layout.prepare_translation_units(doc, preserve_graphics_text=True)
    assert not [b for b, _, _ in units if 190 < b.bbox[1] < 208], (
        "Author names and source Wingdings belong to one source owner."
    )


def test_adjacent_actual_bold_abstract_and_body_remain_translatable():
    with fitz.open(FIXTURE) as doc:
        units, _, _ = layout.prepare_translation_units(doc, preserve_graphics_text=True)
    assert any(
        "intricate currents of the Northwest" in layout.strip_sentinels(b.text) for b, _, _ in units
    )
    assert any(
        "western Pacific" in layout.strip_sentinels(b.text)
        or "Northwest Pacific" in layout.strip_sentinels(b.text)
        for b, _, _ in units
    )


def test_verified_metadata_owner_keeps_whole_source_author_line():
    with fitz.open(FIXTURE) as doc:
        blocks, _ = layout.collect_text_blocks(doc)
    authors = [b for b in blocks if 190 < b.bbox[1] < 208]
    assert len(authors) == 1 and not authors[0].should_translate
    assert authors[0].bbox[2] > 403 and authors[0].bbox[0] < 156
    assert authors[0].block_type == "metadata"
    assert authors[0].source_style_runs[0].role == "author_byline"
    assert layout.looks_like_author_metadata(authors[0], authors[0].text)


def test_name_list_requires_actual_publisher_and_affiliation_evidence():
    import copy

    with fitz.open(FIXTURE) as doc:
        page = doc[0]
        raw = page.get_text("dict")
        assert len(layout._source_compact_author_blocks(page, raw, 0)) == 1
        assert layout._source_compact_author_blocks(page, raw, 1) == []
        no_marks = copy.deepcopy(raw)
        for block in no_marks["blocks"]:
            for line in block.get("lines", ()):
                for span in line.get("spans", ()):
                    span["flags"] = int(span.get("flags", 0)) & ~1
        assert layout._source_compact_author_blocks(page, no_marks, 0) == []
        no_publisher = copy.deepcopy(raw)
        for block in no_publisher["blocks"]:
            for line in block.get("lines", ()):
                for span in line.get("spans", ()):
                    span["text"] = span.get("text", "").replace(
                        "Scientific Reports", "Ordinary Report"
                    )
        assert layout._source_compact_author_blocks(page, no_publisher, 0) == []
