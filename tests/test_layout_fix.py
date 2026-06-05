"""Tests for app.services.layout_fix module."""

import unittest

from app.services.layout_fix import (
    TextBlockInfo,
    _analyze_page_layout,
    _clean_text,
    _has_embedded_line_numbers,
    _is_line_number_text,
    _needs_fix,
)


class TestIsLineNumberText(unittest.TestCase):
    """Test detection of line number artifacts."""

    def test_single_line_number(self):
        self.assertTrue(_is_line_number_text("24"))
        self.assertTrue(_is_line_number_text("024"))
        self.assertTrue(_is_line_number_text("1"))

    def test_multiple_line_numbers(self):
        self.assertTrue(_is_line_number_text("24\n25\n26"))
        self.assertTrue(_is_line_number_text("1\n2\n3\n4\n5"))

    def test_line_numbers_with_spaces(self):
        self.assertTrue(_is_line_number_text(" 24 "))
        self.assertTrue(_is_line_number_text("\n24\n25\n"))

    def test_not_line_number(self):
        self.assertFalse(_is_line_number_text("Introduction"))
        self.assertFalse(_is_line_number_text("24 pages"))
        self.assertFalse(_is_line_number_text(""))
        self.assertFalse(_is_line_number_text("1 引言"))

    def test_four_digit_number(self):
        # 4 digits should NOT match (line numbers are 1-3 digits typically)
        self.assertFalse(_is_line_number_text("2024"))


class TestHasEmbeddedLineNumbers(unittest.TestCase):
    """Test detection of line numbers embedded within text."""

    def test_trailing_number_after_text(self):
        self.assertTrue(_has_embedded_line_numbers("这是正文内容24"))
        self.assertTrue(_has_embedded_line_numbers("这是正文内容 24"))

    def test_standalone_number_first_line(self):
        self.assertTrue(_has_embedded_line_numbers("25\n这是正文内容"))

    def test_number_after_punctuation(self):
        self.assertTrue(_has_embedded_line_numbers("。35"))

    def test_citation_not_flagged(self):
        self.assertFalse(_has_embedded_line_numbers("[26, 27, 28, 25]"))
        self.assertFalse(_has_embedded_line_numbers("如文献 [1, 2, 3] 所述"))

    def test_section_header_not_flagged(self):
        self.assertFalse(_has_embedded_line_numbers("1 Introduction"))
        self.assertFalse(_has_embedded_line_numbers("2 Related Work"))

    def test_chinese_section_header_not_flagged(self):
        self.assertFalse(_has_embedded_line_numbers("1 引言"))
        self.assertFalse(_has_embedded_line_numbers("2 相关工作"))

    def test_normal_text_not_flagged(self):
        self.assertFalse(_has_embedded_line_numbers("这是正常的中文正文"))
        self.assertFalse(_has_embedded_line_numbers("This is normal English text"))

    def test_empty_text(self):
        self.assertFalse(_has_embedded_line_numbers(""))


class TestCleanText(unittest.TestCase):
    """Test line number removal from text."""

    def test_remove_standalone_line_numbers(self):
        self.assertEqual(_clean_text("24\n正文内容"), "正文内容")
        self.assertEqual(_clean_text("24\n25\n26\n正文内容"), "正文内容")

    def test_remove_trailing_numbers(self):
        self.assertEqual(_clean_text("正文内容24"), "正文内容")
        self.assertEqual(_clean_text("正文内容 24"), "正文内容")

    def test_preserve_section_numbers(self):
        self.assertEqual(_clean_text("1 引言"), "1 引言")
        self.assertEqual(_clean_text("2 相关工作"), "2 相关工作")

    def test_preserve_citations(self):
        text = "如文献 [1, 2, 3] 所述"
        self.assertEqual(_clean_text(text), text)

    def test_empty_text(self):
        self.assertEqual(_clean_text(""), "")

    def test_multiline_with_mixed(self):
        text = "24\n这是第一段正文\n25\n这是第二段正文"
        result = _clean_text(text)
        self.assertIn("这是第一段正文", result)
        self.assertIn("这是第二段正文", result)
        self.assertNotIn("24", result)
        self.assertNotIn("25", result)


class TestAnalyzePageLayout(unittest.TestCase):
    """Test page layout analysis."""

    def test_finds_dominant_left_margin(self):
        blocks = [
            TextBlockInfo(bbox=(91, 100, 504, 120), text="text1", avg_font_size=10, block_index=0),
            TextBlockInfo(bbox=(91, 130, 504, 150), text="text2", avg_font_size=10, block_index=1),
            TextBlockInfo(bbox=(91, 160, 504, 180), text="text3", avg_font_size=10, block_index=2),
            TextBlockInfo(bbox=(108, 190, 300, 210), text="narrow", avg_font_size=10, block_index=3),
        ]
        left_margin, col_width = _analyze_page_layout(blocks)
        self.assertAlmostEqual(left_margin, 91.0)
        self.assertAlmostEqual(col_width, 413.0)

    def test_skips_small_blocks(self):
        blocks = [
            TextBlockInfo(bbox=(91, 100, 504, 120), text="text", avg_font_size=10, block_index=0),
            TextBlockInfo(bbox=(50, 100, 60, 105), text="tiny", avg_font_size=5, block_index=1),
        ]
        left_margin, col_width = _analyze_page_layout(blocks)
        self.assertAlmostEqual(left_margin, 91.0)

    def test_empty_blocks(self):
        left_margin, col_width = _analyze_page_layout([])
        self.assertAlmostEqual(left_margin, 0.0)
        self.assertAlmostEqual(col_width, 0.0)


class TestNeedsFix(unittest.TestCase):
    """Test block fix detection."""

    def _make_block(self, bbox, text="text", font_size=10.0):
        return TextBlockInfo(bbox=bbox, text=text, avg_font_size=font_size, block_index=0)

    def test_normal_block_no_fix(self):
        # Normal block at correct position with full width
        block = self._make_block((91, 100, 504, 120))
        self.assertFalse(_needs_fix(block, left_margin=91, col_width=413))

    def test_misaligned_block_needs_fix(self):
        # Block at wrong x position
        block = self._make_block((150, 100, 300, 120))
        self.assertTrue(_needs_fix(block, left_margin=91, col_width=413))

    def test_narrow_block_needs_fix(self):
        # Very narrow block with substantial text
        block = self._make_block((91, 100, 114, 120), text="This is a paragraph of text")  # width=23
        self.assertTrue(_needs_fix(block, left_margin=91, col_width=413))

    def test_line_number_needs_fix(self):
        block = self._make_block((50, 100, 60, 120), text="24")
        self.assertTrue(_needs_fix(block, left_margin=91, col_width=413))

    def test_header_footer_skipped(self):
        # Block in top margin (header)
        block = self._make_block((91, 20, 504, 40))
        self.assertFalse(_needs_fix(block, left_margin=91, col_width=413, page_height=792))

        # Block in bottom margin (footer)
        block = self._make_block((91, 760, 504, 780))
        self.assertFalse(_needs_fix(block, left_margin=91, col_width=413, page_height=792))

    def test_small_font_needs_fix(self):
        block = self._make_block((91, 100, 504, 120), font_size=5.0)
        self.assertTrue(_needs_fix(block, left_margin=91, col_width=413))


if __name__ == "__main__":
    unittest.main()
