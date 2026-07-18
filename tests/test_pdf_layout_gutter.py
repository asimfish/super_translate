"""Tests for gutter line-number detection vs formula sub/superscripts."""

import unittest

from pdf_zh_translator.pdf_layout import (
    _LineRec,
    _normalize_font_name,
    _span_is_isolated,
    is_line_number_span,
    line_is_prose,
    parse_block_lines,
)


def make_span(text, x0, y0, x1, y1, size=7.0):
    return {"text": text, "bbox": (x0, y0, x1, y1), "size": size}


class IsLineNumberSpanTests(unittest.TestCase):
    def test_isolated_small_digit_is_line_number(self):
        self.assertTrue(is_line_number_span("129", 6.0, 10.0, isolated=True))

    def test_single_isolated_digit_can_be_formula_numerator(self):
        self.assertFalse(is_line_number_span("1", 6.0, 10.0, isolated=True))

    def test_glued_digit_is_formula_subscript(self):
        # The 0 in X_0 set in CMR7: same size as a gutter number but glued
        # to its base glyphs, so it must never be erased.
        self.assertFalse(is_line_number_span("0", 7.0, 10.0, isolated=False))

    def test_non_digit_never_line_number(self):
        self.assertFalse(is_line_number_span("ab", 6.0, 10.0, isolated=True))

    def test_body_size_digit_not_line_number(self):
        self.assertFalse(is_line_number_span("42", 10.0, 10.0, isolated=True))


class SpanIsIsolatedTests(unittest.TestCase):
    def test_subscript_touching_base_is_not_isolated(self):
        base = make_span("(X", 244.6, 279.9, 252.9, 291.0, size=10.0)
        subscript = make_span("0", 252.9, 283.7, 257.4, 291.0, size=7.0)
        close_paren = make_span(")", 257.4, 279.9, 261.2, 291.0, size=10.0)
        spans = [base, subscript, close_paren]
        self.assertFalse(_span_is_isolated(subscript, spans))

    def test_lone_gutter_number_is_isolated(self):
        gutter = make_span("129", 88.1, 338.2, 98.0, 345.0, size=6.0)
        self.assertTrue(_span_is_isolated(gutter, [gutter]))

    def test_distant_spans_keep_isolation(self):
        gutter = make_span("129", 88.1, 338.2, 98.0, 345.0, size=6.0)
        body = make_span("text", 107.6, 335.3, 200.0, 346.0, size=10.0)
        self.assertTrue(_span_is_isolated(gutter, [gutter, body]))

    def test_different_line_band_does_not_break_isolation(self):
        gutter = make_span("129", 88.1, 338.2, 98.0, 345.0, size=6.0)
        above = make_span("99", 88.1, 300.0, 98.0, 307.0, size=6.0)
        self.assertTrue(_span_is_isolated(gutter, [gutter, above]))


class LineIsProseTests(unittest.TestCase):
    def _line(self, text):
        return _LineRec(text=text, bbox=(0.0, 0.0, 100.0, 10.0), spans=[])

    def test_short_connective_sentence_is_prose(self):
        # Three real words: previously missed (threshold was 4) and left
        # untranslated inside equation zones.
        self.assertTrue(line_is_prose(self._line("the forward equation is")))

    def test_capitalised_sentence_is_prose(self):
        self.assertTrue(line_is_prose(self._line("Let it be a safety potential. We solve")))

    def test_math_function_words_are_not_prose(self):
        self.assertFalse(line_is_prose(self._line("min max exp log")))

    def test_symbol_line_is_not_prose(self):
        self.assertFalse(line_is_prose(self._line("KL(P||Q) + nD(u, v)")))


class NormalizeFontNameTests(unittest.TestCase):
    def test_display_name_matches_postscript_name(self):
        # fitz.Font.name vs PDF basefont spelling of the same face.
        self.assertEqual(
            _normalize_font_name("Hiragino Sans GB W6"),
            _normalize_font_name("HiraginoSansGB-W6"),
        )

    def test_subset_prefix_is_stripped(self):
        self.assertEqual(
            _normalize_font_name("AAAAAA+ArialUnicodeMS"),
            _normalize_font_name("Arial Unicode MS"),
        )


def test_parse_block_lines_preserves_interior_chart_ticks():
    raw_block = {
        "type": 0,
        "bbox": (105.0, 124.0, 174.0, 133.0),
        "lines": [
            {
                "bbox": (105.0, 124.0, 174.0, 133.0),
                "spans": [
                    make_span("20", 105.0, 124.0, 111.0, 133.0, size=6.0),
                    make_span("40", 125.0, 124.0, 131.0, 133.0, size=6.0),
                    make_span("100", 150.0, 124.0, 160.0, 133.0, size=6.0),
                ],
            }
        ],
    }

    record, dropped = parse_block_lines(raw_block, page_width=612.0)

    assert record is not None
    assert dropped == []
    assert {line.text for line in record.lines} == {"20", "40", "100"}
