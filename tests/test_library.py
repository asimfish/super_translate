"""Tests for app.services.library module."""

import tempfile
import unittest
from pathlib import Path

from app.services.library import generate_stored_filename, get_pdf_info


class TestGenerateStoredFilename(unittest.TestCase):
    """Test filename generation."""

    def test_preserves_pdf_extension(self):
        name = generate_stored_filename("paper.pdf")
        self.assertTrue(name.endswith(".pdf"))
        # uuid4 hex is 32 chars + .pdf = 36
        self.assertEqual(len(name), 36)

    def test_adds_pdf_extension_if_missing(self):
        name = generate_stored_filename("paper")
        self.assertTrue(name.endswith(".pdf"))

    def test_unique_filenames(self):
        names = {generate_stored_filename("test.pdf") for _ in range(100)}
        self.assertEqual(len(names), 100)


class TestGetPdfInfo(unittest.TestCase):
    """Test PDF info extraction."""

    def test_nonexistent_file(self):
        # Should return (0, size) for non-existent files
        # Note: this will raise FileNotFoundError from stat()
        # so we test with a valid but non-PDF file
        with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as f:
            f.write(b"not a pdf")
            path = Path(f.name)

        page_count, file_size = get_pdf_info(path)
        self.assertEqual(page_count, 0)
        self.assertGreater(file_size, 0)
        path.unlink()


if __name__ == "__main__":
    unittest.main()
