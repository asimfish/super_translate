"""Tests for conservative native-layout preservation rules."""

import re
import unittest
from pathlib import Path
from types import SimpleNamespace

import fitz

from pdf_zh_translator.pdf_layout import (
    SENTINEL_CLOSE,
    SENTINEL_OPEN,
    SENTINEL_RUN_RE,
    FontPack,
    TextBlock,
    _align_formula_anchors,
    _attach_formula_keepouts,
    _clip_block_bbox_against_floats,
    _equation_table_region_bboxes,
    _extract_formula_fragments,
    _formula_fragment_present,
    _inline_formula_bridge_block,
    _LineRec,
    _looks_like_formula_fragment,
    _looks_like_overlap_exempt_text,
    _looks_like_untranslated_caption,
    _looks_like_untranslated_english,
    _looks_like_untranslated_formula_explanation,
    _normalize_formula_fragment_for_compare,
    _overlap_text_entries_from_block,
    _preserved_text_qa_regions,
    _promote_equation_table_neighbor_blocks,
    _promote_table_component_blocks,
    _RawBlockRec,
    _review_line_number_bboxes,
    _table_region_bboxes,
    _tokenize_translation_with_formula_clips,
    _translated_block_still_english,
    _unresolved_formula_keepouts,
    _uses_fixed_source_math,
    _visible_image_stats,
    _visual_min_zone_intersects_graphics,
    _visual_regions_for_page,
    apply_inline_bold,
    caption_should_center,
    center_caption_bbox,
    classify_blocks,
    clean_translation,
    collect_text_blocks,
    fragmented_prose_warnings_from_units,
    graphic_regions_for_page,
    insert_translated_text,
    is_math_span,
    join_lines,
    line_is_prose,
    mark_bibliography_blocks,
    math_heavy_block,
    merge_paragraph_blocks,
    parse_block_lines,
    prepare_translation_units,
    preserved_original_text_regions,
    preserved_region_text_changed,
    protect_text,
    record_is_algorithm,
    record_is_table,
    redact_original_text,
    relax_caption_boxes,
    requested_translation_font_size,
    segments_from_record,
    should_preserve_original_block,
    strip_sentinels,
    subset_fonts_safely,
    tokenize_text,
    trim_redact_bbox_against_formula_lines,
    verify_translation,
    verify_translation_issues,
)


class PreserveOriginalBlockTests(unittest.TestCase):
    def test_preserve_graphics_mode_keeps_source_fonts_intact(self):
        class DocumentStub:
            def subset_fonts(self):
                raise AssertionError("source fonts must not be subset")

        document = DocumentStub()
        warnings = []

        result = subset_fonts_safely(
            document,
            None,
            warnings,
            preserve_source_fonts=True,
        )

        self.assertIs(result, document)
        self.assertTrue(any("source fonts intact" in warning for warning in warnings))

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

    def test_preserves_guidedvla_diagram_head_labels(self):
        block = TextBlock(
            page_index=0,
            bbox=(120.0, 220.0, 350.0, 238.0),
            text="(i) Object Head (ii) Skill Head (iii) Depth Head",
            font_size=10.0,
            color=(0.0, 0.0, 0.0),
            source_lines=1,
        )

        self.assertTrue(should_preserve_original_block(block, []))

    def test_preserves_memorywam_diagram_memory_labels(self):
        block = TextBlock(
            page_index=0,
            bbox=(117.5, 363.7, 290.1, 371.2),
            text="Event-Boundary Memory Gist Memory Short-Term Memory",
            font_size=9.0,
            color=(0.0, 0.0, 0.0),
            source_lines=1,
        )

        self.assertTrue(should_preserve_original_block(block, []))

    def test_preserves_vertical_arxiv_margin_metadata(self):
        block = TextBlock(
            page_index=0,
            bbox=(10.9, 222.7, 37.6, 569.3),
            text="arXiv:2606.20562v1  [cs.RO]  18 Jun 2026",
            font_size=10.0,
            color=(0.0, 0.0, 0.0),
            source_lines=1,
        )

        self.assertTrue(should_preserve_original_block(block, []))

    def test_preserves_first_page_author_metadata(self):
        block = TextBlock(
            page_index=0,
            bbox=(135.6, 141.3, 516.2, 192.1),
            text=(
                "Sizhe Yang^{1} Juncheng Mu^{2} Tianming Wei^{2} "
                "Zhengrong Xue^{2} 1The Chinese University of Hong Kong "
                "2Tsinghua University 3Zhejiang University"
            ),
            font_size=9.0,
            color=(0.0, 0.0, 0.0),
            source_lines=2,
        )

        self.assertTrue(should_preserve_original_block(block, []))

    def test_does_not_preserve_body_discussion_of_object_head(self):
        block = TextBlock(
            page_index=0,
            bbox=(50.0, 120.0, 290.0, 145.0),
            text="Object Head. The object head supervises visual grounding in the policy.",
            font_size=10.0,
            color=(0.0, 0.0, 0.0),
            source_lines=2,
        )

        self.assertFalse(should_preserve_original_block(block, []))

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

    def test_translates_small_short_body_fragment_outside_graphic_region(self):
        block = TextBlock(
            page_index=1,
            bbox=(91.4, 578.5, 170.1, 588.4),
            text="Contributions. 78",
            font_size=8.0,
            color=(0.0, 0.0, 0.0),
            source_lines=1,
        )

        self.assertFalse(should_preserve_original_block(block, []))

    def test_translates_formula_adjacent_prose_inside_graphic_region(self):
        block = TextBlock(
            page_index=4,
            bbox=(108.0, 390.0, 225.0, 401.7),
            text=(
                "receive predictions closer to "
                f"{SENTINEL_OPEN}^{{1}}{SENTINEL_CLOSE}"
            ),
            font_size=10.0,
            color=(0.0, 0.0, 0.0),
            source_lines=1,
            nowrap=True,
        )

        self.assertFalse(
            should_preserve_original_block(block, [(104.0, 374.0, 230.0, 406.0)])
        )

    def test_preserves_low_font_multiline_prose_inside_diagram(self):
        block = TextBlock(
            page_index=6,
            bbox=(146.1, 149.5, 466.9, 178.7),
            text=(
                "Source Image x Target URDF U Response: the gripper is aligned "
                "with the object before the edit is applied."
            ),
            font_size=7.28,
            color=(0.0, 0.0, 0.0),
            source_lines=4,
        )

        self.assertTrue(
            should_preserve_original_block(block, [(115.4, 59.0, 496.9, 327.7)])
        )

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

    def test_translates_long_prose_overlapping_figure_region_with_math_symbols(self):
        block = TextBlock(
            page_index=0,
            bbox=(107.6, 324.4, 505.2, 477.9),
            text=(
                "Our hardware platform consists of an ARX dual-arm robot and a RealSense "
                "D455 camera that provides RGB observations. We compare MemoryWAM with "
                "two representative baselines: π0.5 [62] and LingBot-VA [7]. We design "
                "two challenging memory-dependent tasks, Shell Game and Look and Press."
            ),
            font_size=10.0,
            color=(0.0, 0.0, 0.0),
            source_lines=5,
        )

        self.assertFalse(
            should_preserve_original_block(block, [(242.6, 302.5, 529.2, 439.5)])
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

    def test_relaxed_caption_keeps_original_redaction_tight(self):
        caption = TextBlock(
            page_index=0,
            bbox=(143.4, 59.1, 468.3, 69.2),
            text="Table 1: Results on RMBench [1].",
            font_size=10.0,
            color=(0.0, 0.0, 0.0),
            block_type="caption",
        )
        table_row = TextBlock(
            page_index=0,
            bbox=(164.8, 95.8, 250.3, 105.8),
            text="Observe and Pick Up",
            font_size=10.0,
            color=(0.0, 0.0, 0.0),
            block_type="table",
            nowrap=True,
            no_merge=True,
        )

        relax_caption_boxes(
            SimpleNamespace(rect=SimpleNamespace(height=792.0)),
            [(caption, "表1"), (table_row, "")],
        )

        self.assertGreater(caption.bbox[3], 69.2)
        self.assertLess(caption.bbox[3], table_row.bbox[1])
        self.assertEqual(caption.redact_bboxes, [(143.4, 59.1, 468.3, 69.2)])

    def test_inline_bold_marks_caption_prefix_and_verbatim_term(self):
        block = TextBlock(
            page_index=0,
            bbox=(0.0, 0.0, 200.0, 40.0),
            text="Figure 1: Overview. MemoryWAM improves efficiency.",
            font_size=10.0,
            color=(0.0, 0.0, 0.0),
            bold_terms=("MemoryWAM",),
            bold_prefix=True,
        )
        tokens = tokenize_text("图1：概览。MemoryWAM 显著提升效率。")

        apply_inline_bold(tokens, block, "图1：概览。MemoryWAM 显著提升效率。")

        self.assertTrue(tokens[0].bold)
        self.assertTrue(any(token.text == "MemoryWAM" and token.bold for token in tokens))
        self.assertTrue(any(token.text == "显" and not token.bold for token in tokens))

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

    def test_parse_block_lines_merges_cross_line_inline_formula_tail(self):
        raw_block = {
            "type": 0,
            "bbox": (108.0, 465.3, 504.3, 489.9),
            "lines": [
                {
                    "bbox": (108.0, 465.3, 504.3, 477.0),
                    "spans": [
                        _span(
                            "During inference, the clean latent",
                            (108.0, 465.5, 242.5, 475.5),
                        ),
                        _span(" z", (242.5, 465.3, 249.6, 475.3), font="CMMI10", flags=6),
                        _span("t", (249.6, 469.2, 252.6, 476.1), size=6.97, font="CMMI7", flags=6),
                        _span(
                            " of the current observation is forwarded through the video DiT",
                            (252.6, 465.5, 504.3, 477.0),
                        ),
                    ],
                },
                {
                    "bbox": (108.0, 476.0, 347.2, 487.5),
                    "spans": [
                        _span(
                            "only once to update the video-side key-value (KV) cache",
                            (108.0, 477.5, 334.9, 487.5),
                        ),
                        _span(" C", (334.9, 477.1, 342.7, 487.1), font="CMSY10", flags=6),
                        _span("v", (343.2, 476.0, 347.2, 483.0), size=6.97, font="CMMI7", flags=7),
                    ],
                },
                {
                    "bbox": (342.7, 477.5, 350.7, 489.9),
                    "spans": [
                        _span("t", (342.7, 482.1, 345.7, 489.0), size=6.97, font="CMMI7", flags=6),
                        _span(" :", (348.0, 477.5, 350.7, 487.5), flags=5),
                    ],
                },
            ],
        }

        record, _ = parse_block_lines(raw_block, page_width=612.0)
        self.assertIsNotNone(record)
        blocks = segments_from_record(0, record)
        text = strip_sentinels(blocks[0].text)
        _protected, mapping = protect_text(blocks[0].text)

        self.assertIn("z_{t}", text)
        self.assertIn("C^{v}", text)
        self.assertIn("_{t}", text)
        self.assertIn("C^{v}", mapping.values())
        self.assertIn("_{t}:", mapping.values())
        self.assertEqual(len(mapping), 3)
        self.assertNotRegex(text, r"[ᵃ-ᵿ₀-ₜ]")

    def test_parse_block_lines_expands_normal_font_formula_operands(self):
        raw_block = {
            "type": 0,
            "bbox": (108.0, 100.0, 500.0, 112.0),
            "lines": [
                {
                    "bbox": (108.0, 100.0, 500.0, 112.0),
                    "spans": [
                        _span("loss track", (108.0, 100.0, 170.0, 112.0)),
                        _span("s ", (170.0, 100.0, 180.0, 112.0)),
                        _span("L", (180.0, 100.0, 188.0, 112.0), font="CMMI10"),
                        _span("(N) = 0.0584 + 0.087/", (188.0, 100.0, 330.0, 112.0)),
                        _span(" w", (330.0, 100.0, 340.0, 112.0)),
                        _span("ith ", (340.0, 100.0, 360.0, 112.0)),
                        _span("R", (360.0, 100.0, 368.0, 112.0), font="CMMI10"),
                        _span("2", (368.0, 98.0, 373.0, 106.0), size=7.0, font="CMR7"),
                        _span(" = 0.975", (373.0, 100.0, 420.0, 112.0)),
                    ],
                }
            ],
        }

        record, _ = parse_block_lines(raw_block, page_width=612.0)

        self.assertIsNotNone(record)
        line = record.lines[0]
        self.assertEqual(len(SENTINEL_RUN_RE.findall(line.text)), 2)
        self.assertEqual(len(line.math_run_bboxes), 2)
        self.assertIn("loss tracks ", line.text)
        self.assertIn(" with ", line.text)
        self.assertRegex(
            line.text,
            re.escape(SENTINEL_CLOSE) + r"\s+with\s+" + re.escape(SENTINEL_OPEN),
        )
        self.assertGreaterEqual(line.math_run_bboxes[0][2], 329.0)

    def test_short_prose_before_display_equation_stays_translatable(self):
        record = _RawBlockRec(
            lines=[
                _line(
                    f"Then the number of cached video tokens after{SENTINEL_OPEN}N{SENTINEL_CLOSE}",
                    (108.0, 276.4, 295.0, 286.6),
                ),
                _line("frames is", (108.0, 288.6, 144.2, 298.6)),
                _line(
                    f"{SENTINEL_OPEN}|C^v_full| = O(NL){SENTINEL_CLOSE}",
                    (167.8, 299.7, 236.3, 313.5),
                ),
            ]
        )

        blocks = segments_from_record(0, record, equation_record=True)
        exported = " ".join(strip_sentinels(block.text) for block in blocks)

        self.assertIn("frames is", exported)


def _span(text, bbox, size=10.0, font="NimbusRomNo9L-Regu", flags=4):
    return {"text": text, "bbox": bbox, "size": size, "font": font, "flags": flags}


def _line(text, bbox, is_cell=False):
    return _LineRec(text=text, bbox=bbox, spans=[_span(text, bbox)], is_cell=is_cell)


class TableDetectionTests(unittest.TestCase):
    def test_equation_table_cells_promote_adjacent_text_but_not_following_prose(self):
        header = TextBlock(
            page_index=0,
            bbox=(286.4, 146.5, 481.9, 157.9),
            text="2 Cos. alignment Degree-grad rho Recovery rate",
            font_size=9.0,
            color=(0.0, 0.0, 0.0),
            block_type="heading",
            source_lines=4,
        )
        row_label = TextBlock(
            page_index=0,
            bbox=(134.1, 186.8, 288.6, 197.3),
            text="Theoretical prediction",
            font_size=9.0,
            color=(0.0, 0.0, 0.0),
        )
        following_prose = TextBlock(
            page_index=0,
            bbox=(108.0, 223.7, 504.0, 244.6),
            text="All three quantitative predictions are confirmed.",
            font_size=9.0,
            color=(0.0, 0.0, 0.0),
        )
        cells = [
            (130.1, 162.6, 157.0, 171.6),
            (173.2, 160.9, 216.4, 171.4),
            (233.8, 162.3, 287.1, 171.4),
            (301.2, 162.3, 354.5, 171.4),
            (365.9, 162.3, 419.2, 171.4),
            (428.6, 162.3, 481.9, 171.4),
            (130.1, 172.6, 160.5, 181.6),
            (447.3, 186.8, 461.9, 197.1),
        ]

        _promote_equation_table_neighbor_blocks(
            [header, row_label, following_prose],
            cells,
        )

        self.assertEqual(header.block_type, "table")
        self.assertFalse(header.should_translate)
        self.assertEqual(row_label.block_type, "table")
        self.assertFalse(row_label.should_translate)
        self.assertEqual(following_prose.block_type, "body")
        self.assertTrue(following_prose.should_translate)

    def test_handedit_formula_neighbors_remain_translatable_prose(self):
        renderer_description = TextBlock(
            page_index=6,
            bbox=(107.6, 383.7, 504.0, 404.5),
            text=(
                "We use a frozen image encoder and define the camera pose for "
                "all rendered views:"
            ),
            font_size=9.0,
            color=(0.0, 0.0, 0.0),
            source_lines=2,
        )
        hyperparameters = TextBlock(
            page_index=6,
            bbox=(107.6, 535.3, 504.0, 557.7),
            text=(
                "We use lambda equal to 0.5 for all experiments and set the "
                "Lab-space threshold to 25 in all experiments."
            ),
            font_size=9.0,
            color=(0.0, 0.0, 0.0),
            source_lines=2,
        )

        _promote_equation_table_neighbor_blocks(
            [renderer_description, hyperparameters],
            [
                (235.6, 406.1, 384.3, 443.2),
                (220.0, 505.0, 392.0, 536.0),
            ],
        )

        self.assertEqual(renderer_description.block_type, "body")
        self.assertTrue(renderer_description.should_translate)
        self.assertEqual(hyperparameters.block_type, "body")
        self.assertTrue(hyperparameters.should_translate)

    def test_handedit_long_rubric_cell_inside_table_is_preserved(self):
        rubric_cell = TextBlock(
            page_index=23,
            bbox=(204.0, 128.0, 332.0, 174.0),
            text=(
                "Robot cues are visible, but key morphology, material, or color "
                "details are wrong."
            ),
            font_size=8.0,
            color=(0.0, 0.0, 0.0),
            source_lines=4,
        )

        _promote_equation_table_neighbor_blocks(
            [rubric_cell],
            [
                (107.7, 105.4, 204.0, 232.9),
                (204.0, 105.4, 332.0, 232.9),
                (332.0, 105.4, 504.0, 232.9),
            ],
        )

        self.assertEqual(rubric_cell.block_type, "table")
        self.assertFalse(rubric_cell.should_translate)

    def test_long_multiline_cell_inside_caption_anchored_table_is_preserved(self):
        caption = TextBlock(
            page_index=23,
            bbox=(107.7, 79.0, 504.0, 99.9),
            text="Table 14: Rating guide for human evaluation.",
            font_size=9.96,
            color=(0.0, 0.0, 0.0),
            block_type="caption",
        )
        header = TextBlock(
            page_index=23,
            bbox=(112.0, 105.4, 128.9, 114.4),
            text="Axis",
            font_size=8.97,
            color=(0.0, 0.0, 0.0),
            block_type="table",
            should_translate=False,
        )
        anchor_cell = TextBlock(
            page_index=23,
            bbox=(307.5, 121.9, 401.5, 160.7),
            text="Robot cues are visible, but key morphology details are wrong.",
            font_size=8.97,
            color=(0.0, 0.0, 0.0),
            block_type="table",
            should_translate=False,
            source_lines=4,
        )
        long_cell = TextBlock(
            page_index=23,
            bbox=(112.0, 121.9, 301.4, 160.7),
            text=(
                "Target-robot correctness. The requested robot is missing, "
                "mostly humanlike, or clearly the wrong embodiment."
            ),
            font_size=8.97,
            color=(0.0, 0.0, 0.0),
            source_lines=5,
        )

        _promote_table_component_blocks([caption, header, anchor_cell, long_cell])

        self.assertEqual(long_cell.block_type, "table")
        self.assertFalse(long_cell.should_translate)

    def test_cap_sat_training_header_is_detected_as_table(self):
        record = _RawBlockRec(
            lines=[
                _line("Training regime", (124.3, 338.3, 200.0, 347.5)),
                _line("Training signal", (268.9, 338.3, 355.0, 347.5)),
                _line("Accuracy", (430.0, 338.3, 481.7, 347.5)),
            ]
        )

        self.assertTrue(record_is_table(record))

    def test_cap_sat_walksat_header_is_detected_as_table(self):
        record = _RawBlockRec(
            lines=[
                _line("Variables", (190.7, 662.5, 226.8, 671.4)),
                _line("Neural Init", (238.7, 662.5, 281.3, 671.4)),
                _line("Random Init", (293.3, 662.5, 342.3, 671.4)),
                _line("Reduction", (354.3, 662.5, 393.6, 671.4)),
                _line("Inference", (405.6, 662.5, 441.8, 671.4)),
            ]
        )

        self.assertTrue(record_is_table(record))

    def test_cap_sat_scaling_header_with_abbreviation_is_table(self):
        record = _RawBlockRec(
            lines=[
                _line("Instances", (158.8, 595.6, 194.7, 604.6)),
                _line("Labels", (216.2, 595.6, 241.6, 604.6)),
                _line("Mean Acc", (263.2, 595.6, 301.8, 604.6)),
                _line("Dispersion/status", (313.7, 595.6, 379.5, 604.6)),
                _line("Gap to best sup.", (391.4, 595.6, 453.2, 604.6)),
            ]
        )

        self.assertTrue(record_is_table(record))

    def test_cap_sat_supervised_reference_row_is_table(self):
        record = _RawBlockRec(
            lines=[
                _line(
                    "Reference (fully supervised, G4SATBench [5]):",
                    (158.8, 675.7, 328.1, 684.7),
                ),
                _line("SGC", (177.2, 687.8, 194.7, 696.8)),
                _line("100% (18K)", (206.6, 687.8, 251.2, 696.8)),
                _line("0.723", (272.4, 687.8, 292.5, 696.8)),
                _line("ref.", (340.5, 687.8, 352.7, 696.8)),
                _line("0.0 pp", (391.4, 687.6, 414.4, 696.8)),
            ]
        )

        self.assertTrue(record_is_table(record))

    def test_equation_marked_numeric_table_is_exposed_as_preserved_region(self):
        record = _RawBlockRec(
            lines=[
                _line("500", (181.2, 610.9, 194.7, 619.9)),
                _line("0%", (222.9, 610.9, 234.9, 619.9)),
                _line("0.705", (272.4, 610.9, 292.5, 619.9)),
                _line("0.001", (336.5, 610.9, 356.7, 619.9)),
                _line("1.8 pp", (391.4, 610.7, 414.4, 619.9)),
            ]
        )

        regions = _equation_table_region_bboxes([record], [True])

        self.assertEqual(regions, [line.bbox for line in record.lines])
        self.assertEqual(_equation_table_region_bboxes([record], [False]), [])

    def test_single_row_table_header_is_detected(self):
        record = _RawBlockRec(
            lines=[
                _line("Task", (115.4, 271.4, 132.8, 280.0)),
                _line("w/o Anchor Frames", (173.2, 271.4, 246.2, 280.0)),
                _line("w/o Gist Tokens", (256.6, 271.4, 315.3, 280.0)),
                _line("w/o Sliding Window", (325.7, 271.4, 400.0, 280.0)),
                _line("Full Attention", (410.3, 271.4, 462.5, 280.0)),
                _line("Ours", (475.4, 271.4, 494.1, 280.0)),
            ]
        )

        self.assertTrue(record_is_table(record))

    def test_single_row_table_header_with_subscript_metric_is_detected(self):
        record = _RawBlockRec(
            lines=[
                _line("Task", (164.8, 79.4, 185.1, 89.5)),
                _line("π₀.₅", (266.4, 79.3, 285.0, 90.2)),
                _line("FastWAM", (301.1, 79.4, 344.9, 89.5)),
                _line("Lingbot-VA", (356.9, 79.4, 407.5, 89.5)),
                _line("Ours", (422.5, 79.4, 444.3, 89.5)),
            ]
        )

        self.assertTrue(record_is_table(record))

    def test_single_row_table_header_with_protected_script_metric_is_detected(self):
        record = _RawBlockRec(
            lines=[
                _line("Task", (164.8, 79.4, 185.1, 89.5)),
                _line(
                    f"{SENTINEL_OPEN}π{SENTINEL_CLOSE}"
                    f"{SENTINEL_OPEN}_{{0}}{SENTINEL_CLOSE}"
                    f"{SENTINEL_OPEN}_{{.}}{SENTINEL_CLOSE}"
                    f"{SENTINEL_OPEN}_{{5}}{SENTINEL_CLOSE}",
                    (266.4, 79.3, 285.0, 90.2),
                ),
                _line("FastWAM", (301.1, 79.4, 344.9, 89.5)),
                _line("Lingbot-VA", (356.9, 79.4, 407.5, 89.5)),
                _line("Ours", (422.5, 79.4, 444.3, 89.5)),
            ]
        )

        blocks = segments_from_record(0, record)

        self.assertTrue(record_is_table(record))
        self.assertTrue(all(block.block_type == "table" for block in blocks))

    def test_single_row_table_summary_is_detected(self):
        record = _RawBlockRec(
            lines=[
                _line("Average", (115.4, 308.8, 143.9, 317.5)),
                _line("74.0%", (198.4, 308.7, 221.0, 317.3)),
                _line("40%", (278.0, 308.7, 293.9, 317.3)),
                _line("82.5%", (351.5, 308.7, 374.1, 317.3)),
                _line("91.5%", (425.1, 308.7, 447.7, 317.3)),
                _line("92.5%", (472.9, 308.8, 496.6, 317.5)),
            ]
        )

        blocks = segments_from_record(0, record)

        self.assertTrue(record_is_table(record))
        self.assertTrue(all(block.block_type == "table" for block in blocks))
        self.assertTrue(all(block.nowrap and block.no_merge for block in blocks))

    def test_single_row_author_list_is_not_table(self):
        record = _RawBlockRec(
            lines=[
                _line("Sizhe Yang", (114.0, 141.2, 171.4, 151.5)),
                _line("Juncheng Mu", (181.9, 141.2, 250.6, 151.5)),
                _line("Tianming Wei", (261.0, 141.3, 325.3, 151.5)),
                _line("Chenhao Lu", (335.7, 141.2, 394.7, 151.5)),
                _line("Xiaofan Li", (405.1, 141.2, 456.0, 151.5)),
                _line("Linning Xu", (466.3, 141.2, 516.2, 151.5)),
            ]
        )

        self.assertFalse(record_is_table(record))

    def test_algorithm_title_block_is_preserved(self):
        record = _RawBlockRec(
            lines=[
                _line(
                    "Algorithm 1 Decoupled Attention with Guided Heads Require: hidden states h",
                    (312.0, 402.2, 535.4, 414.2),
                )
            ]
        )

        self.assertTrue(record_is_algorithm(record))

    def test_algorithm_stage_line_is_preserved(self):
        record = _RawBlockRec(
            lines=[
                _line(
                    "Stage 2: Per-Head Supervision",
                    (327.2, 527.6, 440.0, 536.5),
                )
            ]
        )

        self.assertTrue(record_is_algorithm(record))

    def test_python_style_algorithm_block_is_preserved(self):
        record = _RawBlockRec(
            lines=[
                _LineRec(
                    text="def masked_mean(loss, valid):",
                    bbox=(64.0, 120.0, 240.0, 132.0),
                    spans=[
                        _span(
                            "def masked_mean(loss, valid):",
                            (64.0, 120.0, 240.0, 132.0),
                            font="Inconsolata",
                        )
                    ],
                ),
                _LineRec(
                    text="    loss = loss * valid",
                    bbox=(64.0, 134.0, 220.0, 146.0),
                    spans=[
                        _span(
                            "    loss = loss * valid",
                            (64.0, 134.0, 220.0, 146.0),
                            font="Inconsolata",
                        )
                    ],
                ),
                _LineRec(
                    text="    return loss.sum() / valid.sum()",
                    bbox=(64.0, 148.0, 280.0, 160.0),
                    spans=[
                        _span(
                            "    return loss.sum() / valid.sum()",
                            (64.0, 148.0, 280.0, 160.0),
                            font="Inconsolata",
                        )
                    ],
                ),
            ]
        )

        self.assertTrue(record_is_algorithm(record))

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
        self.assertFalse(block.should_translate)


class FragmentedProseWarningTests(unittest.TestCase):
    def test_detects_indented_neurips_review_line_number_sequence(self):
        raw_blocks = []
        for offset, number in enumerate(range(78, 83)):
            y = 578.5 + offset * 11.0
            body_bbox = (108.0, y, 468.0, y + 10.0)
            number_bbox = (91.4, y + 3.0, 98.0, y + 9.0)
            body_text = (
                "Contributions."
                if number == 78
                else "A review-paper prose line with enough width."
            )
            raw_blocks.append(
                {
                    "type": 0,
                    "lines": [
                        {
                            "bbox": body_bbox,
                            "spans": [_span(body_text, body_bbox)],
                        },
                        {
                            "bbox": number_bbox,
                            "spans": [_span(str(number), number_bbox, size=6.0)],
                        },
                    ],
                }
            )

        gutter_bboxes = _review_line_number_bboxes({"blocks": raw_blocks})
        record, dropped = parse_block_lines(
            raw_blocks[0],
            page_width=612.0,
            known_gutter_bboxes=gutter_bboxes,
        )

        self.assertEqual(len(gutter_bboxes), 5)
        self.assertEqual(dropped, [(91.4, 581.5, 98.0, 587.5)])
        self.assertIsNotNone(record)
        assert record is not None
        self.assertNotIn("78", record.bare_text())

    def test_does_not_detect_nonconsecutive_plot_ticks_as_review_line_numbers(self):
        raw_blocks = []
        for offset, number in enumerate((20, 40, 60, 80)):
            y = 100.0 + offset * 20.0
            label_bbox = (180.0, y, 280.0, y + 9.0)
            tick_bbox = (160.0, y + 2.0, 174.0, y + 8.0)
            raw_blocks.append(
                {
                    "type": 0,
                    "lines": [
                        {
                            "bbox": label_bbox,
                            "spans": [_span("Evaluation category", label_bbox)],
                        },
                        {
                            "bbox": tick_bbox,
                            "spans": [_span(str(number), tick_bbox, size=6.0)],
                        },
                    ],
                }
            )

        self.assertEqual(_review_line_number_bboxes({"blocks": raw_blocks}), [])

    def test_formula_qa_excludes_confirmed_review_line_numbers(self):
        raw_blocks = []
        for offset, number in enumerate(range(157, 162)):
            y = 180.0 + offset * 11.0
            text_bbox = (108.0, y, 468.0, y + 10.0)
            number_bbox = (91.4, y + 3.0, 98.0, y + 9.0)
            text = "α + β = γ" if offset == 0 else "A review prose line for sequence detection."
            raw_blocks.append(
                {
                    "type": 0,
                    "lines": [
                        {
                            "bbox": text_bbox,
                            "spans": [_span(text, text_bbox)],
                        },
                        {
                            "bbox": number_bbox,
                            "spans": [_span(str(number), number_bbox, size=6.0)],
                        },
                    ],
                }
            )

        fragments = _extract_formula_fragments(
            SimpleNamespace(),
            blocks=raw_blocks,
        )

        self.assertIn("α+β=γ", fragments)
        self.assertTrue(all("157" not in fragment for fragment in fragments))

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
    def test_handedit_multiline_inline_formula_keeps_trailing_prose(self):
        prefix = _line(
            f"requested embodiment. Denote {SENTINEL_OPEN}{{(Iref{SENTINEL_CLOSE}",
            (116.5, 688.1, 387.4, 706.7),
        )
        prefix.math_bboxes = [(360.5, 688.1, 387.4, 706.7)]
        prefix.math_run_bboxes = list(prefix.math_bboxes)
        superscript = _line(
            f"{SENTINEL_OPEN}v, Mref{SENTINEL_CLOSE}",
            (377.5, 688.1, 412.7, 701.8),
        )
        closing = _line(
            f"{SENTINEL_OPEN}v )}} V{SENTINEL_CLOSE}",
            (402.5, 688.1, 427.3, 706.7),
        )
        trailing = _LineRec(
            text=f"{SENTINEL_OPEN}v=1{SENTINEL_CLOSE} as the rendered",
            bbox=(422.6, 689.6, 504.0, 702.0),
            spans=[
                _span("v", (422.6, 694.2, 426.6, 701.1), size=6.97, font="CMMI7"),
                _span("=1", (426.9, 694.2, 437.0, 701.1), size=6.97, font="CMR7"),
                _span(
                    " as the rendered",
                    (437.0, 689.6, 504.0, 699.6),
                    font="NimbusRomNo9L-Regu",
                ),
            ],
        )
        for line in (superscript, closing, trailing):
            line.math_bboxes = [line.bbox]
            line.math_run_bboxes = [line.bbox]

        segments = segments_from_record(
            5,
            _RawBlockRec(lines=[prefix, superscript, closing, trailing]),
            equation_record=True,
        )

        self.assertEqual(len(segments), 1)
        self.assertIn("as the rendered", strip_sentinels(segments[0].text))
        self.assertIn("v=1", strip_sentinels(segments[0].text))
        self.assertEqual(segments[0].source_lines, 4)

    def test_adjacent_display_formula_keepout_above_body_is_ignored(self):
        keepout = (243.4, 406.1, 248.0, 443.2)
        block = TextBlock(
            page_index=6,
            bbox=(116.5, 434.2, 504.2, 499.6),
            text="Unlike the structural term, the score is not computed against pseudo-GT.",
            font_size=9.96,
            color=(0.0, 0.0, 0.0),
            keepout_bboxes=[keepout],
        )

        self.assertEqual(_unresolved_formula_keepouts(block), [])

    def test_formula_keepout_centered_inside_body_remains_active(self):
        keepout = (220.0, 104.0, 240.0, 118.0)
        block = TextBlock(
            page_index=0,
            bbox=(80.0, 90.0, 260.0, 130.0),
            text="Prose surrounding an unresolved formula fragment.",
            font_size=10.0,
            color=(0.0, 0.0, 0.0),
            keepout_bboxes=[keepout],
        )

        self.assertEqual(_unresolved_formula_keepouts(block), [keepout])

    def test_source_line_covered_formula_keepout_is_resolved(self):
        keepout = (100.0, 100.0, 220.0, 112.0)
        block = TextBlock(
            page_index=0,
            bbox=(80.0, 90.0, 260.0, 130.0),
            text="formula bridge",
            font_size=10.0,
            color=(0.0, 0.0, 0.0),
            source_line_bboxes=(keepout,),
            source_math_bboxes=((170.0, 100.0, 220.0, 112.0),),
            keepout_bboxes=[keepout],
        )

        self.assertEqual(_unresolved_formula_keepouts(block), [])

    def test_inline_formula_record_bridges_adjacent_prose_records(self):
        previous = _RawBlockRec(
            lines=[_line("we fit the form L(N) =", (108.0, 100.0, 504.0, 111.0))]
        )
        formula_line = _line(
            f"{SENTINEL_OPEN}L_inf+c/{SENTINEL_CLOSE}",
            (108.0, 109.0, 145.0, 121.0),
        )
        formula_line.math_bboxes = [formula_line.bbox]
        formula_line.math_run_bboxes = [formula_line.bbox]
        root_line = _line(
            f"{SENTINEL_OPEN}sqrt{SENTINEL_CLOSE}",
            (145.0, 102.0, 153.0, 112.0),
        )
        root_line.math_bboxes = [root_line.bbox]
        root_line.math_run_bboxes = [root_line.bbox]
        bridge_record = _RawBlockRec(lines=[formula_line, root_line])
        following = _RawBlockRec(
            lines=[
                _line(
                    f"{SENTINEL_OPEN}N{SENTINEL_CLOSE} to our training runs",
                    (153.0, 109.0, 504.0, 121.0),
                )
            ]
        )

        bridge = _inline_formula_bridge_block(
            0,
            [previous, bridge_record, following],
            [True, True, True],
            [False, False, False],
            1,
        )

        self.assertIsNotNone(bridge)
        self.assertIn("L_inf+c/", strip_sentinels(bridge.text))
        self.assertIn("sqrt", strip_sentinels(bridge.text))
        self.assertEqual(len(bridge.source_math_bboxes), 2)

    def test_short_formula_fragment_bridges_nearby_formula_rich_prose(self):
        prose = _RawBlockRec(
            lines=[
                _line(
                    "Denote the requested morphology as "
                    f"{SENTINEL_OPEN}I^ref{SENTINEL_CLOSE}",
                    (108.0, 100.0, 230.0, 112.0),
                )
            ]
        )
        first_fragment = _RawBlockRec(
            lines=[
                _line(
                    f"{SENTINEL_OPEN}_(v){SENTINEL_CLOSE}",
                    (230.0, 100.0, 246.0, 112.0),
                )
            ]
        )
        target_line = _line(
            f"{SENTINEL_OPEN},M^ref_v{SENTINEL_CLOSE}",
            (246.0, 99.5, 278.0, 112.5),
        )
        target_line.math_bboxes = [target_line.bbox]
        target_line.math_run_bboxes = [target_line.bbox]
        target = _RawBlockRec(lines=[target_line])
        final_fragment = _RawBlockRec(
            lines=[
                _line(
                    f"{SENTINEL_OPEN}v=1{SENTINEL_CLOSE}",
                    (278.0, 100.0, 300.0, 112.0),
                )
            ]
        )

        bridge = _inline_formula_bridge_block(
            0,
            [prose, first_fragment, target, final_fragment],
            [True, True, True, True],
            [False, False, False, False],
            2,
        )

        self.assertIsNotNone(bridge)
        self.assertEqual(strip_sentinels(bridge.text), ",M^ref_v")
        self.assertEqual(bridge.source_math_bboxes, (target_line.bbox,))

    def test_numbered_display_formula_is_not_exposed_as_nearby_fragment(self):
        prose = _RawBlockRec(
            lines=[
                _line(
                    "The objective contains "
                    f"{SENTINEL_OPEN}L_total{SENTINEL_CLOSE}",
                    (108.0, 100.0, 210.0, 112.0),
                )
            ]
        )
        spacer = _RawBlockRec(
            lines=[
                _line(
                    f"{SENTINEL_OPEN}+lambda L_aux{SENTINEL_CLOSE}",
                    (210.0, 100.0, 255.0, 112.0),
                )
            ]
        )
        display = _RawBlockRec(
            lines=[
                _line(
                    f"{SENTINEL_OPEN}L=L_main+lambda L_aux{SENTINEL_CLOSE}",
                    (255.0, 99.0, 420.0, 113.0),
                ),
                _line("(4)", (480.0, 100.0, 498.0, 112.0)),
            ]
        )

        bridge = _inline_formula_bridge_block(
            0,
            [prose, spacer, display],
            [True, True, True],
            [False, False, False],
            2,
        )

        self.assertIsNone(bridge)

    def test_tall_display_delimiter_is_not_exposed_as_inline_fragment(self):
        equation_body = _RawBlockRec(
            lines=[
                _line(
                    f"{SENTINEL_OPEN}cos(phi(x),phi(y)){SENTINEL_CLOSE}",
                    (280.0, 108.0, 422.0, 121.0),
                )
            ]
        )
        tall_delimiter = _RawBlockRec(
            lines=[
                _line(
                    f"{SENTINEL_OPEN})){SENTINEL_CLOSE}",
                    (422.0, 100.0, 432.0, 137.0),
                )
            ]
        )
        formula_tail = _RawBlockRec(
            lines=[
                _line(
                    f"{SENTINEL_OPEN}.{SENTINEL_CLOSE}",
                    (432.0, 108.0, 438.0, 121.0),
                )
            ]
        )
        following_prose = _RawBlockRec(
            lines=[
                _line(
                    "Unlike the structural term, "
                    f"{SENTINEL_OPEN}S_ref{SENTINEL_CLOSE} is not computed against pseudo-GT.",
                    (116.0, 128.0, 504.0, 140.0),
                )
            ]
        )

        bridge = _inline_formula_bridge_block(
            0,
            [equation_body, tall_delimiter, formula_tail, following_prose],
            [True, True, True, True],
            [False, False, False, False],
            1,
        )

        self.assertIsNone(bridge)

    def test_display_formula_record_is_not_an_inline_bridge(self):
        previous = _RawBlockRec(
            lines=[_line("where we use", (108.0, 100.0, 170.0, 111.0))]
        )
        display = _RawBlockRec(
            lines=[
                _line(
                    f"{SENTINEL_OPEN}x=y+z{SENTINEL_CLOSE}",
                    (220.0, 118.0, 360.0, 132.0),
                )
            ]
        )
        following = _RawBlockRec(
            lines=[
                _line(
                    f"{SENTINEL_OPEN}x{SENTINEL_CLOSE} is the result",
                    (108.0, 140.0, 250.0, 151.0),
                )
            ]
        )

        bridge = _inline_formula_bridge_block(
            0,
            [previous, display, following],
            [True, True, True],
            [False, False, False],
            1,
        )

        self.assertIsNone(bridge)

    def test_formula_keepouts_do_not_pin_captured_source_math(self):
        formula_bboxes = tuple(
            (100.0 + index * 12.0, 100.0, 108.0 + index * 12.0, 112.0)
            for index in range(5)
        )
        resolved_block = TextBlock(
            page_index=0,
            bbox=(80.0, 90.0, 260.0, 130.0),
            text="formula-rich prose",
            font_size=10.0,
            color=(0.0, 0.0, 0.0),
            source_math_bboxes=formula_bboxes,
            formula_anchors=formula_bboxes,
            keepout_bboxes=[formula_bboxes[0]],
        )
        unresolved_block = TextBlock(
            page_index=0,
            bbox=(80.0, 90.0, 260.0, 130.0),
            text="formula with an external fragment",
            font_size=10.0,
            color=(0.0, 0.0, 0.0),
            source_math_bboxes=formula_bboxes,
            formula_anchors=formula_bboxes,
            keepout_bboxes=[(220.0, 100.0, 240.0, 112.0)],
        )

        self.assertFalse(_uses_fixed_source_math(resolved_block))
        self.assertFalse(_uses_fixed_source_math(unresolved_block))

    def test_fixed_source_math_survives_redaction_and_is_not_duplicated(self):
        document = fitz.open()
        page = document.new_page(width=300, height=160)
        page.insert_text((40.0, 60.0), "LEFT", fontsize=10.0)
        page.insert_text((140.0, 60.0), "x", fontsize=10.0)
        page.insert_text((200.0, 60.0), "RIGHT", fontsize=10.0)
        left_bbox = tuple(page.search_for("LEFT")[0])
        formula_bbox = tuple(page.search_for("x")[0])
        right_bbox = tuple(page.search_for("RIGHT")[0])
        block = TextBlock(
            page_index=0,
            bbox=(35.0, 45.0, 260.0, 75.0),
            text=f"LEFT {SENTINEL_OPEN}x{SENTINEL_CLOSE} RIGHT",
            font_size=10.0,
            color=(0.0, 0.0, 0.0),
            source_lines=4,
            redact_bboxes=[left_bbox, formula_bbox, right_bbox],
            keepout_bboxes=[(270.0, 45.0, 280.0, 65.0)],
            source_line_bboxes=((35.0, 45.0, 260.0, 65.0),),
            source_math_bboxes=(formula_bbox,),
            formula_anchors=(formula_bbox,),
        )
        font = fitz.Font("helv")
        font_pack = FontPack(
            regular=font,
            regular_file=Path(""),
            bold=font,
            bold_file=Path(""),
            regular_alias="helv",
            bold_alias="helv",
        )

        redact_original_text(page, [block], margin=0.1)
        inserted = insert_translated_text(
            page=page,
            block=block,
            text=f"Translated {SENTINEL_OPEN}x{SENTINEL_CLOSE} result",
            font_pack=font_pack,
            font_size=10.0,
            min_font_size=5.0,
            margin=0.1,
        )

        extracted = page.get_text("text")
        document.close()
        self.assertTrue(inserted)
        self.assertIn("Translated", extracted)
        self.assertIn("result", extracted)
        self.assertEqual(extracted.split().count("x"), 1)

    def test_join_lines_keeps_cross_line_formula_runs_separate(self):
        joined = join_lines(
            [
                f"prefix {SENTINEL_OPEN}c/{SENTINEL_CLOSE}",
                f"{SENTINEL_OPEN}N{SENTINEL_CLOSE} suffix",
            ]
        )

        self.assertEqual(len(SENTINEL_RUN_RE.findall(joined)), 2)
        self.assertEqual(strip_sentinels(joined).split(), ["prefix", "c/", "N", "suffix"])

    def test_join_lines_keeps_normal_spacing(self):
        self.assertEqual(join_lines(["first line", "second line"]), "first line second line")

    def test_join_lines_preserves_known_academic_compound_hyphen(self):
        self.assertEqual(
            join_lines(["We train a vision-", "language model for control."]),
            "We train a vision-language model for control.",
        )

    def test_join_lines_still_mends_split_word(self):
        self.assertEqual(
            join_lines(["The experi-", "ments show consistent gains."]),
            "The experiments show consistent gains.",
        )

    def test_line_break_hyphen_ignores_unrelated_term_earlier_in_context(self):
        from pdf_zh_translator.pdf_layout import _line_break_hyphen_belongs_to_term

        self.assertFalse(
            _line_break_hyphen_belongs_to_term(
                "We use chain-of-thought reasoning. The proof-of-",
                "thought experiment confirms the result.",
            )
        )

    def test_formula_anchor_alignment_requires_exact_count(self):
        anchors = (
            (100.0, 100.0, 110.0, 110.0),
            (112.0, 100.0, 122.0, 110.0),
        )

        self.assertEqual(_align_formula_anchors(anchors, 2), anchors)
        self.assertEqual(_align_formula_anchors(anchors, 1), ())

    def test_formula_tokenizer_falls_back_to_visible_text_when_anchors_mismatch(self):
        block = TextBlock(
            page_index=0,
            bbox=(100.0, 100.0, 300.0, 130.0),
            text="formula",
            font_size=10.0,
            color=(0.0, 0.0, 0.0),
            formula_anchors=(),
        )

        tokens = _tokenize_translation_with_formula_clips(
            f"中文 {SENTINEL_OPEN}x^{{2}}{SENTINEL_CLOSE} 结果",
            block,
        )

        rendered = "".join(token.text for token in tokens)
        self.assertIn("x^{2}", rendered)
        self.assertNotIn(SENTINEL_OPEN, rendered)
        self.assertTrue(all(token.kind != "formula" for token in tokens))

    def test_formula_tokenizer_groups_contiguous_vector_formula_pieces(self):
        anchors = (
            (100.0, 100.0, 120.0, 110.0),
            (120.0, 92.0, 128.0, 102.0),
            (128.0, 100.0, 138.0, 110.0),
        )
        block = TextBlock(
            page_index=0,
            bbox=(80.0, 90.0, 260.0, 130.0),
            text="formula",
            font_size=10.0,
            color=(0.0, 0.0, 0.0),
            formula_anchors=anchors,
        )

        tokens = _tokenize_translation_with_formula_clips(
            (
                f"结果 {SENTINEL_OPEN}c/{SENTINEL_CLOSE}  "
                f"{SENTINEL_OPEN}√{SENTINEL_CLOSE}  "
                f"{SENTINEL_OPEN}N{SENTINEL_CLOSE} 成立"
            ),
            block,
        )

        formulas = [token for token in tokens if token.kind == "formula"]
        self.assertEqual(len(formulas), 1)
        self.assertEqual(formulas[0].text, "c/ √ N")
        self.assertEqual(formulas[0].source_bbox, (100.0, 92.0, 138.0, 110.0))

    def test_formula_tokenizer_keeps_prose_connector_between_formulas(self):
        anchors = (
            (100.0, 100.0, 110.0, 110.0),
            (140.0, 100.0, 190.0, 110.0),
        )
        block = TextBlock(
            page_index=0,
            bbox=(80.0, 90.0, 260.0, 130.0),
            text="formula",
            font_size=10.0,
            color=(0.0, 0.0, 0.0),
            formula_anchors=anchors,
        )

        tokens = _tokenize_translation_with_formula_clips(
            (
                f"{SENTINEL_OPEN}N{SENTINEL_CLOSE} with "
                f"{SENTINEL_OPEN}R^2=0.975{SENTINEL_CLOSE}"
            ),
            block,
        )

        self.assertEqual(sum(token.kind == "formula" for token in tokens), 2)
        self.assertIn("with", "".join(token.text for token in tokens))

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

    def test_metric_comparison_with_formula_numbers_is_prose(self):
        line = _LineRec(
            text=(
                f"2 is{SENTINEL_OPEN}0.442{SENTINEL_CLOSE}"
                f"{SENTINEL_OPEN}±0.134{SENTINEL_CLOSE} (planted) versus"
                f"{SENTINEL_OPEN}0.001±0.199{SENTINEL_CLOSE}"
            ),
            bbox=(316.7, 449.7, 504.3, 463.4),
            spans=[],
        )

        self.assertTrue(line_is_prose(line))

    def test_short_connector_between_formula_runs_is_prose(self):
        line = _LineRec(
            text=(
                f"{SENTINEL_OPEN}N{SENTINEL_CLOSE} with"
                f"{SENTINEL_OPEN}R^2 = 0.975{SENTINEL_CLOSE}"
            ),
            bbox=(280.0, 100.0, 430.0, 112.0),
            spans=[],
        )

        self.assertTrue(line_is_prose(line))

    def test_equation_record_keeps_wrapped_formula_suffix_after_prose(self):
        prose = _line(
            "Both quantities show a decay rate consistent with the",
            (108.0, 100.0, 504.0, 111.0),
        )
        formula_prefix = _line(
            f"{SENTINEL_OPEN}O(1/{SENTINEL_CLOSE}",
            (108.0, 109.5, 130.0, 121.0),
        )
        formula_root = _line(
            f"{SENTINEL_OPEN}sqrt{SENTINEL_CLOSE}",
            (130.0, 101.0, 140.0, 110.0),
        )

        blocks = segments_from_record(
            0,
            _RawBlockRec(lines=[prose, formula_prefix, formula_root]),
            equation_record=True,
        )

        self.assertEqual(len(blocks), 1)
        self.assertIn("O(1/", strip_sentinels(blocks[0].text))
        self.assertIn("sqrt", strip_sentinels(blocks[0].text))

    def test_equation_record_does_not_merge_independent_display_formula(self):
        prose = _line("This completes the proof.", (108.0, 100.0, 260.0, 111.0))
        display = _line(
            f"{SENTINEL_OPEN}x = y + z{SENTINEL_CLOSE}",
            (220.0, 118.0, 360.0, 132.0),
        )

        blocks = segments_from_record(
            0,
            _RawBlockRec(lines=[prose, display]),
            equation_record=True,
        )

        self.assertEqual(len(blocks), 1)
        self.assertNotIn("x = y + z", strip_sentinels(blocks[0].text))

    def test_pure_numeric_comparison_is_not_prose(self):
        line = _LineRec(
            text=(
                f"{SENTINEL_OPEN}0.442±0.134{SENTINEL_CLOSE} versus "
                f"{SENTINEL_OPEN}0.001±0.199{SENTINEL_CLOSE}"
            ),
            bbox=(316.7, 449.7, 504.3, 463.4),
            spans=[],
        )

        self.assertFalse(line_is_prose(line))

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


class TranslationUnitSourceTextsTests(unittest.TestCase):
    def test_returns_translated_prose_and_skips_reference_entries(self):
        from pdf_zh_translator.pdf_layout import translation_unit_source_texts

        document = fitz.open()
        page = document.new_page(width=612, height=792)
        page.insert_text(
            (55, 120),
            "We train a neural network policy on demonstrations and study "
            "how safety constraints shape the learned behavior at scale.",
            fontsize=10,
        )
        page.insert_text((55, 600), "References", fontsize=11)
        page.insert_text(
            (55, 620),
            "Haarnoja, T., Zhou, A., and Levine, S. Soft actor-critic "
            "algorithms and applications. ICML, 2018.",
            fontsize=9,
        )

        import tempfile

        with tempfile.TemporaryDirectory() as tmpdir:
            pdf_path = Path(tmpdir) / "source.pdf"
            document.save(pdf_path)
            texts = translation_unit_source_texts(pdf_path)
        document.close()

        blob = " ".join(texts)
        self.assertIn("neural network policy", blob)
        self.assertNotIn("actor-critic", blob)


class FormulaStampClipTests(unittest.TestCase):
    def _page_with_neighbor_line(self):
        document = fitz.open()
        page = document.new_page(width=612, height=792)
        # Neighboring caption line whose descenders dip into the formula clip.
        page.insert_text((380, 84), "physically grounded", fontsize=9)
        return document, page

    def test_clip_trimmed_against_foreign_descenders(self):
        from pdf_zh_translator.pdf_layout import _trim_formula_clip_against_foreign_ink

        document, page = self._page_with_neighbor_line()
        spans = page.get_text("dict")["blocks"][0]["lines"][0]["spans"]
        span_bottom = spans[0]["bbox"][3]
        # Tall formula clip starting above the neighbor's descent line.
        clip = (386.9, span_bottom - 4.0, 396.9, span_bottom + 11.0)

        trimmed = _trim_formula_clip_against_foreign_ink(document, 0, clip)

        self.assertGreaterEqual(trimmed[1], span_bottom)
        self.assertEqual(trimmed[0], clip[0])
        self.assertEqual(trimmed[2], clip[2])
        self.assertEqual(trimmed[3], clip[3])
        document.close()

    def test_clip_keeps_own_formula_span(self):
        from pdf_zh_translator.pdf_layout import _trim_formula_clip_against_foreign_ink

        document = fitz.open()
        page = document.new_page(width=612, height=792)
        page.insert_text((388, 92), "x", fontsize=10)
        span = page.get_text("dict")["blocks"][0]["lines"][0]["spans"][0]
        x0, y0, x1, y1 = span["bbox"]
        clip = (x0 - 1.0, y0 - 1.0, x1 + 1.0, y1 + 1.0)

        trimmed = _trim_formula_clip_against_foreign_ink(document, 0, clip)

        self.assertEqual(trimmed, clip)
        document.close()

    def test_clip_untouched_without_intruders(self):
        from pdf_zh_translator.pdf_layout import _trim_formula_clip_against_foreign_ink

        document = fitz.open()
        document.new_page(width=612, height=792)
        clip = (100.0, 100.0, 120.0, 118.0)

        trimmed = _trim_formula_clip_against_foreign_ink(document, 0, clip)

        self.assertEqual(trimmed, clip)
        document.close()

    def test_span_cache_does_not_leak_across_documents(self):
        """CPython reuses object ids, so caching must live on the document."""
        from pdf_zh_translator.pdf_layout import _trim_formula_clip_against_foreign_ink

        first, page = self._page_with_neighbor_line()
        span_bottom = page.get_text("dict")["blocks"][0]["lines"][0]["spans"][0]["bbox"][3]
        clip = (386.9, span_bottom - 4.0, 396.9, span_bottom + 11.0)
        trimmed_first = _trim_formula_clip_against_foreign_ink(first, 0, clip)
        first.close()

        second = fitz.open()
        second.new_page(width=612, height=792)
        trimmed_second = _trim_formula_clip_against_foreign_ink(second, 0, clip)
        second.close()

        self.assertGreaterEqual(trimmed_first[1], span_bottom)
        self.assertEqual(trimmed_second, clip)


class OverlappingUnitPreservationTests(unittest.TestCase):
    def test_mutually_overlapping_blocks_are_preserved_not_translated(self):
        """Interleaved borderless-table blocks cannot be translated in place:
        both bboxes receive Chinese text and overprint each other."""
        import unittest.mock

        from pdf_zh_translator import pdf_layout

        document = fitz.open()
        page = document.new_page(width=612, height=792)
        page.insert_text(
            (61, 120),
            "Transparent rendering uses glass refraction with specular materials.",
            fontsize=9,
        )
        page.insert_text(
            (61, 600),
            "Regular body paragraphs must keep translating as before.",
            fontsize=10,
        )

        def fake_overlaps(blocks):
            return [
                block.bbox
                for block in blocks
                if "Transparent rendering" in strip_sentinels(block.text)
            ]

        regions_out = {}
        with unittest.mock.patch.object(
            pdf_layout,
            "_overlapping_translation_block_bboxes",
            side_effect=fake_overlaps,
        ):
            units, _, _ = pdf_layout.prepare_translation_units(
                document,
                preserve_graphics_text=True,
                preserved_regions_out=regions_out,
            )
        document.close()

        texts = [" ".join(strip_sentinels(source).split()) for _, source, _ in units]
        self.assertFalse(any("Transparent rendering" in text for text in texts))
        self.assertTrue(any("Regular body paragraphs" in text for text in texts))
        # QA must exempt the same region so overlap warnings stay consistent.
        self.assertTrue(
            any(abs(region[1] - 111.0) < 12.0 for region in regions_out.get(0, []))
        )

    def test_overlapping_translation_block_bboxes_rule(self):
        from pdf_zh_translator.pdf_layout import _overlapping_translation_block_bboxes

        contained = TextBlock(
            page_index=0,
            bbox=(61.0, 409.0, 302.0, 438.0),
            text="Transparent rendering uses glass refraction.",
            font_size=9.0,
            color=(0.0, 0.0, 0.0),
        )
        container = TextBlock(
            page_index=0,
            bbox=(61.0, 409.0, 532.0, 470.0),
            text="Optical material references match visual difficulty.",
            font_size=9.0,
            color=(0.0, 0.0, 0.0),
        )
        neighbor = TextBlock(
            page_index=0,
            bbox=(61.0, 472.0, 303.0, 511.0),
            text="Safety thresholds for transport tilt limits.",
            font_size=9.0,
            color=(0.0, 0.0, 0.0),
        )

        flagged = _overlapping_translation_block_bboxes([contained, container, neighbor])

        self.assertIn(contained.bbox, flagged)
        self.assertIn(container.bbox, flagged)
        self.assertNotIn(neighbor.bbox, flagged)

    def test_touching_paragraphs_are_not_flagged(self):
        from pdf_zh_translator.pdf_layout import _overlapping_translation_block_bboxes

        first = TextBlock(
            page_index=0,
            bbox=(61.0, 100.0, 302.0, 130.0),
            text="First paragraph of ordinary prose text.",
            font_size=10.0,
            color=(0.0, 0.0, 0.0),
        )
        second = TextBlock(
            page_index=0,
            bbox=(61.0, 128.0, 302.0, 158.0),
            text="Second paragraph overlapping by a hairline.",
            font_size=10.0,
            color=(0.0, 0.0, 0.0),
        )

        self.assertEqual(_overlapping_translation_block_bboxes([first, second]), [])


class PreservedRegionUnitFilterTests(unittest.TestCase):
    def test_block_mostly_inside_preserved_regions(self):
        from pdf_zh_translator.pdf_layout import _block_mostly_inside_preserved_regions

        cell = TextBlock(
            page_index=0,
            bbox=(243.5, 107.9, 281.5, 116.6),
            text="RoboCasa",
            font_size=9.0,
            color=(0.0, 0.0, 0.0),
        )
        envelope = (54.9, 107.9, 541.8, 273.0)

        self.assertTrue(_block_mostly_inside_preserved_regions(cell, [envelope]))
        self.assertFalse(_block_mostly_inside_preserved_regions(cell, []))

        outside = TextBlock(
            page_index=0,
            bbox=(55.0, 372.0, 291.2, 513.5),
            text="Scientific benchmarks impose stricter physical constraints.",
            font_size=10.0,
            color=(0.0, 0.0, 0.0),
        )
        self.assertFalse(_block_mostly_inside_preserved_regions(outside, [envelope]))

    def test_units_skip_body_blocks_inside_preserved_table_envelope(self):
        """Cells misclassified as body must not be translated when the QA
        layer will treat the enclosing table envelope as preserved."""
        import unittest.mock

        from pdf_zh_translator import pdf_layout

        document = fitz.open()
        page = document.new_page(width=612, height=792)
        page.insert_text(
            (60, 116),
            "Uses segmented point clouds following the DP3 protocol.",
            fontsize=9,
        )
        page.insert_text(
            (55, 380),
            "Scientific benchmarks impose stricter physical constraints on tasks.",
            fontsize=10,
        )
        envelope = (54.9, 100.0, 541.8, 273.0)

        # First call (classification-time promotion) sees no envelope — the
        # real-world ordering gap — while the final preserved-union pass does.
        with unittest.mock.patch.object(
            pdf_layout,
            "_table_region_bboxes",
            side_effect=[[], [envelope]],
        ) as mock_regions:
            units, _, _ = pdf_layout.prepare_translation_units(
                document,
                preserve_graphics_text=True,
            )
        document.close()
        self.assertEqual(mock_regions.call_count, 2)

        texts = [" ".join(strip_sentinels(source).split()) for _, source, _ in units]
        self.assertFalse(any("segmented point clouds" in text for text in texts))
        self.assertTrue(any("Scientific benchmarks" in text for text in texts))


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

    def test_merges_run_in_bold_heading_with_same_line_prose(self):
        heading = TextBlock(
            page_index=0,
            bbox=(70.9, 238.9, 163.9, 247.7),
            text="Experimental Results.",
            font_size=8.77,
            color=(0.0, 0.0, 0.0),
            bold=True,
            starts_bold=True,
            no_merge=True,
            block_type="heading",
        )
        first_line = TextBlock(
            page_index=0,
            bbox=(163.9, 233.1, 526.8, 247.2),
            text="Figure 3 summarizes performance, with full results in appendix E.",
            font_size=9.96,
            color=(0.0, 0.0, 0.0),
        )
        continuation = TextBlock(
            page_index=0,
            bbox=(70.9, 251.0, 541.5, 289.1),
            text="On low-dimensional tasks, FlashSAC slightly outperforms PPO.",
            font_size=9.96,
            color=(0.0, 0.0, 0.0),
            source_lines=3,
        )

        merged = merge_paragraph_blocks([heading, first_line, continuation])

        self.assertEqual(len(merged), 1)
        self.assertTrue(merged[0].starts_bold)
        self.assertTrue(merged[0].bold_prefix)
        self.assertFalse(merged[0].bold)
        self.assertAlmostEqual(merged[0].font_size, first_line.font_size)
        self.assertIn("Experimental Results. Figure 3", merged[0].text)

    def test_merges_caption_fragments_split_by_inline_formula(self):
        caption = TextBlock(
            page_index=19,
            bbox=(108.0, 394.1, 363.5, 403.3),
            text=(
                "Figure 7. Empirical data scaling on 3-SAT, with a "
                f"{SENTINEL_OPEN}c/{SENTINEL_CLOSE}"
            ),
            font_size=9.0,
            color=(0.0, 0.0, 0.0),
            redact_bboxes=[(108.0, 396.5, 363.5, 403.3)],
        )
        continuation = TextBlock(
            page_index=19,
            bbox=(107.7, 394.1, 504.6, 426.6),
            text=(
                f"{SENTINEL_OPEN}N{SENTINEL_CLOSE} fit predicted by Proposition 4. "
                "Final-epoch validation loss follows the expected rate."
            ),
            font_size=9.0,
            color=(0.0, 0.0, 0.0),
            source_lines=3,
            redact_bboxes=[
                (371.1, 396.5, 504.6, 400.8),
                (108.0, 408.3, 422.3, 415.0),
            ],
        )
        second_panel = TextBlock(
            page_index=19,
            bbox=(107.7, 413.6, 504.2, 436.6),
            text=(
                "(b) Test prediction error follows the same rate and carries "
                "additional finite-test-set noise."
            ),
            font_size=9.0,
            color=(0.0, 0.0, 0.0),
            source_lines=3,
        )

        merged = merge_paragraph_blocks(
            [caption, continuation, second_panel],
            graphic_regions_by_page={19: [(96.0, 120.0, 516.0, 405.0)]},
        )

        self.assertEqual(len(merged), 1)
        self.assertIn("fit predicted by Proposition 4", merged[0].text)
        self.assertIn("Test prediction error", merged[0].text)

    def test_merges_bold_run_in_heading_split_by_inline_formula(self):
        heading = TextBlock(
            page_index=19,
            bbox=(108.0, 594.5, 241.2, 604.6),
            text=(
                "Empirical verification of the 1"
                f"{SENTINEL_OPEN}/{SENTINEL_CLOSE}"
            ),
            font_size=10.0,
            color=(0.0, 0.0, 0.0),
            bold=True,
            starts_bold=True,
        )
        continuation = TextBlock(
            page_index=19,
            bbox=(107.8, 594.5, 504.4, 651.9),
            text=(
                f"{SENTINEL_OPEN}N{SENTINEL_CLOSE} decay rate. We verify that the "
                "qualitative rate matches the observed data scaling."
            ),
            font_size=10.0,
            color=(0.0, 0.0, 0.0),
            source_lines=6,
            redact_bboxes=[
                (249.5, 594.5, 308.0, 604.7),
                (317.9, 594.7, 504.4, 604.7),
            ],
        )

        merged = merge_paragraph_blocks([heading, continuation])

        self.assertEqual(len(merged), 1)
        self.assertTrue(merged[0].bold_prefix)
        self.assertFalse(merged[0].bold)

    def test_merges_same_source_line_with_small_formula_bbox_overlap(self):
        heading = TextBlock(
            page_index=22,
            bbox=(105.3, 319.7, 269.1, 331.5),
            text=f"(2) Gradient sign correctness at p = {SENTINEL_OPEN}1{SENTINEL_CLOSE}",
            font_size=10.0,
            color=(0.0, 0.0, 0.0),
            source_line_bboxes=((105.3, 319.7, 269.1, 331.5),),
        )
        continuation = TextBlock(
            page_index=22,
            bbox=(265.1, 321.2, 504.0, 334.0),
            text=(
                f"{SENTINEL_OPEN}2{SENTINEL_CLOSE}. On the planted regime, "
                "the initial gradient pushes each variable"
            ),
            font_size=10.0,
            color=(0.0, 0.0, 0.0),
            source_line_bboxes=((265.1, 321.2, 504.0, 334.0),),
        )

        merged = merge_paragraph_blocks([heading, continuation])

        self.assertEqual(len(merged), 1)
        self.assertIn("On the planted regime", merged[0].text)

    def test_merges_same_line_formula_tail_before_metric_comparison(self):
        first = TextBlock(
            page_index=22,
            bbox=(121.6, 448.2, 320.6, 461.8),
            text=(
                "The cosine alignment between"
                f"{SENTINEL_OPEN}-gradient{SENTINEL_CLOSE} and x"
                f"{SENTINEL_OPEN}-1{SENTINEL_CLOSE}"
            ),
            font_size=10.0,
            color=(0.0, 0.0, 0.0),
            source_line_bboxes=((121.6, 448.2, 320.6, 461.8),),
        )
        comparison = TextBlock(
            page_index=22,
            bbox=(316.7, 449.7, 504.3, 463.4),
            text=(
                f"2 is{SENTINEL_OPEN}0.442+/-0.134{SENTINEL_CLOSE} "
                f"(planted) versus{SENTINEL_OPEN}0.001+/-0.199{SENTINEL_CLOSE}"
            ),
            font_size=10.0,
            color=(0.0, 0.0, 0.0),
            source_line_bboxes=((316.7, 449.7, 504.3, 463.4),),
        )

        merged = merge_paragraph_blocks([first, comparison])

        self.assertEqual(len(merged), 1)
        self.assertIn("planted", merged[0].text)

    def test_merges_formula_only_chain_using_matching_source_line(self):
        paragraph = TextBlock(
            page_index=5,
            bbox=(116.5, 678.6, 504.0, 702.0),
            text=(
                "For identity fidelity, we compare against a bank of the requested "
                f"morphology. Denote {SENTINEL_OPEN}I^ref{SENTINEL_CLOSE}"
            ),
            font_size=9.96,
            color=(0.0, 0.0, 0.0),
            source_lines=2,
            source_line_bboxes=(
                (116.5, 678.6, 280.0, 690.4),
                (116.5, 691.0, 504.0, 702.0),
            ),
        )
        middle = TextBlock(
            page_index=5,
            bbox=(278.0, 679.0, 322.0, 691.0),
            text=f"{SENTINEL_OPEN}_(v),M^ref{SENTINEL_CLOSE}",
            font_size=9.96,
            color=(0.0, 0.0, 0.0),
            source_line_bboxes=((278.0, 679.0, 322.0, 691.0),),
        )
        tail = TextBlock(
            page_index=5,
            bbox=(320.0, 679.0, 350.0, 691.0),
            text=f"{SENTINEL_OPEN}v)^V{SENTINEL_CLOSE}",
            font_size=9.96,
            color=(0.0, 0.0, 0.0),
            source_line_bboxes=((320.0, 679.0, 350.0, 691.0),),
        )

        merged = merge_paragraph_blocks([paragraph, middle, tail])

        self.assertEqual(len(merged), 1)
        self.assertIn("M^ref", merged[0].text)
        self.assertIn("v)^V", merged[0].text)

    def test_does_not_merge_same_line_formula_fragments_with_large_overlap(self):
        first = TextBlock(
            page_index=0,
            bbox=(105.0, 100.0, 270.0, 112.0),
            text=f"A formula-rich paragraph ends at {SENTINEL_OPEN}x{SENTINEL_CLOSE}",
            font_size=10.0,
            color=(0.0, 0.0, 0.0),
            source_line_bboxes=((105.0, 100.0, 270.0, 112.0),),
        )
        unrelated = TextBlock(
            page_index=0,
            bbox=(245.0, 100.5, 500.0, 112.5),
            text=f"{SENTINEL_OPEN}y{SENTINEL_CLOSE} unrelated prose starts here",
            font_size=10.0,
            color=(0.0, 0.0, 0.0),
            source_line_bboxes=((245.0, 100.5, 500.0, 112.5),),
        )

        self.assertEqual(len(merge_paragraph_blocks([first, unrelated])), 2)

    def test_attaches_cross_record_formula_keepout(self):
        block = TextBlock(
            page_index=0,
            bbox=(108.0, 594.5, 504.4, 651.9),
            text="Translated prose surrounding a display formula.",
            font_size=10.0,
            color=(0.0, 0.0, 0.0),
        )
        formula = _LineRec(
            text=f"{SENTINEL_OPEN}L_inf + c / sqrt(N){SENTINEL_CLOSE}",
            bbox=(108.0, 609.6, 181.0, 628.9),
            spans=[],
        )

        _attach_formula_keepouts([block], [formula])

        self.assertEqual(block.keepout_bboxes, [formula.bbox])

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

    def test_segments_split_bold_leadin_before_body_tail(self):
        record = _RawBlockRec(
            lines=[
                _LineRec(
                    text="Object Grounding: The policy must localize task-relevant objects.",
                    bbox=(49.0, 100.0, 300.0, 112.0),
                    spans=[
                        _span(
                            "Object Grounding:",
                            (49.0, 100.0, 126.0, 112.0),
                            flags=16,
                        ),
                        _span(
                            " The policy must localize task-relevant objects.",
                            (126.0, 100.0, 300.0, 112.0),
                            flags=0,
                        ),
                    ],
                )
            ]
        )

        blocks = segments_from_record(0, record)

        self.assertEqual(len(blocks), 2)
        self.assertEqual(blocks[0].text, "Object Grounding:")
        self.assertEqual(blocks[0].block_type, "heading")
        self.assertTrue(blocks[0].bold)
        self.assertTrue(blocks[0].no_merge)
        self.assertEqual(
            blocks[1].text,
            "The policy must localize task-relevant objects.",
        )

    def test_segments_shift_multiline_leadin_body_below_heading(self):
        record = _RawBlockRec(
            lines=[
                _LineRec(
                    text="Abstract: Robust robotic manipulation requires memory.",
                    bbox=(143.5, 483.6, 468.1, 493.7),
                    spans=[
                        _span("Abstract:", (143.5, 483.6, 184.7, 493.6), flags=16),
                        _span(
                            " Robust robotic manipulation requires memory.",
                            (184.7, 483.7, 468.1, 493.7),
                            flags=0,
                        ),
                    ],
                ),
                _line(
                    "World action models preserve historical observations.",
                    (143.4, 495.6, 469.9, 505.6),
                ),
            ]
        )

        blocks = segments_from_record(0, record)

        self.assertEqual(len(blocks), 2)
        self.assertEqual(blocks[0].text, "Abstract:")
        self.assertGreaterEqual(blocks[1].bbox[1], blocks[0].bbox[3])
        self.assertLess(blocks[1].redact_bboxes[0][1], blocks[1].bbox[1])

    def test_segments_keep_project_page_url_with_label(self):
        record = _RawBlockRec(
            lines=[
                _LineRec(
                    text="Project page: https://yangsizhe.github.io/MemoryWAM/",
                    bbox=(200.8, 195.8, 429.9, 205.8),
                    spans=[
                        _span("Project page:", (200.8, 195.8, 257.3, 205.8), flags=16),
                        _span(
                            " https://yangsizhe.github.io/MemoryWAM/",
                            (257.3, 195.9, 429.9, 205.8),
                            flags=0,
                        ),
                    ],
                )
            ]
        )

        blocks = segments_from_record(0, record)

        self.assertEqual(len(blocks), 1)
        self.assertEqual(blocks[0].text, "Project page: https://yangsizhe.github.io/MemoryWAM/")
        self.assertTrue(blocks[0].nowrap)
        self.assertTrue(blocks[0].no_merge)
        self.assertGreater(blocks[0].bbox[2], 420.0)

    def test_segments_keep_hyphenated_caption_continuation_together(self):
        record = _RawBlockRec(
            lines=[
                _LineRec(
                    text="Table 2: Results of real-world experi-",
                    bbox=(337.4, 487.2, 505.7, 497.3),
                    spans=[
                        _span("Table 2:", (337.4, 487.3, 371.6, 497.3), flags=4),
                        _span(
                            " Results of real-world experi-",
                            (371.6, 487.2, 505.7, 497.2),
                            flags=20,
                        ),
                    ],
                ),
                _LineRec(
                    text="ments. We report the number of successes",
                    bbox=(337.7, 498.2, 504.0, 508.2),
                    spans=[
                        _span("ments.", (337.7, 498.2, 365.1, 508.1), flags=20),
                        _span(
                            " We report the number of successes",
                            (365.1, 498.3, 504.0, 508.2),
                            flags=4,
                        ),
                    ],
                ),
                _line("over the total number of trials.", (337.7, 509.2, 458.6, 519.1)),
            ]
        )

        blocks = segments_from_record(0, record)

        self.assertEqual(len(blocks), 1)
        self.assertIn("real-world experiments.", blocks[0].text)
        self.assertIn("number of successes over the total number of trials", blocks[0].text)

    def test_segments_split_numbered_marker_before_bold_leadin(self):
        record = _RawBlockRec(
            lines=[
                _LineRec(
                    text="1) Object Grounding: whether action tokens can attend to",
                    bbox=(58.9, 511.5, 300.0, 521.7),
                    spans=[
                        _span("1)", (58.9, 511.6, 67.2, 521.7), flags=4),
                        _span(
                            " Object Grounding",
                            (67.2, 511.5, 151.8, 521.6),
                            flags=20,
                        ),
                        _span(
                            ": whether action tokens can attend to",
                            (151.8, 511.6, 300.0, 521.7),
                            flags=4,
                        ),
                    ],
                ),
                _line(
                    "the correct task-relevant regions.",
                    (58.9, 523.5, 260.0, 533.6),
                ),
            ]
        )

        blocks = segments_from_record(0, record)

        self.assertEqual(len(blocks), 2)
        self.assertEqual(blocks[0].text, "1) Object Grounding:")
        self.assertEqual(blocks[0].block_type, "heading")
        self.assertTrue(blocks[0].bold)
        self.assertTrue(blocks[1].no_merge)
        self.assertEqual(
            blocks[1].text,
            "whether action tokens can attend to the correct task-relevant regions.",
        )

    def test_segments_split_summary_and_contribution_items(self):
        record = _RawBlockRec(
            lines=[
                _line(
                    "In summary, we make the following contributions:",
                    (49.0, 100.0, 300.0, 112.0),
                ),
                _line(
                    "• We propose GuidedVLA for structured robotic reasoning.",
                    (55.0, 116.0, 300.0, 128.0),
                ),
                _line(
                    "• We evaluate sensitivity across guidance choices.",
                    (55.0, 132.0, 300.0, 144.0),
                ),
            ]
        )

        blocks = segments_from_record(0, record)

        self.assertEqual(len(blocks), 3)
        self.assertEqual(blocks[0].block_type, "heading")
        self.assertTrue(blocks[0].bold)
        self.assertTrue(blocks[0].no_merge)
        self.assertTrue(blocks[1].no_merge)
        self.assertTrue(blocks[2].no_merge)
        self.assertTrue(blocks[1].text.startswith("• We propose"))
        self.assertTrue(blocks[2].text.startswith("• We evaluate"))

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

    def test_insert_translated_text_renders_ascii_scripts_as_scripts(self):
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
            bbox=(30.0, 40.0, 250.0, 80.0),
            text="Cache C^v_t.",
            font_size=10.0,
            color=(0.0, 0.0, 0.0),
        )

        inserted = insert_translated_text(
            page=page,
            block=block,
            text="Cache C^{v}_{t}:",
            font_pack=font_pack,
            font_size=10.0,
            min_font_size=5.0,
            margin=0.8,
        )

        extracted = page.get_text("text")
        document.close()
        self.assertTrue(inserted)
        self.assertIn("Cache C^{v}_{t}:", extracted)


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

    def test_author_metadata_is_not_translated(self):
        from pdf_zh_translator.pdf_layout import classify_blocks

        block = self._make_block(
            "Author Names Omitted for Anonymous Review. Paper-ID 74",
            bbox=(49.0, 133.0, 563.0, 146.0),
        )

        classify_blocks([block], 0, 792, [])

        self.assertEqual(block.block_type, "metadata")
        self.assertFalse(block.should_translate)

    def test_reference_entry_without_heading_is_bibliography(self):
        from pdf_zh_translator.pdf_layout import classify_blocks

        block = self._make_block(
            "Depth anything 3: Recovering the visual space from any views. "
            "arXiv preprint arXiv:2511.10647, 2025.",
            bbox=(312.0, 58.4, 563.0, 110.0),
            page=10,
        )

        classify_blocks([block], 10, 792, [])

        self.assertEqual(block.block_type, "bibliography")
        self.assertFalse(block.should_translate)

    def test_fraction_tail_number_is_not_bibliography_without_reference_context(self):
        import pdf_zh_translator.pdf_layout as layout

        block = self._make_block(
            "2. Unlike CSM and VCL, this term gives the model a per-variable target.",
            bbox=(221.0, 391.9, 505.7, 404.3),
            page=4,
        )
        layout._bibliography_seen.clear()
        layout._bibliography_ended = False
        layout._bibliography_heading_size = 0.0

        classify_blocks([block], 4, 792, [])

        self.assertEqual(block.block_type, "body")
        self.assertTrue(block.should_translate)

    def test_citation_dense_body_paragraph_is_not_bibliography(self):
        from pdf_zh_translator.pdf_layout import classify_blocks

        block = self._make_block(
            (
                "Vision-Language-Action (VLA) models [99, 44, 8] represent a significant "
                "step toward generalist robot policies by integrating action as a specialized "
                "modality within Vision-Language Models. In practice, we observe that the "
                "action decoder often latches onto spurious correlations, as shown in Fig. 1."
            ),
            bbox=(312.0, 487.5, 563.0, 725.9),
        )

        classify_blocks([block], 0, 792, [])

        self.assertEqual(block.block_type, "body")
        self.assertTrue(block.should_translate)

    def test_safe_rl_body_paragraph_with_in_this_paper_is_not_bibliography(self):
        from pdf_zh_translator.pdf_layout import classify_blocks

        block = self._make_block(
            (
                "The standard formalism is the constrained Markov decision process (CMDP), "
                "which maximizes reward subject to expected safety costs [2]. In this paper, "
                "we propose SafeTransport, which lifts this flow equivalence into a safe-RL "
                "algorithm and keeps per-channel constraints visible."
            ),
            bbox=(107.6, 74.5, 505.7, 542.7),
        )

        classify_blocks([block], 1, 792, [])

        self.assertEqual(block.block_type, "body")
        self.assertTrue(block.should_translate)

    def test_hyperparameter_2048_is_not_misread_as_reference_year(self):
        from pdf_zh_translator.pdf_layout import _looks_like_reference_entry_text

        text = (
            "As in the GPU-based setting, FlashSAC uses a single unified configuration "
            "across all tasks, with only minimal adjustments to match each benchmark's "
            "conventions. Since sample collection is slower with a single environment, "
            "the CPU-based configuration differs by reducing the batch size from 2048 "
            "to 512 and setting the update-to-data ratio to 1."
        )

        self.assertFalse(_looks_like_reference_entry_text(text))

    def test_named_reference_with_year_remains_bibliography(self):
        from pdf_zh_translator.pdf_layout import _looks_like_reference_entry_text

        text = (
            "Haarnoja, T., Zhou, A., Abbeel, P., and Levine, S. "
            "Soft actor-critic algorithms and applications. ICML, 2018."
        )

        self.assertTrue(_looks_like_reference_entry_text(text))

    def test_untranslated_body_with_url_is_not_exempt(self):
        text = (
            "We release the complete implementation and evaluation scripts at "
            "https://github.com/example/project for reproducible experiments."
        )

        self.assertTrue(_looks_like_untranslated_english(text))
        self.assertFalse(_looks_like_untranslated_english("https://github.com/example/project"))

    def test_preserved_region_text_changed_detects_translated_table_label(self):
        self.assertTrue(preserved_region_text_changed("Task", "任务"))
        self.assertFalse(preserved_region_text_changed("Task FastWAM Ours", "Task FastWAM Ours"))

    def test_preserved_region_text_changed_detects_numeric_value_change(self):
        self.assertTrue(preserved_region_text_changed("91.2", "19.2"))
        self.assertFalse(preserved_region_text_changed("91.2 ± 0.4", "91.2 ± 0.4"))

    def test_preserved_region_normalizes_unicode_minus_in_exponent(self):
        self.assertFalse(preserved_region_text_changed("5.8e−6", "5.8e-6"))

    def test_preserved_region_text_changed_detects_chinese_overlay(self):
        self.assertTrue(
            preserved_region_text_changed(
                "Source Image Target URDF",
                "Source Image Target URDF 源图像 目标模型",
            )
        )

    def test_preserved_formula_ignores_adjacent_chinese_line_bbox(self):
        self.assertFalse(
            preserved_region_text_changed(
                "Sref = max 1 + cos phi clip Iref",
                "Sref = max 1 + cos phi clip Iref Sref并非针对",
            )
        )

    def test_preserved_text_qa_merges_adjacent_formula_atoms(self):
        regions = _preserved_text_qa_regions(
            [
                (248.0, 414.0, 278.4, 424.0),
                (278.8, 412.3, 384.3, 431.4),
                (107.6, 505.0, 190.0, 520.0),
            ]
        )

        self.assertEqual(len(regions), 2)
        self.assertTrue(any(region[0] <= 248.0 and region[2] >= 384.3 for region in regions))

    def test_roman_table_caption_detection(self):
        from pdf_zh_translator.pdf_layout import classify_blocks

        block = self._make_block("TABLE XI: Precision and framework ablation for OpenVLA.")
        classify_blocks([block], 0, 792, [])

        self.assertEqual(block.block_type, "caption")
        self.assertTrue(block.preserve_position)

    def test_references_heading_inside_figure_zone_starts_bibliography(self):
        import pdf_zh_translator.pdf_layout as layout

        heading = TextBlock(
            page_index=7,
            bbox=(409.6, 567.4, 480.0, 577.4),
            text="REFERENCES",
            font_size=10.0,
            color=(0.0, 0.0, 0.0),
            bold=True,
        )
        entry = TextBlock(
            page_index=7,
            bbox=(317.0, 584.4, 563.0, 620.0),
            text="[1] Smith, J. A robust translation method. ICML, 2026.",
            font_size=9.0,
            color=(0.0, 0.0, 0.0),
        )
        layout._bibliography_seen.clear()
        layout._bibliography_ended = False
        layout._bibliography_heading_size = 0.0

        classify_blocks(
            [heading, entry],
            page_index=7,
            page_height=792.0,
            image_zones=[(380.0, 540.0, 520.0, 582.0)],
        )

        self.assertEqual(heading.block_type, "heading")
        self.assertTrue(heading.should_translate)
        self.assertEqual(entry.block_type, "bibliography")
        self.assertFalse(entry.should_translate)

    def test_figure_reference_sentence_is_body(self):
        from pdf_zh_translator.pdf_layout import classify_blocks

        block = self._make_block("Figure 5 summarizes these trends across all guidance factors.")
        classify_blocks([block], 0, 792, [])

        self.assertEqual(block.block_type, "body")

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

    def test_guidedvla_head_labels_are_figure_labels_without_image_zone(self):
        from pdf_zh_translator.pdf_layout import classify_blocks

        block = self._make_block(
            "(i) Object Head (ii) Skill Head (iii) Depth Head",
            bbox=(120, 200, 360, 215),
        )

        classify_blocks([block], 0, 792, [])

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


class TestDefaultFontDiscovery(unittest.TestCase):
    def test_env_override_wins(self):
        import os
        import tempfile
        from unittest import mock

        from pdf_zh_translator.pdf_layout import find_default_font_file

        with tempfile.TemporaryDirectory() as tmpdir:
            font_path = Path(tmpdir) / "custom-font.otf"
            font_path.write_bytes(b"stub")
            with mock.patch.dict(os.environ, {"PDF_ZH_FONT_FILE": str(font_path)}):
                self.assertEqual(find_default_font_file(), font_path)

    def test_discovers_noto_cjk_under_linux_font_root(self):
        import tempfile
        from unittest import mock

        from pdf_zh_translator import pdf_layout

        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            ttc = root / "opentype" / "noto" / "NotoSansCJK-Regular.ttc"
            ttc.parent.mkdir(parents=True)
            ttc.write_bytes(b"stub")
            with (
                mock.patch.object(pdf_layout, "FONT_FILE_CANDIDATES", ()),
                mock.patch.object(pdf_layout, "FONT_SEARCH_ROOTS", (root,)),
            ):
                self.assertEqual(pdf_layout.find_default_font_file(), ttc)


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

    def test_untranslated_detector_allows_translated_metric_fragment(self):
        self.assertFalse(
            _looks_like_untranslated_english(
                "（1.36× wall-clock, 20.1% conflict reduction）和Glucose 4.2"
                "（1.10×, 6.0%）中的"
            )
        )

    def test_untranslated_detector_flags_english_metric_sentence(self):
        self.assertTrue(
            _looks_like_untranslated_english(
                "Accuracy improves by 20.1% and conflict reduction reaches 6.0% "
                "across all benchmark tasks."
            )
        )

    def test_retry_detector_flags_english_run_inside_chinese_caption(self):
        block = TextBlock(
            page_index=17,
            bbox=(72.0, 100.0, 500.0, 130.0),
            text="Figure 6: Empirical verification of Corollary 1 on 3-SAT instances.",
            font_size=9.0,
            color=(0.0, 0.0, 0.0),
            block_type="caption",
        )

        self.assertTrue(
            _translated_block_still_english(
                block,
                "图6：Empirical verification of Corollary 1 on 3-SAT instances，"
                "结果与理论预测一致。",
            )
        )

    def test_retry_detector_flags_model_commentary(self):
        block = TextBlock(
            page_index=19,
            bbox=(72.0, 100.0, 500.0, 150.0),
            text="Figure 7: Verification of the asymptotic decay rate.",
            font_size=9.0,
            color=(0.0, 0.0, 0.0),
            block_type="caption",
        )
        contaminated = (
            "图7：渐近衰减率验证。\n解释：该图比较理论和实测结果。\n步骤：\n"
            "1. 读取曲线。\n2. 比较斜率。\n注：这段翻译保留了公式。"
        )

        self.assertTrue(_translated_block_still_english(block, contaminated))

    def test_retry_detector_allows_chinese_with_standard_acronyms(self):
        block = TextBlock(
            page_index=1,
            bbox=(72.0, 100.0, 500.0, 150.0),
            text="We evaluate CAP-SAT on standard SAT benchmarks.",
            font_size=10.0,
            color=(0.0, 0.0, 0.0),
        )

        self.assertFalse(
            _translated_block_still_english(
                block,
                "我们在 SAT、CAP-SAT 与 Glucose 4.2 基准上评估该方法，并报告运行时间。",
            )
        )

    def test_preserved_regions_include_nontranslated_table_and_metadata(self):
        document = fitz.open()
        page = document.new_page(width=612, height=792)
        page.insert_text(
            (132, 145),
            "Sizhe Yang Juncheng Mu Tianming Wei Chenhao Lu Xiaofan Li Linning Xu",
            fontsize=9,
        )
        page.insert_text(
            (138, 170),
            "1The Chinese University of Hong Kong 2Tsinghua University "
            "3Zhejiang University equal contribution",
            fontsize=9,
        )
        x_positions = [130, 200, 310, 380, 450]
        rows = [
            ["Model", "Method", "Dry-run", "Solved", "Solved / Dry-run"],
            ["MCDC", "Qwen 3.5 9B", "4/30 (13.3%)", "2/30 (6.7%)", "2/4 (50.0%)"],
            ["OpenMC", "Claude Opus 4.6", "29/30 (96.7%)", "25/30 (83.3%)", "25/29"],
        ]
        for y, row in zip([260, 274, 288], rows):
            for x, cell in zip(x_positions, row):
                page.insert_text((x, y), cell, fontsize=9)

        regions = preserved_original_text_regions(document)
        document.close()

        self.assertTrue(
            any(130 <= bbox[1] <= 180 and bbox[2] - bbox[0] > 250 for bbox in regions[0])
        )
        self.assertGreaterEqual(
            sum(1 for bbox in regions[0] if 245 <= bbox[1] <= 295),
            10,
        )

    def test_preserved_regions_include_algorithm_records_skipped_by_collection(self):
        document = fitz.open()
        document.new_page(width=612, height=792)
        page = document.new_page(width=612, height=792)
        page.insert_text(
            (72, 120),
            "Algorithm 5 Python Pseudocode of Dual-Path Control Attention",
            fontsize=9,
        )

        regions = preserved_original_text_regions(document)
        document.close()

        self.assertTrue(
            any(
                bbox[1] <= 120 <= bbox[3] + 10 and bbox[0] <= 72 <= bbox[2]
                for bbox in regions[1]
            )
        )

    def test_verification_ignores_untranslated_table_cells_without_grid(self):
        original = fitz.open()
        page = original.new_page(width=612, height=792)
        pixmap = fitz.Pixmap(fitz.csRGB, fitz.IRect(0, 0, 4, 4), False)
        pixmap.clear_with(0xEEEEEE)
        page.insert_image(fitz.Rect(110, 55, 590, 120), pixmap=pixmap)
        x_positions = [130, 200, 310, 380, 450]
        rows = [
            ["Model", "Method", "Dry-run", "Solved", "Solved / Dry-run"],
            ["MCDC", "Qwen 3.5 9B", "4/30 (13.3%)", "2/30 (6.7%)", "2/4 (50.0%)"],
            ["OpenMC", "Claude Opus 4.6", "29/30 (96.7%)", "25/30 (83.3%)", "25/29"],
        ]
        for y, row in zip([80, 94, 108], rows):
            for x, cell in zip(x_positions, row):
                page.insert_text((x, y), cell, fontsize=9)
        page.insert_text((88, 180), "The results demonstrate improved simulator generation.")

        translated = fitz.open()
        page = translated.new_page(width=612, height=792)
        page.insert_image(fitz.Rect(110, 55, 590, 120), pixmap=pixmap)
        for y, row in zip([80, 94, 108], rows):
            for x, cell in zip(x_positions, row):
                page.insert_text((x, y), cell, fontsize=9)
        page.insert_text((88, 180), "结果表明仿真器生成效果有所提升。")

        import tempfile

        with tempfile.TemporaryDirectory() as tmpdir:
            original_path = Path(tmpdir) / "orig.pdf"
            translated_path = Path(tmpdir) / "zh.pdf"
            original.save(original_path)
            translated.save(translated_path)
            issues = verify_translation_issues(original_path, translated_path)

        original.close()
        translated.close()
        self.assertFalse(any(issue.code == "untranslated_english" for issue in issues))

    def test_verification_flags_changed_numeric_value_in_preserved_table(self):
        original = fitz.open()
        page = original.new_page(width=612, height=792)
        x_positions = [130, 200, 310, 380, 450]
        original_rows = [
            ["Model", "Method", "Accuracy", "Recall", "F1"],
            ["Base", "Encoder A", "88.4", "82.1", "84.9"],
            ["Ours", "Encoder B", "91.2", "89.7", "90.4"],
        ]
        for y, row in zip([80, 96, 112], original_rows):
            for x, cell in zip(x_positions, row):
                page.insert_text((x, y), cell, fontsize=9)

        translated = fitz.open()
        page = translated.new_page(width=612, height=792)
        translated_rows = [
            ["Model", "Method", "Accuracy", "Recall", "F1"],
            ["Base", "Encoder A", "88.4", "82.1", "84.9"],
            ["Ours", "Encoder B", "19.2", "89.7", "90.4"],
        ]
        for y, row in zip([80, 96, 112], translated_rows):
            for x, cell in zip(x_positions, row):
                page.insert_text((x, y), cell, fontsize=9)

        import tempfile

        with tempfile.TemporaryDirectory() as tmpdir:
            original_path = Path(tmpdir) / "orig.pdf"
            translated_path = Path(tmpdir) / "zh.pdf"
            original.save(original_path)
            translated.save(translated_path)
            issues = verify_translation_issues(original_path, translated_path)

        original.close()
        translated.close()
        self.assertTrue(any(issue.code == "preserved_text_changed" for issue in issues))

    def test_verification_still_flags_untranslated_checklist_prose(self):
        original = fitz.open()
        page = original.new_page(width=612, height=792)
        page.insert_text(
            (88, 120),
            "Question: Does the paper fully disclose all the information needed to "
            "reproduce the main experimental results?",
        )

        translated = fitz.open()
        page = translated.new_page(width=612, height=792)
        page.insert_text(
            (88, 120),
            "Question: Does the paper fully disclose all the information needed to "
            "reproduce the main experimental results?",
        )

        import tempfile

        with tempfile.TemporaryDirectory() as tmpdir:
            original_path = Path(tmpdir) / "orig.pdf"
            translated_path = Path(tmpdir) / "zh.pdf"
            original.save(original_path)
            translated.save(translated_path)
            issues = verify_translation_issues(original_path, translated_path)

        original.close()
        translated.close()
        self.assertTrue(any(issue.code == "untranslated_english" for issue in issues))

    def test_formula_explanation_detector_ignores_checklist_line_fragments(self):
        self.assertFalse(
            _looks_like_untranslated_formula_explanation("with human subjects.1117")
        )
        self.assertTrue(
            _looks_like_untranslated_formula_explanation(
                "where x denotes the input vector."
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

    def test_untranslated_caption_detector_supports_roman_tables(self):
        self.assertTrue(
            _looks_like_untranslated_caption(
                "TABLE XI: Precision and framework ablation for OpenVLA."
            )
        )

    def test_untranslated_caption_detector_ignores_figure_reference_sentence(self):
        self.assertFalse(
            _looks_like_untranslated_caption(
                "Figure 5 summarizes these trends across all guidance factors."
            )
        )

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

    def test_overlap_qa_ignores_lines_inside_preserved_table_region(self):
        from unittest.mock import patch

        original = fitz.open()
        original.new_page(width=300, height=220)
        translated = fitz.open()
        page = translated.new_page(width=300, height=220)
        page.insert_text((40, 80), "Model accuracy result", fontsize=11)
        page.insert_text((40, 80), "Method success value", fontsize=11)

        import tempfile

        with tempfile.TemporaryDirectory() as tmpdir:
            original_path = Path(tmpdir) / "orig.pdf"
            translated_path = Path(tmpdir) / "zh.pdf"
            original.save(original_path)
            translated.save(translated_path)
            baseline = verify_translation_issues(original_path, translated_path)

            def prepare_with_preserved_region(*args, **kwargs):
                kwargs["preserved_regions_out"].update(
                    {0: [(20.0, 20.0, 280.0, 180.0)]}
                )
                return [], {}, 0

            with patch(
                "pdf_zh_translator.pdf_layout.prepare_translation_units",
                side_effect=prepare_with_preserved_region,
            ):
                preserved = verify_translation_issues(original_path, translated_path)

        original.close()
        translated.close()
        self.assertTrue(any(issue.code == "text_overlap" for issue in baseline))
        self.assertFalse(any(issue.code == "text_overlap" for issue in preserved))

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

    def test_flags_untranslated_multiline_prose_inside_visual_region(self):
        prose = (
            "The proposed policy uses a visual encoder to extract robust features "
            "from each observation and predicts actions across multiple long horizon tasks."
        )

        original = fitz.open()
        page = original.new_page(width=420, height=320)
        pixmap = fitz.Pixmap(fitz.csRGB, fitz.IRect(0, 0, 4, 4), False)
        pixmap.clear_with(0xEEEEEE)
        page.insert_image(fitz.Rect(40, 40, 380, 230), pixmap=pixmap)
        page.insert_textbox(fitz.Rect(70, 80, 350, 180), prose, fontsize=10)

        translated = fitz.open()
        page = translated.new_page(width=420, height=320)
        page.insert_image(fitz.Rect(40, 40, 380, 230), pixmap=pixmap)
        page.insert_textbox(fitz.Rect(70, 80, 350, 180), prose, fontsize=10)

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
        self.assertTrue(any(issue.code == "untranslated_english" for issue in issues))

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

    def test_visual_regions_include_text_dict_image_blocks(self):
        class FakePage:
            rect = SimpleNamespace(width=300.0, height=220.0)

            def get_text(self, kind):
                assert kind == "dict"
                return {"blocks": [{"type": 1, "bbox": (40.0, 50.0, 180.0, 130.0)}]}

            def get_images(self):
                return []

            def get_drawings(self):
                return []

        self.assertEqual(_visual_regions_for_page(FakePage()), [(40.0, 50.0, 180.0, 130.0)])

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

    def test_clip_block_bbox_against_memorywam_mid_right_image(self):
        clipped = _clip_block_bbox_against_floats(
            (107.6, 324.4, 505.2, 477.9),
            [(254.6, 314.5, 517.2, 427.5)],
            612.0,
        )

        self.assertEqual(clipped, (107.6, 324.4, 251.6, 477.9))

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


def test_classify_blocks_preserves_complete_three_column_table_component():
    blocks = [
        TextBlock(
            0,
            (70.0, 82.0, 518.0, 109.0),
            "Table 9: Hyperparameters used in GPU simulators.",
            9.0,
            (0.0, 0.0, 0.0),
        ),
        TextBlock(
            0,
            (210.0, 124.0, 425.0, 133.0),
            "Hyperparameter Notation Value",
            9.0,
            (0.0, 0.0, 0.0),
            source_lines=3,
        ),
        TextBlock(
            0,
            (136.0, 162.0, 178.0, 171.0),
            "Common",
            9.0,
            (0.0, 0.0, 0.0),
        ),
        TextBlock(
            0,
            (210.0, 140.0, 426.0, 193.0),
            "Parallel environments - 1024 Replay buffer capacity - 10M",
            9.0,
            (0.0, 0.0, 0.0),
            nowrap=True,
            no_merge=True,
            block_type="table",
        ),
        TextBlock(
            0,
            (136.0, 200.0, 413.0, 231.0),
            "Actor Number of blocks - 2 Hidden dimension 128",
            9.0,
            (0.0, 0.0, 0.0),
            nowrap=True,
            no_merge=True,
            block_type="table",
        ),
    ]

    classify_blocks(blocks, page_index=0, page_height=792.0, image_zones=[])

    assert blocks[0].block_type == "caption"
    assert blocks[0].should_translate is True
    assert blocks[1].block_type == "table"
    assert blocks[1].should_translate is False
    assert blocks[2].block_type == "table"
    assert blocks[2].should_translate is False
    assert _table_region_bboxes(blocks) == [(70.0, 124.0, 518.0, 231.0)]


def test_classify_blocks_preserves_short_fragments_between_caption_and_table():
    blocks = [
        TextBlock(
            0,
            (107.7, 318.6, 505.6, 367.7),
            "Table 4: Pairwise comparisons for the ablation study.",
            8.0,
            (0.0, 0.0, 0.0),
        ),
        TextBlock(
            0,
            (166.0, 397.7, 211.0, 404.7),
            "Glucose 4.2",
            7.0,
            (0.0, 0.0, 0.0),
        ),
        TextBlock(
            0,
            (226.0, 403.2, 307.0, 410.1),
            "Polarity-prior (Freq.)",
            7.0,
            (0.0, 0.0, 0.0),
        ),
        TextBlock(
            0,
            (107.7, 411.9, 505.6, 471.3),
            "Neural model 68.2 71.4",
            7.0,
            (0.0, 0.0, 0.0),
            block_type="table",
            should_translate=False,
            nowrap=True,
            no_merge=True,
        ),
    ]

    classify_blocks(blocks, page_index=0, page_height=792.0, image_zones=[])

    assert blocks[1].block_type == "table"
    assert blocks[1].should_translate is False
    assert blocks[2].block_type == "table"
    assert blocks[2].should_translate is False


def test_classify_blocks_preserves_short_header_split_by_formula_cells():
    blocks = [
        TextBlock(
            0,
            (107.7, 197.2, 505.6, 236.1),
            "Table 19: Polarity capture by initialization family.",
            8.0,
            (0.0, 0.0, 0.0),
        ),
        TextBlock(
            0,
            (117.8, 240.9, 144.1, 247.8),
            "Family",
            7.0,
            (0.0, 0.0, 0.0),
        ),
    ]

    classify_blocks(blocks, page_index=0, page_height=792.0, image_zones=[])

    assert blocks[1].block_type == "table"
    assert blocks[1].should_translate is False


def test_classify_blocks_does_not_promote_prose_between_caption_and_table():
    blocks = [
        TextBlock(
            0,
            (107.7, 318.6, 505.6, 350.0),
            "Table 4: Pairwise comparisons for the ablation study.",
            8.0,
            (0.0, 0.0, 0.0),
        ),
        TextBlock(
            0,
            (122.0, 365.0, 315.0, 374.0),
            "This paragraph introduces the ablation",
            7.0,
            (0.0, 0.0, 0.0),
        ),
        TextBlock(
            0,
            (107.7, 381.0, 505.6, 451.0),
            "Method Accuracy Success rate",
            7.0,
            (0.0, 0.0, 0.0),
            block_type="table",
            should_translate=False,
            nowrap=True,
            no_merge=True,
        ),
    ]

    classify_blocks(blocks, page_index=0, page_height=792.0, image_zones=[])

    assert blocks[1].block_type == "body"
    assert blocks[1].should_translate is True


def test_flashsac_three_column_header_is_table():
    record = _RawBlockRec(
        lines=[
            _line("Hyperparameter", (210.0, 124.0, 287.0, 133.0)),
            _line("Notation", (337.0, 124.0, 378.0, 133.0)),
            _line("Value", (399.0, 124.0, 425.0, 133.0)),
        ]
    )

    assert record_is_table(record)


def test_scientific_value_row_is_table():
    record = _RawBlockRec(
        lines=[
            _line("Joint acceleration", (79.0, 340.0, 150.0, 349.0)),
            _line("||q||^2", (181.0, 339.0, 213.0, 349.0)),
            _line("-2.5 x 10^-7", (363.0, 339.0, 408.0, 349.0)),
            _line("-2.5 x 10^-7", (454.0, 339.0, 499.0, 349.0)),
        ]
    )

    assert record_is_table(record)


def test_table_component_caption_starts_new_region():
    blocks = [
        TextBlock(
            0,
            (136.0, 124.0, 426.0, 426.0),
            "first table",
            9.0,
            (0.0, 0.0, 0.0),
            block_type="table",
        ),
        TextBlock(
            0,
            (70.0, 462.0, 518.0, 500.0),
            "Table 10: CPU simulator settings.",
            9.0,
            (0.0, 0.0, 0.0),
            block_type="caption",
        ),
        TextBlock(
            0,
            (164.0, 515.0, 424.0, 584.0),
            "second table",
            9.0,
            (0.0, 0.0, 0.0),
            block_type="table",
        ),
    ]

    assert _table_region_bboxes(blocks) == [
        (70.0, 124.0, 518.0, 426.0),
        (70.0, 515.0, 518.0, 584.0),
    ]


def test_figure_chart_grid_does_not_create_table_component_region():
    blocks = [
        TextBlock(
            0,
            (70.0, 100.0, 518.0, 118.0),
            "Figure 4: Training curves for all environments.",
            9.0,
            (0.0, 0.0, 0.0),
            block_type="caption",
        ),
        TextBlock(
            0,
            (80.0, 130.0, 500.0, 350.0),
            "0 20 40 60 80 100 120 Humanoid Ant Walker",
            8.0,
            (0.0, 0.0, 0.0),
            block_type="table",
            should_translate=False,
            nowrap=True,
            no_merge=True,
        ),
        TextBlock(
            0,
            (80.0, 365.0, 500.0, 405.0),
            "As in the GPU-based setting, FlashSAC uses fewer samples.",
            9.0,
            (0.0, 0.0, 0.0),
        ),
    ]

    classify_blocks(blocks, page_index=0, page_height=792.0, image_zones=[])

    assert _table_region_bboxes(blocks) == []
    assert blocks[2].block_type == "body"
    assert blocks[2].should_translate is True


if __name__ == "__main__":
    unittest.main()


class PreservedCollisionSkipTests(unittest.TestCase):
    def test_candidate_colliding_with_preserved_label_is_flagged(self):
        from pdf_zh_translator.pdf_layout import _candidate_bboxes_colliding_with_preserved

        # DreamZero p26 geometry: heading-classified cell overprints the
        # preserved "Coaster" label below it.
        candidate = TextBlock(
            page_index=0,
            bbox=(95.5, 451.4, 129.0, 459.9),
            text="6 Put Cup on",
            font_size=6.0,
            color=(0.0, 0.0, 0.0),
        )
        label_bbox = (106.2, 457.8, 124.7, 463.0)

        flagged = _candidate_bboxes_colliding_with_preserved([candidate], [label_bbox])

        self.assertEqual(flagged, [candidate.bbox])

    def test_candidate_near_but_not_overlapping_is_not_flagged(self):
        from pdf_zh_translator.pdf_layout import _candidate_bboxes_colliding_with_preserved

        candidate = TextBlock(
            page_index=0,
            bbox=(95.5, 440.0, 129.0, 450.0),
            text="A separate caption line",
            font_size=6.0,
            color=(0.0, 0.0, 0.0),
        )

        flagged = _candidate_bboxes_colliding_with_preserved(
            [candidate], [(106.2, 457.8, 124.7, 463.0)]
        )

        self.assertEqual(flagged, [])

    def test_hairline_touch_is_not_flagged(self):
        from pdf_zh_translator.pdf_layout import _candidate_bboxes_colliding_with_preserved

        candidate = TextBlock(
            page_index=0,
            bbox=(61.0, 100.0, 302.0, 130.0),
            text="Body paragraph above a preserved table region.",
            font_size=10.0,
            color=(0.0, 0.0, 0.0),
        )
        # Wide preserved region grazing the paragraph's bottom edge.
        preserved = (61.0, 128.5, 302.0, 190.0)

        flagged = _candidate_bboxes_colliding_with_preserved([candidate], [preserved])

        self.assertEqual(flagged, [])


class CaptionInsideEnvelopeTests(unittest.TestCase):
    def test_caption_anchoring_table_envelope_is_still_translated(self):
        """Captions anchor table envelopes; sitting inside one must not stop
        their translation (regression: Table 2 caption left in English)."""
        import unittest.mock

        from pdf_zh_translator import pdf_layout

        document = fitz.open()
        page = document.new_page(width=612, height=792)
        page.insert_text(
            (350, 550),
            "Table 2: Image super-resolution on the validation set.",
            fontsize=9,
        )
        page.insert_text(
            (61, 700),
            "Regular body paragraphs must keep translating as before.",
            fontsize=10,
        )
        # Envelope fully covering the caption band.
        envelope = (348.0, 540.0, 504.0, 640.0)

        with unittest.mock.patch.object(
            pdf_layout,
            "_table_region_bboxes",
            return_value=[envelope],
        ):
            units, _, _ = pdf_layout.prepare_translation_units(
                document,
                preserve_graphics_text=True,
            )
        document.close()

        texts = [" ".join(strip_sentinels(source).split()) for _, source, _ in units]
        self.assertTrue(any(text.startswith("Table 2:") for text in texts))
        self.assertTrue(any("Regular body paragraphs" in text for text in texts))

    def test_translated_caption_inside_envelope_does_not_flag_preserved_change(self):
        """QA companion: the caption band overlaps the table envelope, but a
        translated caption must not count as preserved-region tampering."""
        rows = [
            ["Model", "PSNR", "SSIM"],
            ["Baseline", "27.4", "0.81"],
            ["Ours", "29.1", "0.86"],
        ]
        xs = [355, 430, 480]

        original = fitz.open()
        page = original.new_page(width=612, height=792)
        for y, row in zip([530, 542, 554], rows):
            for x, cell in zip(xs, row):
                page.insert_text((x, y), cell, fontsize=9)
        page.insert_text(
            (350, 572),
            "Table 2: Image super-resolution on the validation set.",
            fontsize=9,
        )
        for y, row in zip([596, 608], rows[:2]):
            for x, cell in zip(xs, row):
                page.insert_text((x, y), cell, fontsize=9)

        translated = fitz.open()
        page = translated.new_page(width=612, height=792)
        for y, row in zip([530, 542, 554], rows):
            for x, cell in zip(xs, row):
                page.insert_text((x, y), cell, fontsize=9)
        # Translated captions typically wrap one line taller than the source;
        # preserved table rows stay at their original positions.
        page.insert_text(
            (350, 572),
            "表2：验证集上的图像超分辨率结果，",
            fontsize=9,
            fontname="china-ss",
        )
        page.insert_text(
            (350, 584),
            "包含全部对比方法的定量指标。",
            fontsize=9,
            fontname="china-ss",
        )
        for y, row in zip([596, 608], rows[:2]):
            for x, cell in zip(xs, row):
                page.insert_text((x, y), cell, fontsize=9)

        import tempfile

        with tempfile.TemporaryDirectory() as tmpdir:
            original_path = Path(tmpdir) / "orig.pdf"
            translated_path = Path(tmpdir) / "zh.pdf"
            original.save(original_path)
            translated.save(translated_path)
            issues = verify_translation_issues(original_path, translated_path)

        original.close()
        translated.close()
        self.assertFalse(
            any(issue.code == "preserved_text_changed" for issue in issues),
            [f"{i.code} p{i.page}" for i in issues],
        )


class CaptionBandExclusionTests(unittest.TestCase):
    def test_entries_inside_caption_bands_are_excluded(self):
        from pdf_zh_translator.pdf_layout import _entries_outside_caption_bands

        entries = [
            ((355.0, 530.0, 500.0, 540.0), "Model PSNR SSIM"),
            ((350.0, 565.0, 504.0, 575.0), "Table 2: Image super-resolution."),
            ((355.0, 590.0, 500.0, 600.0), "Baseline 27.4 0.81"),
        ]
        caption_bboxes = [(348.0, 563.0, 504.0, 577.0)]

        kept = _entries_outside_caption_bands(entries, caption_bboxes)

        self.assertEqual(len(kept), 2)
        self.assertTrue(all("Table 2" not in text for _, text in kept))

    def test_no_caption_bboxes_keeps_all_entries(self):
        from pdf_zh_translator.pdf_layout import _entries_outside_caption_bands

        entries = [((355.0, 530.0, 500.0, 540.0), "Model PSNR SSIM")]

        self.assertEqual(_entries_outside_caption_bands(entries, []), entries)

    def test_partial_graze_is_kept(self):
        from pdf_zh_translator.pdf_layout import _entries_outside_caption_bands

        entries = [((355.0, 558.0, 500.0, 568.0), "29.1 0.86 row overlapping slightly")]
        caption_bboxes = [(348.0, 566.5, 504.0, 580.0)]

        kept = _entries_outside_caption_bands(entries, caption_bboxes)

        self.assertEqual(len(kept), 1)
