"""Tests for app.services.library module."""

import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from app.services.library import (
    _safe_delete,
    delete_paper_files,
    extract_title_from_pdf,
    generate_stored_filename,
    get_pdf_info,
)


class TestGenerateStoredFilename(unittest.TestCase):
    """Test filename generation."""

    def test_preserves_pdf_extension(self):
        name = generate_stored_filename("paper.pdf")
        self.assertTrue(name.endswith(".pdf"))
        self.assertEqual(len(name), 36)

    def test_adds_pdf_extension_if_missing(self):
        name = generate_stored_filename("paper")
        self.assertTrue(name.endswith(".pdf"))

    def test_unique_filenames(self):
        names = {generate_stored_filename("test.pdf") for _ in range(100)}
        self.assertEqual(len(names), 100)

    def test_no_path_separators(self):
        """Filename should not contain path traversal characters."""
        name = generate_stored_filename("../../etc/passwd.pdf")
        self.assertNotIn("/", name)
        self.assertNotIn("\\", name)

    def test_preserves_non_pdf_extension(self):
        name = generate_stored_filename("doc.docx")
        self.assertTrue(name.endswith(".docx"))


class TestGetPdfInfo(unittest.TestCase):
    """Test PDF info extraction."""

    def test_nonexistent_file(self):
        with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as f:
            f.write(b"not a pdf")
            path = Path(f.name)

        page_count, file_size = get_pdf_info(path)
        self.assertEqual(page_count, 0)
        self.assertGreater(file_size, 0)
        path.unlink()

    def test_valid_pdf(self):
        """Test with a minimal valid PDF."""
        with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as f:
            f.write(
                b"%PDF-1.4\n1 0 obj<</Type/Catalog/Pages 2 0 R>>endobj\n"
                b"2 0 obj<</Type/Pages/Kids[3 0 R]/Count 1>>endobj\n"
                b"3 0 obj<</Type/Page/MediaBox[0 0 612 792]/Parent 2 0 R>>endobj\n"
                b"xref\n0 4\n0000000000 65535 f \n0000000009 00000 n \n"
                b"0000000058 00000 n \n0000000115 00000 n \n"
                b"trailer<</Size 4/Root 1 0 R>>\nstartxref\n190\n%%EOF"
            )
            path = Path(f.name)

        page_count, file_size = get_pdf_info(path)
        self.assertGreaterEqual(page_count, 1)
        self.assertGreater(file_size, 0)
        path.unlink()

    def test_deleted_file_returns_zero(self):
        """File deleted between open and stat should return (0, 0)."""
        with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as f:
            f.write(b"not a pdf")
            path = Path(f.name)
        path.unlink()  # delete before calling

        page_count, file_size = get_pdf_info(path)
        self.assertEqual(page_count, 0)
        self.assertEqual(file_size, 0)


class TestExtractTitleFromPdf(unittest.TestCase):
    """Test title extraction."""

    def test_fallback_to_filename(self):
        """When PDF is invalid, should fall back to filename."""
        with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False, prefix="my_research_") as f:
            f.write(b"not a pdf")
            path = Path(f.name)

        title = extract_title_from_pdf(path)
        self.assertIsInstance(title, str)
        self.assertGreater(len(title), 0)
        path.unlink()

    def test_returns_string_always(self):
        """Should always return a string, never None."""
        with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as f:
            f.write(b"garbage")
            path = Path(f.name)

        title = extract_title_from_pdf(path)
        self.assertIsInstance(title, str)
        path.unlink()

    def test_extracts_title_from_metadata(self):
        """Should extract title from PDF metadata when available."""
        import fitz

        with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as f:
            path = Path(f.name)

        doc = fitz.open()
        doc.set_metadata({"title": "My Research Paper"})
        doc.new_page()
        doc.save(str(path))
        doc.close()

        title = extract_title_from_pdf(path)
        self.assertEqual(title, "My Research Paper")
        path.unlink()

    def test_extracts_title_from_first_page_text(self):
        """Should extract title from large text on first page."""
        import fitz

        with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as f:
            path = Path(f.name)

        doc = fitz.open()
        doc.set_metadata({"title": ""})  # No metadata title
        page = doc.new_page()
        # Insert large text (font size > 14) as title
        page.insert_text((72, 72), "Deep Learning for Natural Language Processing", fontsize=18)
        doc.save(str(path))
        doc.close()

        title = extract_title_from_pdf(path)
        self.assertIn("Deep Learning", title)
        path.unlink()

    def test_fallback_when_no_large_text(self):
        """Should fall back to filename when page has only small text."""
        import fitz

        with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as f:
            path = Path(f.name)

        doc = fitz.open()
        doc.set_metadata({"title": ""})
        page = doc.new_page()
        # Insert small text (font size <= 14)
        page.insert_text((72, 72), "small", fontsize=10)
        doc.save(str(path))
        doc.close()

        title = extract_title_from_pdf(path)
        self.assertIsInstance(title, str)
        self.assertGreater(len(title), 0)
        path.unlink()


class TestDeletePaperFiles:
    """Test paper file deletion."""

    @pytest.mark.asyncio
    async def test_deletes_original(self, tmp_path):
        papers_dir = tmp_path / "papers"
        papers_dir.mkdir()
        translations_dir = tmp_path / "translations"
        translations_dir.mkdir()

        original = papers_dir / "test.pdf"
        original.write_bytes(b"pdf content")

        with patch("app.services.library.settings") as mock_settings:
            mock_settings.papers_path = papers_dir
            mock_settings.translations_path = translations_dir

            paper = MagicMock()
            paper.stored_filename = "test.pdf"
            paper.translated_filename = None
            paper.dual_filename = None

            await delete_paper_files(paper)
            assert not original.exists()

    @pytest.mark.asyncio
    async def test_deletes_all_files(self, tmp_path):
        papers_dir = tmp_path / "papers"
        papers_dir.mkdir()
        translations_dir = tmp_path / "translations"
        translations_dir.mkdir()

        original = papers_dir / "test.pdf"
        original.write_bytes(b"original")
        translated = translations_dir / "trans.pdf"
        translated.write_bytes(b"translated")
        dual = translations_dir / "dual.pdf"
        dual.write_bytes(b"dual")

        with patch("app.services.library.settings") as mock_settings:
            mock_settings.papers_path = papers_dir
            mock_settings.translations_path = translations_dir

            paper = MagicMock()
            paper.stored_filename = "test.pdf"
            paper.translated_filename = "trans.pdf"
            paper.dual_filename = "dual.pdf"

            await delete_paper_files(paper)
            assert not original.exists()
            assert not translated.exists()
            assert not dual.exists()

    @pytest.mark.asyncio
    async def test_handles_missing_files(self, tmp_path):
        """Should not raise when files are already missing."""
        papers_dir = tmp_path / "papers"
        papers_dir.mkdir()
        translations_dir = tmp_path / "translations"
        translations_dir.mkdir()

        with patch("app.services.library.settings") as mock_settings:
            mock_settings.papers_path = papers_dir
            mock_settings.translations_path = translations_dir

            paper = MagicMock()
            paper.stored_filename = "nonexistent.pdf"
            paper.translated_filename = "also_missing.pdf"
            paper.dual_filename = None

            # Should not raise
            await delete_paper_files(paper)

    @pytest.mark.asyncio
    async def test_cleans_empty_translation_directory(self, tmp_path):
        """Empty translation output directory should be removed after file deletion."""
        papers_dir = tmp_path / "papers"
        papers_dir.mkdir()
        translations_dir = tmp_path / "translations"
        translations_dir.mkdir()

        # Create files inside a subdirectory (like paper.id/)
        subdir = translations_dir / "abc123"
        subdir.mkdir()
        translated = subdir / "test-mono.pdf"
        translated.write_bytes(b"translated")

        with patch("app.services.library.settings") as mock_settings:
            mock_settings.papers_path = papers_dir
            mock_settings.translations_path = translations_dir

            paper = MagicMock()
            paper.stored_filename = "test.pdf"
            paper.translated_filename = "abc123/test-mono.pdf"
            paper.dual_filename = None

            await delete_paper_files(paper)
            assert not translated.exists()
            assert not subdir.exists()  # empty directory should be cleaned up

    @pytest.mark.asyncio
    async def test_preserves_nonempty_translation_directory(self, tmp_path):
        """Translation directory with other files should not be removed."""
        papers_dir = tmp_path / "papers"
        papers_dir.mkdir()
        translations_dir = tmp_path / "translations"
        translations_dir.mkdir()

        subdir = translations_dir / "abc123"
        subdir.mkdir()
        translated = subdir / "test-mono.pdf"
        translated.write_bytes(b"translated")
        other_file = subdir / "other.pdf"
        other_file.write_bytes(b"other")

        with patch("app.services.library.settings") as mock_settings:
            mock_settings.papers_path = papers_dir
            mock_settings.translations_path = translations_dir

            paper = MagicMock()
            paper.stored_filename = "test.pdf"
            paper.translated_filename = "abc123/test-mono.pdf"
            paper.dual_filename = None

            await delete_paper_files(paper)
            assert not translated.exists()
            assert subdir.exists()  # directory has other files, keep it
            assert other_file.exists()


class TestExtractTitleFromFirstPage(unittest.TestCase):
    """Test _extract_title_from_first_page edge cases."""

    def test_skips_non_text_blocks(self):
        """Non-text blocks (type=1, images) should be skipped."""
        from app.services.library import _extract_title_from_first_page

        page = MagicMock()
        page.get_text.return_value = {
            "blocks": [
                {"type": 1, "bbox": [0, 0, 100, 100]},  # image block
                {
                    "type": 0,
                    "lines": [{"spans": [{"text": "Actual Title Text", "size": 16.0}]}],
                },
            ]
        }
        result = _extract_title_from_first_page(page)
        self.assertEqual(result, "Actual Title Text")

    def test_returns_none_when_no_large_text(self):
        """Returns None when no text has font size > 14."""
        from app.services.library import _extract_title_from_first_page

        page = MagicMock()
        page.get_text.return_value = {
            "blocks": [
                {
                    "type": 0,
                    "lines": [{"spans": [{"text": "small text", "size": 10.0}]}],
                },
            ]
        }
        result = _extract_title_from_first_page(page)
        self.assertIsNone(result)

    def test_returns_none_for_empty_page(self):
        """Returns None for a page with no blocks."""
        from app.services.library import _extract_title_from_first_page

        page = MagicMock()
        page.get_text.return_value = {"blocks": []}
        result = _extract_title_from_first_page(page)
        self.assertIsNone(result)


class TestSafeDelete:
    """Test _safe_delete path traversal protection."""

    def test_deletes_existing_file(self, tmp_path):
        f = tmp_path / "test.pdf"
        f.write_bytes(b"content")
        _safe_delete(tmp_path, "test.pdf")
        assert not f.exists()

    def test_ignores_missing_file(self, tmp_path):
        # Should not raise
        _safe_delete(tmp_path, "nonexistent.pdf")

    def test_ignores_empty_filename(self, tmp_path):
        _safe_delete(tmp_path, "")
        _safe_delete(tmp_path, None)

    def test_rejects_path_traversal(self, tmp_path):
        """Should refuse to delete files outside the base directory."""
        secret = tmp_path.parent / "secret.pdf"
        secret.write_bytes(b"secret")
        _safe_delete(tmp_path, "../secret.pdf")
        # File should still exist (deletion refused)
        assert secret.exists()
        secret.unlink()

    def test_allows_subdirectory_paths(self, tmp_path):
        """Paths with subdirectories within base dir should work."""
        subdir = tmp_path / "sub"
        subdir.mkdir()
        f = subdir / "test.pdf"
        f.write_bytes(b"content")
        _safe_delete(tmp_path, "sub/test.pdf")
        assert not f.exists()


class TestCleanupOutputDir:
    """Test cleanup_output_dir error handling."""

    def test_logs_warning_on_os_error(self, tmp_path, caplog):
        """Should log a warning if rmtree fails, not raise."""
        import logging

        from app.services.library import cleanup_output_dir

        output_dir = tmp_path / "output"
        output_dir.mkdir()

        with (
            patch("app.services.library.shutil.rmtree", side_effect=OSError("Permission denied")),
            caplog.at_level(logging.WARNING),
        ):
            cleanup_output_dir(output_dir)

        assert "Failed to clean up" in caplog.text
        assert "Permission denied" in caplog.text

    def test_noop_when_dir_missing(self, tmp_path):
        """Should do nothing if directory doesn't exist."""
        from app.services.library import cleanup_output_dir

        cleanup_output_dir(tmp_path / "nonexistent")


if __name__ == "__main__":
    unittest.main()
