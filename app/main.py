"""Main application entry point."""

import asyncio
import json
import logging
import re
import time
import webbrowser
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager, suppress
from datetime import datetime, timedelta, timezone
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
from app.api.auth import router as auth_router
from app.api.papers import router as papers_router
from app.api.provider_credentials import router as provider_credentials_router
from app.core.access import access_decision_for_request, get_request_access_scope
from app.core.config import ensure_dirs, settings
from app.core.database import init_db
from app.core.rate_limit import RateLimitMiddleware
from app.core.users import refresh_token_scopes
from app.services.translation_recovery import current_engine_revision

# Stats cache with async lock, partitioned by workspace access scope so one
# workspace can never observe another workspace's aggregate counters.
_STATS_CACHE_TTL = 30  # seconds
_stats_cache: dict[str, dict[str, int]] = {}
_stats_cache_time: dict[str, float] = {}
_stats_lock = asyncio.Lock()
_startup_translation_tasks: set[asyncio.Task] = set()
_scheduled_recovery_job_ids: set[str] = set()
_RECOVERY_STALE_SECONDS = 90
_RECOVERY_WATCHDOG_SECONDS = 30.0
_RECOVERY_WAITING_STAGES = ("等待恢复", "等待系统修复")

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logger = logging.getLogger(__name__)


def _translation_job_resume_payload(job) -> dict[str, object]:
    """Serialize a queued translation job into _run_translation arguments."""
    return {
        "paper_id": job.paper_id,
        "access_scope": job.access_scope,
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
) -> bool:
    """Resume a durable queued translation job after startup."""
    from app.api.papers import _reset_paper_status, _run_translation

    job_id = str(payload["job_id"])
    if job_id in _scheduled_recovery_job_ids:
        return False
    _scheduled_recovery_job_ids.add(job_id)

    async def _run_after_delay() -> None:
        try:
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
                payload["access_scope"],
            )
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("Recovered translation task crashed for job %s", job_id)
            await asyncio.to_thread(
                _reset_paper_status,
                str(payload["paper_id"]),
                "Recovered translation task crashed unexpectedly",
                job_id,
            )

    task = asyncio.create_task(_run_after_delay())
    _startup_translation_tasks.add(task)

    def _discard_finished_task(finished_task: asyncio.Task) -> None:
        _startup_translation_tasks.discard(finished_task)
        _scheduled_recovery_job_ids.discard(job_id)

    task.add_done_callback(_discard_finished_task)
    return True


def _job_is_stale_for_recovery(
    job: object,
    *,
    now: datetime | None = None,
) -> bool:
    """Return whether a durable job has stopped receiving live-worker updates."""
    reference = next(
        (
            value
            for value in (
                getattr(job, "heartbeat_at", None),
                getattr(job, "updated_at", None),
                getattr(job, "created_at", None),
            )
            if isinstance(value, datetime)
        ),
        None,
    )
    if reference is None:
        return True
    current = now or datetime.now(timezone.utc)
    if reference.tzinfo is None:
        current = current.replace(tzinfo=None)
    return current - reference >= timedelta(seconds=_RECOVERY_STALE_SECONDS)


def _translation_output_is_usable(filename: str | None) -> bool:
    """Return whether a recorded translation points to a non-empty local output."""
    if not filename:
        return False
    translations_root = settings.translations_path.resolve()
    output_path = (translations_root / filename).resolve()
    if not output_path.is_relative_to(translations_root):
        logger.warning("Rejected translation output outside translations directory: %s", filename)
        return False
    try:
        return output_path.is_file() and output_path.stat().st_size > 0
    except OSError:
        return False


async def _repair_translation_state_drift() -> int:
    """Reconcile translating papers whose latest same-scope job is terminal."""
    from sqlalchemy import and_, select

    from app.core.database import async_session
    from app.models.paper import (
        Paper,
        TranslationJob,
        TranslationJobStatus,
        TranslationStatus,
    )

    terminal_statuses = (
        TranslationJobStatus.COMPLETED.value,
        TranslationJobStatus.FAILED.value,
        TranslationJobStatus.CANCELLED.value,
    )
    now = datetime.now(timezone.utc)
    async with async_session() as db:
        try:
            latest_same_scope_job_id = (
                select(TranslationJob.id)
                .where(
                    TranslationJob.paper_id == Paper.id,
                    TranslationJob.access_scope == Paper.access_scope,
                )
                .order_by(TranslationJob.created_at.desc(), TranslationJob.id.desc())
                .limit(1)
                .correlate(Paper)
                .scalar_subquery()
            )
            result = await db.execute(
                select(Paper, TranslationJob)
                .join(
                    TranslationJob,
                    and_(
                        TranslationJob.id == latest_same_scope_job_id,
                        TranslationJob.paper_id == Paper.id,
                        TranslationJob.access_scope == Paper.access_scope,
                    ),
                )
                .where(
                    Paper.translation_status == TranslationStatus.TRANSLATING.value,
                    TranslationJob.status.in_(terminal_statuses),
                )
            )
            records = list(result.all())
            for paper, job in records:
                if job.status == TranslationJobStatus.COMPLETED.value:
                    if _translation_output_is_usable(paper.translated_filename):
                        paper.translation_status = TranslationStatus.COMPLETED.value
                        paper.translation_progress = 1.0
                        paper.translation_stage = "翻译完成"
                        paper.translation_error = None
                    else:
                        error = "Translation output is missing after recovery"
                        paper.translation_status = TranslationStatus.FAILED.value
                        paper.translation_progress = 0.0
                        paper.translation_stage = "恢复失败"
                        paper.translation_error = error
                        job.status = TranslationJobStatus.FAILED.value
                        job.progress = 0.0
                        job.error = error
                        job.finished_at = job.finished_at or now
                        job.updated_at = now
                elif job.status == TranslationJobStatus.CANCELLED.value:
                    paper.translation_status = TranslationStatus.PENDING.value
                    paper.translation_progress = 0.0
                    paper.translation_stage = ""
                    paper.translation_error = None
                else:
                    paper.translation_status = TranslationStatus.FAILED.value
                    paper.translation_progress = 0.0
                    paper.translation_stage = "翻译失败"
                    paper.translation_error = (
                        job.error or "Translation failed before state recovery"
                    )
                paper.translation_eta_seconds = None

            if records:
                await db.commit()
                logger.warning(
                    "Reconciled %d translation state record(s) with terminal jobs",
                    len(records),
                )
            return len(records)
        except Exception:
            await db.rollback()
            logger.exception("Failed to reconcile translation state drift")
            return 0


async def _find_waiting_translation_jobs() -> list[dict[str, object]]:
    """Find stale queued jobs explicitly left for the recovery scheduler."""
    from sqlalchemy import and_, select

    from app.core.database import async_session
    from app.models.paper import (
        Paper,
        TranslationJob,
        TranslationJobStatus,
        TranslationStatus,
    )

    async with async_session() as db:
        latest_same_scope_job_id = (
            select(TranslationJob.id)
            .where(
                TranslationJob.paper_id == Paper.id,
                TranslationJob.access_scope == Paper.access_scope,
            )
            .order_by(TranslationJob.created_at.desc(), TranslationJob.id.desc())
            .limit(1)
            .correlate(Paper)
            .scalar_subquery()
        )
        result = await db.execute(
            select(TranslationJob)
            .join(
                Paper,
                and_(
                    Paper.id == TranslationJob.paper_id,
                    Paper.access_scope == TranslationJob.access_scope,
                ),
            )
            .where(
                TranslationJob.id == latest_same_scope_job_id,
                TranslationJob.status == TranslationJobStatus.QUEUED.value,
                Paper.translation_status == TranslationStatus.TRANSLATING.value,
                Paper.translation_stage.in_(_RECOVERY_WAITING_STAGES),
            )
            .order_by(TranslationJob.created_at.asc())
        )
        jobs = result.scalars().all()
        now = datetime.now(timezone.utc)
        return [
            _translation_job_resume_payload(job)
            for job in jobs
            if str(job.id) not in _scheduled_recovery_job_ids
            and _job_is_stale_for_recovery(job, now=now)
        ]


async def _translation_recovery_watchdog(*, resume_queued: bool = True) -> None:
    """Continuously repair state drift and reschedule forgotten recovery jobs."""
    while True:
        await asyncio.sleep(_RECOVERY_WATCHDOG_SECONDS)
        try:
            await _repair_translation_state_drift()
            if not resume_queued:
                continue
            payloads = await _find_waiting_translation_jobs()
            scheduled = sum(
                _schedule_recovered_translation(payload, delay_seconds=0) for payload in payloads
            )
            if scheduled:
                logger.warning("Recovery watchdog resumed %d translation job(s)", scheduled)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("Translation recovery watchdog iteration failed")


async def _recover_stuck_translations(
    *,
    resume_queued: bool = True,
    reconcile_terminal: bool = False,
) -> list[dict[str, object]]:
    """Recover durable translation records after a crash.

    Queued jobs and interrupted running jobs are safe to resume when startup
    recovery is enabled. The translation runner cleans the output directory
    before a re-run, so stale partial PDFs are discarded instead of reused.
    """
    from sqlalchemy import and_, func, select
    from sqlalchemy import update as sa_update

    from app.core.database import async_session
    from app.models.paper import (
        Paper,
        TranslationJob,
        TranslationJobStatus,
        TranslationStatus,
    )

    if reconcile_terminal:
        await _repair_translation_state_drift()

    async with async_session() as db:
        latest_same_scope_job_id = (
            select(TranslationJob.id)
            .where(
                TranslationJob.paper_id == Paper.id,
                TranslationJob.access_scope == Paper.access_scope,
            )
            .order_by(TranslationJob.created_at.desc(), TranslationJob.id.desc())
            .limit(1)
            .correlate(Paper)
            .scalar_subquery()
        )
        queued_result = await db.execute(
            select(TranslationJob)
            .join(
                Paper,
                and_(
                    Paper.id == TranslationJob.paper_id,
                    Paper.access_scope == TranslationJob.access_scope,
                ),
            )
            .where(TranslationJob.id == latest_same_scope_job_id)
            .where(TranslationJob.status == TranslationJobStatus.QUEUED.value)
            .where(Paper.translation_status == TranslationStatus.TRANSLATING.value)
            .order_by(TranslationJob.created_at.asc())
        )
        queued_jobs = list(queued_result.scalars().all())
        running_result = await db.execute(
            select(TranslationJob)
            .join(
                Paper,
                and_(
                    Paper.id == TranslationJob.paper_id,
                    Paper.access_scope == TranslationJob.access_scope,
                ),
            )
            .where(TranslationJob.id == latest_same_scope_job_id)
            .where(TranslationJob.status == TranslationJobStatus.RUNNING.value)
            .where(Paper.translation_status == TranslationStatus.TRANSLATING.value)
            .order_by(TranslationJob.started_at.asc(), TranslationJob.created_at.asc())
        )
        running_jobs = list(running_result.scalars().all())
        discovered_jobs = [*queued_jobs, *running_jobs]
        if resume_queued:
            recovery_now = datetime.now(timezone.utc)
            resumable_jobs = [
                job
                for job in discovered_jobs
                if _job_is_stale_for_recovery(job, now=recovery_now)
            ]
            live_jobs = [job for job in discovered_jobs if job not in resumable_jobs]
        else:
            resumable_jobs = []
            live_jobs = []
        resume_payloads = [_translation_job_resume_payload(job) for job in resumable_jobs]
        resumable_paper_ids = list(
            dict.fromkeys(str(payload["paper_id"]) for payload in resume_payloads)
        )
        resumable_job_ids = [str(job.id) for job in resumable_jobs]
        live_paper_ids = list(dict.fromkeys(str(job.paper_id) for job in live_jobs))
        live_job_ids = [str(job.id) for job in live_jobs]

        paper_update = sa_update(Paper).where(
            Paper.translation_status == TranslationStatus.TRANSLATING.value
        )
        protected_paper_ids = [*resumable_paper_ids, *live_paper_ids]
        if protected_paper_ids:
            paper_update = paper_update.where(Paper.id.not_in(protected_paper_ids))
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
            protected_job_ids = [*resumable_job_ids, *live_job_ids]
            if protected_job_ids:
                stale_job_update = stale_job_update.where(
                    TranslationJob.id.not_in(protected_job_ids)
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


async def _recover_repair_pending_translations() -> list[dict[str, object]]:
    """Requeue parked repair jobs when translation-engine code has changed."""
    from sqlalchemy import and_, func, select
    from sqlalchemy import update as sa_update

    from app.core.database import async_session
    from app.models.paper import (
        Paper,
        TranslationJob,
        TranslationJobStatus,
        TranslationStatus,
    )

    revision = current_engine_revision()
    async with async_session() as db:
        latest_same_scope_job_id = (
            select(TranslationJob.id)
            .where(
                TranslationJob.paper_id == Paper.id,
                TranslationJob.access_scope == Paper.access_scope,
            )
            .order_by(TranslationJob.created_at.desc(), TranslationJob.id.desc())
            .limit(1)
            .correlate(Paper)
            .scalar_subquery()
        )
        result = await db.execute(
            select(TranslationJob)
            .join(
                Paper,
                and_(
                    Paper.id == TranslationJob.paper_id,
                    Paper.access_scope == TranslationJob.access_scope,
                ),
            )
            .where(
                TranslationJob.id == latest_same_scope_job_id,
                TranslationJob.status == TranslationJobStatus.REPAIR_PENDING.value,
                Paper.translation_status == TranslationStatus.REPAIRING.value,
            )
            .order_by(TranslationJob.created_at.desc())
        )
        jobs = [job for job in result.scalars().all() if job.engine_revision != revision]
        if not jobs:
            return []

        job_ids = [job.id for job in jobs]
        paper_keys = [(job.paper_id, job.access_scope) for job in jobs]
        payloads = [_translation_job_resume_payload(job) for job in reversed(jobs)]
        await db.execute(
            sa_update(TranslationJob)
            .where(TranslationJob.id.in_(job_ids))
            .values(
                status=TranslationJobStatus.QUEUED.value,
                progress=0.0,
                attempt_count=0,
                engine_revision=revision,
                last_issue_fingerprint="",
                cancel_requested=False,
                heartbeat_at=None,
                started_at=None,
                finished_at=None,
                error=None,
                updated_at=func.now(),
            )
        )
        for paper_id, access_scope in paper_keys:
            await db.execute(
                sa_update(Paper)
                .where(
                    Paper.id == paper_id,
                    Paper.access_scope == access_scope,
                )
                .values(
                    translation_status=TranslationStatus.TRANSLATING.value,
                    translation_error=None,
                    translation_progress=0.0,
                    translation_stage="等待恢复",
                    translation_eta_seconds=None,
                )
            )
        await db.commit()
        logger.info(
            "Requeued %d repair-pending translation job(s) for engine revision %s",
            len(payloads),
            revision,
        )
        return payloads


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    ensure_dirs()
    await init_db()
    resume_queued = settings.resume_queued_translations_on_startup
    recovered_jobs = await _recover_stuck_translations(
        resume_queued=resume_queued,
        reconcile_terminal=True,
    )
    if resume_queued:
        recovered_jobs.extend(await _recover_repair_pending_translations())
    for payload in recovered_jobs:
        _schedule_recovered_translation(payload)
    if recovered_jobs:
        logger.info("Resumed %d translation job(s)", len(recovered_jobs))
    elif not resume_queued:
        logger.info("Startup translation resume is disabled")
    logger.info("Super Translate started at http://localhost:8000")
    recovery_watchdog = asyncio.create_task(
        _translation_recovery_watchdog(resume_queued=resume_queued)
    )
    try:
        yield
    finally:
        recovery_watchdog.cancel()
        with suppress(asyncio.CancelledError):
            await recovery_watchdog


app = FastAPI(
    title="Super Translate",
    version=__version__,
    lifespan=lifespan,
    docs_url="/docs" if settings.enable_api_docs else None,
    redoc_url="/redoc" if settings.enable_api_docs else None,
    openapi_url="/openapi.json" if settings.enable_api_docs else None,
)

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
    # The login endpoint must stay reachable without a token.
    if request.url.path == "/api/auth/login":
        return await call_next(request)
    # The benchmark showcase is public by design (read-only metrics plus
    # previews that are themselves licence-gated inside the endpoint).
    if request.url.path == "/api/showcase" or request.url.path.startswith(
        "/api/showcase/"
    ):
        return await call_next(request)

    user_scopes = tuple((await refresh_token_scopes()).items())
    decision = access_decision_for_request(request, extra_token_scopes=user_scopes)
    if not decision.allowed:
        return JSONResponse(status_code=decision.status_code, content={"detail": decision.detail})

    return await call_next(request)


@app.middleware("http")
async def add_security_headers(request: Request, call_next: RequestResponseEndpoint) -> Response:
    response = await call_next(request)
    if request.url.path.startswith("/static/"):
        # Fingerprinted-by-etag assets; cache for an hour so repeat visits do
        # not revalidate every JS/CSS file through the (slow) public tunnel.
        response.headers.setdefault("Cache-Control", "public, max-age=3600")
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
        "img-src 'self' data: blob:; "
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
app.include_router(auth_router)
app.include_router(provider_credentials_router)

static_dir = Path(__file__).parent / "static"
app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")


@app.get("/", response_class=HTMLResponse)
async def index() -> HTMLResponse:
    html_path = static_dir / "index.html"
    if html_path.exists():
        return html_path.read_text(encoding="utf-8")
    return "<h1>Super Translate</h1><p>Static files not found.</p>"


_BENCHMARK_DIR = "data/benchmark/classic20"
_PREVIEW_NAME_RE = re.compile(r"^p\d{3}_(?:original|translated)\.jpg$")
_PAPER_ID_RE = re.compile(r"^[a-z0-9_]{1,40}$")


def _showcase_payload() -> dict | None:
    path = settings.base_dir / _BENCHMARK_DIR / "showcase.json"
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


@app.get("/showcase", response_class=HTMLResponse)
async def showcase_page() -> HTMLResponse:
    """Public benchmark showcase (metrics for all papers, previews for CC)."""
    html_path = static_dir / "showcase.html"
    if html_path.exists():
        return html_path.read_text(encoding="utf-8")
    return "<h1>Benchmark showcase</h1><p>Static files not found.</p>"


def _showcase_request_is_authenticated(request: Request) -> bool:
    """Whether the caller presented a valid workspace/API credential.

    The showcase endpoints bypass the token wall so the public page works,
    but authenticated operators still get extra rights (previews of
    non-CC papers for internal review).
    """
    try:
        decision = access_decision_for_request(request)
        return bool(decision.allowed and decision.authenticated)
    except Exception:
        return False


@app.get("/api/showcase")
async def showcase_data(request: Request) -> JSONResponse:
    payload = _showcase_payload()
    if payload is None:
        return JSONResponse(
            status_code=404,
            content={"detail": "benchmark showcase has not been generated"},
        )
    payload = dict(payload)
    payload["authenticated"] = _showcase_request_is_authenticated(request)
    return JSONResponse(content=payload)


@app.get("/api/showcase/previews/{paper_id}/{name}")
async def showcase_preview(request: Request, paper_id: str, name: str):
    """Serve page preview images, gated by the paper's license flag.

    Anonymous access sees Creative Commons papers only; authenticated
    operators may review every paper's pages.
    """
    from fastapi.responses import FileResponse

    if not _PAPER_ID_RE.match(paper_id) or not _PREVIEW_NAME_RE.match(name):
        return JSONResponse(status_code=404, content={"detail": "not found"})
    payload = _showcase_payload()
    if payload is None:
        return JSONResponse(status_code=404, content={"detail": "not found"})
    paper = next(
        (item for item in payload.get("papers", []) if item.get("id") == paper_id),
        None,
    )
    if not paper:
        return JSONResponse(status_code=403, content={"detail": "license restricted"})
    if not paper.get("showcase_ok") and not _showcase_request_is_authenticated(
        request
    ):
        return JSONResponse(status_code=403, content={"detail": "license restricted"})
    path = settings.base_dir / _BENCHMARK_DIR / "previews" / paper_id / name
    if not path.is_file():
        return JSONResponse(status_code=404, content={"detail": "not found"})
    return FileResponse(path, media_type="image/jpeg")


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
