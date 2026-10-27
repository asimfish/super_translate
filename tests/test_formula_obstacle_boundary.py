"""Only floats can admit paragraph growth beside them; equations stay fixed."""

from pathlib import Path

import fitz

from pdf_zh_translator.pdf_layout import (
    FontPack,
    TextBlock,
    _expand_multiline_block_bbox,
)


def test_fixed_equation_stops_multiline_growth_even_with_a_wide_free_side():
    block = TextBlock(
        page_index=0,
        bbox=(40.0, 90.0, 440.0, 116.0),
        text="A paragraph preceding a centered equation.",
        font_size=10.0,
        source_lines=2,
        color=(0.0, 0.0, 0.0),
    )
    font = fitz.Font("helv")
    pack = FontPack(regular=font, regular_file=Path(""), bold=None, bold_file=None)
    equation = (240.0, 120.0, 275.0, 160.0)
    expanded = _expand_multiline_block_bbox(
        block,
        "A translated sentence that must not cross the next equation. " * 4,
        [block],
        pack,
        10.0,
        0.5,
        792.0,
        obstacles=[equation],
        fixed_obstacles=[equation],
    )
    assert expanded.bbox[3] <= equation[1] - 2.0
    assert expanded.keepout_bboxes in (None, [])


def test_side_float_still_allows_multiline_growth():
    block = TextBlock(
        page_index=0,
        bbox=(40.0, 90.0, 440.0, 116.0),
        text="A paragraph beside a floating figure.",
        font_size=10.0,
        source_lines=2,
        color=(0.0, 0.0, 0.0),
    )
    font = fitz.Font("helv")
    pack = FontPack(regular=font, regular_file=Path(""), bold=None, bold_file=None)
    floating_figure = (240.0, 120.0, 275.0, 160.0)
    expanded = _expand_multiline_block_bbox(
        block,
        "A translated sentence that can wrap beside the floating figure. " * 4,
        [block],
        pack,
        10.0,
        0.5,
        792.0,
        obstacles=[floating_figure],
    )
    assert expanded.bbox[3] > floating_figure[1]
    assert floating_figure in expanded.keepout_bboxes


def test_fixed_equation_in_another_column_does_not_block_growth():
    block = TextBlock(
        page_index=0,
        bbox=(40.0, 90.0, 240.0, 116.0),
        text="A left-column paragraph.",
        font_size=10.0,
        source_lines=2,
        color=(0.0, 0.0, 0.0),
    )
    font = fitz.Font("helv")
    pack = FontPack(regular=font, regular_file=Path(""), bold=None, bold_file=None)
    equation = (300.0, 120.0, 420.0, 160.0)
    expanded = _expand_multiline_block_bbox(
        block,
        "A translated sentence that stays in its own text column. " * 4,
        [block],
        pack,
        10.0,
        0.5,
        792.0,
        obstacles=[equation],
        fixed_obstacles=[equation],
    )
    assert expanded.bbox[3] > equation[1]
    assert expanded.bbox[:3] == block.bbox[:3]
