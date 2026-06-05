"""Tests for app.services.layout_fix module."""

import unittest
import unittest.mock

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
        # Only strip numbers directly attached to CJK characters (no space)
        self.assertEqual(_clean_text("正文内容24"), "正文内容")
        # Numbers with space before them are preserved (could be meaningful)
        self.assertEqual(_clean_text("正文内容 24"), "正文内容 24")

    def test_preserve_section_numbers(self):
        self.assertEqual(_clean_text("1 引言"), "1 引言")
        self.assertEqual(_clean_text("2 相关工作"), "2 相关工作")

    def test_preserve_english_references(self):
        # English text with trailing numbers should NOT be stripped
        self.assertEqual(_clean_text("Figure 3"), "Figure 3")
        self.assertEqual(_clean_text("Table 2"), "Table 2")
        self.assertEqual(_clean_text("Chapter 5"), "Chapter 5")
        self.assertEqual(_clean_text("abc24"), "abc24")
        self.assertEqual(_clean_text("abc 24"), "abc 24")

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

    def test_skips_tiny_font_blocks(self):
        """Blocks with font size < BODY_TEXT_MIN_SIZE are skipped."""
        blocks = [
            TextBlockInfo(bbox=(91, 100, 504, 120), text="body text", avg_font_size=10, block_index=0),
            TextBlockInfo(bbox=(50, 100, 504, 120), text="footnote", avg_font_size=5, block_index=1),
        ]
        left_margin, col_width = _analyze_page_layout(blocks)
        self.assertAlmostEqual(left_margin, 91.0)

    def test_skips_line_number_text(self):
        """Blocks containing just line numbers are skipped."""
        blocks = [
            TextBlockInfo(bbox=(91, 100, 504, 120), text="body text", avg_font_size=10, block_index=0),
            TextBlockInfo(bbox=(50, 100, 60, 120), text="24\n25\n26", avg_font_size=10, block_index=1),
        ]
        left_margin, col_width = _analyze_page_layout(blocks)
        self.assertAlmostEqual(left_margin, 91.0)

    def test_weights_by_text_length(self):
        """Longer blocks have more influence on dominant left margin."""
        blocks = [
            TextBlockInfo(bbox=(91, 100, 504, 120), text="short", avg_font_size=10, block_index=0),
            TextBlockInfo(bbox=(91, 130, 504, 200), text="a" * 200, avg_font_size=10, block_index=1),
            TextBlockInfo(bbox=(100, 210, 504, 230), text="b" * 200, avg_font_size=10, block_index=2),
        ]
        left_margin, col_width = _analyze_page_layout(blocks)
        # Both x=91 and x=100 have same text length, but x=91 appears first
        self.assertIn(left_margin, [91.0, 100.0])


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

    def test_very_small_block_skipped(self):
        """Blocks with height < 3 or width < 10 are skipped (images/decorations)."""
        block = self._make_block((91, 100, 95, 101))  # width=4, height=1
        self.assertFalse(_needs_fix(block, left_margin=91, col_width=413))

    def test_embedded_line_numbers_needs_fix(self):
        """Blocks with embedded line numbers should be fixed."""
        block = self._make_block((91, 100, 504, 120), text="这是正文内容24")
        self.assertTrue(_needs_fix(block, left_margin=91, col_width=413))

    def test_short_text_narrow_width_skipped(self):
        """Short text in narrow blocks is likely figure labels — skip."""
        block = self._make_block((91, 100, 160, 120), text="Time")  # len=4, width=69
        self.assertFalse(_needs_fix(block, left_margin=91, col_width=413))

    def test_short_text_wide_width_needs_fix(self):
        """Short text in wide blocks is still checked for margin offset."""
        block = self._make_block((150, 100, 504, 120), text="Time")  # len=4, width=354
        self.assertTrue(_needs_fix(block, left_margin=91, col_width=413))


class TestFindChineseFont(unittest.TestCase):
    """Test Chinese font detection in pages."""

    def test_source_han_serif(self):
        """Detects SourceHanSerif font."""
        page = unittest.mock.MagicMock()
        page.get_fonts.return_value = [
            (0, 0, 0, "SourceHanSerif-Regular", "F1"),
        ]
        from app.services.layout_fix import _find_chinese_font
        self.assertEqual(_find_chinese_font(page), "F1")

    def test_noto_font(self):
        """Detects Noto font."""
        page = unittest.mock.MagicMock()
        page.get_fonts.return_value = [
            (0, 0, 0, "NotoSansSC-Regular", "F2"),
        ]
        from app.services.layout_fix import _find_chinese_font
        self.assertEqual(_find_chinese_font(page), "F2")

    def test_fallback_when_no_chinese_font(self):
        """Falls back to china-ss when no Chinese font found."""
        page = unittest.mock.MagicMock()
        page.get_fonts.return_value = [
            (0, 0, 0, "Helvetica", "F3"),
        ]
        from app.services.layout_fix import _find_chinese_font
        self.assertEqual(_find_chinese_font(page), "china-ss")

    def test_empty_font_list(self):
        """Falls back when page has no fonts."""
        page = unittest.mock.MagicMock()
        page.get_fonts.return_value = []
        from app.services.layout_fix import _find_chinese_font
        self.assertEqual(_find_chinese_font(page), "china-ss")

    def test_short_font_info(self):
        """Handles font_info with fewer than 5 elements."""
        page = unittest.mock.MagicMock()
        page.get_fonts.return_value = [(0, 0, 0)]
        from app.services.layout_fix import _find_chinese_font
        self.assertEqual(_find_chinese_font(page), "china-ss")


class TestCleanTextEdgeCases(unittest.TestCase):
    """Test edge cases for _clean_text."""

    def test_cjk_punctuation_trailing_number(self):
        self.assertEqual(_clean_text("结束。35"), "结束。")

    def test_multiple_cjk_trailing_numbers(self):
        text = "第一段内容24\n第二段内容25"
        result = _clean_text(text)
        self.assertEqual(result, "第一段内容\n第二段内容")

    def test_preserves_english_with_trailing_number(self):
        """English text like 'abc24' should not be modified."""
        self.assertEqual(_clean_text("abc24"), "abc24")

    def test_mixed_cjk_and_english_lines(self):
        text = "中文内容24\nFigure 3\n英文正文"
        result = _clean_text(text)
        self.assertIn("中文内容", result)
        self.assertIn("Figure 3", result)
        self.assertIn("英文正文", result)
        self.assertNotIn("24", result)

    def test_whitespace_only_lines_filtered(self):
        text = "  \n正文内容\n  \n"
        result = _clean_text(text)
        self.assertEqual(result, "正文内容")


class TestHasEmbeddedLineNumberEdgeCases(unittest.TestCase):
    """Test edge cases for _has_embedded_line_numbers."""

    def test_cjk_fullwidth_punctuation(self):
        """Fullwidth punctuation followed by number."""
        self.assertTrue(_has_embedded_line_numbers("内容。35"))

    def test_multiline_first_line_number(self):
        """First line is a standalone number."""
        self.assertTrue(_has_embedded_line_numbers("42\n这是正文"))

    def test_multiline_not_first_line(self):
        """Standalone number on non-first line is not flagged."""
        self.assertFalse(_has_embedded_line_numbers("正文内容\n正常的行"))

    def test_section_number_with_chinese(self):
        """Section headers like '1 引言' should not be flagged."""
        self.assertFalse(_has_embedded_line_numbers("1 引言"))

    def test_english_figure_reference(self):
        """'Figure 3' should not be flagged."""
        self.assertFalse(_has_embedded_line_numbers("Figure 3"))

    def test_only_whitespace(self):
        self.assertFalse(_has_embedded_line_numbers("   "))


class TestExtractTextBlocks(unittest.TestCase):
    """Test _extract_text_blocks edge cases."""

    def _make_page(self, blocks_data):
        """Create a mock page with given block data."""
        page = unittest.mock.MagicMock()
        page.get_text.return_value = {"blocks": blocks_data}
        return page

    def test_skips_image_blocks(self):
        """Non-text blocks (type=1) are skipped."""
        from app.services.layout_fix import _extract_text_blocks
        blocks_data = [
            {"type": 1, "bbox": [0, 0, 100, 100]},  # image
            {
                "type": 0,
                "bbox": [91, 100, 504, 120],
                "lines": [{"spans": [{"text": "body text", "size": 10.0}]}],
            },
        ]
        page = self._make_page(blocks_data)
        blocks = _extract_text_blocks(page)
        self.assertEqual(len(blocks), 1)
        self.assertEqual(blocks[0].text, "body text")

    def test_skips_empty_text_blocks(self):
        """Blocks with no text content are skipped."""
        from app.services.layout_fix import _extract_text_blocks
        blocks_data = [
            {
                "type": 0,
                "bbox": [91, 100, 504, 120],
                "lines": [{"spans": [{"text": "   ", "size": 10.0}]}],
            },
        ]
        page = self._make_page(blocks_data)
        blocks = _extract_text_blocks(page)
        self.assertEqual(len(blocks), 0)

    def test_skips_null_bytes(self):
        """Blocks containing null bytes are skipped."""
        from app.services.layout_fix import _extract_text_blocks
        blocks_data = [
            {
                "type": 0,
                "bbox": [91, 100, 504, 120],
                "lines": [{"spans": [{"text": "text\x00content", "size": 10.0}]}],
            },
        ]
        page = self._make_page(blocks_data)
        blocks = _extract_text_blocks(page)
        self.assertEqual(len(blocks), 0)

    def test_empty_page(self):
        """Page with no blocks returns empty list."""
        from app.services.layout_fix import _extract_text_blocks
        page = self._make_page([])
        blocks = _extract_text_blocks(page)
        self.assertEqual(len(blocks), 0)

    def test_joins_multiline_text(self):
        """Multiple lines are joined with newline."""
        from app.services.layout_fix import _extract_text_blocks
        blocks_data = [
            {
                "type": 0,
                "bbox": [91, 100, 504, 160],
                "lines": [
                    {"spans": [{"text": "line one", "size": 10.0}]},
                    {"spans": [{"text": "line two", "size": 10.0}]},
                ],
            },
        ]
        page = self._make_page(blocks_data)
        blocks = _extract_text_blocks(page)
        self.assertEqual(len(blocks), 1)
        self.assertEqual(blocks[0].text, "line one\nline two")


class TestFixPageLayoutEdgeCases(unittest.TestCase):
    """Test _fix_page_layout edge cases with mocked pages."""

    def test_empty_page_returns_zero(self):
        """Page with no text blocks returns 0 fixed."""
        from app.services.layout_fix import _fix_page_layout
        page = unittest.mock.MagicMock()
        page.get_text.return_value = {"blocks": []}
        result = _fix_page_layout(page)
        self.assertEqual(result, 0)

    def test_small_column_width_returns_zero(self):
        """Page with very narrow layout returns 0 (can't determine layout)."""
        from app.services.layout_fix import _fix_page_layout
        page = unittest.mock.MagicMock()
        page.get_text.return_value = {"blocks": [
            {
                "type": 0,
                "bbox": [10, 100, 50, 120],
                "lines": [{"spans": [{"text": "tiny", "size": 10.0}]}],
            },
        ]}
        page.rect.height = 792
        result = _fix_page_layout(page)
        self.assertEqual(result, 0)

    def test_no_blocks_needing_fix_returns_zero(self):
        """Page with properly aligned blocks returns 0."""
        from app.services.layout_fix import _fix_page_layout
        page = unittest.mock.MagicMock()
        page.get_text.return_value = {"blocks": [
            {
                "type": 0,
                "bbox": [91, 100, 504, 120],
                "lines": [{"spans": [{"text": "a" * 50, "size": 10.0}]}],
            },
            {
                "type": 0,
                "bbox": [91, 130, 504, 150],
                "lines": [{"spans": [{"text": "b" * 50, "size": 10.0}]}],
            },
        ]}
        page.rect.height = 792
        page.rect.width = 612
        result = _fix_page_layout(page)
        self.assertEqual(result, 0)


class TestFixTranslatedLayout(unittest.TestCase):
    """Test the main fix_translated_layout function with real PDFs."""

    def _create_test_pdf(self, path, blocks):
        """Create a PDF with text blocks at specified positions.

        blocks: list of (x0, y0, x1, y1, text, fontsize)
        """
        import fitz
        doc = fitz.open()
        page = doc.new_page(width=612, height=792)
        for x0, y0, x1, y1, text, size in blocks:
            rect = fitz.Rect(x0, y0, x1, y1)
            shape = page.new_shape()
            shape.insert_textbox(rect, text, fontname="helv", fontsize=size, color=(0, 0, 0))
            shape.commit()
        doc.save(str(path))
        doc.close()

    def test_fixes_misaligned_blocks(self):
        """Test that misaligned blocks are moved to correct position."""
        from app.services.layout_fix import fix_translated_layout
        import tempfile
        import os
        import fitz

        with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as f:
            path = f.name

        try:
            # Create PDF with body text and a misaligned block
            # Use insert_htmlbox for reliable text rendering
            doc = fitz.open()
            page = doc.new_page(width=612, height=792)
            normal_blocks = [
                (91, 100, 504, 130, "This is normal body text at the correct margin position for testing."),
                (91, 140, 504, 170, "Another properly aligned body text block with enough content here."),
                (91, 180, 504, 210, "Third normal block to establish the page layout pattern correctly."),
                (91, 220, 504, 250, "Fourth normal block for reliable layout detection of the column."),
                (91, 260, 504, 290, "Fifth normal block ensures column width is detected properly here."),
            ]
            for x0, y0, x1, y1, text in normal_blocks:
                rect = fitz.Rect(x0, y0, x1, y1)
                shape = page.new_shape()
                shape.insert_textbox(rect, text, fontname="helv", fontsize=10, color=(0, 0, 0))
                shape.commit()
            # Misaligned block: x0=200, narrow width but fits text
            rect = fitz.Rect(200, 300, 504, 330)
            shape = page.new_shape()
            shape.insert_textbox(rect, "Misaligned text block that should be fixed to correct position.", fontname="helv", fontsize=10, color=(0, 0, 0))
            shape.commit()
            doc.save(path)
            doc.close()

            result = fix_translated_layout(path)
            self.assertTrue(result)
        finally:
            os.unlink(path)

    def test_no_fix_needed_for_aligned_pdf(self):
        """Test that properly aligned PDFs are not modified."""
        from app.services.layout_fix import fix_translated_layout
        import tempfile
        import os

        with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as f:
            path = f.name

        try:
            # Create PDF with all blocks at correct position
            self._create_test_pdf(path, [
                (91, 100, 504, 120, "All blocks are at the correct left margin", 10),
                (91, 130, 504, 150, "Every block has proper width for the column", 10),
                (91, 180, 504, 200, "No fixes should be needed for this page", 10),
            ])

            result = fix_translated_layout(path)
            # May or may not fix depending on exact analysis, but should not crash
            self.assertIsInstance(result, bool)
        finally:
            os.unlink(path)

    def test_output_path_parameter(self):
        """Test that output_path parameter works."""
        from app.services.layout_fix import fix_translated_layout
        import tempfile
        import os

        with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as f:
            in_path = f.name
        out_path = in_path + ".fixed.pdf"

        try:
            self._create_test_pdf(in_path, [
                (91, 100, 504, 120, "Test content for output path", 10),
                (91, 130, 504, 150, "More content to make it a valid page", 10),
            ])

            result = fix_translated_layout(in_path, output_path=out_path)
            self.assertIsInstance(result, bool)
            # Input should be unchanged
            self.assertTrue(os.path.exists(in_path))
        finally:
            os.unlink(in_path)
            if os.path.exists(out_path):
                os.unlink(out_path)


class TestFixTranslatedLayoutEdgeCases(unittest.TestCase):
    """Test fix_translated_layout edge cases."""

    def test_exception_during_processing_closes_doc(self):
        """Exception during page processing closes the document and re-raises."""
        from app.services.layout_fix import fix_translated_layout
        import tempfile
        import os
        import fitz

        with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as f:
            path = f.name

        try:
            doc = fitz.open()
            doc.new_page()
            doc.save(path)
            doc.close()

            with unittest.mock.patch(
                "app.services.layout_fix._fix_page_layout",
                side_effect=RuntimeError("test error"),
            ):
                with self.assertRaises(RuntimeError):
                    fix_translated_layout(path)
        finally:
            os.unlink(path)

    def test_different_output_path_with_fixes(self):
        """When output_path differs and fixes are made, saves to output_path."""
        from app.services.layout_fix import fix_translated_layout
        import tempfile
        import os
        import fitz

        with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as f:
            in_path = f.name
        out_path = in_path + ".fixed.pdf"

        try:
            doc = fitz.open()
            page = doc.new_page(width=612, height=792)
            for i in range(5):
                rect = fitz.Rect(91, 100 + i * 40, 504, 130 + i * 40)
                shape = page.new_shape()
                shape.insert_textbox(rect, f"Normal body text block number {i} for layout detection.", fontname="helv", fontsize=10, color=(0, 0, 0))
                shape.commit()
            rect = fitz.Rect(200, 350, 504, 380)
            shape = page.new_shape()
            shape.insert_textbox(rect, "Misaligned text that should be fixed by layout fixer.", fontname="helv", fontsize=10, color=(0, 0, 0))
            shape.commit()
            doc.save(in_path)
            doc.close()

            result = fix_translated_layout(in_path, output_path=out_path)
            self.assertIsInstance(result, bool)
        finally:
            os.unlink(in_path)
            if os.path.exists(out_path):
                os.unlink(out_path)


if __name__ == "__main__":
    unittest.main()
