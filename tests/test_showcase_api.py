"""Tests for the public benchmark showcase endpoints."""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient

import app.main as app_main
from app.main import app


@pytest.fixture
def client():
    from app.core.rate_limit import RateLimitMiddleware

    def _find_middleware(stack, cls):
        if isinstance(stack, cls):
            return stack
        if hasattr(stack, "app"):
            return _find_middleware(stack.app, cls)
        return None

    if app.middleware_stack is None:
        app.middleware_stack = app.build_middleware_stack()
    rate_limiter = _find_middleware(app.middleware_stack, RateLimitMiddleware)
    if rate_limiter:
        rate_limiter.reset()

    with (
        patch(
            "app.main._recover_stuck_translations",
            new_callable=AsyncMock,
            return_value=[],
        ),
        TestClient(app) as test_client,
    ):
        yield test_client


@pytest.fixture
def benchmark_dir(tmp_path, monkeypatch):
    """Point the showcase endpoints at a temporary benchmark directory."""
    from app.core.config import settings

    monkeypatch.setattr(settings, "base_dir", tmp_path)
    bench = tmp_path / app_main._BENCHMARK_DIR
    (bench / "previews" / "otf").mkdir(parents=True)
    (bench / "previews" / "closed").mkdir(parents=True)
    payload = {
        "generated_at": "2026-08-12T00:00:00+00:00",
        "papers": [
            {
                "id": "otf",
                "arxiv_id": "0000.00000",
                "title": "Open paper",
                "showcase_ok": True,
                "pages": 3,
                "visual_score": 0.9,
                "error_count": 1,
                "issues_by_code": {"font_size_drift": 1},
                "strict_pass": False,
                "legacy_pass": True,
                "previews": [{"page": 1}],
            },
            {
                "id": "closed",
                "arxiv_id": "1111.11111",
                "title": "Restricted paper",
                "showcase_ok": False,
                "pages": 2,
                "visual_score": 0.8,
                "error_count": 0,
                "issues_by_code": {},
                "strict_pass": True,
                "legacy_pass": True,
                "previews": [],
            },
        ],
        "comparison": {
            "baseline": {"error_count": 10, "strict_pass": 0, "legacy_pass": 1},
            "current": {"error_count": 4, "strict_pass": 1, "legacy_pass": 2},
        },
    }
    (bench / "showcase.json").write_text(
        json.dumps(payload, ensure_ascii=False), encoding="utf-8"
    )
    jpg = bytes.fromhex("ffd8ffdb004300ffffd9")
    (bench / "previews" / "otf" / "p001_original.jpg").write_bytes(jpg)
    (bench / "previews" / "otf" / "p001_translated.jpg").write_bytes(jpg)
    (bench / "previews" / "closed" / "p001_original.jpg").write_bytes(jpg)
    return bench


class TestShowcaseData:
    def test_page_is_served(self, client):
        response = client.get("/showcase")
        assert response.status_code == 200
        # Page title is Chinese ("translation quality benchmark").
        assert (
            "\u7ffb\u8bd1\u8d28\u91cf\u57fa\u51c6" in response.text
            or "showcase" in response.text.lower()
        )

    def test_data_endpoint_returns_payload(self, client, benchmark_dir):
        response = client.get("/api/showcase")
        assert response.status_code == 200
        body = response.json()
        assert {paper["id"] for paper in body["papers"]} == {"otf", "closed"}
        assert body["comparison"]["current"]["error_count"] == 4

    def test_data_endpoint_404_when_missing(self, client, tmp_path, monkeypatch):
        from app.core.config import settings

        monkeypatch.setattr(settings, "base_dir", tmp_path)
        response = client.get("/api/showcase")
        assert response.status_code == 404

    def test_showcase_is_public_even_with_api_token_configured(
        self, client, benchmark_dir, monkeypatch
    ):
        """The showcase bypasses the bearer-token wall; other APIs do not."""
        from app.core.config import settings

        from pydantic import SecretStr

        monkeypatch.setattr(settings, "api_token", SecretStr("secret-token"))
        assert client.get("/api/showcase").status_code == 200
        assert (
            client.get(
                "/api/showcase/previews/otf/p001_original.jpg"
            ).status_code
            == 200
        )

    def test_showcase_page_has_no_inline_script(self, client):
        """Production CSP is script-src 'self'; inline script would be dead."""
        response = client.get("/showcase")
        assert "<script src=" in response.text
        assert "<script>" not in response.text


class TestShowcasePreviews:
    def test_open_paper_preview_served(self, client, benchmark_dir):
        response = client.get("/api/showcase/previews/otf/p001_original.jpg")
        assert response.status_code == 200
        assert response.headers["content-type"] == "image/jpeg"

    def test_license_restricted_paper_is_403(self, client, benchmark_dir):
        response = client.get("/api/showcase/previews/closed/p001_original.jpg")
        assert response.status_code == 403

    def test_authenticated_operator_previews_non_cc_paper(
        self, client, benchmark_dir, monkeypatch
    ):
        from app.core.config import settings

        from pydantic import SecretStr

        monkeypatch.setattr(settings, "api_token", SecretStr("secret-token"))
        anonymous = client.get("/api/showcase/previews/closed/p001_original.jpg")
        assert anonymous.status_code == 403
        authed = client.get(
            "/api/showcase/previews/closed/p001_original.jpg",
            headers={"Authorization": "Bearer secret-token"},
        )
        assert authed.status_code == 200
        payload = client.get(
            "/api/showcase", headers={"Authorization": "Bearer secret-token"}
        ).json()
        assert payload["authenticated"] is True
        assert client.get("/api/showcase").json()["authenticated"] is False

    def test_unknown_paper_is_403(self, client, benchmark_dir):
        response = client.get("/api/showcase/previews/nope/p001_original.jpg")
        assert response.status_code == 403

    @pytest.mark.parametrize(
        "name",
        ["../secret.jpg", "p001_original.png", "p1_original.jpg", "evil.jpg"],
    )
    def test_bad_names_rejected(self, client, benchmark_dir, name):
        response = client.get(f"/api/showcase/previews/otf/{name}")
        assert response.status_code in (403, 404)

    def test_missing_file_is_404(self, client, benchmark_dir):
        response = client.get("/api/showcase/previews/otf/p002_original.jpg")
        assert response.status_code == 404


class TestShowcasePublicAccess:
    def test_showcase_api_needs_no_token(self, client, benchmark_dir, monkeypatch):
        # The showcase endpoints are public: an API token being configured
        # must not lock the aggregate metrics behind authentication.
        from pydantic import SecretStr

        from app.core.config import settings

        monkeypatch.setattr(settings, "api_token", SecretStr("secret-token"))
        response = client.get("/api/showcase")
        assert response.status_code == 200
        response = client.get("/api/papers")
        assert response.status_code == 401
