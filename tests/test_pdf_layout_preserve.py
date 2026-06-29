"""Tests for conservative native-layout preservation rules."""

import unittest
from pathlib import Path
from types import SimpleNamespace

import fitz

from pdf_zh_translator.pdf_layout import (
    SENTINEL_CLOSE,
    SENTINEL_OPEN,
    FontPack,
    TextBlock,
    _clip_block_bbox_against_floats,
    _formula_fragment_present,
    _LineRec,
    _looks_like_formula_fragment,
    _looks_like_overlap_exempt_text,
    _looks_like_untranslated_english,
    _normalize_formula_fragment_for_compare,
    _overlap_text_entries_from_block,
    _RawBlockRec,
    _visible_image_stats,
    _visual_min_zone_intersects_graphics,
    caption_should_center,
    center_caption_bbox,
    classify_blocks,
    clean_translation,
    collect_text_blocks,
    fragmented_prose_warnings_from_units,
    graphic_regions_for_page,
    insert_translated_text,
    is_math_span,
    mark_bibliography_blocks,
    math_heavy_block,
    merge_paragraph_blocks,
    prepare_translation_units,
    record_is_table,
    requested_translation_font_size,
    segments_from_record,
    should_preserve_original_block,
    strip_sentinels,
    trim_redact_bbox_against_formula_lines,
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

    def test_overlap_entries_use_line_bboxes_not_outer_table_block(self):
        block = {
            "bbox": (100.0, 100.0, 500.0, 160.0),
            "lines": [
                {
                    "bbox": (100.0, 100.0, 180.0, 112.0),
                    "spans": [{"text": "字段"}],
                },
                {
                    "bbox": (220.0, 124.0, 500.0, 136.0),
                    "spans": [{"text": "较长的字段说明"}],
                },
            ],
        }

        entries = _overlap_text_entries_from_block(block)

        self.assertEqual(
            [bbox for bbox, _ in entries],
            [(100.0, 100.0, 180.0, 112.0), (220.0, 124.0, 500.0, 136.0)],
        )

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

    def test_preserves_table_text_even_when_math_heavy(self):
        block = TextBlock(
            page_index=0,
            bbox=(207.3, 513.8, 396.8, 525.6),
            text=f"Regularization weight balancing{SENTINEL_OPEN}L{SENTINEL_CLOSE}SIG "
            f"and{SENTINEL_OPEN}L{SENTINEL_CLOSE}inv",
            font_size=10.0,
            color=(0.0, 0.0, 0.0),
            nowrap=True,
            no_merge=True,
            block_type="table",
        )

        self.assertTrue(math_heavy_block(block))
        self.assertTrue(should_preserve_original_block(block, []))

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

    def test_preserves_short_block_crossing_graphic_region(self):
        block = TextBlock(
            page_index=0,
            bbox=(10.0, 10.0, 260.0, 120.0),
            text="latent axis",
            font_size=10.0,
            color=(0.0, 0.0, 0.0),
            source_lines=1,
        )

        self.assertTrue(should_preserve_original_block(block, [(170.0, 20.0, 280.0, 100.0)]))

    def test_translates_theorem_text_inside_background_region(self):
        block = TextBlock(
            page_index=0,
            bbox=(117.6, 626.4, 494.4, 648.8),
            text=(
                "Consider any world satisfying Assumptions 3.1. Suppose every "
                "minimizer of (3) with Cov(h(z)) = In is linear, h(z) = Qz. "
                "Then z is Gaussian."
            ),
            font_size=10.0,
            color=(0.0, 0.0, 0.0),
            source_lines=3,
        )

        self.assertFalse(
            should_preserve_original_block(block, [(96.0, 592.2, 516.0, 667.0)])
        )

    def test_translates_theorem_heading_inside_background_region(self):
        block = TextBlock(
            page_index=0,
            bbox=(117.6, 120.2, 317.4, 130.2),
            text="Theorem 5 (Identifiability via Dirichlet energy)",
            font_size=10.0,
            color=(0.0, 0.0, 0.0),
        )

        self.assertFalse(
            should_preserve_original_block(block, [(96.0, 105.4, 516.0, 181.5)])
        )

    def test_translates_enumerated_assumption_inside_background_region(self):
        block = TextBlock(
            page_index=0,
            bbox=(129.6, 537.2, 409.1, 566.0),
            text=(
                "(ii) Stationarity. Both views share the same marginal: p(z) = p(z'). "
                "(iii) Additive noise. z' i = mi(zi) + eta i with eta i independent of zi."
            ),
            font_size=10.0,
            color=(0.0, 0.0, 0.0),
            source_lines=3,
        )

        self.assertFalse(
            should_preserve_original_block(block, [(96.0, 487.5, 516.0, 583.4)])
        )

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

    def test_heading_over_graphic_region_is_still_translated(self):
        block = TextBlock(
            page_index=0,
            bbox=(55.4, 67.8, 183.0, 79.8),
            text="A. Simulation and Assets",
            font_size=12.0,
            color=(0.0, 0.0, 0.0),
            block_type="heading",
        )

        self.assertFalse(should_preserve_original_block(block, [(40.0, 40.0, 550.0, 700.0)]))

    def test_nearly_centered_caption_is_centered_in_output(self):
        block = TextBlock(
            page_index=0,
            bbox=(110.0, 390.7, 486.4, 399.7),
            text="Figure 1: Overview of SafeLab.",
            font_size=9.0,
            color=(0.0, 0.0, 0.0),
            block_type="caption",
            preserve_position=True,
        )

        self.assertTrue(caption_should_center(block, 612.0))

    def test_centered_caption_moves_insert_box_but_keeps_original_redaction(self):
        block = TextBlock(
            page_index=0,
            bbox=(110.0, 390.7, 486.4, 399.7),
            text="Figure 1: Overview of SafeLab.",
            font_size=9.0,
            color=(0.0, 0.0, 0.0),
            block_type="caption",
        )

        moved = center_caption_bbox(block, 612.0)

        self.assertAlmostEqual((moved.bbox[0] + moved.bbox[2]) / 2.0, 306.0)
        self.assertEqual(moved.redact_bboxes, [block.bbox])

    def test_heading_requests_larger_translation_font(self):
        block = TextBlock(
            page_index=0,
            bbox=(75.0, 423.0, 120.0, 435.0),
            text="Abstract",
            font_size=10.0,
            color=(0.0, 0.0, 0.0),
            block_type="heading",
        )

        self.assertGreater(requested_translation_font_size(block, 5.0, 0.92), 10.0)

    def test_bibliography_ends_at_appendix_heading(self):
        blocks = [
            TextBlock(0, (10, 10, 200, 24), "References", 12.0, (0, 0, 0), bold=True),
            TextBlock(
                0,
                (10, 30, 200, 60),
                "[1] Smith et al. Learning representations. 2024.",
                9.0,
                (0, 0, 0),
            ),
            TextBlock(1, (10, 10, 200, 24), "A. Prompt", 10.0, (0, 0, 0)),
            TextBlock(
                1,
                (10, 30, 200, 60),
                "The appendix describes additional experiments.",
                9.0,
                (0, 0, 0),
            ),
        ]

        self.assertEqual(mark_bibliography_blocks(blocks), [False, True, False, False])

    def test_prepare_units_translates_appendix_after_references(self):
        document = fitz.open()
        page = document.new_page(width=360, height=360)
        page.insert_text((30, 40), "References", fontsize=12)
        page.insert_text((30, 70), "[1] Smith et al. Learning representations. 2024.")
        page = document.new_page(width=360, height=360)
        page.insert_text((120, 40), "A OPTIMIZATION OF THE PROXY REWARD", fontsize=10)
        page.insert_text((30, 70), "The appendix describes additional experiments.")

        units, _, _ = prepare_translation_units(document)

        document.close()
        exported_text = "\n".join(block.text for block, _, _ in units)
        self.assertIn("The appendix describes additional experiments.", exported_text)
        self.assertNotIn("Smith et al. Learning representations.", exported_text)

    def test_prepare_units_translates_lettered_appendix_heading_after_references(self):
        document = fitz.open()
        page = document.new_page(width=360, height=360)
        page.insert_text((30, 40), "References", fontsize=12)
        page.insert_text((30, 70), "[1] Smith et al. Learning representations. 2024.")
        page = document.new_page(width=360, height=360)
        page.insert_text((30, 40), "A. Prompt", fontsize=10)
        page.insert_text((30, 70), "We use a conversational structure to prompt the model.")

        units, _, _ = prepare_translation_units(document)

        document.close()
        exported_text = "\n".join(block.text for block, _, _ in units)
        self.assertIn("A. Prompt", exported_text)
        self.assertIn("We use a conversational structure to prompt the model.", exported_text)
        self.assertNotIn("Smith et al. Learning representations.", exported_text)


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
        self.assertTrue(all(segment.no_merge for segment in segments))
        self.assertTrue(all(segment.block_type == "table" for segment in segments))

    def test_equation_table_rows_do_not_merge_over_formula_column(self):
        record = _RawBlockRec(
            lines=[
                _LineRec(
                    text=f"{SENTINEL_OPEN}H_k(x){SENTINEL_CLOSE}",
                    bbox=(152.7, 544.0, 182.9, 554.8),
                    spans=[_span("H_k(x)", (152.7, 544.0, 182.9, 554.8), font="CMMI10")],
                ),
                _line(
                    "Probabilist Hermite polynomial of degree k",
                    (207.3, 544.0, 387.5, 554.2),
                ),
                _LineRec(
                    text=f"{SENTINEL_OPEN}c_alpha{SENTINEL_CLOSE}",
                    bbox=(160.9, 595.1, 174.2, 607.9),
                    spans=[
                        _span(
                            "c_alpha",
                            (160.9, 595.1, 174.2, 607.9),
                            font="CMMI10",
                        )
                    ],
                ),
                _line(
                    "Hermite coefficient of hi at multi-index alpha",
                    (207.3, 598.0, 374.0, 609.6),
                ),
                _line(
                    "Variance fraction of hi at degree d: P",
                    (207.3, 611.8, 360.6, 629.3),
                ),
                _LineRec(
                    text=f"{SENTINEL_OPEN}|alpha|=d(c_alpha)^2{SENTINEL_CLOSE}",
                    bbox=(360.6, 609.3, 406.9, 624.4),
                    spans=[
                        _span(
                            "|alpha|=d(c_alpha)^2",
                            (360.6, 609.3, 406.9, 624.4),
                            font="CMMI10",
                        )
                    ],
                ),
            ]
        )

        self.assertTrue(record_is_table(record))

        segments = segments_from_record(0, record, equation_record=True)
        merged = merge_paragraph_blocks(segments)

        self.assertEqual(len(segments), 3)
        self.assertEqual(len(merged), 3)
        self.assertTrue(all(segment.nowrap and segment.no_merge for segment in segments))
        self.assertTrue(all(segment.block_type == "table" for segment in segments))
        self.assertTrue(all(segment.bbox[2] <= 387.5 for segment in segments))
        self.assertIn("Variance fraction", segments[-1].text)
        assert segments[-1].redact_bboxes is not None
        self.assertAlmostEqual(segments[-1].redact_bboxes[0][2], 359.4)

    def test_redaction_trims_above_nearby_display_formula(self):
        formula = _LineRec(
            text=f"{SENTINEL_OPEN}h(z + sqrt(eps eta)){SENTINEL_CLOSE}",
            bbox=(208.8, 302.6, 403.2, 321.2),
            spans=[],
        )

        trimmed = trim_redact_bbox_against_formula_lines(
            (108.0, 294.0, 249.8, 304.0),
            [formula],
        )

        self.assertEqual(trimmed[:3], (108.0, 294.0, 249.8))
        self.assertAlmostEqual(trimmed[3], 301.4)

    def test_classification_preserves_equation_table_rows(self):
        block = TextBlock(
            page_index=0,
            bbox=(207.3, 513.8, 396.8, 525.6),
            text=f"Regularization weight balancing{SENTINEL_OPEN}L{SENTINEL_CLOSE}SIG "
            f"and{SENTINEL_OPEN}L{SENTINEL_CLOSE}inv",
            font_size=10.0,
            color=(0.0, 0.0, 0.0),
            nowrap=True,
            no_merge=True,
            block_type="table",
        )

        classify_blocks([block], page_index=0, page_height=720.0, image_zones=[])

        self.assertEqual(block.block_type, "table")
        self.assertTrue(block.should_translate)


class FragmentedProseWarningTests(unittest.TestCase):
    def test_margin_line_numbers_do_not_block_prose_merging(self):
        document = fitz.open()
        page = document.new_page(width=410, height=300)
        body_lines = [
            "Dexterous grasping with multi-fingered hands has achieved",
            "substantial progress in static object manipulation. In contrast,",
            "catching in-flight objects remains largely underexplored.",
        ]
        for index, text in enumerate(body_lines, start=5):
            y = 150 + (index - 5) * 11
            page.insert_text((62, y), text, fontsize=9)
            page.insert_text((5, y), f"{index:03d}", fontsize=7)
            page.insert_text((396, y), f"{index:03d}", fontsize=7)

        blocks, gutter_rects = collect_text_blocks(document)
        merged = merge_paragraph_blocks(blocks)

        document.close()
        self.assertGreaterEqual(len(gutter_rects.get(0, [])), 6)
        self.assertEqual(len(merged), 1)
        self.assertFalse(merged[0].nowrap)
        self.assertIn("substantial progress", merged[0].text)
        self.assertNotIn("005", merged[0].text)

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

    def test_ignores_small_fixed_width_table_fragments(self):
        units = [
            (
                TextBlock(
                    page_index=0,
                    bbox=(50.0, 80.0 + index * 8.0, 260.0, 86.0 + index * 8.0),
                    text=f"0.{index} d4019: Disease code ↔ p966: Procedure code",
                    font_size=7.0,
                    color=(0.0, 0.0, 0.0),
                    nowrap=True,
                    source_lines=1,
                ),
                f"0.{index} d4019: Disease code ↔ p966: Procedure code",
                {},
            )
            for index in range(12)
        ]

        warnings = fragmented_prose_warnings_from_units(units)

        self.assertEqual(warnings, [])

    def test_ignores_catalog_and_task_table_fragments(self):
        snippets = [
            "Standard Erlenmeyer flasks, Stoppered Erlenmeyer flasks, Volumetric flasks",
            "Transfer liquid from a 100 mL beaker to a 250 mL beaker without spillage.",
            "Schema-constrained YAML with scene, goals, phase transitions, and safety constraints.",
            "Pass rates are stage-conditional; the end-to-end pass rate is normalized in Table 6.",
            "DiffDrive-Perception",
            "IntentConditionedAgentGate",
            "Controls corpus shuffle order",
        ]
        units = [
            (
                TextBlock(
                    page_index=0,
                    bbox=(160.0, 100.0 + index * 12.0, 500.0, 110.0 + index * 12.0),
                    text=text,
                    font_size=9.0,
                    color=(0.0, 0.0, 0.0),
                    nowrap=True,
                    source_lines=1,
                ),
                text,
                {},
            )
            for index, text in enumerate(snippets)
        ]

        warnings = fragmented_prose_warnings_from_units(units)

        self.assertEqual(warnings, [])

    def test_ignores_prompt_template_table_fragments(self):
        snippets = [
            "Your task is to judge whether the edited image successfully transforms the human hand",
            "Please assign four 1-5 sub-scores:",
            "Target-embodiment match",
            "Interaction preservation",
            "Scene preservation",
            "Use the following scale for each sub-score: 1 = failure, 2 = incorrect",
            "Output a concise rationale and the following numeric fields:",
            "integer score from 1 to 5",
            "brief explanation",
        ]
        units = [
            (
                TextBlock(
                    page_index=0,
                    bbox=(115.0, 100.0 + index * 12.0, 512.0, 110.0 + index * 12.0),
                    text=text,
                    font_size=10.0,
                    color=(0.0, 0.0, 0.0),
                    nowrap=True,
                    source_lines=1,
                ),
                text,
                {},
            )
            for index, text in enumerate(snippets)
        ]

        warnings = fragmented_prose_warnings_from_units(units)

        self.assertEqual(warnings, [])


class FormulaTailProseTests(unittest.TestCase):
    def test_clean_translation_collapses_mixed_formula_parentheses(self):
        self.assertEqual(clean_translation("（U e ij≈0).）"), "（U e ij≈0）")

    def test_clean_translation_replaces_math_angle_brackets_for_cjk_fonts(self):
        self.assertEqual(clean_translation("元组T = ⟨S,G,C⟩。"), "元组 T = 〈S,G,C〉。")

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
        self.assertEqual(segments[0].redact_bboxes, [first.bbox, tail.bbox, second.bbox])

    def test_inline_formula_bridge_redacts_spans_not_next_display_formula(self):
        first = _LineRec(
            text=f"We now proceed to bound{SENTINEL_OPEN}H^{1}{SENTINEL_CLOSE}",
            bbox=(88.9, 155.8, 255.3, 168.4),
            spans=[
                _span("We now proceed to bound", (88.9, 155.8, 230.0, 168.4)),
                _span("H^1", (230.0, 155.8, 255.3, 168.4), font="CMMI10"),
            ],
        )
        formula_tail = _LineRec(
            text=f"{SENTINEL_OPEN}w∈D'(x;t,δ){SENTINEL_CLOSE}",
            bbox=(255.3, 155.8, 379.5, 169.3),
            spans=[_span("w∈D'(x;t,δ)", (255.3, 155.8, 379.5, 169.3), font="CMMI10")],
        )
        prose_tail = _LineRec(
            text=f". For any{SENTINEL_OPEN}w'{SENTINEL_CLOSE}, we",
            bbox=(392.9, 153.4, 540.0, 168.4),
            spans=[
                _span(". For any", (392.9, 153.4, 448.0, 168.4)),
                _span("w'", (448.0, 153.4, 460.0, 168.4), font="CMMI10"),
                _span(", we", (460.0, 153.4, 540.0, 168.4)),
            ],
        )
        define = _LineRec(
            text="define",
            bbox=(72.0, 171.1, 99.9, 182.0),
            spans=[_span("define", (72.0, 171.1, 99.9, 182.0))],
        )
        next_formula_bbox = (170.2, 176.4, 540.0, 207.8)
        record = _RawBlockRec(lines=[first, formula_tail, prose_tail, define])

        segments = segments_from_record(0, record, equation_record=True)

        self.assertEqual(len(segments), 1)
        assert segments[0].redact_bboxes is not None
        self.assertGreater(len(segments[0].redact_bboxes), 1)
        self.assertNotEqual(segments[0].redact_bboxes, [segments[0].bbox])
        self.assertFalse(
            any(
                max(bbox[0], next_formula_bbox[0]) < min(bbox[2], next_formula_bbox[2])
                and max(bbox[1], next_formula_bbox[1]) < min(bbox[3], next_formula_bbox[3])
                for bbox in segments[0].redact_bboxes
            )
        )

    def test_formula_prefix_tail_translates_on_following_prose_line(self):
        formula_prefix = _LineRec(
            text=(
                f"2{SENTINEL_OPEN}D{SENTINEL_CLOSE} "
                f"{SENTINEL_OPEN}≥{SENTINEL_CLOSE}"
                f"{SENTINEL_OPEN}c{SENTINEL_CLOSE}. As"
            ),
            bbox=(487.2, 545.7, 540.0, 560.4),
            spans=[
                _span("2", (487.2, 551.9, 491.4, 559.9), size=8.0),
                _span("D", (491.4, 551.9, 498.4, 559.9), size=8.0, font="CMMI8"),
                _span(" ", (498.4, 549.5, 503.5, 560.4), font="CMSY10"),
                _span("≥", (503.5, 545.7, 512.0, 556.6), font="CMSY10"),
                _span("c", (515.0, 545.9, 519.7, 556.8), font="CMMI10"),
                _span(". As", (519.7, 545.9, 540.0, 556.8)),
            ],
        )
        prose = _LineRec(
            text=(
                f"{SENTINEL_OPEN}ε{SENTINEL_CLOSE} is sufficiently small, by (5.188), "
                f"{SENTINEL_OPEN}⟨V(y),a⟩≥c{SENTINEL_CLOSE}."
            ),
            bbox=(72.0, 560.8, 531.2, 573.0),
            spans=[
                _span("ε", (72.0, 560.8, 78.0, 573.0), font="CMMI10"),
                _span(
                    " is sufficiently small, by (5.188), ",
                    (78.0, 560.8, 260.0, 573.0),
                ),
                _span("⟨V(y),a⟩≥c", (260.0, 560.8, 360.0, 573.0), font="CMMI10"),
                _span(".", (360.0, 560.8, 365.0, 573.0)),
            ],
        )
        next_formula = _LineRec(
            text=f"{SENTINEL_OPEN}t−⟨y,a⟩−b{SENTINEL_CLOSE}",
            bbox=(115.2, 584.3, 206.1, 603.0),
            spans=[_span("t−⟨y,a⟩−b", (115.2, 584.3, 206.1, 603.0), font="CMMI10")],
        )
        record = _RawBlockRec(lines=[formula_prefix, prose, next_formula])

        segments = segments_from_record(0, record)

        self.assertEqual(len(segments), 1)
        self.assertNotIn("2D", strip_sentinels(segments[0].text))
        self.assertTrue(segments[0].text.startswith("As"))
        self.assertEqual(segments[0].bbox, prose.bbox)
        self.assertEqual(
            segments[0].redact_bboxes,
            [(529.85, 545.9, 540.0, 556.8), prose.bbox],
        )

    def test_equation_record_keeps_short_formula_prose_connector(self):
        first = _LineRec(
            text=(
                f"We define the distance between {SENTINEL_OPEN}T{SENTINEL_CLOSE} "
                f"and {SENTINEL_OPEN}T'{SENTINEL_CLOSE}"
            ),
            bbox=(72.0, 346.7, 540.0, 359.4),
            spans=[_span("We define the distance between T and T'", (72.0, 346.7, 540.0, 359.4))],
        )
        connector = _LineRec(
            text=(
                f"between{SENTINEL_OPEN}T{SENTINEL_CLOSE} "
                f"and{SENTINEL_OPEN}T'{SENTINEL_CLOSE} to be"
            ),
            bbox=(72.0, 358.1, 185.7, 372.9),
            spans=[
                _span("between", (72.0, 358.1, 105.0, 372.9)),
                _span("T", (105.0, 358.1, 114.0, 372.9), font="CMMI10"),
                _span(" and", (114.0, 358.1, 136.0, 372.9)),
                _span("T'", (136.0, 358.1, 148.0, 372.9), font="CMMI10"),
                _span(" to be", (148.0, 358.1, 185.7, 372.9)),
            ],
        )
        formula = _LineRec(
            text=f"{SENTINEL_OPEN}p{SENTINEL_CLOSE}",
            bbox=(189.4, 360.5, 200.3, 371.4),
            spans=[_span("p", (189.4, 360.5, 200.3, 371.4), font="CMMI10")],
        )
        record = _RawBlockRec(lines=[first, connector, formula])

        segments = segments_from_record(0, record, equation_record=True)

        self.assertEqual(len(segments), 1)
        self.assertIn("to be", segments[0].text)
        self.assertEqual(segments[0].redact_bboxes, [first.bbox, connector.bbox])

    def test_equation_record_keeps_pushforward_connector(self):
        first = _LineRec(
            text="Define Q to",
            bbox=(72.0, 693.8, 540.0, 711.6),
            spans=[_span("Define Q to", (72.0, 693.8, 540.0, 711.6))],
        )
        connector = _LineRec(
            text=f"be the pushforward of{SENTINEL_OPEN}γ'{SENTINEL_CLOSE}",
            bbox=(72.0, 709.7, 190.3, 722.4),
            spans=[
                _span("be the pushforward of", (72.0, 709.7, 177.0, 722.4)),
                _span("γ'", (177.0, 709.7, 190.3, 722.4), font="CMMI10"),
            ],
        )
        continuation = _LineRec(
            text=f"{SENTINEL_OPEN}0{SENTINEL_CLOSE} by r. Note that Q is Borel",
            bbox=(187.4, 707.4, 540.0, 725.3),
            spans=[
                _span("0", (187.4, 707.4, 194.0, 725.3), font="CMMI10"),
                _span(" by r. Note that Q is Borel", (194.0, 707.4, 540.0, 725.3)),
            ],
        )
        record = _RawBlockRec(lines=[first, connector, continuation])

        segments = segments_from_record(0, record, equation_record=True)

        self.assertEqual(len(segments), 1)
        self.assertIn("pushforward", segments[0].text)
        self.assertIn("Note that Q is Borel", segments[0].text)

    def test_equation_record_keeps_short_where_use_fragment(self):
        where_line = _LineRec(
            text="where we use",
            bbox=(72.0, 298.5, 136.0, 309.4),
            spans=[_span("where we use", (72.0, 298.5, 136.0, 309.4))],
        )
        integral = _LineRec(
            text=f"{SENTINEL_OPEN}R{SENTINEL_CLOSE}",
            bbox=(140.2, 297.5, 145.4, 308.4),
            spans=[_span("R", (140.2, 297.5, 145.4, 308.4), font="CMEX10")],
        )
        record = _RawBlockRec(lines=[where_line, integral])

        segments = segments_from_record(0, record, equation_record=True)

        self.assertEqual(len(segments), 1)
        self.assertEqual(segments[0].text, "where we use")

    def test_equation_record_keeps_short_by_replacement_fragment(self):
        first = _LineRec(
            text="using (3.8) with x replaced",
            bbox=(216.7, 622.9, 540.0, 637.8),
            spans=[_span("using (3.8) with x replaced", (216.7, 622.9, 540.0, 637.8))],
        )
        by_fragment = _LineRec(
            text=f"by{SENTINEL_OPEN}x0, x'{SENTINEL_CLOSE}",
            bbox=(72.0, 636.4, 111.5, 650.1),
            spans=[
                _span("by", (72.0, 636.4, 84.0, 650.1)),
                _span("x0, x'", (84.0, 636.4, 111.5, 650.1), font="CMMI10"),
            ],
        )
        continuation = _LineRec(
            text=f"{SENTINEL_OPEN}0, z{SENTINEL_CLOSE}), we have",
            bbox=(109.2, 638.2, 172.8, 651.4),
            spans=[
                _span("0, z", (109.2, 638.2, 132.0, 651.4), font="CMMI10"),
                _span("), we have", (132.0, 638.2, 172.8, 651.4)),
            ],
        )
        record = _RawBlockRec(lines=[first, by_fragment, continuation])

        segments = segments_from_record(0, record, equation_record=True)

        self.assertEqual(len(segments), 1)
        self.assertIn("by", segments[0].text)
        assert segments[0].redact_bboxes is not None
        self.assertIn(by_fragment.bbox, segments[0].redact_bboxes)

    def test_equation_record_keeps_math_wrapped_sentence(self):
        first = _LineRec(
            text="We always consider a policy in the context of the underlying metric MDP.",
            bbox=(120.0, 680.0, 504.0, 690.0),
            spans=[
                _span(
                    "We always consider a policy in the context of the underlying metric MDP.",
                    (120.0, 680.0, 504.0, 690.0),
                )
            ],
        )
        formula_sentence = _LineRec(
            text=(
                f"({SENTINEL_OPEN}S, A, R, P, T, d{SENTINEL_CLOSE}"
                f"{SENTINEL_OPEN}E{SENTINEL_CLOSE}) are different from every policy "
                f"acting on ({SENTINEL_OPEN}S, A, R, P, T, d{SENTINEL_CLOSE}"
                f"{SENTINEL_OPEN}A{SENTINEL_CLOSE}) as soon as"
                f"{SENTINEL_OPEN}d{SENTINEL_CLOSE}{SENTINEL_OPEN}E{SENTINEL_CLOSE}"
                f"{SENTINEL_OPEN}̸{SENTINEL_CLOSE}={SENTINEL_OPEN}d{SENTINEL_CLOSE}"
                f"{SENTINEL_OPEN}A{SENTINEL_CLOSE}. This"
            ),
            bbox=(108.0, 691.0, 504.0, 701.0),
            spans=[
                _span("(S, A, R, P, T, dE)", (108.0, 691.0, 178.0, 701.0)),
                _span(
                    " are different from every policy acting on ",
                    (178.0, 691.0, 329.0, 701.0),
                ),
                _span("(S, A, R, P, T, dA)", (329.0, 691.0, 402.0, 701.0)),
                _span(" as soon as dE ̸= dA. This", (402.0, 691.0, 504.0, 701.0)),
            ],
        )
        continuation = _LineRec(
            text="guarantees that the distance respects the identity of indiscernibles.",
            bbox=(108.0, 701.0, 422.0, 710.0),
            spans=[
                _span(
                    "guarantees that the distance respects the identity of indiscernibles.",
                    (108.0, 701.0, 422.0, 710.0),
                )
            ],
        )
        record = _RawBlockRec(lines=[first, formula_sentence, continuation])

        segments = segments_from_record(0, record, equation_record=True)

        self.assertEqual(len(segments), 1)
        self.assertIn("are different from every policy", segments[0].text)
        self.assertIn("guarantees that the distance", segments[0].text)

    def test_equation_record_keeps_short_formula_label_tail(self):
        formula_label = _LineRec(
            text=(
                f"{SENTINEL_OPEN}e{SENTINEL_CLOSE}{SENTINEL_OPEN}l{SENTINEL_CLOSE}, "
                f"{SENTINEL_OPEN}e{SENTINEL_CLOSE}{SENTINEL_OPEN}g{SENTINEL_CLOSE}, "
                f"{SENTINEL_OPEN}e{SENTINEL_CLOSE}{SENTINEL_OPEN}o{SENTINEL_CLOSE} = "
                f"{SENTINEL_OPEN}T{SENTINEL_CLOSE}(... ) "
                "(Fig. 2, top); and readout heads"
            ),
            bbox=(49.0, 94.0, 300.0, 105.0),
            spans=[
                _span("el, eg, eo = T(...)", (49.0, 94.0, 153.0, 105.0)),
                _span(" (Fig. 2, top); and", (153.0, 94.0, 232.0, 105.0)),
                _span(" readout heads", (232.0, 94.0, 300.0, 105.0), flags=5),
            ],
        )
        record = _RawBlockRec(lines=[formula_label])

        segments = segments_from_record(0, record, equation_record=True)

        self.assertEqual(len(segments), 1)
        self.assertIn("readout heads", segments[0].text)

    def test_equation_record_does_not_split_same_baseline_formula_chunks(self):
        record = _RawBlockRec(
            lines=[
                _LineRec(
                    text=(
                        f"language instructions{SENTINEL_OPEN}ℓ{SENTINEL_CLOSE}, "
                        f"goals{SENTINEL_OPEN}g{SENTINEL_CLOSE}, and observation sequences"
                    ),
                    bbox=(49.0, 58.0, 300.0, 68.0),
                    spans=[_span("language instructions ℓ, goals g", (49.0, 58.0, 180.0, 68.0))],
                ),
                _LineRec(
                    text=(
                        f"{SENTINEL_OPEN}o{SENTINEL_CLOSE}1"
                        f"{SENTINEL_OPEN}, . . . , o{SENTINEL_CLOSE}"
                        f"{SENTINEL_OPEN}H{SENTINEL_CLOSE} into tokens"
                    ),
                    bbox=(49.0, 70.0, 138.0, 82.0),
                    spans=[_span("o1, . . . , oH into tokens", (49.0, 70.0, 138.0, 82.0))],
                ),
                _LineRec(
                    text=f"{SENTINEL_OPEN}\x02{SENTINEL_CLOSE}",
                    bbox=(141.0, 69.0, 145.0, 79.0),
                    spans=[_span("\x02", (141.0, 69.0, 145.0, 79.0), font="CMEX10")],
                ),
                _LineRec(
                    text=(
                        f"{SENTINEL_OPEN}T{SENTINEL_CLOSE}{SENTINEL_OPEN}l{SENTINEL_CLOSE}"
                        f"{SENTINEL_OPEN},{SENTINEL_CLOSE}"
                        f"{SENTINEL_OPEN}T{SENTINEL_CLOSE}{SENTINEL_OPEN}g{SENTINEL_CLOSE}"
                        f"{SENTINEL_OPEN},{SENTINEL_CLOSE}"
                        f"{SENTINEL_OPEN}T{SENTINEL_CLOSE}{SENTINEL_OPEN}o{SENTINEL_CLOSE}"
                    ),
                    bbox=(145.0, 70.0, 182.0, 87.0),
                    spans=[_span("Tl,Tg,To", (145.0, 70.0, 182.0, 87.0), font="CMSY10")],
                ),
                _LineRec(
                    text=f"{SENTINEL_OPEN}\x03{SENTINEL_CLOSE}",
                    bbox=(183.0, 69.0, 187.0, 79.0),
                    spans=[_span("\x03", (183.0, 69.0, 187.0, 79.0), font="CMEX10")],
                ),
                _LineRec(
                    text="(Fig. 2, left); a transformer backbone",
                    bbox=(189.0, 70.0, 300.0, 92.0),
                    spans=[
                        _span(
                            "(Fig. 2, left); a transformer backbone",
                            (189.0, 70.0, 300.0, 92.0),
                        )
                    ],
                ),
            ]
        )

        segments = segments_from_record(0, record, equation_record=True)

        self.assertEqual(len(segments), 1)
        self.assertIn("observation sequences", segments[0].text)
        self.assertIn("a transformer backbone", segments[0].text)

    def test_multiline_segments_redact_per_source_line(self):
        first = _LineRec(
            text="Here, a proximal term based on Kullback-Leibler (KL) di-",
            bbox=(55.4, 180.9, 291.1, 190.9),
            spans=[
                _span(
                    "Here, a proximal term based on Kullback-Leibler (KL) di-",
                    (55.4, 180.9, 291.1, 190.9),
                )
            ],
        )
        second = _LineRec(
            text=(
                f"vergence, KL({SENTINEL_OPEN}T∥T ⁽ⁿ⁾{SENTINEL_CLOSE}) = "
                f"{SENTINEL_OPEN}P{SENTINEL_CLOSE}"
            ),
            bbox=(55.2, 193.0, 175.2, 211.4),
            spans=[
                _span("vergence, KL(", (55.2, 193.0, 114.0, 211.4)),
                _span("T∥T ⁽ⁿ⁾", (114.0, 193.0, 146.0, 211.4), font="CMMIB10"),
                _span(") = P", (146.0, 193.0, 175.2, 211.4)),
            ],
        )
        record = _RawBlockRec(lines=[first, second])

        segments = segments_from_record(0, record)

        self.assertEqual(len(segments), 1)
        self.assertEqual(segments[0].redact_bboxes, [first.bbox, second.bbox])
        self.assertLess(segments[0].redact_bboxes[1][2], segments[0].bbox[2])

    def test_equation_record_keeps_short_heading_glued_to_prose(self):
        heading = _LineRec(
            text="Gist memory.",
            bbox=(108.0, 216.7, 168.5, 226.8),
            spans=[_span("Gist memory.", (108.0, 216.7, 168.5, 226.8))],
        )
        first = _LineRec(
            text="While short-term and event-",
            bbox=(177.5, 216.8, 297.7, 226.9),
            spans=[_span("While short-term and event-", (177.5, 216.8, 297.7, 226.9))],
        )
        second = _LineRec(
            text="boundary memories preserve selected frames",
            bbox=(108.0, 228.8, 296.0, 238.8),
            spans=[
                _span(
                    "boundary memories preserve selected frames",
                    (108.0, 228.8, 296.0, 238.8),
                )
            ],
        )
        formula = _LineRec(
            text=f"{SENTINEL_OPEN}|Cv_full| = O(NL){SENTINEL_CLOSE}",
            bbox=(167.8, 299.7, 296.7, 313.5),
            spans=[_span("|Cv_full| = O(NL)", (167.8, 299.7, 296.7, 313.5))],
        )
        record = _RawBlockRec(lines=[heading, first, second, formula])

        segments = segments_from_record(0, record, equation_record=True)

        self.assertEqual(len(segments), 1)
        self.assertIn("Gist memory. While short-term", segments[0].text)
        self.assertNotIn("O(NL)", strip_sentinels(segments[0].text))
        self.assertEqual(segments[0].redact_bboxes[0], heading.bbox)


class PreserveGraphicsTextTests(unittest.TestCase):
    def test_wide_shallow_background_rule_is_not_graphic_region(self):
        document = fitz.open()
        page = document.new_page(width=410, height=620)
        page.draw_rect(fitz.Rect(-160, 443, 407, 455), color=None, fill=(0.95, 0.95, 0.95))
        page.draw_rect(fitz.Rect(60, 80, 220, 180))

        regions = graphic_regions_for_page(page)

        document.close()
        self.assertTrue(all(region[0] < region[2] and region[1] < region[3] for region in regions))
        self.assertTrue(any(region[0] <= 60 and region[2] >= 220 for region in regions))
        self.assertFalse(
            any(region[1] <= 443 and region[3] >= 455 and region[0] <= 1 for region in regions)
        )

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

    def test_merges_fixed_width_body_line_fragments(self):
        blocks = [
            TextBlock(
                page_index=0,
                bbox=(312.0, 565.0 + index * 12.0, 563.0, 575.0 + index * 12.0),
                text=text,
                font_size=10.0,
                color=(0.0, 0.0, 0.0),
                nowrap=True,
                source_lines=1,
            )
            for index, text in enumerate(
                [
                    "To test our hypotheses, we extract activations from the 33",
                    "hidden layers of OpenVLA's Llama 2 7B backbone. Each",
                    "hidden-layer embedding is a 4096-dimensional vector. We then",
                ]
            )
        ]

        merged = merge_paragraph_blocks(blocks)

        self.assertEqual(len(merged), 1)
        self.assertFalse(merged[0].nowrap)
        self.assertEqual(merged[0].source_lines, 3)
        self.assertIn("hidden-layer embedding", merged[0].text)

    def test_stops_merge_when_wrapped_float_text_resumes_full_width(self):
        blocks = [
            TextBlock(
                page_index=0,
                bbox=(108.0, 96.0 + index * 11.0, 296.0, 106.0 + index * 11.0),
                text=text,
                font_size=10.0,
                color=(0.0, 0.0, 0.0),
            )
            for index, text in enumerate(
                [
                    "Table 4 evaluates the effect of LLM backbone ca-",
                    "pability on closed-loop discovery. We keep the",
                    "Qwen2.5-7B-Instruct as the smaller local ensem-",
                    "ble model but vary the primary model driving",
                    "the experiments. Model scale is most benefi-",
                    "cial on the more compositional and structured",
                    "benchmarks. ActiveSciBench-Chem improves",
                    "consistently from Qwen3-4B to Qwen3-32B",
                    "across SA, exact accuracy, and RMSLE, while",
                    "ActiveSciBench-GRN shows clear gains in edge",
                ]
            )
        ]
        blocks.extend(
            [
                TextBlock(
                    page_index=0,
                    bbox=(108.0, 206.0, 504.0, 216.0),
                    text="F1 and exact graph accuracy. This suggests that stronger backbones",
                    font_size=10.0,
                    color=(0.0, 0.0, 0.0),
                ),
                TextBlock(
                    page_index=0,
                    bbox=(108.0, 217.0, 504.0, 227.0),
                    text="provide better mechanistic priors for selecting relevant variables.",
                    font_size=10.0,
                    color=(0.0, 0.0, 0.0),
                ),
            ]
        )

        merged = merge_paragraph_blocks(blocks)

        self.assertEqual(len(merged), 2)
        self.assertLess(merged[0].bbox[2], 300.0)
        self.assertEqual(merged[0].source_lines, 10)
        self.assertEqual(merged[1].source_lines, 2)
        self.assertGreater(merged[1].bbox[2], 500.0)

    def test_merges_overlapping_formula_tail_continuation(self):
        first = TextBlock(
            page_index=0,
            bbox=(108.0, 388.0, 504.0, 498.0),
            text=(
                f"Reward design. For Driving Score. Let {SENTINEL_OPEN}"
                f"ell_comp_t{SENTINEL_CLOSE}"
            ),
            font_size=10.0,
            color=(0.0, 0.0, 0.0),
            source_lines=10,
        )
        second = TextBlock(
            page_index=0,
            bbox=(108.0, 486.9, 504.0, 518.9),
            text=(
                f"{SENTINEL_OPEN}>{SENTINEL_CLOSE} 0 be the theoretical compute "
                "latency of frame t in seconds."
            ),
            font_size=10.0,
            color=(0.0, 0.0, 0.0),
            source_lines=3,
        )

        merged = merge_paragraph_blocks([first, second])

        self.assertEqual(len(merged), 1)
        self.assertEqual(merged[0].source_lines, 13)
        self.assertIn("theoretical compute latency", merged[0].text)

    def test_does_not_merge_narrow_fixed_width_table_cells(self):
        blocks = [
            TextBlock(
                page_index=0,
                bbox=(312.0, 637.0 + index * 12.0, 390.0, 647.0 + index * 12.0),
                text=text,
                font_size=10.0,
                color=(0.0, 0.0, 0.0),
                nowrap=True,
                source_lines=1,
            )
            for index, text in enumerate(
                [
                    "behind(tabletop-object1,",
                    "of(tabletop-object1,",
                    "on(tabletop-object1,",
                ]
            )
        ]

        merged = merge_paragraph_blocks(blocks)

        self.assertEqual(len(merged), 3)

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

    def test_segments_split_standalone_heading_before_body(self):
        record = _RawBlockRec(
            lines=[
                _LineRec(
                    text="Abstract",
                    bbox=(50.0, 50.0, 95.0, 62.0),
                    spans=[
                        {
                            "text": "Abstract",
                            "bbox": (50.0, 50.0, 95.0, 62.0),
                            "size": 10.0,
                            "flags": 16,
                            "color": 0,
                        }
                    ],
                ),
                _LineRec(
                    text="Laboratory automation requires safe embodied agents.",
                    bbox=(50.0, 66.0, 280.0, 78.0),
                    spans=[
                        {
                            "text": "Laboratory automation requires safe embodied agents.",
                            "bbox": (50.0, 66.0, 280.0, 78.0),
                            "size": 9.0,
                            "flags": 0,
                            "color": 0,
                        }
                    ],
                ),
            ]
        )

        blocks = segments_from_record(0, record)

        self.assertEqual(len(blocks), 2)
        self.assertEqual(blocks[0].block_type, "heading")
        self.assertTrue(blocks[0].bold)
        self.assertTrue(blocks[0].no_merge)
        self.assertEqual(blocks[1].text, "Laboratory automation requires safe embodied agents.")

    def test_segments_split_numbered_heading_before_body(self):
        record = _RawBlockRec(
            lines=[
                _LineRec(
                    text="1. Introduction",
                    bbox=(55.0, 67.0, 132.0, 80.0),
                    spans=[
                        {
                            "text": "1. Introduction",
                            "bbox": (55.0, 67.0, 132.0, 80.0),
                            "size": 12.0,
                            "flags": 16,
                            "color": 0,
                        }
                    ],
                ),
                _LineRec(
                    text="While scientific discovery drives technological progress.",
                    bbox=(55.0, 83.0, 291.0, 93.0),
                    spans=[
                        {
                            "text": "While scientific discovery drives technological progress.",
                            "bbox": (55.0, 83.0, 291.0, 93.0),
                            "size": 10.0,
                            "flags": 0,
                            "color": 0,
                        }
                    ],
                ),
            ]
        )

        blocks = segments_from_record(0, record)

        self.assertEqual(len(blocks), 2)
        self.assertEqual(blocks[0].text, "1. Introduction")
        self.assertEqual(blocks[0].block_type, "heading")
        self.assertEqual(
            blocks[1].text,
            "While scientific discovery drives technological progress.",
        )

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
    def test_overlap_detector_exempts_formula_code_and_table_cells(self):
        self.assertTrue(_looks_like_overlap_exempt_text("minT ∈Π(µs,µt)⟨L(Cs(X(m)"))
        self.assertTrue(
            _looks_like_overlap_exempt_text(
                "def policy(graph): mem = graph.task_memory.setdefault('swap_cups', {})"
            )
        )
        self.assertTrue(_looks_like_overlap_exempt_text("1,50010−5"))
        self.assertTrue(
            _looks_like_overlap_exempt_text(
                "PnP OnceSuccess RateDrop CubeSuccess RateStage CupSuccess Rate"
            )
        )
        self.assertTrue(
            _looks_like_overlap_exempt_text(
                "\x07\x08\x06\x05\x07\x04\x03\x01\x08 \x00\x02 \x1b synthetic glyph run"
            )
        )

    def test_overlap_detector_keeps_body_prose(self):
        self.assertFalse(
            _looks_like_overlap_exempt_text(
                "本文提出一种用于长时程操作任务的结构化场景记忆方法。"
            )
        )
        self.assertFalse(
            _looks_like_overlap_exempt_text(
                "The proposed model improves retrieval quality across multiple tasks."
            )
        )

    def test_untranslated_detector_ignores_expected_english_fragments(self):
        self.assertFalse(
            _looks_like_untranslated_english(
                "Anthony Brohan∗, Noah Brown∗, Justice Carbajal∗, Yevgen Chebotar∗, "
                "Joseph Dabis∗, Chelsea Finn∗"
            )
        )
        self.assertFalse(
            _looks_like_untranslated_english(
                "Kevin Black, Noah Brown, Danny Driess, Adnan Esmail, Michael Equi, "
                "Chelsea Finn, Niccolo Fusai, Lachy Groom"
            )
        )
        self.assertFalse(
            _looks_like_untranslated_english(
                "Theodor Wulff* Federico Tavella Rahul Singh Maharjan Manith Adikari "
                "Angelo Cangelosi"
            )
        )
        self.assertFalse(
            _looks_like_untranslated_english("Hao Liu1Yanni Ma2Yan Liu2Haihong Xiao3Ying He1")
        )
        self.assertFalse(
            _looks_like_untranslated_english(
                "language-action models transfer web knowledge to robotic control, "
                "in CoRL, 2023, pp. 2165-2183."
            )
        )
        self.assertFalse(
            _looks_like_untranslated_english(
                "Pieter Abbeel and Andrew Y Ng. Apprenticeship learning via inverse "
                "reinforcement learning. In"
            )
        )
        self.assertFalse(
            _looks_like_untranslated_english(
                "def policy(graph): mem = graph.task_memory.setdefault('swap_cups', {})"
            )
        )
        self.assertFalse(
            _looks_like_untranslated_english(
                "ϕ : supp[ρπE] supp[ρπA] satisfies the metric relation"
            )
        )
        self.assertFalse(
            _looks_like_untranslated_english(
                "PnP OnceSuccess RateDrop CubeSuccess RateStage CupSuccess Rate"
            )
        )
        self.assertFalse(
            _looks_like_untranslated_english(
                "MVT [21]VoteNetScanReferViewRefer [18]VoteNetScanRefer3D-SPS [29]VoteNetScanRefer"
            )
        )
        self.assertFalse(
            _looks_like_untranslated_english(
                "Require: Prompt context St, base ensemble models, candidate query pool"
            )
        )
        self.assertFalse(
            _looks_like_untranslated_english(
                "23:Move selected candidates from Vque to Vref; update hi ←yi24: end for25: "
                "return top-k candidates"
            )
        )
        self.assertFalse(
            _looks_like_untranslated_english(
                "# 设置材料pure_f = mcdc.MaterialMG(fission=np.array([0.0, 1.0]), "
                "nu_p=np.array([1.2]))"
            )
        )
        self.assertFalse(
            _looks_like_untranslated_english(
                "runtime_contract: execution_class: 内联 affects_current_frame: 真 "
                "output_shape:(201, B, 256)"
            )
        )
        self.assertFalse(
            _looks_like_untranslated_english(
                "DatasetError rateˆβ1 (gap)ˆβ2 (centroid)ˆβ3 (feat. diff.)LRT"
            )
        )
        self.assertFalse(
            _looks_like_untranslated_english(
                "DKimCatching Objects in Flight [19]5IrregularACDHuangDynamic "
                "Handover [18]26RegularGA"
            )
        )
        self.assertFalse(
            _looks_like_untranslated_english(
                "DKim5IrregularACDHuang26RegularGADHuModular NN Catching "
                "[17]18RegularGADZhangCatch It! [42]27RegularGA"
            )
        )
        self.assertFalse(
            _looks_like_untranslated_english(
                "GPT-Image-2 [77]Nano-Banana-2 [78]GPT-Image-1.5 [79]"
                "Seedream-4.5 [80]Flux-2-Pro [81]HunyuanImage-3.0 [82]"
                "Nano-Banana [83]Qwen-Image-Edit-2511 [84]"
            )
        )
        self.assertFalse(
            _looks_like_untranslated_english(
                "robotnesstarget_embodiment_matchinteraction_preservationscene_"
                "preservationreasoning"
            )
        )
        self.assertFalse(
            _looks_like_untranslated_english(
                "Car locomotionInput command 3;actuator targets 8 = 4steer +4 drive."
                "B2 locomotion"
            )
        )
        self.assertFalse(
            _looks_like_untranslated_english(
                "3Projected gravitygbase = Rb ez3Locomotion commandct = [vcmd"
            )
        )
        self.assertFalse(
            _looks_like_untranslated_english("zero.Humanoid locomotion12 leg actions.")
        )
        self.assertFalse(
            _looks_like_untranslated_english(
                "Intercept tracking1¬near exp(−5dI)4.0Approach-to-ballexp(−5d)0.5"
            )
        )
        self.assertFalse(
            _looks_like_untranslated_english(
                "3v3/5v583v3/5v5Ball state[p−p , 0.2v, 1[h = i], pball]"
                "83v3/5v5Teammate states"
            )
        )
        self.assertFalse(
            _looks_like_untranslated_english(
                "Dai, T., Vijayakrishnan, S., Szczypi´nski, F. T., Ayme, J.-"
            )
        )
        self.assertFalse(
            _looks_like_untranslated_english(
                "Darvish, K., Skreta, M., Zhao, Y., Yoshikawa, N., Som, S.,"
            )
        )

    def test_untranslated_detector_still_flags_body_prose(self):
        self.assertTrue(
            _looks_like_untranslated_english(
                "The proposed model improves retrieval quality substantially across "
                "multiple long horizon manipulation tasks."
            )
        )

    def test_formula_fragment_detector_ignores_code_and_table_rows(self):
        self.assertFalse(_looks_like_formula_fragment("nu_p=np.array([1.2]))462"))
        self.assertFalse(_looks_like_formula_fragment("x=[0.0,4.0],486"))
        self.assertFalse(_looks_like_formula_fragment('"score":<1-5>,602'))
        self.assertFalse(_looks_like_formula_fragment("cr1=0.0556"))
        self.assertFalse(_looks_like_formula_fragment("u=fuel_uo2612"))
        self.assertFalse(_looks_like_formula_fragment("4,0±305305π,0±π"))
        self.assertFalse(_looks_like_formula_fragment("Svlm=√"))
        self.assertFalse(_looks_like_formula_fragment("6\x11andδ′∈\x000,1"))
        self.assertFalse(_looks_like_formula_fragment("If⟨V(x),a−z⟩≤0,by(4.4),wehave"))
        self.assertFalse(
            _looks_like_formula_fragment(
                "hence∥x−y−⟨x−y,Ui,j⟩Ui,j∥≤2(∥x−ci∥+∥y−c′j∥)≤4δ2√"
            )
        )
        self.assertFalse(_looks_like_formula_fragment("T∗1f(x)1A(x)dx="))
        self.assertFalse(
            _looks_like_formula_fragment("Handover47/7714/6550/7915/6852/8016/6955/8118/72")
        )
        self.assertTrue(_looks_like_formula_fragment("α+β=γ"))

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

    def test_ignores_multiline_english_reference_section(self):
        original = fitz.open()
        page = original.new_page(width=360, height=360)
        page.insert_text((30, 40), "The proposed model improves retrieval quality substantially.")
        page.insert_text((30, 180), "References", fontsize=12)
        page.insert_text(
            (30, 205),
            "Smith and Doe introduce contrastive learning for dense representations.",
        )

        translated = fitz.open()
        page = translated.new_page(width=360, height=360)
        page.insert_text((30, 40), "The proposed model improves retrieval quality substantially.")
        page.insert_text((30, 180), "参考文献", fontsize=12)
        page.insert_text(
            (30, 205),
            "Smith and Doe introduce contrastive learning for dense representations.",
        )

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
        english_issues = [issue for issue in issues if issue.code == "untranslated_english"]
        self.assertEqual(len(english_issues), 1)
        self.assertIn("1 block", english_issues[0].message)

    def test_ignores_reference_continuation_on_following_page(self):
        original = fitz.open()
        page = original.new_page(width=360, height=360)
        page.insert_text((30, 40), "The proposed model improves retrieval quality substantially.")
        page.insert_text((30, 180), "References", fontsize=12)
        page.insert_text((30, 205), "[1] Smith et al. Learning representations. 2024.")
        page = original.new_page(width=360, height=360)
        page.insert_text(
            (30, 40),
            "Brown Lee Patel. 2023. Visual graph imitation learning benchmark.",
        )

        translated = fitz.open()
        page = translated.new_page(width=360, height=360)
        page.insert_text((30, 40), "The proposed model improves retrieval quality substantially.")
        page.insert_text((30, 180), "参考文献", fontsize=12)
        page.insert_text((30, 205), "[1] Smith et al. Learning representations. 2024.")
        page = translated.new_page(width=360, height=360)
        page.insert_text(
            (30, 40),
            "Brown Lee Patel. 2023. Visual graph imitation learning benchmark.",
        )

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
        english_issues = [issue for issue in issues if issue.code == "untranslated_english"]
        self.assertEqual(len(english_issues), 1)
        self.assertEqual(english_issues[0].page, 1)

    def test_ignores_untranslated_english_inside_visual_region(self):
        original = fitz.open()
        page = original.new_page(width=420, height=320)
        page.draw_rect(fitz.Rect(40, 40, 360, 220))
        page.insert_text(
            (80, 126),
            "Action States Probe best action state layer output",
            fontsize=8,
        )
        page.insert_text((30, 285), "The proposed model improves retrieval quality substantially.")

        translated = fitz.open()
        page = translated.new_page(width=420, height=320)
        page.draw_rect(fitz.Rect(40, 40, 360, 220))
        page.insert_text(
            (80, 126),
            "Action States Probe best action state layer output",
            fontsize=8,
        )
        page.insert_text((30, 285), "该模型显著提升了检索质量。")

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
        self.assertFalse(any(issue.code == "untranslated_english" for issue in issues))

    def test_flags_short_untranslated_english_caption(self):
        original = fitz.open()
        page = original.new_page(width=300, height=220)
        page.insert_text((30, 80), "Figure 1: System overview.")

        translated = fitz.open()
        page = translated.new_page(width=300, height=220)
        page.insert_text((30, 80), "Figure 1: System overview.")

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
        self.assertTrue(any(issue.code == "untranslated_caption" for issue in issues))

    def test_flags_short_untranslated_formula_explanation(self):
        original = fitz.open()
        page = original.new_page(width=300, height=220)
        page.insert_text((30, 80), "where x denotes the input vector.")

        translated = fitz.open()
        page = translated.new_page(width=300, height=220)
        page.insert_text((30, 80), "where x denotes the input vector.")

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
        self.assertTrue(
            any(issue.code == "untranslated_formula_explanation" for issue in issues)
        )

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

    def test_ignores_caption_overlap_when_still_in_source_caption_area(self):
        original = fitz.open()
        page = original.new_page(width=300, height=260)
        page.draw_rect(fitz.Rect(50, 50, 240, 190))
        page.insert_text((50, 166), "Figure 1: Overview of the system.")

        translated = fitz.open()
        page = translated.new_page(width=300, height=260)
        page.draw_rect(fitz.Rect(50, 50, 240, 190))
        page.insert_text((50, 166), "图1：系统概览。")

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
        self.assertFalse(any(issue.code == "caption_overlap" for issue in issues))

    def test_flags_missing_visible_image_blocks(self):
        original = fitz.open()
        page = original.new_page(width=300, height=220)
        pixmap = fitz.Pixmap(fitz.csRGB, fitz.IRect(0, 0, 20, 20), False)
        pixmap.clear_with(0x00FF00)
        page.insert_image(fitz.Rect(40, 50, 180, 130), pixmap=pixmap)
        page.insert_text((30, 170), "This method improves the visual policy.")

        translated = fitz.open()
        page = translated.new_page(width=300, height=220)
        page.insert_text((30, 170), "该方法改进了视觉策略。")

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
        self.assertTrue(any(issue.code == "missing_image" for issue in issues))

    def test_flags_translated_page_count_mismatch(self):
        original = fitz.open()
        page = original.new_page(width=300, height=220)
        page.insert_text((30, 80), "The first page describes the proposed model.")
        page = original.new_page(width=300, height=220)
        page.insert_text((30, 80), "The second page contains evaluation details.")

        translated = fitz.open()
        page = translated.new_page(width=300, height=220)
        page.insert_text((30, 80), "第一页介绍所提出的模型。")

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
        self.assertTrue(any(issue.code == "page_count_mismatch" for issue in issues))

    def test_flags_translated_page_size_mismatch(self):
        original = fitz.open()
        page = original.new_page(width=300, height=220)
        page.draw_rect(fitz.Rect(40, 40, 260, 130), color=(0, 0, 0))
        page.insert_text((30, 170), "The model improves visual policy learning.")

        translated = fitz.open()
        page = translated.new_page(width=150, height=220)
        page.draw_rect(fitz.Rect(20, 40, 130, 130), color=(0, 0, 0))
        page.insert_text((20, 170), "该模型改进了视觉策略学习。")

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
        self.assertTrue(any(issue.code == "page_size_mismatch" for issue in issues))

    def test_visible_image_stats_uses_displayed_image_blocks(self):
        document = fitz.open()
        page = document.new_page(width=300, height=220)
        pixmap = fitz.Pixmap(fitz.csRGB, fitz.IRect(0, 0, 20, 20), False)
        pixmap.clear_with(0x0000FF)
        page.insert_image(fitz.Rect(40, 50, 180, 130), pixmap=pixmap)

        count, area = _visible_image_stats(page)

        document.close()
        self.assertEqual(count, 1)
        self.assertGreater(area, 10000.0)

    def test_visual_min_zone_ignores_text_only_region(self):
        document = fitz.open()
        document.new_page(width=300, height=300)
        visual = SimpleNamespace(
            pages=[SimpleNamespace(page=1, min_zone_score=0.0, zone_scores=(1, 1, 1, 1, 0, 1))]
        )

        intersects = _visual_min_zone_intersects_graphics(document, visual)

        document.close()
        self.assertFalse(intersects)

    def test_visual_min_zone_detects_graphic_region(self):
        document = fitz.open()
        page = document.new_page(width=300, height=300)
        page.draw_rect(fitz.Rect(20, 220, 130, 285))
        visual = SimpleNamespace(
            pages=[SimpleNamespace(page=1, min_zone_score=0.0, zone_scores=(1, 1, 1, 1, 0, 1))]
        )

        intersects = _visual_min_zone_intersects_graphics(document, visual)

        document.close()
        self.assertTrue(intersects)

    def test_formula_fragment_compare_normalizes_fullwidth_punctuation(self):
        self.assertEqual(
            _normalize_formula_fragment_for_compare("(b) K-NN：|Vs| = 100"),
            "(b)K-NN:|Vs|=100",
        )

    def test_formula_fragment_compare_allows_missing_trailing_label(self):
        translated = _normalize_formula_fragment_for_compare("2⟨F(x)(w+w′),w′−w⟩")

        self.assertTrue(_formula_fragment_present("2⟨F(x)(w+w′),w′−w⟩.", translated))
        self.assertTrue(_formula_fragment_present("|α|=d(c(i)α)2:", "|α|=d(c(i)α)2"))
        self.assertTrue(
            _formula_fragment_present("1−ρ2:", _normalize_formula_fragment_for_compare("1−ρ2"))
        )
        self.assertTrue(
            _formula_fragment_present(
                "A∩T∗1f(x)dx=µ(A).(4.58)",
                _normalize_formula_fragment_for_compare("A∩T∗1f(x)dx=µ(A)"),
            )
        )

    def test_clip_block_bbox_against_right_side_float(self):
        clipped = _clip_block_bbox_against_floats(
            (40.0, 100.0, 560.0, 180.0),
            [(390.0, 90.0, 560.0, 210.0)],
            600.0,
        )

        self.assertEqual(clipped, (40.0, 100.0, 387.0, 180.0))

    def test_clip_block_bbox_allows_two_column_body_width(self):
        clipped = _clip_block_bbox_against_floats(
            (107.5, 348.3, 505.2, 525.6),
            [(306.0, 401.4, 505.6, 476.9)],
            612.0,
        )

        self.assertEqual(clipped, (107.5, 348.3, 303.0, 525.6))

    def test_clip_block_bbox_ignores_non_right_side_float(self):
        bbox = (40.0, 100.0, 560.0, 180.0)

        clipped = _clip_block_bbox_against_floats(
            bbox,
            [(170.0, 90.0, 260.0, 210.0)],
            600.0,
        )

        self.assertEqual(clipped, bbox)

    def test_clip_block_bbox_keeps_original_when_too_narrow(self):
        bbox = (40.0, 100.0, 560.0, 180.0)

        clipped = _clip_block_bbox_against_floats(
            bbox,
            [(180.0, 90.0, 560.0, 210.0)],
            600.0,
        )

        self.assertEqual(clipped, bbox)


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
