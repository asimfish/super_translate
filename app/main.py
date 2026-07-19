"""Main application entry point."""

import asyncio
import logging
import time
import webbrowser
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Annotated

from fastapi import Depends, FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.base import RequestResponseEndpoint
from starlette.responses import Response

from app import __version__
from app.api.papers import router as papers_router
from app.core.access import access_decision_for_request, get_request_access_scope
from app.core.config import ensure_dirs, settings
from app.core.database import init_db
from app.core.rate_limit import RateLimitMiddleware

# Stats cache with async lock, partitioned by workspace access scope so one
# workspace can never observe another workspace's aggregate counters.
_STATS_CACHE_TTL = 30  # seconds
_stats_cache: dict[str, dict[str, int]] = {}
_stats_cache_time: dict[str, float] = {}
_stats_lock = asyncio.Lock()
_startup_translation_tasks: set[asyncio.Task] = set()

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logger = logging.getLogger(__name__)


def _translation_job_resume_payload(job) -> dict[str, object]:
    """Serialize a queued translation job into _run_translation arguments."""
    return {
        "paper_id": job.paper_id,
        "backend": job.backend,
        "quality": job.quality,
        "preserve_graphics_text": job.preserve_graphics_text,
        "skip_overflow": job.skip_overflow,
        "qa_mode": job.qa_mode,
        "qa_max_passes": job.qa_max_passes,
        "ocr_mode": job.ocr_mode,
        "ocr_language": job.ocr_language,
        "ocr_dpi": job.ocr_dpi,
        "job_id": job.id,
    }


def _schedule_recovered_translation(
    payload: dict[str, object],
    delay_seconds: float | None = None,
) -> None:
    """Resume a durable queued translation job after startup."""
    from app.api.papers import _run_translation

    async def _run_after_delay() -> None:
        delay = (
            settings.resume_queued_translations_delay_seconds
            if delay_seconds is None
            else delay_seconds
        )
        if delay > 0:
            await asyncio.sleep(delay)
        await asyncio.to_thread(
            _run_translation,
            payload["paper_id"],
            payload["backend"],
            payload["quality"],
            payload["preserve_graphics_text"],
            payload["skip_overflow"],
            payload["qa_mode"],
            payload["qa_max_passes"],
            payload["ocr_mode"],
            payload["ocr_language"],
            payload["ocr_dpi"],
            payload["job_id"],
        )

    task = asyncio.create_task(_run_after_delay())
    _startup_translation_tasks.add(task)
    task.add_done_callback(_startup_translation_tasks.discard)


async def _recover_stuck_translations(
    *,
    resume_queued: bool = True,
) -> list[dict[str, object]]:
    """Recover durable translation records after a crash.

    Queued jobs and interrupted running jobs are safe to resume when startup
    recovery is enabled. The translation runner cleans the output directory
    before a re-run, so stale partial PDFs are discarded instead of reused.
    """
    from sqlalchemy import func, select
    from sqlalchemy import update as sa_update

    from app.core.database import async_session
    from app.models.paper import (
        Paper,
        TranslationJob,
        TranslationJobStatus,
        TranslationStatus,
    )

    async with async_session() as db:
        queued_result = await db.execute(
            select(TranslationJob)
            .join(Paper, Paper.id == TranslationJob.paper_id)
            .where(TranslationJob.status == TranslationJobStatus.QUEUED.value)
            .where(Paper.translation_status == TranslationStatus.TRANSLATING.value)
            .order_by(TranslationJob.created_at.asc())
        )
        queued_jobs = list(queued_result.scalars().all())
        running_result = await db.execute(
            select(TranslationJob)
            .join(Paper, Paper.id == TranslationJob.paper_id)
            .where(TranslationJob.status == TranslationJobStatus.RUNNING.value)
            .where(Paper.translation_status == TranslationStatus.TRANSLATING.value)
            .order_by(TranslationJob.started_at.asc(), TranslationJob.created_at.asc())
        )
        running_jobs = list(running_result.scalars().all())
        resumable_jobs = [*queued_jobs, *running_jobs] if resume_queued else []
        resume_payloads = [_translation_job_resume_payload(job) for job in resumable_jobs]
        resumable_paper_ids = list(
            dict.fromkeys(str(payload["paper_id"]) for payload in resume_payloads)
        )
        resumable_job_ids = [str(job.id) for job in resumable_jobs]

        paper_update = sa_update(Paper).where(
            Paper.translation_status == TranslationStatus.TRANSLATING.value
        )
        if resumable_paper_ids:
            paper_update = paper_update.where(Paper.id.not_in(resumable_paper_ids))
        paper_result = await db.execute(
            paper_update.values(
                translation_status=TranslationStatus.FAILED.value,
                translation_error="Translation was interrupted (server restart)",
            ),
        )
        resumed_job_result = None
        failed_running_job_result = None
        if resume_queued and resumable_job_ids:
            resumed_job_result = await db.execute(
                sa_update(TranslationJob)
                .where(
                    TranslationJob.id.in_(resumable_job_ids),
                    TranslationJob.status.in_(
                        [
                            TranslationJobStatus.QUEUED.value,
                            TranslationJobStatus.RUNNING.value,
                        ]
                    ),
                )
                .values(
                    status=TranslationJobStatus.QUEUED.value,
                    progress=0.0,
                    cancel_requested=False,
                    heartbeat_at=None,
                    started_at=None,
                    finished_at=None,
                    error=None,
                    updated_at=func.now(),
                ),
            )
        stale_job_result = None
        if resume_queued:
            stale_job_update = sa_update(TranslationJob).where(
                TranslationJob.status.in_(
                    [
                        TranslationJobStatus.QUEUED.value,
                        TranslationJobStatus.RUNNING.value,
                    ]
                )
            )
            if resumable_job_ids:
                stale_job_update = stale_job_update.where(
                    TranslationJob.id.not_in(resumable_job_ids)
                )
            stale_job_result = await db.execute(
                stale_job_update.values(
                    status=TranslationJobStatus.FAILED.value,
                    error=(
                        "Translation job no longer has an active translating paper "
                        "after server restart"
                    ),
                    finished_at=func.now(),
                    updated_at=func.now(),
                ),
            )
        elif not resume_queued:
            failed_running_job_result = await db.execute(
                sa_update(TranslationJob)
                .where(TranslationJob.status == TranslationJobStatus.RUNNING.value)
                .values(
                    status=TranslationJobStatus.FAILED.value,
                    error="Translation was interrupted (server restart)",
                    finished_at=func.now(),
                    updated_at=func.now(),
                ),
            )
        queued_job_result = None
        if not resume_queued:
            queued_job_result = await db.execute(
                sa_update(TranslationJob)
                .where(
                    TranslationJob.status == TranslationJobStatus.QUEUED.value,
                )
                .values(
                    status=TranslationJobStatus.FAILED.value,
                    error="Queued translation was not resumed automatically after server restart",
                    finished_at=func.now(),
                    updated_at=func.now(),
                ),
            )
        refreshed = 0
        if resume_queued and resumable_paper_ids:
            resumable_paper_result = await db.execute(
                sa_update(Paper)
                .where(Paper.id.in_(resumable_paper_ids))
                .values(
                    translation_status=TranslationStatus.TRANSLATING.value,
                    translation_error=None,
                    translation_progress=0.0,
                    translation_stage="等待恢复",
                    translation_eta_seconds=None,
                ),
            )
            refreshed = resumable_paper_result.rowcount or 0
        recovered = (
            (paper_result.rowcount or 0)
            + ((resumed_job_result.rowcount or 0) if resumed_job_result is not None else 0)
            + ((stale_job_result.rowcount or 0) if stale_job_result is not None else 0)
            + (
                (failed_running_job_result.rowcount or 0)
                if failed_running_job_result is not None
                else 0
            )
            + ((queued_job_result.rowcount or 0) if queued_job_result is not None else 0)
            + refreshed
        )
        if recovered > 0:
            try:
                await db.commit()
                logger.info("Recovered %d stuck translation record(s)", recovered)
            except Exception:
                await db.rollback()
                logger.exception("Failed to recover stuck translations")
                return []
        return resume_payloads if resume_queued else []


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    ensure_dirs()
    await init_db()
    resume_queued = settings.resume_queued_translations_on_startup
    recovered_jobs = await _recover_stuck_translations(resume_queued=resume_queued)
    for payload in recovered_jobs:
        _schedule_recovered_translation(payload)
    if recovered_jobs:
        logger.info("Resumed %d translation job(s)", len(recovered_jobs))
    elif not resume_queued:
        logger.info("Startup translation resume is disabled")
    logger.info("Super Translate started at http://localhost:8000")
    yield


app = FastAPI(title="Super Translate", version=__version__, lifespan=lifespan)

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
    trust_proxy=settings.trust_proxy,
)

# Response compression
app.add_middleware(GZipMiddleware, minimum_size=1000)


@app.middleware("http")
async def enforce_api_access(request: Request, call_next: RequestResponseEndpoint) -> Response:
    if not request.url.path.startswith("/api/"):
        return await call_next(request)
    if request.method == "OPTIONS":
        return await call_next(request)

    decision = access_decision_for_request(request)
    if not decision.allowed:
        return JSONResponse(status_code=decision.status_code, content={"detail": decision.detail})

    return await call_next(request)


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
        "worker-src 'self' blob:; "
        "style-src 'self' 'unsafe-inline'; "
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
    request: Request,
    exc: RequestValidationError,
) -> JSONResponse:
    """Return 400 for value validation errors, 422 for missing fields."""
    errors = exc.errors()
    if errors:
        err_type = errors[0].get("type", "")
        msg = errors[0].get("msg", "Validation error")
        # Only convert value errors to 400; missing fields stay 422
        if err_type.startswith("value_error"):
            msg = msg.removeprefix("Value error, ")
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
    return "<h1>Super Translate</h1><p>Static files not found.</p>"


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok", "version": __version__}


@app.get("/api/stats")
async def stats(
    access_scope: Annotated[str, Depends(get_request_access_scope)],
) -> dict[str, int]:
    """Get workspace-scoped statistics with caching.

    Counts are restricted to the caller's access scope and cached per scope
    for 30 seconds to reduce database queries. Thread-safe: uses a
    module-level lock to prevent race conditions.
    """
    from sqlalchemy import func, select

    from app.core.database import async_session
    from app.models.paper import Paper

    now = time.time()

    async with _stats_lock:
        # Return cached result if fresh for this workspace scope
        cached = _stats_cache.get(access_scope)
        cached_at = _stats_cache_time.get(access_scope, 0.0)
        if cached is not None and (now - cached_at) < _STATS_CACHE_TTL:
            return cached

        async with async_session() as db:
            total = await db.scalar(
                select(func.count(Paper.id)).where(Paper.access_scope == access_scope),
            )
            completed = await db.scalar(
                select(func.count(Paper.id)).where(
                    Paper.access_scope == access_scope,
                    Paper.translation_status == "completed",
                ),
            )
            result = {
                "total_papers": total or 0,
                "completed_translations": completed or 0,
            }

            _stats_cache[access_scope] = result
            _stats_cache_time[access_scope] = now

            return result


def cli() -> None:
    import argparse

    import uvicorn

    parser = argparse.ArgumentParser(description="Super Translate - AI Paper Translation System")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--open", action="store_true", help="Open browser on start")
    args = parser.parse_args()

    if args.host not in ("127.0.0.1", "localhost", "::1"):
        if settings.api_token.get_secret_value() or settings.workspace_tokens:
            logger.info("Binding to %s with API token authentication enabled.", args.host)
        else:
            logger.warning(
                "Binding to %s without PAPER_CHINA_API_TOKEN. Remote API clients will be rejected "
                "unless PAPER_CHINA_ALLOW_UNAUTHENTICATED_REMOTE=true.",
                args.host,
            )

    if args.open:
        webbrowser.open(f"http://{args.host}:{args.port}")

    uvicorn.run("app.main:app", host=args.host, port=args.port, reload=settings.debug)


if __name__ == "__main__":
    cli()
