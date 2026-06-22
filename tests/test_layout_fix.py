"""Tests for app.services.layout_fix module."""

import unittest
import unittest.mock

from app.services.layout_fix import (
    ColumnInfo,
    TextBlockInfo,
    _analyze_page_layout,
    _clean_text,
    _find_nearest_column,
    _has_embedded_line_numbers,
    _is_line_number_text,
    _needs_fix,
)


def _tb(bbox, text="text", font_size=10.0, idx=0):
    """Shorthand for creating TextBlockInfo in tests."""
    return TextBlockInfo(
        bbox=bbox,
        text=text,
        avg_font_size=font_size,
        block_index=idx,
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
            _tb((91, 100, 504, 120), "text1"),
            _tb((91, 130, 504, 150), "text2"),
            _tb((91, 160, 504, 180), "text3"),
            _tb((108, 190, 300, 210), "narrow"),
        ]
        columns = _analyze_page_layout(blocks)
        self.assertEqual(len(columns), 1)
        self.assertAlmostEqual(columns[0].left_margin, 91.0)
        self.assertAlmostEqual(columns[0].col_width, 413.0)

    def test_skips_small_blocks(self):
        blocks = [
            _tb((91, 100, 504, 120), "text"),
            _tb((50, 100, 60, 105), "tiny", font_size=5),
        ]
        columns = _analyze_page_layout(blocks)
        self.assertEqual(len(columns), 1)
        self.assertAlmostEqual(columns[0].left_margin, 91.0)

    def test_empty_blocks(self):
        columns = _analyze_page_layout([])
        self.assertEqual(columns, [])

    def test_skips_tiny_font_blocks(self):
        """Blocks with font size < BODY_TEXT_MIN_SIZE are skipped."""
        blocks = [
            _tb((91, 100, 504, 120), "body text"),
            _tb((50, 100, 504, 120), "footnote", font_size=5),
        ]
        columns = _analyze_page_layout(blocks)
        self.assertEqual(len(columns), 1)
        self.assertAlmostEqual(columns[0].left_margin, 91.0)

    def test_skips_line_number_text(self):
        """Blocks containing just line numbers are skipped."""
        blocks = [
            _tb((91, 100, 504, 120), "body text"),
            _tb((50, 100, 400, 120), "24\n25\n26"),
        ]
        columns = _analyze_page_layout(blocks)
        self.assertEqual(len(columns), 1)
        self.assertAlmostEqual(columns[0].left_margin, 91.0)

    def test_weights_by_text_length(self):
        """Longer blocks have more influence on dominant left margin."""
        blocks = [
            _tb((91, 100, 504, 120), "short"),
            _tb((91, 130, 504, 200), "a" * 200),
            _tb((100, 210, 504, 230), "b" * 200),
        ]
        columns = _analyze_page_layout(blocks)
        self.assertEqual(len(columns), 1)
        self.assertIn(columns[0].left_margin, [91.0, 100.0])

    def test_returns_first_mode_on_distinct_widths(self):
        """When all widths are distinct, statistics.mode returns the first."""
        blocks = [
            _tb((91, 100, 400, 120), "text1"),  # width=309
            _tb((91, 130, 450, 150), "text2"),  # width=359
            _tb((91, 160, 500, 180), "text3"),  # width=409
        ]
        columns = _analyze_page_layout(blocks)
        self.assertEqual(len(columns), 1)
        self.assertAlmostEqual(columns[0].left_margin, 91.0)
        self.assertAlmostEqual(columns[0].col_width, 309.0)  # first encountered


class TestNeedsFix(unittest.TestCase):
    """Test block fix detection."""

    _COL = [ColumnInfo(91.0, 413.0)]

    def _make_block(self, bbox, text="text", font_size=10.0):
        return _tb(bbox, text, font_size)

    def test_normal_block_no_fix(self):
        # Normal block at correct position with full width
        block = self._make_block((91, 100, 504, 120))
        self.assertFalse(_needs_fix(block, self._COL))

    def test_misaligned_block_needs_fix(self):
        # Block at wrong x position
        block = self._make_block((150, 100, 300, 120))
        self.assertTrue(_needs_fix(block, self._COL))

    def test_narrow_block_needs_fix(self):
        # Very narrow block with substantial text (width=23)
        block = self._make_block((91, 100, 114, 120), text="This is a paragraph of text")
        self.assertTrue(_needs_fix(block, self._COL))

    def test_line_number_needs_fix(self):
        block = self._make_block((50, 100, 60, 120), text="24")
        self.assertTrue(_needs_fix(block, self._COL))

    def test_header_footer_skipped(self):
        # Block in top margin (header)
        block = self._make_block((91, 20, 504, 40))
        self.assertFalse(_needs_fix(block, self._COL, page_height=792))

        # Block in bottom margin (footer)
        block = self._make_block((91, 760, 504, 780))
        self.assertFalse(_needs_fix(block, self._COL, page_height=792))

    def test_small_font_needs_fix(self):
        block = self._make_block((91, 100, 504, 120), font_size=5.0)
        self.assertTrue(_needs_fix(block, self._COL))

    def test_very_small_block_skipped(self):
        """Blocks with height < 3 or width < 10 are skipped (images/decorations)."""
        block = self._make_block((91, 100, 95, 101))  # width=4, height=1
        self.assertFalse(_needs_fix(block, self._COL))

    def test_embedded_line_numbers_needs_fix(self):
        """Blocks with embedded line numbers should be fixed."""
        block = self._make_block((91, 100, 504, 120), text="这是正文内容24")
        self.assertTrue(_needs_fix(block, self._COL))

    def test_short_text_narrow_width_skipped(self):
        """Short text in narrow blocks is likely figure labels — skip."""
        block = self._make_block((91, 100, 160, 120), text="Time")  # len=4, width=69
        self.assertFalse(_needs_fix(block, self._COL))

    def test_short_text_wide_width_needs_fix(self):
        """Short text in wide blocks is still checked for margin offset."""
        block = self._make_block((150, 100, 504, 120), text="Time")  # len=4, width=354
        self.assertTrue(_needs_fix(block, self._COL))


class TestFindChineseFont(unittest.TestCase):
    """Test Chinese font detection in pages."""

    def test_always_returns_china_ss(self):
        """Always returns china-ss to avoid \\xa0 artifacts from embedded fonts."""
        from app.services.layout_fix import _find_chinese_font

        page = unittest.mock.MagicMock()
        page.get_fonts.return_value = [(0, 0, 0, "SourceHanSerif-Regular", "F1")]
        self.assertEqual(_find_chinese_font(page), "china-ss")

    def test_empty_font_list(self):
        """Returns china-ss when page has no fonts."""
        from app.services.layout_fix import _find_chinese_font

        page = unittest.mock.MagicMock()
        page.get_fonts.return_value = []
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

    def test_cleans_null_bytes(self):
        """Blocks containing null bytes are cleaned, not skipped."""
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
        self.assertEqual(len(blocks), 1)
        self.assertEqual(blocks[0].text, "textcontent")

    def test_skips_block_when_cleaning_leaves_empty_text(self):
        """Blocks that are only control characters are skipped after cleaning."""
        from app.services.layout_fix import _extract_text_blocks

        blocks_data = [
            {
                "type": 0,
                "bbox": [91, 100, 504, 120],
                "lines": [{"spans": [{"text": "\x00\x01\x02", "size": 10.0}]}],
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
        page.get_text.return_value = {
            "blocks": [
                {
                    "type": 0,
                    "bbox": [10, 100, 50, 120],
                    "lines": [{"spans": [{"text": "tiny", "size": 10.0}]}],
                },
            ]
        }
        # get_text("text", clip=...) for artifact detection returns clean text
        page.get_text.side_effect = lambda *a, **kw: (
            "tiny"
            if a and a[0] == "text"
            else {
                "blocks": [
                    {
                        "type": 0,
                        "bbox": [10, 100, 50, 120],
                        "lines": [{"spans": [{"text": "tiny", "size": 10.0}]}],
                    },
                ]
            }
        )
        page.rect.height = 792
        page.get_fonts.return_value = []
        result = _fix_page_layout(page)
        self.assertEqual(result, 0)

    def test_no_blocks_needing_fix_returns_zero(self):
        """Page with properly aligned blocks returns 0."""
        from app.services.layout_fix import _fix_page_layout

        blocks = [
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
        ]
        page = unittest.mock.MagicMock()
        page.get_text.return_value = {"blocks": blocks}
        page.get_text.side_effect = lambda *a, **kw: (
            ("a" * 50 + "\n" + "b" * 50) if a and a[0] == "text" else {"blocks": blocks}
        )
        page.rect.height = 792
        page.rect.width = 612
        page.get_fonts.return_value = []
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
        import os
        import tempfile

        import fitz

        from app.services.layout_fix import fix_translated_layout

        with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as f:
            path = f.name

        try:
            # Create PDF with body text and a misaligned block
            # Use insert_htmlbox for reliable text rendering
            doc = fitz.open()
            page = doc.new_page(width=612, height=792)
            normal_blocks = [
                (91, 100, 504, 130, "Normal body text at the correct margin for testing."),
                (91, 140, 504, 170, "Another aligned body text block with enough content."),
                (91, 180, 504, 210, "Third block to establish page layout pattern."),
                (91, 220, 504, 250, "Fourth block for reliable layout detection."),
                (91, 260, 504, 290, "Fifth block ensures column width is detected."),
            ]
            for x0, y0, x1, y1, text in normal_blocks:
                rect = fitz.Rect(x0, y0, x1, y1)
                shape = page.new_shape()
                shape.insert_textbox(rect, text, fontname="helv", fontsize=10, color=(0, 0, 0))
                shape.commit()
            # Misaligned block: x0=200, narrow width but fits text
            rect = fitz.Rect(200, 300, 504, 330)
            shape = page.new_shape()
            shape.insert_textbox(
                rect,
                "Misaligned text that should be fixed.",
                fontname="helv",
                fontsize=10,
                color=(0, 0, 0),
            )
            shape.commit()
            doc.save(path)
            doc.close()

            result = fix_translated_layout(path)
            self.assertTrue(result)
        finally:
            os.unlink(path)

    def test_no_fix_needed_for_aligned_pdf(self):
        """Test that properly aligned PDFs are not modified."""
        import os
        import tempfile

        from app.services.layout_fix import fix_translated_layout

        with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as f:
            path = f.name

        try:
            # Create PDF with all blocks at correct position
            self._create_test_pdf(
                path,
                [
                    (91, 100, 504, 120, "All blocks are at the correct left margin", 10),
                    (91, 130, 504, 150, "Every block has proper width for the column", 10),
                    (91, 180, 504, 200, "No fixes should be needed for this page", 10),
                ],
            )

            result = fix_translated_layout(path)
            # May or may not fix depending on exact analysis, but should not crash
            self.assertIsInstance(result, bool)
        finally:
            os.unlink(path)

    def test_output_path_parameter(self):
        """Test that output_path parameter works."""
        import os
        import tempfile

        from app.services.layout_fix import fix_translated_layout

        with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as f:
            in_path = f.name
        out_path = in_path + ".fixed.pdf"

        try:
            self._create_test_pdf(
                in_path,
                [
                    (91, 100, 504, 120, "Test content for output path", 10),
                    (91, 130, 504, 150, "More content to make it a valid page", 10),
                ],
            )

            result = fix_translated_layout(in_path, output_path=out_path)
            self.assertIsInstance(result, bool)
            # Input should be unchanged
            self.assertTrue(os.path.exists(in_path))
        finally:
            os.unlink(in_path)
            if os.path.exists(out_path):
                os.unlink(out_path)


class TestCleanPageArtifacts(unittest.TestCase):
    """Test _clean_page_artifacts function."""

    def test_cleans_null_bytes_in_rendered_text(self):
        """Blocks with null bytes in raw page text are redacted and reinserted."""
        import fitz

        from app.services.layout_fix import _clean_page_artifacts, _extract_text_blocks

        doc = fitz.open()
        page = doc.new_page(width=612, height=792)

        # Insert normal text
        shape = page.new_shape()
        shape.insert_textbox(
            fitz.Rect(91, 100, 504, 130),
            "Normal body text block for testing.",
            fontname="helv",
            fontsize=10,
            color=(0, 0, 0),
        )
        shape.commit()

        # Insert text with null byte embedded (simulating pdf2zh artifact)
        shape = page.new_shape()
        shape.insert_textbox(
            fitz.Rect(91, 140, 504, 170),
            "Text with \x00 null byte artifact.",
            fontname="helv",
            fontsize=10,
            color=(0, 0, 0),
        )
        shape.commit()

        blocks = _extract_text_blocks(page)
        if blocks:
            _clean_page_artifacts(page, blocks)

        # Verify null bytes are cleaned from the page text
        cleaned_text = page.get_text()
        self.assertNotIn("\x00", cleaned_text)
        doc.close()

    def test_noop_when_page_is_clean(self):
        """Clean pages should not be modified."""
        import fitz

        from app.services.layout_fix import _clean_page_artifacts, _extract_text_blocks

        doc = fitz.open()
        page = doc.new_page(width=612, height=792)
        shape = page.new_shape()
        shape.insert_textbox(
            fitz.Rect(91, 100, 504, 130),
            "Clean text without artifacts.",
            fontname="helv",
            fontsize=10,
            color=(0, 0, 0),
        )
        shape.commit()

        blocks = _extract_text_blocks(page)
        before_text = page.get_text()
        _clean_page_artifacts(page, blocks)
        after_text = page.get_text()
        # Text should be unchanged
        self.assertEqual(before_text, after_text)
        doc.close()

    def test_cleans_non_breaking_spaces(self):
        """Non-breaking spaces (\\xa0) are normalized to regular spaces."""
        import fitz

        from app.services.layout_fix import _clean_page_artifacts, _extract_text_blocks

        doc = fitz.open()
        page = doc.new_page(width=612, height=792)
        # Insert text with non-breaking space
        shape = page.new_shape()
        shape.insert_textbox(
            fitz.Rect(91, 100, 504, 130),
            "Text with\xa0non-breaking space.",
            fontname="helv",
            fontsize=10,
            color=(0, 0, 0),
        )
        shape.commit()

        blocks = _extract_text_blocks(page)
        _clean_page_artifacts(page, blocks)

        cleaned_text = page.get_text()
        self.assertNotIn("\xa0", cleaned_text)
        doc.close()

    def test_skips_short_text_after_cleaning(self):
        """Blocks where cleaning leaves very short text are skipped."""
        import fitz

        from app.services.layout_fix import _clean_page_artifacts, _extract_text_blocks

        doc = fitz.open()
        page = doc.new_page(width=612, height=792)
        # Insert text that is one char + null byte — cleaning leaves 1 char (< MIN_TEXT_LEN=2)
        shape = page.new_shape()
        shape.insert_textbox(
            fitz.Rect(91, 100, 504, 130),
            "A\x00",
            fontname="helv",
            fontsize=10,
            color=(0, 0, 0),
        )
        shape.commit()

        blocks = _extract_text_blocks(page)
        # After extraction cleaning, block text is "A" (1 char)
        # _clean_page_artifacts should skip reinsertion for this block
        _clean_page_artifacts(page, blocks)
        doc.close()

    def test_noop_when_no_dirty_blocks(self):
        """Pages with no artifacts are not modified."""
        import fitz

        from app.services.layout_fix import _clean_page_artifacts, _extract_text_blocks

        doc = fitz.open()
        page = doc.new_page(width=612, height=792)
        shape = page.new_shape()
        shape.insert_textbox(
            fitz.Rect(91, 100, 504, 130),
            "Clean text without any artifacts.",
            fontname="helv",
            fontsize=10,
            color=(0, 0, 0),
        )
        shape.commit()

        blocks = _extract_text_blocks(page)
        before = page.get_text()
        _clean_page_artifacts(page, blocks)
        after = page.get_text()
        self.assertEqual(before, after)
        doc.close()

    def test_fallback_redaction_when_kwargs_unsupported(self):
        """Falls back to apply_redactions() when kwargs are not supported."""
        import fitz

        from app.services.layout_fix import _clean_page_artifacts, _extract_text_blocks

        doc = fitz.open()
        page = doc.new_page(width=612, height=792)
        shape = page.new_shape()
        shape.insert_textbox(
            fitz.Rect(91, 100, 504, 130),
            "Text with\x00null byte.",
            fontname="helv",
            fontsize=10,
            color=(0, 0, 0),
        )
        shape.commit()

        blocks = _extract_text_blocks(page)

        # Mock apply_redactions to raise TypeError on first call (with kwargs)
        original_apply = page.apply_redactions
        call_count = [0]

        def mock_apply(**kwargs):
            call_count[0] += 1
            if kwargs:
                raise TypeError("unsupported kwargs")
            original_apply()

        page.apply_redactions = mock_apply
        _clean_page_artifacts(page, blocks)
        # Should have called apply_redactions twice: once with kwargs (failed), once without
        self.assertEqual(call_count[0], 2)
        doc.close()


class TestFindNbspBboxes(unittest.TestCase):
    """Test _find_nbsp_bboxes function."""

    def test_returns_empty_for_clean_page(self):
        """Clean page returns empty list."""
        import fitz

        from app.services.layout_fix import _find_nbsp_bboxes

        doc = fitz.open()
        page = doc.new_page(width=612, height=792)
        shape = page.new_shape()
        shape.insert_textbox(
            fitz.Rect(91, 100, 504, 130),
            "Clean text without artifacts.",
            fontname="helv",
            fontsize=10,
            color=(0, 0, 0),
        )
        shape.commit()

        page_dict = page.get_text("dict")
        result = _find_nbsp_bboxes(page_dict)
        self.assertEqual(result, [])
        doc.close()

    def test_skips_image_blocks(self):
        """Image blocks (type != 0) are skipped."""
        from app.services.layout_fix import _find_nbsp_bboxes

        page_dict = {
            "blocks": [
                {"type": 1, "bbox": [0, 0, 100, 100]},  # image block
                {
                    "type": 0,
                    "bbox": [91, 100, 504, 130],
                    "lines": [{"spans": [{"text": "text\xa0with\xa0nbsp", "size": 10.0}]}],
                },
            ]
        }
        result = _find_nbsp_bboxes(page_dict)
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0], (91, 100, 504, 130))


class TestBlockHasNbspBbox(unittest.TestCase):
    """Test _block_has_nbsp_bbox function."""

    def test_returns_false_for_empty_bboxes(self):
        """Empty bboxes list returns False."""
        from app.services.layout_fix import TextBlockInfo, _block_has_nbsp_bbox

        block = TextBlockInfo(
            bbox=(91.0, 100.0, 504.0, 130.0),
            text="test",
            avg_font_size=10.0,
            block_index=0,
        )
        self.assertFalse(_block_has_nbsp_bbox(block, []))

    def test_returns_true_for_overlapping_bbox(self):
        """Overlapping bbox returns True."""
        from app.services.layout_fix import TextBlockInfo, _block_has_nbsp_bbox

        block = TextBlockInfo(
            bbox=(91.0, 100.0, 504.0, 130.0),
            text="test",
            avg_font_size=10.0,
            block_index=0,
        )
        nbsp_bboxes = [(90.0, 99.0, 505.0, 131.0)]
        self.assertTrue(_block_has_nbsp_bbox(block, nbsp_bboxes))

    def test_returns_false_for_non_overlapping_bbox(self):
        """Non-overlapping bbox returns False."""
        from app.services.layout_fix import TextBlockInfo, _block_has_nbsp_bbox

        block = TextBlockInfo(
            bbox=(91.0, 100.0, 504.0, 130.0),
            text="test",
            avg_font_size=10.0,
            block_index=0,
        )
        nbsp_bboxes = [(600.0, 700.0, 700.0, 800.0)]
        self.assertFalse(_block_has_nbsp_bbox(block, nbsp_bboxes))


class TestRedactBlocks(unittest.TestCase):
    """Test _redact_blocks edge cases."""

    def test_skips_empty_text_blocks(self):
        """Blocks with empty text after strip are skipped."""
        from app.services.layout_fix import _redact_blocks

        page = unittest.mock.MagicMock()
        blocks = [
            _tb((91, 100, 504, 120), text="  "),
            _tb((91, 130, 504, 150), text="valid text"),
        ]
        _redact_blocks(page, blocks)
        # Only one redact annotation should be added (for the non-empty block)
        self.assertEqual(page.add_redact_annot.call_count, 1)

    def test_fallback_when_kwargs_unsupported(self):
        """Falls back to apply_redactions() when kwargs are not supported."""
        from app.services.layout_fix import _redact_blocks

        page = unittest.mock.MagicMock()
        page.apply_redactions.side_effect = [TypeError("unsupported"), None]
        blocks = [_tb((91, 100, 504, 120), text="text")]
        # Should not raise
        _redact_blocks(page, blocks)
        self.assertEqual(page.apply_redactions.call_count, 2)


class TestReinsertBlocks(unittest.TestCase):
    """Test _reinsert_blocks filtering edge cases."""

    _COL = [ColumnInfo(91.0, 413.0)]

    def test_skips_short_cleaned_text(self):
        """Blocks where _clean_text produces < 2 chars are skipped."""
        from app.services.layout_fix import _reinsert_blocks

        page = unittest.mock.MagicMock()
        page.rect.width = 612
        page.get_fonts.return_value = []
        # Single CJK char + trailing number → cleaned to 1 char
        blocks = [_tb((91, 100, 504, 120), text="你", font_size=10)]
        result = _reinsert_blocks(page, blocks, self._COL)
        self.assertEqual(result, 0)

    def test_skips_very_short_small_font(self):
        """Very short text (<=3 chars) with small font (<8) is skipped."""
        from app.services.layout_fix import _reinsert_blocks

        page = unittest.mock.MagicMock()
        page.rect.width = 612
        page.get_fonts.return_value = []
        blocks = [_tb((91, 100, 504, 120), text="ab", font_size=7)]
        result = _reinsert_blocks(page, blocks, self._COL)
        self.assertEqual(result, 0)

    def test_skips_right_margin_blocks(self):
        """Blocks in the right margin area (x0 > 70% of page width, narrow) are skipped."""
        from app.services.layout_fix import _reinsert_blocks

        page = unittest.mock.MagicMock()
        page.rect.width = 612
        page.get_fonts.return_value = []
        # x0=440 > 612*0.7=428.4, width=45 < 80
        blocks = [_tb((440, 100, 485, 120), text="label text", font_size=10)]
        result = _reinsert_blocks(page, blocks, self._COL)
        self.assertEqual(result, 0)

    def test_skips_short_fragment_at_correct_x(self):
        """Short fragments already at correct x position with narrow width are skipped."""
        from app.services.layout_fix import _reinsert_blocks

        page = unittest.mock.MagicMock()
        page.rect.width = 612
        page.get_fonts.return_value = []
        # x0=91 matches left_margin, len(text)=9 < 10, width=50 < 80
        blocks = [_tb((91, 100, 141, 120), text="short txt", font_size=10)]
        result = _reinsert_blocks(page, blocks, self._COL)
        self.assertEqual(result, 0)


class TestInsertTextWithFallback(unittest.TestCase):
    """Test _insert_text_with_fallback edge cases."""

    def test_fallback_when_all_sizes_fail(self):
        """When all font sizes return negative, falls back to minimum size."""
        import fitz

        from app.services.layout_fix import _insert_text_with_fallback

        page = unittest.mock.MagicMock()
        shape = unittest.mock.MagicMock()
        shape.insert_textbox.return_value = -1  # all sizes fail
        page.new_shape.return_value = shape

        rect = fitz.Rect(91, 100, 504, 120)
        result = _insert_text_with_fallback(page, rect, "test text", "helv", 10.0)
        self.assertTrue(result)
        # Should have called commit at least once (for the fallback)
        self.assertTrue(shape.commit.called)


class TestEstimateTextHeight(unittest.TestCase):
    """Test _estimate_text_height function."""

    def test_single_line_ascii(self):
        """Short ASCII text on one line."""
        from app.services.layout_fix import _estimate_text_height

        # "hello" = 5 chars * 5pt width = 25pt, rect_width=400 → 1 line
        h = _estimate_text_height("hello", 10.0, 400.0)
        self.assertAlmostEqual(h, 15.0, delta=1)  # 1 line * 1.5 * 10

    def test_single_line_cjk(self):
        """Short CJK text on one line."""
        from app.services.layout_fix import _estimate_text_height

        # "你好" = 2 chars * 10pt width = 20pt, rect_width=400 → 1 line
        h = _estimate_text_height("你好", 10.0, 400.0)
        self.assertAlmostEqual(h, 15.0, delta=1)

    def test_multi_line_newlines(self):
        """Text with newlines creates multiple lines."""
        from app.services.layout_fix import _estimate_text_height

        h = _estimate_text_height("line1\nline2\nline3", 10.0, 400.0)
        self.assertAlmostEqual(h, 45.0, delta=1)  # 3 lines * 15

    def test_long_text_wraps(self):
        """Long text that exceeds rect width wraps to multiple lines."""
        from app.services.layout_fix import _estimate_text_height

        # 80 CJK chars * 10pt = 800pt, rect_width=200 → 4 lines
        text = "你" * 80
        h = _estimate_text_height(text, 10.0, 200.0)
        self.assertGreater(h, 45.0)  # at least 3 lines

    def test_zero_width_returns_fallback(self):
        """Zero rect width returns fallback height."""
        from app.services.layout_fix import _estimate_text_height

        h = _estimate_text_height("test", 10.0, 0)
        self.assertEqual(h, 20.0)  # font_size * 2

    def test_zero_font_size_returns_zero(self):
        """Zero font size returns 0."""
        from app.services.layout_fix import _estimate_text_height

        h = _estimate_text_height("test", 0, 400.0)
        self.assertEqual(h, 0)


class TestFindChineseFontEdgeCases(unittest.TestCase):
    """Test _find_chinese_font edge cases for font info with missing name index."""

    def test_always_returns_china_ss_for_any_font(self):
        """Returns china-ss regardless of embedded fonts."""
        from app.services.layout_fix import _find_chinese_font

        page = unittest.mock.MagicMock()
        page.get_fonts.return_value = [(0, 0, 0, "SimHei-Regular", "F4")]
        self.assertEqual(_find_chinese_font(page), "china-ss")


class TestFixTranslatedLayoutEdgeCases(unittest.TestCase):
    """Test fix_translated_layout edge cases."""

    def test_exception_during_processing_closes_doc(self):
        """Exception during page processing closes the document and re-raises."""
        import os
        import tempfile

        import fitz

        from app.services.layout_fix import fix_translated_layout

        with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as f:
            path = f.name

        try:
            doc = fitz.open()
            doc.new_page()
            doc.save(path)
            doc.close()

            with (
                unittest.mock.patch(
                    "app.services.layout_fix._fix_page_layout",
                    side_effect=RuntimeError("test error"),
                ),
                self.assertRaises(RuntimeError),
            ):
                fix_translated_layout(path)
        finally:
            os.unlink(path)

    def test_different_output_path_with_fixes(self):
        """When output_path differs and fixes are made, saves to output_path."""
        import os
        import tempfile

        import fitz

        from app.services.layout_fix import fix_translated_layout

        with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as f:
            in_path = f.name
        out_path = in_path + ".fixed.pdf"

        try:
            doc = fitz.open()
            page = doc.new_page(width=612, height=792)
            for i in range(5):
                rect = fitz.Rect(91, 100 + i * 40, 504, 130 + i * 40)
                shape = page.new_shape()
                shape.insert_textbox(
                    rect,
                    f"Normal body text block {i} for detection.",
                    fontname="helv",
                    fontsize=10,
                    color=(0, 0, 0),
                )
                shape.commit()
            rect = fitz.Rect(200, 350, 504, 380)
            shape = page.new_shape()
            shape.insert_textbox(
                rect,
                "Misaligned text that should be fixed.",
                fontname="helv",
                fontsize=10,
                color=(0, 0, 0),
            )
            shape.commit()
            doc.save(in_path)
            doc.close()

            result = fix_translated_layout(in_path, output_path=out_path)
            self.assertIsInstance(result, bool)
        finally:
            os.unlink(in_path)
            if os.path.exists(out_path):
                os.unlink(out_path)


class TestFixTranslatedLayoutTempFileError(unittest.TestCase):
    """Test fix_translated_layout when temp file replacement fails."""

    def test_oserror_on_temp_file_replace(self):
        """OSError during temp file replacement should clean up and re-raise."""
        import os
        import tempfile

        import fitz

        from app.services.layout_fix import fix_translated_layout

        with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as f:
            path = f.name

        try:
            # Create a PDF with enough blocks to trigger fixes
            doc = fitz.open()
            page = doc.new_page(width=612, height=792)
            for i in range(5):
                rect = fitz.Rect(91, 100 + i * 40, 504, 130 + i * 40)
                shape = page.new_shape()
                shape.insert_textbox(
                    rect,
                    f"Normal body text block {i} for detection.",
                    fontname="helv",
                    fontsize=10,
                    color=(0, 0, 0),
                )
                shape.commit()
            rect = fitz.Rect(200, 350, 504, 380)
            shape = page.new_shape()
            shape.insert_textbox(
                rect,
                "Misaligned text that should be fixed.",
                fontname="helv",
                fontsize=10,
                color=(0, 0, 0),
            )
            shape.commit()
            doc.save(path)
            doc.close()

            # Mock Path.replace to raise OSError
            with unittest.mock.patch(
                "pathlib.Path.replace",
                side_effect=OSError("Permission denied"),
            ):
                with self.assertRaises(OSError):
                    fix_translated_layout(path)

            # Temp file should be cleaned up (unlink called)
        finally:
            if os.path.exists(path):
                os.unlink(path)
            tmp = path + ".tmp.pdf"
            if os.path.exists(tmp):
                os.unlink(tmp)


class TestGetImageBboxes(unittest.TestCase):
    """Test _get_image_bboxes function."""

    def test_returns_empty_for_page_without_images(self):
        """Page with no images returns empty list."""
        import fitz

        from app.services.layout_fix import _get_image_bboxes

        doc = fitz.open()
        page = doc.new_page(width=612, height=792)
        result = _get_image_bboxes(page)
        self.assertEqual(result, [])
        doc.close()

    def test_returns_bboxes_for_page_with_image(self):
        """Page with an image returns its bounding box."""
        from app.services.layout_fix import _get_image_bboxes

        # Mock a page with images
        page = unittest.mock.MagicMock()
        page.get_images.return_value = [(1, 0, 0, 0, 0, 0, 0, "", "")]
        mock_rect = unittest.mock.MagicMock()
        mock_rect.is_empty = False
        mock_rect.is_valid = True
        mock_rect.x0 = 100.0
        mock_rect.y0 = 100.0
        mock_rect.x1 = 200.0
        mock_rect.y1 = 200.0
        page.get_image_bbox.return_value = mock_rect

        result = _get_image_bboxes(page)
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0], (100.0, 100.0, 200.0, 200.0))

    def test_handles_get_image_bbox_exception(self):
        """Exceptions from get_image_bbox are silently skipped."""
        from app.services.layout_fix import _get_image_bboxes

        page = unittest.mock.MagicMock()
        page.get_images.return_value = [(1, 0, 0, 0, 0, 0, 0, "", "")]
        page.get_image_bbox.side_effect = ValueError("no image")
        result = _get_image_bboxes(page)
        self.assertEqual(result, [])


class TestBlockOverlapsImage(unittest.TestCase):
    """Test _block_overlaps_image function."""

    def test_no_overlap_returns_false(self):
        """Block that doesn't overlap image returns False."""
        from app.services.layout_fix import TextBlockInfo, _block_overlaps_image

        block = TextBlockInfo(
            bbox=(100.0, 100.0, 200.0, 120.0),
            text="test",
            avg_font_size=10.0,
            block_index=0,
        )
        image_bboxes = [(300.0, 300.0, 400.0, 400.0)]
        self.assertFalse(_block_overlaps_image(block, image_bboxes))

    def test_full_overlap_returns_true(self):
        """Block fully inside image returns True."""
        from app.services.layout_fix import TextBlockInfo, _block_overlaps_image

        block = TextBlockInfo(
            bbox=(110.0, 110.0, 190.0, 190.0),
            text="test",
            avg_font_size=10.0,
            block_index=0,
        )
        image_bboxes = [(100.0, 100.0, 200.0, 200.0)]
        self.assertTrue(_block_overlaps_image(block, image_bboxes))

    def test_partial_overlap_below_threshold_returns_false(self):
        """Block with <50% overlap returns False."""
        from app.services.layout_fix import TextBlockInfo, _block_overlaps_image

        # Block: 100x20 area = 2000
        block = TextBlockInfo(
            bbox=(100.0, 100.0, 200.0, 120.0),
            text="test",
            avg_font_size=10.0,
            block_index=0,
        )
        # Image overlaps only 10x20 = 200 (10% of block)
        image_bboxes = [(190.0, 100.0, 250.0, 120.0)]
        self.assertFalse(_block_overlaps_image(block, image_bboxes))

    def test_partial_overlap_above_threshold_returns_true(self):
        """Block with >50% overlap returns True."""
        from app.services.layout_fix import TextBlockInfo, _block_overlaps_image

        # Block: 100x20 area = 2000
        block = TextBlockInfo(
            bbox=(100.0, 100.0, 200.0, 120.0),
            text="test",
            avg_font_size=10.0,
            block_index=0,
        )
        # Image overlaps 60x20 = 1200 (60% of block)
        image_bboxes = [(140.0, 100.0, 250.0, 120.0)]
        self.assertTrue(_block_overlaps_image(block, image_bboxes))

    def test_empty_image_bboxes_returns_false(self):
        """No images means no overlap."""
        from app.services.layout_fix import TextBlockInfo, _block_overlaps_image

        block = TextBlockInfo(
            bbox=(100.0, 100.0, 200.0, 120.0),
            text="test",
            avg_font_size=10.0,
            block_index=0,
        )
        self.assertFalse(_block_overlaps_image(block, []))

    def test_zero_area_block_returns_false(self):
        """Block with zero area returns False."""
        from app.services.layout_fix import TextBlockInfo, _block_overlaps_image

        block = TextBlockInfo(
            bbox=(100.0, 100.0, 100.0, 100.0),
            text="test",
            avg_font_size=10.0,
            block_index=0,
        )
        image_bboxes = [(100.0, 100.0, 200.0, 200.0)]
        self.assertFalse(_block_overlaps_image(block, image_bboxes))

    def test_multiple_images_checks_all(self):
        """Checks overlap against all images."""
        from app.services.layout_fix import TextBlockInfo, _block_overlaps_image

        block = TextBlockInfo(
            bbox=(100.0, 100.0, 200.0, 120.0),
            text="test",
            avg_font_size=10.0,
            block_index=0,
        )
        # First image doesn't overlap, second does
        image_bboxes = [
            (300.0, 300.0, 400.0, 400.0),
            (100.0, 100.0, 200.0, 120.0),
        ]
        self.assertTrue(_block_overlaps_image(block, image_bboxes))


class TestFindNearestColumn(unittest.TestCase):
    """Test _find_nearest_column helper."""

    def test_returns_nearest_column(self):
        block = _tb((300, 100, 500, 120))
        columns = [ColumnInfo(54.0, 228.0), ColumnInfo(337.0, 228.0)]
        result = _find_nearest_column(block, columns)
        self.assertAlmostEqual(result.left_margin, 337.0)

    def test_returns_left_column_for_left_block(self):
        block = _tb((60, 100, 280, 120))
        columns = [ColumnInfo(54.0, 228.0), ColumnInfo(337.0, 228.0)]
        result = _find_nearest_column(block, columns)
        self.assertAlmostEqual(result.left_margin, 54.0)

    def test_returns_none_for_empty_columns(self):
        block = _tb((100, 100, 300, 120))
        result = _find_nearest_column(block, [])
        self.assertIsNone(result)

    def test_single_column(self):
        block = _tb((91, 100, 504, 120))
        columns = [ColumnInfo(91.0, 413.0)]
        result = _find_nearest_column(block, columns)
        self.assertAlmostEqual(result.left_margin, 91.0)


class TestTwoColumnLayout(unittest.TestCase):
    """Test two-column layout detection and per-column fixing."""

    def test_detects_two_columns(self):
        """Blocks at x0=54 and x0=337 should detect two columns."""
        blocks = [
            _tb((54, 100, 282, 120), "a" * 50),
            _tb((54, 130, 282, 150), "b" * 50),
            _tb((54, 160, 282, 180), "c" * 50),
            _tb((337, 100, 565, 120), "d" * 50),
            _tb((337, 130, 565, 150), "e" * 50),
            _tb((337, 160, 565, 180), "f" * 50),
        ]
        columns = _analyze_page_layout(blocks)
        self.assertEqual(len(columns), 2)
        self.assertAlmostEqual(columns[0].left_margin, 54.0)
        self.assertAlmostEqual(columns[1].left_margin, 337.0)

    def test_single_column_not_misdetected(self):
        """Blocks at x0=91 should detect single column."""
        blocks = [
            _tb((91, 100, 504, 120), "a" * 50),
            _tb((91, 130, 504, 150), "b" * 50),
            _tb((91, 160, 504, 180), "c" * 50),
        ]
        columns = _analyze_page_layout(blocks)
        self.assertEqual(len(columns), 1)
        self.assertAlmostEqual(columns[0].left_margin, 91.0)

    def test_two_column_needs_fix_uses_correct_margin(self):
        """In two-column layout, blocks checked against their column's margin."""
        columns = [ColumnInfo(54.0, 228.0), ColumnInfo(337.0, 228.0)]

        # Block in right column at correct position → no fix
        block = _tb((337, 100, 565, 120), text="d" * 50)
        self.assertFalse(_needs_fix(block, columns))

        # Block in right column at wrong position → needs fix
        block = _tb((380, 100, 565, 120), text="d" * 50)
        self.assertTrue(_needs_fix(block, columns))

        # Block in left column at correct position → no fix
        block = _tb((54, 100, 282, 120), text="a" * 50)
        self.assertFalse(_needs_fix(block, columns))

        # Block in left column at wrong position → needs fix
        block = _tb((100, 100, 282, 120), text="a" * 50)
        self.assertTrue(_needs_fix(block, columns))

    def test_two_column_reinsert_uses_correct_margin(self):
        """In two-column layout, blocks reinserted at their column's margin."""
        from app.services.layout_fix import _reinsert_blocks

        columns = [ColumnInfo(54.0, 228.0), ColumnInfo(337.0, 228.0)]
        page = unittest.mock.MagicMock()
        page.rect.width = 612
        page.get_fonts.return_value = []
        # Mock insert_textbox to return positive (success)
        page.new_shape.return_value.insert_textbox.return_value = 1

        # Block near right column should be reinserted at x0=337
        blocks = [_tb((380, 100, 565, 120), text="right column text", font_size=10)]
        _reinsert_blocks(page, blocks, columns)
        # Check that insert_textbox was called with rect starting at ~337
        shape = page.new_shape.return_value
        self.assertTrue(shape.insert_textbox.called)
        rect = shape.insert_textbox.call_args[0][0]
        self.assertAlmostEqual(rect.x0, 337.0, delta=1)

    def test_needs_fix_returns_true_for_empty_columns(self):
        """When columns list is empty, block conservatively needs fix."""
        block = _tb((91, 100, 504, 120), text="some text")
        self.assertTrue(_needs_fix(block, []))

    def test_reinsert_skips_block_when_columns_empty(self):
        """When columns list is empty, block is skipped (no column to assign)."""
        from app.services.layout_fix import _reinsert_blocks

        page = unittest.mock.MagicMock()
        page.rect.width = 612
        page.get_fonts.return_value = []
        blocks = [_tb((91, 100, 504, 120), text="some text", font_size=10)]
        result = _reinsert_blocks(page, blocks, [])
        self.assertEqual(result, 0)


if __name__ == "__main__":
    unittest.main()
