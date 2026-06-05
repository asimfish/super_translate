"""Tests for app.services.library module."""

import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from app.services.library import (
    delete_paper_files,
    extract_title_from_pdf,
    generate_stored_filename,
    get_pdf_info,
    save_uploaded_pdf,
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


class TestSaveUploadedPdf:
    """Test PDF upload saving."""

    @pytest.mark.asyncio
    async def test_save_creates_file(self, tmp_path):
        with patch("app.services.library.settings") as mock_settings:
            mock_settings.papers_path = tmp_path
            result = await save_uploaded_pdf(b"%PDF-1.4 test content", "upload.pdf")
            assert result.exists()
            assert result.read_bytes() == b"%PDF-1.4 test content"

    @pytest.mark.asyncio
    async def test_save_unique_names(self, tmp_path):
        with patch("app.services.library.settings") as mock_settings:
            mock_settings.papers_path = tmp_path
            p1 = await save_uploaded_pdf(b"pdf1", "same_name.pdf")
            p2 = await save_uploaded_pdf(b"pdf2", "same_name.pdf")
            assert p1 != p2
            assert p1.exists()
            assert p2.exists()

    @pytest.mark.asyncio
    async def test_save_creates_parent_dirs(self, tmp_path):
        nested = tmp_path / "deep" / "nested"
        with patch("app.services.library.settings") as mock_settings:
            mock_settings.papers_path = nested
            result = await save_uploaded_pdf(b"content", "test.pdf")
            assert result.exists()


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


if __name__ == "__main__":
    unittest.main()
