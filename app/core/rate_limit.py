"""In-memory rate limiting middleware."""

import time
from collections import defaultdict
from typing import Callable

from fastapi import Request, Response
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse


class RateLimitMiddleware(BaseHTTPMiddleware):
    """Sliding window rate limiter.

    Tracks request counts per client IP within a time window.
    """

    def __init__(
        self,
        app,
        requests_per_minute: int = 60,
        requests_per_hour: int = 500,
        window_seconds: int = 60,
    ):
        super().__init__(app)
        self.requests_per_minute = requests_per_minute
        self.requests_per_hour = requests_per_hour
        self.window_seconds = window_seconds
        # {ip: [(timestamp, ...), ...]}
        self._minute_requests: dict[str, list[float]] = defaultdict(list)
        self._hour_requests: dict[str, list[float]] = defaultdict(list)
        self._last_cleanup = time.time()

    def reset(self) -> None:
        """Reset rate limit state (for testing)."""
        self._minute_requests.clear()
        self._hour_requests.clear()
        self._last_cleanup = time.time()

    def _get_client_ip(self, request: Request) -> str:
        """Extract client IP from request."""
        forwarded = request.headers.get("X-Forwarded-For")
        if forwarded:
            return forwarded.split(",")[0].strip()
        return request.client.host if request.client else "unknown"

    def _cleanup_old_entries(self) -> None:
        """Remove expired entries to prevent memory leaks."""
        now = time.time()
        if now - self._last_cleanup < 60:
            return

        self._last_cleanup = now
        cutoff_minute = now - 60
        cutoff_hour = now - 3600

        # Clean minute requests
        for ip in list(self._minute_requests.keys()):
            self._minute_requests[ip] = [
                t for t in self._minute_requests[ip] if t > cutoff_minute
            ]
            if not self._minute_requests[ip]:
                del self._minute_requests[ip]

        # Clean hour requests
        for ip in list(self._hour_requests.keys()):
            self._hour_requests[ip] = [
                t for t in self._hour_requests[ip] if t > cutoff_hour
            ]
            if not self._hour_requests[ip]:
                del self._hour_requests[ip]

    def _check_rate_limit(self, client_ip: str) -> tuple[bool, str]:
        """Check if request is within rate limits.

        Returns (allowed, error_message).
        """
        now = time.time()
        cutoff_minute = now - 60
        cutoff_hour = now - 3600

        # Clean old entries for this IP
        self._minute_requests[client_ip] = [
            t for t in self._minute_requests[client_ip] if t > cutoff_minute
        ]
        self._hour_requests[client_ip] = [
            t for t in self._hour_requests[client_ip] if t > cutoff_hour
        ]

        # Check minute limit
        if len(self._minute_requests[client_ip]) >= self.requests_per_minute:
            return False, f"Rate limit exceeded: {self.requests_per_minute} requests per minute"

        # Check hour limit
        if len(self._hour_requests[client_ip]) >= self.requests_per_hour:
            return False, f"Rate limit exceeded: {self.requests_per_hour} requests per hour"

        # Record this request
        self._minute_requests[client_ip].append(now)
        self._hour_requests[client_ip].append(now)

        return True, ""

    async def dispatch(self, request: Request, call_next: Callable) -> Response:
        """Process request with rate limiting."""
        # Skip rate limiting for health checks and static files
        if request.url.path in ("/health", "/"):
            return await call_next(request)

        # Skip rate limiting for static files
        if request.url.path.startswith("/static/"):
            return await call_next(request)

        client_ip = self._get_client_ip(request)
        self._cleanup_old_entries()

        allowed, error_msg = self._check_rate_limit(client_ip)
        if not allowed:
            return JSONResponse(
                status_code=429,
                content={"detail": error_msg},
                headers={"Retry-After": "60"},
            )

        return await call_next(request)
