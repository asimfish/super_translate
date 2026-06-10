"""In-memory rate limiting middleware."""

import time
from collections import defaultdict
from collections.abc import Callable

from fastapi import Request, Response
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse

# Maximum number of distinct client IPs to track simultaneously.
# Prevents unbounded memory growth from spoofed-source DDoS.
_MAX_TRACKED_IPS = 10_000

# Timing constants (seconds)
_CLEANUP_INTERVAL = 60
_MINUTE_WINDOW = 60
_HOUR_WINDOW = 3600


class RateLimitMiddleware(BaseHTTPMiddleware):
    """Sliding window rate limiter.

    Tracks request counts per client IP within a time window.
    Only trusts X-Forwarded-For when trust_proxy is True.
    """

    def __init__(
        self,
        app,
        *,
        requests_per_minute: int = 60,
        requests_per_hour: int = 500,
        window_seconds: int = 60,
        trust_proxy: bool = False,
    ):
        super().__init__(app)
        self.requests_per_minute = requests_per_minute
        self.requests_per_hour = requests_per_hour
        self.window_seconds = window_seconds
        self.trust_proxy = trust_proxy
        self._minute_requests: dict[str, list[float]] = defaultdict(list)
        self._hour_requests: dict[str, list[float]] = defaultdict(list)
        self._last_cleanup = time.time()

    def reset(self) -> None:
        """Reset rate limit state (for testing)."""
        self._minute_requests.clear()
        self._hour_requests.clear()
        self._last_cleanup = time.time()

    def _get_client_ip(self, request: Request) -> str:
        """Extract client IP from request.

        Only trusts X-Forwarded-For when trust_proxy is True to prevent
        attackers from spoofing their IP to bypass rate limiting.
        """
        if self.trust_proxy:
            forwarded = request.headers.get("X-Forwarded-For")
            if forwarded:
                return forwarded.split(",")[0].strip()
        return request.client.host if request.client else "unknown"

    def _cleanup_old_entries(self) -> None:
        """Remove expired entries to prevent memory leaks."""
        now = time.time()
        if now - self._last_cleanup < _CLEANUP_INTERVAL:
            return

        self._last_cleanup = now
        cutoff_minute = now - _MINUTE_WINDOW
        cutoff_hour = now - _HOUR_WINDOW

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

    def _check_rate_limit(self, client_ip: str) -> tuple[bool, str, int]:
        """Check if request is within rate limits.

        Returns (allowed, error_message, retry_after_seconds).
        """
        now = time.time()
        cutoff_minute = now - _MINUTE_WINDOW
        cutoff_hour = now - _HOUR_WINDOW

        # Clean old entries for this IP
        self._minute_requests[client_ip] = [
            t for t in self._minute_requests[client_ip] if t > cutoff_minute
        ]
        self._hour_requests[client_ip] = [
            t for t in self._hour_requests[client_ip] if t > cutoff_hour
        ]

        # Check minute limit
        if len(self._minute_requests[client_ip]) >= self.requests_per_minute:
            oldest = min(self._minute_requests[client_ip])
            retry_after = max(int(_MINUTE_WINDOW - (now - oldest)) + 1, 1)
            msg = f"Rate limit exceeded: {self.requests_per_minute} requests per minute"
            return False, msg, retry_after

        # Check hour limit
        if len(self._hour_requests[client_ip]) >= self.requests_per_hour:
            oldest = min(self._hour_requests[client_ip])
            retry_after = max(int(_HOUR_WINDOW - (now - oldest)) + 1, 1)
            msg = f"Rate limit exceeded: {self.requests_per_hour} requests per hour"
            return False, msg, retry_after

        # Record this request
        self._minute_requests[client_ip].append(now)
        self._hour_requests[client_ip].append(now)

        return True, "", 0

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

        # Reject if tracking too many distinct IPs (likely DDoS with spoofed sources)
        if (
            client_ip not in self._minute_requests
            and len(self._minute_requests) >= _MAX_TRACKED_IPS
        ):
            # Force cleanup to reclaim stale entries before rejecting
            self._last_cleanup = 0
            self._cleanup_old_entries()
            if len(self._minute_requests) >= _MAX_TRACKED_IPS:
                return JSONResponse(
                    status_code=429,
                    content={"detail": "Too many clients, try again later"},
                    headers={"Retry-After": "60"},
                )

        allowed, error_msg, retry_after = self._check_rate_limit(client_ip)
        if not allowed:
            return JSONResponse(
                status_code=429,
                content={"detail": error_msg},
                headers={"Retry-After": str(retry_after)},
            )

        return await call_next(request)
