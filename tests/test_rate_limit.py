"""Tests for app.core.rate_limit module."""

import pytest
from unittest.mock import MagicMock
from starlette.testclient import TestClient
from fastapi import FastAPI

from app.core.rate_limit import RateLimitMiddleware


@pytest.fixture
def app_with_rate_limit():
    """Create a minimal app with rate limiting."""
    app = FastAPI()

    @app.get("/test")
    async def test_endpoint():
        return {"ok": True}

    @app.get("/health")
    async def health():
        return {"status": "ok"}

    @app.get("/static/file.js")
    async def static_file():
        return "js content"

    app.add_middleware(RateLimitMiddleware, requests_per_minute=3, requests_per_hour=10)
    return app


@pytest.fixture
def client(app_with_rate_limit):
    return TestClient(app_with_rate_limit)


class TestRateLimitMiddleware:
    """Test rate limiting behavior."""

    def test_allows_requests_within_limit(self, client):
        for _ in range(3):
            response = client.get("/test")
            assert response.status_code == 200

    def test_blocks_requests_over_limit(self, client):
        for _ in range(3):
            client.get("/test")
        response = client.get("/test")
        assert response.status_code == 429
        assert "Rate limit" in response.json()["detail"]

    def test_skips_health_endpoint(self, client):
        for _ in range(10):
            response = client.get("/health")
            assert response.status_code == 200

    def test_skips_static_files(self, client):
        for _ in range(10):
            response = client.get("/static/file.js")
            assert response.status_code == 200

    def test_returns_retry_after_header(self, client):
        for _ in range(3):
            client.get("/test")
        response = client.get("/test")
        assert response.status_code == 429
        assert "Retry-After" in response.headers


class TestGetClientIp:
    """Test IP extraction."""

    def test_uses_client_host_by_default(self):
        app = FastAPI()
        middleware = RateLimitMiddleware(app, trust_proxy=False)
        request = MagicMock()
        request.headers = {}
        request.client.host = "1.2.3.4"
        assert middleware._get_client_ip(request) == "1.2.3.4"

    def test_ignores_forwarded_for_when_not_trusting_proxy(self):
        app = FastAPI()
        middleware = RateLimitMiddleware(app, trust_proxy=False)
        request = MagicMock()
        request.headers = {"X-Forwarded-For": "5.6.7.8"}
        request.client.host = "1.2.3.4"
        assert middleware._get_client_ip(request) == "1.2.3.4"

    def test_uses_forwarded_for_when_trusting_proxy(self):
        app = FastAPI()
        middleware = RateLimitMiddleware(app, trust_proxy=True)
        request = MagicMock()
        request.headers = {"X-Forwarded-For": "5.6.7.8, 9.10.11.12"}
        request.client.host = "1.2.3.4"
        assert middleware._get_client_ip(request) == "5.6.7.8"

    def test_fallback_to_unknown_without_client(self):
        app = FastAPI()
        middleware = RateLimitMiddleware(app)
        request = MagicMock()
        request.headers = {}
        request.client = None
        assert middleware._get_client_ip(request) == "unknown"


class TestReset:
    """Test reset functionality."""

    def test_reset_clears_state(self):
        app = FastAPI()
        middleware = RateLimitMiddleware(app)
        middleware._minute_requests["1.2.3.4"] = [1.0, 2.0, 3.0]
        middleware._hour_requests["1.2.3.4"] = [1.0, 2.0]
        middleware.reset()
        assert len(middleware._minute_requests) == 0
        assert len(middleware._hour_requests) == 0
