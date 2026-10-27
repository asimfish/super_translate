"""Observable regressions for mixed academic documents and book source ownership."""

import fitz
import pytest

from pdf_zh_translator.layout_families import (
    load_layout_registry,
    page_layout,
    validate_layout_registry,
)
from pdf_zh_translator.layout_profiles import detect_layout_profile
from pdf_zh_translator.pdf_layout import prepare_translation_units


def two_columns(page):
    for y in range(130, 500, 24):
        page.insert_text((54, y), "A full scientific paragraph in the left column.", fontsize=9)
        page.insert_text((325, y), "A full scientific paragraph in the right column.", fontsize=9)


def book():
    doc = fitz.open()
    doc.new_page().insert_text((60, 150), "An Academic Book", fontsize=24)
    p = doc.new_page()
    p.insert_text((60, 80), "Contents", fontsize=18)
    p.insert_text((60, 140), "1 Introduction . . . . . . . . . . . . . . . . . 1")
    p.insert_text((60, 165), "2 Data structures . . . . . . . . . . . . . . . . 12")
    for n in (1, 2):
        p = doc.new_page()
        p.insert_text((60, 95), f"Chapter {n}", fontsize=20)
        p.insert_text((60, 125), "A scientific introduction", fontsize=24)
        p.insert_text((60, 180), "This chapter explains the scientific method and data analysis.")
        p.insert_text((60, 215), ">>> print('hello')", fontname="cour", fontsize=10)
        p.insert_text((60, 230), "hello", fontname="cour", fontsize=10)
        p.insert_text((60, 280), "The program prints a greeting and preserves the example syntax.")
    return doc


def test_acl_us_letter_is_not_shadowed_by_generic_columns():
    with fitz.open() as doc:
        p = doc.new_page(width=612, height=792)
        two_columns(p)
        p.insert_text((54, 740), "Association for Computational Linguistics", fontsize=8)
        assert detect_layout_profile(doc).name == "acl_anthology"


def test_body_columns_are_not_inferred_from_a_cover():
    with fitz.open() as doc:
        doc.new_page().insert_text((60, 150), "Conference proceedings", fontsize=24)
        for _ in range(3):
            two_columns(doc.new_page())
        assert detect_layout_profile(doc).columns == 2


def test_a_publisher_mentioned_in_prose_is_not_a_venue():
    with fitz.open() as doc:
        p = doc.new_page(width=612, height=792)
        two_columns(p)
        p.insert_text((54, 560), "Related work includes IEEE and ACM papers.", fontsize=9)
        assert detect_layout_profile(doc).name == "generic_two_column"


def test_book_structure_is_detected_beyond_cover():
    with book() as doc:
        assert detect_layout_profile(doc).name == "academic_book"


def test_book_chapter_titles_and_source_code_have_separate_owners():
    with book() as doc:
        units, _, _ = prepare_translation_units(doc, preserve_graphics_text=True)
        chapter = [b for b, _, _ in units if "Chapter 2" in b.text]
        assert len(chapter) == 1
        assert chapter[0].block_type == "heading"
        assert not any("print(" in b.text for b, _, _ in units)
        assert any("program prints" in b.text for b, _, _ in units)


def test_code_and_explanation_in_one_source_object_are_not_merged():
    with book() as doc:
        p = doc[3]
        # Closely spaced fonts make MuPDF expose these as one source object,
        # as observed on Think Python's assignment-statements page.
        p.insert_text((60, 400), "An assignment statement creates a new variable:", fontsize=10)
        p.insert_text((60, 414), ">>> message = 'a greeting'", fontname="cour", fontsize=10)
        p.insert_text((60, 428), ">>> n = 17", fontname="cour", fontsize=10)
        units, _, _ = prepare_translation_units(doc, preserve_graphics_text=True)
        assert any("assignment statement" in b.text for b, _, _ in units)
        assert not any("message =" in b.text for b, _, _ in units)


def test_full_width_body_is_not_two_columns_because_of_indented_code():
    with fitz.open() as doc:
        p = doc.new_page(width=612, height=792)
        for y in range(100, 500, 24):
            p.insert_text(
                (54, y), "Scientific prose that spans the whole text area. " * 2, fontsize=9,
            )
        for y in range(550, 640, 18):
            p.insert_text((340, y), "Indented source code example.", fontname="cour", fontsize=9)
        assert detect_layout_profile(doc).columns == 1


def test_mixed_pages_keep_their_own_geometry():
    with fitz.open() as doc:
        doc.new_page().insert_text((60, 150), "A single-column cover", fontsize=24)
        for _ in range(3):
            two_columns(doc.new_page())
        assert detect_layout_profile(doc).columns == 2
        assert page_layout(doc[0], 0).columns == 1
        assert page_layout(doc[1], 1).columns == 2


def test_contents_page_number_column_is_source_owned():
    with book() as doc:
        page = doc[1]
        page.insert_text((60, 215), "3 Samples . . . . . . . . . . . . . . . . . .")
        page.insert_text((530, 215), "37")
        units, _, _ = prepare_translation_units(doc, preserve_graphics_text=True)
        assert any("Samples" in b.text for b, _, _ in units)
        assert not any(b.bbox[0] > page.rect.width * 0.75 and "37" in b.text
                       for b, _, _ in units if b.page_index == 1)


@pytest.mark.parametrize(
    "change", ["duplicate", "unknown_adapter", "unsafe_geometry", "unsafe_size"],
)
def test_invalid_registry_is_rejected(change):
    import copy

    registry = copy.deepcopy(load_layout_registry())
    if change == "duplicate":
        registry["families"].append(registry["families"][0])
    elif change == "unknown_adapter":
        registry["families"][0]["adapter"] = "external_plugin"
    elif change == "unsafe_geometry":
        registry["geometry"]["wide_weight_ratio"] = -1
    else:
        registry["policies"]["conference"]["caption_extra_height"] = 1000
    with pytest.raises(ValueError):
        validate_layout_registry(registry)


def test_front_matter_title_uses_empty_body_width_instead_of_tiny_font(tmp_path):
    from pdf_zh_translator.pdf_layout import translate_pdf

    class ChineseTranslator:
        def translate_batch(self, texts):
            return ["科学计算和数据结构导论" for _ in texts]

    source = tmp_path / "book.pdf"
    output = tmp_path / "translated.pdf"
    with book() as doc:
        # A real body text frame supplies the empty width the title may use.
        for page_index in (2, 3):
            for y in (330, 355, 380):
                doc[page_index].insert_text(
                    (60, y), "The scientific text spans the full academic book column. " * 2,
                    fontsize=10,
                )
        doc.save(source)
    translate_pdf(source, output, ChineseTranslator(), preserve_graphics_text=True)
    with fitz.open(output) as doc:
        spans = [s for b in doc[0].get_text("dict")["blocks"]
                 for line in b.get("lines", []) for s in line["spans"]]
        title = [s for s in spans if "科学" in s["text"]]
        assert title
        assert min(s["size"] for s in title) >= 23


def test_contents_keeps_source_weight_for_regular_and_bold_rows():
    with book() as doc:
        page = doc[1]
        page.insert_text((60, 220), "1.1 What is a program? . . . . . . . . . .", fontsize=10)
        page.insert_text(
            (60, 250), "1.2 Other section . . . . . . . . . .", fontname="hebo", fontsize=10,
        )
        units, _, _ = prepare_translation_units(doc, preserve_graphics_text=True)
        rows = [b for b, _, _ in units if b.page_index == 1]
        regular = next(b for b in rows if "What is a program?" in b.text)
        bold = next(b for b in rows if "Other section" in b.text)
        assert not regular.bold
        assert bold.bold
