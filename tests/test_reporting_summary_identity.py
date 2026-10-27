"""A link to a disclosure form does not give it ownership of an article page."""

import fitz
import pytest

from pdf_zh_translator.journal_layout import (
    bound_reporting_summary_items,
    is_reporting_summary,
)
from pdf_zh_translator.pdf_layout import TextBlock, build_font_pack


@pytest.fixture
def page_pair():
    document = fitz.open()
    page = document.new_page(width=595, height=790)
    yield document, page
    document.close()


def article_summary_link(page):
    page.insert_text((40, 200), "Reporting summary", fontsize=9)
    page.insert_textbox(
        fitz.Rect(40, 210, 295, 250),
        "Further information on research design is available in the Nature Portfolio "
        "Reporting Summary linked to this article.",
        fontsize=9,
    )


def test_article_link_is_not_a_reporting_summary_form(page_pair):
    _, page = page_pair
    article_summary_link(page)
    assert not is_reporting_summary(page)


def test_independent_article_heading_is_not_sufficient_form_identity(page_pair):
    _, page = page_pair
    page.insert_text((40, 130), "Reporting Summary", fontsize=22)
    page.insert_text((40, 160), "Nature Portfolio provides linked research information.")
    assert not is_reporting_summary(page)


@pytest.mark.parametrize("position", [(40, 380), (310, 580)])
def test_masthead_quoted_inside_article_is_not_a_form(page_pair, position):
    _, page = page_pair
    page.insert_text(position, "nature portfolio | reporting summary", fontsize=9)
    page.insert_text((40, 200), "Figure: Reporting Summary", fontsize=20)
    assert not is_reporting_summary(page)


@pytest.mark.parametrize("brand", ["nature portfolio", "nature research"])
@pytest.mark.parametrize("rotated", [False, True])
def test_actual_form_masthead_owns_checkbox_rows(page_pair, brand, rotated):
    _, page = page_pair
    if rotated:
        page.insert_text((580, 220), f"{brand} | reporting summary", fontsize=9, rotate=90)
    else:
        page.insert_text((20, 40), f"{brand} | reporting summary", fontsize=9)
    page.draw_rect(fitz.Rect(20, 272, 30, 282), width=0.5)
    page.insert_text((51, 281), "A statement about the statistical analysis", fontsize=9)
    assert is_reporting_summary(page)


def test_current_form_title_and_introduction_work_without_sidebar(page_pair):
    _, page = page_pair
    page.insert_text((20, 130), "Reporting Summary", fontsize=22)
    page.insert_textbox(
        fitz.Rect(20, 150, 550, 200),
        "Nature Portfolio wishes to improve the reproducibility of the work that we "
        "publish. This form provides structure for consistency and transparency in reporting.",
        fontsize=9,
    )
    page.draw_rect(fitz.Rect(20, 272, 30, 282), width=0.5)
    assert is_reporting_summary(page)


def test_article_column_is_not_widened_by_linked_form(page_pair):
    _, page = page_pair
    article_summary_link(page)
    block = TextBlock(
        page_index=0,
        bbox=(40, 50, 295, 180),
        text="Methods and data analysis remain in the left article column.",
        font_size=9,
        color=(0, 0, 0),
        source_lines=8,
        source_line_bboxes=((40, 50, 295, 60),),
    )
    items = [(block, "方法与数据分析应保留在文章左栏。")]
    bounded = bound_reporting_summary_items(
        items, page, font_pack=build_font_pack(None, []), margin=0.8
    )
    assert bounded[0][0].bbox == block.bbox


def test_true_form_field_still_expands_beside_its_checkbox(page_pair):
    _, page = page_pair
    page.insert_text((20, 40), "nature portfolio | reporting summary", fontsize=9)
    page.draw_rect(fitz.Rect(20, 272, 30, 282), width=0.5)
    block = TextBlock(
        page_index=0,
        bbox=(51, 271, 250, 282),
        text="A statement about the statistical analysis",
        font_size=9,
        color=(0, 0, 0),
        preserve_position=True,
        source_line_bboxes=((51, 271, 250, 282),),
    )
    bounded = bound_reporting_summary_items(
        [(block, "关于统计分析的说明。")],
        page,
        font_pack=build_font_pack(None, []),
        margin=0.8,
    )
    assert bounded[0][0].bbox[0] == 51
    assert bounded[0][0].bbox[2] > 500
