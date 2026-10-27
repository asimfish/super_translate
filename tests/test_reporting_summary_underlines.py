"""Distinct hyperlinks at one baseline must not become a damaged table rule."""

import fitz

from pdf_zh_translator.page_inspector import (
    _page_line_art,
    _rule_is_text_underline,
    inspect_translation,
)


def _linked_summary(tmp_path):
    source_path = tmp_path / "summary.pdf"
    document = fitz.open()
    page = document.new_page(width=500, height=300)
    for y in (50, 145):
        page.draw_line((25, y), (475, y), color=(1, 0, 0), width=0.5)
    text_a = "Editorial Policies"
    text_b = "Editorial Checklist"
    size = 10
    width_a = fitz.get_text_length(text_a, fontname="helv", fontsize=size)
    width_b = fitz.get_text_length(text_b, fontname="helv", fontsize=size)
    x_a, x_b, baseline = 100, 100 + width_a + 13, 100
    page.insert_text((x_a, baseline), text_a, fontsize=size, color=(0, 0, 1))
    page.insert_text((x_a + width_a + 4, baseline), "&", fontsize=7, color=(0, 0, 0))
    page.insert_text((x_b, baseline), text_b, fontsize=size, color=(0, 0, 1))
    rule_y = baseline + 0.8
    for x, width in ((x_a, width_a), (x_b, width_b)):
        page.draw_line((x, rule_y), (x + width, rule_y), color=(0, 0, 1), width=0.36)
    document.save(source_path)
    document.close()
    return source_path, (rule_y, x_a, x_b + width_b)


def test_disjoint_link_underlines_are_recognized_after_rule_merging(tmp_path):
    source_path, rule = _linked_summary(tmp_path)
    with fitz.open(source_path) as document:
        rules, _ = _page_line_art(document[0])
        merged = next(item for item in rules if abs(item[0] - rule[0]) < 0.2)
        assert _rule_is_text_underline(document[0], merged)


def test_erased_link_underlines_do_not_fail_table_qa(tmp_path):
    source_path, rule = _linked_summary(tmp_path)
    translated_path = tmp_path / "translated.pdf"
    with fitz.open(source_path) as document:
        document[0].draw_rect(
            fitz.Rect(rule[1] - 1, rule[0] - 1.4, rule[2] + 1, rule[0] + 1.4),
            color=None, fill=(1, 1, 1), overlay=True,
        )
        document.save(translated_path)
    issues = inspect_translation(source_path, translated_path)
    assert not [issue for issue in issues if issue.code == "table_structure_mismatch"]


def test_continuous_table_rule_is_not_exempted_by_separate_text_spans(tmp_path):
    source_path, rule = _linked_summary(tmp_path)
    with fitz.open(source_path) as document:
        page = document[0]
        page.draw_line((rule[1], rule[0]), (rule[2], rule[0]), width=0.5)
        assert not _rule_is_text_underline(page, rule)


def test_link_and_unlabelled_rule_are_not_all_text_underlines(tmp_path):
    source_path, rule = _linked_summary(tmp_path)
    with fitz.open(source_path) as document:
        page = document[0]
        page.draw_line((350, rule[0]), (440, rule[0]), width=0.5)
        assert not _rule_is_text_underline(page, (rule[0], rule[1], 440))
