"""Integration tests for Paper China API endpoints."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from app.core.database import get_session
from app.main import app
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

    mock_session.add = MagicMock()
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
    paper.id = "abcd12345678"
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

    def test_index_fallback_when_static_missing(self, client):
        with patch("pathlib.Path.exists", return_value=False):
            response = client.get("/")
            assert response.status_code == 200
            assert "Static files not found" in response.text


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

    def test_list_papers_returns_data(self, client, mock_db, sample_paper):
        """Test that list returns paper data when papers exist."""
        mock_db.scalar.return_value = 1
        mock_result = MagicMock()
        mock_scalars = MagicMock()
        mock_scalars.all.return_value = [sample_paper]
        mock_result.scalars.return_value = mock_scalars
        mock_db.execute.return_value = mock_result

        with patch("pathlib.Path.exists", return_value=False):
            response = client.get("/api/papers/")
        assert response.status_code == 200
        data = response.json()
        assert data["total"] == 1
        assert len(data["papers"]) == 1
        assert data["papers"][0]["id"] == sample_paper.id


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

    def test_upload_tags_too_long(self, client):
        pdf_content = b"%PDF-1.4 test"
        response = client.post(
            "/api/papers/upload",
            files={"file": ("test.pdf", pdf_content, "application/pdf")},
            data={"tags": "x" * 1001},
        )
        assert response.status_code == 400
        assert "Tags must be 1000 characters or less" in response.json()["detail"]

    def test_upload_file_too_large_streaming_reject(self, client):
        """Test that oversized files are rejected during streaming read."""
        # Patch the max size to a small value to test without creating 100MB
        with patch("app.api.papers.settings") as mock_settings:
            mock_settings.max_upload_size = 100
            mock_settings.upload_chunk_size = 50
            content = b"%PDF-1.4 " + b"x" * 200
            response = client.post(
                "/api/papers/upload",
                files={"file": ("test.pdf", content, "application/pdf")},
            )
            assert response.status_code == 400
            assert "too large" in response.json()["detail"].lower()

    def test_upload_valid_pdf(self, client, mock_db, tmp_path):
        # Create a minimal valid PDF
        pdf_content = (
            b"%PDF-1.4\n1 0 obj<</Type/Catalog/Pages 2 0 R>>endobj\n"
            b"2 0 obj<</Type/Pages/Kids[3 0 R]/Count 1>>endobj\n"
            b"3 0 obj<</Type/Page/MediaBox[0 0 612 792]/Parent 2 0 R>>endobj\n"
            b"xref\n0 4\n0000000000 65535 f \n0000000009 00000 n \n"
            b"0000000058 00000 n \n0000000115 00000 n \n"
            b"trailer<</Size 4/Root 1 0 R>>\nstartxref\n190\n%%EOF"
        )

        with patch("app.api.papers.generate_stored_filename") as mock_gen, \
             patch("app.api.papers.settings") as mock_settings, \
             patch("app.api.papers.get_pdf_info") as mock_info, \
             patch("app.api.papers.extract_title_from_pdf") as mock_title:

            mock_gen.return_value = "stored_test.pdf"
            mock_settings.papers_path = tmp_path
            mock_settings.translations_path = tmp_path
            mock_settings.max_upload_size = 100 * 1024 * 1024
            mock_settings.upload_chunk_size = 1024 * 1024
            mock_info.return_value = (10, 1024)
            mock_title.return_value = "Test Paper Title"

            # Mock the Paper constructor to return a proper object
            with patch("app.api.papers.Paper") as mock_paper_cls:
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
                mock_paper_cls.return_value = mock_paper

                response = client.post(
                    "/api/papers/upload",
                    files={"file": ("test.pdf", pdf_content, "application/pdf")},
                    data={"tags": "test"},
                )
                assert response.status_code == 200
                data = response.json()
                assert data["title"] == "Test Paper Title"
                assert data["page_count"] == 10

    def test_upload_cleans_file_on_pdf_info_failure(self, mock_db, tmp_path):
        """Test that uploaded file is deleted if get_pdf_info raises."""
        pdf_content = b"%PDF-1.4 test"
        stored_path = tmp_path / "stored_test.pdf"

        with patch("app.api.papers.generate_stored_filename") as mock_gen, \
             patch("app.api.papers.settings") as mock_settings, \
             patch("app.api.papers.get_pdf_info", side_effect=Exception("corrupt PDF")), \
             TestClient(app, raise_server_exceptions=False) as c:
                mock_gen.return_value = "stored_test.pdf"
                mock_settings.papers_path = tmp_path
                mock_settings.translations_path = tmp_path
                mock_settings.max_upload_size = 100 * 1024 * 1024
                mock_settings.upload_chunk_size = 1024 * 1024
                c.post(
                    "/api/papers/upload",
                    files={"file": ("test.pdf", pdf_content, "application/pdf")},
                    data={"tags": ""},
                )
        assert not stored_path.exists(), "Orphaned file should be cleaned up"


class TestPaperDetailEndpoint:
    """Test paper detail endpoint."""

    def test_get_paper_not_found(self, client, mock_db):
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = None
        mock_db.execute.return_value = mock_result

        response = client.get("/api/papers/000000000000")
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

    def test_progress_clamped_to_valid_range(self, client, mock_db, sample_paper):
        """Progress values outside 0-1 are clamped at the API level."""
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = sample_paper
        mock_db.execute.return_value = mock_result

        # Test out-of-range values
        for invalid_val in [-0.5, 1.5, 999.0]:
            sample_paper.translation_progress = invalid_val
            with patch("pathlib.Path.exists", return_value=False):
                response = client.get(f"/api/papers/{sample_paper.id}")
                data = response.json()
                prog = data["translation_progress"]
                assert 0.0 <= prog <= 1.0, (
                    f"Expected clamped progress for {invalid_val}, got {prog}"
                )


class TestPaperDeleteEndpoint:
    """Test paper delete endpoint."""

    def test_delete_paper_not_found(self, client, mock_db):
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = None
        mock_db.execute.return_value = mock_result

        response = client.delete("/api/papers/000000000000")
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

    def test_delete_paper_while_translating(self, client, mock_db, sample_paper):
        sample_paper.translation_status = "translating"
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = sample_paper
        mock_db.execute.return_value = mock_result

        response = client.delete(f"/api/papers/{sample_paper.id}")
        assert response.status_code == 409
        assert "translation is in progress" in response.json()["detail"]


class TestPaperUpdateEndpoint:
    """Test paper update endpoint."""

    def test_update_paper_not_found(self, client, mock_db):
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = None
        mock_db.execute.return_value = mock_result

        response = client.patch(
            "/api/papers/000000000000",
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

    def test_update_paper_with_notes(self, client, mock_db, sample_paper):
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = sample_paper
        mock_db.execute.return_value = mock_result

        response = client.patch(
            f"/api/papers/{sample_paper.id}",
            json={"notes": "Important paper on attention mechanisms"},
        )
        assert response.status_code == 200
        assert sample_paper.notes == "Important paper on attention mechanisms"

    def test_update_paper_validation_title_too_long(self, client, mock_db):
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = MagicMock()
        mock_db.execute.return_value = mock_result

        response = client.patch(
            "/api/papers/000000000000",
            json={"title": "x" * 501},
        )
        assert response.status_code == 400
        assert "Title must be 500 characters or less" in response.json()["detail"]

    def test_update_paper_validation_tags_too_long(self, client, mock_db):
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = MagicMock()
        mock_db.execute.return_value = mock_result

        response = client.patch(
            "/api/papers/000000000000",
            json={"tags": "x" * 1001},
        )
        assert response.status_code == 400
        assert "Tags must be 1000 characters or less" in response.json()["detail"]

    def test_update_paper_validation_notes_too_long(self, client, mock_db):
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = MagicMock()
        mock_db.execute.return_value = mock_result

        response = client.patch(
            "/api/papers/000000000000",
            json={"notes": "x" * 10001},
        )
        assert response.status_code == 400
        assert "Notes must be 10000 characters or less" in response.json()["detail"]

    def test_update_whitespace_title_stripped_to_none(self, client, mock_db, sample_paper):
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = sample_paper
        mock_db.execute.return_value = mock_result

        response = client.patch(
            f"/api/papers/{sample_paper.id}",
            json={"title": "   "},
        )
        assert response.status_code == 200
        # Title should be stripped to None, so paper.title is not updated
        sample_paper.title = sample_paper.title  # unchanged

    def test_update_whitespace_tags_stripped_to_none(self, client, mock_db, sample_paper):
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = sample_paper
        mock_db.execute.return_value = mock_result

        response = client.patch(
            f"/api/papers/{sample_paper.id}",
            json={"tags": "   "},
        )
        assert response.status_code == 200

    def test_update_whitespace_notes_stripped_to_none(self, client, mock_db, sample_paper):
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = sample_paper
        mock_db.execute.return_value = mock_result

        response = client.patch(
            f"/api/papers/{sample_paper.id}",
            json={"notes": "   "},
        )
        assert response.status_code == 200

    def test_update_title_with_leading_trailing_spaces(self, client, mock_db, sample_paper):
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = sample_paper
        mock_db.execute.return_value = mock_result

        response = client.patch(
            f"/api/papers/{sample_paper.id}",
            json={"title": "  My Paper  "},
        )
        assert response.status_code == 200
        assert sample_paper.title == "My Paper"


class TestTranslationEndpoint:
    """Test translation endpoint."""

    def test_translate_paper_not_found(self, client, mock_db):
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = None
        mock_db.execute.return_value = mock_result

        response = client.post("/api/papers/000000000000/translate")
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

    def test_translate_with_quality_param(self, client, mock_db, sample_paper):
        sample_paper.translation_status = "pending"
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = sample_paper
        mock_db.execute.return_value = mock_result

        with patch("app.api.papers.BackgroundTasks.add_task") as mock_task:
            response = client.post(f"/api/papers/{sample_paper.id}/translate?quality=fast")
            assert response.status_code == 200
            # Verify quality param was passed to background task
            # call_args[0] = (paper_id, backend, quality)
            mock_task.assert_called_once()
            call_args = mock_task.call_args[0]
            assert call_args[3] == "fast"  # quality is 4th arg (after func, paper_id, backend)

    def test_translate_invalid_backend_rejected(self, client, mock_db, sample_paper):
        sample_paper.translation_status = "pending"
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = sample_paper
        mock_db.execute.return_value = mock_result

        response = client.post(f"/api/papers/{sample_paper.id}/translate?backend=malicious")
        assert response.status_code == 400
        assert "Invalid backend" in response.json()["detail"]

    def test_translate_invalid_quality_rejected(self, client, mock_db, sample_paper):
        sample_paper.translation_status = "pending"
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = sample_paper
        mock_db.execute.return_value = mock_result

        response = client.post(f"/api/papers/{sample_paper.id}/translate?quality=ultra")
        assert response.status_code == 400
        assert "Invalid quality" in response.json()["detail"]


class TestDownloadEndpoints:
    """Test download endpoints."""

    def test_download_original_not_found(self, client, mock_db):
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = None
        mock_db.execute.return_value = mock_result

        response = client.get("/api/papers/000000000000/download/original")
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

    def test_download_original_success(self, client, mock_db, sample_paper, tmp_path):
        """Test successful original PDF download."""
        from unittest.mock import patch as _patch
        sample_paper.stored_filename = "test.pdf"
        (tmp_path / "test.pdf").write_bytes(b"%PDF-1.4 fake content")

        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = sample_paper
        mock_db.execute.return_value = mock_result

        with _patch("app.api.papers.settings") as mock_settings:
            mock_settings.papers_path = tmp_path
            response = client.get(f"/api/papers/{sample_paper.id}/download/original")
            assert response.status_code == 200
            assert response.headers["content-type"] == "application/pdf"

    def test_view_original_success(self, client, mock_db, sample_paper, tmp_path):
        """Test successful original PDF view."""
        from unittest.mock import patch as _patch
        sample_paper.stored_filename = "test.pdf"
        (tmp_path / "test.pdf").write_bytes(b"%PDF-1.4 fake content")

        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = sample_paper
        mock_db.execute.return_value = mock_result

        with _patch("app.api.papers.settings") as mock_settings:
            mock_settings.papers_path = tmp_path
            response = client.get(f"/api/papers/{sample_paper.id}/view/original")
            assert response.status_code == 200

    def test_view_translated_success(self, client, mock_db, sample_paper, tmp_path):
        """Test successful translated PDF view."""
        from unittest.mock import patch as _patch
        sample_paper.translated_filename = "paper123/mono.pdf"
        (tmp_path / "paper123").mkdir()
        (tmp_path / "paper123" / "mono.pdf").write_bytes(b"%PDF-1.4 translated")

        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = sample_paper
        mock_db.execute.return_value = mock_result

        with _patch("app.api.papers.settings") as mock_settings:
            mock_settings.translations_path = tmp_path
            response = client.get(f"/api/papers/{sample_paper.id}/view/translated")
            assert response.status_code == 200

    def test_download_translated_success(self, client, mock_db, sample_paper, tmp_path):
        """Test successful translated PDF download."""
        from unittest.mock import patch as _patch
        sample_paper.translated_filename = "paper123/mono.pdf"
        sample_paper.original_filename = "paper.pdf"
        (tmp_path / "paper123").mkdir()
        (tmp_path / "paper123" / "mono.pdf").write_bytes(b"%PDF-1.4 translated")

        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = sample_paper
        mock_db.execute.return_value = mock_result

        with _patch("app.api.papers.settings") as mock_settings:
            mock_settings.translations_path = tmp_path
            response = client.get(f"/api/papers/{sample_paper.id}/download/translated")
            assert response.status_code == 200
            assert "paper_zh.pdf" in response.headers.get("content-disposition", "")

    def test_download_dual_success(self, client, mock_db, sample_paper, tmp_path):
        """Test successful dual PDF download."""
        from unittest.mock import patch as _patch
        sample_paper.dual_filename = "paper123/dual.pdf"
        sample_paper.original_filename = "paper.pdf"
        (tmp_path / "paper123").mkdir()
        (tmp_path / "paper123" / "dual.pdf").write_bytes(b"%PDF-1.4 dual")

        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = sample_paper
        mock_db.execute.return_value = mock_result

        with _patch("app.api.papers.settings") as mock_settings:
            mock_settings.translations_path = tmp_path
            response = client.get(f"/api/papers/{sample_paper.id}/download/dual")
            assert response.status_code == 200
            assert "paper_dual.pdf" in response.headers.get("content-disposition", "")


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

    def test_stats_caching(self, client):
        """Stats should be cached and not hit DB on repeated calls."""
        import app.main as main_module
        # Reset cache
        main_module._stats_cache = None
        main_module._stats_cache_time = 0.0

        with patch("app.core.database.async_session") as mock_session_cls:
            mock_session = AsyncMock()
            mock_session.__aenter__ = AsyncMock(return_value=mock_session)
            mock_session.__aexit__ = AsyncMock(return_value=False)
            mock_session.scalar = AsyncMock(side_effect=[10, 5])
            mock_session_cls.return_value = mock_session

            # First call hits DB
            r1 = client.get("/api/stats")
            assert r1.status_code == 200
            assert r1.json()["total_papers"] == 10

            # Second call should use cache (no more scalar calls available)
            r2 = client.get("/api/stats")
            assert r2.status_code == 200
            assert r2.json()["total_papers"] == 10

        # Cleanup
        main_module._stats_cache = None
        main_module._stats_cache_time = 0.0


class TestErrorHandling:
    """Test error handling scenarios."""

    def test_invalid_paper_id_format(self, client, mock_db):
        """Test that invalid paper ID format returns 404."""
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = None
        mock_db.execute.return_value = mock_result

        response = client.get("/api/papers/invalid-id-format")
        assert response.status_code == 400

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

    def test_file_exists_safe_with_precomputed_base(self, tmp_path):

        from app.api.papers import _file_exists_safe
        (tmp_path / "test.pdf").write_bytes(b"content")
        resolved = tmp_path.resolve()
        # Precomputed base should work the same
        assert _file_exists_safe(tmp_path, "test.pdf", resolved) is True
        assert _file_exists_safe(tmp_path, "missing.pdf", resolved) is False
        assert _file_exists_safe(tmp_path, None, resolved) is False

    def test_get_paper_file_valid(self, tmp_path):
        from app.api.papers import _get_paper_file
        (tmp_path / "test.pdf").write_bytes(b"content")
        paper = MagicMock()
        paper.stored_filename = "test.pdf"
        result = _get_paper_file(paper, "stored_filename", tmp_path)
        assert result.name == "test.pdf"

    def test_get_paper_file_missing_attr(self, tmp_path):
        from fastapi import HTTPException

        from app.api.papers import _get_paper_file
        paper = MagicMock()
        paper.stored_filename = None
        with pytest.raises(HTTPException) as exc_info:
            _get_paper_file(paper, "stored_filename", tmp_path)
        assert exc_info.value.status_code == 404

    def test_get_paper_file_traversal(self, tmp_path):
        from fastapi import HTTPException

        from app.api.papers import _get_paper_file
        paper = MagicMock()
        paper.stored_filename = "../../etc/passwd"
        with pytest.raises(HTTPException) as exc_info:
            _get_paper_file(paper, "stored_filename", tmp_path)
        assert exc_info.value.status_code == 403

    def test_resolve_backend_config_google(self):
        from app.api.papers import _resolve_backend_config
        from app.services.translator import QualityPreset
        config = _resolve_backend_config("google", QualityPreset.BALANCED)
        assert config.backend == "google"
        assert config.api_key == ""

    def test_resolve_backend_config_fast_forces_google(self):
        from app.api.papers import _resolve_backend_config
        from app.services.translator import QualityPreset
        config = _resolve_backend_config("deepseek", QualityPreset.FAST)
        assert config.backend == "google"
        assert config.api_key == ""

    def test_resolve_backend_config_deepseek(self):
        from app.api.papers import _resolve_backend_config
        from app.services.translator import QualityPreset
        with patch("app.api.papers.settings") as mock_settings:
            mock_settings.deepseek_api_key = "test-key"
            mock_settings.deepseek_model = "deepseek-v4"
            config = _resolve_backend_config("deepseek", QualityPreset.BALANCED)
            assert config.backend == "deepseek"
            assert config.api_key == "test-key"
            assert config.model == "deepseek-v4"

    def test_resolve_backend_config_openai(self):
        from app.api.papers import _resolve_backend_config
        from app.services.translator import QualityPreset
        with patch("app.api.papers.settings") as mock_settings:
            mock_settings.openai_api_key = "oa-key"
            mock_settings.openai_base_url = "https://api.openai.com/v1"
            mock_settings.openai_model = "gpt-4o-mini"
            config = _resolve_backend_config("openai", QualityPreset.BALANCED)
            assert config.backend == "openai"
            assert config.api_key == "oa-key"
            assert config.base_url == "https://api.openai.com/v1"
            assert config.model == "gpt-4o-mini"

    def test_resolve_backend_config_deepl(self):
        from app.api.papers import _resolve_backend_config
        from app.services.translator import QualityPreset
        with patch("app.api.papers.settings") as mock_settings:
            mock_settings.deepl_api_key = "dl-test-key"
            config = _resolve_backend_config("deepl", QualityPreset.BALANCED)
            assert config.backend == "deepl"
            assert config.api_key == "dl-test-key"

    def test_resolve_backend_config_ollama(self):
        from app.api.papers import _resolve_backend_config
        from app.services.translator import QualityPreset
        with patch("app.api.papers.settings") as mock_settings:
            mock_settings.ollama_host = "http://localhost:11434"
            config = _resolve_backend_config("ollama", QualityPreset.BALANCED)
            assert config.backend == "ollama"
            assert config.base_url == "http://localhost:11434"

    def test_resolve_backend_config_missing_api_key_raises(self):
        from fastapi import HTTPException

        from app.api.papers import _resolve_backend_config
        from app.services.translator import QualityPreset
        with patch("app.api.papers.settings") as mock_settings, \
             patch.dict("os.environ", {}, clear=True):
            mock_settings.deepseek_api_key = ""
            with pytest.raises(HTTPException) as exc_info:
                _resolve_backend_config("deepseek", QualityPreset.BALANCED)
            assert exc_info.value.status_code == 400
            assert "API key" in exc_info.value.detail

    def test_resolve_backend_config_missing_key_allowed_in_fast_mode(self):
        from app.api.papers import _resolve_backend_config
        from app.services.translator import QualityPreset
        with patch("app.api.papers.settings") as mock_settings:
            mock_settings.deepseek_api_key = ""
            config = _resolve_backend_config("deepseek", QualityPreset.FAST)
            assert config.backend == "google"


class TestRunTranslation:
    """Test _run_translation background function."""

    def _make_async_session_mock(self, db_mock):
        """Create a mock async_session factory that yields db_mock."""
        ctx = MagicMock()
        ctx.__aenter__ = AsyncMock(return_value=db_mock)
        ctx.__aexit__ = AsyncMock(return_value=False)
        return MagicMock(return_value=ctx)

    def _setup_db_mock(self, paper):
        """Create a mock DB session that returns the given paper."""
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = paper
        db = AsyncMock()
        db.execute = AsyncMock(return_value=mock_result)
        db.commit = AsyncMock()
        return db

    @patch("app.api.papers.translate_pdf_sync")
    @patch("app.api.papers.settings")
    def test_successful_translation(self, mock_settings, mock_translate, tmp_path):
        from app.api.papers import _run_translation
        from app.services.translator import TranslationResult

        paper = MagicMock()
        paper.id = "paper123"
        paper.stored_filename = "test.pdf"
        paper.translated_filename = None
        paper.dual_filename = None
        paper.translation_status = "pending"

        db = self._setup_db_mock(paper)

        papers_dir = tmp_path / "papers"
        papers_dir.mkdir()
        translations_dir = tmp_path / "translations"
        translations_dir.mkdir()
        mock_settings.papers_path = papers_dir
        mock_settings.translations_path = translations_dir

        # Create the input file so the existence check passes
        (papers_dir / "test.pdf").write_bytes(b"PDF content")

        mono_path = translations_dir / "paper123" / "test-mono.pdf"
        mono_path.parent.mkdir(parents=True)
        mono_path.write_bytes(b"translated")
        mock_translate.return_value = TranslationResult(mono_path=mono_path)

        with patch("app.core.database.async_session", self._make_async_session_mock(db)):
            _run_translation("paper123", "google", "fast")

        assert paper.translation_status == "completed"
        assert paper.translation_progress == 1.0

    @patch("app.api.papers.translate_pdf_sync")
    @patch("app.api.papers.settings")
    def test_progress_callback_updates_db(self, mock_settings, mock_translate, tmp_path):
        """Test that the progress callback updates translation_progress in DB."""
        from app.api.papers import _run_translation
        from app.services.translator import TranslationResult

        paper = MagicMock()
        paper.id = "paper123"
        paper.stored_filename = "test.pdf"
        paper.translated_filename = None
        paper.dual_filename = None
        paper.translation_status = "translating"

        db = self._setup_db_mock(paper)

        papers_dir = tmp_path / "papers"
        papers_dir.mkdir()
        translations_dir = tmp_path / "translations"
        translations_dir.mkdir()
        mock_settings.papers_path = papers_dir
        mock_settings.translations_path = translations_dir

        (papers_dir / "test.pdf").write_bytes(b"PDF content")

        mono_path = translations_dir / "paper123" / "test-mono.pdf"
        mono_path.parent.mkdir(parents=True)
        mono_path.write_bytes(b"translated")

        def fake_translate(input_path, output_dir, config, progress_callback=None):
            # Simulate pdf2zh calling the progress callback
            if progress_callback:
                progress_callback(0.5)
            return TranslationResult(mono_path=mono_path)

        mock_translate.side_effect = fake_translate

        # Mock async_session for both the main DB and the progress callback DB
        progress_db = AsyncMock()
        progress_paper = MagicMock()
        progress_paper.translation_status = "translating"
        progress_result = MagicMock()
        progress_result.scalar_one_or_none.return_value = progress_paper
        progress_db.get = AsyncMock(return_value=progress_paper)
        progress_db.commit = AsyncMock()

        call_count = [0]
        original_mock = self._make_async_session_mock(db)

        def session_factory():
            call_count[0] += 1
            if call_count[0] == 1:
                return original_mock()
            # Second call is from progress callback
            ctx = MagicMock()
            ctx.__aenter__ = AsyncMock(return_value=progress_db)
            ctx.__aexit__ = AsyncMock(return_value=False)
            return ctx

        with patch("app.core.database.async_session", side_effect=session_factory):
            _run_translation("paper123", "google", "fast")

        assert paper.translation_status == "completed"

    @patch("app.api.papers.translate_pdf_sync")
    @patch("app.api.papers.settings")
    def test_progress_callback_throttled(self, mock_settings, mock_translate, tmp_path):
        """Test that progress callback throttles DB writes to 1% increments."""
        from app.api.papers import _run_translation
        from app.services.translator import TranslationResult

        paper = MagicMock()
        paper.id = "paper123"
        paper.stored_filename = "test.pdf"
        paper.translated_filename = None
        paper.dual_filename = None
        paper.translation_status = "translating"

        db = self._setup_db_mock(paper)

        papers_dir = tmp_path / "papers"
        papers_dir.mkdir()
        translations_dir = tmp_path / "translations"
        translations_dir.mkdir()
        mock_settings.papers_path = papers_dir
        mock_settings.translations_path = translations_dir

        (papers_dir / "test.pdf").write_bytes(b"PDF content")

        mono_path = translations_dir / "paper123" / "test-mono.pdf"
        mono_path.parent.mkdir(parents=True)
        mono_path.write_bytes(b"translated")

        progress_db = AsyncMock()
        progress_paper = MagicMock()
        progress_paper.translation_status = "translating"
        progress_db.get = AsyncMock(return_value=progress_paper)
        progress_db.commit = AsyncMock()

        session_call_count = [0]
        original_mock = self._make_async_session_mock(db)

        def session_factory():
            session_call_count[0] += 1
            if session_call_count[0] == 1:
                return original_mock()
            ctx = MagicMock()
            ctx.__aenter__ = AsyncMock(return_value=progress_db)
            ctx.__aexit__ = AsyncMock(return_value=False)
            return ctx

        def fake_translate(input_path, output_dir, config, progress_callback=None):
            if progress_callback:
                # Many small increments within 1% — only first should fire
                for i in range(1, 10):
                    progress_callback(i / 1000.0)  # 0.001, 0.002, ..., 0.009
                # This crosses 1% threshold — should fire
                progress_callback(0.015)
                # Final 100% — should always fire
                progress_callback(1.0)
            return TranslationResult(mono_path=mono_path)

        mock_translate.side_effect = fake_translate

        with patch("app.core.database.async_session", side_effect=session_factory):
            _run_translation("paper123", "google", "fast")

        # Session created: 1 (main) + 2 (0.015, 1.0) = 3
        # The 9 calls from 0.001-0.009 are all within 1% of 0.0 → throttled
        assert session_call_count[0] == 3
        assert paper.translation_status == "completed"

    @patch("app.api.papers.translate_pdf_sync")
    @patch("app.api.papers.settings")
    def test_paper_not_found(self, mock_settings, mock_translate):
        from app.api.papers import _run_translation

        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = None
        db = AsyncMock()
        db.execute = AsyncMock(return_value=mock_result)

        with patch("app.core.database.async_session", self._make_async_session_mock(db)):
            _run_translation("000000000000", "google", "fast")

        mock_translate.assert_not_called()

    @patch("app.api.papers.translate_pdf_sync")
    @patch("app.api.papers.settings")
    def test_missing_input_file_sets_failed(self, mock_settings, mock_translate, tmp_path):
        from app.api.papers import _run_translation

        paper = MagicMock()
        paper.id = "paper123"
        paper.stored_filename = "test.pdf"
        paper.translation_status = "translating"

        db = self._setup_db_mock(paper)

        papers_dir = tmp_path / "papers"
        papers_dir.mkdir()
        translations_dir = tmp_path / "translations"
        translations_dir.mkdir()
        mock_settings.papers_path = papers_dir
        mock_settings.translations_path = translations_dir

        # Do NOT create the input file — simulate missing original

        with patch("app.core.database.async_session", self._make_async_session_mock(db)):
            _run_translation("paper123", "google", "fast")

        assert paper.translation_status == "failed"
        assert paper.translation_error == "Original PDF file not found"
        mock_translate.assert_not_called()

    @patch("app.api.papers.translate_pdf_sync")
    @patch("app.api.papers.settings")
    def test_path_traversal_blocked(self, mock_settings, mock_translate, tmp_path):
        from app.api.papers import _run_translation

        paper = MagicMock()
        paper.id = "paper123"
        paper.stored_filename = "../../../etc/passwd"
        paper.translation_status = "translating"

        db = self._setup_db_mock(paper)

        papers_dir = tmp_path / "papers"
        papers_dir.mkdir()
        translations_dir = tmp_path / "translations"
        translations_dir.mkdir()
        mock_settings.papers_path = papers_dir
        mock_settings.translations_path = translations_dir

        with patch("app.core.database.async_session", self._make_async_session_mock(db)):
            _run_translation("paper123", "google", "fast")

        assert paper.translation_status == "failed"
        assert paper.translation_error == "Invalid file path"
        mock_translate.assert_not_called()

    @patch("app.api.papers.translate_pdf_sync")
    @patch("app.api.papers.settings")
    def test_translation_exception_sets_failed(self, mock_settings, mock_translate, tmp_path):
        from app.api.papers import _run_translation

        paper = MagicMock()
        paper.id = "paper123"
        paper.stored_filename = "test.pdf"
        paper.translation_status = "translating"

        db = self._setup_db_mock(paper)

        papers_dir = tmp_path / "papers"
        papers_dir.mkdir()
        translations_dir = tmp_path / "translations"
        translations_dir.mkdir()
        mock_settings.papers_path = papers_dir
        mock_settings.translations_path = translations_dir

        # Create the input file so the existence check passes
        (papers_dir / "test.pdf").write_bytes(b"PDF content")

        mock_translate.side_effect = Exception("API error")

        with patch("app.core.database.async_session", self._make_async_session_mock(db)):
            _run_translation("paper123", "deepseek", "balanced")

        assert paper.translation_status == "failed"
        assert paper.translation_error is not None

    @patch("app.api.papers.translate_pdf_sync")
    @patch("app.api.papers.settings")
    def test_translation_crash_cleans_up_output_dir(self, mock_settings, mock_translate, tmp_path):
        """Test that crash handler cleans up partial output directory."""
        from app.api.papers import _run_translation

        paper = MagicMock()
        paper.id = "paper123"
        paper.stored_filename = "test.pdf"
        paper.translation_status = "translating"

        db = self._setup_db_mock(paper)

        papers_dir = tmp_path / "papers"
        papers_dir.mkdir()
        translations_dir = tmp_path / "translations"
        translations_dir.mkdir()
        mock_settings.papers_path = papers_dir
        mock_settings.translations_path = translations_dir

        (papers_dir / "test.pdf").write_bytes(b"PDF content")

        # Create partial output directory (simulating partial translation)
        output_dir = translations_dir / "paper123"
        output_dir.mkdir()
        (output_dir / "partial.pdf").write_bytes(b"partial")

        mock_translate.side_effect = Exception("API crash")

        with patch("app.core.database.async_session", self._make_async_session_mock(db)):
            _run_translation("paper123", "deepseek", "balanced")

        assert paper.translation_status == "failed"
        assert not output_dir.exists()

    @patch("app.api.papers.translate_pdf_sync")
    @patch("app.api.papers.settings")
    def test_translation_failure_cleans_up(self, mock_settings, mock_translate, tmp_path):
        from app.api.papers import _run_translation
        from app.services.translator import TranslationResult

        paper = MagicMock()
        paper.id = "paper123"
        paper.stored_filename = "test.pdf"
        paper.translation_status = "translating"

        db = self._setup_db_mock(paper)

        papers_dir = tmp_path / "papers"
        papers_dir.mkdir()
        translations_dir = tmp_path / "translations"
        translations_dir.mkdir()
        mock_settings.papers_path = papers_dir
        mock_settings.translations_path = translations_dir

        # Create the input file so the existence check passes
        (papers_dir / "test.pdf").write_bytes(b"PDF content")

        partial_dir = translations_dir / "paper123"
        partial_dir.mkdir()
        (partial_dir / "partial.pdf").write_bytes(b"partial")

        mock_translate.return_value = TranslationResult(error="Translation failed")

        with patch("app.core.database.async_session", self._make_async_session_mock(db)):
            _run_translation("paper123", "deepseek", "balanced")

        assert paper.translation_status == "failed"
        assert not partial_dir.exists()

    @patch("app.api.papers._reset_paper_status")
    @patch("app.api.papers._translation_semaphore")
    def test_semaphore_timeout_resets_paper(self, mock_semaphore, mock_reset):
        from app.api.papers import _run_translation

        mock_semaphore.acquire.return_value = False
        _run_translation("paper123", "google", "fast")
        mock_reset.assert_called_once_with(
            "paper123", "Translation queue is busy, please try again later"
        )

    @patch("app.api.papers.translate_pdf_sync")
    @patch("app.api.papers.settings")
    def test_dual_path_stored(self, mock_settings, mock_translate, tmp_path):
        from app.api.papers import _run_translation
        from app.services.translator import TranslationResult

        paper = MagicMock()
        paper.id = "paper123"
        paper.stored_filename = "test.pdf"
        paper.translated_filename = None
        paper.dual_filename = None
        paper.translation_status = "pending"

        db = self._setup_db_mock(paper)

        papers_dir = tmp_path / "papers"
        papers_dir.mkdir()
        translations_dir = tmp_path / "translations"
        translations_dir.mkdir()
        mock_settings.papers_path = papers_dir
        mock_settings.translations_path = translations_dir

        # Create the input file so the existence check passes
        (papers_dir / "test.pdf").write_bytes(b"PDF content")

        out_dir = translations_dir / "paper123"
        out_dir.mkdir()
        mono = out_dir / "test-mono.pdf"
        dual = out_dir / "test-dual.pdf"
        mono.write_bytes(b"mono")
        dual.write_bytes(b"dual")

        mock_translate.return_value = TranslationResult(mono_path=mono, dual_path=dual)

        with patch("app.core.database.async_session", self._make_async_session_mock(db)):
            _run_translation("paper123", "google", "fast")

        assert paper.translation_status == "completed"
        assert paper.translated_filename is not None
        assert paper.dual_filename is not None

    @patch("app.api.papers.translate_pdf_sync")
    @patch("app.api.papers.settings")
    def test_unhandled_exception_resets_paper_status(self, mock_settings, mock_translate):
        """Test that an unhandled exception outside _do_translate resets paper status."""
        import app.api.papers as papers_mod
        from app.api.papers import _run_translation

        paper = MagicMock()
        paper.id = "paper123"
        paper.stored_filename = "test.pdf"
        paper.translation_status = "translating"

        # Make _resolve_backend_config raise before _do_translate is defined
        with patch.object(
            papers_mod, "_resolve_backend_config",
            side_effect=Exception("Config error"),
        ):
            # Need to provide a mock db for the reset path
            reset_db = AsyncMock()
            reset_result = MagicMock()
            reset_result.scalar_one_or_none.return_value = paper
            reset_db.execute = AsyncMock(return_value=reset_result)
            reset_db.commit = AsyncMock()
            reset_ctx = MagicMock()
            reset_ctx.__aenter__ = AsyncMock(return_value=reset_db)
            reset_ctx.__aexit__ = AsyncMock(return_value=False)

            with patch("app.core.database.async_session", MagicMock(return_value=reset_ctx)):
                _run_translation("paper123", "deepseek", "balanced")

        # Paper should be marked failed, not stuck as "translating"
        assert paper.translation_status == "failed"
        assert "Unexpected" in paper.translation_error

    def test_reset_paper_status_resets_translating_paper(self):
        """Test _reset_paper_status resets a paper stuck in translating state."""
        from app.api.papers import _reset_paper_status

        paper = MagicMock()
        paper.id = "paper123"
        paper.translation_status = "translating"

        db = AsyncMock()
        result = MagicMock()
        result.scalar_one_or_none.return_value = paper
        db.execute = AsyncMock(return_value=result)
        db.commit = AsyncMock()
        ctx = MagicMock()
        ctx.__aenter__ = AsyncMock(return_value=db)
        ctx.__aexit__ = AsyncMock(return_value=False)

        with patch("app.core.database.async_session", MagicMock(return_value=ctx)):
            _reset_paper_status("paper123", "Queue busy")

        assert paper.translation_status == "failed"
        assert paper.translation_error == "Queue busy"

    def test_reset_paper_status_skips_non_translating_paper(self):
        """Test _reset_paper_status does not modify papers not in translating state."""
        from app.api.papers import _reset_paper_status

        paper = MagicMock()
        paper.id = "paper123"
        paper.translation_status = "completed"

        db = AsyncMock()
        result = MagicMock()
        result.scalar_one_or_none.return_value = paper
        db.execute = AsyncMock(return_value=result)
        db.commit = AsyncMock()
        ctx = MagicMock()
        ctx.__aenter__ = AsyncMock(return_value=db)
        ctx.__aexit__ = AsyncMock(return_value=False)

        with patch("app.core.database.async_session", MagicMock(return_value=ctx)):
            _reset_paper_status("paper123", "Queue busy")

        # Should not have been modified
        assert paper.translation_status == "completed"
        db.commit.assert_not_called()
    """Test custom validation error handling."""

    def test_value_error_returns_400(self, client, mock_db):
        """Value validation errors should return 400."""
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = MagicMock()
        mock_db.execute.return_value = mock_result

        response = client.patch(
            "/api/papers/000000000000",
            json={"title": "x" * 501},
        )
        assert response.status_code == 400
        assert "500 characters" in response.json()["detail"]

    def test_missing_field_returns_422(self, client):
        """Missing required fields should return 422."""
        response = client.post("/api/papers/upload")
        assert response.status_code == 422


class TestSecurityHeaders:
    """Test that security headers are set on all responses."""

    def test_security_headers_on_health(self, client):
        response = client.get("/health")
        assert response.headers["X-Content-Type-Options"] == "nosniff"
        assert response.headers["X-Frame-Options"] == "DENY"
        assert response.headers["X-XSS-Protection"] == "0"
        assert response.headers["Referrer-Policy"] == "strict-origin-when-cross-origin"
        assert "camera=()" in response.headers["Permissions-Policy"]
        csp = response.headers["Content-Security-Policy"]
        assert "default-src 'self'" in csp
        assert "script-src 'self'" in csp
        assert "style-src 'self'" in csp
        assert "connect-src 'self'" in csp
        assert "font-src 'self'" in csp
        assert "frame-ancestors 'none'" in csp
        assert "'unsafe-inline'" not in csp

    def test_security_headers_on_api(self, client, mock_db):
        mock_db.scalar.return_value = 0
        mock_result = MagicMock()
        mock_scalars = MagicMock()
        mock_scalars.all.return_value = []
        mock_result.scalars.return_value = mock_scalars
        mock_db.execute.return_value = mock_result

        response = client.get("/api/papers/")
        assert response.headers["X-Content-Type-Options"] == "nosniff"
        assert response.headers["X-Frame-Options"] == "DENY"

    def test_security_headers_on_404(self, client, mock_db):
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = None
        mock_db.execute.return_value = mock_result

        response = client.get("/api/papers/000000000000")
        assert response.status_code == 404
        assert response.headers["X-Content-Type-Options"] == "nosniff"
        assert response.headers["X-Frame-Options"] == "DENY"


class TestRecoverStuckTranslations:
    """Test startup recovery of stuck translations."""

    @pytest.mark.asyncio
    async def test_recovers_stuck_papers(self):
        from app.main import _recover_stuck_translations

        paper1 = MagicMock()
        paper1.translation_status = "translating"
        paper1.translation_error = None

        paper2 = MagicMock()
        paper2.translation_status = "translating"
        paper2.translation_error = None

        mock_result = MagicMock()
        mock_scalars = MagicMock()
        mock_scalars.all.return_value = [paper1, paper2]
        mock_result.scalars.return_value = mock_scalars

        mock_session = AsyncMock()
        mock_session.execute = AsyncMock(return_value=mock_result)
        mock_session.commit = AsyncMock()

        mock_ctx_manager = MagicMock()
        mock_ctx_manager.__aenter__ = AsyncMock(return_value=mock_session)
        mock_ctx_manager.__aexit__ = AsyncMock(return_value=False)
        mock_factory = MagicMock(return_value=mock_ctx_manager)
        with patch("app.core.database.async_session", mock_factory):
            await _recover_stuck_translations()

        assert paper1.translation_status == "failed"
        assert paper2.translation_status == "failed"
        assert "interrupted" in paper1.translation_error.lower()
        mock_session.commit.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_noop_when_no_stuck_papers(self):
        from app.main import _recover_stuck_translations

        mock_result = MagicMock()
        mock_scalars = MagicMock()
        mock_scalars.all.return_value = []
        mock_result.scalars.return_value = mock_scalars

        mock_session = AsyncMock()
        mock_session.execute = AsyncMock(return_value=mock_result)
        mock_session.commit = AsyncMock()

        mock_ctx_manager = MagicMock()
        mock_ctx_manager.__aenter__ = AsyncMock(return_value=mock_session)
        mock_ctx_manager.__aexit__ = AsyncMock(return_value=False)
        mock_factory = MagicMock(return_value=mock_ctx_manager)
        with patch("app.core.database.async_session", mock_factory):
            await _recover_stuck_translations()

        mock_session.commit.assert_not_awaited()


class TestEnsureDirs:
    """Test directory creation on startup."""

    def test_creates_all_directories(self, tmp_path):
        from app.core.config import Settings, ensure_dirs

        settings = Settings(
            base_dir=tmp_path,
            data_dir="test_data",
            papers_dir="test_data/papers",
            translations_dir="test_data/translations",
        )
        with patch("app.core.config.settings", settings):
            ensure_dirs()

        assert (tmp_path / "test_data").is_dir()
        assert (tmp_path / "test_data" / "papers").is_dir()
        assert (tmp_path / "test_data" / "translations").is_dir()

    def test_idempotent(self, tmp_path):
        from app.core.config import Settings, ensure_dirs

        settings = Settings(
            base_dir=tmp_path,
            data_dir="test_data",
            papers_dir="test_data/papers",
            translations_dir="test_data/translations",
        )
        with patch("app.core.config.settings", settings):
            ensure_dirs()
            ensure_dirs()  # Should not raise

        assert (tmp_path / "test_data" / "papers").is_dir()


class TestLifespan:
    """Test FastAPI lifespan context manager."""

    @pytest.mark.asyncio
    async def test_lifespan_calls_startup_functions(self):
        """Test that lifespan calls ensure_dirs, init_db, and recover_stuck."""
        from app.main import lifespan

        with (
            patch("app.main.ensure_dirs") as mock_dirs,
            patch("app.main.init_db", new_callable=AsyncMock) as mock_db,
            patch("app.main._recover_stuck_translations", new_callable=AsyncMock) as mock_recover,
        ):
            async with lifespan(MagicMock()):
                pass

        mock_dirs.assert_called_once()
        mock_db.assert_awaited_once()
        mock_recover.assert_awaited_once()


class TestInitDb:
    """Test database initialization."""

    @pytest.mark.asyncio
    async def test_init_db_creates_tables(self):
        """Test that init_db creates all tables."""
        from sqlalchemy.ext.asyncio import create_async_engine
        from sqlalchemy.pool import StaticPool

        from app.core.database import init_db

        test_engine = create_async_engine(
            "sqlite+aiosqlite://",
            poolclass=StaticPool,
            connect_args={"check_same_thread": False},
        )

        # Patch the engine used by init_db
        with patch("app.core.database.engine", test_engine):
            await init_db()

        # Verify tables were created
        import sqlalchemy as sa
        async with test_engine.connect() as conn:
            result = await conn.run_sync(
                lambda sync_conn: sync_conn.execute(
                    sa.text("SELECT name FROM sqlite_master WHERE type='table'")
                ).fetchall()
            )
            table_names = [row[0] for row in result]
            assert "papers" in table_names

        await test_engine.dispose()


class TestCliFunction:
    """Test CLI entry point."""

    @patch("uvicorn.run")
    def test_cli_default_args(self, mock_run):
        """Test CLI with default arguments."""
        from app.main import cli
        with patch("sys.argv", ["paper-china"]):
            cli()
        mock_run.assert_called_once()
        call_kwargs = mock_run.call_args
        assert call_kwargs[1]["host"] == "127.0.0.1"
        assert call_kwargs[1]["port"] == 8000

    @patch("uvicorn.run")
    def test_cli_custom_port(self, mock_run):
        """Test CLI with custom port."""
        from app.main import cli
        with patch("sys.argv", ["paper-china", "--port", "9000"]):
            cli()
        assert mock_run.call_args[1]["port"] == 9000

    @patch("uvicorn.run")
    def test_cli_non_localhost_warns(self, mock_run):
        """Test that binding to non-localhost triggers a warning."""
        from app.main import cli
        with patch("sys.argv", ["paper-china", "--host", "0.0.0.0"]), \
             patch("app.main.logger") as mock_logger:
                cli()
                mock_logger.warning.assert_called_once()
                assert "no authentication" in mock_logger.warning.call_args[0][0].lower()

    @patch("app.main.webbrowser.open")
    @patch("uvicorn.run")
    def test_cli_open_browser(self, mock_run, mock_open):
        """Test --open flag opens browser."""
        from app.main import cli
        with patch("sys.argv", ["paper-china", "--open"]):
            cli()
        mock_open.assert_called_once_with("http://127.0.0.1:8000")
