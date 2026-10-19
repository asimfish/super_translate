"""Regression for detached Z=1 / U×X in a translated proof paragraph."""

import json
from dataclasses import replace
from pathlib import Path

from pdf_zh_translator.pdf_layout import TextBlock, _uses_fixed_source_math


def paragraph():
    return TextBlock(**json.loads(
        (Path(__file__).parent / "fixtures/gflownet_inline_assumption.json").read_text()
    ))


def test_compact_math_in_academic_prose_moves_with_the_translated_sentence():
    block = paragraph()
    assert "Assumption 3.1 implies" in block.text
    assert not _uses_fixed_source_math(block)


def test_formula_without_source_prose_geometry_remains_fixed():
    assert _uses_fixed_source_math(replace(paragraph(), source_prose_bboxes=()))


def test_inset_continuation_keeps_its_place_beside_unowned_math():
    block = paragraph()
    rows = list(block.source_line_bboxes)
    x0, y0, x1, y1 = rows[1]
    rows[1] = (x0 + 40, y0, x1, y1)
    assert _uses_fixed_source_math(replace(block, source_line_bboxes=tuple(rows)))


def test_formula_on_a_separate_display_row_remains_fixed():
    block = paragraph()
    prose = tuple((p[0], p[1] - 30, p[2], p[3] - 30) for p in block.source_prose_bboxes)
    assert _uses_fixed_source_math(replace(block, source_prose_bboxes=prose))


def test_tall_formula_remains_fixed_even_with_an_adjacent_prose_label():
    block = paragraph()
    boxes = list(block.source_math_bboxes)
    x0, y0, x1, y1 = boxes[0]
    boxes[0] = (x0, y0 - 20, x1, y1 + 20)
    assert _uses_fixed_source_math(replace(block, source_math_bboxes=tuple(boxes)))


def test_wide_equation_is_not_reflowed_as_an_inline_symbol():
    block = paragraph()
    boxes = list(block.source_math_bboxes)
    x0, y0, _, y1 = boxes[0]
    boxes[0] = (x0, y0, x0 + 180, y1)
    assert _uses_fixed_source_math(replace(block, source_math_bboxes=tuple(boxes)))


def test_compound_math_stays_fixed_even_in_a_wide_paragraph():
    block = paragraph()
    boxes = list(block.source_math_bboxes)
    x0, y0, _, y1 = boxes[0]
    boxes[0] = (x0, y0, x0 + 7 * block.font_size, y1)
    left, top, _, bottom = block.bbox
    assert _uses_fixed_source_math(replace(
        block, bbox=(left, top, left + 600, bottom), source_math_bboxes=tuple(boxes)
    ))
