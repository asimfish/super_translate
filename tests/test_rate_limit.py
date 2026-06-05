"""Tests for app.core.rate_limit module."""

from unittest.mock import MagicMock

import pytest
from fastapi import FastAPI
from starlette.testclient import TestClient

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


class TestHourlyLimit:
    """Test hourly rate limit enforcement."""

    def test_blocks_when_hourly_limit_exceeded(self):
        import time
        app = FastAPI()
        middleware = RateLimitMiddleware(app, requests_per_minute=100, requests_per_hour=5)
        now = time.time()
        # Pre-fill hour requests to just under the limit
        middleware._hour_requests["1.2.3.4"] = [now - 300, now - 200, now - 100, now - 50, now - 10]
        allowed, msg = middleware._check_rate_limit("1.2.3.4")
        assert not allowed
        assert "5 requests per hour" in msg

    def test_allows_under_hourly_limit(self):
        import time
        app = FastAPI()
        middleware = RateLimitMiddleware(app, requests_per_minute=100, requests_per_hour=10)
        now = time.time()
        middleware._hour_requests["1.2.3.4"] = [now - 300, now - 200, now - 100]
        allowed, msg = middleware._check_rate_limit("1.2.3.4")
        assert allowed


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


class TestCleanup:
    """Test old entry cleanup."""

    def test_cleanup_removes_expired_entries(self):
        import time
        app = FastAPI()
        middleware = RateLimitMiddleware(app)
        now = time.time()
        # Add expired entries (older than 60s for minute, 3600s for hour)
        middleware._minute_requests["1.2.3.4"] = [now - 120, now - 90]
        middleware._hour_requests["1.2.3.4"] = [now - 7200, now - 3700]
        # Force cleanup by setting last_cleanup to past
        middleware._last_cleanup = now - 120
        middleware._cleanup_old_entries()
        assert len(middleware._minute_requests) == 0
        assert len(middleware._hour_requests) == 0

    def test_cleanup_keeps_recent_entries(self):
        import time
        app = FastAPI()
        middleware = RateLimitMiddleware(app)
        now = time.time()
        # Mix of expired and recent entries
        middleware._minute_requests["1.2.3.4"] = [now - 120, now - 10]
        middleware._hour_requests["1.2.3.4"] = [now - 7200, now - 100]
        middleware._last_cleanup = now - 120
        middleware._cleanup_old_entries()
        assert len(middleware._minute_requests["1.2.3.4"]) == 1
        assert len(middleware._hour_requests["1.2.3.4"]) == 1

    def test_cleanup_skips_if_recent(self):
        import time
        app = FastAPI()
        middleware = RateLimitMiddleware(app)
        now = time.time()
        middleware._minute_requests["1.2.3.4"] = [now - 120]
        middleware._last_cleanup = now - 10  # Recent cleanup
        middleware._cleanup_old_entries()
        # Should NOT have cleaned up because last_cleanup was recent
        assert len(middleware._minute_requests["1.2.3.4"]) == 1


class TestIpCap:
    """Test IP tracking cap to prevent memory exhaustion."""

    @pytest.mark.asyncio
    async def test_rejects_new_ips_when_at_cap(self):
        import time
        from unittest.mock import patch as _patch

        app = FastAPI()
        middleware = RateLimitMiddleware(app, requests_per_minute=100, requests_per_hour=1000)
        now = time.time()
        # Fill up to the cap with distinct IPs (use small cap for test speed)
        cap = 5
        for i in range(cap):
            middleware._minute_requests[f"10.0.0.{i}"] = [now]
        middleware._last_cleanup = now

        request = MagicMock()
        request.headers = {}
        request.client.host = "192.168.1.1"
        request.url.path = "/test"

        async def call_next(req):
            return MagicMock(status_code=200)

        with _patch("app.core.rate_limit._MAX_TRACKED_IPS", cap):
            response = await middleware.dispatch(request, call_next)
        assert response.status_code == 429
        assert "Too many clients" in response.body.decode()

    @pytest.mark.asyncio
    async def test_allows_existing_ip_at_cap(self):
        import time
        from unittest.mock import patch as _patch

        app = FastAPI()
        middleware = RateLimitMiddleware(app, requests_per_minute=100, requests_per_hour=1000)
        now = time.time()
        cap = 5
        for i in range(cap - 1):
            middleware._minute_requests[f"10.0.0.{i}"] = [now]
        middleware._minute_requests["192.168.1.1"] = [now]
        middleware._last_cleanup = now

        request = MagicMock()
        request.headers = {}
        request.client.host = "192.168.1.1"
        request.url.path = "/test"

        async def call_next(req):
            return MagicMock(status_code=200)

        with _patch("app.core.rate_limit._MAX_TRACKED_IPS", cap):
            response = await middleware.dispatch(request, call_next)
        assert response.status_code == 200
