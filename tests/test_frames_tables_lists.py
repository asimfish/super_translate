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
