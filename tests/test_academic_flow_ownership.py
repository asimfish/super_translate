"""A no-op theorem merger must retain the extraction's inline-math decision."""

from pdf_zh_translator.pdf_layout import (
    TextBlock,
    _combine_academic_formula_statement_translation_items,
)


def test_complete_statement_retains_its_inline_formula_flow():
    statement = TextBlock(
        0, (40, 100, 300, 140), "Proposition 3. A complete conditional statement.",
        10, (0, 0, 0), block_type="run_in_heading", source_lines=4,
        flow_inline_math=True, formula_anchors=((120, 110, 150, 120),),
    )
    next_paragraph = TextBlock(0, (40, 155, 300, 180), "Separate paragraph.", 10, (0, 0, 0))
    result = _combine_academic_formula_statement_translation_items(
        [(statement, "命题三。完整的条件命题。"), (next_paragraph, "独立的段落。")]
    )
    assert len(result) == 2
    assert result[0][0].flow_inline_math
    assert result[0][0].formula_anchors == statement.formula_anchors
