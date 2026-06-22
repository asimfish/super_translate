"""Tests for conservative native-layout preservation rules."""

import unittest

import fitz

from pdf_zh_translator.pdf_layout import (
    SENTINEL_CLOSE,
    SENTINEL_OPEN,
    TextBlock,
    _LineRec,
    _RawBlockRec,
    clean_translation,
    is_math_span,
    math_heavy_block,
    merge_paragraph_blocks,
    prepare_translation_units,
    segments_from_record,
    should_preserve_original_block,
)


class PreserveOriginalBlockTests(unittest.TestCase):
    def test_translates_figure_caption(self):
        block = TextBlock(
            page_index=0,
            bbox=(10.0, 10.0, 250.0, 30.0),
            text="Figure 1: Overview of the workflow.",
            font_size=10.0,
            color=(0.0, 0.0, 0.0),
        )

        self.assertFalse(should_preserve_original_block(block, []))

    def test_translates_nowrap_prose_outside_graphic_regions(self):
        block = TextBlock(
            page_index=0,
            bbox=(10.0, 10.0, 250.0, 30.0),
            text="RankRefine++ is the closest prior work to our proposed method.",
            font_size=10.0,
            color=(0.0, 0.0, 0.0),
            nowrap=True,
        )

        self.assertFalse(should_preserve_original_block(block, []))

    def test_skips_math_heavy_short_block(self):
        block = TextBlock(
            page_index=0,
            bbox=(10.0, 10.0, 250.0, 30.0),
            text=f"{SENTINEL_OPEN}x^2 + y^2{SENTINEL_CLOSE} objective",
            font_size=10.0,
            color=(0.0, 0.0, 0.0),
            source_lines=1,
        )

        self.assertTrue(should_preserve_original_block(block, []))

    def test_preserves_block_crossing_graphic_region(self):
        block = TextBlock(
            page_index=0,
            bbox=(10.0, 10.0, 260.0, 120.0),
            text="A long text block whose rectangular bbox crosses a figure region.",
            font_size=10.0,
            color=(0.0, 0.0, 0.0),
            source_lines=5,
        )

        self.assertTrue(should_preserve_original_block(block, [(170.0, 20.0, 280.0, 100.0)]))


def _span(text, bbox, size=10.0, font="NimbusRomNo9L-Regu", flags=4):
    return {"text": text, "bbox": bbox, "size": size, "font": font, "flags": flags}


class FormulaTailProseTests(unittest.TestCase):
    def test_clean_translation_collapses_mixed_formula_parentheses(self):
        self.assertEqual(clean_translation("（U e ij≈0).）"), "（U e ij≈0）")

    def test_superscript_flag_does_not_protect_common_prose_word(self):
        self.assertFalse(
            is_math_span(
                "NimbusRomNo9L-Regu",
                flags=5,
                text="is",
                size=10.0,
                line_max_size=10.0,
            )
        )

    def test_splits_where_clause_without_redacting_fraction(self):
        prefix = _LineRec(
            text=f"we call lateral propagation and set {SENTINEL_OPEN}λ{SENTINEL_CLOSE} =",
            bbox=(108.0, 553.4, 434.4, 563.7),
            spans=[
                _span("we call lateral propagation and set", (108.0, 553.4, 415.1, 563.7)),
                _span(" λ", (415.1, 553.4, 423.5, 563.4), font="CMMI10", flags=6),
                _span(" =", (423.5, 553.4, 434.4, 563.4), font="CMR10"),
            ],
        )
        numerator = _LineRec(
            text="1",
            bbox=(443.1, 551.9, 447.1, 558.8),
            spans=[_span("1", (443.1, 551.9, 447.1, 558.8), size=7.0)],
        )
        where_line = _LineRec(
            text=(
                f"2{SENTINEL_OPEN}σ{SENTINEL_CLOSE}"
                f"{SENTINEL_OPEN}²{SENTINEL_CLOSE}re where"
                f"{SENTINEL_OPEN}σ{SENTINEL_CLOSE}{SENTINEL_OPEN}²{SENTINEL_CLOSE}"
            ),
            bbox=(438.6, 552.2, 493.0, 567.9),
            spans=[
                _span("2", (438.6, 559.2, 442.6, 566.2), size=7.0),
                _span("σ", (442.6, 559.2, 447.2, 566.2), size=7.0, font="CMMI7", flags=6),
                _span("2", (447.5, 558.8, 450.9, 563.8), size=5.0),
                _span("re", (447.2, 562.9, 451.1, 567.9), size=5.0),
                _span(" ", (451.1, 559.0, 455.5, 569.1)),
                _span("where", (455.5, 553.6, 480.3, 563.7), flags=5),
                _span(" σ", (480.3, 553.4, 488.6, 563.4), font="CMMI10", flags=7),
                _span("2", (489.0, 552.2, 493.0, 559.1), size=7.0),
            ],
        )
        continuation = _LineRec(
            text="re is",
            bbox=(488.6, 553.6, 504.0, 566.1),
            spans=[
                _span("re", (488.6, 558.4, 494.1, 565.4), size=7.0),
                _span(" ", (494.1, 556.1, 497.2, 566.1)),
                _span("is", (497.2, 553.6, 504.0, 563.7), flags=5),
            ],
        )
        prose = _LineRec(
            text="the associated point-wise uncertainty of the regressor",
            bbox=(108.0, 566.8, 504.2, 576.9),
            spans=[
                _span(
                    "the associated point-wise uncertainty of the regressor",
                    (108.0, 566.8, 504.2, 576.9),
                )
            ],
        )
        record = _RawBlockRec(lines=[prefix, numerator, where_line, continuation, prose])

        segments = segments_from_record(0, record)

        self.assertEqual(len(segments), 2)
        self.assertIn("where", segments[1].text)
        self.assertIn("associated point-wise uncertainty", segments[1].text)
        self.assertEqual(segments[1].bbox, prose.bbox)
        self.assertIsNotNone(segments[1].redact_bboxes)
        assert segments[1].redact_bboxes is not None
        self.assertGreaterEqual(segments[1].redact_bboxes[0][0], 455.0)
        self.assertEqual(segments[1].redact_bboxes[-1], prose.bbox)

    def test_math_rich_sentence_with_prose_is_not_preserved(self):
        block = TextBlock(
            page_index=0,
            bbox=(108.0, 297.5, 497.0, 311.4),
            text=(
                f"{SENTINEL_OPEN}w_ij{SENTINEL_CLOSE} is unknown before querying, "
                f"we use {SENTINEL_OPEN}E[w_ij]=U_ij/τ²{SENTINEL_CLOSE}. "
                "The expected log-determinant is then,"
            ),
            font_size=10.0,
            color=(0.0, 0.0, 0.0),
        )

        self.assertFalse(math_heavy_block(block))
        self.assertFalse(should_preserve_original_block(block, []))

    def test_equation_record_keeps_inline_tail_with_prose(self):
        first = _LineRec(
            text=(
                f"This means a query is uninformative if the outcome"
                f" is near-certain ({SENTINEL_OPEN}U{SENTINEL_CLOSE}"
            ),
            bbox=(108.0, 362.2, 426.3, 373.7),
            spans=[
                _span(
                    "This means a query is uninformative if the outcome is near-certain (",
                    (108.0, 362.2, 411.9, 373.7),
                ),
                _span("U", (411.9, 363.5, 418.7, 373.5), font="CMMI10", flags=6),
            ],
        )
        tail = _LineRec(
            text=f"{SENTINEL_OPEN}ij ≈ 0{SENTINEL_CLOSE}) or the pair is",
            bbox=(418.7, 363.4, 504.0, 375.9),
            spans=[
                _span("ij", (418.7, 368.4, 424.8, 375.4), size=7.0, font="CMMI7", flags=6),
                _span("≈", (430.0, 363.4, 437.8, 373.3), font="CMSY10", flags=7),
                _span("0", (441.1, 363.5, 446.1, 373.5), flags=5),
                _span(") or the pair is", (446.1, 363.7, 504.0, 373.7), flags=5),
            ],
        )
        second = _LineRec(
            text=f"already well-constrained by the graph ({SENTINEL_OPEN}U_e{SENTINEL_CLOSE})",
            bbox=(108.0, 373.1, 302.2, 386.8),
            spans=[
                _span(
                    "already well-constrained by the graph (",
                    (108.0, 373.1, 264.3, 384.6),
                ),
                _span("U", (264.3, 374.4, 271.1, 384.4), font="CMMI10", flags=6),
                _span("e", (272.2, 373.1, 276.0, 380.1), size=7.0, font="CMMI7", flags=7),
                _span(")", (296.4, 374.6, 302.2, 384.6), flags=5),
            ],
        )
        record = _RawBlockRec(lines=[first, tail, second])

        segments = segments_from_record(0, record, equation_record=True)

        self.assertEqual(len(segments), 1)
        self.assertIn("or the pair is", segments[0].text)
        self.assertIn("already well-constrained", segments[0].text)
        self.assertEqual(segments[0].redact_bboxes, [segments[0].bbox])


class PreserveGraphicsTextTests(unittest.TestCase):
    def test_merge_stops_before_crossing_graphic_region(self):
        first = TextBlock(
            page_index=0,
            bbox=(10.0, 10.0, 90.0, 20.0),
            text="This is the first line",
            font_size=10.0,
            color=(0.0, 0.0, 0.0),
        )
        second = TextBlock(
            page_index=0,
            bbox=(10.0, 24.0, 200.0, 34.0),
            text="This is the second line",
            font_size=10.0,
            color=(0.0, 0.0, 0.0),
        )

        self.assertEqual(len(merge_paragraph_blocks([first, second])), 1)
        self.assertEqual(
            len(
                merge_paragraph_blocks(
                    [first, second],
                    graphic_regions_by_page={0: [(100.0, 12.0, 180.0, 28.0)]},
                )
            ),
            2,
        )

    def test_prepare_units_skips_text_inside_drawing_region(self):
        document = fitz.open()
        page = document.new_page(width=300, height=300)
        page.draw_rect(fitz.Rect(40, 40, 180, 110))
        page.insert_text((70, 80), "Figure Label", fontsize=8)
        page.insert_text((40, 170), "This body sentence should be translated.", fontsize=11)

        normal_units, _, _ = prepare_translation_units(document)
        preserved_units, _, _ = prepare_translation_units(document, preserve_graphics_text=True)
        normal_sources = [source for _, source, _ in normal_units]
        preserved_sources = [source for _, source, _ in preserved_units]

        self.assertTrue(any("Figure Label" in source for source in normal_sources))
        self.assertFalse(any("Figure Label" in source for source in preserved_sources))
        self.assertTrue(any("body sentence" in source for source in preserved_sources))


if __name__ == "__main__":
    unittest.main()
