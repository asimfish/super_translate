"""A wrapped paragraph owns its native same-row bold lead-in."""

from dataclasses import replace

import pytest

from pdf_zh_translator.pdf_layout import TextBlock, _combine_inline_style_translation_items


def native_pair():
    heading_box = (108.0, 570.0, 193.0, 580.0)
    lines = ((203.0, 570.0, 504.0, 580.0), (108.0, 581.0, 504.0, 591.0))
    heading = TextBlock(
        0, heading_box, "On-policy exclusion.", 9.9, (0, 0, 0),
        bold=True, block_type="heading", source_lines=1,
        source_line_bboxes=(heading_box,), source_prose_bboxes=(heading_box,),
    )
    body = TextBlock(
        0, (108.0, 570.0, 504.0, 613.0), "With margin 0.8, the controllers are unsafe.",
        9.9, (0, 0, 0), block_type="formula_prose", source_lines=4,
        source_line_bboxes=lines, source_prose_bboxes=lines,
    )
    return heading, body


def test_same_row_heading_and_wrapped_body_form_one_styled_flow():
    heading, body = native_pair()
    result = _combine_inline_style_translation_items(
        [(heading, "同策略排除。"), (body, "控制器仍不安全。")]
    )
    assert len(result) == 1
    merged, text = result[0]
    assert text == "同策略排除。 控制器仍不安全。"
    assert merged.bold_prefix and not merged.bold
    assert merged.source_style_runs[0].role == "heading_prefix"
    assert merged.source_style_runs[1].role == "body"


@pytest.mark.parametrize("case", ["next_paragraph", "other_column", "other_page", "large_gap"])
def test_separate_source_owners_do_not_merge(case):
    heading, body = native_pair()
    if case == "next_paragraph":
        body = replace(body, source_line_bboxes=((108, 592, 504, 602),))
    elif case == "other_column":
        body = replace(body, bbox=(203, 570, 504, 613))
    elif case == "other_page":
        body = replace(body, page_index=1)
    else:
        body = replace(body, source_line_bboxes=((260, 570, 504, 580), (108, 581, 504, 591)))
    assert len(_combine_inline_style_translation_items([(heading, "小标题"), (body, "正文")])) == 2
