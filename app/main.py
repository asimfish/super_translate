"""Main application entry point."""

import asyncio
import logging
import time
import webbrowser
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.base import RequestResponseEndpoint
from starlette.responses import Response

from app import __version__
from app.api.papers import router as papers_router
from app.core.config import ensure_dirs, settings
from app.core.database import init_db
from app.core.rate_limit import RateLimitMiddleware

# Stats cache with async lock
_stats_cache: dict | None = None
_stats_cache_time: float = 0.0
_stats_lock = asyncio.Lock()

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logger = logging.getLogger(__name__)


async def _recover_stuck_translations() -> None:
    """Reset papers stuck in 'translating' status after a crash.

    On startup, any paper with translation_status='translating' is marked
    as failed, since the translation process no longer exists.
    """
    from sqlalchemy import update as sa_update

    from app.core.database import async_session
    from app.models.paper import Paper, TranslationStatus

    async with async_session() as db:
        result = await db.execute(
            sa_update(Paper)
            .where(Paper.translation_status == TranslationStatus.TRANSLATING.value)
            .values(
                translation_status=TranslationStatus.FAILED.value,
                translation_error="Translation was interrupted (server restart)",
            )
        )
        if result.rowcount > 0:
            try:
                await db.commit()
                logger.info("Recovered %d stuck translation(s)", result.rowcount)
            except Exception:
                await db.rollback()
                logger.exception("Failed to recover stuck translations")


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    ensure_dirs()
    await init_db()
    await _recover_stuck_translations()
    logger.info("Paper China started at http://localhost:8000")
    yield


app = FastAPI(title="Paper China", version=__version__, lifespan=lifespan)

# CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins.split(","),
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Rate limiting
app.add_middleware(
    RateLimitMiddleware,
    requests_per_minute=settings.rate_limit_per_minute,
    requests_per_hour=settings.rate_limit_per_hour,
)

# Response compression
app.add_middleware(GZipMiddleware, minimum_size=1000)


@app.middleware("http")
async def add_security_headers(request: Request, call_next: RequestResponseEndpoint) -> Response:
    response = await call_next(request)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["X-XSS-Protection"] = "0"
    response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
    response.headers["Permissions-Policy"] = "camera=(), microphone=(), geolocation=()"
    response.headers["Content-Security-Policy"] = (
        "default-src 'self'; "
        "script-src 'self'; "
        "style-src 'self'; "
        "font-src 'self'; "
        "img-src 'self' data:; "
        "connect-src 'self'; "
        "object-src 'none'; "
        "base-uri 'self'; "
        "frame-ancestors 'none'"
    )
    return response


@app.exception_handler(RequestValidationError)
async def validation_exception_handler(
    request: Request, exc: RequestValidationError
) -> JSONResponse:
    """Return 400 for value validation errors, 422 for missing fields."""
    errors = exc.errors()
    if errors:
        err_type = errors[0].get("type", "")
        msg = errors[0].get("msg", "Validation error")
        # Only convert value errors to 400; missing fields stay 422
        if err_type.startswith("value_error"):
            if msg.startswith("Value error, "):
                msg = msg[len("Value error, "):]
            return JSONResponse(status_code=400, content={"detail": msg})
    # Missing fields get standard 422 response
    return JSONResponse(status_code=422, content={"detail": errors})


app.include_router(papers_router)

static_dir = Path(__file__).parent / "static"
app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")


@app.get("/", response_class=HTMLResponse)
async def index() -> HTMLResponse:
    html_path = static_dir / "index.html"
    if html_path.exists():
        return html_path.read_text(encoding="utf-8")
    return "<h1>Paper China</h1><p>Static files not found.</p>"


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok", "version": __version__}


@app.get("/api/stats")
async def stats() -> dict[str, int]:
    """Get system statistics with caching.

    Returns cached stats for 30 seconds to reduce database queries.
    Thread-safe: uses a module-level lock to prevent race conditions.
    """
    global _stats_cache, _stats_cache_time
    from sqlalchemy import func, select

    from app.core.database import async_session
    from app.models.paper import Paper

    now = time.time()

    async with _stats_lock:
        # Return cached result if fresh
        if _stats_cache and (now - _stats_cache_time) < 30:
            return _stats_cache

        async with async_session() as db:
            total = await db.scalar(select(func.count(Paper.id)))
            completed = await db.scalar(
                select(func.count(Paper.id)).where(Paper.translation_status == "completed")
            )
            result = {
                "total_papers": total or 0,
                "completed_translations": completed or 0,
            }

            _stats_cache = result
            _stats_cache_time = now

            return result


def cli() -> None:
    import argparse

    import uvicorn

    parser = argparse.ArgumentParser(description="Paper China - AI Paper Translation System")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--open", action="store_true", help="Open browser on start")
    args = parser.parse_args()

    if args.host not in ("127.0.0.1", "localhost", "::1"):
        logger.warning(
            "Binding to %s — API has no authentication! Ensure network is trusted.",
            args.host,
        )

    if args.open:
        webbrowser.open(f"http://{args.host}:{args.port}")

    uvicorn.run("app.main:app", host=args.host, port=args.port, reload=settings.debug)


if __name__ == "__main__":
    cli()
