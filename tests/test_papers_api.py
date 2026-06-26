"""Tests for app.api.papers module."""

import unittest
from unittest.mock import MagicMock

from app.api.papers import _paper_to_response


class TestPaperToResponse(unittest.TestCase):
    """Test PaperResponse helper function."""

    def _make_paper(self, **kwargs):
        paper = MagicMock()
        paper.id = kwargs.get("id", "test-id-123")
        paper.title = kwargs.get("title", "Test Paper")
        paper.original_filename = kwargs.get("original_filename", "test.pdf")
        paper.file_size = kwargs.get("file_size", 1024)
        paper.page_count = kwargs.get("page_count", 10)
        paper.translation_status = kwargs.get("translation_status", "pending")
        paper.translation_progress = kwargs.get("translation_progress", 0.0)
        paper.translation_error = kwargs.get("translation_error")
        paper.translation_log = kwargs.get("translation_log", "")
        paper.tags = kwargs.get("tags", "")
        paper.notes = kwargs.get("notes", "")
        paper.created_at = kwargs.get("created_at")
        paper.updated_at = kwargs.get("updated_at")
        return paper

    def test_basic_conversion(self):
        paper = self._make_paper()
        resp = _paper_to_response(paper)
        self.assertEqual(resp.id, "test-id-123")
        self.assertEqual(resp.title, "Test Paper")
        self.assertEqual(resp.file_size, 1024)
        self.assertEqual(resp.translation_stage, "等待翻译")
        self.assertIsNone(resp.translation_eta_seconds)
        self.assertEqual(resp.translation_eta, "")
        self.assertFalse(resp.has_original)
        self.assertFalse(resp.has_translated)
        self.assertFalse(resp.has_dual)
        self.assertFalse(resp.has_qa_report)

    def test_translation_eta_and_stage_are_derived_from_log(self):
        paper = self._make_paper(
            translation_status="translating",
            translation_progress=0.4,
            translation_log="[10:00:00] 翻译进度: 40%，预计剩余 1分20秒",
        )
        resp = _paper_to_response(paper)
        self.assertEqual(resp.translation_stage, "翻译中")
        self.assertEqual(resp.translation_eta_seconds, 80)
        self.assertEqual(resp.translation_eta, "1分20秒")

    def test_translation_stage_reports_qa_phase(self):
        paper = self._make_paper(
            translation_status="translating",
            translation_log="[10:00:00] 正在检查译文和版面",
        )
        resp = _paper_to_response(paper)
        self.assertEqual(resp.translation_stage, "译后检查")

    def test_with_file_flags(self):
        paper = self._make_paper()
        resp = _paper_to_response(
            paper,
            has_original=True,
            has_translated=True,
            has_qa_report=True,
        )
        self.assertTrue(resp.has_original)
        self.assertTrue(resp.has_translated)
        self.assertFalse(resp.has_dual)
        self.assertTrue(resp.has_qa_report)

    def test_with_datetime(self):
        from datetime import datetime

        paper = self._make_paper(
            created_at=datetime(2026, 1, 15, 10, 30),
            updated_at=datetime(2026, 1, 16, 14, 0),
        )
        resp = _paper_to_response(paper)
        self.assertIn("2026-01-15", resp.created_at)
        self.assertIn("2026-01-16", resp.updated_at)

    def test_with_none_datetime(self):
        paper = self._make_paper(created_at=None, updated_at=None)
        resp = _paper_to_response(paper)
        self.assertEqual(resp.created_at, "")
        self.assertEqual(resp.updated_at, "")


class TestGenerateId(unittest.TestCase):
    """Test paper ID generation."""

    def test_returns_12_char_hex(self):
        from app.models.paper import generate_id

        id_str = generate_id()
        self.assertEqual(len(id_str), 12)
        self.assertTrue(all(c in "0123456789abcdef" for c in id_str))

    def test_unique_ids(self):
        from app.models.paper import generate_id

        ids = {generate_id() for _ in range(100)}
        self.assertEqual(len(ids), 100)


if __name__ == "__main__":
    unittest.main()
