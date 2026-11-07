"""Mixed theorem lead-ins need the same whitespace budget as ordinary prose."""

from pdf_zh_translator.pdf_layout import (
    TextBlock,
    _expand_multiline_block_bbox,
    build_font_pack,
    translated_text_fits,
)


def test_multiline_runin_statement_uses_free_gap_before_shrinking():
    block = TextBlock(
        0, (40, 100, 240, 126), "Proposition 1. A sufficiently detailed statement.",
        10, (0, 0, 0), block_type="run_in_heading", source_lines=3,
        starts_bold=True, bold_prefix=True, translated_bold_prefix_chars=5,
    )
    following = TextBlock(0, (40, 170, 240, 190), "Next paragraph", 10, (0, 0, 0))
    text = "命题一。" + "该结论对任意有界序列成立，并要求反馈项与观测代价共同收敛。" * 2
    fonts = build_font_pack(None, [])
    assert not translated_text_fits(block, text, fonts, 9.2, 9.2, 0.8)
    expanded = _expand_multiline_block_bbox(
        block, text, [block, following], fonts, 9.2, 0.8, 300,
    )
    assert expanded.bbox[3] > block.bbox[3]
    assert expanded.bbox[3] <= following.bbox[1] - 2
    assert translated_text_fits(expanded, text, fonts, 9.2, 9.2, 0.8)
