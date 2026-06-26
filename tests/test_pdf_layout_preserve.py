"""Tests for conservative native-layout preservation rules."""

import unittest
from pathlib import Path

import fitz

from pdf_zh_translator.pdf_layout import (
    SENTINEL_CLOSE,
    SENTINEL_OPEN,
    FontPack,
    TextBlock,
    _LineRec,
    _RawBlockRec,
    clean_translation,
    fragmented_prose_warnings_from_units,
    insert_translated_text,
    is_math_span,
    math_heavy_block,
    merge_paragraph_blocks,
    prepare_translation_units,
    record_is_table,
    segments_from_record,
    should_preserve_original_block,
    verify_translation,
    verify_translation_issues,
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

    def test_caption_over_graphic_region_is_still_translated(self):
        block = TextBlock(
            page_index=0,
            bbox=(10.0, 100.0, 260.0, 124.0),
            text="Figure 2: Accuracy improves with additional supervision.",
            font_size=10.0,
            color=(0.0, 0.0, 0.0),
            block_type="caption",
        )

        self.assertFalse(should_preserve_original_block(block, [(0.0, 40.0, 280.0, 140.0)]))


def _span(text, bbox, size=10.0, font="NimbusRomNo9L-Regu", flags=4):
    return {"text": text, "bbox": bbox, "size": size, "font": font, "flags": flags}


def _line(text, bbox, is_cell=False):
    return _LineRec(text=text, bbox=bbox, spans=[_span(text, bbox)], is_cell=is_cell)


class TableDetectionTests(unittest.TestCase):
    def test_same_baseline_prose_fragments_are_not_table(self):
        record = _RawBlockRec(
            lines=[
                _line("A of Fig. 1.", (50.0, 74.0, 102.0, 84.0)),
                _line("It remains open to building an end-to-end", (110.0, 74.0, 286.0, 84.0)),
                _line(
                    "SGG model in a general open-vocabulary setting. More-",
                    (50.0, 86.0, 286.0, 96.0),
                ),
                _line(
                    "over, those methods often employ an additional pre-training",
                    (50.0, 98.0, 286.0, 108.0),
                ),
                _line(
                    "framework consisting of three main components.",
                    (50.0, 400.0, 256.0, 410.0),
                ),
                _line("First,", (266.0, 400.0, 286.0, 410.0)),
                _line("we introduce scene graph prompts.", (50.0, 412.0, 286.0, 422.0)),
            ]
        )

        self.assertFalse(record_is_table(record))

        segments = segments_from_record(0, record)
        self.assertEqual(len(segments), 1)
        self.assertFalse(segments[0].nowrap)
        self.assertIn("Moreover", segments[0].text)
        self.assertIn("components. First, we introduce", segments[0].text)

    def test_repeated_wide_same_row_gaps_are_table(self):
        record = _RawBlockRec(
            lines=[
                _line("Metric", (50.0, 100.0, 90.0, 110.0)),
                _line("Score", (180.0, 100.0, 220.0, 110.0)),
                _line("Accuracy", (50.0, 116.0, 102.0, 126.0)),
                _line("91.2", (180.0, 116.0, 205.0, 126.0)),
            ]
        )

        self.assertTrue(record_is_table(record))

        segments = segments_from_record(0, record)
        self.assertTrue(all(segment.nowrap for segment in segments))


class FragmentedProseWarningTests(unittest.TestCase):
    def test_warns_when_many_body_units_are_fixed_width_fragments(self):
        units = [
            (
                TextBlock(
                    page_index=0,
                    bbox=(50.0, 80.0 + index * 12.0, 286.0, 90.0 + index * 12.0),
                    text="This body line was incorrectly isolated from a paragraph.",
                    font_size=10.0,
                    color=(0.0, 0.0, 0.0),
                    nowrap=True,
                    source_lines=1,
                ),
                "This body line was incorrectly isolated from a paragraph.",
                {},
            )
            for index in range(6)
        ]

        warnings = fragmented_prose_warnings_from_units(units)

        self.assertEqual(len(warnings), 1)
        self.assertIn("Page 1", warnings[0])
        self.assertIn("fixed-width fragments", warnings[0])


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

        self.assertFalse(any("Figure Label" in source for source in normal_sources))
        self.assertFalse(any("Figure Label" in source for source in preserved_sources))
        self.assertTrue(any("body sentence" in source for source in normal_sources))
        self.assertTrue(any("body sentence" in source for source in preserved_sources))

    def test_insert_translated_text_renders_caption_without_name_error(self):
        document = fitz.open()
        page = document.new_page(width=300, height=180)
        font = fitz.Font("helv")
        font_pack = FontPack(
            regular=font,
            regular_file=Path(""),
            bold=font,
            bold_file=Path(""),
            regular_alias="helv",
            bold_alias="helv",
        )
        block = TextBlock(
            page_index=0,
            bbox=(30.0, 40.0, 250.0, 90.0),
            text="Figure 1: Small caption.",
            font_size=10.0,
            color=(0.0, 0.0, 0.0),
            block_type="caption",
        )

        inserted = insert_translated_text(
            page=page,
            block=block,
            text="Short translated caption.",
            font_pack=font_pack,
            font_size=10.0,
            min_font_size=5.0,
            margin=0.8,
        )

        self.assertTrue(inserted)
        self.assertIn("Short translated caption", page.get_text("text"))


class TestClassifyBlocks(unittest.TestCase):
    """Test block classification into semantic types."""

    def _make_block(self, text, bbox=(100, 100, 400, 120), bold=False, page=0):
        return TextBlock(
            page_index=page,
            bbox=bbox,
            text=text,
            font_size=11.0,
            color=(0, 0, 0),
            bold=bold,
        )

    def test_caption_detection(self):
        """Figure/Table captions are classified correctly."""
        from pdf_zh_translator.pdf_layout import classify_blocks

        block = self._make_block("Figure 1: Overview of the system architecture.")
        classify_blocks([block], 0, 792, [])
        self.assertEqual(block.block_type, "caption")
        self.assertTrue(block.preserve_position)
        self.assertTrue(block.should_translate)

    def test_chinese_caption_detection(self):
        """Chinese captions are detected."""
        from pdf_zh_translator.pdf_layout import classify_blocks

        block = self._make_block("图1 系统架构总览")
        classify_blocks([block], 0, 792, [])
        self.assertEqual(block.block_type, "caption")

    def test_heading_detection(self):
        """Bold numbered text is classified as heading."""
        from pdf_zh_translator.pdf_layout import classify_blocks

        block = self._make_block("1 Introduction", bold=True)
        classify_blocks([block], 0, 792, [])
        self.assertEqual(block.block_type, "heading")

    def test_heading_requires_bold(self):
        """Non-bold numbered text is not a heading."""
        from pdf_zh_translator.pdf_layout import classify_blocks

        block = self._make_block("1 Introduction", bold=False)
        classify_blocks([block], 0, 792, [])
        self.assertEqual(block.block_type, "body")

    def test_footer_detection(self):
        """Text near page bottom is classified as footer."""
        from pdf_zh_translator.pdf_layout import classify_blocks

        block = self._make_block("15", bbox=(250, 750, 280, 765))
        classify_blocks([block], 0, 792, [])
        self.assertEqual(block.block_type, "footer")
        self.assertFalse(block.should_translate)

    def test_figure_label_in_image_zone(self):
        """Short text inside image zone is classified as figure_label."""
        from pdf_zh_translator.pdf_layout import classify_blocks

        block = self._make_block("Time", bbox=(150, 200, 180, 215))
        image_zones = [(100, 150, 300, 300)]  # covers the block
        classify_blocks([block], 0, 792, image_zones)
        self.assertEqual(block.block_type, "figure_label")
        self.assertFalse(block.should_translate)
        self.assertTrue(block.preserve_position)

    def test_body_text_default(self):
        """Regular text is classified as body."""
        from pdf_zh_translator.pdf_layout import classify_blocks

        block = self._make_block("This is a regular paragraph of body text.")
        classify_blocks([block], 0, 792, [])
        self.assertEqual(block.block_type, "body")
        self.assertTrue(block.should_translate)


class TestDetectColumns(unittest.TestCase):
    """Test column layout detection."""

    def test_single_column(self):
        """Blocks at same x0 detect single column."""
        from pdf_zh_translator.pdf_layout import detect_columns

        blocks = [
            TextBlock(0, (91, 100, 504, 120), "a" * 50, 11.0, (0, 0, 0)),
            TextBlock(0, (91, 130, 504, 150), "b" * 50, 11.0, (0, 0, 0)),
        ]
        columns = detect_columns(blocks)
        self.assertEqual(len(columns), 1)
        self.assertAlmostEqual(columns[0][0], 91.0)

    def test_two_columns(self):
        """Blocks at x0=54 and x0=337 detect two columns."""
        from pdf_zh_translator.pdf_layout import detect_columns

        blocks = [
            TextBlock(0, (54, 100, 282, 120), "a" * 50, 11.0, (0, 0, 0)),
            TextBlock(0, (54, 130, 282, 150), "b" * 50, 11.0, (0, 0, 0)),
            TextBlock(0, (337, 100, 565, 120), "c" * 50, 11.0, (0, 0, 0)),
            TextBlock(0, (337, 130, 565, 150), "d" * 50, 11.0, (0, 0, 0)),
        ]
        columns = detect_columns(blocks)
        self.assertEqual(len(columns), 2)

    def test_empty_blocks(self):
        """Empty block list returns empty columns."""
        from pdf_zh_translator.pdf_layout import detect_columns

        self.assertEqual(detect_columns([]), [])


class TestDetectImageZones(unittest.TestCase):
    def test_vector_drawing_region_is_detected(self):
        from pdf_zh_translator.pdf_layout import detect_image_zones

        document = fitz.open()
        page = document.new_page(width=300, height=300)
        page.draw_rect(fitz.Rect(50, 50, 220, 140))

        zones = detect_image_zones(page)

        self.assertTrue(any(z[0] <= 50 and z[2] >= 220 for z in zones))
        document.close()


class TestTranslationVerification(unittest.TestCase):
    def test_flags_untranslated_body_but_ignores_reference_entry(self):
        original = fitz.open()
        page = original.new_page(width=300, height=300)
        page.insert_text((30, 40), "This method improves the training objective significantly.")
        page.insert_text((30, 80), "[1] Smith et al. Learning representations. 2024.")

        translated = fitz.open()
        page = translated.new_page(width=300, height=300)
        page.insert_text((30, 40), "This method improves the training objective significantly.")
        page.insert_text((30, 80), "[1] Smith et al. Learning representations. 2024.")

        with self.subTest("verification"):
            import tempfile
            from pathlib import Path

            with tempfile.TemporaryDirectory() as tmpdir:
                original_path = Path(tmpdir) / "orig.pdf"
                translated_path = Path(tmpdir) / "zh.pdf"
                original.save(original_path)
                translated.save(translated_path)

                issues = verify_translation(original_path, translated_path)

        original.close()
        translated.close()
        self.assertTrue(any("untranslated English" in issue for issue in issues))
        self.assertFalse(any("2 block" in issue for issue in issues))

    def test_flags_caption_overlapping_figure_region(self):
        original = fitz.open()
        page = original.new_page(width=300, height=260)
        page.draw_rect(fitz.Rect(50, 50, 240, 150))
        page.insert_text((50, 178), "Figure 1: Overview of the system.")

        translated = fitz.open()
        page = translated.new_page(width=300, height=260)
        page.draw_rect(fitz.Rect(50, 50, 240, 150))
        page.insert_text((58, 92), "Figure 1: System overview.", fontsize=10)

        import tempfile
        from pathlib import Path

        with tempfile.TemporaryDirectory() as tmpdir:
            original_path = Path(tmpdir) / "orig.pdf"
            translated_path = Path(tmpdir) / "zh.pdf"
            original.save(original_path)
            translated.save(translated_path)

            issues = verify_translation_issues(original_path, translated_path)

        original.close()
        translated.close()
        self.assertTrue(any(issue.code == "caption_overlap" for issue in issues))


class TestBlockInZone(unittest.TestCase):
    """Test block-in-zone overlap detection."""

    def test_full_overlap(self):
        from pdf_zh_translator.pdf_layout import _block_in_zone
        self.assertTrue(_block_in_zone((100, 100, 200, 200), (50, 50, 250, 250)))

    def test_no_overlap(self):
        from pdf_zh_translator.pdf_layout import _block_in_zone
        self.assertFalse(_block_in_zone((100, 100, 200, 200), (300, 300, 400, 400)))

    def test_partial_overlap_below_threshold(self):
        from pdf_zh_translator.pdf_layout import _block_in_zone
        # Only ~10% overlap
        self.assertFalse(_block_in_zone((100, 100, 200, 200), (190, 100, 300, 200)))


if __name__ == "__main__":
    unittest.main()
