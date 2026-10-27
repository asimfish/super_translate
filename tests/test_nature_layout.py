"""Publisher geometry regressions, independent of a particular paper's cache."""

import fitz
import pytest

from pdf_zh_translator.layout_profiles import detect_layout_profile
from pdf_zh_translator.pdf_layout import prepare_translation_units, requested_translation_font_size


@pytest.fixture
def nature_document():
    document = fitz.open()
    page = document.new_page(width=595, height=790)
    page.insert_text((40, 50), "nature computational science", fontsize=18)
    page.insert_text((375, 101), "https://doi.org/10.1038/example", fontsize=8)
    page.insert_text((40, 101), "Article", fontsize=10)
    page.insert_text((40, 126), "A journal title about laboratory automation", fontsize=22)
    page.insert_text((40, 150), "and a digital twin for scientific discovery", fontsize=22)
    page.insert_textbox(
        fitz.Rect(215, 240, 555, 310),
        "Kourosh Darvish, Arjun Sohal, Abhijoy Mandal, Hatem Fakhruldeen, "
        "Nikola Radulov, Zhengxue Zhou, Joshua Choi, Sijie Han, Brayden Zhang",
        fontsize=9,
    )
    page.insert_textbox(
        fitz.Rect(215, 340, 555, 560),
        "Accelerated materials discovery is critical for addressing global challenges. " * 12,
        fontsize=10,
    )
    page.insert_textbox(
        fitz.Rect(40, 620, 290, 720),
        "Laboratory workflows depend on repeatable experiments and careful measurements. " * 5,
        fontsize=8.25,
    )
    yield document
    document.close()


def test_nature_front_page_separates_title_and_preserves_doi(nature_document):
    units, _, _ = prepare_translation_units(nature_document, preserve_graphics_text=True)
    title = [block for block, _, _ in units if block.block_type == "title"]
    assert len(title) == 1
    assert title[0].text.startswith("A journal title")
    assert "Article" not in title[0].text
    assert "doi.org" not in title[0].text
    assert not any("doi.org" in block.text for block, _, _ in units)


def test_nature_byline_is_preserved_below_long_title(nature_document):
    units, _, _ = prepare_translation_units(nature_document, preserve_graphics_text=True)
    assert not any("Kourosh Darvish" in block.text for block, _, _ in units)


def test_nature_profile_and_body_size(nature_document):
    assert detect_layout_profile(nature_document).name == "nature_portfolio"
    units, _, _ = prepare_translation_units(nature_document, preserve_graphics_text=True)
    body = next(block for block, _, _ in units if block.text.startswith("Laboratory workflows"))
    assert requested_translation_font_size(body, 5.0, 0.92) >= body.font_size


def test_reporting_summary_keeps_one_owner_per_checkbox_row(nature_document):
    page = nature_document.new_page(width=595, height=790)
    page.insert_text((20, 40), "nature portfolio | reporting summary", fontsize=9)
    page.insert_text((20, 130), "Reporting Summary", fontsize=22)
    for y in (272, 289, 306):
        for x in (20, 36):
            page.draw_rect(fitz.Rect(x, y, x + 10, y + 10), width=0.5)
    page.insert_text((51, 281), "The exact sample size for every experimental group", fontsize=9)
    page.insert_text((51, 298), "A statement on whether measurements were repeated", fontsize=9)
    page.insert_text(
        (51, 315), "The statistical tests used and whether they are one sided", fontsize=9
    )
    units, _, _ = prepare_translation_units(nature_document, preserve_graphics_text=True)
    rows = [block for block, _, _ in units if block.page_index == 1 and block.bbox[1] > 260]
    assert len(rows) == 3
    assert all(block.no_merge and block.preserve_position for block in rows)


def test_reporting_summary_wrapped_field_stays_with_its_checkbox(nature_document):
    page = nature_document.new_page(width=595, height=790)
    page.insert_text((20, 40), "nature portfolio | reporting summary", fontsize=9)
    for y in (371.8, 400.4):
        page.draw_rect(fitz.Rect(20, y, 30, y + 10), width=0.5)
    page.insert_text((51, 375), "A full description of the statistical parameters", fontsize=9)
    page.insert_text((51, 385.8), "AND associated estimates of uncertainty", fontsize=9)
    units, _, _ = prepare_translation_units(nature_document, preserve_graphics_text=True)
    fields = [block for block, _, _ in units if block.page_index == 1 and block.bbox[1] > 350]
    assert len(fields) == 1
    assert "AND associated" in fields[0].text


def test_reporting_summary_parallel_choices_stay_separate(nature_document):
    page = nature_document.new_page(width=595, height=790)
    page.insert_text((20, 40), "nature portfolio | reporting summary", fontsize=9)
    for x, text in ((30, "Life sciences"), (135, "Behavioural sciences"),
                    (270, "Environmental sciences")):
        page.draw_rect(fitz.Rect(x - 15, 274, x - 5, 284), width=0.5)
        page.insert_text((x, 282), text, fontsize=9)
    units, _, _ = prepare_translation_units(nature_document, preserve_graphics_text=True)
    choices = [block for block, _, _ in units if block.page_index == 1 and block.bbox[1] > 250]
    assert len(choices) == 3


def test_nature_title_keeps_source_hierarchy_after_role_promotion(nature_document):
    units, _, _ = prepare_translation_units(nature_document, preserve_graphics_text=True)
    title = next(block for block, _, _ in units if block.block_type == "title")
    assert requested_translation_font_size(title, 5.0, 0.92) <= title.font_size * 1.05


def test_form_field_gives_cjk_font_vertical_room(nature_document, tmp_path):
    from pdf_zh_translator.pdf_layout import translate_pdf

    page = nature_document.new_page(width=595, height=790)
    page.insert_text((20, 40), "nature portfolio | reporting summary", fontsize=9)
    page.draw_rect(fitz.Rect(560, 0, 582, 790), fill=(1, 0, 0))
    page.draw_rect(fitz.Rect(20, 272, 30, 282), width=0.5)
    page.insert_text((51, 281), "A statement about the statistical analysis", fontsize=9)
    source, output = tmp_path / "source.pdf", tmp_path / "output.pdf"
    nature_document.save(source)

    class Translator:
        def translate_batch(self, texts):
            return ["关于统计分析的说明。" if text.startswith("A statement")
                    else "实验室自动化与科学发现。" for text in texts]

    translate_pdf(source, output, Translator(), preserve_graphics_text=True)
    with fitz.open(output) as translated:
        spans = [span for block in translated[1].get_text("dict")["blocks"]
                 if block["type"] == 0 for line in block["lines"] for span in line["spans"]
                 if "统计分析" in span["text"]]
        assert spans
        assert min(span["size"] for span in spans) >= 8.5
        assert max(span["bbox"][2] for span in spans) < 550


def test_nature_byline_fragment_uses_source_block_owner(nature_document):
    from pdf_zh_translator.journal_layout import apply_nature_block_roles
    from pdf_zh_translator.pdf_layout import TextBlock

    fragment = TextBlock(
        page_index=0, bbox=(215, 250, 500, 260), text="Sijie Han, Brayden Zhang",
        font_size=9, color=(0, 0, 0),
    )
    apply_nature_block_roles([fragment], nature_document[0], 0)
    assert not fragment.should_translate
    assert fragment.block_type == "metadata"


def test_reporting_summary_formula_field_never_absorbs_next_response(nature_document):
    page = nature_document.new_page(width=595, height=790)
    page.insert_text((20, 40), "nature portfolio | reporting summary", fontsize=9)
    for y in (272, 289, 310):
        page.draw_rect(fitz.Rect(20, y, 30, y + 10), width=0.5)
    page.insert_text((51, 281), "The exact sample size (", fontsize=9)
    page.insert_text((138, 281), "n", fontname="tiit", fontsize=9)
    page.insert_text((143, 281), ") for each experimental group", fontsize=9)
    page.insert_text((51, 298), "A statement on whether measurements were repeated", fontsize=9)
    page.insert_text(
        (51, 319), "The statistical tests used and whether they are one sided", fontsize=9
    )
    units, _, _ = prepare_translation_units(nature_document, preserve_graphics_text=True)
    first = next(block for block, _, _ in units if block.text.startswith("The exact sample"))
    assert "A statement" not in first.text
    assert "statistical tests" not in first.text


def test_nature_printed_url_is_protected_as_a_canonical_address(nature_document):
    page = nature_document.new_page(width=595, height=790)
    page.insert_textbox(
        fitz.Rect(40, 280, 550, 350),
        "All data were generated using the code associated with this manuscript. "
        "The code will be released publicly at https:// github.com/example/code, "
        "and all software dependencies are documented in the repository.",
        fontsize=9,
    )
    units, _, _ = prepare_translation_units(nature_document, preserve_graphics_text=True)
    mappings = [mapping for block, _, mapping in units if block.page_index == 1]
    assert any("https://github.com/example/code" in value
               for mapping in mappings for value in mapping.values())


def test_reporting_summary_restores_lost_selectable_author_field():
    from pdf_zh_translator.journal_layout import restore_reporting_summary_metadata

    source = fitz.open()
    page = source.new_page(width=595, height=790)
    page.insert_text((20, 40), "nature portfolio | reporting summary", fontsize=9)
    page.insert_text((300, 70), "Corresponding author(s):", fontsize=9)
    page.insert_text((399, 70), "Kourosh Darvish", fontsize=8)
    translated = fitz.open(stream=source.tobytes(), filetype="pdf")
    translated[0].add_redact_annot(fitz.Rect(395, 55, 460, 75))
    translated[0].apply_redactions()
    assert "Kourosh Darvish" not in translated[0].get_text()
    restore_reporting_summary_metadata(translated[0], source[0])
    assert "Kourosh Darvish" in translated[0].get_text()
    translated.close()
    source.close()


def test_nature_comparison_matrix_retains_row_label_correspondence(nature_document):
    page = nature_document.new_page(width=595, height=790)
    page.draw_line((80, 180), (500, 180))
    for row in range(6):
        y = 190 + row * 14
        page.insert_text((80, y + 5), f"Feature comparison row number {row}", fontsize=5.5)
        for column in range(4):
            x = 260 + column * 60
            color = (0, 0.5, 0.5) if column % 2 else (0.8, 0, 0)
            page.draw_rect(fitz.Rect(x, y, x + 5, y + 5), color=None, fill=color)
    units, _, _ = prepare_translation_units(nature_document, preserve_graphics_text=True)
    assert not any("Feature comparison row" in block.text for block, _, _ in units)
    translated_units, _, _ = prepare_translation_units(
        nature_document, preserve_graphics_text=False,
    )
    assert any("Feature comparison row" in block.text for block, _, _ in translated_units)


def test_reporting_summary_without_checkboxes_keeps_labels_and_answers_separate(nature_document):
    page = nature_document.new_page(width=595, height=790)
    page.insert_text((20, 40), "nature portfolio | reporting summary", fontsize=9)
    page.insert_text((25, 80), "Seed stocks", fontsize=9)
    page.insert_text((120, 80), "Not applicable.", fontsize=9)
    units, _, _ = prepare_translation_units(nature_document, preserve_graphics_text=True)
    fields = [block for block, _, _ in units if block.page_index == 1 and block.bbox[1] > 60]
    assert len(fields) == 2
    answer = next(block for block in fields if "Not applicable" in block.text)
    assert answer.bbox[0] >= 115
