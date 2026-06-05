"""Integration tests for Paper China API endpoints."""

import io
import pytest
from unittest.mock import patch, MagicMock, AsyncMock
from fastapi.testclient import TestClient

from app.main import app
from app.core.database import get_session
from app.models.paper import Paper


@pytest.fixture
def client():
    """Create test client."""
    return TestClient(app)


@pytest.fixture
def mock_db():
    """Mock database session with async support."""
    mock_session = AsyncMock()

    # Setup default return values for scalar (used for count queries)
    mock_session.scalar = AsyncMock(return_value=0)

    # Setup execute to return a mock result with scalars().all()
    mock_result = MagicMock()
    mock_scalars = MagicMock()
    mock_scalars.all.return_value = []
    mock_result.scalars.return_value = mock_scalars
    mock_result.scalar_one_or_none.return_value = None
    mock_session.execute = AsyncMock(return_value=mock_result)

    mock_session.commit = AsyncMock()
    mock_session.refresh = AsyncMock()
    mock_session.delete = AsyncMock()

    app.dependency_overrides[get_session] = lambda: mock_session
    yield mock_session
    app.dependency_overrides.clear()


@pytest.fixture
def sample_paper():
    """Create a sample paper object."""
    paper = MagicMock(spec=Paper)
    paper.id = "test12345678"
    paper.title = "Test Paper"
    paper.original_filename = "test.pdf"
    paper.stored_filename = "stored_test.pdf"
    paper.translated_filename = None
    paper.dual_filename = None
    paper.file_size = 1024
    paper.page_count = 10
    paper.translation_status = "pending"
    paper.translation_progress = 0.0
    paper.translation_error = None
    paper.tags = "test,ai"
    paper.notes = ""
    paper.created_at = None
    paper.updated_at = None
    return paper


class TestHealthEndpoint:
    """Test health check endpoint."""

    def test_health_returns_ok(self, client):
        response = client.get("/health")
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "ok"
        assert "version" in data


class TestIndexEndpoint:
    """Test index page endpoint."""

    def test_index_returns_html(self, client):
        response = client.get("/")
        assert response.status_code == 200
        assert "Paper China" in response.text


class TestPapersListEndpoint:
    """Test papers list endpoint."""

    def test_list_papers_empty(self, client, mock_db):
        mock_db.scalar.return_value = 0
        mock_result = MagicMock()
        mock_scalars = MagicMock()
        mock_scalars.all.return_value = []
        mock_result.scalars.return_value = mock_scalars
        mock_db.execute.return_value = mock_result

        response = client.get("/api/papers/")
        assert response.status_code == 200
        data = response.json()
        assert data["total"] == 0
        assert data["papers"] == []

    def test_list_papers_with_search(self, client, mock_db):
        mock_db.scalar.return_value = 1
        mock_result = MagicMock()
        mock_scalars = MagicMock()
        mock_scalars.all.return_value = []
        mock_result.scalars.return_value = mock_scalars
        mock_db.execute.return_value = mock_result

        response = client.get("/api/papers/?search=test")
        assert response.status_code == 200

    def test_list_papers_with_status_filter(self, client, mock_db):
        mock_db.scalar.return_value = 0
        mock_result = MagicMock()
        mock_scalars = MagicMock()
        mock_scalars.all.return_value = []
        mock_result.scalars.return_value = mock_scalars
        mock_db.execute.return_value = mock_result

        response = client.get("/api/papers/?status=completed")
        assert response.status_code == 200

    def test_list_papers_with_pagination(self, client, mock_db):
        mock_db.scalar.return_value = 100
        mock_result = MagicMock()
        mock_scalars = MagicMock()
        mock_scalars.all.return_value = []
        mock_result.scalars.return_value = mock_scalars
        mock_db.execute.return_value = mock_result

        response = client.get("/api/papers/?offset=10&limit=20")
        assert response.status_code == 200


class TestPaperUploadEndpoint:
    """Test paper upload endpoint."""

    def test_upload_invalid_file_type(self, client):
        response = client.post(
            "/api/papers/upload",
            files={"file": ("test.txt", b"not a pdf", "text/plain")},
        )
        assert response.status_code == 400
        assert "PDF" in response.json()["detail"]

    def test_upload_invalid_pdf_header(self, client):
        response = client.post(
            "/api/papers/upload",
            files={"file": ("test.pdf", b"not a pdf content", "application/pdf")},
        )
        assert response.status_code == 400
        assert "PDF header" in response.json()["detail"]

    def test_upload_valid_pdf(self, client, mock_db):
        # Create a minimal valid PDF
        pdf_content = b"%PDF-1.4\n1 0 obj<</Type/Catalog/Pages 2 0 R>>endobj\n2 0 obj<</Type/Pages/Kids[3 0 R]/Count 1>>endobj\n3 0 obj<</Type/Page/MediaBox[0 0 612 792]/Parent 2 0 R>>endobj\nxref\n0 4\n0000000000 65535 f \n0000000009 00000 n \n0000000058 00000 n \n0000000115 00000 n \ntrailer<</Size 4/Root 1 0 R>>\nstartxref\n190\n%%EOF"

        with patch("app.api.papers.save_uploaded_pdf") as mock_save, \
             patch("app.api.papers.get_pdf_info") as mock_info, \
             patch("app.api.papers.extract_title_from_pdf") as mock_title:

            mock_save.return_value = MagicMock()
            mock_save.return_value.name = "stored_test.pdf"
            mock_info.return_value = (10, 1024)
            mock_title.return_value = "Test Paper Title"

            # Mock the Paper constructor to return a proper object
            with patch("app.api.papers.Paper") as MockPaper:
                mock_paper = MagicMock()
                mock_paper.id = "new123456789"
                mock_paper.title = "Test Paper Title"
                mock_paper.original_filename = "test.pdf"
                mock_paper.stored_filename = "stored_test.pdf"
                mock_paper.file_size = 1024
                mock_paper.page_count = 10
                mock_paper.translation_status = "pending"
                mock_paper.translation_progress = 0.0
                mock_paper.translation_error = None
                mock_paper.tags = "test"
                mock_paper.notes = ""
                mock_paper.created_at = None
                mock_paper.updated_at = None
                MockPaper.return_value = mock_paper

                response = client.post(
                    "/api/papers/upload",
                    files={"file": ("test.pdf", pdf_content, "application/pdf")},
                    data={"tags": "test"},
                )
                assert response.status_code == 200
                data = response.json()
                assert data["title"] == "Test Paper Title"
                assert data["page_count"] == 10


class TestPaperDetailEndpoint:
    """Test paper detail endpoint."""

    def test_get_paper_not_found(self, client, mock_db):
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = None
        mock_db.execute.return_value = mock_result

        response = client.get("/api/papers/nonexistent")
        assert response.status_code == 404

    def test_get_paper_success(self, client, mock_db, sample_paper):
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = sample_paper
        mock_db.execute.return_value = mock_result

        with patch("pathlib.Path.exists", return_value=False):
            response = client.get(f"/api/papers/{sample_paper.id}")
            assert response.status_code == 200
            data = response.json()
            assert data["id"] == sample_paper.id
            assert data["title"] == sample_paper.title


class TestPaperDeleteEndpoint:
    """Test paper delete endpoint."""

    def test_delete_paper_not_found(self, client, mock_db):
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = None
        mock_db.execute.return_value = mock_result

        response = client.delete("/api/papers/nonexistent")
        assert response.status_code == 404

    def test_delete_paper_success(self, client, mock_db, sample_paper):
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = sample_paper
        mock_db.execute.return_value = mock_result

        with patch("app.api.papers.delete_paper_files") as mock_delete:
            response = client.delete(f"/api/papers/{sample_paper.id}")
            assert response.status_code == 200
            assert response.json()["ok"] is True
            mock_delete.assert_called_once()


class TestPaperUpdateEndpoint:
    """Test paper update endpoint."""

    def test_update_paper_not_found(self, client, mock_db):
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = None
        mock_db.execute.return_value = mock_result

        response = client.patch(
            "/api/papers/nonexistent",
            json={"title": "New Title"},
        )
        assert response.status_code == 404

    def test_update_paper_success(self, client, mock_db, sample_paper):
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = sample_paper
        mock_db.execute.return_value = mock_result

        response = client.patch(
            f"/api/papers/{sample_paper.id}",
            json={"title": "New Title", "tags": "new,tag"},
        )
        assert response.status_code == 200
        assert response.json()["ok"] is True
        assert sample_paper.title == "New Title"
        assert sample_paper.tags == "new,tag"

    def test_update_paper_validation_title_too_long(self, client, mock_db):
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = MagicMock()
        mock_db.execute.return_value = mock_result

        response = client.patch(
            "/api/papers/test123",
            json={"title": "x" * 501},
        )
        assert response.status_code == 400
        assert "Title must be 500 characters or less" in response.json()["detail"]

    def test_update_paper_validation_tags_too_long(self, client, mock_db):
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = MagicMock()
        mock_db.execute.return_value = mock_result

        response = client.patch(
            "/api/papers/test123",
            json={"tags": "x" * 1001},
        )
        assert response.status_code == 400
        assert "Tags must be 1000 characters or less" in response.json()["detail"]

    def test_update_paper_validation_notes_too_long(self, client, mock_db):
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = MagicMock()
        mock_db.execute.return_value = mock_result

        response = client.patch(
            "/api/papers/test123",
            json={"notes": "x" * 10001},
        )
        assert response.status_code == 400
        assert "Notes must be 10000 characters or less" in response.json()["detail"]


class TestTranslationEndpoint:
    """Test translation endpoint."""

    def test_translate_paper_not_found(self, client, mock_db):
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = None
        mock_db.execute.return_value = mock_result

        response = client.post("/api/papers/nonexistent/translate")
        assert response.status_code == 404

    def test_translate_already_in_progress(self, client, mock_db, sample_paper):
        sample_paper.translation_status = "translating"
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = sample_paper
        mock_db.execute.return_value = mock_result

        response = client.post(f"/api/papers/{sample_paper.id}/translate")
        assert response.status_code == 409

    def test_translate_starts_successfully(self, client, mock_db, sample_paper):
        sample_paper.translation_status = "pending"
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = sample_paper
        mock_db.execute.return_value = mock_result

        with patch("app.api.papers.BackgroundTasks.add_task"):
            response = client.post(f"/api/papers/{sample_paper.id}/translate")
            assert response.status_code == 200
            assert response.json()["status"] == "translating"


class TestDownloadEndpoints:
    """Test download endpoints."""

    def test_download_original_not_found(self, client, mock_db):
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = None
        mock_db.execute.return_value = mock_result

        response = client.get("/api/papers/nonexistent/download/original")
        assert response.status_code == 404

    def test_download_translated_not_found(self, client, mock_db, sample_paper):
        sample_paper.translated_filename = None
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = sample_paper
        mock_db.execute.return_value = mock_result

        response = client.get(f"/api/papers/{sample_paper.id}/download/translated")
        assert response.status_code == 404

    def test_download_dual_not_found(self, client, mock_db, sample_paper):
        sample_paper.dual_filename = None
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = sample_paper
        mock_db.execute.return_value = mock_result

        response = client.get(f"/api/papers/{sample_paper.id}/download/dual")
        assert response.status_code == 404


class TestStatsEndpoint:
    """Test stats endpoint."""

    def test_stats_returns_data(self, client):
        # The stats endpoint creates its own session, so we need to mock it differently
        with patch("app.core.database.async_session") as mock_session_cls:
            mock_session = AsyncMock()
            mock_session.__aenter__ = AsyncMock(return_value=mock_session)
            mock_session.__aexit__ = AsyncMock(return_value=False)
            mock_session.scalar = AsyncMock(side_effect=[10, 5])
            mock_session_cls.return_value = mock_session

            response = client.get("/api/stats")
            assert response.status_code == 200
            data = response.json()
            assert data["total_papers"] == 10
            assert data["completed_translations"] == 5


class TestErrorHandling:
    """Test error handling scenarios."""

    def test_invalid_paper_id_format(self, client, mock_db):
        """Test that invalid paper ID format returns 404."""
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = None
        mock_db.execute.return_value = mock_result

        response = client.get("/api/papers/invalid-id-format")
        assert response.status_code == 404

    def test_upload_no_file(self, client):
        """Test that upload without file returns 422."""
        response = client.post("/api/papers/upload")
        assert response.status_code == 422

    def test_upload_empty_file(self, client):
        """Test that upload with empty file returns 400."""
        response = client.post(
            "/api/papers/upload",
            files={"file": ("test.pdf", b"", "application/pdf")},
        )
        assert response.status_code == 400

    def test_translate_already_completed(self, client, mock_db, sample_paper):
        """Test that translating already completed paper is allowed (re-translation)."""
        sample_paper.translation_status = "completed"
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = sample_paper
        mock_db.execute.return_value = mock_result

        with patch("app.api.papers.BackgroundTasks.add_task"):
            response = client.post(f"/api/papers/{sample_paper.id}/translate")
            assert response.status_code == 200
            assert response.json()["status"] == "translating"

    def test_update_paper_empty_body(self, client, mock_db, sample_paper):
        """Test that update with empty body succeeds (no changes)."""
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = sample_paper
        mock_db.execute.return_value = mock_result

        response = client.patch(
            f"/api/papers/{sample_paper.id}",
            json={},
        )
        assert response.status_code == 200
        assert response.json()["ok"] is True

    def test_download_original_file_missing(self, client, mock_db, sample_paper):
        """Test that download returns 404 when file is missing."""
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = sample_paper
        mock_db.execute.return_value = mock_result

        with patch("pathlib.Path.exists", return_value=False):
            response = client.get(f"/api/papers/{sample_paper.id}/download/original")
            assert response.status_code == 404

    def test_view_original_file_missing(self, client, mock_db, sample_paper):
        """Test that view returns 404 when file is missing."""
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = sample_paper
        mock_db.execute.return_value = mock_result

        with patch("pathlib.Path.exists", return_value=False):
            response = client.get(f"/api/papers/{sample_paper.id}/view/original")
            assert response.status_code == 404

    def test_list_papers_invalid_limit(self, client, mock_db):
        """Test that invalid limit is clamped."""
        mock_db.scalar.return_value = 0
        mock_result = MagicMock()
        mock_scalars = MagicMock()
        mock_scalars.all.return_value = []
        mock_result.scalars.return_value = mock_scalars
        mock_db.execute.return_value = mock_result

        # Test with limit too high
        response = client.get("/api/papers/?limit=1000")
        assert response.status_code == 200

        # Test with limit too low
        response = client.get("/api/papers/?limit=0")
        assert response.status_code == 200

    def test_list_papers_negative_offset(self, client, mock_db):
        """Test that negative offset is handled."""
        mock_db.scalar.return_value = 0
        mock_result = MagicMock()
        mock_scalars = MagicMock()
        mock_scalars.all.return_value = []
        mock_result.scalars.return_value = mock_scalars
        mock_db.execute.return_value = mock_result

        response = client.get("/api/papers/?offset=-10")
        assert response.status_code == 200


if __name__ == "__main__":
    pytest.main([__file__, "-v"])


class TestHelpers:
    """Test internal helper functions."""

    def test_file_exists_safe_valid(self, tmp_path):
        from app.api.papers import _file_exists_safe
        (tmp_path / "test.pdf").write_bytes(b"content")
        assert _file_exists_safe(tmp_path, "test.pdf") is True

    def test_file_exists_safe_missing(self, tmp_path):
        from app.api.papers import _file_exists_safe
        assert _file_exists_safe(tmp_path, "nonexistent.pdf") is False

    def test_file_exists_safe_none(self, tmp_path):
        from app.api.papers import _file_exists_safe
        assert _file_exists_safe(tmp_path, None) is False

    def test_file_exists_safe_traversal(self, tmp_path):
        from app.api.papers import _file_exists_safe
        # Should reject path traversal even if file exists
        (tmp_path.parent / "secret.pdf").write_bytes(b"secret")
        assert _file_exists_safe(tmp_path, "../secret.pdf") is False

    def test_get_paper_file_valid(self, tmp_path):
        from app.api.papers import _get_paper_file
        (tmp_path / "test.pdf").write_bytes(b"content")
        paper = MagicMock()
        paper.stored_filename = "test.pdf"
        result = _get_paper_file(paper, "stored_filename", tmp_path)
        assert result.name == "test.pdf"

    def test_get_paper_file_missing_attr(self, tmp_path):
        from app.api.papers import _get_paper_file
        from fastapi import HTTPException
        paper = MagicMock()
        paper.stored_filename = None
        with pytest.raises(HTTPException) as exc_info:
            _get_paper_file(paper, "stored_filename", tmp_path)
        assert exc_info.value.status_code == 404

    def test_get_paper_file_traversal(self, tmp_path):
        from app.api.papers import _get_paper_file
        from fastapi import HTTPException
        paper = MagicMock()
        paper.stored_filename = "../../etc/passwd"
        with pytest.raises(HTTPException) as exc_info:
            _get_paper_file(paper, "stored_filename", tmp_path)
        assert exc_info.value.status_code == 403
