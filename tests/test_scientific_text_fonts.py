"""STIX text faces must translate; mathematical fonts/glyphs remain source-owned."""

import pytest

from pdf_zh_translator.pdf_layout import MATH_FONT_RE, is_math_span


@pytest.mark.parametrize("font", ["STIXGeneral-Regular", "STIXGeneral-Bold", "STIXTwoText-Regular"])
def test_stix_text_font_does_not_protect_english_prose(font):
    assert not MATH_FONT_RE.search(font)
    assert not is_math_span(
        font, 4, "The chemical reaction rate increases with temperature.", 10, 10,
    )
    assert not is_math_span(font, 4, "Introduction", 14, 14)


@pytest.mark.parametrize(
    "font", ["STIXTwoMath-Regular", "STIXMath-Regular", "STIXSizeOneSym-Regular"],
)
def test_dedicated_stix_math_fonts_are_still_protected(font):
    assert is_math_span(font, 4, "x = y", 10, 10)


@pytest.mark.parametrize("text", ["α", "∑", "x = 2", "a+b", "≤"])
def test_stix_general_math_glyphs_are_still_protected(text):
    assert is_math_span("STIXGeneral-Regular", 4, text, 10, 10)


def test_single_letter_italic_variables_and_superscripts_are_preserved():
    assert is_math_span("STIXGeneral-Italic", 6, "x", 10, 10)
    assert is_math_span("STIXGeneral-Regular", 5, "2", 6, 10)
