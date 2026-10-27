"""Following formula ink must remain guarded when prose grows into its top."""

from pdf_zh_translator.pdf_layout import TextBlock, _unresolved_formula_keepouts


def test_following_tall_formula_crossing_body_bottom_stays_guarded():
    equation = (20.0, 35.0, 60.0, 65.0)
    block = TextBlock(
        page_index=0, bbox=(0.0, 0.0, 120.0, 40.0),
        text="A paragraph before an equation.", font_size=10.0,
        color=(0.0, 0.0, 0.0), keepout_bboxes=[equation],
    )
    assert _unresolved_formula_keepouts(block) == [equation]


def test_previous_tall_extraction_hull_retains_the_existing_center_guard():
    block = TextBlock(
        page_index=0, bbox=(0.0, 0.0, 120.0, 40.0),
        text="A paragraph after an equation.", font_size=10.0,
        color=(0.0, 0.0, 0.0), keepout_bboxes=[(20.0, -25.0, 60.0, 5.0)],
    )
    assert _unresolved_formula_keepouts(block) == []


def test_distant_following_formula_is_not_a_body_obstacle():
    block = TextBlock(
        page_index=0, bbox=(0.0, 0.0, 120.0, 40.0),
        text="A paragraph in another reading group.", font_size=10.0,
        color=(0.0, 0.0, 0.0), keepout_bboxes=[(20.0, 70.0, 60.0, 100.0)],
    )
    assert _unresolved_formula_keepouts(block) == []


def test_formula_already_owned_by_the_body_does_not_become_a_second_keepout():
    equation = (20.0, 35.0, 60.0, 65.0)
    block = TextBlock(
        page_index=0, bbox=(0.0, 0.0, 120.0, 40.0),
        text="A paragraph with its own inline formula.", font_size=10.0,
        color=(0.0, 0.0, 0.0), keepout_bboxes=[equation],
        source_math_bboxes=[equation],
    )
    assert _unresolved_formula_keepouts(block) == []
