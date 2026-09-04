"""Regressions from the Mistral 7B head-to-head (issue #2).

Four engine defects and two QA blind spots surfaced on one paper:

* a compact borderless result table was re-flowed as prose,
* a boxed system prompt was widened past its frame,
* a wrapped bullet lost its hanging indent,
* an unchanged ASCII title was re-set from the CJK font chain,
* ``inspect`` reported none of it.
"""

import json
import tempfile
import unittest
from pathlib import Path

import fitz

from pdf_zh_translator import page_inspector as inspector
from pdf_zh_translator.pdf_layout import (
    FontPack,
    TextBlock,
    _expand_multiline_block_bbox,
    _expand_single_line_body_bbox,
    _expand_standalone_heading_to_column,
    _frame_right_limit,
    _LineRec,
    _list_hanging_indent,
    _page_frame_rule_bboxes,
    _RawBlockRec,
    _record_has_aligned_value_column_rows,
    _translation_repeats_source,
    build_font_pack,
    record_is_table,
)
from pdf_zh_translator.translators import CacheOnlyTranslator, TranslationError, cache_key


def _span(text, bbox, size=9.0):
    return {"text": text, "bbox": bbox, "size": size, "font": "NimbusRomNo9L-Regu", "flags": 4}


def _line(text, bbox):
    return _LineRec(text=text, bbox=bbox, spans=[_span(text, bbox)])


ZH_LEAD_IN = "\u89c4\u6a21\u4e0e\u6548\u7387\u3002"
ZH_BODY = "\u672c\u6587\u8ba1\u7b97\u4e86\u89c4\u6a21\u3002"


def _helv_pack() -> FontPack:
    font = fitz.Font("helv")
    return FontPack(
        regular=font,
        regular_file=Path(""),
        bold=font,
        bold_file=Path(""),
        regular_alias="helv",
        bold_alias="helv",
    )


class CompactValueTableTests(unittest.TestCase):
    """Mistral 7B Table 4: label | value rows with a 12pt column gap."""

    def _table4_body(self):
        return _RawBlockRec(
            lines=[
                _line("No system prompt", (367.0, 537.4, 433.3, 546.4)),
                _line("6.84 \u00b1 0.07", (454.6, 537.1, 497.6, 546.4)),
                _line("Llama 2 system prompt", (357.7, 547.4, 442.6, 556.4)),
                _line("6.38 \u00b1 0.07", (454.6, 547.1, 497.6, 556.4)),
                _line("Mistral system prompt", (359.5, 557.4, 440.7, 566.3)),
                _line("6.58 \u00b1 0.05", (454.6, 557.0, 497.6, 566.3)),
            ]
        )

    def test_centered_label_column_with_aligned_value_column_is_a_table(self):
        record = self._table4_body()
        self.assertTrue(_record_has_aligned_value_column_rows(record))
        self.assertTrue(record_is_table(record))

    def test_table_of_contents_page_numbers_are_not_a_table(self):
        record = _RawBlockRec(
            lines=[
                _line("1 Introduction", (108.0, 100.0, 180.0, 110.0)),
                _line("3", (500.0, 100.0, 505.0, 110.0)),
                _line("2 Related Work", (108.0, 112.0, 186.0, 122.0)),
                _line("5", (500.0, 112.0, 505.0, 122.0)),
                _line("3 Method", (108.0, 124.0, 152.0, 134.0)),
                _line("8", (500.0, 124.0, 505.0, 134.0)),
            ]
        )
        self.assertFalse(_record_has_aligned_value_column_rows(record))

    def test_numbered_equation_rows_are_not_a_table(self):
        record = _RawBlockRec(
            lines=[
                _line("x = y + 1", (200.0, 100.0, 260.0, 110.0)),
                _line("(4)", (490.0, 100.0, 505.0, 110.0)),
                _line("z = 2y", (208.0, 120.0, 252.0, 130.0)),
                _line("(5)", (490.0, 120.0, 505.0, 130.0)),
                _line("w = z - x", (201.0, 140.0, 259.0, 150.0)),
                _line("(6)", (490.0, 140.0, 505.0, 150.0)),
            ]
        )
        self.assertFalse(_record_has_aligned_value_column_rows(record))

    def test_misaligned_value_column_is_not_a_table(self):
        record = self._table4_body()
        record.lines[3] = _line("6.38 \u00b1 0.07", (470.0, 547.1, 513.0, 556.4))
        self.assertFalse(_record_has_aligned_value_column_rows(record))

    def test_sentence_rows_are_not_a_table(self):
        record = _RawBlockRec(
            lines=[
                _line("We evaluate the model carefully.", (108.0, 100.0, 260.0, 110.0)),
                _line("6.84 \u00b1 0.07", (454.6, 100.0, 497.6, 110.0)),
                _line("It performs well overall.", (108.0, 112.0, 240.0, 122.0)),
                _line("6.38 \u00b1 0.07", (454.6, 112.0, 497.6, 122.0)),
                _line("Results are stable.", (108.0, 124.0, 210.0, 134.0)),
                _line("6.58 \u00b1 0.05", (454.6, 124.0, 497.6, 134.0)),
            ]
        )
        self.assertFalse(_record_has_aligned_value_column_rows(record))


class FrameRuleTests(unittest.TestCase):
    """Boxed prompt: a two-line body must not be widened past its frame."""

    def _boxed_page(self):
        document = fitz.open()
        page = document.new_page(width=612, height=792)
        shape = page.new_shape()
        shape.draw_line((108.0, 483.0), (502.9, 483.0))
        shape.draw_line((108.0, 509.2), (502.9, 509.2))
        shape.draw_line((108.2, 483.1), (108.2, 509.2))
        shape.draw_line((502.7, 483.1), (502.7, 509.2))
        shape.finish(color=(0, 0, 0), width=0.4)
        shape.commit()
        return document, page

    def test_frame_rules_are_collected_from_hairlines(self):
        _document, page = self._boxed_page()
        horizontal, vertical = _page_frame_rule_bboxes(page)
        self.assertEqual(len(horizontal), 2)
        self.assertEqual(len(vertical), 2)
        limit = _frame_right_limit((111.0, 485.5, 500.7, 506.3), vertical, inset=1.8)
        self.assertIsNotNone(limit)
        self.assertLess(limit, 502.7)

    def test_two_line_body_that_still_wraps_keeps_its_source_box(self):
        block = TextBlock(
            page_index=0,
            bbox=(111.0, 485.5, 500.7, 506.3),
            text=(
                "Always assist with care, respect, and truth. Respond with utmost "
                "utility yet securely. Avoid harmful, unethical, prejudiced, or "
                "negative content. Ensure replies promote fairness and positivity."
            ),
            font_size=9.9,
            color=(0.0, 0.0, 0.0),
            source_lines=2,
            block_type="body",
        )
        translated = (
            "\u59cb\u7ec8\u4ee5\u5173\u6000\u3001\u5c0a\u91cd\u548c\u771f"
            "\u5b9e\u7684\u6001\u5ea6\u63d0\u4f9b\u5e2e\u52a9\u3002"
        ) * 4
        _document, page = self._boxed_page()
        frame_rules = _page_frame_rule_bboxes(page)

        expanded = _expand_single_line_body_bbox(
            block,
            translated,
            [block],
            build_font_pack(None, []),
            9.1,
            0.8,
            612.0,
            frame_rules=frame_rules,
        )

        self.assertEqual(expanded.bbox, block.bbox)

    def test_single_line_body_widening_stops_at_the_frame(self):
        block = TextBlock(
            page_index=0,
            bbox=(111.0, 488.0, 300.0, 500.0),
            text="Always assist with care and respect.",
            font_size=9.9,
            color=(0.0, 0.0, 0.0),
            source_lines=1,
            block_type="body",
        )
        translated = (
            "\u59cb\u7ec8\u4ee5\u5173\u6000\u3001\u5c0a\u91cd\u548c\u771f"
            "\u5b9e\u7684\u6001\u5ea6\u63d0\u4f9b\u5e2e\u52a9\uff0c\u5e76"
            "\u4ee5\u6700\u5927\u7684\u6548\u7528\u4e14\u5b89\u5168\u5730"
            "\u56de\u5e94\u6bcf\u4e00\u4e2a\u8bf7\u6c42\u3002"
        ) * 2
        _document, page = self._boxed_page()
        frame_rules = _page_frame_rule_bboxes(page)

        expanded = _expand_single_line_body_bbox(
            block,
            translated,
            [block],
            _helv_pack(),
            9.1,
            0.8,
            612.0,
            frame_rules=frame_rules,
        )

        self.assertLessEqual(expanded.bbox[2], 502.7 - 1.5 + 1e-6)

    def test_widening_never_runs_far_past_the_text_column(self):
        block = TextBlock(
            page_index=0,
            bbox=(108.0, 300.0, 320.0, 312.0),
            text="A short note before the display equation.",
            font_size=10.0,
            color=(0.0, 0.0, 0.0),
            source_lines=1,
            block_type="body",
        )
        translated = (
            "\u8fd9\u662f\u4e00\u6bb5\u5f88\u957f\u7684\u4e2d\u6587\u8bf4"
            "\u660e\u6587\u5b57\uff0c\u7528\u6765\u9a8c\u8bc1\u52a0\u5bbd"
            "\u4e0d\u4f1a\u4e00\u8def\u51b2\u5230\u9875\u9762\u8fb9\u7f18"
            "\u53bb\u3002"
        ) * 3

        expanded = _expand_single_line_body_bbox(
            block,
            translated,
            [block],
            _helv_pack(),
            9.2,
            0.8,
            612.0,
            page_columns=[(108.0, 396.0)],
        )

        self.assertLessEqual(expanded.bbox[2], 108.0 + 396.0 + 9.2 * 1.5 + 1e-6)

    def test_multiline_growth_stops_above_a_frame_rule(self):
        block = TextBlock(
            page_index=0,
            bbox=(111.0, 485.5, 500.7, 506.3),
            text="Always assist with care, respect, and truth. Respond with utmost utility.",
            font_size=9.9,
            color=(0.0, 0.0, 0.0),
            source_lines=2,
            block_type="body",
        )
        translated = (
            "\u59cb\u7ec8\u4ee5\u5173\u6000\u3001\u5c0a\u91cd\u548c\u771f"
            "\u5b9e\u7684\u6001\u5ea6\u63d0\u4f9b\u5e2e\u52a9\u3002\u4ee5"
            "\u6700\u5927\u7684\u6548\u7528\u4e14\u5b89\u5168\u5730\u56de"
            "\u5e94\u3002"
        ) * 3
        _document, page = self._boxed_page()
        horizontal, _vertical = _page_frame_rule_bboxes(page)

        expanded = _expand_multiline_block_bbox(
            block,
            translated,
            [block],
            build_font_pack(None, []),
            9.1,
            0.8,
            792.0,
            obstacles=horizontal,
        )

        bottom_rule_y = max(rule[1] for rule in horizontal)
        self.assertLessEqual(expanded.bbox[3], bottom_rule_y - 2.0 + 1e-6)
        self.assertGreater(expanded.bbox[3], block.bbox[3])


class HangingIndentTests(unittest.TestCase):
    def test_bullet_item_reports_its_source_continuation_indent(self):
        block = TextBlock(
            page_index=0,
            bbox=(109.5, 529.1, 505.2, 550.2),
            text="\u2022 Commonsense Reasoning (0-shot): Hellaswag, Winogrande, PIQA, SIQA",
            font_size=10.0,
            color=(0.0, 0.0, 0.0),
            source_lines=2,
            block_type="body",
            source_line_bboxes=(
                (109.5, 529.1, 505.2, 539.3),
                (118.0, 540.2, 420.9, 550.2),
            ),
        )
        self.assertAlmostEqual(_list_hanging_indent(block, 394.0), 8.5, places=3)

    def test_plain_paragraph_has_no_hanging_indent(self):
        block = TextBlock(
            page_index=0,
            bbox=(109.5, 529.1, 505.2, 550.2),
            text="Commonsense reasoning benchmarks include Hellaswag and Winogrande.",
            font_size=10.0,
            color=(0.0, 0.0, 0.0),
            source_lines=2,
            block_type="body",
            source_line_bboxes=(
                (109.5, 529.1, 505.2, 539.3),
                (118.0, 540.2, 420.9, 550.2),
            ),
        )
        self.assertEqual(_list_hanging_indent(block, 394.0), 0.0)

    def test_unreasonable_indent_is_ignored(self):
        block = TextBlock(
            page_index=0,
            bbox=(109.5, 529.1, 505.2, 550.2),
            text="\u2022 Item text that wraps onto a second physical line",
            font_size=10.0,
            color=(0.0, 0.0, 0.0),
            source_lines=2,
            block_type="body",
            source_line_bboxes=(
                (109.5, 529.1, 505.2, 539.3),
                (300.0, 540.2, 420.9, 550.2),
            ),
        )
        self.assertEqual(_list_hanging_indent(block, 394.0), 0.0)


class CenteredHeadingExpansionTests(unittest.TestCase):
    """DPO p1: the centred ``Abstract`` heading must stay centred as ``\u6458\u8981``."""

    def _heading(self):
        return TextBlock(
            page_index=0,
            bbox=(283.8, 285.9, 328.4, 302.1),
            text="Abstract",
            font_size=12.0,
            color=(0.0, 0.0, 0.0),
            block_type="heading",
            source_line_bboxes=((283.8, 285.9, 328.4, 302.1),),
        )

    def test_centered_heading_grows_symmetrically_around_its_source_center(self):
        expanded = _expand_standalone_heading_to_column(
            self._heading(),
            [(108.0, 396.0)],
            612.0,
            centered=True,
        )
        source_center = (283.8 + 328.4) / 2.0
        self.assertAlmostEqual(
            (expanded.bbox[0] + expanded.bbox[2]) / 2.0, source_center, places=3
        )
        self.assertGreater(expanded.bbox[2] - expanded.bbox[0], 200.0)
        self.assertGreaterEqual(expanded.bbox[0], 108.0)
        self.assertLessEqual(expanded.bbox[2], 504.0)

    def test_left_aligned_heading_still_grows_to_the_right(self):
        block = TextBlock(
            page_index=0,
            bbox=(108.0, 400.0, 190.0, 412.0),
            text="1 Introduction",
            font_size=12.0,
            color=(0.0, 0.0, 0.0),
            block_type="heading",
            source_line_bboxes=((108.0, 400.0, 190.0, 412.0),),
        )
        expanded = _expand_standalone_heading_to_column(block, [(108.0, 396.0)], 612.0)
        self.assertEqual(expanded.bbox[0], 108.0)
        self.assertGreater(expanded.bbox[2], 400.0)


def _bold_span(text, bbox, size=10.0):
    return {
        "text": text,
        "bbox": bbox,
        "size": size,
        "font": "NimbusRomNo9L-Medi",
        "flags": 20,
    }


class NumberedListBoldLeadInTests(unittest.TestCase):
    """Qwen-RobotWorld p4: ``1. Task Goal Layer\u2014...`` items sharing one PDF block."""

    def _record(self):
        def item(number, lead, tail, y):
            marker = "%d." % number
            marker_box = (70.9, y, 79.0, y + 10.0)
            lead_box = (79.0, y, 79.0 + 6.0 * len(lead), y + 10.0)
            tail_box = (lead_box[2], y, 525.0, y + 10.0)
            return _LineRec(
                text="%s %s%s" % (marker, lead, tail),
                bbox=(70.9, y, 525.0, y + 10.0),
                spans=[
                    _span(marker, marker_box),
                    _bold_span(" " + lead, lead_box),
                    _span(tail, tail_box),
                ],
            )

        def continuation(text, y):
            return _line(text, (83.4, y, 500.0, y + 10.0))

        return _RawBlockRec(
            lines=[
                item(1, "Task Goal Layer", "\u2014infer the high-level intent of the", 602.1),
                continuation("transition, integrating external instructions;", 613.4),
                item(
                    2,
                    "Action Detail Layer",
                    "\u2014decompose the action into trajectories",
                    627.1,
                ),
                continuation("and force, with explicit viewpoint information;", 638.3),
                item(
                    3,
                    "Physical Feedback Layer",
                    "\u2014describe the observable consequences",
                    662.8,
                ),
                continuation("of the action on the environment.", 674.2),
            ]
        )

    def test_sequential_numbered_openers_are_detected(self):
        from pdf_zh_translator.pdf_layout import _sequential_numbered_item_lines

        record = self._record()
        openers = _sequential_numbered_item_lines(record)
        self.assertEqual(len(openers), 3)
        self.assertEqual(
            {id(line) for line in record.lines[::2]},
            openers,
        )

    def test_lone_line_initial_number_is_not_a_list(self):
        from pdf_zh_translator.pdf_layout import _sequential_numbered_item_lines

        record = _RawBlockRec(
            lines=[
                _line("results are summarised in Table", (70.9, 100.0, 500.0, 110.0)),
                _line("1. Then we discuss the ablations in detail.", (70.9, 112.0, 500.0, 122.0)),
            ]
        )
        self.assertEqual(_sequential_numbered_item_lines(record), set())

    def test_items_split_into_bold_run_in_headings_and_bodies(self):
        from pdf_zh_translator.pdf_layout import segments_from_record, strip_sentinels

        segments = segments_from_record(0, self._record())
        headings = [s for s in segments if s.block_type == "run_in_heading"]
        bodies = [s for s in segments if s.block_type == "body"]
        self.assertEqual(len(headings), 3)
        self.assertEqual(len(bodies), 3)
        self.assertTrue(all(h.bold for h in headings))
        self.assertEqual(
            [strip_sentinels(h.text).strip() for h in headings],
            ["1. Task Goal Layer", "2. Action Detail Layer", "3. Physical Feedback Layer"],
        )

    def test_period_marker_counts_as_lead_in_marker(self):
        from pdf_zh_translator.pdf_layout import span_is_leadin_marker

        self.assertTrue(span_is_leadin_marker("1."))
        self.assertTrue(span_is_leadin_marker("12)"))
        self.assertFalse(span_is_leadin_marker("1.5"))
        self.assertFalse(span_is_leadin_marker("Fig."))

    def test_list_marker_stays_regular_inside_a_bold_prefix(self):
        from pdf_zh_translator.pdf_layout import apply_inline_bold, tokenize_text

        text = (
            "1. \u4efb\u52a1\u76ee\u6807\u5c42"
            "\u2014\u63a8\u65ad\u8f6c\u6362\u7684\u9ad8\u5c42\u610f\u56fe"
        )
        block = TextBlock(
            page_index=0,
            bbox=(70.9, 602.1, 525.0, 624.0),
            text="1. Task Goal Layer\u2014infer the high-level intent",
            font_size=10.0,
            color=(0.0, 0.0, 0.0),
            translated_bold_prefix_chars=len("1. \u4efb\u52a1\u76ee\u6807\u5c42"),
        )
        tokens = tokenize_text(text)
        apply_inline_bold(tokens, block, text)
        visible = [token for token in tokens if token.kind != "space"]
        self.assertEqual(visible[0].text, "1.")
        self.assertFalse(visible[0].bold)
        lead_in = visible[1:6]
        self.assertEqual("".join(token.text for token in lead_in), "\u4efb\u52a1\u76ee\u6807\u5c42")
        self.assertTrue(all(token.bold for token in lead_in))
        self.assertFalse(any(token.bold for token in visible[6:]))


class RowGridPromotionTests(unittest.TestCase):
    """ResNet p6 Table 3: one PDF block per row, merged into a paragraph block."""

    def _grid_block(
        self,
        rows,
        x_cols=((96.4, 150.0), (170.2, 190.4), (217.6, 233.3)),
        y0=74.8,
        text=None,
    ):
        boxes = []
        for index in range(rows):
            y = y0 + index * 12.0
            for x_start, x_end in x_cols:
                boxes.append((x_start, y, x_end, y + 8.0))
        return TextBlock(
            page_index=0,
            bbox=(96.4, y0, 240.0, y0 + rows * 12.0),
            text=text or " ".join("VGG-%d [1] 28.0%d 9.3%d" % (i, i, i) for i in range(rows)),
            font_size=8.0,
            color=(0.0, 0.0, 0.0),
            source_lines=rows * len(x_cols),
            block_type="body",
            source_line_bboxes=tuple(boxes),
        )

    def test_three_column_result_rows_become_a_table(self):
        from pdf_zh_translator.pdf_layout import _promote_row_grid_blocks

        block = self._grid_block(4)
        _promote_row_grid_blocks([block])
        self.assertEqual(block.block_type, "table")
        self.assertFalse(block.should_translate)
        self.assertTrue(block.nowrap)

    def test_adjacent_single_row_blocks_are_stacked(self):
        from pdf_zh_translator.pdf_layout import _promote_row_grid_blocks

        rows = [
            self._grid_block(1, y0=74.8 + i * 12.0, text="ResNet-%d 24.%d 7.%d" % (i, i, i))
            for i in range(3)
        ]
        _promote_row_grid_blocks(rows)
        self.assertTrue(all(block.block_type == "table" for block in rows))

    def test_bulleted_list_with_separate_bullet_boxes_stays_prose(self):
        from pdf_zh_translator.pdf_layout import _promote_row_grid_blocks

        block = self._grid_block(
            4,
            x_cols=((109.5, 113.0), (120.0, 500.0)),
            text=(
                "\u2022 We evaluate the model on commonsense reasoning benchmarks. "
                "\u2022 We also report world knowledge results on two datasets. "
                "\u2022 Reading comprehension uses the standard evaluation. "
                "\u2022 Code generation follows the usual protocol."
            ),
        )
        _promote_row_grid_blocks([block])
        self.assertEqual(block.block_type, "body")

    def test_two_column_definition_rows_without_numbers_stay_prose(self):
        from pdf_zh_translator.pdf_layout import _promote_row_grid_blocks

        block = self._grid_block(
            3,
            x_cols=((96.4, 140.0), (150.0, 240.0)),
            text="policy the agent behaviour reward the scalar signal value the expected return",
        )
        _promote_row_grid_blocks([block])
        self.assertEqual(block.block_type, "body")

    def test_plain_paragraph_lines_are_not_a_grid(self):
        from pdf_zh_translator.pdf_layout import _promote_row_grid_blocks

        block = self._grid_block(5, x_cols=((96.4, 240.0),), text="ordinary prose " * 20)
        _promote_row_grid_blocks([block])
        self.assertEqual(block.block_type, "body")


class VerbatimTranslationTests(unittest.TestCase):
    def _block(self, text):
        return TextBlock(
            page_index=0,
            bbox=(266.6, 99.8, 345.4, 117.0),
            text=text,
            font_size=17.2,
            color=(0.0, 0.0, 0.0),
        )

    def test_unchanged_ascii_title_is_kept_in_source_typesetting(self):
        self.assertTrue(_translation_repeats_source(self._block("Mistral 7B"), "Mistral 7B"))

    def test_wrapped_source_matches_single_line_answer(self):
        self.assertTrue(
            _translation_repeats_source(
                self._block("https://github.com/\nmistralai/mistral-src"),
                "https://github.com/mistralai/mistral-src",
            )
        )

    def test_translated_text_is_not_verbatim(self):
        self.assertFalse(_translation_repeats_source(self._block("Abstract"), "\u6458\u8981"))

    def test_partial_change_is_not_verbatim(self):
        self.assertFalse(_translation_repeats_source(self._block("Mistral 7B"), "Mistral 7B."))


class SegmentedCacheReplayTests(unittest.TestCase):
    """A live run caches bold lead-in and body separately; replay must too."""

    def _cache_file(self, directory):
        cache_file = Path(directory) / "cache.jsonl"
        lead_in, body = "Size and Efficiency.", "We computed sizes."
        records = [
            {"key": cache_key(lead_in), "source": lead_in, "translation": ZH_LEAD_IN},
            {"key": cache_key(body), "source": body, "translation": ZH_BODY},
        ]
        cache_file.write_text(
            "\n".join(json.dumps(record, ensure_ascii=False) for record in records) + "\n",
            encoding="utf-8",
        )
        return cache_file

    def test_segmented_replay_serves_the_live_cache_entries(self):
        with tempfile.TemporaryDirectory() as directory:
            translator = CacheOnlyTranslator(
                self._cache_file(directory), segment_source_styles=True
            )
            self.assertTrue(translator.supports_source_style_segments)
            self.assertEqual(
                translator.translate_batch(["Size and Efficiency.", "We computed sizes."]),
                [ZH_LEAD_IN, ZH_BODY],
            )

    def test_default_replay_keeps_whole_block_lookup(self):
        with tempfile.TemporaryDirectory() as directory:
            translator = CacheOnlyTranslator(self._cache_file(directory))
            self.assertFalse(translator.supports_source_style_segments)
            with self.assertRaises(TranslationError):
                translator.translate_batch(["Size and Efficiency. We computed sizes."])


def _table_source_page(document):
    page = document.new_page(width=612, height=792)
    shape = page.new_shape()
    for y in (518.8, 534.1, 569.4):
        shape.draw_line((357.7, y), (497.6, y))
    shape.finish(color=(0, 0, 0), width=0.4)
    shape.commit()
    rows = [
        ("Guardrails", "MT Bench", 530.0),
        ("No system prompt", "6.84 \u00b1 0.07", 545.0),
        ("Llama 2 system prompt", "6.38 \u00b1 0.07", 555.5),
        ("Mistral system prompt", "6.58 \u00b1 0.05", 565.0),
    ]
    for label, value, baseline in rows:
        page.insert_text((362.0, baseline), label, fontsize=9, fontname="helv")
        page.insert_text((455.0, baseline), value, fontsize=9, fontname="helv")
    return page


def _table_reflowed_page(document):
    page = document.new_page(width=612, height=792)
    shape = page.new_shape()
    for y in (518.8, 534.1, 569.4):
        shape.draw_line((357.7, y), (497.6, y))
    shape.finish(color=(0, 0, 0), width=0.4)
    shape.commit()
    lines = [
        "Guardrails MT Bench No system prompt",
        "6.84 \u00b1 0.07 Llama 2 system prompt",
        "6.38 \u00b1 0.07 Mistral system prompt",
        "6.58 \u00b1 0.05",
    ]
    for offset, text in enumerate(lines):
        page.insert_text((359.0, 530.0 + offset * 11.5), text, fontsize=8.5, fontname="helv")
    return page


def _boxed_source_page(document, text_right=498.0):
    page = document.new_page(width=612, height=792)
    shape = page.new_shape()
    shape.draw_rect(fitz.Rect(108.0, 483.0, 502.9, 509.2))
    shape.finish(color=(0, 0, 0), width=0.4)
    shape.commit()
    page.insert_text(
        (111.0, 495.0), "Always assist with care and respect.", fontsize=9.5, fontname="helv"
    )
    return page


def _boxed_overflow_page(document):
    page = document.new_page(width=612, height=792)
    shape = page.new_shape()
    shape.draw_rect(fitz.Rect(108.0, 483.0, 502.9, 509.2))
    shape.finish(color=(0, 0, 0), width=0.4)
    shape.commit()
    page.insert_text(
        (111.0, 495.0),
        "A very long refilled line that keeps going well past the right edge of the frame it "
        "lives in, and then keeps going some more so that it clearly escapes the box",
        fontsize=9.5,
        fontname="helv",
    )
    return page


class DiagramClusterTests(unittest.TestCase):
    """ResNet Figure 3: a column of small labelled boxes joined by arrows."""

    def _diagram_page(self, boxes=12, with_lines=True, prose=False):
        document = fitz.open()
        page = document.new_page(width=612, height=792)
        shape = page.new_shape()
        for index in range(boxes):
            y = 100.0 + index * 24.0
            shape.draw_rect(fitz.Rect(150.0, y, 198.0, y + 7.0))
            if with_lines and index:
                shape.draw_line((174.0, y - 17.0), (174.0, y))
        shape.finish(color=(0.5, 0.5, 0.5), fill=(0.95, 0.9, 0.85), width=0.5)
        shape.commit()
        for index in range(boxes):
            y = 100.0 + index * 24.0
            label = (
                "this is a long explanatory sentence with many words inside the box"
                if prose
                else "3x3 conv, 64"
            )
            page.insert_text((152.0, y + 5.5), label, fontsize=4.5, fontname="helv")
        return document, page

    def test_labelled_box_column_with_connectors_is_a_graphic_region(self):
        from pdf_zh_translator.pdf_layout import _diagram_cluster_regions

        _document, page = self._diagram_page()
        regions = _diagram_cluster_regions(page)
        self.assertEqual(len(regions), 1)
        x0, y0, x1, y1 = regions[0]
        self.assertLessEqual(x0, 150.0)
        self.assertGreaterEqual(x1, 198.0)
        self.assertLessEqual(y0, 100.0)
        self.assertGreaterEqual(y1, 100.0 + 11 * 24.0 + 7.0)

    def test_too_few_boxes_are_not_a_diagram(self):
        from pdf_zh_translator.pdf_layout import _diagram_cluster_regions

        _document, page = self._diagram_page(boxes=5)
        self.assertEqual(_diagram_cluster_regions(page), [])

    def test_boxes_without_connectors_are_not_a_diagram(self):
        from pdf_zh_translator.pdf_layout import _diagram_cluster_regions

        _document, page = self._diagram_page(with_lines=False)
        self.assertEqual(_diagram_cluster_regions(page), [])

    def test_prose_inside_the_cluster_vetoes_it(self):
        from pdf_zh_translator.pdf_layout import _diagram_cluster_regions

        _document, page = self._diagram_page(prose=True)
        self.assertEqual(_diagram_cluster_regions(page), [])


class ReferenceHeaderExclusionTests(unittest.TestCase):
    """ViT p10-12: a venue+year running header is not a tampered reference."""

    HEADER = "Published as a conference paper at ICLR 2021"
    ENTRIES = [
        "Ashish Vaswani, Noam Shazeer, and Niki Parmar. Attention is all you need. "
        "In NeurIPS, 2017.",
        "Kaiming He, Xiangyu Zhang, and Jian Sun. Deep residual learning. In CVPR, 2016.",
        "Jacob Devlin, Ming-Wei Chang, and Kenton Lee. BERT pre-training. In NAACL, 2019.",
    ]

    def _pages(self, translated_header):
        source_doc, translated_doc = fitz.open(), fitz.open()
        pages = []
        for doc, header in ((source_doc, self.HEADER), (translated_doc, translated_header)):
            page = doc.new_page(width=612, height=792)
            font = "china-s" if any(ord(c) > 127 for c in header) else "helv"
            page.insert_text((108.0, 35.0), header, fontsize=9, fontname=font)
            for index, entry in enumerate(self.ENTRIES):
                page.insert_text(
                    (108.0, 120.0 + index * 14.0), entry, fontsize=9, fontname="helv"
                )
            pages.append(page)
        return pages

    def test_translated_running_header_is_excluded_by_role(self):
        header_zh = "\u53d1\u8868\u4e8e ICLR 2021 \u4f1a\u8bae\u8bba\u6587" * 3
        source, translated = self._pages(header_zh)
        header_block = TextBlock(
            page_index=9,
            bbox=(108.0, 27.8, 293.1, 37.8),
            text=self.HEADER,
            font_size=9.0,
            color=(0.0, 0.0, 0.0),
            block_type="header",
            preserve_position=True,
            nowrap=True,
        )
        issues = inspector._reference_issues(
            source,
            translated,
            10,
            0.0,
            source_role_blocks=[header_block],
            reference_regions=[(0.0, 0.0, 612.0, 792.0)],
        )
        codes = [i.code for i in issues if i.code == "reference_content_changed"]
        self.assertEqual(codes, [])

    def test_translated_entry_inside_references_is_still_reported(self):
        source, translated = self._pages(self.HEADER)
        translated.insert_text(
            (108.0, 160.0),
            ("\u4ed6\u4eec\u63d0\u51fa\u4e86\u4e00\u79cd\u65b0\u7684"
             "\u6ce8\u610f\u529b\u673a\u5236") * 2,
            fontsize=9,
            fontname="china-s",
        )
        issues = inspector._reference_issues(
            source,
            translated,
            10,
            0.0,
            source_role_blocks=[],
            reference_regions=[(0.0, 0.0, 612.0, 792.0)],
        )
        self.assertEqual([i.code for i in issues], ["reference_content_changed"])


class InspectorBlindSpotTests(unittest.TestCase):
    def test_reflowed_table_cells_are_reported(self):
        source_doc, translated_doc = fitz.open(), fitz.open()
        source = _table_source_page(source_doc)
        reflowed = _table_reflowed_page(translated_doc)
        rules, _ = inspector._page_line_art(source)

        issues = inspector._table_cell_layout_issues(1, source, reflowed, rules)

        self.assertEqual([issue.code for issue in issues], ["table_cells_reflowed"])
        self.assertEqual(issues[0].severity, "error")

    def test_faithful_table_is_silent(self):
        source_doc, translated_doc = fitz.open(), fitz.open()
        source = _table_source_page(source_doc)
        faithful = _table_source_page(translated_doc)
        rules, _ = inspector._page_line_art(source)

        self.assertEqual(inspector._table_cell_layout_issues(1, source, faithful, rules), [])

    def test_preserved_table_region_is_left_to_the_ink_check(self):
        source_doc, translated_doc = fitz.open(), fitz.open()
        source = _table_source_page(source_doc)
        reflowed = _table_reflowed_page(translated_doc)
        rules, _ = inspector._page_line_art(source)

        issues = inspector._table_cell_layout_issues(
            1,
            source,
            reflowed,
            rules,
            excluded_regions=[(357.7, 518.8, 497.6, 569.4)],
        )

        self.assertEqual(issues, [])

    def test_text_escaping_its_frame_is_reported(self):
        source_doc, translated_doc = fitz.open(), fitz.open()
        source = _boxed_source_page(source_doc)
        overflow = _boxed_overflow_page(translated_doc)

        issues = inspector._frame_overflow_issues(1, source, overflow)

        self.assertEqual([issue.code for issue in issues], ["text_outside_frame"])
        self.assertIn("right edge", issues[0].message)

    def test_text_inside_its_frame_is_silent(self):
        source_doc, translated_doc = fitz.open(), fitz.open()
        source = _boxed_source_page(source_doc)
        inside = _boxed_source_page(translated_doc)

        self.assertEqual(inspector._frame_overflow_issues(1, source, inside), [])


if __name__ == "__main__":
    unittest.main()
