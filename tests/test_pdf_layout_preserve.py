"""Tests for conservative native-layout preservation rules."""

import re
import unittest
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import fitz

from pdf_zh_translator.pdf_layout import (
    SENTINEL_CLOSE,
    SENTINEL_OPEN,
    SENTINEL_RUN_RE,
    FontPack,
    TextBlock,
    _align_formula_anchors,
    _attach_formula_keepouts,
    _cascade_expand_page_items,
    _clip_block_bbox_against_floats,
    _combine_inline_style_translation_items,
    _demote_unanchorable_math_blocks,
    _equation_table_region_bboxes,
    _expand_single_line_body_bbox,
    _expand_multiline_block_bbox,
    _expand_standalone_heading_to_column,
    _extract_formula_fragments,
    _float_clip_min_font_size,
    _focused_foreign_phrase_translation,
    _font_role_consistency_issues,
    _formula_fragment_present,
    _formula_markers_form_atom,
    _has_parallel_panel_sibling,
    _inline_formula_bridge_block,
    _insert_source_region_raster,
    _line_is_equation_explanation,
    _LineRec,
    _looks_like_formula_fragment,
    _looks_like_overlap_exempt_text,
    _looks_like_stranded_formula_prose_tail,
    _looks_like_untranslated_caption,
    _looks_like_untranslated_english,
    _looks_like_untranslated_formula_explanation,
    _merge_wrapped_formula_continuation_records,
    _normalize_formula_accessibility_text,
    _normalize_formula_fragment_for_compare,
    _overlap_text_entries_from_block,
    _preserved_text_qa_regions,
    _promote_equation_table_neighbor_blocks,
    _promote_table_component_blocks,
    _RawBlockRec,
    _retained_bracketed_natural_language,
    _review_line_number_bboxes,
    _select_formula_source_rect,
    _table_region_bboxes,
    _text_outside_sentinels,
    _Token,
    _tokenize_translation_with_formula_clips,
    _translated_block_still_english,
    _translation_retains_foreign_prose,
    _unresolved_formula_keepouts,
    _uses_fixed_source_math,
    _visible_image_stats,
    _visual_min_zone_intersects_graphics,
    _visual_regions_for_page,
    apply_inline_bold,
    can_merge_blocks,
    caption_should_center,
    center_caption_bbox,
    classify_blocks,
    clean_translation,
    collect_text_blocks,
    expand_heading_bbox,
    fragmented_prose_warnings_from_units,
    graphic_regions_for_page,
    insert_translated_text,
    is_math_span,
    join_lines,
    line_block_height,
    line_is_prose,
    line_looks_like_section_heading,
    looks_like_action_skeleton_sequence,
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
    restore_text,
    segments_from_record,
    should_preserve_original_block,
    strip_sentinels,
    subset_fonts_safely,
    tokenize_text,
    trim_redact_bbox_against_formula_lines,
    verify_translation,
    verify_translation_issues,
)


class FontRoleConsistencyTests(unittest.TestCase):
    def test_multiline_body_sets_heading_hierarchy_baseline(self):
        source_blocks = [
            TextBlock(
                page_index=0,
                bbox=(150.0, 100.0, 440.0, 120.0),
                text="Misclassified paper title",
                font_size=16.75,
                color=(0.0, 0.0, 0.0),
                source_lines=1,
                block_type="body",
            ),
            TextBlock(
                page_index=0,
                bbox=(70.0, 400.0, 180.0, 422.0),
                text="1 Introduction",
                font_size=13.96,
                color=(0.0, 0.0, 0.0),
                bold=True,
                source_lines=1,
                block_type="heading",
            ),
            TextBlock(
                page_index=0,
                bbox=(70.0, 430.0, 525.0, 610.0),
                text="Ordinary multiline body paragraph.",
                font_size=10.62,
                color=(0.0, 0.0, 0.0),
                source_lines=12,
                block_type="body",
            ),
            TextBlock(
                page_index=0,
                bbox=(70.0, 700.0, 525.0, 730.0),
                text="Footnote body.",
                font_size=8.73,
                color=(0.0, 0.0, 0.0),
                source_lines=3,
                block_type="body",
            ),
        ]
        spans = [
            {
                "text": "近端策略优化算法",
                "bbox": (157.0, 100.0, 438.0, 120.0),
                "origin": (157.0, 116.0),
                "size": 15.39,
                "font": "HiraginoSansGB-W6",
                "flags": 16,
            },
            {
                "text": "1 引言",
                "bbox": (70.0, 406.0, 118.0, 421.0),
                "origin": (70.0, 418.0),
                "size": 15.39,
                "font": "HiraginoSansGB-W6",
                "flags": 16,
            },
            {
                "text": "近年来，针对强化学习已提出多种不同方法。",
                "bbox": (70.0, 433.0, 490.0, 447.0),
                "origin": (70.0, 444.0),
                "size": 9.77,
                "font": "STSongti-SC-Regular",
                "flags": 4,
            },
            {
                "text": "这些方法在多个任务上进行比较。",
                "bbox": (70.0, 448.0, 410.0, 462.0),
                "origin": (70.0, 459.0),
                "size": 9.77,
                "font": "STSongti-SC-Regular",
                "flags": 4,
            },
        ]

        class Page:
            def get_text(self, _kind):
                return {
                    "blocks": [
                        {
                            "type": 0,
                            "lines": [{"spans": spans}],
                        }
                    ]
                }

        issues = _font_role_consistency_issues(source_blocks, Page(), 1)

        self.assertFalse(
            [issue for issue in issues if issue.code == "font_role_heading_mismatch"]
        )

    def test_cascaded_run_in_prefix_is_not_reported_as_bold_body(self):
        source_blocks = [
            TextBlock(
                page_index=0,
                bbox=(50.0, 100.0, 115.0, 110.0),
                text="Mask R-CNN:",
                font_size=10.0,
                color=(0.0, 0.0, 0.0),
                bold=True,
                block_type="run_in_heading",
            ),
            TextBlock(
                page_index=0,
                bbox=(50.0, 111.0, 286.0, 200.0),
                text="Mask R-CNN adopts the same two-stage procedure.",
                font_size=10.0,
                color=(0.0, 0.0, 0.0),
                source_lines=8,
                block_type="body",
            ),
        ]
        spans = [
            {
                "text": "掩码区域卷积神经网络：",
                "bbox": (50.0, 128.0, 145.0, 140.0),
                "origin": (50.0, 138.0),
                "size": 9.2,
                "font": "HiraginoSansGB-W6",
                "flags": 16,
            },
            {
                "text": "采用相同的两阶段流程。",
                "bbox": (148.0, 128.0, 250.0, 140.0),
                "origin": (148.0, 138.0),
                "size": 9.2,
                "font": "STSongti-SC-Regular",
                "flags": 4,
            },
        ]

        class Page:
            def get_text(self, _kind):
                return {"blocks": [{"type": 0, "lines": [{"spans": spans}]}]}

        issues = _font_role_consistency_issues(source_blocks, Page(), 1)

        self.assertFalse(
            [issue for issue in issues if issue.code == "font_role_bold_spill"]
        )

    def test_cascaded_heading_is_matched_below_intruding_body_text(self):
        source_blocks = [
            TextBlock(
                page_index=0,
                bbox=(108.0, 620.0, 505.0, 688.0),
                text="Long preceding paragraph.",
                font_size=9.86,
                color=(0.0, 0.0, 0.0),
                source_lines=6,
                block_type="body",
            ),
            TextBlock(
                page_index=0,
                bbox=(108.3, 676.5, 201.7, 688.4),
                text="3 METHODOLOGY",
                font_size=11.96,
                color=(0.0, 0.0, 0.0),
                bold=True,
                source_lines=1,
                block_type="heading",
            ),
        ]
        spans = [
            {
                "text": "前段正文扩行进入原标题区域",
                "bbox": (108.4, 677.7, 209.8, 690.6),
                "origin": (108.4, 688.0),
                "size": 9.21,
                "font": "STSongti-SC-Regular",
                "flags": 4,
            },
            {
                "text": "3 方法",
                "bbox": (109.1, 692.4, 168.9, 704.4),
                "origin": (109.1, 702.0),
                "size": 11.96,
                "font": "HiraginoSansGB-W6",
                "flags": 16,
            },
        ]

        class Page:
            def get_text(self, _kind):
                return {"blocks": [{"type": 0, "lines": [{"spans": spans}]}]}

        issues = _font_role_consistency_issues(source_blocks, Page(), 1)

        self.assertFalse(
            [issue for issue in issues if issue.code == "font_role_heading_mismatch"]
        )

    def test_consecutive_cascaded_headings_claim_distinct_bold_lines(self):
        source_blocks = [
            TextBlock(
                page_index=0,
                bbox=(108.0, 620.0, 505.0, 688.0),
                text="Long preceding paragraph.",
                font_size=9.86,
                color=(0.0, 0.0, 0.0),
                source_lines=6,
                block_type="body",
            ),
            TextBlock(
                page_index=0,
                bbox=(108.3, 676.5, 201.7, 688.4),
                text="3 METHODOLOGY",
                font_size=11.96,
                color=(0.0, 0.0, 0.0),
                bold=True,
                source_lines=1,
                block_type="heading",
            ),
            TextBlock(
                page_index=0,
                bbox=(108.2, 701.7, 254.0, 711.7),
                text="3.1 OPTIMAL FLOW TRANSPORT",
                font_size=9.96,
                color=(0.0, 0.0, 0.0),
                bold=True,
                source_lines=1,
                block_type="heading",
            ),
        ]
        spans = [
            {
                "text": "前段正文扩行进入原标题区域",
                "bbox": (108.4, 682.5, 209.8, 695.4),
                "origin": (108.4, 692.8),
                "size": 9.21,
                "font": "STSongti-SC-Regular",
                "flags": 4,
            },
            {
                "text": "3 方法",
                "bbox": (109.1, 700.9, 168.9, 712.9),
                "origin": (109.1, 710.5),
                "size": 11.96,
                "font": "HiraginoSansGB-W6",
                "flags": 16,
            },
            {
                "text": "3.1 最优流传输",
                "bbox": (109.1, 720.4, 218.6, 730.4),
                "origin": (109.1, 728.4),
                "size": 9.96,
                "font": "HiraginoSansGB-W6",
                "flags": 16,
            },
        ]

        class Page:
            def get_text(self, _kind):
                return {"blocks": [{"type": 0, "lines": [{"spans": spans}]}]}

        issues = _font_role_consistency_issues(source_blocks, Page(), 1)

        self.assertFalse(
            [issue for issue in issues if issue.code == "font_role_heading_mismatch"]
        )

    def test_parallel_lettered_panels_are_not_column_width_list_items(self):
        panel_a = TextBlock(
            page_index=0,
            bbox=(50.0, 283.0, 192.0, 319.0),
            text="(a) Backbone Architecture: Better backbones.",
            font_size=8.0,
            color=(0.0, 0.0, 0.0),
            source_lines=4,
        )
        panel_b = TextBlock(
            page_index=0,
            bbox=(202.0, 283.0, 335.0, 319.0),
            text="(b) Independent Masks: Decoupling helps.",
            font_size=8.0,
            color=(0.0, 0.0, 0.0),
            source_lines=4,
        )
        ordinary_item = TextBlock(
            page_index=0,
            bbox=(50.0, 350.0, 285.0, 380.0),
            text="(c) A vertical contribution item.",
            font_size=10.0,
            color=(0.0, 0.0, 0.0),
            source_lines=2,
        )

        self.assertTrue(
            _has_parallel_panel_sibling(panel_a, [panel_a, panel_b, ordinary_item])
        )
        self.assertFalse(
            _has_parallel_panel_sibling(
                ordinary_item,
                [panel_a, panel_b, ordinary_item],
            )
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

    def test_preserves_function_call_action_chain(self):
        block = TextBlock(
            page_index=0,
            bbox=(150.0, 280.0, 500.0, 292.0),
            text=(
                "pick(hook) -> pull(cube, hook) -> place(hook) -> pick(cube)"
            ),
            font_size=10.0,
            color=(0.0, 0.0, 0.0),
        )

        self.assertTrue(should_preserve_original_block(block, []))

    def test_preserves_wrapped_action_chain_tail(self):
        block = TextBlock(
            page_index=0,
            bbox=(163.8, 363.1, 232.4, 373.0),
            text="place(cube, rack)",
            font_size=10.0,
            color=(0.0, 0.0, 0.0),
        )

        self.assertTrue(should_preserve_original_block(block, []))

    def test_long_prose_with_calls_and_arrow_is_not_action_skeleton(self):
        paragraph = (
            "The policy maps observations with encoder(image) and then uses "
            "decoder(features) to predict actions. The training objective follows "
            "the expert -> student direction, while the remaining sentences explain "
            "the architecture, supervision, optimization, and deployment procedure "
            "in ordinary academic prose rather than pseudocode."
        )

        self.assertFalse(looks_like_action_skeleton_sequence(paragraph))

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

    def test_overlap_entries_ignore_tall_math_span_in_mixed_prose_line(self):
        block = {
            "bbox": (50.0, 100.0, 300.0, 130.0),
            "lines": [
                {
                    "bbox": (50.0, 100.0, 300.0, 124.0),
                    "spans": [
                        {
                            "text": "析因实验",
                            "font": "NotoSerifCJKsc-Regular",
                            "bbox": (50.0, 100.0, 110.0, 113.0),
                        },
                        {
                            "text": "2×2",
                            "font": "CMSY10",
                            "bbox": (112.0, 99.0, 132.0, 124.0),
                        },
                        {
                            "text": "交叉两个编码器",
                            "font": "NotoSerifCJKsc-Regular",
                            "bbox": (134.0, 100.0, 230.0, 113.0),
                        },
                    ],
                }
            ],
        }

        entries = _overlap_text_entries_from_block(block)

        self.assertEqual(entries[0][0], (50.0, 100.0, 230.0, 113.0))

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

    def test_relaxed_caption_stops_before_preserved_table_obstacle(self):
        caption = TextBlock(
            page_index=0,
            bbox=(100.0, 50.0, 500.0, 62.0),
            text="Table 2: Evaluation on the benchmark.",
            font_size=9.0,
            color=(0.0, 0.0, 0.0),
            block_type="caption",
        )
        page = SimpleNamespace(rect=SimpleNamespace(height=792.0))

        relax_caption_boxes(
            page,
            [(caption, "表2：基准评估。")],
            obstacles=[(90.0, 68.0, 510.0, 180.0)],
        )

        self.assertLessEqual(caption.bbox[3], 65.0)

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

    def test_protect_text_preserves_structured_prompt_code_lists(self):
        source = (
            "Available primitives: ['pick(a)', 'place(a, b)'] "
            "Available scene objects: ['table', 'yellow box'] "
            "Human instruction: Move the yellow box onto the table."
        )

        protected, mapping = protect_text(source)
        restored, missing = restore_text(protected, mapping)

        self.assertNotIn("pick(a)", protected)
        self.assertNotIn("yellow box']", protected)
        self.assertIn("Human instruction", protected)
        self.assertEqual(restored, source)
        self.assertEqual(missing, [])
        self.assertEqual(len(mapping), 2)

    def test_restore_text_expands_nested_placeholders_to_fixed_point(self):
        restored, missing = restore_text(
            "translated ⟦0⟧",
            {
                0: "prefix ⟦1⟧ suffix",
                1: "x_{t}",
            },
        )

        self.assertEqual(restored, "translated prefix x_{t} suffix")
        self.assertEqual(missing, [])

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

    def test_parse_block_lines_keeps_short_preposition_outside_formula(self):
        raw_block = {
            "type": 0,
            "bbox": (366.9, 624.0, 397.9, 638.8),
            "lines": [
                {
                    "bbox": (366.9, 624.0, 397.9, 638.8),
                    "spans": [
                        _span(
                            "i",
                            (366.9, 631.1, 369.8, 638.1),
                            size=6.97,
                            font="CMMI7",
                            flags=6,
                        ),
                        _span(" ", (369.8, 628.8, 376.5, 638.8)),
                        _span("in", (376.5, 626.0, 384.2, 636.0), flags=5),
                        _span(
                            " z",
                            (384.2, 626.1, 391.8, 636.1),
                            font="CMBX10",
                            flags=21,
                        ),
                        _span(
                            "+",
                            (391.8, 624.7, 397.9, 631.7),
                            size=6.97,
                            font="CMR7",
                            flags=5,
                        ),
                    ],
                }
            ],
        }

        record, _ = parse_block_lines(raw_block, page_width=612.0)

        self.assertIsNotNone(record)
        protected, mapping = protect_text(record.lines[0].text)
        self.assertIn("in", protected)
        self.assertTrue(all("in" not in fragment for fragment in mapping.values()))

    def test_parse_block_lines_keeps_fragmented_italic_prose_outside_formula(self):
        raw_block = {
            "type": 0,
            "bbox": (143.9, 165.2, 468.1, 197.2),
            "lines": [
                {
                    "bbox": (143.9, 165.2, 468.1, 177.2),
                    "spans": [
                        _span(
                            "pour effectuer ",
                            (143.9, 165.2, 201.0, 177.2),
                            font="NimbusRomNo9L-ReguItal",
                            flags=6,
                        ),
                        _span(
                            "u",
                            (201.0, 165.2, 206.0, 177.2),
                            font="NimbusRomNo9L-ReguItal",
                            flags=6,
                        ),
                        _span(
                            "n",
                            (206.0, 165.2, 211.0, 177.2),
                            font="NimbusRomNo9L-ReguItal",
                            flags=6,
                        ),
                        _span(
                            " diagnostic ",
                            (211.0, 165.2, 270.0, 177.2),
                            font="NimbusRomNo9L-ReguItal",
                            flags=6,
                        ),
                        _span(
                            "o",
                            (270.0, 165.2, 275.0, 177.2),
                            font="NimbusRomNo9L-ReguItal",
                            flags=6,
                        ),
                        _span(
                            "u",
                            (275.0, 165.2, 280.0, 177.2),
                            font="NimbusRomNo9L-ReguItal",
                            flags=6,
                        ),
                        _span(
                            " une procedure",
                            (280.0, 165.2, 350.0, 177.2),
                            font="NimbusRomNo9L-ReguItal",
                            flags=6,
                        ),
                    ],
                }
            ],
        }

        record, _ = parse_block_lines(raw_block, page_width=612.0)

        self.assertIsNotNone(record)
        protected, mapping = protect_text(record.lines[0].text)
        self.assertIn("un diagnostic ou une procedure", protected)
        self.assertEqual(mapping, {})

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


class RomanSmallCapsHeadingTests(unittest.TestCase):
    def test_roman_section_title_is_split_from_following_body(self):
        heading = _LineRec(
            text="V. DATA COLLECTION AND TRAINING RECIPE",
            bbox=(338.6, 173.4, 536.4, 183.4),
            spans=[
                _span("V. D", (338.6, 173.4, 358.0, 183.4), size=9.96),
                _span(
                    "ATA COLLECTION AND TRAINING RECIPE",
                    (358.0, 173.4, 536.4, 183.4),
                    size=7.97,
                ),
            ],
        )
        body = _line(
            "Broadly capable robot foundation models require the right dataset.",
            (321.9, 189.0, 563.0, 199.0),
        )

        segments = segments_from_record(4, _RawBlockRec(lines=[heading, body]))

        self.assertTrue(line_looks_like_section_heading(heading))
        self.assertEqual(segments[0].block_type, "heading")
        self.assertEqual(strip_sentinels(segments[0].text), heading.text)
        self.assertFalse(segments[0].bold)
        self.assertEqual(segments[1].block_type, "body")
        self.assertAlmostEqual(segments[1].font_size, 10.0)

    def test_roman_list_item_is_not_a_section_title(self):
        item = _line("IV. This result follows from the previous theorem.", (72, 100, 310, 112))

        self.assertFalse(line_looks_like_section_heading(item))


class ContributionNameProtectionTests(unittest.TestCase):
    def test_author_names_are_preserved_while_role_and_conjunction_translate(self):
        source = (
            "Data and operations: Noah Brown, Michael Equi, Chelsea Finn, "
            "and Anna Walling."
        )

        protected, mapping = protect_text(source)
        translated = protected.replace("Data and operations:", "数据与操作：").replace(
            "and", "和"
        )
        restored, missing = restore_text(translated, mapping)
        restored = clean_translation(restored)

        self.assertNotIn("Noah Brown", protected)
        self.assertEqual(len(mapping), 4)
        self.assertEqual(missing, [])
        self.assertIn("数据与操作：Noah Brown", restored)
        self.assertIn("Chelsea Finn, 和 Anna Walling.", restored)

    def test_unrelated_capitalized_method_list_is_not_protected(self):
        source = "Baselines: Real Method, Vision Model, and Flow Solver."

        protected, mapping = protect_text(source)

        self.assertEqual(protected, source)
        self.assertEqual(mapping, {})


class FormulaBridgedRunInHeadingTests(unittest.TestCase):
    def test_formula_between_run_in_heading_and_body_keeps_one_flow(self):
        formula = (390.44, 345.09, 402.90, 355.90)
        heading = TextBlock(
            page_index=14,
            bbox=(321.94, 345.23, 390.44, 355.20),
            text="Attention mask.",
            font_size=9.96,
            color=(0.0, 0.0, 0.0),
            bold=True,
            source_lines=1,
            block_type="run_in_heading",
            preserve_position=True,
            keepout_bboxes=[formula],
            source_line_bboxes=((321.94, 345.23, 390.44, 355.20),),
        )
        body = TextBlock(
            page_index=14,
            bbox=(311.97, 345.32, 563.04, 498.75),
            text="uses a blockwise causal attention mask with three blocks.",
            font_size=9.96,
            color=(0.0, 0.0, 0.0),
            source_lines=14,
            block_type="body",
            keepout_bboxes=[formula],
            source_line_bboxes=((402.90, 345.32, 563.03, 356.78),),
        )

        combined = _combine_inline_style_translation_items(
            [(heading, "注意力掩码。"), (body, "采用分块因果注意力掩码。")]
        )

        self.assertEqual(len(combined), 1)
        merged, translated = combined[0]
        self.assertEqual(translated, "注意力掩码。 采用分块因果注意力掩码。")
        self.assertTrue(merged.bold_prefix)
        self.assertEqual(merged.keepout_bboxes, [formula])
        self.assertFalse(merged.preserve_position)


class FormulaExplanationExtractionTests(unittest.TestCase):
    def test_where_glued_to_variable_with_corresponds_is_prose(self):
        prose_prefix = (49.0, 94.1, 73.3, 104.1)
        formula = (73.3, 93.9, 193.2, 105.4)
        prose_suffix = (193.2, 94.0, 300.0, 104.1)
        line = _LineRec(
            text=(
                f"where{SENTINEL_OPEN}A_t=[a_t,...,a_{{t+H-1}}]{SENTINEL_CLOSE} "
                "corresponds to an action"
            ),
            bbox=(49.0, 93.9, 300.0, 105.4),
            spans=[
                _span("where", prose_prefix),
                _span("A_t=[a_t,...,a_{t+H-1}]", formula),
                _span(" corresponds to an action", prose_suffix),
            ],
            math_bboxes=[formula],
            math_run_bboxes=[formula],
            prose_bboxes=[prose_prefix, prose_suffix],
        )

        self.assertTrue(_line_is_equation_explanation(line))
        segments = segments_from_record(4, _RawBlockRec(lines=[line]))
        self.assertEqual(len(segments), 1)
        self.assertEqual(segments[0].block_type, "body")
        self.assertIn("corresponds to an action", segments[0].text)

    def test_whereas_is_not_a_glued_formula_cue(self):
        line = _LineRec(
            text=f"whereas {SENTINEL_OPEN}A_t{SENTINEL_CLOSE} corresponds to an action",
            bbox=(49.0, 93.9, 300.0, 105.4),
            spans=[],
            math_bboxes=[(93.0, 93.9, 110.0, 105.4)],
            math_run_bboxes=[(93.0, 93.9, 110.0, 105.4)],
            prose_bboxes=[(49.0, 94.1, 93.0, 104.1), (110.0, 94.0, 300.0, 104.1)],
        )

        self.assertFalse(_line_is_equation_explanation(line))


class TableDetectionTests(unittest.TestCase):
    def test_panel_reference_sentence_is_body_not_caption(self):
        block = TextBlock(
            page_index=6,
            bbox=(108.0, 426.3, 504.0, 515.0),
            text=(
                "Fig. 3 (d). Consider the source phrase [the man] which was "
                "translated into [l' homme]."
            ),
            font_size=9.96,
            color=(0.0, 0.0, 0.0),
            source_lines=2,
        )

        classify_blocks([block], 6, 792.0, [])

        self.assertEqual(block.block_type, "body")
        self.assertFalse(block.preserve_position)

    def test_academic_structure_heading_is_not_promoted_to_equation_table(self):
        for text in (
            "Definition 4.1.",
            "Step 1: Guided Sampling.",
            "Step 2: Manifold Projection.",
            "Proposition 4.3.",
        ):
            heading = TextBlock(
                page_index=0,
                bbox=(120.0, 100.0, 260.0, 110.0),
                text=text,
                font_size=9.9,
                color=(0.0, 0.0, 0.0),
                bold=True,
                source_lines=1,
                block_type="heading",
                should_translate=True,
            )

            _promote_equation_table_neighbor_blocks(
                [heading],
                [(100.0, 112.0, 300.0, 140.0)],
            )

            self.assertEqual(heading.block_type, "heading")
            self.assertTrue(heading.should_translate)

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

    def test_proposition_statement_above_formula_is_not_promoted_to_table(self):
        statement = TextBlock(
            page_index=0,
            bbox=(167.0, 399.7, 344.5, 409.7),
            text=(
                f"For{SENTINEL_OPEN}G{SENTINEL_CLOSE} fixed, the optimal "
                f"discriminator{SENTINEL_OPEN}D{SENTINEL_CLOSE} is"
            ),
            font_size=9.96,
            color=(0.0, 0.0, 0.0),
            source_lines=1,
        )

        _promote_equation_table_neighbor_blocks(
            [statement],
            [
                (249.3, 422.7, 262.0, 434.5),
                (257.6, 424.6, 289.1, 436.3),
                (311.2, 417.8, 343.4, 428.7),
            ],
        )

        self.assertEqual(statement.block_type, "body")
        self.assertTrue(statement.should_translate)

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

    def test_table_record_trailing_prose_is_split_for_translation(self):
        lines = [
            _line("person", (56.0, 285.0, 78.0, 293.0)),
            _line("rider", (89.0, 285.0, 104.0, 293.0)),
            _line("car", (121.0, 285.0, 131.0, 293.0)),
            _line("truck", (147.0, 285.0, 163.0, 293.0)),
            _line("17.9k", (58.0, 296.0, 76.0, 304.0)),
            _line("1.8k", (89.0, 296.0, 103.0, 304.0)),
            _line("26.9k", (117.0, 296.0, 135.0, 304.0)),
            _line("0.5k", (148.0, 296.0, 162.0, 304.0)),
        ]
        for text, bbox in (
            (
                "Instance segmentation performance on this task is measured",
                (50.0, 308.0, 286.0, 318.0),
            ),
            (
                "by the COCO-style mask AP averaged over IoU thresholds;",
                (50.0, 320.0, 286.0, 330.0),
            ),
            (
                "AP50 at an IoU of 0.5 is also reported.",
                (50.0, 332.0, 286.0, 342.0),
            ),
        ):
            lines.append(
                _LineRec(
                    text=text,
                    bbox=bbox,
                    spans=[_span(text, bbox)],
                    prose_bboxes=[bbox],
                )
            )
        record = _RawBlockRec(lines=lines)

        blocks = segments_from_record(0, record)

        body = [block for block in blocks if block.block_type == "body"]
        self.assertTrue(record_is_table(record))
        self.assertEqual(len(body), 1)
        self.assertIn("Instance segmentation performance", body[0].text)
        self.assertIn("also reported", body[0].text)
        self.assertFalse(body[0].nowrap)
        self.assertTrue(
            all(block.block_type == "table" for block in blocks if block not in body)
        )

    def test_narrow_table_caption_does_not_absorb_full_width_following_prose(self):
        record = _RawBlockRec(
            lines=[
                _line(
                    "Table 2: Performance with quantized inference.",
                    (349.3, 546.3, 505.5, 555.5),
                ),
                _line(
                    "4-bit quantization matches the default approach (see Table 5).",
                    (349.6, 556.4, 504.2, 625.2),
                ),
                _line(
                    "OpenVLA consumes more memory at inference time than prior policies",
                    (108.0, 628.7, 504.0, 638.6),
                ),
                _line(
                    "but bfloat16 cuts the memory footprint in half.",
                    (108.0, 640.4, 504.0, 650.6),
                ),
            ]
        )

        blocks = segments_from_record(0, record)

        self.assertEqual([block.block_type for block in blocks], ["caption", "body"])
        self.assertNotIn("OpenVLA consumes", blocks[0].text)
        self.assertIn("OpenVLA consumes", blocks[1].text)
        self.assertTrue(blocks[0].no_merge)
        self.assertFalse(blocks[1].nowrap)

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

    def test_prose_mentioning_output_colon_is_not_algorithm(self):
        # GPT-3 analysis prose cites "Output:" inline; keyword search over
        # the merged text preserved (and silently dropped) whole analysis
        # paragraphs. Running prose with sentence structure must not count.
        record = _RawBlockRec(
            lines=[
                _line(
                    "We evaluate each example by drawing K examples as",
                    (99.0, 380.0, 542.0, 392.0),
                ),
                _line(
                    "conditioning. The prompt shows Output: followed by the",
                    (99.0, 393.0, 542.0, 405.0),
                ),
                _line(
                    "answer. We then compare the likelihood of each option",
                    (99.0, 406.0, 542.0, 418.0),
                ),
                _line(
                    "and pick the best one. This is the standard protocol.",
                    (99.0, 419.0, 542.0, 431.0),
                ),
            ]
        )

        self.assertFalse(record_is_algorithm(record))

    def test_line_initial_io_markers_stay_algorithm(self):
        record = _RawBlockRec(
            lines=[
                _line("Input: a sequence of tokens x", (99.0, 380.0, 300.0, 392.0)),
                _line("Output: the predicted label y", (99.0, 393.0, 300.0, 405.0)),
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

    def test_redaction_trims_above_formula_extending_from_next_line(self):
        formula = _LineRec(
            text=f"{SENTINEL_OPEN}N(x; mean, variance){SENTINEL_CLOSE}",
            bbox=(307.4, 445.0, 541.4, 463.4),
            spans=[],
        )

        trimmed = trim_redact_bbox_against_formula_lines(
            (307.4, 440.8, 452.3, 450.9),
            [formula],
        )

        self.assertEqual(trimmed[:3], (307.4, 440.8, 452.3))
        self.assertAlmostEqual(trimmed[3], 443.8)

    def test_formula_keepout_excludes_same_line_prose_suffix(self):
        from pdf_zh_translator.pdf_layout import _attach_formula_keepouts

        formula_line = _LineRec(
            text=(
                f"{SENTINEL_OPEN}N(x; mean, variance){SENTINEL_CLOSE} "
                "A key property follows"
            ),
            bbox=(307.4, 445.0, 541.4, 463.4),
            spans=[],
            prose_bboxes=[(412.4, 452.8, 541.4, 462.9)],
            math_run_bboxes=[(307.4, 445.0, 412.4, 463.4)],
        )
        block = TextBlock(
            0,
            (307.4, 416.8, 542.7, 512.0),
            "Forward process prose",
            10.0,
            (0.0, 0.0, 0.0),
        )

        _attach_formula_keepouts([block], [formula_line])

        self.assertEqual(block.keepout_bboxes, [(307.4, 445.0, 412.4, 463.4)])

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
    def test_overlapping_superscript_fragments_form_one_formula_atom(self):
        tau_minus = (448.14, 641.92, 464.19, 653.02)
        exponent_one = (454.99, 644.58, 468.50, 657.89)

        self.assertTrue(
            _formula_markers_form_atom(tau_minus, exponent_one, "")
        )

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

    def test_formula_subscript_touching_body_bottom_remains_active(self):
        keepout = (216.3, 473.3, 220.1, 481.0)
        block = TextBlock(
            page_index=1,
            bbox=(196.3, 464.0, 240.2, 474.6),
            text="maximize",
            font_size=10.62,
            color=(0.0, 0.0, 0.0),
            source_line_bboxes=((196.3, 464.0, 240.2, 474.6),),
            keepout_bboxes=[keepout],
        )

        self.assertEqual(_unresolved_formula_keepouts(block), [keepout])

    def test_single_line_body_moves_above_formula_subscript(self):
        keepout = (216.3, 473.3, 220.1, 481.0)
        block = TextBlock(
            page_index=1,
            bbox=(196.3, 464.0, 240.2, 474.6),
            text="maximize",
            font_size=10.62,
            color=(0.0, 0.0, 0.0),
            source_line_bboxes=((196.3, 464.0, 240.2, 474.6),),
            keepout_bboxes=[keepout],
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

        shifted = _expand_single_line_body_bbox(
            block,
            "最大化",
            [block],
            font_pack,
            9.77,
            0.8,
            612.0,
        )

        self.assertLessEqual(shifted.bbox[3], keepout[1] - 0.4)

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

    def test_formula_rich_block_uses_prose_font_size(self):
        from pdf_zh_translator.pdf_layout import _accumulate_line, _SegmentAccumulator

        prose_bbox = (75.4, 69.9, 88.0, 80.7)
        math_bboxes = [
            (88.0 + index * 8.0, 69.9, 96.0 + index * 8.0, 80.7)
            for index in range(5)
        ]
        spans = [
            {
                "text": "Let",
                "bbox": prose_bbox,
                "size": 10.0,
                "flags": 0,
                "color": 0,
            }
        ]
        spans.extend(
            {
                "text": symbol,
                "bbox": bbox,
                "size": 5.0,
                "flags": 0,
                "color": 0,
            }
            for symbol, bbox in zip("abcde", math_bboxes)
        )
        line = _LineRec(
            text=f"Let{SENTINEL_OPEN}abcde{SENTINEL_CLOSE}",
            bbox=(75.4, 69.9, 128.0, 80.7),
            spans=spans,
            prose_bboxes=[prose_bbox],
            math_bboxes=math_bboxes,
            math_run_bboxes=[(88.0, 69.9, 128.0, 80.7)],
        )
        accumulator = _SegmentAccumulator()

        _accumulate_line(accumulator, line)
        block = accumulator.flush(0)

        self.assertIsNotNone(block)
        self.assertEqual(block.font_size, 10.0)

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

    def test_tall_inline_fraction_touching_formula_prose_is_exposed(self):
        prose = _RawBlockRec(
            lines=[
                _line(
                    "The loss uses "
                    f"{SENTINEL_OPEN}L_total{SENTINEL_CLOSE} in training.",
                    (80.0, 100.0, 270.0, 110.0),
                )
            ]
        )
        fraction_line = _line(
            f"{SENTINEL_OPEN}a/b{SENTINEL_CLOSE}",
            (270.0, 93.0, 292.0, 113.0),
        )
        fraction_line.math_bboxes = [fraction_line.bbox]
        fraction_line.math_run_bboxes = [fraction_line.bbox]
        fraction = _RawBlockRec(lines=[fraction_line])

        bridge = _inline_formula_bridge_block(
            0,
            [prose, fraction],
            [True, True],
            [False, False],
            1,
        )

        self.assertIsNotNone(bridge)
        self.assertEqual(strip_sentinels(bridge.text), "a/b")

    def test_unnumbered_display_formula_tail_is_not_an_inline_bridge(self):
        previous_line = _line(
            f"{SENTINEL_OPEN}2 sigma (z_i-z_hat)^T(z_i-z_hat)+C{SENTINEL_CLOSE}",
            (226.6, 570.6, 370.3, 594.6),
        )
        previous_line.math_bboxes = [previous_line.bbox]
        previous_line.math_run_bboxes = [previous_line.bbox]
        previous_row = _RawBlockRec(lines=[previous_line])

        target_line = _line(
            f"{SENTINEL_OPEN}2 sigma ||z_i-z_hat||^2+C{SENTINEL_CLOSE}",
            (226.6, 596.7, 318.9, 622.6),
        )
        target_line.math_bboxes = [target_line.bbox]
        target_line.math_run_bboxes = [target_line.bbox]
        target = _RawBlockRec(lines=[target_line])

        following = _RawBlockRec(
            lines=[
                _line(
                    "There are different ways of combining multiple "
                    f"{SENTINEL_OPEN}z_i{SENTINEL_CLOSE} values.",
                    (107.7, 624.1, 504.4, 636.1),
                )
            ]
        )

        bridge = _inline_formula_bridge_block(
            0,
            [previous_row, target, following],
            [True, True, False],
            [False, False, False],
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

    def test_academic_statement_keeps_source_math_in_its_original_line_slots(self):
        block = TextBlock(
            page_index=0,
            bbox=(108.0, 100.0, 504.0, 124.0),
            text=f"Theorem 1. The iteration converges {SENTINEL_OPEN}x_t{SENTINEL_CLOSE} and",
            font_size=10.0,
            color=(0.0, 0.0, 0.0),
            bold_prefix=True,
            source_line_bboxes=(
                (108.0, 100.0, 504.0, 111.0),
                (108.0, 113.0, 370.0, 124.0),
            ),
            source_math_bboxes=((180.0, 113.0, 350.0, 124.0),),
            formula_anchors=((180.0, 113.0, 350.0, 124.0),),
        )

        self.assertTrue(_uses_fixed_source_math(block))

    def test_tiny_anchored_math_layout_demotes_to_inline_sprites(self):
        block = TextBlock(
            page_index=0,
            bbox=(40.0, 40.0, 260.0, 70.0),
            text=(
                f"Theorem 1. result {SENTINEL_OPEN}x{SENTINEL_CLOSE}"
            ),
            font_size=10.0,
            color=(0.0, 0.0, 0.0),
            source_lines=2,
            source_line_bboxes=(
                (40.0, 40.0, 260.0, 52.0),
                (40.0, 56.0, 260.0, 68.0),
            ),
            source_math_bboxes=((120.0, 56.0, 150.0, 68.0),),
            formula_anchors=((120.0, 56.0, 150.0, 68.0),),
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

        with patch(
            "pdf_zh_translator.pdf_layout._formula_anchored_layout",
            return_value=(4.0, False, []),
        ):
            updated = _demote_unanchorable_math_blocks(
                [(block, block.text)],
                [False],
                font_pack=font_pack,
                min_font_size=5.0,
                font_scale=1.0,
                margin=0.5,
            )

        self.assertTrue(updated[0][0].flow_inline_math)

    def test_formula_prose_bullets_share_font_size_with_body_siblings(self):
        from pdf_zh_translator.pdf_layout import _harmonize_sibling_list_items

        font = fitz.Font("helv")
        font_pack = FontPack(
            regular=font,
            regular_file=Path(""),
            bold=font,
            bold_file=Path(""),
            regular_alias="helv",
            bold_alias="helv",
        )
        items = [
            (
                TextBlock(
                    0,
                    (134.0, 453.0, 504.0, 474.0),
                    "• 1x1 and 3x3 filters keep the same dimensions.",
                    10.0,
                    (0.0, 0.0, 0.0),
                    block_type="formula_prose",
                    preserve_position=True,
                ),
                "• 1x1 and 3x3 filters keep identical output dimensions.",
            ),
            (
                TextBlock(
                    0,
                    (134.0, 478.0, 486.0, 489.0),
                    "• ReLU is applied to squeeze and expand layers.",
                    10.0,
                    (0.0, 0.0, 0.0),
                    block_type="formula_prose",
                    preserve_position=True,
                ),
                "• ReLU is applied to both layer families.",
            ),
            (
                TextBlock(
                    0,
                    (134.0, 492.0, 504.0, 515.0),
                    "• Dropout is applied after the final module.",
                    10.0,
                    (0.0, 0.0, 0.0),
                ),
                "• Dropout is applied after the final module.",
            ),
        ]

        harmonized = _harmonize_sibling_list_items(
            items,
            [False, False, False],
            font_pack=font_pack,
            min_font_size=5.0,
            font_scale=0.92,
            margin=0.8,
            page_height=792.0,
        )

        sizes = [item[0].fixed_translation_font_size for item in harmonized]
        self.assertTrue(all(size is not None for size in sizes))
        self.assertEqual(len(set(sizes)), 1)

    def test_standalone_heading_body_raster_overlap_is_flagged(self):
        from pdf_zh_translator.pdf_layout import _raster_ink_overlap_issues

        original = fitz.open()
        original_page = original.new_page(width=300, height=200)
        translated = fitz.open()
        translated_page = translated.new_page(width=300, height=200)
        translated_page.insert_font(
            fontname="cjkbold", fontfile="data/fonts/HiraginoSansGB-W6.ttf"
        )
        translated_page.insert_font(
            fontname="cjkbody", fontfile="data/fonts/SongtiSC-Regular.ttf"
        )
        translated_page.insert_text(
            (40, 60), "普通标题第一行", fontsize=12, fontname="cjkbold"
        )
        translated_page.insert_text(
            (40, 72), "节", fontsize=12, fontname="cjkbold"
        )
        translated_page.insert_text(
            (40, 78), "正文内容与标题发生重叠", fontsize=10, fontname="cjkbody"
        )
        source_heading = TextBlock(
            0,
            (40.0, 45.0, 150.0, 61.0),
            "Standalone heading",
            12.0,
            (0.0, 0.0, 0.0),
            bold=True,
            block_type="heading",
        )

        issues = _raster_ink_overlap_issues(
            original_page,
            translated_page,
            1,
            source_blocks=[source_heading],
        )
        translated.close()
        original.close()

        self.assertTrue(
            any(issue.code == "raster_heading_body_overlap" for issue in issues)
        )

    def test_layout_cascade_expands_body_and_shifts_same_column_followers(self):
        first = TextBlock(
            page_index=0,
            bbox=(40.0, 40.0, 260.0, 60.0),
            text="first paragraph",
            font_size=10.0,
            color=(0.0, 0.0, 0.0),
            source_lines=2,
        )
        follower = TextBlock(
            page_index=0,
            bbox=(40.0, 70.0, 260.0, 82.0),
            text="following heading",
            font_size=10.0,
            color=(0.0, 0.0, 0.0),
            bold=True,
            block_type="heading",
        )
        other_column = TextBlock(
            page_index=0,
            bbox=(320.0, 70.0, 540.0, 90.0),
            text="other column",
            font_size=10.0,
            color=(0.0, 0.0, 0.0),
            source_lines=2,
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

        with patch(
            "pdf_zh_translator.pdf_layout._sibling_group_item_height",
            side_effect=lambda block, *_args: 34.0 if block.text == first.text else 10.0,
        ):
            updated = _cascade_expand_page_items(
                [
                    (first, "long translated paragraph"),
                    (follower, "heading"),
                    (other_column, "other"),
                ],
                font_pack=font_pack,
                min_font_size=5.0,
                font_scale=1.0,
                margin=0.5,
                page_height=150.0,
            )

        expanded, shifted, untouched = [item[0] for item in updated]
        self.assertAlmostEqual(expanded.bbox[3], 75.6)
        self.assertAlmostEqual(shifted.bbox[1], 79.1)
        self.assertAlmostEqual(shifted.bbox[3], 91.1)
        self.assertAlmostEqual(shifted.bbox[1] - expanded.bbox[3], 3.5)
        self.assertEqual(untouched.bbox, other_column.bbox)

    def test_layout_cascade_expands_heading_and_shifts_body_at_role_size(self):
        heading = TextBlock(
            page_index=0,
            bbox=(50.0, 40.0, 286.0, 56.0),
            text="3. Mask R-CNN",
            font_size=12.0,
            color=(0.0, 0.0, 0.0),
            bold=True,
            block_type="heading",
        )
        body = TextBlock(
            page_index=0,
            bbox=(50.0, 66.0, 286.0, 96.0),
            text="following paragraph",
            font_size=10.0,
            color=(0.0, 0.0, 0.0),
            source_lines=3,
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

        with patch(
            "pdf_zh_translator.pdf_layout._sibling_group_item_height",
            side_effect=lambda block, *_args: 32.0 if block is heading else 20.0,
        ):
            updated = _cascade_expand_page_items(
                [(heading, "3. 掩码区域卷积神经网络（Mask R-CNN）"), (body, "正文")],
                font_pack=font_pack,
                min_font_size=5.0,
                font_scale=1.0,
                margin=0.5,
                page_height=150.0,
            )

        expanded, shifted = [item[0] for item in updated]
        self.assertGreater(expanded.bbox[3] - expanded.bbox[1], 30.0)
        self.assertGreater(shifted.bbox[1], body.bbox[1])

    def test_layout_cascade_does_not_cross_fixed_obstacle_without_slack(self):
        first = TextBlock(
            page_index=0,
            bbox=(40.0, 40.0, 260.0, 60.0),
            text="first paragraph",
            font_size=10.0,
            color=(0.0, 0.0, 0.0),
            source_lines=2,
        )
        follower = TextBlock(
            page_index=0,
            bbox=(40.0, 68.0, 260.0, 82.0),
            text="following paragraph",
            font_size=10.0,
            color=(0.0, 0.0, 0.0),
            source_lines=2,
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

        with patch(
            "pdf_zh_translator.pdf_layout._sibling_group_item_height",
            side_effect=lambda block, *_args: 34.0 if block.text == first.text else 13.0,
        ):
            updated = _cascade_expand_page_items(
                [(first, "long translated paragraph"), (follower, "following")],
                font_pack=font_pack,
                min_font_size=5.0,
                font_scale=1.0,
                margin=0.5,
                page_height=150.0,
                obstacles=[(38.0, 88.0, 262.0, 120.0)],
            )

        self.assertEqual([item[0].bbox for item in updated], [first.bbox, follower.bbox])

    def test_layout_cascade_wraps_into_free_side_beside_float(self):
        first = TextBlock(
            page_index=0,
            bbox=(40.0, 40.0, 300.0, 60.0),
            text="first paragraph",
            font_size=10.0,
            color=(0.0, 0.0, 0.0),
            source_lines=2,
        )
        follower = TextBlock(
            page_index=0,
            bbox=(40.0, 70.0, 185.0, 84.0),
            text="following paragraph",
            font_size=10.0,
            color=(0.0, 0.0, 0.0),
            source_lines=2,
        )
        # The target expansion ends at y=76, while this float starts lower.
        # It already intersects the movable follower, which has its own wrap
        # geometry, so the float must not become a global cascade floor.
        side_float = (190.0, 80.0, 300.0, 110.0)
        font = fitz.Font("helv")
        font_pack = FontPack(
            regular=font,
            regular_file=Path(""),
            bold=font,
            bold_file=Path(""),
            regular_alias="helv",
            bold_alias="helv",
        )

        with patch(
            "pdf_zh_translator.pdf_layout._cascade_required_height",
            return_value=36.0,
        ):
            updated = _cascade_expand_page_items(
                [(first, "long translated paragraph"), (follower, "following")],
                font_pack=font_pack,
                min_font_size=5.0,
                font_scale=1.0,
                margin=0.5,
                page_height=150.0,
                obstacles=[side_float],
                float_obstacles=[side_float],
            )

        expanded, shifted = [item[0] for item in updated]
        self.assertAlmostEqual(expanded.bbox[3], 76.0)
        self.assertGreater(shifted.bbox[1], follower.bbox[1])
        self.assertIn((188.0, 78.0, 302.0, 112.0), expanded.keepout_bboxes)

    def test_layout_cascade_ignores_obstacle_disjoint_from_wrapping_follower(self):
        first = TextBlock(
            page_index=0,
            bbox=(40.0, 40.0, 300.0, 60.0),
            text="first paragraph",
            font_size=10.0,
            color=(0.0, 0.0, 0.0),
            source_lines=2,
        )
        follower = TextBlock(
            page_index=0,
            bbox=(40.0, 70.0, 185.0, 84.0),
            text="following paragraph",
            font_size=10.0,
            color=(0.0, 0.0, 0.0),
            source_lines=2,
        )
        right_table_row = (190.0, 80.0, 300.0, 110.0)
        font = fitz.Font("helv")
        font_pack = FontPack(
            regular=font,
            regular_file=Path(""),
            bold=font,
            bold_file=Path(""),
            regular_alias="helv",
            bold_alias="helv",
        )

        with patch(
            "pdf_zh_translator.pdf_layout._cascade_required_height",
            return_value=36.0,
        ):
            updated = _cascade_expand_page_items(
                [(first, "long translated paragraph"), (follower, "following")],
                font_pack=font_pack,
                min_font_size=5.0,
                font_scale=1.0,
                margin=0.5,
                page_height=150.0,
                obstacles=[right_table_row],
            )

        expanded, shifted = [item[0] for item in updated]
        self.assertAlmostEqual(expanded.bbox[3], 76.0)
        self.assertGreater(shifted.bbox[1], follower.bbox[1])

    def test_multiline_body_borrows_space_above_when_formula_blocks_bottom(self):
        heading = TextBlock(
            page_index=0,
            bbox=(40.0, 40.0, 260.0, 50.0),
            text="3.1 Overview",
            font_size=11.0,
            color=(0.0, 0.0, 0.0),
            block_type="heading",
        )
        body = TextBlock(
            page_index=0,
            bbox=(40.0, 60.0, 260.0, 80.0),
            text="two-line body",
            font_size=10.0,
            color=(0.0, 0.0, 0.0),
            source_lines=2,
        )
        equation = (40.0, 81.0, 260.0, 96.0)
        font = fitz.Font("helv")
        font_pack = FontPack(
            regular=font,
            regular_file=Path(""),
            bold=font,
            bold_file=Path(""),
            regular_alias="helv",
            bold_alias="helv",
        )

        with patch(
            "pdf_zh_translator.pdf_layout.line_block_height",
            return_value=27.0,
        ):
            expanded = _expand_multiline_block_bbox(
                body,
                "需要保持正文字号的两行中文段落",
                [heading, body],
                font_pack,
                10.0,
                0.5,
                150.0,
                obstacles=[equation],
            )

        self.assertEqual(expanded.bbox, (40.0, 52.0, 260.0, 80.0))
        self.assertEqual(expanded.redact_bboxes, [body.bbox])

    def test_layout_cascade_probes_required_height_around_formula_keepouts(self):
        first = TextBlock(
            page_index=0,
            bbox=(40.0, 40.0, 260.0, 60.0),
            text="formula-rich paragraph",
            font_size=10.0,
            color=(0.0, 0.0, 0.0),
            source_lines=2,
            keepout_bboxes=[(120.0, 45.0, 160.0, 55.0)],
        )
        follower = TextBlock(
            page_index=0,
            bbox=(40.0, 70.0, 260.0, 82.0),
            text="following heading",
            font_size=10.0,
            color=(0.0, 0.0, 0.0),
            bold=True,
            block_type="heading",
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

        def fits_at_36_points_or_more(block, **_kwargs):
            return block.bbox[3] - block.bbox[1] >= 36.0

        with (
            patch(
                "pdf_zh_translator.pdf_layout._sibling_group_item_height",
                return_value=None,
            ),
            patch(
                "pdf_zh_translator.pdf_layout.translated_text_fits",
                side_effect=fits_at_36_points_or_more,
            ) as mock_fits,
        ):
            updated = _cascade_expand_page_items(
                [(first, "formula-rich translation"), (follower, "heading")],
                font_pack=font_pack,
                min_font_size=5.0,
                font_scale=1.0,
                margin=0.5,
                page_height=150.0,
            )

        expanded, shifted = [item[0] for item in updated]
        self.assertGreaterEqual(expanded.bbox[3] - expanded.bbox[1], 36.0)
        self.assertGreater(shifted.bbox[1], follower.bbox[1])
        self.assertLessEqual(mock_fits.call_count, 8)

    def test_layout_cascade_expands_fixed_formula_body_without_moving_formula(self):
        formula_body = TextBlock(
            page_index=0,
            bbox=(40.0, 40.0, 260.0, 60.0),
            text=f"translated prose {SENTINEL_OPEN}x=y{SENTINEL_CLOSE}",
            font_size=10.0,
            color=(0.0, 0.0, 0.0),
            source_lines=2,
            block_type="formula_prose",
            preserve_position=True,
            keepout_bboxes=[(120.0, 45.0, 160.0, 55.0)],
            formula_anchors=((120.0, 45.0, 160.0, 55.0),),
            preserved_math_placeholders=(0,),
        )
        follower = TextBlock(
            page_index=0,
            bbox=(40.0, 70.0, 260.0, 82.0),
            text="following paragraph",
            font_size=10.0,
            color=(0.0, 0.0, 0.0),
            source_lines=1,
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

        with patch(
            "pdf_zh_translator.pdf_layout._cascade_required_height",
            return_value=38.0,
        ):
            updated = _cascade_expand_page_items(
                [(formula_body, "公式说明译文"), (follower, "后续正文")],
                font_pack=font_pack,
                min_font_size=5.0,
                font_scale=1.0,
                margin=0.5,
                page_height=150.0,
            )

        expanded, shifted = [item[0] for item in updated]
        self.assertEqual(expanded.bbox[:3], formula_body.bbox[:3])
        self.assertAlmostEqual(expanded.bbox[3], 78.0)
        self.assertEqual(expanded.formula_anchors, formula_body.formula_anchors)
        self.assertGreater(shifted.bbox[1], follower.bbox[1])

    def test_layout_cascade_expands_anchored_caption_into_available_space(self):
        caption = TextBlock(
            page_index=0,
            bbox=(40.0, 40.0, 260.0, 54.0),
            text="Figure 3: A long caption.",
            font_size=10.0,
            color=(0.0, 0.0, 0.0),
            source_lines=1,
            block_type="caption",
            preserve_position=True,
        )
        heading = TextBlock(
            page_index=0,
            bbox=(40.0, 75.0, 260.0, 90.0),
            text="4 Results",
            font_size=12.0,
            color=(0.0, 0.0, 0.0),
            block_type="heading",
            bold=True,
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

        with patch(
            "pdf_zh_translator.pdf_layout._cascade_required_height",
            return_value=30.0,
        ):
            updated = _cascade_expand_page_items(
                [(caption, "图3：这是一段需要两行空间的较长图注。"), (heading, "4 结果")],
                font_pack=font_pack,
                min_font_size=5.0,
                font_scale=1.0,
                margin=0.5,
                page_height=150.0,
            )

        expanded, shifted = [item[0] for item in updated]
        self.assertEqual(expanded.bbox[1], caption.bbox[1])
        self.assertAlmostEqual(expanded.bbox[3], 70.0)
        self.assertEqual(shifted.bbox[1], heading.bbox[1])
        self.assertGreaterEqual(shifted.bbox[1] - expanded.bbox[3], 3.5)

    def test_formula_raster_adds_transparent_margin_without_rescaling_ink(self):
        source = fitz.open()
        source_page = source.new_page(width=200, height=100)
        source_page.draw_line((40, 40), (60, 55), color=(0, 0, 0), width=1)
        target = fitz.open()
        target_page = target.new_page(width=200, height=100)

        _insert_source_region_raster(
            target_page,
            source,
            0,
            fitz.Rect(40, 40, 60, 60),
            fitz.Rect(40, 40, 60, 60),
        )

        image = next(
            block
            for block in target_page.get_text("dict")["blocks"]
            if block.get("type") == 1
        )
        mask = fitz.Pixmap(image["mask"])
        samples = mask.samples
        self.assertFalse(any(samples[: mask.width]))
        self.assertFalse(any(samples[-mask.width :]))
        self.assertTrue(any(samples))
        rendered = target_page.get_pixmap(
            clip=fitz.Rect(image["bbox"]),
            dpi=180,
            colorspace=fitz.csGRAY,
            alpha=False,
        )
        self.assertTrue(any(sample < 180 for sample in rendered.samples))
        bbox = fitz.Rect(image["bbox"])
        self.assertLess(bbox.x0, 40.0)
        self.assertGreater(bbox.x1, 60.0)

        target.close()
        source.close()

    def test_formula_source_clip_falls_back_when_trimmed_region_has_no_ink(self):
        source = fitz.open()
        page = source.new_page(width=200, height=100)
        page.draw_line((40.0, 50.0), (60.0, 50.0), color=(0, 0, 0), width=1.0)
        core = (39.0, 49.0, 61.0, 51.0)
        padded = (38.0, 47.0, 62.0, 53.0)

        with patch(
            "pdf_zh_translator.pdf_layout._trim_formula_clip_against_foreign_ink",
            return_value=(38.0, 47.0, 62.0, 48.0),
        ):
            selected = _select_formula_source_rect(source, 0, core, padded)

        self.assertEqual(selected, padded)
        source.close()

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

    def test_redaction_preserves_selectable_label_grazing_caption(self):
        document = fitz.open()
        page = document.new_page(width=300, height=120)
        page.insert_text((40.0, 50.0), "iter. (1e4)", fontsize=10.0)
        page.insert_text((40.0, 63.0), "Figure 6. Training results.", fontsize=10.0)
        label_bbox = tuple(page.search_for("iter. (1e4)")[0])
        caption_bbox = tuple(page.search_for("Figure 6. Training results.")[0])
        self.assertGreater(label_bbox[3], caption_bbox[1])
        block = TextBlock(
            page_index=0,
            bbox=caption_bbox,
            text="Figure 6. Training results.",
            font_size=10.0,
            color=(0.0, 0.0, 0.0),
            block_type="caption",
            redact_bboxes=[caption_bbox],
        )

        redact_original_text(
            page,
            [block],
            margin=0.8,
            protected_regions=[label_bbox],
        )

        extracted = page.get_text("text")
        document.close()
        self.assertIn("iter. (1e4)", extracted)
        self.assertNotIn("Figure 6", extracted)

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

    def test_formula_anchor_absorbs_adjacent_leading_root_operator(self):
        radicand = (457.6, 458.6, 477.0, 468.6)
        root = (447.7, 457.3, 457.6, 467.2)

        anchors = _align_formula_anchors((radicand,), 1, (root,))

        self.assertEqual(anchors, ((447.7, 457.3, 477.0, 468.6),))

    def test_merge_propagates_inline_formula_flow(self):
        previous = TextBlock(
            0,
            (100.0, 100.0, 500.0, 112.0),
            "The formula is x.",
            10.0,
            (0.0, 0.0, 0.0),
            flow_inline_math=True,
        )
        following = TextBlock(
            0,
            (100.0, 112.0, 500.0, 124.0),
            "The next sentence continues.",
            10.0,
            (0.0, 0.0, 0.0),
        )

        merged = merge_paragraph_blocks([previous, following])

        self.assertEqual(len(merged), 1)
        self.assertTrue(merged[0].flow_inline_math)

    def test_restored_formula_runs_keep_anchor_boundaries(self):
        from pdf_zh_translator.pdf_layout import _restore_unit_translation

        anchors = (
            (100.0, 100.0, 120.0, 110.0),
            (120.0, 104.0, 135.0, 112.0),
        )
        block = TextBlock(
            page_index=0,
            bbox=(80.0, 90.0, 260.0, 130.0),
            text="formula",
            font_size=10.0,
            color=(0.0, 0.0, 0.0),
            preserved_math_placeholders=(0, 1),
            formula_anchors=anchors,
        )

        restored, missing = _restore_unit_translation(
            "结果 ⟦0⟧ ⟦1⟧ 成立",
            {0: "z^{temp}", 1: "_{ℓ-1}"},
            block,
        )
        tokens = _tokenize_translation_with_formula_clips(restored, block)

        self.assertEqual(missing, [])
        self.assertEqual(len(SENTINEL_RUN_RE.findall(restored)), 2)
        self.assertTrue(any(token.kind == "formula" for token in tokens))
        self.assertFalse(
            any(token.kind == "word" and "_{" in token.text for token in tokens)
        )

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

    def test_formula_tokenizer_redraws_unambiguous_dimension_formula(self):
        block = TextBlock(
            page_index=0,
            bbox=(80.0, 90.0, 300.0, 130.0),
            text="formula",
            font_size=10.0,
            color=(0.0, 0.0, 0.0),
            formula_anchors=((100.0, 100.0, 180.0, 120.0),),
        )

        tokens = _tokenize_translation_with_formula_clips(
            f"特征 {SENTINEL_OPEN}G→R^{{C}}^{{→}}^{{H}}^{{→}}^{{W}}{SENTINEL_CLOSE}",
            block,
        )

        formula = next(token for token in tokens if token.kind == "formula")
        self.assertEqual(formula.text, "G∈R^{C×H×W}")
        self.assertEqual(formula.source_bbox, (100.0, 100.0, 180.0, 120.0))

    def test_formula_tokenizer_redraws_kernel_size_without_neighbor_clip(self):
        block = TextBlock(
            page_index=0,
            bbox=(80.0, 90.0, 300.0, 130.0),
            text="formula",
            font_size=10.0,
            color=(0.0, 0.0, 0.0),
            formula_anchors=((100.0, 100.0, 118.0, 118.0),),
        )

        tokens = _tokenize_translation_with_formula_clips(
            f"使用 {SENTINEL_OPEN}1↑1{SENTINEL_CLOSE} 卷积",
            block,
        )

        formula = next(token for token in tokens if token.kind == "formula")
        self.assertEqual(formula.text, "1×1")
        self.assertEqual(formula.source_bbox, (100.0, 100.0, 118.0, 118.0))

    def test_formula_accessibility_text_normalizes_math_alphabet_glyphs(self):
        self.assertEqual(
            _normalize_formula_accessibility_text("𝑦_{𝑖}=𝑓(𝑥_{𝑖})."),
            "y_{i}=f(x_{i}).",
        )
        self.assertEqual(
            _normalize_formula_accessibility_text(
                "𝐾_{𝑗}=(𝑘_{(𝑗−1)𝐵+1},...,𝑘_{𝑗𝐵})"
            ),
            "K_{j}=(k_{(j-1)B+1},...,k_{jB})",
        )

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

    def test_same_row_formula_suffix_reflows_below_preserved_formula(self):
        prefix_math = (150.0, 100.0, 270.0, 111.0)
        formula_prefix = _LineRec(
            text=(
                "defined as"
                f"{SENTINEL_OPEN}p_k(x)=exp(a_k(x))/{SENTINEL_CLOSE}"
            ),
            bbox=(100.0, 100.0, 270.0, 111.0),
            spans=[
                _span("defined as", (100.0, 100.0, 150.0, 110.0)),
                _span(
                    "p_k(x)=exp(a_k(x))/",
                    prefix_math,
                    font="CMMI10",
                ),
            ],
            prose_bboxes=[(100.0, 100.0, 150.0, 110.0)],
            math_bboxes=[prefix_math],
            math_run_bboxes=[prefix_math],
        )
        formula_tail = _LineRec(
            text=f"{SENTINEL_OPEN}sum exp(a_k(x)){SENTINEL_CLOSE}",
            bbox=(270.0, 96.0, 360.0, 113.0),
            spans=[
                _span(
                    "sum exp(a_k(x))",
                    (270.0, 96.0, 360.0, 113.0),
                    font="CMMI10",
                )
            ],
            math_bboxes=[(270.0, 96.0, 360.0, 113.0)],
            math_run_bboxes=[(270.0, 96.0, 360.0, 113.0)],
        )
        suffix_math = (395.0, 100.0, 420.0, 111.0)
        formula_suffix = _LineRec(
            text=(
                "where"
                f"{SENTINEL_OPEN}a_k(x){SENTINEL_CLOSE}"
                " denotes the"
            ),
            bbox=(364.0, 100.0, 490.0, 111.0),
            spans=[
                _span("where", (364.0, 100.0, 395.0, 110.0)),
                _span("a_k(x)", suffix_math, font="CMMI10"),
                _span(" denotes the", (420.0, 100.0, 490.0, 110.0)),
            ],
            prose_bboxes=[
                (364.0, 100.0, 395.0, 110.0),
                (420.0, 100.0, 490.0, 110.0),
            ],
            math_bboxes=[suffix_math],
            math_run_bboxes=[suffix_math],
        )
        record = _RawBlockRec(
            lines=[formula_prefix, formula_tail, formula_suffix]
        )

        segments = segments_from_record(0, record)

        suffix = next(
            segment
            for segment in segments
            if "where" in strip_sentinels(segment.text)
        )
        self.assertGreaterEqual(suffix.bbox[1], formula_tail.bbox[3] + 1.0)
        self.assertEqual(suffix.source_line_bboxes[0], formula_suffix.bbox)

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

    def test_equation_formula_tail_starts_at_to_be_before_where_clause(self):
        raw_block = {
            "type": 0,
            "bbox": (108.0, 664.0, 504.0, 687.7),
            "lines": [
                {
                    "bbox": (108.0, 665.1, 293.7, 676.7),
                    "spans": [
                        _span(
                            "To be concrete, let the singular values of",
                            (108.0, 666.7, 273.6, 676.7),
                        ),
                        _span(" U", (273.6, 666.5, 283.6, 676.5), font="CMMI10"),
                        _span("i", (284.6, 665.2, 287.5, 672.2), size=7.0, font="CMMI7"),
                    ],
                },
                {
                    "bbox": (283.6, 664.0, 305.4, 679.2),
                    "spans": [
                        _span("A", (283.6, 671.6, 289.5, 678.6), size=7.0, font="CMMI7"),
                        _span(" U", (294.2, 666.5, 301.0, 676.5), font="CMMI10"),
                        _span("j", (301.0, 664.0, 305.4, 675.8), size=7.0, font="CMMI7"),
                    ],
                },
                {
                    "bbox": (301.0, 666.4, 504.0, 679.6),
                    "spans": [
                        _span("B", (301.0, 671.8, 307.0, 678.7), size=7.0, font="CMMI7"),
                        _span(" to be", (311.0, 666.7, 331.2, 676.7)),
                        _span(" sigma", (331.2, 666.5, 388.6, 676.5), font="CMMI10"),
                        _span(" where", (392.7, 666.7, 420.7, 678.2)),
                        _span(" p=min{i,j}", (420.7, 666.5, 483.5, 676.5), font="CMMI10"),
                        _span(". We", (483.5, 666.7, 504.0, 676.7)),
                    ],
                },
                {
                    "bbox": (108.0, 677.7, 367.6, 687.7),
                    "spans": [
                        _span(
                            "know that the Projection Metric is defined as:",
                            (108.0, 677.7, 367.6, 687.7),
                        )
                    ],
                },
            ],
        }

        record, _ = parse_block_lines(raw_block, page_width=612.0)
        self.assertIsNotNone(record)
        segments = segments_from_record(0, record, equation_record=True)
        exported = " ".join(strip_sentinels(segment.text) for segment in segments)
        clause = next(segment for segment in segments if "where" in segment.text)

        self.assertIn("to be", exported)
        self.assertIn("know that the Projection Metric", exported)
        self.assertTrue(clause.source_math_bboxes)

    def test_formula_tail_prefix_registers_nearby_fraction_as_keepout(self):
        raw_block = {
            "type": 0,
            "bbox": (108.0, 515.1, 501.8, 553.6),
            "lines": [
                {
                    "bbox": (108.0, 517.8, 383.8, 527.9),
                    "spans": [
                        _span(
                            "One can naturally consider a feature amplification "
                            "factor as the ratio",
                            (108.0, 517.8, 383.8, 527.9),
                        )
                    ],
                },
                {
                    "bbox": (396.7, 515.1, 424.3, 522.8),
                    "spans": [
                        _span("numerator", (396.7, 515.1, 424.3, 522.8), size=7.0, font="CMMI7")
                    ],
                },
                {
                    "bbox": (387.8, 517.7, 501.8, 532.5),
                    "spans": [
                        _span("denominator", (387.8, 523.1, 433.2, 531.2), size=7.0, font="CMMI7"),
                        _span(", where", (435.8, 517.9, 465.4, 527.9)),
                        _span(" U", (465.4, 517.7, 475.0, 527.7), font="CMMI10"),
                        _span(" and", (475.0, 517.9, 493.2, 527.9)),
                        _span(" V", (493.2, 517.7, 501.8, 527.7), font="CMMI10"),
                    ],
                },
                {
                    "bbox": (108.0, 532.7, 501.8, 542.7),
                    "spans": [
                        _span(
                            "are the left- and right-singular matrices of the decomposition.",
                            (108.0, 532.7, 501.8, 542.7),
                        )
                    ],
                },
            ],
        }

        record, _ = parse_block_lines(raw_block, page_width=612.0)
        self.assertIsNotNone(record)
        segments = segments_from_record(0, record, equation_record=True)
        prefix = next(segment for segment in segments if "ratio" in segment.text)

        self.assertTrue(prefix.keepout_bboxes)
        self.assertTrue(any(bbox[0] <= 388.0 for bbox in prefix.keepout_bboxes))
        self.assertTrue(any(bbox[0] >= 396.0 for bbox in prefix.keepout_bboxes))

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
    def test_formula_clip_height_contributes_to_line_height(self):
        formula = _Token(
            "formula",
            "x/y",
            source_bbox=(0.0, 0.0, 20.0, 20.0),
            source_size=10.0,
        )

        self.assertGreaterEqual(line_block_height([[formula]], 10.0, 1.2), 16.0)

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

    def test_clip_trimmed_against_same_line_neighbor(self):
        from pdf_zh_translator.pdf_layout import _trim_formula_clip_against_foreign_ink

        document = fitz.open()
        page = document.new_page(width=612, height=792)
        page.insert_text((50, 100), "neighbor", fontsize=10)
        span = page.get_text("dict")["blocks"][0]["lines"][0]["spans"][0]
        sx0, sy0, sx1, sy1 = span["bbox"]
        clip = (sx1 - 0.25, sy0, sx1 + 12.0, sy1)

        trimmed = _trim_formula_clip_against_foreign_ink(document, 0, clip)

        self.assertGreaterEqual(trimmed[0], sx1)
        self.assertEqual(trimmed[1], clip[1])
        self.assertEqual(trimmed[2], clip[2])
        self.assertEqual(trimmed[3], clip[3])
        document.close()

    def test_clip_trimmed_above_wide_following_line(self):
        from pdf_zh_translator.pdf_layout import _trim_formula_clip_against_foreign_ink

        document = fitz.open()
        page = document.new_page(width=612, height=792)
        page.insert_text((50, 100), "1x1", fontsize=10)
        page.insert_text((50, 112), "learning continues on the next line", fontsize=10)
        spans = [
            span
            for block in page.get_text("dict")["blocks"]
            for line in block.get("lines", [])
            for span in line.get("spans", [])
        ]
        formula_bbox = spans[0]["bbox"]
        following_top = spans[1]["bbox"][1]
        clip = (
            formula_bbox[0] - 0.25,
            formula_bbox[1] - 0.25,
            formula_bbox[2] + 0.25,
            following_top + (formula_bbox[3] - formula_bbox[1]) * 0.4,
        )

        trimmed = _trim_formula_clip_against_foreign_ink(document, 0, clip)

        # The cut must exclude the following line's ink but may sit inside
        # its bbox headroom: the edge is relocated into the real whitespace
        # gap so tight source leading cannot slice formula sub/superscripts.
        self.assertLess(trimmed[3], clip[3])
        pixmap = document[0].get_pixmap(
            matrix=fitz.Matrix(6, 6),
            clip=fitz.Rect(trimmed),
            colorspace=fitz.csGRAY,
            alpha=False,
        )
        width = pixmap.width
        bottom_rows = pixmap.samples[-2 * width :]
        self.assertFalse(any(sample < 205 for sample in bottom_rows))
        document.close()

    def test_clip_uses_least_ink_row_when_tight_leading_has_no_clean_gap(self):
        from pdf_zh_translator import pdf_layout

        document = SimpleNamespace(
            _pdfzh_span_cache={0: [(0.0, 8.0, 10.0, 12.0)]}
        )
        profile = [
            (0.0, 0.08),
            (1.0, 0.10),
            (2.0, 0.12),
            (3.0, 0.09),
            (4.0, 0.07),
            (5.0, 0.05),
            (6.0, 0.03),
            (7.0, 0.02),
            (8.0, 0.012),
            (9.0, 0.018),
            (10.0, 0.04),
        ]

        with patch.object(pdf_layout, "_clip_row_ink_profile", return_value=profile):
            trimmed = pdf_layout._trim_formula_clip_against_foreign_ink(
                document,
                0,
                (0.0, 0.0, 10.0, 10.0),
            )

        self.assertAlmostEqual(trimmed[3], 8.0)

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

    def test_formula_scale_caps_tall_clip_to_line_height(self):
        from pdf_zh_translator.pdf_layout import _formula_clip_scale

        scale = _formula_clip_scale((0.0, 0.0, 20.0, 20.0), 10.0, 10.0)

        self.assertAlmostEqual(scale, 0.8)
        self.assertLessEqual(20.0 * scale, 16.0)


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

    def test_stacked_full_width_prose_with_inflated_bboxes_is_not_flagged(self):
        """Math-heavy paragraphs get bboxes inflated by sub/superscripts, so
        consecutive full-column blocks interlock vertically. They are laid
        out line by line, not overprinted, and must stay translatable
        (SafeTransport p4 regression)."""
        from pdf_zh_translator.pdf_layout import _overlapping_translation_block_bboxes

        first = TextBlock(
            page_index=0,
            bbox=(108.0, 342.0, 506.0, 373.0),
            text="Definition 5 (Safety Budget Allocation). For each constraint k,",
            font_size=10.0,
            color=(0.0, 0.0, 0.0),
            source_line_bboxes=(
                (108.0, 342.0, 506.0, 353.0),
                (108.0, 354.0, 506.0, 365.0),
            ),
        )
        second = TextBlock(
            page_index=0,
            bbox=(108.0, 353.0, 505.0, 380.0),
            text="distributes the budget across edges with induced capacity.",
            font_size=10.0,
            color=(0.0, 0.0, 0.0),
            source_line_bboxes=(
                (108.0, 366.0, 505.0, 377.0),
                (108.0, 378.0, 505.0, 380.0),
            ),
        )
        nested = TextBlock(
            page_index=0,
            bbox=(107.0, 442.0, 504.0, 521.0),
            text="Lemma 6 (Flow-Occupancy Mapping). Let Phi map rho to F.",
            font_size=10.0,
            color=(0.0, 0.0, 0.0),
            source_line_bboxes=(
                (107.0, 442.0, 504.0, 452.0),
                (107.0, 468.0, 504.0, 478.0),
                (107.0, 510.0, 504.0, 521.0),
            ),
        )
        continuation = TextBlock(
            page_index=0,
            bbox=(108.0, 454.0, 234.0, 466.0),
            text="the realizable gamma-flow set F real",
            font_size=10.0,
            color=(0.0, 0.0, 0.0),
            source_line_bboxes=((108.0, 454.0, 234.0, 466.0),),
        )

        flagged = _overlapping_translation_block_bboxes(
            [first, second, nested, continuation]
        )

        self.assertEqual(flagged, [])


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
    def test_full_page_multi_panel_figure_has_one_preserved_envelope(self):
        from pdf_zh_translator.pdf_layout import _captioned_composite_figure_regions

        caption = TextBlock(
            page_index=0,
            bbox=(50.0, 632.0, 545.0, 654.0),
            text="Figure 19: Complete annotation guidelines.",
            font_size=10.0,
            color=(0.0, 0.0, 0.0),
            source_lines=2,
            block_type="caption",
        )
        graphic_regions = [
            (163.0, 60.0, 557.0, 158.0),
            (38.0, 162.0, 557.0, 260.0),
            (38.0, 279.0, 306.0, 377.0),
            (289.0, 381.0, 557.0, 480.0),
            (38.0, 491.0, 557.0, 589.0),
        ]

        regions = _captioned_composite_figure_regions([caption], graphic_regions)

        self.assertEqual(regions, [(38.0, 60.0, 557.0, 589.0)])

    def test_composite_figure_envelope_does_not_cross_another_caption(self):
        from pdf_zh_translator.pdf_layout import _captioned_composite_figure_regions

        first_caption = TextBlock(
            page_index=0,
            bbox=(50.0, 260.0, 545.0, 280.0),
            text="Figure 1: First experiment.",
            font_size=10.0,
            color=(0.0, 0.0, 0.0),
            block_type="caption",
        )
        second_caption = replace(
            first_caption,
            bbox=(50.0, 610.0, 545.0, 630.0),
            text="Figure 2: Second experiment.",
        )
        graphic_regions = [
            (50.0, 60.0, 250.0, 150.0),
            (300.0, 60.0, 550.0, 150.0),
            (50.0, 300.0, 250.0, 390.0),
            (300.0, 300.0, 550.0, 390.0),
            (50.0, 420.0, 250.0, 510.0),
            (300.0, 420.0, 550.0, 510.0),
        ]

        regions = _captioned_composite_figure_regions(
            [first_caption, second_caption], graphic_regions
        )

        self.assertEqual(regions, [])

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

    def test_keeps_standalone_bold_heading_above_formula_rich_body_separate(self):
        heading = TextBlock(
            page_index=4,
            bbox=(70.9, 397.4, 161.1, 411.7),
            text="Critic learning",
            font_size=14.35,
            color=(0.0, 0.0, 0.0),
            bold=True,
            starts_bold=True,
            source_lines=1,
            source_line_bboxes=((70.9, 397.4, 161.1, 411.7),),
        )
        body = TextBlock(
            page_index=4,
            bbox=(70.5, 421.1, 543.1, 461.9),
            text=(
                "The actor and critic learn from abstract trajectories predicted "
                f"by the world model{SENTINEL_OPEN}^{{14}}{SENTINEL_CLOSE}. "
                "They operate on recurrent model states."
            ),
            font_size=12.03,
            color=(0.0, 0.0, 0.0),
            source_lines=3,
            source_line_bboxes=(
                (70.5, 421.1, 543.1, 433.0),
                (70.9, 434.2, 541.1, 447.5),
                (70.9, 449.9, 541.1, 461.9),
            ),
            source_math_bboxes=((240.6, 434.2, 248.5, 442.2),),
        )

        merged = merge_paragraph_blocks([heading, body])

        self.assertEqual(len(merged), 2)
        self.assertEqual(merged[0].text, "Critic learning")
        self.assertAlmostEqual(merged[1].font_size, 12.03)

    def test_keeps_action_skeleton_chain_separate_from_run_in_label(self):
        label = TextBlock(
            page_index=0,
            bbox=(155.3, 282.8, 234.3, 292.8),
            text="Action Skeleton:",
            font_size=10.0,
            color=(0.0, 0.0, 0.0),
            bold=True,
            starts_bold=True,
            block_type="heading",
            source_lines=1,
        )
        chain = TextBlock(
            page_index=0,
            bbox=(234.3, 282.5, 481.9, 292.8),
            text="pick(hook) -> pull(cube, hook) -> place(hook) -> pick(cube)",
            font_size=10.0,
            color=(0.0, 0.0, 0.0),
            block_type="body",
            source_lines=1,
        )

        self.assertEqual(len(merge_paragraph_blocks([label, chain])), 2)

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

    def test_merges_short_math_lead_with_same_line_formula_prose(self):
        lead = TextBlock(
            page_index=14,
            bbox=(75.4, 66.8, 176.8, 80.7),
            text=(
                f"Let{SENTINEL_OPEN}delta(z_0)={SENTINEL_CLOSE} "
                f"{SENTINEL_OPEN}exp(-E(z_0|c)){SENTINEL_CLOSE}"
            ),
            font_size=5.0,
            color=(0.0, 0.0, 0.0),
            source_lines=2,
            source_line_bboxes=(
                (75.4, 69.9, 122.7, 80.7),
                (144.2, 66.8, 176.8, 75.3),
            ),
        )
        continuation = TextBlock(
            page_index=14,
            bbox=(126.7, 69.8, 542.1, 86.0),
            text=(
                f"{SENTINEL_OPEN}E_q[exp(-E)]-E(z_0|c){SENTINEL_CLOSE} "
                "represents the difference between the ideal and approximate weighting"
            ),
            font_size=5.0,
            color=(0.0, 0.0, 0.0),
            source_line_bboxes=((126.7, 69.8, 542.1, 86.0),),
        )

        merged = merge_paragraph_blocks([lead, continuation])

        self.assertEqual(len(merged), 1)
        self.assertIn("represents the difference", merged[0].text)

    def test_merges_deeply_overlapping_inline_fraction_continuations(self):
        first = TextBlock(
            page_index=14,
            bbox=(75.4, 85.6, 543.1, 120.8),
            text=(
                "The noise magnitude is approximately "
                f"{SENTINEL_OPEN}sqrt(d) approximately{SENTINEL_CLOSE}"
            ),
            font_size=10.0,
            color=(0.0, 0.0, 0.0),
        )
        second = TextBlock(
            page_index=14,
            bbox=(75.1, 100.9, 541.4, 159.7),
            text=(
                f"{SENTINEL_OPEN}sqrt(d){SENTINEL_CLOSE}. The components are "
                "independent and the accumulated error tends to scale with"
            ),
            font_size=10.0,
            color=(0.0, 0.0, 0.0),
        )
        third = TextBlock(
            page_index=14,
            bbox=(75.4, 138.6, 541.4, 169.4),
            text=(
                f"{SENTINEL_OPEN}sqrt(d){SENTINEL_CLOSE} in expectation, as the "
                "contributions from different dimensions accumulate."
            ),
            font_size=10.0,
            color=(0.0, 0.0, 0.0),
        )

        merged = merge_paragraph_blocks([first, second, third])

        self.assertEqual(len(merged), 1)
        self.assertIn("different dimensions", merged[0].text)

    def test_formula_row_connector_merges_without_becoming_layout_origin(self):
        connector = TextBlock(
            page_index=6,
            bbox=(264.8, 314.2, 279.7, 324.2),
            text="and",
            font_size=9.96,
            color=(0.0, 0.0, 0.0),
            source_lines=1,
            redact_bboxes=[(264.8, 314.2, 279.7, 324.2)],
            keepout_bboxes=[(264.8, 314.2, 279.7, 324.2)],
            source_line_bboxes=((264.8, 314.2, 279.7, 324.2),),
        )
        prose = TextBlock(
            page_index=6,
            bbox=(107.6, 332.5, 504.0, 357.0),
            text=(
                f"Here {SENTINEL_OPEN}u,v{SENTINEL_CLOSE} are two scaling "
                f"variables satisfying {SENTINEL_OPEN}u*v=1{SENTINEL_CLOSE}"
            ),
            font_size=9.96,
            color=(0.0, 0.0, 0.0),
            source_lines=2,
            source_line_bboxes=(
                (107.6, 332.5, 504.0, 346.0),
                (108.0, 347.0, 157.3, 357.0),
            ),
            source_math_bboxes=((126.9, 336.0, 146.3, 346.0),),
        )

        merged = merge_paragraph_blocks([connector, prose])

        self.assertEqual(len(merged), 1)
        self.assertTrue(merged[0].text.startswith("and Here"))
        self.assertEqual(merged[0].bbox, prose.bbox)
        self.assertIn(connector.bbox, merged[0].redact_bboxes)

    def test_does_not_merge_deep_overlap_without_formula_bridge_cue(self):
        first = TextBlock(
            page_index=0,
            bbox=(75.0, 100.0, 543.0, 140.0),
            text=(
                f"A paragraph contains {SENTINEL_OPEN}x{SENTINEL_CLOSE} "
                "but ends with unrelated prose"
            ),
            font_size=10.0,
            color=(0.0, 0.0, 0.0),
        )
        unrelated = TextBlock(
            page_index=0,
            bbox=(75.0, 118.0, 543.0, 160.0),
            text=f"{SENTINEL_OPEN}y{SENTINEL_CLOSE} starts another paragraph",
            font_size=10.0,
            color=(0.0, 0.0, 0.0),
        )

        self.assertEqual(len(merge_paragraph_blocks([first, unrelated])), 2)

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

    def test_segments_preserve_fragmented_single_row_numbered_equation(self):
        def line(text, bbox, *, math=False):
            return _LineRec(
                text=text,
                bbox=bbox,
                spans=[
                    {
                        "text": strip_sentinels(text),
                        "bbox": bbox,
                        "size": 12.0,
                        "flags": 0,
                        "color": 0,
                    }
                ],
                math_bboxes=[bbox] if math else [],
                math_run_bboxes=[bbox] if math else [],
            )

        record = _RawBlockRec(
            lines=[
                line("Actor:", (172.9, 531.6, 203.4, 543.6)),
                line(
                    f"{SENTINEL_OPEN}a_t ∼ π(a_t | s_t){SENTINEL_CLOSE}",
                    (225.4, 531.2, 300.2, 545.0),
                    math=True,
                ),
                line("Critic:", (334.0, 531.6, 364.6, 543.6)),
                line(
                    f"{SENTINEL_OPEN}v(R_t | s_t){SENTINEL_CLOSE}",
                    (386.5, 531.2, 439.1, 545.0),
                    math=True,
                ),
                line("(4)", (528.0, 531.7, 541.9, 543.7)),
            ]
        )

        self.assertEqual(segments_from_record(4, record, equation_record=True), [])

    def test_segments_translate_multiline_prose_with_numbered_inline_math(self):
        formula = f"{SENTINEL_OPEN}v_t = E[v(s_t)]{SENTINEL_CLOSE}"
        record = _RawBlockRec(
            lines=[
                _LineRec(
                    text="The critic predicts a distribution from model states.",
                    bbox=(70.9, 553.4, 541.1, 565.3),
                    spans=[
                        {
                            "text": "The critic predicts a distribution from model states.",
                            "bbox": (70.9, 553.4, 541.1, 565.3),
                            "size": 12.0,
                            "flags": 0,
                            "color": 0,
                        }
                    ],
                    prose_bboxes=[(70.9, 553.4, 541.1, 565.3)],
                ),
                _LineRec(
                    text=f"Its predicted value is {formula} for every state.",
                    bbox=(70.9, 567.5, 541.1, 580.4),
                    spans=[
                        {
                            "text": "Its predicted value is for every state.",
                            "bbox": (70.9, 567.5, 541.1, 580.4),
                            "size": 12.0,
                            "flags": 0,
                            "color": 0,
                        }
                    ],
                    prose_bboxes=[(70.9, 567.5, 541.1, 580.4)],
                    math_bboxes=[(190.0, 567.5, 260.0, 580.4)],
                    math_run_bboxes=[(190.0, 567.5, 260.0, 580.4)],
                ),
                _LineRec(
                    text="(4)",
                    bbox=(528.0, 567.7, 541.9, 579.7),
                    spans=[
                        {
                            "text": "(4)",
                            "bbox": (528.0, 567.7, 541.9, 579.7),
                            "size": 12.0,
                            "flags": 0,
                            "color": 0,
                        }
                    ],
                ),
            ]
        )

        blocks = segments_from_record(4, record, equation_record=True)

        self.assertTrue(any("critic predicts" in strip_sentinels(block.text) for block in blocks))

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
        self.assertEqual(blocks[0].block_type, "run_in_heading")
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
        self.assertEqual(blocks[0].block_type, "run_in_heading")
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

    def test_repeated_running_header_ignores_trailing_page_number(self):
        from pdf_zh_translator.pdf_layout import _repeated_top_header_texts

        blocks = [
            self._make_block(
                f"Learning Transferable Visual Models From Natural Language Supervision {page}",
                bbox=(55.4, 47.3, 541.4, 57.2),
                page=page - 1,
            )
            for page in range(1, 7)
        ]

        repeated = _repeated_top_header_texts(blocks, page_count=6)

        self.assertIn(
            "learning transferable visual models from natural language supervision",
            repeated,
        )

    def test_header_bbox_expands_upward_without_moving_bottom(self):
        from pdf_zh_translator.pdf_layout import _expand_header_bbox_upward

        block = self._make_block(
            "Learning Transferable Visual Models From Natural Language Supervision 46",
            bbox=(55.4, 47.3, 541.4, 57.2),
            page=45,
        )
        block.block_type = "header"
        block.font_size = 9.46

        expanded = _expand_header_bbox_upward(block)

        self.assertLess(expanded.bbox[1], block.bbox[1])
        self.assertEqual(expanded.bbox[3], block.bbox[3])
        self.assertEqual(expanded.redact_bboxes, [block.bbox])

    def test_caption_detection(self):
        """Figure/Table captions are classified correctly."""
        from pdf_zh_translator.pdf_layout import classify_blocks

        block = self._make_block("Figure 1: Overview of the system architecture.")
        classify_blocks([block], 0, 792, [])
        self.assertEqual(block.block_type, "caption")
        self.assertTrue(block.preserve_position)
        self.assertTrue(block.should_translate)

    def test_academic_structure_headings_override_dense_vector_image_zone(self):
        blocks = [
            self._make_block("Definition 4.1.", bold=True),
            self._make_block("Step 1: Guided Sampling.", bold=True),
            self._make_block("Step 2: Manifold Projection.", bold=True),
            self._make_block("Proposition 4.3.", bold=True),
        ]

        classify_blocks(blocks, 0, 792, [(20.0, 0.0, 612.0, 487.0)])

        self.assertTrue(all(block.block_type == "heading" for block in blocks))
        self.assertTrue(all(block.should_translate for block in blocks))
        self.assertTrue(all(block.preserve_position for block in blocks))

    def test_small_figure_academic_label_remains_preserved(self):
        block = self._make_block("Definition 4.1.", bold=True)
        block.font_size = 7.5

        classify_blocks([block], 0, 792, [(20.0, 0.0, 612.0, 487.0)])

        self.assertEqual(block.block_type, "figure_label")
        self.assertFalse(block.should_translate)

    def test_parallel_figure_task_labels_remain_preserved(self):
        labels = (
            (75.0, 143.5, 115.0, 155.7),
            (155.0, 143.5, 198.0, 155.7),
            (240.0, 143.5, 278.0, 155.7),
            (316.0, 143.5, 367.0, 155.7),
            (398.0, 143.5, 447.0, 155.7),
        )
        block = TextBlock(
            page_index=0,
            bbox=(75.0, 143.5, 447.0, 155.7),
            text="Close Jar Drag Stick Insert Peg Meat off Grill Open Drawer",
            font_size=9.04,
            color=(0.0, 0.0, 0.0),
            source_lines=5,
            source_line_bboxes=labels,
        )
        block.redact_bboxes = list(labels)

        classify_blocks([block], 0, 792, [(20.0, 0.0, 612.0, 410.0)])

        self.assertEqual(block.block_type, "figure_label")
        self.assertFalse(block.should_translate)

    def test_author_metadata_is_not_translated(self):
        from pdf_zh_translator.pdf_layout import classify_blocks

        block = self._make_block(
            "Author Names Omitted for Anonymous Review. Paper-ID 74",
            bbox=(49.0, 133.0, 563.0, 146.0),
        )

        classify_blocks([block], 0, 792, [])

        self.assertEqual(block.block_type, "metadata")
        self.assertFalse(block.should_translate)

    def test_tall_industry_author_wall_is_metadata(self):
        """Large-team author walls (30+ names over 5+ rows) exceed the old
        90pt height cap; they are still bylines, not prose (DreamZero p1)."""
        from pdf_zh_translator.pdf_layout import classify_blocks

        block = self._make_block(
            "Kaiyuan Zheng^{*} Shenyuan Gao^{*} Sihyun Yu^{*} George Kurian^{*} "
            "Suneel Indupuru^{*} You Liang Tan^{*} Chuning Zhu Jiannan Xiang "
            "Ayaan Malik Kyungmin Lee William Liang Nadun Ranawaka Jiasheng Gu "
            "Yinzhen Xu Guanzhi Wang Fengyuan Hu Avnish Narayan Johan Bjorck "
            "Jing Wang Gwanghyun Kim Dantong Niu Ruijie Zheng Yuqi Xie Jimmy Wu "
            "Qi Wang Ryan Julian Danfei Xu Yilun Du Yevgen Chebotar Scott Reed "
            "Jan Kautz Yuke Zhu^{†} Linxi \u201cJim\u201d Fan^{†} Joel Jang^{†} NVIDIA "
            "^{†}Project Leads ^{*}Core Contributors https://dreamzero0.github.io",
            bbox=(62.0, 124.0, 502.0, 246.0),
        )

        classify_blocks([block], 0, 792, [])

        self.assertEqual(block.block_type, "metadata")
        self.assertFalse(block.should_translate)

    def test_tall_first_page_abstract_is_still_prose(self):
        from pdf_zh_translator.pdf_layout import classify_blocks

        block = self._make_block(
            "World action models learn transferable physical priors from "
            "large-scale video. We show that jointly predicting video and "
            "action enables zero-shot generalization to unseen tasks in "
            "unseen environments, and that few-shot adaptation to new "
            "embodiments emerges from cross-embodiment pretraining. Our "
            "results demonstrate strong improvements over prior methods "
            "across four benchmark suites and two real robots.",
            bbox=(62.0, 124.0, 502.0, 240.0),
        )

        classify_blocks([block], 0, 792, [])

        self.assertEqual(block.block_type, "body")
        self.assertTrue(block.should_translate)

    def test_fragmented_gan_byline_is_metadata_not_run_in_prose(self):
        blocks = [
            self._make_block(
                "Ian J. Goodfellow, Jean Pouget-Abadie",
                bbox=(120.9, 167.8, 290.8, 177.8),
            ),
            self._make_block(
                "Mehdi Mirza, Bing Xu, David Warde-Farley",
                bbox=(290.8, 166.3, 493.6, 177.8),
            ),
            self._make_block(
                "Sherjil Ozair",
                bbox=(204.7, 179.6, 260.3, 189.6),
            ),
            self._make_block(
                "Aaron Courville, Yoshua Bengio Department of Computer Science, "
                "University of Montreal",
                bbox=(188.0, 190.8, 424.0, 222.5),
            ),
        ]
        blocks[2].block_type = "run_in_heading"
        blocks[2].bold = True

        classify_blocks(blocks, 0, 792, [])

        self.assertTrue(all(block.block_type == "metadata" for block in blocks))
        self.assertTrue(all(not block.should_translate for block in blocks))

    def test_fragmented_author_contact_bands_are_metadata(self):
        for blocks in (
            [
                self._make_block(
                    "Kaiming He Xiangyu Zhang Shaoqing Ren Jian Sun",
                    bbox=(136.4, 152.4, 458.8, 177.0),
                ),
                self._make_block(
                    "Microsoft Research {kahe,v-xiangz,v-shren,jiansun}@microsoft.com",
                    bbox=(136.4, 178.0, 458.8, 202.1),
                ),
            ],
            [
                self._make_block(
                    "Volodymyr Mnih Koray Kavukcuoglu David Silver Alex Graves",
                    bbox=(123.6, 171.0, 524.9, 181.0),
                ),
                self._make_block(
                    "Daan Wierstra Martin Riedmiller",
                    bbox=(245.5, 192.9, 400.5, 202.9),
                ),
                self._make_block(
                    "DeepMind Technologies",
                    bbox=(274.0, 215.0, 372.1, 224.9),
                ),
                self._make_block(
                    "{vlad,koray,david,daan}@deepmind.com",
                    bbox=(119.4, 237.2, 526.7, 246.3),
                ),
            ],
        ):
            classify_blocks(blocks, 0, 792, [])
            self.assertTrue(all(block.block_type == "metadata" for block in blocks))
            self.assertTrue(all(not block.should_translate for block in blocks))

    def test_title_case_prose_band_is_not_fragmented_author_metadata(self):
        from pdf_zh_translator.pdf_layout import classify_blocks

        blocks = [
            self._make_block(
                "Capacitated Constraints on Nodes",
                bbox=(108.0, 165.5, 254.0, 175.5),
            ),
            self._make_block(
                "Initially, we consider capacitated constraints on nodes, "
                "which impose bounds on the optimization problem.",
                bbox=(108.0, 176.6, 504.0, 237.2),
            ),
            self._make_block(
                "Capacitated Constraints on Edges",
                bbox=(108.0, 244.1, 254.1, 254.1),
            ),
        ]
        blocks[0].block_type = "run_in_heading"
        blocks[0].bold = True
        blocks[2].block_type = "run_in_heading"
        blocks[2].bold = True

        classify_blocks(blocks, 0, 792, [])

        self.assertEqual(blocks[0].block_type, "run_in_heading")
        self.assertEqual(blocks[1].block_type, "body")
        self.assertEqual(blocks[2].block_type, "run_in_heading")
        self.assertTrue(all(block.should_translate for block in blocks))

    def test_first_page_two_column_prose_is_not_fragmented_author_metadata(self):
        """An affiliation in the byline must not turn all nearby page-one
        records into metadata (BERT p1 regression)."""
        from pdf_zh_translator.pdf_layout import classify_blocks

        blocks = [
            self._make_block(
                "Jacob Devlin Ming-Wei Chang Kenton Lee Kristina Toutanova "
                "Google AI Language {jacobdevlin,mwchang,kentonl,kristout}@google.com",
                bbox=(107.8, 130.8, 492.7, 170.4),
            ),
            self._make_block("Abstract", bbox=(158.9, 224.3, 203.4, 236.3)),
            self._make_block(
                "We introduce a new language representation model called BERT, "
                "which stands for Bidirectional Encoder Representations from Transformers. "
                "The model obtains new state-of-the-art results on eleven tasks.",
                bbox=(89.0, 245.8, 273.3, 546.8),
            ),
            self._make_block(
                "There are two existing strategies for applying pre-trained language "
                "representations to downstream tasks. The feature-based approach uses "
                "task-specific architectures that include the representations as features.",
                bbox=(307.3, 225.2, 525.5, 765.6),
            ),
        ]

        classify_blocks(blocks, 0, 792, [])

        self.assertEqual(blocks[0].block_type, "metadata")
        self.assertFalse(blocks[0].should_translate)
        self.assertTrue(all(block.should_translate for block in blocks[1:]))
        self.assertTrue(all(block.block_type != "metadata" for block in blocks[1:]))

    def test_editor_name_fragments_do_not_end_bibliography(self):
        """Reference entries wrap onto editor-name fragments like
        'H. Wallach,' which match the '<letter>. <title>' appendix-heading
        shape; they must neither end the bibliography nor be translated."""
        from pdf_zh_translator import pdf_layout as layout
        from pdf_zh_translator.pdf_layout import classify_blocks

        layout._bibliography_seen.clear()
        layout._bibliography_ended = False
        layout._bibliography_heading_size = 0.0

        heading = self._make_block("References", bold=True, page=9)
        entry = self._make_block(
            "Emilien Dupont, Arnaud Doucet, and Yee Whye Teh. Augmented "
            "neural odes. In",
            bbox=(108, 678, 504, 688),
            page=9,
        )
        editor_fragment = self._make_block(
            "H. Wallach,", bbox=(118, 689, 169, 699), page=9
        )
        tail = self._make_block(
            "and R. Garnett (eds.), Advances in Neural Information "
            "Processing Systems, volume 32, 2019.",
            bbox=(118, 700, 504, 732),
            page=9,
        )

        try:
            classify_blocks([heading, entry, editor_fragment, tail], 9, 792, [])
        finally:
            layout._bibliography_seen.clear()
            layout._bibliography_ended = False
            layout._bibliography_heading_size = 0.0

        self.assertEqual(entry.block_type, "bibliography")
        self.assertEqual(editor_fragment.block_type, "bibliography")
        self.assertEqual(tail.block_type, "bibliography")

    def test_appendix_heading_rejects_name_fragments(self):
        from pdf_zh_translator.pdf_layout import _looks_like_appendix_heading

        self.assertFalse(_looks_like_appendix_heading("H. Wallach,"))
        self.assertFalse(_looks_like_appendix_heading("A. Beygelzimer,"))
        self.assertTrue(_looks_like_appendix_heading("A Proofs"))
        self.assertTrue(_looks_like_appendix_heading("B Additional Experiments"))

    def test_paper_checklist_ends_bibliography(self):
        """The NeurIPS checklist follows References; its heading must end the
        bibliography range so checklist prose gets translated."""
        from pdf_zh_translator import pdf_layout as layout
        from pdf_zh_translator.pdf_layout import classify_blocks

        layout._bibliography_seen.clear()
        layout._bibliography_ended = False
        layout._bibliography_heading_size = 0.0

        heading = self._make_block("References", bold=True, page=12)
        entry = self._make_block(
            "E. Altman. Constrained Markov Decision Processes. Chapman and Hall/CRC, 1999.",
            page=12,
        )
        checklist = self._make_block(
            "NeurIPS Paper Checklist", bold=True, bbox=(108, 80, 300, 100), page=20
        )
        item = self._make_block("1. Claims", bbox=(120, 110, 200, 122), page=20)
        question = self._make_block(
            "Question: Does the paper provide open access to the data and code, "
            "with sufficient instructions to faithfully reproduce the results? "
            "Answer: [Yes] Justification: Anonymous code is included.",
            bbox=(130, 130, 500, 180),
            page=20,
        )

        try:
            classify_blocks([heading, entry], 12, 792, [])
            classify_blocks([checklist, item, question], 20, 792, [])
        finally:
            layout._bibliography_seen.clear()
            layout._bibliography_ended = False
            layout._bibliography_heading_size = 0.0

        self.assertEqual(entry.block_type, "bibliography")
        self.assertNotEqual(checklist.block_type, "bibliography")
        self.assertNotEqual(item.block_type, "bibliography")
        self.assertNotEqual(question.block_type, "bibliography")
        self.assertTrue(question.should_translate)

    def test_checklist_question_block_is_never_bibliography(self):
        """Merged checklist blocks contain Question/Answer/Justification
        markers; they are prose regardless of numbering that looks like a
        reference entry."""
        from pdf_zh_translator import pdf_layout as layout
        from pdf_zh_translator.pdf_layout import classify_blocks

        layout._bibliography_seen.clear()
        layout._bibliography_seen[11] = True
        layout._bibliography_ended = False
        layout._bibliography_heading_size = 0.0

        block = self._make_block(
            "5. Open access to data and code Question: Does the paper provide "
            "open access to the data and code? Answer: [Yes] Justification: "
            "Anonymous code is included as supplementary material.",
            bbox=(131, 74, 506, 411),
            page=21,
        )

        try:
            classify_blocks([block], 21, 792, [])
        finally:
            layout._bibliography_seen.clear()
            layout._bibliography_ended = False
            layout._bibliography_heading_size = 0.0

        self.assertNotEqual(block.block_type, "bibliography")
        self.assertTrue(block.should_translate)

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

    def test_numbered_research_questions_are_not_bibliography(self):
        import pdf_zh_translator.pdf_layout as layout

        block = self._make_block(
            (
                "1. How does RT-2 perform on seen tasks and more importantly, "
                "generalize over new objects, backgrounds, and environments? "
                "2. Can we observe and measure any emergent capabilities of RT-2? "
                "3. How does the generalization vary with parameter count and other "
                "design decisions? 4. Can RT-2 exhibit signs of chain-of-thought "
                "reasoning similarly to vision-language models? We evaluate our "
                "approach with about 6,000 trajectories and use PaLI-X "
                "(Chen et al., 2023a) and PaLM-E (Driess et al., 2023)."
            ),
            bbox=(61.9, 82.8, 534.7, 247.0),
            page=6,
        )
        block.source_lines = 11
        layout._bibliography_seen.clear()
        layout._bibliography_ended = False
        layout._bibliography_heading_size = 0.0

        try:
            classify_blocks([block], 6, 792, [])
        finally:
            layout._bibliography_seen.clear()
            layout._bibliography_ended = False
            layout._bibliography_heading_size = 0.0

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

    def test_leading_citation_prose_after_run_in_split_is_not_reference_entry(self):
        # GuidedVLA p5: the run-in heading split leaves the paragraph body
        # starting with "[28] is a ..." — a citation marker flowing into
        # lowercase prose, not a reference entry.
        from pdf_zh_translator.pdf_layout import _looks_like_reference_entry_text

        text = (
            "[28] is a robustness-oriented benchmark built upon LIBERO [59]. "
            "It is designed to evaluate generalist manipulation policies under "
            "distribution shifts. It introduces perturbations along seven "
            "dimensions: camera viewpoint, robot initial state, language "
            "variation, lighting condition, background texture, sensor noise, "
            "and object layout to expose failure modes under generalization "
            "scenario beyond in-domain evaluation. We compare with "
            "state-of-the-art baselines in Table I."
        )

        self.assertFalse(_looks_like_reference_entry_text(text))

    def test_leading_citation_prose_block_is_body_not_bibliography(self):
        import pdf_zh_translator.pdf_layout as layout
        from pdf_zh_translator.pdf_layout import classify_blocks

        block = self._make_block(
            (
                "[28] is a robustness-oriented benchmark built upon LIBERO [59]. "
                "It is designed to evaluate generalist manipulation policies "
                "under distribution shifts. It introduces perturbations along "
                "seven dimensions: camera viewpoint, robot initial state, "
                "language variation, lighting condition, background texture, "
                "sensor noise, and object layout to expose failure modes under "
                "generalization scenario beyond in-domain evaluation. We compare "
                "with state-of-the-art baselines in Table I."
            ),
            bbox=(312.0, 380.5, 563.0, 546.9),
            page=4,
        )
        layout._bibliography_seen.clear()
        layout._bibliography_ended = False
        layout._bibliography_heading_size = 0.0

        try:
            classify_blocks([block], 4, 792, [])
        finally:
            layout._bibliography_seen.clear()
            layout._bibliography_ended = False
            layout._bibliography_heading_size = 0.0

        self.assertEqual(block.block_type, "body")
        self.assertTrue(block.should_translate)

    def test_merged_numbered_reference_entries_stay_bibliography(self):
        from pdf_zh_translator.pdf_layout import _looks_like_reference_entry_text

        text = (
            "[1] J. Smith, A. Jones, and B. Brown. Deep residual learning for "
            "image recognition. CVPR, 2016. [2] K. He, X. Zhang, S. Ren, and "
            "J. Sun. Identity mappings in deep residual networks. ECCV, 2016. "
            "[3] A. Vaswani, N. Shazeer, N. Parmar, J. Uszkoreit, L. Jones, "
            "A. N. Gomez, L. Kaiser, and I. Polosukhin. Attention is all you "
            "need. NeurIPS, 2017. [4] T. Brown, B. Mann, N. Ryder, and "
            "M. Subbiah. Language models are few-shot learners. NeurIPS, 2020."
        )

        self.assertTrue(_looks_like_reference_entry_text(text))

    def test_lowercase_particle_author_reference_stays_bibliography(self):
        from pdf_zh_translator.pdf_layout import _looks_like_reference_entry_text

        text = (
            "[28] van der Maaten, L. and Hinton, G. Visualizing data using "
            "t-SNE. Journal of Machine Learning Research, 2008."
        )

        self.assertTrue(_looks_like_reference_entry_text(text))

    def test_reference_author_line_detection(self):
        from pdf_zh_translator.pdf_layout import _looks_like_reference_author_line

        # OTF p12: author lead split off a wrapped reference entry.
        self.assertTrue(
            _looks_like_reference_author_line("R. Mohammad Ebrahim and J. Razmi.")
        )
        self.assertTrue(
            _looks_like_reference_author_line(
                "H. Wallach, S. Larochelle, and K. Grauman."
            )
        )
        self.assertTrue(
            _looks_like_reference_author_line(
                "J. Snoek, H. Larochelle, and R.P. Adams."
            )
        )
        # Appendix headings keep terminating the references range.
        self.assertFalse(
            _looks_like_reference_author_line("A. Additional Experiments")
        )
        self.assertFalse(
            _looks_like_reference_author_line("A. Proof of Theorem 1.")
        )
        self.assertFalse(
            _looks_like_reference_author_line("B. Convergence Analysis.")
        )

    def test_references_and_notes_heading_starts_bibliography(self):
        import pdf_zh_translator.pdf_layout as layout
        from pdf_zh_translator.pdf_layout import classify_blocks

        heading = self._make_block(
            "References and Notes",
            bbox=(78.0, 108.0, 235.0, 125.0),
            bold=True,
            page=6,
        )
        entry = self._make_block(
            "8. N. Jaitly, P. Nguyen, A. Senior, V. Vanhoucke, An Application "
            "of Pretrained Deep Neural Networks, Tech. Rep. 001 (2012).",
            bbox=(84.0, 330.0, 531.0, 371.0),
            page=6,
        )

        layout._bibliography_seen.clear()
        layout._bibliography_ended = False
        layout._bibliography_heading_size = 0.0
        try:
            classify_blocks([heading, entry], 6, 792, [])
        finally:
            layout._bibliography_seen.clear()
            layout._bibliography_ended = False
            layout._bibliography_heading_size = 0.0

        self.assertEqual(heading.block_type, "heading")
        self.assertTrue(heading.should_translate)
        self.assertEqual(entry.block_type, "bibliography")
        self.assertFalse(entry.should_translate)

    def test_compact_multi_initial_author_does_not_end_bibliography(self):
        import pdf_zh_translator.pdf_layout as layout
        from pdf_zh_translator.pdf_layout import classify_blocks

        references = self._make_block(
            "References", bbox=(108.0, 80.0, 200.0, 95.0), bold=True, page=10
        )
        entry = self._make_block(
            "Karen Simonyan and Andrew Zisserman. Very deep convolutional "
            "networks. arXiv:1409.1556, 2014.",
            bbox=(108.0, 100.0, 507.0, 130.0),
            page=10,
        )
        author_lead = self._make_block(
            "J. Snoek, H. Larochelle, and R.P. Adams.",
            bbox=(108.0, 579.0, 282.0, 590.0),
            page=11,
        )
        tail = self._make_block(
            "Practical bayesian optimization of machine learning algorithms. "
            "In NIPS, 2012.",
            bbox=(118.0, 579.0, 504.0, 601.0),
            page=11,
        )

        layout._bibliography_seen.clear()
        layout._bibliography_ended = False
        layout._bibliography_heading_size = 0.0
        try:
            classify_blocks([references, entry], 10, 792, [])
            classify_blocks([author_lead, tail], 11, 792, [])
        finally:
            layout._bibliography_seen.clear()
            layout._bibliography_ended = False
            layout._bibliography_heading_size = 0.0

        self.assertEqual(author_lead.block_type, "bibliography")
        self.assertFalse(author_lead.should_translate)
        self.assertEqual(tail.block_type, "bibliography")
        self.assertFalse(tail.should_translate)

    def test_author_lead_inside_references_does_not_end_bibliography(self):
        # OTF p12: "R. Mohammad Ebrahim and J. Razmi." looks like a lettered
        # appendix heading; ending the references range there translated the
        # rest of the page and overprinted the entry with a bold heading.
        import pdf_zh_translator.pdf_layout as layout
        from pdf_zh_translator.pdf_layout import classify_blocks

        references = self._make_block(
            "REFERENCES", bbox=(108.0, 80.0, 200.0, 95.0), bold=True, page=10
        )
        entry = self._make_block(
            "Yang Li, Yichuan Mo, Liangliang Shi, and Junchi Yan. Improving "
            "generative adversarial networks via optimal transport. ICLR, 2024.",
            bbox=(108.0, 100.0, 507.0, 130.0),
            page=10,
        )
        author_lead = self._make_block(
            "R. Mohammad Ebrahim and J. Razmi.",
            bbox=(108.0, 468.6, 275.8, 478.7),
            page=11,
        )
        tail = self._make_block(
            "A hybrid meta heuristic algorithm for bi-objective minimum cost "
            "flow (bmcf) problem. Advances in Engineering Software, "
            "40(10):1056-1062, 2009. ISSN 0965-9978.",
            bbox=(108.0, 468.6, 507.0, 511.5),
            page=11,
        )
        following = self._make_block(
            "Gaspard Monge. Memoire sur la theorie des deblais et des remblais. "
            "Mem. Math. Phys. Acad. Royale Sci., 1781.",
            bbox=(108.0, 515.0, 507.0, 540.0),
            page=11,
        )
        layout._bibliography_seen.clear()
        layout._bibliography_ended = False
        layout._bibliography_heading_size = 0.0

        try:
            classify_blocks([references, entry], 10, 792, [])
            classify_blocks([author_lead, tail, following], 11, 792, [])
        finally:
            layout._bibliography_seen.clear()
            layout._bibliography_ended = False
            layout._bibliography_heading_size = 0.0

        self.assertEqual(entry.block_type, "bibliography")
        self.assertEqual(author_lead.block_type, "bibliography")
        self.assertEqual(tail.block_type, "bibliography")
        self.assertEqual(following.block_type, "bibliography")
        self.assertFalse(following.should_translate)

    def test_small_wrapped_reference_fragments_do_not_end_bibliography(self):
        """MobileNet p8: PDF extraction splits a reference across blocks.

        A small author tail such as ``Y. Chen.`` shares the bibliography font
        size but resembles an appendix heading.  It and the following column
        must remain protected instead of reopening the translation stream.
        """
        import pdf_zh_translator.pdf_layout as layout
        from pdf_zh_translator.pdf_layout import classify_blocks

        references = self._make_block(
            "References", bbox=(50.0, 411.0, 106.0, 424.0), bold=True, page=7
        )
        entry = self._make_block(
            "[1] M. Abadi et al. Tensorflow. OSDI, 2016.",
            bbox=(55.0, 432.0, 286.0, 498.0),
            page=7,
        )
        author_tail = self._make_block(
            "Y. Chen.", bbox=(70.0, 500.0, 103.0, 509.0), page=7
        )
        author_tail.font_size = 9.0
        left_tail = self._make_block(
            "Compressing neural networks with the hashing trick. CoRR, 2015.",
            bbox=(55.0, 500.0, 286.0, 530.0),
            page=7,
        )
        right_column = self._make_block(
            "[8] K. He, X. Zhang, S. Ren, and J. Sun. Deep residual learning. 2015.",
            bbox=(309.0, 75.0, 545.0, 120.0),
            page=7,
        )
        wrapped_author_heading = self._make_block(
            "T. Duerig, J. Philbin, and L. Fei-Fei. The unreasonable ef-",
            bbox=(329.0, 484.0, 545.0, 493.0),
            page=7,
        )
        wrapped_author_heading.block_type = "run_in_heading"

        layout._bibliography_seen.clear()
        layout._bibliography_ended = False
        layout._bibliography_heading_size = 0.0
        try:
            classify_blocks(
                [
                    references,
                    entry,
                    author_tail,
                    left_tail,
                    right_column,
                    wrapped_author_heading,
                ],
                7,
                792,
                [],
            )
        finally:
            layout._bibliography_seen.clear()
            layout._bibliography_ended = False
            layout._bibliography_heading_size = 0.0

        self.assertEqual(author_tail.block_type, "bibliography")
        self.assertEqual(left_tail.block_type, "bibliography")
        self.assertEqual(right_column.block_type, "bibliography")
        self.assertEqual(wrapped_author_heading.block_type, "bibliography")
        self.assertFalse(right_column.should_translate)

    def test_figure_caption_after_references_stays_translatable(self):
        """DiT p12: a figure caption can precede a later appendix heading on
        the final references page and must not inherit bibliography state."""
        import pdf_zh_translator.pdf_layout as layout
        from pdf_zh_translator.pdf_layout import classify_blocks

        references = self._make_block(
            "References", bbox=(50.0, 80.0, 106.0, 93.0), bold=True, page=11
        )
        entry = self._make_block(
            "[1] J. Smith et al. Diffusion transformers. NeurIPS, 2023.",
            bbox=(50.0, 100.0, 545.0, 140.0),
            page=11,
        )
        caption = self._make_block(
            "Figure 11. Additional selected samples from our DiT-XL/2 models.",
            bbox=(50.0, 422.0, 545.0, 443.0),
            page=11,
        )

        layout._bibliography_seen.clear()
        layout._bibliography_ended = False
        layout._bibliography_heading_size = 0.0
        try:
            classify_blocks([references, entry, caption], 11, 792, [])
        finally:
            layout._bibliography_seen.clear()
            layout._bibliography_ended = False
            layout._bibliography_heading_size = 0.0

        self.assertEqual(caption.block_type, "caption")
        self.assertTrue(caption.should_translate)

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

    def test_preserved_region_text_changed_detects_clipped_action_call(self):
        original = "pick(hook) → pull(cube, hook) → place(hook) → pick(cube)"
        clipped = "ick(hook) → pull(cube, hook) → place(hook) → pick(cube)"

        self.assertTrue(preserved_region_text_changed(original, clipped))
        self.assertFalse(preserved_region_text_changed(original, original))

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

    def test_pipe_separated_table_caption_detection(self):
        from pdf_zh_translator.pdf_layout import classify_blocks

        block = self._make_block(
            f"Table 6{SENTINEL_OPEN}|{SENTINEL_CLOSE} Tokenizer batch size "
            "scaling hyperparameters."
        )
        classify_blocks([block], 0, 792, [])

        self.assertEqual(block.block_type, "caption")
        self.assertTrue(block.should_translate)
        self.assertTrue(block.preserve_position)

    def test_split_figure_reference_without_punctuation_stays_body(self):
        previous = self._make_block(
            "Figure 8 provides a quantitative measure of the compounding "
            "error demonstrated qualitatively in",
            bbox=(108.0, 97.4, 504.0, 107.5),
            page=23,
        )
        continuation = self._make_block(
            "Figure 3 for DDPM and EDM based world models.",
            bbox=(108.0, 108.4, 312.9, 118.4),
            page=23,
        )

        classify_blocks([previous, continuation], 23, 792, [])

        self.assertEqual(continuation.block_type, "body")
        self.assertTrue(continuation.should_translate)
        self.assertFalse(continuation.preserve_position)

    def test_parameter_field_row_is_table_header_not_prose(self):
        from pdf_zh_translator.pdf_layout import _looks_like_table_header_text

        self.assertTrue(
            _looks_like_table_header_text(
                "Parameters num_layers num_heads d_model k/q size"
            )
        )
        self.assertFalse(
            _looks_like_table_header_text(
                "The parameters are optimized carefully for every experiment."
            )
        )

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

    def test_single_column_prefers_body_margin_over_long_indented_list(self):
        """A long bullet list may contain more text than surrounding prose,
        but its indent is not a new column boundary."""
        from pdf_zh_translator.pdf_layout import detect_columns

        blocks = [
            TextBlock(0, (108, 100, 504, 125), "heading", 10.0, (0, 0, 0)),
            TextBlock(0, (108, 130, 504, 180), "body " * 80, 10.0, (0, 0, 0)),
            TextBlock(0, (134, 190, 504, 500), "• list item " * 120, 10.0, (0, 0, 0)),
        ]

        self.assertEqual(detect_columns(blocks), [(108.0, 396.0)])

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
    def test_citation_rich_body_is_not_a_reference_entry(self):
        from pdf_zh_translator.pdf_layout import _looks_like_reference_entry_text

        body = (
            "We take inspiration from Li et al. (2018) and Aghajanyan et al. "
            "(2020), which show that learned models occupy a low intrinsic "
            "dimension. We therefore propose a low-rank adaptation method."
        )

        self.assertFalse(_looks_like_reference_entry_text(body))

        discourse_body = (
            "Since Bengio et al. (2003) introduced a neural language model, "
            "neural networks have been widely used in machine translation. "
            "However, their role was initially limited to reranking."
        )
        self.assertFalse(_looks_like_reference_entry_text(discourse_body))

    def test_leading_citation_with_punctuation_remains_body_prose(self):
        from pdf_zh_translator.pdf_layout import _looks_like_reference_entry_text

        body = (
            "[2], and pruning, vector quantization, and Huffman coding have "
            "been proposed in the literature. Another method trains compact "
            "networks by distilling a larger model."
        )

        self.assertFalse(_looks_like_reference_entry_text(body))

    def test_reference_continuation_tolerates_figure_blocks_above_entries(self):
        from pdf_zh_translator.pdf_layout import _page_looks_like_reference_continuation

        document = fitz.open()
        page = document.new_page(width=612, height=792)
        for index, label in enumerate(
            ("person", "car", "bus", "bicycle", "truck", "chair")
        ):
            page.insert_text((72 + index * 48, 100), f"{label}: 0.9{index}")
        page.insert_textbox(
            fitz.Rect(36, 620, 275, 760),
            (
                "networks for recognition, in ICLR, 2015. "
                "[4] J. Smith and A. Doe. Selective search. IJCV, 2013. "
                "[5] P. Brown et al. Region proposals. CVPR, 2014."
            ),
            fontsize=8,
        )
        page.insert_textbox(
            fitz.Rect(287, 620, 526, 760),
            (
                "[7] J. Long et al. Fully convolutional networks. CVPR, 2015. "
                "[8] R. Girshick et al. Fast detection. ICCV, 2015."
            ),
            fontsize=8,
        )

        self.assertTrue(_page_looks_like_reference_continuation(page))
        document.close()

    def test_verification_reports_placeholder_leak(self):
        from pdf_zh_translator.pdf_layout import _placeholder_leak_issues

        issues = _placeholder_leak_issues(
            4,
            "The method updates the cached state.",
            "该方法更新缓存状态 ⟦3⟧。",
        )

        self.assertEqual([issue.code for issue in issues], ["placeholder_leak"])
        self.assertEqual(issues[0].page, 4)

    def test_untranslated_issue_families_are_deduplicated_per_page(self):
        prose = (
            "We take inspiration from Li et al. (2018), which shows that the "
            "learned model occupies a low intrinsic dimension. We therefore "
            "propose a compact low-rank adaptation method for every layer."
        )
        original = fitz.open()
        page = original.new_page(width=420, height=320)
        page.insert_textbox(fitz.Rect(40, 70, 380, 180), prose, fontsize=10)
        translated = fitz.open()
        translated.insert_pdf(original)

        import tempfile

        with tempfile.TemporaryDirectory() as tmpdir:
            original_path = Path(tmpdir) / "orig.pdf"
            translated_path = Path(tmpdir) / "zh.pdf"
            original.save(original_path)
            translated.save(translated_path)
            issues = verify_translation_issues(original_path, translated_path)

        original.close()
        translated.close()
        untranslated = [
            issue
            for issue in issues
            if issue.code
            in {
                "untranslated_block",
                "untranslated_english",
                "untranslated_natural_language",
            }
        ]
        self.assertEqual(len(untranslated), 1, untranslated)

    def test_verification_ignores_labeled_generated_poem_samples(self):
        original = fitz.open()
        page = original.new_page(width=420, height=520)
        page.insert_text((50, 70), "-------- Generated Poem 1 --------")
        poem = (
            "The sun was all we had. All is changed. White fields remain. "
            "Ancient gleams surround the roots. The great dark books of reverie "
            "follow the labyrinth of the sea."
        )
        page.insert_textbox(fitz.Rect(50, 85, 240, 300), poem, fontsize=9)
        translated = fitz.open()
        translated.insert_pdf(original)

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
            any(
                issue.code
                in {
                    "untranslated_block",
                    "untranslated_english",
                    "untranslated_natural_language",
                }
                for issue in issues
            ),
            issues,
        )

    def test_overlap_detector_exempts_formula_code_and_table_cells(self):
        self.assertTrue(_looks_like_overlap_exempt_text("minT ∈Π(µs,µt)⟨L(Cs(X(m)"))
        self.assertTrue(
            _looks_like_overlap_exempt_text(
                "def policy(graph): mem = graph.task_memory.setdefault('swap_cups', {})"
            )
        )
        self.assertTrue(_looks_like_overlap_exempt_text("1,50010−5"))
        self.assertTrue(_looks_like_overlap_exempt_text('! "# $'))
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
                "（Recurrent Neural Networks）[Laurent et al., 2015, Amodei et al."
            )
        )
        self.assertFalse(
            _looks_like_untranslated_english(
                "（Weight Normalization） [Salimans and Kingma, 2016]"
            )
        )
        self.assertFalse(
            _looks_like_untranslated_english(
                "SGD（Path-normalized SGD）[Neyshabur et"
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

    def test_untranslated_detector_ignores_preserved_prompt_code_in_chinese(self):
        self.assertFalse(
            _looks_like_untranslated_english(
                "目标谓词集：[['under(yellow box, rack)', 'under(blue box, rack)']] "
                "最优1个机器人动作序列：['push(yellow box, hook, rack)', "
                "'push(red box, hook, rack)']"
            )
        )

    def test_untranslated_detector_ignores_detached_prompt_code_list_in_chinese(self):
        self.assertFalse(
            _looks_like_untranslated_english(
                "pick(blue box)’,’place(blue box, rack)’,’pick(hook)’,"
                "’push(cyan box, hook, rack)’,’place(hook, table)’]"
            )
        )
        self.assertFalse(
            _looks_like_untranslated_english(
                "box, rack)’,’under(cyan box, rack)’,’under(red box, rack)’]] "
                "这是一段用于验证版面稳定性[’pick(bluebox)’,’place(blue box, table)’,"
                "’pick(hook)’,’push(cyan box, hook, rack)’,’place(hook, table)’,"
                "’pick(blue"
            )
        )

    def test_untranslated_detector_still_flags_english_prompt_prose(self):
        self.assertTrue(
            _looks_like_untranslated_english(
                "目标谓词集：[['under(yellow box, rack)']] Human instruction: "
                "Move both colored boxes beneath the rack before returning the hook."
            )
        )

    def test_untranslated_detector_allows_translated_metric_fragment(self):
        self.assertFalse(
            _looks_like_untranslated_english(
                "（1.36× wall-clock, 20.1% conflict reduction）和Glucose 4.2"
                "（1.10×, 6.0%）中的"
            )
        )

    def test_untranslated_detector_allows_split_parenthetical_gloss(self):
        self.assertFalse(
            _looks_like_untranslated_english(
                "Diffusion Probabilistic Model）（Sohl-Dickstein 等人，"
            )
        )

    def test_untranslated_detector_still_flags_unparenthesized_english(self):
        self.assertTrue(
            _looks_like_untranslated_english(
                "Diffusion Probabilistic Model improves generation quality "
                "across diverse tasks and environments."
            )
        )

    def test_untranslated_detector_flags_english_metric_sentence(self):
        self.assertTrue(
            _looks_like_untranslated_english(
                "Accuracy improves by 20.1% and conflict reduction reaches 6.0% "
                "across all benchmark tasks."
            )
        )

    def test_formula_explanation_detector_flags_distribution_prose(self):
        self.assertTrue(
            _looks_like_untranslated_formula_explanation(
                "to represent the distributions pθ(τ1|qs, τ2) and "
                "pθ(τK|τK−1, qg). This corresponds to the training objective"
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

    def test_retained_bracketed_natural_language_excludes_citations_and_math(self):
        text = (
            "译文保留了 [a medical center]、"
            "[based on his status as a health care worker] 和 "
            "[en fonction de son etat]，但 [1, 2] 与 [x, y] 应保留。"
        )

        self.assertEqual(
            _retained_bracketed_natural_language(text),
            [
                "a medical center",
                "based on his status as a health care worker",
                "en fonction de son etat",
            ],
        )

    def test_retained_bracketed_natural_language_includes_single_word_examples(self):
        text = (
            "将 [the] 与 [man] 对齐到 [l']、[le]、[la] 和 [homme]，"
            "保留 [UNK]、[1] 与 [x,y]。"
        )

        self.assertEqual(
            _retained_bracketed_natural_language(text),
            ["the", "man", "l'", "le", "la", "homme"],
        )

    def test_bracket_retry_scan_excludes_preserved_formula_sentinels(self):
        text = "译文\ue000E_q[delta(z) epsilon]\ue001保留 [the man] 示例。"

        self.assertEqual(
            _retained_bracketed_natural_language(_text_outside_sentinels(text)),
            ["the man"],
        )

    def test_retry_detector_flags_natural_language_inside_brackets(self):
        block = TextBlock(
            page_index=6,
            bbox=(108.0, 426.3, 504.0, 515.0),
            text="Consider [European Economic Area] and [the man] alignment examples.",
            font_size=9.96,
            color=(0.0, 0.0, 0.0),
        )

        self.assertTrue(
            _translated_block_still_english(
                block,
                "考虑短语 [European Economic Area] 与 [the man] 的对齐示例。",
            )
        )

    def test_focused_foreign_phrase_glosses_elided_french_article(self):
        self.assertEqual(
            _focused_foreign_phrase_translation("l'", "l'"),
            "省音定冠词（l'）",
        )
        self.assertEqual(
            _focused_foreign_phrase_translation("les", "les"),
            "定冠词（les）",
        )
        self.assertEqual(
            _focused_foreign_phrase_translation("the", "the"),
            "定冠词（the）",
        )
        self.assertEqual(
            _focused_foreign_phrase_translation("man", "man"),
            "男人（man）",
        )
        self.assertEqual(
            _focused_foreign_phrase_translation("homme", "男人"),
            "男人",
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

    def test_source_comparison_flags_short_foreign_residue_in_chinese(self):
        block = TextBlock(
            page_index=7,
            bbox=(143.9, 165.2, 468.1, 197.2),
            text=(
                "Un privilege d'admission est le droit d'un medecin pour effectuer "
                "un diagnostic ou une procedure, selon son statut de travailleur."
            ),
            font_size=9.96,
            color=(0.0, 0.0, 0.0),
        )

        self.assertTrue(
            _translation_retains_foreign_prose(
                block,
                "入院特权是医生进行 un 诊断 ou une 程序的权利，根据 son 状态。",
            )
        )

    def test_source_comparison_flags_single_english_pronoun_residue(self):
        block = TextBlock(
            page_index=7,
            bbox=(143.9, 227.9, 468.1, 259.8),
            text=(
                "This experience extends the lifetime of its series through digital "
                "platforms that are becoming more important, he added."
            ),
            font_size=9.96,
            color=(0.0, 0.0, 0.0),
        )

        self.assertTrue(
            _translation_retains_foreign_prose(
                block,
                "这种体验延长了系列作品的寿命，he 补充道。",
            )
        )

    def test_source_comparison_allows_acronyms_names_and_glosses(self):
        block = TextBlock(
            page_index=1,
            bbox=(72.0, 100.0, 500.0, 150.0),
            text="We evaluate RNNsearch and Disney examples with GPU acceleration.",
            font_size=10.0,
            color=(0.0, 0.0, 0.0),
        )

        self.assertFalse(
            _translation_retains_foreign_prose(
                block,
                "我们使用 GPU 加速评估 RNNsearch 与迪士尼（Disney）示例。",
            )
        )

    def test_source_comparison_allows_quoted_single_word_under_analysis(self):
        block = TextBlock(
            page_index=13,
            bbox=(108.0, 614.3, 505.4, 646.1),
            text=(
                "Figure 4: Isolated attentions from just the word 'its' for "
                "attention heads 5 and 6."
            ),
            font_size=9.96,
            color=(0.0, 0.0, 0.0),
            block_type="caption",
        )

        self.assertFalse(
            _translation_retains_foreign_prose(
                block,
                "图4：仅针对单词“its”的头5 和头6 的孤立注意力。",
            )
        )

    def test_source_comparison_allows_formula_variables_and_roman_lists(self):
        block = TextBlock(
            page_index=4,
            bbox=(72.0, 100.0, 500.0, 150.0),
            text=(
                "We compare i) sample quality and ii) coverage, where a is a "
                "coefficient and f is a function; i.e. z has unit variance."
            ),
            font_size=10.0,
            color=(0.0, 0.0, 0.0),
        )

        self.assertFalse(
            _translation_retains_foreign_prose(
                block,
                "我们比较 i) 样本质量和 ii) 覆盖率，其中 a 与 f 为系数，"
                "即（i.e.）z 具有单位方差。",
            )
        )

    def test_source_comparison_still_flags_contiguous_function_word_residue(self):
        block = TextBlock(
            page_index=3,
            bbox=(72.0, 100.0, 500.0, 150.0),
            text=(
                "The coefficient is given in terms of the standard deviation "
                "of the data distribution."
            ),
            font_size=10.0,
            color=(0.0, 0.0, 0.0),
        )

        self.assertTrue(
            _translation_retains_foreign_prose(
                block,
                "该系数由 of the standard deviation of the data distribution 给出。",
            )
        )

    def test_source_comparison_allows_named_baseline_with_repeated_of(self):
        block = TextBlock(
            page_index=8,
            bbox=(72.0, 100.0, 500.0, 160.0),
            text=(
                "We compare DPO with the Best of 128 baseline and report the "
                "performance of every method."
            ),
            font_size=10.0,
            color=(0.0, 0.0, 0.0),
        )

        self.assertFalse(
            _translation_retains_foreign_prose(
                block,
                "我们将 DPO 与 Best of 128 基线比较，并报告每种方法的性能。",
            )
        )

    def test_source_comparison_allows_cjk_labeled_verbatim_samples(self):
        block = TextBlock(
            page_index=29,
            bbox=(72.0, 100.0, 500.0, 160.0),
            text=(
                "Poor English input: We think that Leslie likes ourselves. "
                "Good English output: We think that Leslie likes us."
            ),
            font_size=10.0,
            color=(0.0, 0.0, 0.0),
        )

        self.assertFalse(
            _translation_retains_foreign_prose(
                block,
                "较差的英语输入：We think that Leslie likes ourselves. "
                "较好的英语输出：We think that Leslie likes us.",
            )
        )

    def test_source_comparison_allows_quoted_natural_language_examples(self):
        block = TextBlock(
            page_index=11,
            bbox=(72.0, 100.0, 500.0, 160.0),
            text=(
                "The sentence I am in a mocha mood follows the grammar rules, "
                "and the phrase in a mocha mood supplies context."
            ),
            font_size=10.0,
            color=(0.0, 0.0, 0.0),
        )

        self.assertFalse(
            _translation_retains_foreign_prose(
                block,
                "句子“I am in a mocha mood”符合语法规则，短语“in a mocha mood”"
                "提供了语境。",
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
        preserved_issue = next(
            issue for issue in issues if issue.code == "preserved_text_changed"
        )
        self.assertIn("; example: ", preserved_issue.message)
        self.assertTrue(preserved_issue.message.rsplit("; example: ", 1)[-1].strip())

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

    def test_verification_flags_short_academic_heading_echo(self):
        original = fitz.open()
        page = original.new_page(width=360, height=360)
        page.insert_text(
            (30, 60),
            "Step 1: Guided Sampling.",
            fontsize=10,
            fontname="hebo",
        )

        translated = fitz.open()
        page = translated.new_page(width=360, height=360)
        page.insert_text(
            (30, 60),
            "Step 1: Guided Sampling.",
            fontsize=10,
            fontname="hebo",
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
        self.assertEqual(english_issues[0].page, 1)
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

    def test_apa_reference_page_is_detected_as_continuation(self):
        from pdf_zh_translator.pdf_layout import _page_looks_like_reference_continuation

        document = fitz.open()
        page = document.new_page(width=612, height=792)
        entries = (
            "Unterthiner, T., Van Steenkiste, S., Kurach, K., and Gelly, S. "
            "(2018). Towards accurate generative models of video. arXiv preprint.",
            "Valevski, D., Leviathan, Y., Arar, M., and Fruchter, S. (2024). "
            "Diffusion models are real-time game engines.",
            "Van Den Oord, A., Vinyals, O., et al. (2017). Neural discrete "
            "representation learning. Advances in Neural Information Processing Systems.",
        )
        for index, entry in enumerate(entries):
            page.insert_textbox(
                fitz.Rect(72, 60 + index * 90, 540, 130 + index * 90),
                entry,
                fontsize=10,
            )

        self.assertTrue(_page_looks_like_reference_continuation(page))
        document.close()

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

    def test_formula_preserved_region_does_not_hide_stranded_english_tail(self):
        original = fitz.open()
        page = original.new_page(width=300, height=220)
        page.insert_text((30, 80), "or up to 1,000 integration steps.")

        translated = fitz.open()
        translated.insert_pdf(original)

        import tempfile

        def preserve_formula_region(_document, *_args, **kwargs):
            regions = kwargs.get("preserved_regions_out")
            if regions is not None:
                regions[0] = [(25.0, 65.0, 230.0, 90.0)]
            return [], {}, 1

        with tempfile.TemporaryDirectory() as tmpdir:
            original_path = Path(tmpdir) / "orig.pdf"
            translated_path = Path(tmpdir) / "zh.pdf"
            original.save(original_path)
            translated.save(translated_path)
            with patch(
                "pdf_zh_translator.pdf_layout.prepare_translation_units",
                side_effect=preserve_formula_region,
            ):
                issues = verify_translation_issues(original_path, translated_path)

        original.close()
        translated.close()
        self.assertTrue(
            any(issue.code == "untranslated_formula_explanation" for issue in issues),
            [f"{issue.code}: {issue.message}" for issue in issues],
        )

    def test_stranded_formula_tail_detector_rejects_chinese_and_fragments(self):
        self.assertTrue(
            _looks_like_stranded_formula_prose_tail(
                "or up to 1,000 integration steps."
            )
        )
        self.assertFalse(_looks_like_stranded_formula_prose_tail("or up to 1,000"))
        self.assertFalse(
            _looks_like_stranded_formula_prose_tail("或最多 1,000 个积分步。")
        )

    def test_preserved_algorithm_formula_steps_are_not_untranslated_prose(self):
        original = fitz.open()
        page = original.new_page(width=300, height=260)
        for y in (40, 66, 190):
            page.draw_line((30, y), (270, y), width=0.8)
        page.insert_text((35, 58), "Algorithm 1 Deep Q-learning")
        page.insert_text(
            (45, 90),
            "With probability epsilon select a random action a; otherwise a=max Q(s,a).",
            fontsize=8,
        )

        translated = fitz.open()
        translated.insert_pdf(original)

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
        self.assertFalse(
            any(
                issue.code in {
                    "untranslated_formula_explanation",
                    "untranslated_english",
                }
                for issue in issues
            ),
            [f"{issue.code}: {issue.message}" for issue in issues],
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

    def test_sprite_image_bbox_defers_to_wide_visible_drawing_band(self):
        from pdf_zh_translator.pdf_layout import (
            _image_bbox_clipped_by_wide_drawing_band,
        )

        page_rect = SimpleNamespace(width=612.0, height=792.0)
        image_bbox = (153.6, 387.1, 227.4, 632.9)
        drawing_bands = [(167.4, 387.0, 444.6, 436.2)]

        self.assertTrue(
            _image_bbox_clipped_by_wide_drawing_band(
                image_bbox,
                drawing_bands,
                page_rect,
            )
        )
        self.assertFalse(
            _image_bbox_clipped_by_wide_drawing_band(
                (120.0, 200.0, 500.0, 500.0),
                drawing_bands,
                page_rect,
            )
        )

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

    def test_float_side_column_must_fit_near_requested_body_size(self):
        block = TextBlock(
            page_index=0,
            bbox=(108.0, 139.0, 505.0, 453.0),
            text="Concretely, we compare several fine-tuning approaches.",
            font_size=10.06,
            color=(0.0, 0.0, 0.0),
            source_lines=26,
        )
        requested = requested_translation_font_size(block, 5.0, 0.92)

        self.assertEqual(
            _float_clip_min_font_size(5.0, requested),
            requested * 0.96,
        )
        self.assertGreater(requested * 0.96, 8.8)

    def test_clip_block_bbox_against_memorywam_mid_right_image(self):
        clipped = _clip_block_bbox_against_floats(
            (107.6, 324.4, 505.2, 477.9),
            [(254.6, 314.5, 517.2, 427.5)],
            612.0,
        )

        self.assertEqual(clipped, (107.6, 324.4, 251.6, 477.9))

    def test_clip_block_bbox_merges_adjacent_right_side_figure_panels(self):
        clipped = _clip_block_bbox_against_floats(
            (107.6, 375.1, 504.2, 582.5),
            [
                (256.4, 383.6, 374.3, 482.8),
                (376.8, 383.6, 491.7, 482.8),
            ],
            612.0,
        )

        self.assertEqual(clipped[:2], (107.6, 375.1))
        self.assertAlmostEqual(clipped[2], 253.4)
        self.assertEqual(clipped[3], 582.5)

    def test_clip_block_bbox_does_not_merge_panels_with_stacked_obstacles(self):
        clipped = _clip_block_bbox_against_floats(
            (107.6, 375.1, 504.2, 582.5),
            [
                (256.4, 383.6, 374.3, 482.8),
                (376.8, 383.6, 491.7, 482.8),
                (246.6, 485.8, 504.3, 530.8),
                (107.6, 505.0, 250.0, 535.0),
            ],
            612.0,
        )

        self.assertEqual(clipped[:2], (107.6, 375.1))
        self.assertAlmostEqual(clipped[2], 253.4)
        self.assertEqual(clipped[3], 582.5)

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
    def test_heading_expansion_keeps_original_redaction_bbox(self):
        source_bbox = (155.3, 352.0, 235.1, 362.1)
        label = TextBlock(
            page_index=0,
            bbox=source_bbox,
            text="Action Skeleton:",
            font_size=10.0,
            color=(0.0, 0.0, 0.0),
            block_type="heading",
        )

        expanded = expand_heading_bbox(label)

        self.assertGreater(expanded.bbox[2], source_bbox[2])
        self.assertLess(expanded.bbox[1], source_bbox[1])
        self.assertEqual(expanded.redact_bboxes, [source_bbox])

    def test_short_heading_expands_to_detected_column_width(self):
        source_bbox = (50.1, 180.8, 132.1, 192.8)
        heading = TextBlock(
            page_index=0,
            bbox=source_bbox,
            text="3. Mask R-CNN",
            font_size=11.96,
            color=(0.0, 0.0, 0.0),
            block_type="heading",
            bold=True,
        )

        expanded = _expand_standalone_heading_to_column(
            heading,
            [(50.0, 236.0), (309.0, 236.0)],
            595.0,
        )

        self.assertEqual(expanded.bbox, (50.1, 180.8, 286.0, 192.8))
        self.assertEqual(expanded.redact_bboxes, [source_bbox])
        self.assertEqual(expanded.fixed_translation_font_size, 11.96)

    def test_heading_uses_nearby_full_width_body_when_float_skews_columns(self):
        source_bbox = (108.0, 630.9, 263.3, 640.9)
        heading = TextBlock(
            page_index=0,
            bbox=source_bbox,
            text="4.5 Effectiveness of Design Choices",
            font_size=9.96,
            color=(0.0, 0.0, 0.0),
            block_type="heading",
            bold=True,
        )
        following = TextBlock(
            page_index=0,
            bbox=(107.5, 652.6, 505.7, 722.4),
            text="Following full-width body paragraph.",
            font_size=9.88,
            color=(0.0, 0.0, 0.0),
            block_type="body",
        )

        expanded = _expand_standalone_heading_to_column(
            heading,
            [(108.0, 129.0), (337.0, 168.0)],
            612.0,
            [heading, following],
        )

        self.assertEqual(expanded.bbox, (108.0, 630.9, 505.7, 640.9))
        self.assertEqual(expanded.redact_bboxes, [source_bbox])

    def test_caption_opener_does_not_merge_backward_into_table_tail(self):
        table_tail = TextBlock(
            page_index=0,
            bbox=(108.0, 508.0, 462.0, 529.4),
            text="Large 77 ± 4 Giant 61 ± 5",
            font_size=10.0,
            color=(0.0, 0.0, 0.0),
        )
        caption = TextBlock(
            page_index=0,
            bbox=(108.0, 537.5, 504.0, 547.6),
            text="Table 9: Quantitative Results with Inverse Dynamics Models of Different",
            font_size=10.0,
            color=(0.0, 0.0, 0.0),
            bold=True,
        )
        continuation = TextBlock(
            page_index=0,
            bbox=(108.0, 548.4, 150.0, 558.5),
            text="Horizons.",
            font_size=10.0,
            color=(0.0, 0.0, 0.0),
            block_type="heading",
            bold=True,
            no_merge=True,
        )

        self.assertFalse(can_merge_blocks(table_tail, caption))
        self.assertTrue(can_merge_blocks(caption, continuation))

    def test_table_caption_does_not_merge_forward_into_parallel_header_cells(self):
        caption = TextBlock(
            page_index=0,
            bbox=(107.7, 71.2, 504.0, 92.1),
            text=(
                "Table 2: The Transformer achieves better BLEU scores than "
                "previous state-of-the-art models."
            ),
            font_size=9.96,
            color=(0.0, 0.0, 0.0),
            source_lines=2,
            source_line_bboxes=(
                (107.7, 71.2, 504.0, 81.2),
                (108.0, 82.1, 483.5, 92.1),
            ),
        )
        table_header = TextBlock(
            page_index=0,
            bbox=(136.7, 97.9, 475.3, 116.2),
            text="Model BLEU Training Cost (FLOPs)",
            font_size=9.96,
            color=(0.0, 0.0, 0.0),
            source_lines=3,
            source_line_bboxes=(
                (136.7, 106.2, 162.7, 116.2),
                (311.0, 97.9, 337.0, 107.9),
                (383.3, 97.9, 475.3, 107.9),
            ),
        )

        self.assertFalse(can_merge_blocks(caption, table_header))

    def test_side_adjacent_preserved_code_becomes_redaction_keepout(self):
        from pdf_zh_translator.pdf_layout import _side_adjacent_preserved_regions

        label = TextBlock(
            page_index=0,
            bbox=(155.3, 352.0, 235.1, 362.1),
            text="Action Skeleton:",
            font_size=10.0,
            color=(0.0, 0.0, 0.0),
        )
        code = (235.1, 351.8, 504.0, 362.1)

        self.assertEqual(_side_adjacent_preserved_regions(label, [code]), [code])

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

    def test_small_label_fragment_overlapping_larger_figure_label_is_flagged(self):
        from pdf_zh_translator.pdf_layout import _candidate_bboxes_colliding_with_preserved

        candidate = TextBlock(
            page_index=0,
            bbox=(453.8, 639.0, 480.4, 648.5),
            text="Behaviors",
            font_size=5.7,
            color=(0.0, 0.0, 0.0),
            source_line_bboxes=((453.8, 639.0, 480.4, 648.5),),
        )
        figure_label = (439.2, 632.1, 496.7, 641.6)

        flagged = _candidate_bboxes_colliding_with_preserved(
            [candidate], [figure_label]
        )

        self.assertEqual(flagged, [candidate.bbox])

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

    def test_wraparound_paragraph_lines_clear_of_float_are_not_flagged(self):
        """Flow Matching p9: a full-width paragraph wrapping a float table has
        a hull covering the cells, but its lines stay in the text column, so
        line-level probing must let it translate."""
        from pdf_zh_translator.pdf_layout import _candidate_bboxes_colliding_with_preserved

        candidate = TextBlock(
            page_index=0,
            bbox=(108.0, 474.0, 504.0, 593.0),
            text="Lastly, we experimented with Flow Matching for conditional image generation.",
            font_size=10.0,
            color=(0.0, 0.0, 0.0),
            source_line_bboxes=(
                (108.0, 474.0, 338.0, 484.0),
                (108.0, 485.0, 338.0, 495.0),
                (108.0, 496.0, 338.0, 506.0),
            ),
        )
        cell = (350.0, 480.0, 379.0, 499.0)

        flagged = _candidate_bboxes_colliding_with_preserved([candidate], [cell])

        self.assertEqual(flagged, [])

    def test_formula_dense_carved_block_keeps_hull_test(self):
        """IPMF p21: prose tails carved around preserved inline math have
        ragged left edges; reflow re-wraps from the hull origin and would
        overprint the preserved formula, so the hull test must flag it."""
        from pdf_zh_translator.pdf_layout import _candidate_bboxes_colliding_with_preserved

        candidate = TextBlock(
            page_index=0,
            bbox=(108.0, 355.0, 504.0, 383.0),
            text=(
                "˜PS^{−}^{1}^{/}^{2 and "
                "P^{′^{′ bound their spectral norms"
            ),
            font_size=10.0,
            color=(0.0, 0.0, 0.0),
            source_line_bboxes=(
                (307.0, 355.0, 374.0, 368.0),
                (368.0, 355.0, 420.0, 371.0),
                (108.0, 368.0, 312.0, 382.0),
                (306.0, 371.0, 504.0, 383.0),
            ),
        )
        preserved = (108.0, 355.0, 307.0, 370.0)

        flagged = _candidate_bboxes_colliding_with_preserved([candidate], [preserved])

        self.assertEqual(flagged, [candidate.bbox])


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


class FormulaKeepoutQaExemptionTests(unittest.TestCase):
    def _run_verify(self, keepout_bboxes):
        import tempfile
        import unittest.mock

        from pdf_zh_translator import pdf_layout

        english_line = (
            "ing the distribution q(x0, x1) and pW(xin|x0, x1) of the process."
        )
        original = fitz.open()
        page = original.new_page(width=612, height=792)
        page.insert_text((108, 370), english_line, fontsize=10)
        page.insert_text(
            (108, 500),
            "A regular paragraph that the translator fully handles as prose.",
            fontsize=10,
        )

        translated = fitz.open()
        page = translated.new_page(width=612, height=792)
        # Keepout line survives verbatim; the paragraph got translated.
        page.insert_text((108, 370), english_line, fontsize=10)
        page.insert_text(
            (108, 500), "一段被完整翻译的中文正文。", fontsize=10, fontname="china-ss"
        )

        block = TextBlock(
            page_index=0,
            bbox=(108.0, 340.0, 504.0, 430.0),
            text="The process combin-",
            font_size=10.0,
            color=(0.0, 0.0, 0.0),
            keepout_bboxes=list(keepout_bboxes),
        )

        def fake_prepare(document, preserve_graphics_text=False, **kwargs):
            regions_out = kwargs.get("preserved_regions_out")
            if regions_out is not None:
                regions_out.clear()
            return [(block, block.text, {})], {}, 0

        with unittest.mock.patch.object(
            pdf_layout, "prepare_translation_units", side_effect=fake_prepare
        ):
            import tempfile

            with tempfile.TemporaryDirectory() as tmpdir:
                original_path = Path(tmpdir) / "orig.pdf"
                translated_path = Path(tmpdir) / "zh.pdf"
                original.save(original_path)
                translated.save(translated_path)
                issues = pdf_layout.verify_translation_issues(
                    original_path, translated_path
                )
        original.close()
        translated.close()
        return issues

    def test_verbatim_keepout_line_is_not_flagged_untranslated(self):
        issues = self._run_verify(keepout_bboxes=[(108.0, 361.8, 420.0, 375.5)])

        self.assertFalse(
            any(issue.code == "untranslated_english" for issue in issues),
            [f"{i.code} p{i.page}" for i in issues],
        )

    def test_english_line_outside_keepouts_still_flagged(self):
        issues = self._run_verify(keepout_bboxes=[])

        self.assertTrue(
            any(issue.code == "untranslated_english" for issue in issues)
        )


class PreservedLineKeepoutAttachmentTests(unittest.TestCase):
    def test_display_equation_line_attaches_to_adjacent_segments(self):
        """A preserved equation line separating two prose runs must be
        registered as a keepout even when segment bboxes don't touch it."""
        prose_one = _line(
            "Properties of the starting process that would be desirable are",
            (108.0, 433.3, 504.0, 443.3),
        )
        equation = _line(
            f"{SENTINEL_OPEN}(1) q(x_{{0}}) = p_{{0}}(x_{{0}}){SENTINEL_CLOSE} and "
            f"{SENTINEL_OPEN}q(x_{{1}}){SENTINEL_CLOSE} to be close to "
            f"{SENTINEL_OPEN}p_{{1}}(x_{{1}}){SENTINEL_CLOSE}",
            (108.0, 466.0, 504.0, 476.8),
        )
        prose_two = _line(
            "In the IMF or IPF, we had to choose one of these properties.",
            (108.0, 477.0, 504.0, 487.0),
        )
        record = _RawBlockRec(lines=[prose_one, equation, prose_two])

        segments = segments_from_record(0, record)

        self.assertGreaterEqual(len(segments), 1)
        attached = [
            keepout
            for segment in segments
            for keepout in (segment.keepout_bboxes or [])
        ]
        self.assertIn(equation.bbox, attached)


class CaptionRecordEquationGuardTests(unittest.TestCase):
    def _caption_record(self):
        # Real geometry from flowmatching p8: bold "Figure 5:" prefix beside
        # the caption body, second line holding "64×64" math glyphs.
        return _RawBlockRec(
            lines=[
                _LineRec(
                    text="Figure 5:",
                    bbox=(365.4, 625.1, 404.8, 635.0),
                    spans=[_span("Figure 5:", (365.4, 625.1, 404.8, 635.0), flags=16)],
                ),
                _LineRec(
                    text="Image quality during",
                    bbox=(414.2, 625.1, 504.0, 635.0),
                    spans=[_span("Image quality during", (414.2, 625.1, 504.0, 635.0))],
                ),
                _LineRec(
                    text=(
                        f"training, ImageNet 64{SENTINEL_OPEN}×{SENTINEL_CLOSE}"
                        f"{SENTINEL_OPEN}64.{SENTINEL_CLOSE}"
                    ),
                    bbox=(365.4, 635.7, 473.3, 646.0),
                    spans=[_span("training, ImageNet 64×64.", (365.4, 635.7, 473.3, 646.0))],
                ),
            ]
        )

    def test_caption_record_is_never_equation_flagged(self):
        from pdf_zh_translator.pdf_layout import mark_equation_blocks

        flags = mark_equation_blocks([self._caption_record()])

        self.assertEqual(flags, [False])

    def test_caption_prefix_survives_segmentation_via_collect_semantics(self):
        segments = segments_from_record(7, self._caption_record())

        texts = [" ".join(strip_sentinels(seg.text).split()) for seg in segments]
        self.assertTrue(any(text.startswith("Figure 5:") for text in texts))


class MixedProseEquationBlockTests(unittest.TestCase):
    def _record(self, line_texts, *, y0=100.0):
        from pdf_zh_translator.pdf_layout import _LineRec, _RawBlockRec

        lines = []
        y = y0
        for text in line_texts:
            lines.append(
                _LineRec(text=text, bbox=(72.0, y, 520.0, y + 11.0), spans=[])
            )
            y += 12.0
        return _RawBlockRec(lines=lines)

    def test_paragraph_with_embedded_display_equation_is_not_strong_math(self):
        """PyMuPDF merges prose paragraphs and their display equations into
        one raw block; an equation number line like '(11)' must not flag the
        whole prose block as an equation (Flow Matching p4 regression)."""
        from pdf_zh_translator.pdf_layout import block_is_strong_math

        record = self._record(
            [
                "There is an infinite number of vector fields that generate a",
                "particular probability path, e.g. by adding a divergence free",
                "component to the continuity equation before training starts.",
                "\ue000ψ\ue001\ue000_{t}\ue001\ue000(\ue001\ue000x\ue001\ue000) =\ue001"
                "\ue000σ\ue001\ue000_{t}\ue001\ue000(\ue001\ue000x\ue001\ue000_{1}\ue001"
                "\ue000)\ue001",
                "(11)",
                "When x is distributed as a standard Gaussian, the transformation",
                "maps to a normally distributed random variable with known mean.",
            ]
        )

        self.assertFalse(block_is_strong_math(record))

    def test_equation_with_number_line_is_still_strong_math(self):
        from pdf_zh_translator.pdf_layout import block_is_strong_math

        record = self._record(
            [
                "\ue000ψ\ue001\ue000_{t}\ue001\ue000(\ue001\ue000x\ue001\ue000) =\ue001"
                "\ue000σ\ue001\ue000_{t}\ue001\ue000(\ue001\ue000x\ue001\ue000_{1}\ue001"
                "\ue000)\ue001\ue000x\ue001\ue000+\ue001\ue000µ\ue001\ue000_{t}\ue001",
                "(11)",
            ]
        )

        self.assertTrue(block_is_strong_math(record))

    def _equation_row_lines(self, y, formula):
        from pdf_zh_translator.pdf_layout import _LineRec

        return [
            _LineRec(text=formula, bbox=(250.0, y, 360.0, y + 11.0), spans=[]),
            _LineRec(text="(11)", bbox=(487.0, y, 504.0, y + 10.0), spans=[]),
        ]

    def test_paragraph_carrying_equations_is_not_a_table(self):
        """Equation + right-aligned number pairs mimic table rows (wide
        horizontal gap on one y-band); surrounding prose lines must veto the
        table verdict (Flow Matching p4 regression)."""
        from pdf_zh_translator.pdf_layout import _LineRec, _RawBlockRec, record_is_table

        prose = [
            "There is an infinite number of vector fields that generate a",
            "particular probability path, e.g. by adding a divergence free",
            "component to the continuity equation before training starts.",
        ]
        lines = [
            _LineRec(text=text, bbox=(72.0, 100.0 + i * 12.0, 520.0, 111.0 + i * 12.0), spans=[])
            for i, text in enumerate(prose)
        ]
        lines += self._equation_row_lines(140.0, "ψ_{t}(x) =σ_{t}")
        lines += self._equation_row_lines(152.0, "[ψ_{t}]_{∗}p(x)")

        self.assertFalse(record_is_table(_RawBlockRec(lines=lines)))

    def test_bare_equation_rows_still_look_like_table(self):
        from pdf_zh_translator.pdf_layout import _RawBlockRec, record_is_table

        lines = self._equation_row_lines(100.0, "ψ_{t}")
        lines += self._equation_row_lines(112.0, "σ_{t}")

        self.assertTrue(record_is_table(_RawBlockRec(lines=lines)))

    def test_prose_majority_with_fragile_overlap_stays_strong_math(self):
        """IPMF p27: a prose-majority record whose lines share area with 2D
        math fragments (sub/superscript towers) cannot be processed line-wise
        without erasing the neighbour's glyphs; the prose guard must yield."""
        from pdf_zh_translator.pdf_layout import (
            _LineRec,
            _RawBlockRec,
            block_is_strong_math,
        )

        prose = [
            "and Ch(C2) is 1-Lipschitz w.r.t. the Frobenius norm (Wihler,",
            "2009, Thm. 1.1). Note that the bound holds for all cases here.",
            "It can be shown from the orthogonality of K that this works.",
            "Therefore the mapping is contractive in the Frobenius norm.",
        ]
        lines = [
            _LineRec(text=text, bbox=(108.0, 100.0 + i * 12.0, 504.0, 111.0 + i * 12.0), spans=[])
            for i, text in enumerate(prose)
        ]
        # Superscript tower sharing area with the following prose line.
        lines.append(
            _LineRec(
                text="\ue000∥\ue001\ue000K\ue001\ue000d\ue001\ue000C\ue001\ue000∥\ue001\ue000^{\ue001\ue0002\ue001",
                bbox=(120.0, 146.0, 200.0, 158.0),
                spans=[],
            )
        )
        lines.append(
            _LineRec(
                text=(
                    "\ue000_{\ue001\ue000F\ue001\ue000}\ue001 \ue000=\ue001 "
                    "\ue000T\ue001\ue000r\ue001\ue000[\ue001\ue000(\ue001\ue000K\ue001dC)] "
                    "= 0 since K dK is skew-symmetric here."
                ),
                bbox=(120.0, 150.0, 480.0, 161.0),
                spans=[],
            )
        )

        self.assertTrue(block_is_strong_math(_RawBlockRec(lines=lines)))

    def test_wrapped_prose_with_inline_subscripts_is_not_condemned(self):
        """oc p4 (4.3): a long prose paragraph whose wrapped line carries an
        inline subscript on its own physical line, ending with a display
        equation number, must stay translatable — it is normal paragraph
        typesetting, not 2D math sharing area with prose."""
        from pdf_zh_translator.pdf_layout import (
            _LineRec,
            _RawBlockRec,
            block_is_strong_math,
            record_is_table,
        )

        sentences = [
            "As illustrated in Fig. 4, the propagation transformer con-",
            "sists of three main components: (1) the motion-aware layer",
            "normalization module implicitly updates the object state ac-",
            "cording to the context embedding and motion information",
            "recorded in the memory queue; (2) the hybrid attention re-",
            "places the default self-attention operation. It plays the role",
            "of temporal modeling and removing duplicated predictions;",
            "Motion-aware Layer Normalization is designed to model",
            "the movement of objects. For simplicity, we take the trans-",
            "formation process from the last frame as the exam-",
            "ple and adopt the same operation for other previous frames.",
            "Given the ego pose matrix from the last frame and",
            "current frame, the ego transfo-",
        ]
        lines = [
            _LineRec(text=text, bbox=(309.0, 316.0 + i * 12.0, 545.0, 326.0 + i * 12.0), spans=[])
            for i, text in enumerate(sentences)
        ]
        # Wrapped line mixing an inline subscript tower with hyphenated prose;
        # it overlaps the previous prose line's area (PDF line leading).
        lines.append(
            _LineRec(
                text="_{t}_{−}_{1} can be cal-",
                bbox=(309.0, 466.0, 400.0, 477.0),
                spans=[],
            )
        )
        # Display equation tail + its number.
        lines.append(
            _LineRec(text="E^{t}", bbox=(400.0, 480.0, 430.0, 492.0), spans=[])
        )
        lines.append(
            _LineRec(text="_{t}_{−}_{1} =E^{inv}", bbox=(400.0, 488.0, 500.0, 500.0), spans=[])
        )
        lines.append(
            _LineRec(text="t", bbox=(470.0, 494.0, 480.0, 504.0), spans=[])
        )
        lines.append(
            _LineRec(text="(8)", bbox=(533.0, 486.0, 545.0, 496.0), spans=[])
        )

        record = _RawBlockRec(lines=lines)
        self.assertFalse(block_is_strong_math(record))
        self.assertFalse(record_is_table(record))

    def test_long_inference_paragraph_with_split_inline_scripts_is_not_table(self):
        """pi0 p16: one overlapping subscript cannot preserve 40 prose lines."""
        from pdf_zh_translator.pdf_layout import (
            block_is_strong_math,
            record_is_table,
        )

        lines = [
            _line("D. Inference", (312.0, 206.2, 363.9, 216.1)),
            _line(
                "Recall that our model takes an observation and predicts actions",
                (321.9, 221.1, 563.0, 231.0),
            ),
        ]
        for index in range(12):
            lines.append(
                _line(
                    "The model runs a forward pass and integrates the vector field",
                    (312.0, 233.0 + index * 12.0, 563.0, 243.0 + index * 12.0),
                )
            )
        lines.extend(
            [
                _LineRec(
                    text=f"chunk{SENTINEL_OPEN}A_t{SENTINEL_CLOSE}, we encode each image",
                    bbox=(312.0, 377.0, 510.0, 389.1),
                    spans=[],
                ),
                _LineRec(
                    text=f"{SENTINEL_OPEN}_t, ..., I^n{SENTINEL_CLOSE}",
                    bbox=(506.0, 377.0, 536.9, 390.0),
                    spans=[],
                ),
            ]
        )
        record = _RawBlockRec(lines=lines)

        self.assertFalse(block_is_strong_math(record))
        self.assertFalse(record_is_table(record))

        segments = segments_from_record(15, record)
        self.assertEqual(segments[0].block_type, "heading")
        self.assertTrue(any(segment.block_type == "body" for segment in segments[1:]))


    def test_script_split_fragment_matches_by_window(self):
        """Sub/superscript stream-order divergence keeps a long contiguous run
        of the fragment on the page; that must count as present."""
        fragment = "H(ε)=H(F∗ε)."
        # Page text carries the run split right before the superscript ε.
        translated_compact = "版面文字H(ε)=H(F∗其余行内容ε⟩继续"

        self.assertTrue(_formula_fragment_present(fragment, translated_compact))

    def test_fully_missing_fragment_is_still_flagged(self):
        fragment = "H(ε)=H(F∗ε)."
        translated_compact = "这页只有无关文本和另一个公式ψ(x)=0"

        self.assertFalse(_formula_fragment_present(fragment, translated_compact))

    def test_fragment_interleaved_with_translation_filler_matches(self):
        """A formula whose pieces survive in order, separated by translated
        prose, is still present on the page."""
        fragment = "ψ-1t(y)=y-µt(x1)"
        translated_compact = "版面。ψ-1t中文占位译文。ψt中文占位译文。(y)=y-µt(x1)第121段"

        self.assertTrue(_formula_fragment_present(fragment, translated_compact))

    def test_short_fragment_with_displaced_math_symbol_matches(self):
        fragment = "L−1 = √"
        translated_compact = "用于检查版面。L-1=中文占位译文。√第207段中文占位译文。"

        self.assertTrue(_formula_fragment_present(fragment, translated_compact))

    def test_reordered_pieces_do_not_match(self):
        """Pieces must appear in reading order; reversed pieces mean the
        formula was rewritten."""
        fragment = "ψ-1t(y)=y-µt(x1)"
        translated_compact = "(y)=y-µt(x1)中文占位译文。ψ-1t结尾"

        self.assertFalse(_formula_fragment_present(fragment, translated_compact))

    def test_altered_value_is_still_flagged(self):
        fragment = "α+β=γ2/4x"
        translated_compact = "正文α+β=δ2/4x其余"

        self.assertFalse(_formula_fragment_present(fragment, translated_compact))


class FormulaScriptNotationCompareTests(unittest.TestCase):
    """Formulas re-rendered via script-notation fallback extract as
    ``F^{\\x00}_{ε}``: sub/superscripts gain ^{}/_{} wrappers and glyphs the
    fallback font lacks (like ∗) extract as NUL. Comparison must see through
    both."""

    def test_script_notation_and_nul_glyphs_match(self):
        # Real sample from SafeTransport p5 (DeepSeek production run).
        fragment = "H(ε) = H(F∗ε)."
        translated_compact = (
            "定义奖励R(ε)=-〈D,F^{\x00}F\x00_{ε}ε〉和熵"
            "H(ε)=H(F^{\x00}=H(F\x00_{ε}).ε)."
        )

        self.assertTrue(_formula_fragment_present(fragment, translated_compact))

    def test_interleaved_script_rendering_matches(self):
        # Real sample from SafeTransport p3.
        fragment = "a′ρ∗(s,a′)."
        translated_compact = "ρ\x00(s,a)/P_{a}_{′}ρ^{\x00}(s,a′ρ\x00(s,a^{′}).a′)约束"

        self.assertTrue(_formula_fragment_present(fragment, translated_compact))

    def test_genuinely_missing_formula_still_flagged_with_script_noise(self):
        fragment = "H(ε) = H(F∗ε)."
        translated_compact = "页面只有P_{a}_{′}ρ^{\x00}(s,a)这一个别的公式和中文"

        self.assertFalse(_formula_fragment_present(fragment, translated_compact))


class FormulaFragmentExtractionProseTrimTests(unittest.TestCase):
    def test_author_year_citation_is_not_part_of_formula_signature(self):
        document = fitz.open()
        page = document.new_page(width=300, height=200)
        page.insert_text((40, 80), "beta(tau) (Song et al., 2020).", fontsize=10)

        fragments = _extract_formula_fragments(page)

        document.close()
        self.assertFalse(any("Song" in fragment for fragment in fragments))

    def test_leading_prose_words_are_trimmed_from_fragment(self):
        """A source block can prepend prose like 'objective),' to a formula;
        the prose is legitimately translated, so it must not be part of the
        fragment we require verbatim."""
        from pdf_zh_translator.pdf_layout import _trim_fragment_prose

        self.assertEqual(
            _trim_fragment_prose("objective), g′(ε) = −H(F∗ε)."),
            "g′(ε) = −H(F∗ε).",
        )

    def test_trailing_prose_words_are_trimmed(self):
        from pdf_zh_translator.pdf_layout import _trim_fragment_prose

        self.assertEqual(
            _trim_fragment_prose("H(ε) = H(F∗ε), therefore"),
            "H(ε) = H(F∗ε),",
        )

    def test_math_function_names_are_kept(self):
        from pdf_zh_translator.pdf_layout import _trim_fragment_prose

        self.assertEqual(
            _trim_fragment_prose("arg min F(x) = exp(−x)"),
            "arg min F(x) = exp(−x)",
        )

    def test_majority_deleted_fragment_is_still_flagged(self):
        fragment = "∥Ξ-1n(A)∥2≤√L-1(1-ω)"
        # Only a short head survives (formula largely overwritten).
        translated_compact = "前文∥Ξ-1后文完全不同"

        self.assertFalse(_formula_fragment_present(fragment, translated_compact))


class CaptionBandExtensionBlockerTests(unittest.TestCase):
    def test_preserved_table_rows_survive_band_extension(self):
        """A wrapped caption chains downward over CJK lines, but preserved
        English table rows inside the grown vertical range must remain
        visible to the preserved-region comparison."""
        from pdf_zh_translator.pdf_layout import (
            _entries_outside_caption_bands,
            _extend_caption_bands_for_translated,
        )

        band = (100.0, 400.0, 500.0, 497.0)
        wrapped = ((110.0, 495.0, 480.0, 508.0), "中文图注换行第二行")
        left_body = [
            ((110.0, 509.0, 300.0, 521.0), "左栏中文正文第一行"),
            ((110.0, 522.0, 300.0, 534.0), "左栏中文正文第二行"),
        ]
        table_rows = [
            ((330.0, 520.0, 490.0, 529.0), "DSBM-IMF-OT 53.42 0.085"),
            ((330.0, 529.0, 490.0, 538.0), "DSBM-Identity 65.19 0.054"),
        ]
        entries = [wrapped, *left_body, *table_rows]

        extended = _extend_caption_bands_for_translated([band], entries)
        kept = _entries_outside_caption_bands(entries, extended, base_bboxes=[band])

        kept_texts = [text for _, text in kept]
        self.assertIn("DSBM-IMF-OT 53.42 0.085", kept_texts)
        self.assertIn("DSBM-Identity 65.19 0.054", kept_texts)
        self.assertNotIn("中文图注换行第二行", kept_texts)

    def test_non_cjk_caption_tail_fragment_is_excluded(self):
        """A wrapped caption line can end with a non-CJK fragment (e.g.
        '×64.') extracted as its own entry; being glued to an excluded CJK
        line, it is caption content, not preserved-table text."""
        from pdf_zh_translator.pdf_layout import (
            _entries_outside_caption_bands,
            _extend_caption_bands_for_translated,
        )

        band = (360.0, 620.0, 506.0, 648.0)
        entries = [
            ((363.0, 648.0, 383.0, 657.0), "分辨率为64"),
            ((385.0, 648.0, 403.0, 657.0), "×64."),
        ]

        extended = _extend_caption_bands_for_translated([band], entries)
        kept = _entries_outside_caption_bands(entries, extended, base_bboxes=[band])

        self.assertEqual(kept, [])

    def test_wrapped_caption_lines_are_still_excluded(self):
        from pdf_zh_translator.pdf_layout import (
            _entries_outside_caption_bands,
            _extend_caption_bands_for_translated,
        )

        band = (100.0, 400.0, 500.0, 430.0)
        entries = [
            ((110.0, 429.0, 480.0, 441.0), "中文图注换行第二行"),
            ((110.0, 441.0, 480.0, 453.0), "中文图注换行第三行"),
        ]

        extended = _extend_caption_bands_for_translated([band], entries)
        kept = _entries_outside_caption_bands(entries, extended, base_bboxes=[band])

        self.assertEqual(kept, [])


class FormulaCompareDualSourceTests(unittest.TestCase):
    def test_fragment_present_in_any_source_is_not_missing(self):
        """Block-joined and raw extraction can order 2D math differently;
        presence in either source means the formula survived."""
        from pdf_zh_translator.pdf_layout import _missing_formula_fragments

        fragment = "ψ-1t(y)=y-µt(x1)"
        blocks_compact = "打乱后的(y)ψ-1t=y-µt(x1)顺序"
        raw_compact = "正确顺序里ψ-1t(y)=y-µt(x1)出现"

        missing = _missing_formula_fragments(
            [fragment], [blocks_compact, raw_compact]
        )

        self.assertEqual(missing, [])

    def test_fragment_absent_from_all_sources_is_missing(self):
        from pdf_zh_translator.pdf_layout import _missing_formula_fragments

        fragment = "ψ-1t(y)=y-µt(x1)"

        missing = _missing_formula_fragments(
            [fragment], ["无关文本一", "无关文本二"]
        )

        self.assertEqual(missing, [fragment])


def test_table_region_envelopes_split_side_by_side_tables():
    """Object-Centric p8: two tables side by side (Table 7 left, Table 8
    right) must not chain into one full-width envelope that swallows the
    prose around them and trips preserved-text QA."""
    caption7 = TextBlock(
        0,
        (59.0, 253.0, 281.0, 277.0),
        "Table 7. Number of frames (N) for long-term fusion.",
        9.0,
        (0.0, 0.0, 0.0),
        block_type="caption",
    )
    caption8 = TextBlock(
        0,
        (309.0, 253.0, 545.0, 295.0),
        "Table 8. Form of the temporal propagation.",
        9.0,
        (0.0, 0.0, 0.0),
        block_type="caption",
    )
    cells = []
    for row in range(5):
        y = 284.0 + row * 9.5
        for x0, x1 in ((83.0, 87.0), (124.0, 141.0), (191.0, 208.0), (264.0, 278.0)):
            cells.append(
                TextBlock(
                    0,
                    (x0, y, x1, y + 8.0),
                    "0.31",
                    9.0,
                    (0.0, 0.0, 0.0),
                    block_type="table",
                )
            )
    for row in range(4):
        y = 315.0 + row * 8.5
        for x0, x1 in ((419.0, 433.0), (470.0, 484.0), (526.0, 538.0)):
            cells.append(
                TextBlock(
                    0,
                    (x0, y, x1, y + 7.0),
                    "0.31",
                    9.0,
                    (0.0, 0.0, 0.0),
                    block_type="table",
                )
            )
    blocks = [caption7, caption8, *cells]

    regions = _table_region_bboxes(blocks)

    left = (59.0, 284.0, 281.0, 284.0 + 4 * 9.5 + 8.0)
    right = (309.0, 315.0, 545.0, 315.0 + 3 * 8.5 + 7.0)
    assert regions == [left, right]


def test_table_region_does_not_chain_shifted_figure_labels_below_table():
    """GuidedVLA p28: Figure 11 labels sit shortly below Table XIII.

    Their row overlaps the table's x-span by only about half.  It is a
    separate right-column component and must not extend the preserved table
    envelope into the body prose beginning at the same y position.
    """
    caption = TextBlock(
        0,
        (49.0, 54.0, 563.0, 101.0),
        "Table XIII: Ablation study on auxiliary loss weights.",
        9.0,
        (0.0, 0.0, 0.0),
        block_type="caption",
    )
    table_rows = [
        TextBlock(
            0,
            (158.0, y0, 454.0, y0 + 9.0),
            "Final 0.001 0.001 94.60 89.00 79.90 87.83",
            9.0,
            (0.0, 0.0, 0.0),
            block_type="table",
        )
        for y0 in (112.0, 130.0, 148.0, 175.0)
    ]
    figure_header = TextBlock(
        0,
        (328.0, 208.0, 556.0, 235.0),
        "Task Positional generalization Lighting generalization Scene generalization",
        7.0,
        (0.0, 0.0, 0.0),
        block_type="table",
    )

    assert _table_region_bboxes([caption, *table_rows, figure_header]) == [
        (49.0, 112.0, 563.0, 184.0)
    ]


def test_borderless_aligned_table_above_caption_is_preserved():
    """Distillation p8: a borderless three-column table is emitted as one
    ordinary text block per cell and has its caption below the table."""
    from pdf_zh_translator.pdf_layout import classify_blocks

    cells = []
    rows = [
        ("System & training set", "Train Frame Accuracy", "Test Frame Accuracy"),
        ("Baseline (100% of training set)", "63.4%", "58.9%"),
        ("Baseline (3% of training set)", "67.3%", "44.5%"),
        ("Soft Targets (3% of training set)", "65.4%", "57.0%"),
    ]
    columns = [(158.0, 274.0), (286.0, 366.0), (378.0, 455.0)]
    for row_index, row in enumerate(rows):
        y0 = 80.0 + row_index * 10.0
        for text, (x0, x1) in zip(row, columns):
            cells.append(
                TextBlock(
                    0,
                    (x0, y0, x1, y0 + 9.0),
                    text,
                    9.0,
                    (0.0, 0.0, 0.0),
                )
            )
    caption = TextBlock(
        0,
        (108.0, 130.0, 504.0, 155.0),
        "Table 5: Soft targets allow a new model to generalize well.",
        10.0,
        (0.0, 0.0, 0.0),
    )
    blocks = [*cells, caption]

    classify_blocks(blocks, 0, 792.0, [])

    assert caption.block_type == "caption"
    assert all(cell.block_type == "table" for cell in cells)
    assert all(not cell.should_translate for cell in cells)


def test_captioned_horizontal_rule_table_has_preserved_envelope():
    from pdf_zh_translator.pdf_layout import _vector_table_region_bboxes

    class FakePage:
        def get_drawings(self):
            return [
                {"rect": SimpleNamespace(x0=108.0, y0=y, x1=516.0, y1=y)}
                for y in (150.0, 182.0, 214.0, 246.0)
            ]

    caption = TextBlock(
        0,
        (108.0, 116.0, 505.0, 141.0),
        "Table 12: Summary of math word problem benchmarks.",
        9.0,
        (0.0, 0.0, 0.0),
        block_type="caption",
    )
    rows = [
        TextBlock(
            0,
            (112.0, y, 512.0, y + 20.0),
            text,
            9.0,
            (0.0, 0.0, 0.0),
        )
        for y, text in (
            (154.0, "GSM8K 1319 example problem"),
            (186.0, "SVAMP 1000 example problem"),
            (218.0, "ASDiv 2096 example problem"),
        )
    ]

    assert _vector_table_region_bboxes(FakePage(), [caption, *rows]) == [
        (108.0, 150.0, 516.0, 246.0)
    ]


def test_tall_formula_keepout_is_not_vertically_trimmed_as_adjacent_line():
    """Cross-line operators are handled by block-level horizontal splitting."""
    from pdf_zh_translator.pdf_layout import (
        _LineRec,
        trim_redact_bbox_against_formula_lines,
    )

    keepout = _LineRec(
        text="sum",
        bbox=(310.3, 394.4, 320.8, 424.3),
        spans=[],
    )

    trimmed = trim_redact_bbox_against_formula_lines(
        (108.0, 416.9, 468.4, 430.2),
        [keepout],
    )

    assert trimmed == (108.0, 416.9, 468.4, 430.2)


def test_tall_formula_keepout_splits_block_redaction_around_operator():
    from pdf_zh_translator.pdf_layout import _trim_redacts_against_block_keepouts

    block = TextBlock(
        0,
        (108.0, 416.9, 468.4, 430.2),
        "If the temperature is high, we can approximate:",
        10.0,
        (0.0, 0.0, 0.0),
        redact_bboxes=[(108.0, 416.9, 468.4, 430.2)],
        keepout_bboxes=[(310.3, 394.4, 320.8, 424.3)],
    )

    _trim_redacts_against_block_keepouts([block])

    assert block.redact_bboxes is not None
    assert len(block.redact_bboxes) == 2
    assert block.redact_bboxes[0][2] < 310.3
    assert block.redact_bboxes[1][0] > 320.8


class JustifiedWordFragmentTests(unittest.TestCase):
    def test_justified_keyword_word_does_not_split_paragraph(self):
        """oc p6 (5.2): heavy justification makes PyMuPDF report every word of
        the first line as its own line; the word "experiments" is a structural
        heading keyword but here it is mid-sentence and must not split the
        paragraph into overlapping fragments."""
        from pdf_zh_translator.pdf_layout import (
            _LineRec,
            _RawBlockRec,
            segments_from_record,
        )

        def word_line(text, bbox):
            return _LineRec(
                text=text,
                bbox=bbox,
                spans=[{"text": text, "bbox": bbox, "size": 9.0, "flags": 4, "color": 0}],
            )

        words = ["We", "conduct", "experiments", "with", "ResNet50", "[13],"]
        xs = [62.1, 86.1, 128.7, 188.2, 217.0, 267.3]
        xe = [75.1, 117.7, 177.2, 205.9, 256.3, 286.4]
        lines = [
            word_line(w, (x0, 461.7, x1, 471.6))
            for w, x0, x1 in zip(words, xs, xe)
        ]
        lines.append(
            word_line(
                "ResNet101, V2-99 [21] and ViT [8] backbones under differ-",
                (50.1, 473.6, 286.4, 483.6),
            )
        )
        lines.append(
            word_line(
                "ent pre-training. Following previous methods [27, 30, 39],",
                (50.1, 485.6, 286.4, 495.5),
            )
        )

        segments = segments_from_record(0, _RawBlockRec(lines=lines), equation_record=False)

        self.assertEqual(len(segments), 1)
        self.assertEqual(segments[0].block_type, "body")
        self.assertIn("We conduct experiments with", segments[0].text)


class DetachedInlineScriptTests(unittest.TestCase):
    def test_cross_record_stacked_fraction_rejoins_formula_paragraph(self):
        prose_lines = [
            _line(
                "This paragraph explains the model training procedure and results.",
                (50.0, 100.0 + index * 12.0, 300.0, 110.0 + index * 12.0),
            )
            for index in range(12)
        ]
        prose_lines.append(
            _LineRec(
                text=f"by {SENTINEL_OPEN}p(t)=Beta(s-t{SENTINEL_CLOSE}",
                bbox=(50.0, 244.0, 150.0, 256.0),
                spans=[_span("p(t)=Beta(s-t", (60.0, 244.0, 150.0, 256.0), font="CMMI10")],
                math_bboxes=[(60.0, 244.0, 150.0, 256.0)],
                math_run_bboxes=[(60.0, 244.0, 150.0, 256.0)],
                prose_bboxes=[(50.0, 244.0, 60.0, 256.0)],
            )
        )
        continuation = _RawBlockRec(
            lines=[
                _LineRec(
                    text=f"{SENTINEL_OPEN}_s;1.5,1){SENTINEL_CLOSE} and is visualized.",
                    bbox=(120.0, 252.0, 300.0, 264.0),
                    spans=[_span("_s;1.5,1)", (120.0, 252.0, 180.0, 264.0), font="CMMI10")],
                    math_bboxes=[(120.0, 252.0, 180.0, 264.0)],
                    math_run_bboxes=[(120.0, 252.0, 180.0, 264.0)],
                    prose_bboxes=[(180.0, 252.0, 300.0, 264.0)],
                ),
                _line("This setting allows for a ratio greater than", (50.0, 264.0, 275.0, 276.0)),
                _line("1", (284.0, 264.0, 290.0, 271.0)),
                _LineRec(
                    text=f"{SENTINEL_OPEN}_1000{SENTINEL_CLOSE}",
                    bbox=(280.0, 271.0, 300.0, 279.0),
                    spans=[_span("1000", (280.0, 271.0, 300.0, 279.0), size=7.0, font="CMR7")],
                    math_bboxes=[(280.0, 271.0, 300.0, 279.0)],
                    math_run_bboxes=[(280.0, 271.0, 300.0, 279.0)],
                ),
                _line("or up to one thousand integration steps.", (50.0, 280.0, 220.0, 292.0)),
            ]
        )

        records = _merge_wrapped_formula_continuation_records(
            [_RawBlockRec(lines=prose_lines), continuation]
        )
        blocks = segments_from_record(0, records[0], equation_record=False)

        self.assertEqual(len(records), 1)
        self.assertEqual(len(blocks), 1)
        self.assertTrue(blocks[0].flow_inline_math)
        self.assertIn("or up to one thousand", strip_sentinels(blocks[0].text))
        self.assertIn(f"{SENTINEL_OPEN}1{SENTINEL_CLOSE}", blocks[0].text)

    def test_numbered_display_equation_records_are_not_joined(self):
        prose = _RawBlockRec(
            lines=[
                _line(
                    "This paragraph explains the model training procedure and results.",
                    (50.0, 100.0 + index * 12.0, 300.0, 110.0 + index * 12.0),
                )
                for index in range(12)
            ]
            + [
                _LineRec(
                    text=f"by {SENTINEL_OPEN}p(t){SENTINEL_CLOSE}",
                    bbox=(50.0, 244.0, 100.0, 256.0),
                    spans=[_span("p(t)", (60.0, 244.0, 100.0, 256.0), font="CMMI10")],
                    math_bboxes=[(60.0, 244.0, 100.0, 256.0)],
                    math_run_bboxes=[(60.0, 244.0, 100.0, 256.0)],
                    prose_bboxes=[(50.0, 244.0, 60.0, 256.0)],
                )
            ]
        )
        equation = _RawBlockRec(
            lines=[
                _line("(12)", (280.0, 252.0, 300.0, 264.0)),
                _LineRec(
                    text=f"{SENTINEL_OPEN}x=y+z{SENTINEL_CLOSE}",
                    bbox=(120.0, 252.0, 180.0, 264.0),
                    spans=[_span("x=y+z", (120.0, 252.0, 180.0, 264.0), font="CMMI10")],
                    math_bboxes=[(120.0, 252.0, 180.0, 264.0)],
                    math_run_bboxes=[(120.0, 252.0, 180.0, 264.0)],
                ),
                _line("This is a complete explanatory sentence.", (50.0, 266.0, 240.0, 278.0)),
            ]
        )

        self.assertEqual(
            len(_merge_wrapped_formula_continuation_records([prose, equation])),
            2,
        )

    def test_long_prose_paragraph_recovers_wide_formula_continuation(self):
        lines = [
            _line(
                "This paragraph explains a model and its training procedure in detail.",
                (50.0, 100.0 + index * 12.0, 300.0, 110.0 + index * 12.0),
            )
            for index in range(12)
        ]
        formula_bbox = (82.0, 244.0, 190.0, 256.0)
        lines.extend(
            [
                _LineRec(
                    text=(
                        f"{SENTINEL_OPEN}q(A_t)=N(A_t,I){SENTINEL_CLOSE}. "
                        "In practice, the network"
                    ),
                    bbox=(82.0, 244.0, 300.0, 256.0),
                    spans=[_span("q(A_t)=N(A_t,I)", formula_bbox, font="CMMI10")],
                    math_bboxes=[formula_bbox],
                    math_run_bboxes=[formula_bbox],
                    prose_bboxes=[(192.0, 244.0, 300.0, 256.0)],
                ),
                _line(
                    "continues training with stable optimization and reports results.",
                    (50.0, 256.0, 300.0, 266.0),
                ),
                _LineRec(
                    text=f"field {SENTINEL_OPEN}u(A_t)=A_t-e{SENTINEL_CLOSE}",
                    bbox=(50.0, 268.0, 150.0, 280.0),
                    spans=[_span("u(A_t)=A_t-e", (75.0, 268.0, 150.0, 280.0), font="CMMI10")],
                    math_bboxes=[(75.0, 268.0, 150.0, 280.0)],
                    math_run_bboxes=[(75.0, 268.0, 150.0, 280.0)],
                    prose_bboxes=[(50.0, 268.0, 75.0, 280.0)],
                ),
            ]
        )

        blocks = segments_from_record(0, _RawBlockRec(lines=lines), equation_record=False)

        self.assertEqual(len(blocks), 1)
        self.assertTrue(blocks[0].flow_inline_math)
        self.assertEqual(len(blocks[0].source_math_bboxes), 2)
        self.assertIn("In practice, the network", strip_sentinels(blocks[0].text))

    def test_long_prose_does_not_absorb_wide_pure_display_formula(self):
        lines = [
            _line(
                "This paragraph explains a model and its training procedure in detail.",
                (50.0, 100.0 + index * 12.0, 300.0, 110.0 + index * 12.0),
            )
            for index in range(12)
        ]
        formula_bbox = (50.0, 244.0, 285.0, 258.0)
        lines.append(
            _LineRec(
                text=f"{SENTINEL_OPEN}q(A_t)=N(A_t,I)+u(A_t){SENTINEL_CLOSE}",
                bbox=formula_bbox,
                spans=[_span("q(A_t)=N(A_t,I)+u(A_t)", formula_bbox, font="CMMI10")],
                math_bboxes=[formula_bbox],
                math_run_bboxes=[formula_bbox],
            )
        )

        blocks = segments_from_record(0, _RawBlockRec(lines=lines), equation_record=False)

        self.assertFalse(any(block.flow_inline_math for block in blocks))

    def test_detached_subscripts_rejoin_one_reflowable_paragraph(self):
        raw_block = {
            "lines": [
                {
                    "bbox": (108.0, 100.0, 196.0, 113.5),
                    "spans": [
                        {
                            "text": "The preconditioners ",
                            "bbox": (108.0, 101.5, 185.0, 111.5),
                            "size": 10.0,
                            "font": "Times-Roman",
                            "flags": 4,
                            "color": 0,
                        },
                        {
                            "text": "c",
                            "bbox": (185.0, 101.2, 191.0, 111.2),
                            "size": 10.0,
                            "font": "CMMI10",
                            "flags": 4,
                            "color": 0,
                        },
                        {
                            "text": "tau",
                            "bbox": (191.0, 99.9, 196.0, 106.9),
                            "size": 7.0,
                            "font": "CMMI7",
                            "flags": 4,
                            "color": 0,
                        },
                    ],
                },
                {
                    "bbox": (191.0, 100.0, 500.0, 123.5),
                    "spans": [
                        {
                            "text": "in",
                            "bbox": (191.0, 106.4, 198.0, 113.4),
                            "size": 7.0,
                            "font": "Times-Roman",
                            "flags": 4,
                            "color": 0,
                        },
                        {
                            "text": " are selected to keep the network input at unit variance.",
                            "bbox": (198.0, 101.5, 500.0, 111.5),
                            "size": 10.0,
                            "font": "Times-Roman",
                            "flags": 4,
                            "color": 0,
                        },
                    ],
                },
                {
                    "bbox": (108.0, 114.0, 470.0, 124.0),
                    "spans": [
                        {
                            "text": (
                                "These preconditioners are fully described in the appendix "
                                "and remain stable during training."
                            ),
                            "bbox": (108.0, 114.0, 500.0, 124.0),
                            "size": 10.0,
                            "font": "Times-Roman",
                            "flags": 4,
                            "color": 0,
                        }
                    ],
                },
            ]
        }

        record, _ = parse_block_lines(raw_block, page_width=612.0)
        self.assertIsNotNone(record)
        segments = segments_from_record(0, record, equation_record=False)

        self.assertEqual(len(segments), 1)
        self.assertTrue(segments[0].flow_inline_math)
        self.assertIn("c", strip_sentinels(segments[0].text))
        self.assertIn("_{in}", segments[0].text)
        self.assertNotIn("c in are", strip_sentinels(segments[0].text))

    def test_full_size_wrapped_in_remains_prose(self):
        raw_block = {
            "lines": [
                {
                    "bbox": (108.0, 100.0, 500.0, 110.0),
                    "spans": [
                        {
                            "text": "We evaluate a policy with x",
                            "bbox": (108.0, 100.0, 250.0, 110.0),
                            "size": 10.0,
                            "font": "Times-Roman",
                            "flags": 4,
                            "color": 0,
                        }
                    ],
                },
                {
                    "bbox": (108.0, 112.0, 500.0, 122.0),
                    "spans": [
                        {
                            "text": "in multiple environments and report the results.",
                            "bbox": (108.0, 112.0, 500.0, 122.0),
                            "size": 10.0,
                            "font": "Times-Roman",
                            "flags": 4,
                            "color": 0,
                        }
                    ],
                },
            ]
        }

        record, _ = parse_block_lines(raw_block, page_width=612.0)

        self.assertIsNotNone(record)
        self.assertFalse(record.detached_inline_scripts)
        self.assertIn("in multiple", strip_sentinels(record.lines[1].text))


class TranslationEchoDetectionTests(unittest.TestCase):
    def test_source_echo_allows_detached_email_and_url_path_fragments(self):
        from pdf_zh_translator.pdf_layout import _translation_retains_source_prose_run

        for text in (
            "thibautlav,gizacard,egrave,glample}@meta.com",
            "tree/main/projects/OPT/chronicles",
        ):
            with self.subTest(text=text):
                block = TextBlock(
                    page_index=0,
                    bbox=(72.0, 100.0, 500.0, 120.0),
                    text=text,
                    font_size=8.0,
                    color=(0.0, 0.0, 0.0),
                )
                self.assertFalse(_translation_retains_source_prose_run(block, text))

    def test_source_echo_ignores_unclosed_author_year_citation_tail(self):
        from pdf_zh_translator.pdf_layout import _translation_retains_source_prose_run

        block = TextBlock(
            page_index=2,
            bbox=(72.0, 100.0, 500.0, 180.0),
            text=(
                "Robots can generalize to new object instances and new tasks "
                "(Finn et al., 2017; Levine et al., 2018; Jang et al., 2021; "
                "Jiang et al., 2022; Liu et al.,"
            ),
            font_size=10.0,
            color=(0.0, 0.0, 0.0),
        )
        translated = (
            "机器人可以泛化到新的物体实例和任务（Finn et al., 2017; "
            "Levine et al., 2018; Jang et al., 2021; Jiang et al., 2022; "
            "Liu et al.,"
        )

        self.assertFalse(_translation_retains_source_prose_run(block, translated))

    def test_generated_poem_label_marks_adjacent_sample_columns_verbatim(self):
        from pdf_zh_translator.pdf_layout import (
            _source_unit_is_verbatim_generated_sample,
        )

        label = TextBlock(
            page_index=48,
            bbox=(90.0, 192.0, 406.0, 200.0),
            text="-------- Generated Poem 1 -------- Generated Poem 3 --------",
            font_size=8.0,
            color=(0.0, 0.0, 0.0),
        )
        poem = TextBlock(
            page_index=48,
            bbox=(280.0, 208.0, 455.0, 455.0),
            text="The sun was all we had. All is changed. White fields remain.",
            font_size=8.0,
            color=(0.0, 0.0, 0.0),
            source_lines=20,
        )

        self.assertTrue(_source_unit_is_verbatim_generated_sample(poem, [label, poem]))

    def test_preserved_formula_words_do_not_count_as_source_prose_echo(self):
        from pdf_zh_translator.pdf_layout import (
            _translation_retains_foreign_prose,
            _translation_retains_source_prose_run,
        )

        block = TextBlock(
            page_index=24,
            bbox=(107.7, 622.0, 504.0, 642.3),
            text=(
                "Table 12: Aggregated pairwise judgments where the value is "
                f"{SENTINEL_OPEN}# judgment x is better than y minus "
                f"# judgment y is better than x{SENTINEL_CLOSE}"
            ),
            font_size=8.88,
            color=(0.0, 0.0, 0.0),
            should_translate=True,
            block_type="caption",
        )
        translated = (
            "total number of judgments 表12：系统间成对判断的聚合结果，"
            "其中单元格值为 # judgment x is better than y minus "
            "# judgment y is better than x"
        )

        self.assertFalse(_translation_retains_source_prose_run(block, translated))
        self.assertFalse(_translation_retains_foreign_prose(block, translated))
        self.assertTrue(
            _translation_retains_foreign_prose(
                block,
                translated + " where the value is",
            )
        )

    def test_parenthetical_english_gloss_is_not_flagged_as_untranslated(self):
        from pdf_zh_translator.pdf_layout import _translation_retains_source_prose_run

        block = TextBlock(
            page_index=7,
            bbox=(307.4, 666.7, 543.1, 713.4),
            text=(
                "We evaluate on a benchmark designed for offline goal-conditioned "
                "reinforcement learning that assesses long-horizon reasoning."
            ),
            font_size=9.0,
            color=(0.0, 0.0, 0.0),
            should_translate=True,
            block_type="body",
        )
        translated = (
            "本文在专为离线目标条件强化学习（Offline Goal-Conditioned "
            "Reinforcement Learning）设计的基准上进行评估。"
        )

        self.assertFalse(_translation_retains_source_prose_run(block, translated))

    def test_english_run_outside_gloss_is_still_flagged(self):
        from pdf_zh_translator.pdf_layout import _translation_retains_source_prose_run

        block = TextBlock(
            page_index=7,
            bbox=(307.4, 666.7, 543.1, 713.4),
            text=(
                "We evaluate offline goal-conditioned reinforcement learning "
                "across diverse and challenging tasks."
            ),
            font_size=9.0,
            color=(0.0, 0.0, 0.0),
            should_translate=True,
            block_type="body",
        )
        contaminated = (
            "本文开展评估。offline goal-conditioned reinforcement learning "
            "across diverse and challenging tasks."
        )

        self.assertTrue(_translation_retains_source_prose_run(block, contaminated))

    def test_short_academic_heading_echo_is_flagged(self):
        block = TextBlock(
            page_index=4,
            bbox=(55.4, 353.4, 164.9, 363.4),
            text="Step 1: Guided Sampling.",
            font_size=9.96,
            color=(0.0, 0.0, 0.0),
            bold=True,
            source_lines=1,
            should_translate=True,
            block_type="heading",
        )

        self.assertTrue(_translated_block_still_english(block, block.text))

    def test_url_footnote_echo_is_accepted(self):
        # word2vec p2: "1The test set is available at www..." churned through
        # the retry loop forever (the vendor echoes the scaffolding around
        # the link). Link footnotes accept the echo instead of retrying.
        block = TextBlock(
            page_index=1,
            bbox=(108.0, 690.0, 400.0, 700.0),
            text="1The test set is available at www.fit.vutbr.cz/~imikolov/rnnlm/word-test.v1.txt",
            font_size=8.0,
            color=(0.0, 0.0, 0.0),
            bold=False,
            source_lines=1,
            should_translate=True,
            block_type="body",
        )

        self.assertFalse(_translated_block_still_english(block, block.text))

    def test_formula_dense_echo_is_flagged(self):
        """IPMF p3: the restored echo of a formula-explanation block must be
        flagged even though it trips the reference/author exemptions."""
        from pdf_zh_translator.pdf_layout import _translated_block_still_english

        block = TextBlock(
            page_index=2,
            bbox=(108.0, 415.0, 504.0, 440.0),
            text=(
                "where Π_{N}(p_{0}, p_{1})⊂P_{2,ac}(R^{D×(N+2)}) is the subset of "
                "discrete stochastic processes with marginals q(x_{0}) = p_{0}(x_{0}). "
                "The objective function in (1) admits a decomposition into simpler terms."
            ),
            font_size=9.0,
            color=(0.0, 0.0, 0.0),
            should_translate=True,
            block_type="body",
        )
        echo = block.text
        self.assertTrue(_translated_block_still_english(block, echo))

    def test_genuine_translation_is_not_flagged(self):
        from pdf_zh_translator.pdf_layout import _translated_block_still_english

        block = TextBlock(
            page_index=2,
            bbox=(108.0, 415.0, 504.0, 440.0),
            text=(
                "where Π_{N}(p_{0}, p_{1}) is the subset of discrete stochastic "
                "processes with given marginals."
            ),
            font_size=9.0,
            color=(0.0, 0.0, 0.0),
            should_translate=True,
            block_type="body",
        )
        translated = "其中 Π_{N}(p_{0}, p_{1}) 是具有给定边缘分布的离散随机过程子集。"
        self.assertFalse(_translated_block_still_english(block, translated))

    def test_short_proper_noun_identity_is_not_flagged(self):
        """Blocks under five prose words may legitimately stay as-is (proper
        nouns); the echo check must not condemn them."""
        from pdf_zh_translator.pdf_layout import _translated_block_still_english

        block = TextBlock(
            page_index=0,
            bbox=(108.0, 100.0, 200.0, 112.0),
            text="StreamPETR",
            font_size=9.0,
            color=(0.0, 0.0, 0.0),
            should_translate=True,
            block_type="body",
        )
        self.assertFalse(_translated_block_still_english(block, "StreamPETR"))


class EquationTableProseSentenceTests(unittest.TestCase):
    def test_sentence_between_formula_rows_stays_translatable(self):
        """IPMF p3: 'This leads to the Static SB problem:' wedged above the
        rows of display equation (2) must be extracted as a translatable
        sentence, not condemned as a table cell; the formula rows themselves
        stay preserved."""
        from pdf_zh_translator.pdf_layout import (
            _equation_table_region_bboxes,
            _LineRec,
            _RawBlockRec,
            segments_from_record,
        )

        def line(text, y0, x0=108.0, x1=300.0):
            return _LineRec(
                text=text,
                bbox=(x0, y0, x1, y0 + 10.0),
                spans=[
                    {
                        "text": text,
                        "bbox": (x0, y0, x1, y0 + 10.0),
                        "size": 9.0,
                        "flags": 4,
                        "color": 0,
                    }
                ],
            )

        lines = [
            line("This leads to the Static SB problem:", 513.0),
            line("min", 528.0, 108.0, 130.0),
            line("_{q}_{∈}Π(_{p}_{0}_{,p}_{1}_{)}KL", 528.0, 200.0, 330.0),
            line("q(x_{0}, x_{1})||p^{W}^{ϵ}(x_{0}, x_{1})", 552.0, 108.0, 300.0),
            line("(2)", 552.0, 480.0, 504.0),
        ]
        record = _RawBlockRec(lines=lines)

        segments = segments_from_record(0, record, equation_record=True)
        bodies = [s for s in segments if s.block_type == "body"]
        self.assertEqual(len(bodies), 1)
        self.assertIn("Static SB problem", bodies[0].text)

        from pdf_zh_translator.pdf_layout import record_is_table

        self.assertTrue(record_is_table(record))
        regions = _equation_table_region_bboxes([record], [True])
        self.assertNotIn(lines[0].bbox, regions)
        self.assertIn(lines[1].bbox, regions)


class CaptionBandGlueScopeTests(unittest.TestCase):
    def test_table_header_under_caption_extension_is_kept(self):
        """MCF p9: a 3-line wrapped translated caption extends its band into
        the table header zone; header cells overlapping the caption line
        horizontally are content UNDER it, not caption fragments."""
        from pdf_zh_translator.pdf_layout import (
            _entries_outside_caption_bands,
            _extend_caption_bands_for_translated,
        )

        band = (155.1, 89.0, 453.9, 103.0)
        entries = [
            ((184.1, 89.7, 237.4, 102.9), "表2：中小型"),
            ((163.5, 100.5, 257.9, 113.7), "NETGEN数据集上最小"),
            ((273.9, 111.3, 338.1, 124.5), "费用流的评估。"),
            ((170.9, 106.5, 197.7, 113.7), "Methods"),
            ((263.0, 102.3, 294.0, 110.3), "100 × 100"),
            ((259.0, 110.0, 269.0, 118.0), "obj"),
        ]
        extended = _extend_caption_bands_for_translated([band], entries)
        kept_texts = [
            text
            for _, text in _entries_outside_caption_bands(
                entries, extended, base_bboxes=[band]
            )
        ]
        self.assertIn("Methods", kept_texts)
        self.assertIn("100 × 100", kept_texts)
        self.assertIn("obj", kept_texts)
        self.assertNotIn("表2：中小型", kept_texts)

    def test_inline_caption_continuation_is_dropped(self):
        """A fragment starting right after the CJK line's right edge is an
        inline continuation of the caption and stays excluded."""
        from pdf_zh_translator.pdf_layout import (
            _entries_outside_caption_bands,
            _extend_caption_bands_for_translated,
        )

        band = (360.0, 620.0, 506.0, 648.0)
        entries = [
            ((363.0, 648.0, 383.0, 657.0), "分辨率为64"),
            ((385.0, 648.0, 403.0, 657.0), "×64."),
        ]
        extended = _extend_caption_bands_for_translated([band], entries)
        kept = _entries_outside_caption_bands(entries, extended, base_bboxes=[band])
        self.assertEqual(kept, [])


class DisplayEquationRowTests(unittest.TestCase):
    def test_underbrace_annotation_stays_inside_formula(self):
        def formula_span(text, bbox):
            return {
                "text": text,
                "bbox": bbox,
                "size": 10.0,
                "font": "CMEX10",
                "flags": 4,
                "color": 0,
            }
        label_span = {
            "text": "Network training target",
            "bbox": (327.7, 619.5, 393.0, 626.4),
            "size": 7.0,
            "font": "Times-Roman",
            "flags": 4,
            "color": 0,
        }
        record = _RawBlockRec(
            lines=[
                _LineRec(
                    f"{SENTINEL_OPEN}|{SENTINEL_CLOSE}",
                    (313.8, 615.6, 318.3, 625.6),
                    [formula_span("|", (313.8, 615.6, 318.3, 625.6))],
                    math_bboxes=[(313.8, 615.6, 318.3, 625.6)],
                    math_run_bboxes=[(313.8, 615.6, 318.3, 625.6)],
                ),
                _LineRec(
                    f"{SENTINEL_OPEN}{{z{SENTINEL_CLOSE}",
                    (355.9, 615.6, 364.8, 625.6),
                    [formula_span("{z", (355.9, 615.6, 364.8, 625.6))],
                    math_bboxes=[(355.9, 615.6, 364.8, 625.6)],
                    math_run_bboxes=[(355.9, 615.6, 364.8, 625.6)],
                ),
                _LineRec(
                    f"{SENTINEL_OPEN}}}{SENTINEL_CLOSE}",
                    (402.4, 615.6, 406.9, 625.6),
                    [formula_span("}", (402.4, 615.6, 406.9, 625.6))],
                    math_bboxes=[(402.4, 615.6, 406.9, 625.6)],
                    math_run_bboxes=[(402.4, 615.6, 406.9, 625.6)],
                ),
                _LineRec(
                    "Network training target",
                    (327.7, 619.5, 393.0, 626.4),
                    [label_span],
                    prose_bboxes=[(327.7, 619.5, 393.0, 626.4)],
                ),
            ]
        )

        segments = segments_from_record(3, record, equation_record=True)

        self.assertEqual(segments, [])


    def test_short_formula_explanation_is_still_translated(self):
        prose = _LineRec(
            "where the target is normalized",
            (250.0, 100.0, 390.0, 110.0),
            [
                {
                    "text": "where the target is normalized",
                    "bbox": (250.0, 100.0, 390.0, 110.0),
                    "size": 10.0,
                    "font": "Times-Roman",
                    "flags": 4,
                    "color": 0,
                }
            ],
            prose_bboxes=[(250.0, 100.0, 390.0, 110.0)],
        )
        formula = _LineRec(
            f"{SENTINEL_OPEN}x=1{SENTINEL_CLOSE}",
            (150.0, 100.0, 180.0, 110.0),
            [{"text": "x=1", "bbox": (150.0, 100.0, 180.0, 110.0), "size": 10.0}],
            math_bboxes=[(150.0, 100.0, 180.0, 110.0)],
            math_run_bboxes=[(150.0, 100.0, 180.0, 110.0)],
        )

        segments = segments_from_record(
            0,
            _RawBlockRec(lines=[formula, prose]),
            equation_record=True,
        )

        self.assertTrue(any("target is normalized" in block.text for block in segments))

    def test_where_clause_on_numbered_equation_row_stays_preserved(self):
        """MCF p3 eq (1): the 'where U(a,b) = {...}' clause shares the visual
        row with 'min <C,P> −εH(P), (1)' (subscript towers make the math
        lines ~1.7x taller). Extracting it for reflow overprints the
        preserved formula."""
        from pdf_zh_translator.pdf_layout import (
            _LineRec,
            _RawBlockRec,
            segments_from_record,
        )

        def line(text, x0, x1, y0, y1):
            return _LineRec(
                text=text,
                bbox=(x0, y0, x1, y1),
                spans=[
                    {
                        "text": text,
                        "bbox": (x0, y0, x1, y1),
                        "size": 10.0,
                        "flags": 4,
                        "color": 0,
                    }
                ],
            )

        record = _RawBlockRec(
            lines=[
                line("min", 133.0, 149.0, 147.1, 157.1),
                line("_{P}_{∈}_{U}_{(}_{a}_{,}_{b}_{)} <C,P>−ϵH(P),", 123.0, 245.0, 147.0, 163.9),
                line("where", 257.0, 281.0, 147.3, 157.3),
                line("U(a,b) ={P∈R^{+}", 291.0, 374.0, 145.3, 159.6),
                line("_{mn}|P1_{n} =a,P^{⊤}1_{m} =b}", 368.0, 478.0, 145.2, 158.9),
                line("(1)", 493.0, 505.0, 147.3, 157.3),
            ]
        )

        segments = segments_from_record(0, record, equation_record=True)

        self.assertEqual(segments, [])


class ColumnStraddlingRecordTests(unittest.TestCase):
    @staticmethod
    def _prose_line(text, bbox):
        line = _line(text, bbox)
        line.prose_bboxes.append(bbox)
        return line

    def test_two_column_sample_record_splits_at_gutter(self):
        # InstructGPT appendix: one extraction block carries the left sample
        # column plus the right column's run-in header; keeping them together
        # concatenates both texts and the reflow overprints the right cell.
        from pdf_zh_translator.pdf_layout import _split_column_straddling_record

        prose_line = self._prose_line
        lines = [
            prose_line("GPT-3 175B completion:", (118.0, 102.0, 212.0, 111.0)),
            prose_line("InstructGPT 175B completion:", (308.1, 102.0, 427.0, 111.0)),
            prose_line("Écrivez une histoire au sujet d'un enfant", (118.0, 113.0, 296.6, 122.0)),
            prose_line("voudrait tout savoir sur les jeux", (118.0, 123.0, 296.6, 132.0)),
            prose_line("retrouve dans l'une de leurs histoires.", (118.0, 133.0, 252.0, 142.0)),
        ]
        pieces = _split_column_straddling_record(_RawBlockRec(lines=lines))

        self.assertEqual(len(pieces), 2)
        left, right = pieces
        self.assertEqual(len(left.lines), 4)
        self.assertEqual(len(right.lines), 1)
        self.assertTrue(all(line.bbox[2] <= 297.0 for line in left.lines))
        self.assertTrue(right.lines[0].text.startswith("InstructGPT"))

    def test_ordinary_paragraph_stays_whole(self):
        from pdf_zh_translator.pdf_layout import _split_column_straddling_record

        lines = [
            _line("A normal paragraph line that spans", (72.0, 100.0, 300.0, 110.0)),
            _line("the whole column width without any", (72.0, 112.0, 300.0, 122.0)),
            _line("gutter between its physical lines.", (72.0, 124.0, 260.0, 134.0)),
            _line("short tail.", (72.0, 136.0, 140.0, 146.0)),
        ]
        pieces = _split_column_straddling_record(_RawBlockRec(lines=lines))

        self.assertEqual(len(pieces), 1)
class ParagraphMergeFontSizeTests(unittest.TestCase):
    def test_formula_annotation_cannot_shrink_following_body_paragraph(self):
        from pdf_zh_translator.pdf_layout import merge_two_blocks

        annotation = TextBlock(
            page_index=3,
            bbox=(258.0, 180.0, 335.0, 187.0),
            text="Joint-embedding prediction",
            font_size=6.97,
            color=(0.0, 0.0, 0.0),
            source_lines=1,
            block_type="body",
            source_line_bboxes=((258.0, 180.0, 335.0, 187.0),),
        )
        paragraph = TextBlock(
            page_index=3,
            bbox=(108.0, 187.0, 504.0, 270.0),
            text=(
                "where sg is the stop-grad operator and the target is an "
                "exponential moving average over several time steps."
            ),
            font_size=9.96,
            color=(0.0, 0.0, 0.0),
            source_lines=7,
            block_type="body",
            source_line_bboxes=((108.0, 187.0, 504.0, 198.0),),
        )

        merged = merge_two_blocks(annotation, paragraph)

        self.assertAlmostEqual(merged.font_size, 9.96)

    def test_regular_one_line_paragraph_keeps_its_source_scale(self):
        from pdf_zh_translator.pdf_layout import merge_two_blocks

        first = TextBlock(
            page_index=0,
            bbox=(72.0, 80.0, 300.0, 92.0),
            text="A normal paragraph starts here and continues",
            font_size=9.0,
            color=(0.0, 0.0, 0.0),
            source_lines=1,
            block_type="body",
        )
        continuation = TextBlock(
            page_index=0,
            bbox=(72.0, 92.0, 300.0, 128.0),
            text="on the following physical lines without changing its role.",
            font_size=9.4,
            color=(0.0, 0.0, 0.0),
            source_lines=3,
            block_type="body",
        )

        merged = merge_two_blocks(first, continuation)

        self.assertAlmostEqual(merged.font_size, 9.0)

    def test_formula_paragraph_can_request_three_lines_of_cascade_space(self):
        from unittest.mock import patch

        from pdf_zh_translator.pdf_layout import (
            _cascade_required_height,
            build_font_pack,
        )

        block = TextBlock(
            page_index=0,
            bbox=(72.0, 100.0, 500.0, 134.0),
            text="A paragraph with protected math and a translated explanation.",
            font_size=10.0,
            color=(0.0, 0.0, 0.0),
            source_lines=3,
            block_type="body",
            source_math_bboxes=((220.0, 110.0, 340.0, 124.0),),
            keepout_bboxes=[(220.0, 110.0, 340.0, 124.0)],
        )

        def fits_at_required_height(*, block, **_kwargs):
            return block.bbox[3] - block.bbox[1] >= 65.0

        with (
            patch(
                "pdf_zh_translator.pdf_layout._sibling_group_item_height",
                return_value=None,
            ),
            patch(
                "pdf_zh_translator.pdf_layout.translated_text_fits",
                side_effect=fits_at_required_height,
            ),
        ):
            required = _cascade_required_height(
                block,
                "包含公式的中文解释需要在公式周围重新换行。",
                font_pack=build_font_pack(None, []),
                min_font_size=5.0,
                requested=10.0,
                margin=0.8,
            )

        self.assertIsNotNone(required)
        self.assertGreaterEqual(required, 65.0)

    def test_cascade_moves_heading_but_keeps_caption_fixed(self):
        from pdf_zh_translator.pdf_layout import _cascade_item_movable

        heading = TextBlock(
            page_index=0,
            bbox=(72.0, 200.0, 300.0, 216.0),
            text="3 Experiments",
            font_size=12.0,
            color=(0.0, 0.0, 0.0),
            source_lines=1,
            block_type="heading",
            preserve_position=True,
        )
        caption = TextBlock(
            page_index=0,
            bbox=(72.0, 240.0, 500.0, 260.0),
            text="Figure 1: Overview of the method.",
            font_size=9.0,
            color=(0.0, 0.0, 0.0),
            source_lines=1,
            block_type="caption",
            preserve_position=True,
        )

        self.assertTrue(_cascade_item_movable(heading))
        self.assertFalse(_cascade_item_movable(caption))

    def test_cascade_consumes_gaps_instead_of_shifting_whole_column(self):
        from pdf_zh_translator.pdf_layout import _cascade_follower_shifts

        def body(y0, y1, text="body"):
            return TextBlock(
                page_index=0,
                bbox=(72.0, y0, 500.0, y1),
                text=text,
                font_size=10.0,
                color=(0.0, 0.0, 0.0),
                source_lines=3,
                block_type="body",
            )

        items = [
            (body(100.0, 134.0, "target"), "目标段落"),
            (body(146.0, 200.0, "first follower"), "第一段"),
            (body(230.0, 280.0, "second follower"), "第二段"),
            (body(340.0, 390.0, "third follower"), "第三段"),
        ]

        shifts = _cascade_follower_shifts(items, 0, [1, 2, 3], 31.0)

        self.assertAlmostEqual(shifts[1], 20.8)
        self.assertNotIn(2, shifts)
        self.assertNotIn(3, shifts)
