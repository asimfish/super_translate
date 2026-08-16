"""Paper management API routes."""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import os
import re
import threading
import time
import uuid
from collections.abc import Callable
from datetime import datetime, timezone
from pathlib import Path
from typing import Annotated, Any

from fastapi import APIRouter, BackgroundTasks, Depends, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel, field_validator
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.access import LOCAL_ACCESS_SCOPE, get_request_access_scope
from app.core.config import settings
from app.core.database import get_session
from app.core.provider_credentials import (
    PROVIDER_SPECS,
    CredentialConfigurationError,
    CredentialDecryptionError,
    ResolvedProviderCredential,
    load_provider_credential,
    server_provider_credential,
)
from app.models.paper import (
    Paper,
    TranslationJob,
    TranslationJobStatus,
    TranslationStatus,
    generate_job_id,
)
from app.services.library import (
    cleanup_output_dir,
    delete_paper_files,
    extract_title_from_pdf,
    generate_stored_filename,
    get_pdf_info,
)
from app.services.pdf_sanitizer import safe_pdf_for_use
from app.services.quality_agent import (
    RETRANSLATABLE_ISSUE_CODES,
    QualityAction,
    create_quality_agent,
    has_retranslatable_error,
    issue_fingerprint,
)
from app.services.translation_recovery import (
    current_engine_revision,
    recovery_attempt_limit,
    recovery_backoff_seconds,
)
from app.services.translator import (
    QualityPreset,
    TranslationConfig,
    TranslationResult,
    sanitize_error,
)

# Limit concurrent translations to prevent resource exhaustion
_translation_semaphore = threading.Semaphore(settings.max_concurrent_translations)
_quality_map = {
    "fast": QualityPreset.FAST,
    "balanced": QualityPreset.BALANCED,
    "quality": QualityPreset.QUALITY,
}
# Set of paper IDs with pending cancellation requests
_cancelled_papers: set[str] = set()
_cancel_lock = threading.Lock()


class TranslationCancelledError(Exception):
    """Raised when translation is cancelled by user."""


logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/papers", tags=["papers"])
AccessScope = Annotated[str, Depends(get_request_access_scope)]

# Paper ID format: 12-character hex string (uuid4 hex[:12])
_PAPER_ID_RE = re.compile(r"^[0-9a-f]{12}$")

# Validation limits
_MAX_TITLE_LEN = 500
_MAX_TAGS_LEN = 1000
_MAX_NOTES_LEN = 10_000
_MAX_SEARCH_LEN = 200
_PROGRESS_THROTTLE = 0.01
_PROGRESS_LOG_STEP = 10
_TRANSLATION_PROGRESS_END = 0.88
_QA_PROGRESS_START = 0.89
_QA_PROGRESS_END = 0.99
_VALID_QA_MODES = {"single", "iterative"}
_VALID_OCR_MODES = {"off", "auto", "force"}
_ETA_RE = re.compile(
    r"预计剩余\s*(?:(?P<hours>\d+)小时)?(?:(?P<minutes>\d+)分)?(?:(?P<seconds>\d+)秒)?"
)
_EDITABLE_FIGURES_DIRNAME = "editable_figures"


def _schedule_background_task(func: Callable[..., None], *args: Any) -> threading.Thread:
    """Start long-running work outside the request response lifecycle."""
    task_name = getattr(func, "__name__", "task").strip("_") or "task"
    first_arg = str(args[0]) if args else "job"
    thread = threading.Thread(
        target=func,
        args=args,
        name=f"paper-china-{task_name}-{first_arg}",
        daemon=True,
    )
    thread.start()
    return thread


def _cancel_marker_path(paper_id: str) -> Path:
    safe_id = re.sub(r"[^0-9A-Za-z_-]", "_", paper_id)
    return settings.translations_path / f"{safe_id}.cancel"


def _mark_cancel_requested(paper_id: str) -> None:
    with _cancel_lock:
        _cancelled_papers.add(paper_id)
    try:
        settings.translations_path.mkdir(parents=True, exist_ok=True)
        _cancel_marker_path(paper_id).write_text(str(time.time()), encoding="utf-8")
    except OSError:
        logger.warning("Failed to persist cancellation marker for %s", paper_id)


def _clear_cancel_requested(paper_id: str) -> None:
    with _cancel_lock:
        _cancelled_papers.discard(paper_id)
    with contextlib.suppress(OSError):
        _cancel_marker_path(paper_id).unlink(missing_ok=True)


def _is_cancel_requested(paper_id: str) -> bool:
    with _cancel_lock:
        if paper_id in _cancelled_papers:
            return True
    return _cancel_marker_path(paper_id).exists()


def _write_upload_chunks(
    file: UploadFile,
    stored_path: Path,
) -> tuple[int, bool, str]:
    """Write uploaded file chunks to disk with validation.

    Returns (total_size, is_empty, error_message).
    """
    stored_path.parent.mkdir(parents=True, exist_ok=True)
    total = 0
    is_first = True
    last_chunk = b""
    max_mb = settings.max_upload_size // (1024 * 1024)
    with stored_path.open("wb") as f:
        while chunk := file.file.read(settings.upload_chunk_size):
            total += len(chunk)
            if total > settings.max_upload_size:
                return 0, True, f"File too large (max {max_mb}MB)"
            if is_first:
                if not chunk[:5].startswith(b"%PDF"):
                    return 0, True, "Invalid PDF file (missing PDF header)"
                is_first = False
            last_chunk = chunk
            f.write(chunk)
    if not is_first and b"%%EOF" not in last_chunk[-1024:]:
        return 0, True, "Invalid PDF file (missing %%EOF marker)"
    return total, is_first, ""


def _validate_paper_id(paper_id: str) -> str:
    """Validate paper ID format. Returns the ID if valid, raises 400 otherwise."""
    if not _PAPER_ID_RE.match(paper_id):
        raise HTTPException(400, "Invalid paper ID format")
    return paper_id


class PaperResponse(BaseModel):
    """Response model for paper data."""

    id: str
    title: str
    original_filename: str
    file_size: int
    page_count: int
    translation_status: str
    translation_progress: float
    translation_error: str | None
    translation_log: str = ""
    translation_stage: str = ""
    translation_eta_seconds: int | None = None
    translation_eta: str = ""
    tags: str
    notes: str
    has_original: bool
    has_translated: bool
    has_dual: bool
    has_qa_report: bool = False
    created_at: str
    updated_at: str

    model_config = {"from_attributes": True}


class PaperListResponse(BaseModel):
    """Response model for paper list with pagination."""

    papers: list[PaperResponse]
    total: int
    offset: int
    limit: int


class TranslationJobResponse(BaseModel):
    """Response model for durable translation job data."""

    id: str
    paper_id: str
    backend: str
    quality: str
    qa_mode: str
    qa_max_passes: int
    ocr_mode: str
    ocr_language: str
    ocr_dpi: int
    status: str
    progress: float
    attempt_count: int
    max_attempts: int
    engine_revision: str
    last_issue_fingerprint: str
    cancel_requested: bool
    error: str | None
    created_at: str
    updated_at: str
    heartbeat_at: str
    started_at: str
    finished_at: str


class PaperUpdateRequest(BaseModel):
    """Request model for updating paper metadata."""

    title: str | None = None
    tags: str | None = None
    notes: str | None = None

    @field_validator("title")
    @classmethod
    def validate_title(cls, v: str | None) -> str | None:
        if v is not None:
            v = v.strip()
            if not v:
                return None
            if len(v) > _MAX_TITLE_LEN:
                raise ValueError(f"Title must be {_MAX_TITLE_LEN} characters or less")
        return v

    @field_validator("tags")
    @classmethod
    def validate_tags(cls, v: str | None) -> str | None:
        if v is not None:
            v = v.strip()
            if not v:
                return None
            if len(v) > _MAX_TAGS_LEN:
                raise ValueError(f"Tags must be {_MAX_TAGS_LEN} characters or less")
        return v

    @field_validator("notes")
    @classmethod
    def validate_notes(cls, v: str | None) -> str | None:
        if v is not None:
            v = v.strip()
            if not v:
                return None
            if len(v) > _MAX_NOTES_LEN:
                raise ValueError(f"Notes must be {_MAX_NOTES_LEN} characters or less")
        return v


def _paper_to_response(
    paper: Paper,
    *,
    has_original: bool = False,
    has_translated: bool = False,
    has_dual: bool = False,
    has_qa_report: bool = False,
) -> PaperResponse:
    """Convert a Paper model to a PaperResponse."""
    progress_meta = _translation_progress_meta(paper)
    return PaperResponse(
        id=paper.id,
        title=paper.title,
        original_filename=paper.original_filename,
        file_size=paper.file_size,
        page_count=paper.page_count,
        translation_status=paper.translation_status,
        translation_progress=max(0.0, min(1.0, paper.translation_progress)),
        translation_error=paper.translation_error,
        translation_log=paper.translation_log or "",
        translation_stage=progress_meta["stage"],
        translation_eta_seconds=progress_meta["eta_seconds"],
        translation_eta=progress_meta["eta"],
        tags=paper.tags,
        notes=paper.notes,
        has_original=has_original,
        has_translated=has_translated,
        has_dual=has_dual,
        has_qa_report=has_qa_report,
        created_at=paper.created_at.isoformat() if paper.created_at else "",
        updated_at=paper.updated_at.isoformat() if paper.updated_at else "",
    )


def _job_to_response(job: TranslationJob) -> TranslationJobResponse:
    return TranslationJobResponse(
        id=job.id,
        paper_id=job.paper_id,
        backend=job.backend,
        quality=job.quality,
        qa_mode=job.qa_mode,
        qa_max_passes=job.qa_max_passes,
        ocr_mode=job.ocr_mode,
        ocr_language=job.ocr_language,
        ocr_dpi=job.ocr_dpi,
        status=job.status,
        progress=max(0.0, min(1.0, job.progress)),
        attempt_count=job.attempt_count,
        max_attempts=job.max_attempts,
        engine_revision=job.engine_revision,
        last_issue_fingerprint=job.last_issue_fingerprint,
        cancel_requested=job.cancel_requested,
        error=job.error,
        created_at=job.created_at.isoformat() if job.created_at else "",
        updated_at=job.updated_at.isoformat() if job.updated_at else "",
        heartbeat_at=job.heartbeat_at.isoformat() if job.heartbeat_at else "",
        started_at=job.started_at.isoformat() if job.started_at else "",
        finished_at=job.finished_at.isoformat() if job.finished_at else "",
    )


def _translation_progress_meta(paper: Paper) -> dict:
    """Derive structured progress metadata.

    Prefers the live ``translation_stage`` / ``translation_eta_seconds`` columns
    written during translation, and falls back to log parsing for older rows (and
    for terminal states, where stored stage/ETA would be stale).
    """
    status = paper.translation_status
    log = paper.translation_log or ""

    if status == TranslationStatus.REPAIRING.value:
        db_stage = getattr(paper, "translation_stage", "")
        stage = (
            db_stage.strip()
            if isinstance(db_stage, str) and db_stage.strip()
            else "等待系统修复"
        )
        eta_seconds = None
    elif status == TranslationStatus.TRANSLATING.value:
        db_stage = getattr(paper, "translation_stage", "")
        stage = (
            db_stage.strip()
            if isinstance(db_stage, str) and db_stage.strip()
            else _infer_translation_stage(status, log)
        )
        db_eta = getattr(paper, "translation_eta_seconds", None)
        eta_seconds = db_eta if isinstance(db_eta, int) else _parse_latest_eta_seconds(log)
    else:
        stage = _infer_translation_stage(status, log)
        eta_seconds = None

    return {
        "stage": stage,
        "eta_seconds": eta_seconds,
        "eta": _format_duration(eta_seconds) if eta_seconds is not None else "",
    }


def _infer_translation_stage(status: str, log: str) -> str:
    if status == TranslationStatus.PENDING.value:
        return "等待翻译"
    if status == TranslationStatus.COMPLETED.value:
        return "已完成"
    if status == TranslationStatus.REPAIRING.value:
        return "等待系统修复"
    if status == TranslationStatus.FAILED.value:
        return "失败"
    if status != TranslationStatus.TRANSLATING.value:
        return status or ""

    recent = "\n".join((log or "").splitlines()[-8:])
    if "版面修复" in recent or "自动执行一次版面修复" in recent:
        return "版面修复"
    if "检查" in recent or "QA" in recent:
        return "译后检查"
    if "OCR" in recent:
        return "OCR 处理"
    if "已记录" in recent and "术语" in recent:
        return "术语检查"
    return "翻译中"


def _parse_latest_eta_seconds(log: str) -> int | None:
    matches = list(_ETA_RE.finditer(log or ""))
    if not matches:
        return None
    match = matches[-1]
    hours = int(match.group("hours") or 0)
    minutes = int(match.group("minutes") or 0)
    seconds = int(match.group("seconds") or 0)
    total = hours * 3600 + minutes * 60 + seconds
    return total if total > 0 else None


def _escape_like(value: str) -> str:
    """Escape LIKE wildcards so user-typed % and _ are treated literally."""
    return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def _get_paper_file(
    paper: Paper,
    file_attr: str,
    base_dir: Path,
    filename: str | None = None,
) -> Path:
    """Resolve and validate a paper file path.

    Args:
        paper: Paper database object
        file_attr: Attribute name on paper for the filename (e.g. 'stored_filename')
        base_dir: Base directory to resolve against
        filename: Optional explicit filename (falls back to getattr(paper, file_attr))

    Returns:
        Resolved file path

    Raises:
        HTTPException: If file not found or path traversal detected
    """
    fname = filename or getattr(paper, file_attr, None)
    if not fname:
        raise HTTPException(404, "File not found")
    resolved_base = base_dir.resolve()
    file_path = (base_dir / fname).resolve()
    if not file_path.is_relative_to(resolved_base):
        raise HTTPException(403, "Access denied")
    if not file_path.exists():
        raise HTTPException(404, "File not found")
    return file_path


def _file_exists_safe(
    base_dir: Path,
    filename: str | None,
    resolved_base: Path | None = None,
) -> bool:
    """Check if a file exists safely (no path traversal)."""
    if not filename:
        return False
    if resolved_base is None:
        resolved_base = base_dir.resolve()
    file_path = (base_dir / filename).resolve()
    if not file_path.is_relative_to(resolved_base):
        return False
    return file_path.exists()


def _qa_report_path(paper: Paper) -> Path:
    translated_path = _get_paper_file(
        paper,
        "translated_filename",
        settings.translations_path,
    )
    return translated_path.with_suffix(".qa.json")


def _qa_report_exists(paper: Paper) -> bool:
    try:
        return _qa_report_path(paper).exists()
    except HTTPException:
        return False


def _editable_figures_root() -> Path:
    return settings.translations_path / _EDITABLE_FIGURES_DIRNAME


def _editable_source_manifest_path(paper: Paper) -> Path:
    from pdf_zh_translator.editable_figures import SOURCE_FIGURES_MANIFEST_FILENAME

    return _editable_figures_root() / paper.id / SOURCE_FIGURES_MANIFEST_FILENAME


def _ui_safe_path(value: str | Path | None) -> str:
    if not value:
        return ""
    path = Path(str(value))
    try:
        resolved = path.resolve()
        base_value = getattr(settings, "base_dir", None)
        if base_value:
            base_dir = Path(base_value).resolve()
            if resolved.is_relative_to(base_dir):
                return str(resolved.relative_to(base_dir))
    except (OSError, ValueError):
        pass
    return path.name


def _editable_figure_manifest_response(
    paper: Paper,
    manifest: dict[str, Any],
    manifest_path: Path,
) -> dict[str, Any]:
    from pdf_zh_translator.editable_figures import audit_figure_source_manifest

    audit = audit_figure_source_manifest(manifest_path)
    safe_figures: list[dict[str, Any]] = []
    for figure in manifest.get("figures", []):
        if not isinstance(figure, dict):
            continue
        safe_figures.append(
            {
                "figure_id": str(figure.get("figure_id", "")),
                "page": figure.get("page"),
                "bbox": figure.get("bbox") if isinstance(figure.get("bbox"), list) else [],
                "kind": str(figure.get("kind", "")),
                "image_path": _ui_safe_path(figure.get("image_path")),
                "image_sha256": str(figure.get("image_sha256", "")),
                "width": figure.get("width"),
                "height": figure.get("height"),
                "area": figure.get("area"),
                "status": str(figure.get("status", "")),
                "editppt_run": _ui_safe_path(figure.get("editppt_run")),
                "editable_manifest": _ui_safe_path(figure.get("editable_manifest")),
            }
        )

    safe_manifest_path = _ui_safe_path(manifest_path)
    return {
        "schema_version": manifest.get("schema_version", 1),
        "paper_id": paper.id,
        "status": str(manifest.get("status", "unknown")),
        "generated_at": str(manifest.get("generated_at", "")),
        "updated_at": str(manifest.get("updated_at", "")),
        "skill": str(manifest.get("skill", "")),
        "skill_source": str(manifest.get("skill_source", "")),
        "source_pdf": _ui_safe_path(manifest.get("source_pdf")),
        "source_pdf_sha256": str(manifest.get("source_pdf_sha256", "")),
        "source_manifest_path": safe_manifest_path,
        "figure_count": int(manifest.get("figure_count") or len(safe_figures)),
        "prepared_count": int(manifest.get("prepared_count") or 0),
        "registered_count": int(manifest.get("registered_count") or 0),
        "figures": safe_figures,
        "audit": {
            "ok": audit.ok,
            "checked": audit.checked,
            "passed": audit.passed,
            "failed": audit.failed,
            "issues": audit.issues,
        },
        "next_commands": [
            f".venv/bin/python -m pdf_zh_translator figure-ppt-source-audit {safe_manifest_path}",
            f".venv/bin/python -m pdf_zh_translator figure-ppt-batch-prepare {safe_manifest_path}",
            (
                ".venv/bin/python -m pdf_zh_translator figure-ppt-source-audit "
                f"{safe_manifest_path} --require-prepared"
            ),
            f".venv/bin/python -m pdf_zh_translator figure-ppt-batch-register {safe_manifest_path}",
            (
                ".venv/bin/python -m pdf_zh_translator figure-ppt-source-audit "
                f"{safe_manifest_path} --require-registered"
            ),
            (
                ".venv/bin/python -m pdf_zh_translator figure-ppt-audit "
                f"{_ui_safe_path(_editable_figures_root())}"
            ),
        ],
    }


async def _get_paper_or_404(
    paper_id: str,
    db: AsyncSession,
    access_scope: str | None = None,
) -> Paper:
    """Fetch paper by ID or raise 404."""
    _validate_paper_id(paper_id)
    query = select(Paper).where(Paper.id == paper_id)
    if access_scope is not None:
        query = query.where(Paper.access_scope == access_scope)
    result = await db.execute(query)
    paper = result.scalar_one_or_none()
    if not paper:
        raise HTTPException(404, "Paper not found")
    return paper


@router.get("/")
async def list_papers(
    db: Annotated[AsyncSession, Depends(get_session)],
    access_scope: AccessScope,
    search: str = "",
    status: str = "",
    tag: str = "",
    offset: int = 0,
    limit: int = 50,
) -> PaperListResponse:
    """List papers with optional filtering and pagination.

    Args:
        search: Search term for paper title
        status: Filter by translation status
        tag: Filter by tag (exact match within comma-separated tags)
        offset: Number of papers to skip
        limit: Maximum number of papers to return (1-200)

    Returns:
        PaperListResponse with papers and total count
    """
    # Clamp limit and offset
    limit = min(max(limit, 1), 200)
    offset = max(offset, 0)

    # Validate search length to prevent resource abuse
    if search and len(search) > _MAX_SEARCH_LEN:
        raise HTTPException(400, f"Search term too long (max {_MAX_SEARCH_LEN} characters)")

    # Base query with filters
    base = (
        select(Paper)
        .where(Paper.access_scope == access_scope)
        .order_by(Paper.created_at.desc())
    )
    if search:
        escaped = _escape_like(search)
        base = base.where(Paper.title.like(f"%{escaped}%", escape="\\"))
    if status:
        base = base.where(Paper.translation_status == status)
    if tag:
        escaped_tag = _escape_like(tag)
        base = base.where(Paper.tags.like(f"%{escaped_tag}%", escape="\\"))

    # Count and paginate from the same filtered base
    total = await db.scalar(select(func.count()).select_from(base.subquery())) or 0
    query = base.offset(offset).limit(limit)
    result = await db.execute(query)
    papers = result.scalars().all()

    # Check file existence per paper (safe path validation, max 200 papers)
    # Pre-resolve base dirs once to avoid repeated resolve() calls per paper
    papers_base = settings.papers_path.resolve()
    trans_base = settings.translations_path.resolve()

    def _check_files() -> list[PaperResponse]:
        return [
            _paper_to_response(
                p,
                has_original=_file_exists_safe(
                    settings.papers_path,
                    p.stored_filename,
                    papers_base,
                ),
                has_translated=_file_exists_safe(
                    settings.translations_path,
                    p.translated_filename,
                    trans_base,
                ),
                has_dual=_file_exists_safe(
                    settings.translations_path,
                    p.dual_filename,
                    trans_base,
                ),
                has_qa_report=_qa_report_exists(p),
            )
            for p in papers
        ]

    paper_responses = await asyncio.to_thread(_check_files)

    return PaperListResponse(papers=paper_responses, total=total, offset=offset, limit=limit)


@router.post("/upload")
async def upload_paper(
    db: Annotated[AsyncSession, Depends(get_session)],
    access_scope: AccessScope,
    file: Annotated[UploadFile, File()],
    tags: Annotated[str, Form()] = "",
) -> PaperResponse:
    """Upload a PDF paper.

    Args:
        file: PDF file to upload
        tags: Comma-separated tags for the paper

    Returns:
        PaperResponse with the uploaded paper data

    Raises:
        HTTPException: If file is not PDF, too large, or invalid
    """
    if not file.filename or not file.filename.lower().endswith(".pdf"):
        raise HTTPException(400, "Only PDF files are accepted")

    if len(tags) > _MAX_TAGS_LEN:
        raise HTTPException(400, f"Tags must be {_MAX_TAGS_LEN} characters or less")

    stored_name = generate_stored_filename(file.filename)
    stored_path = settings.papers_path / stored_name

    try:
        _total_size, first_chunk, error = await asyncio.to_thread(
            _write_upload_chunks,
            file,
            stored_path,
        )
    except Exception:
        stored_path.unlink(missing_ok=True)
        logger.exception("Error writing uploaded file")
        raise HTTPException(500, "Failed to save uploaded file") from None
    if error:
        stored_path.unlink(missing_ok=True)
        raise HTTPException(400, error)

    if first_chunk:
        stored_path.unlink(missing_ok=True)
        raise HTTPException(400, "Empty PDF file")

    try:
        (page_count, file_size), title = await asyncio.gather(
            asyncio.to_thread(get_pdf_info, stored_path),
            asyncio.to_thread(extract_title_from_pdf, stored_path),
        )
    except Exception:
        stored_path.unlink(missing_ok=True)
        logger.exception("Error processing uploaded PDF")
        raise HTTPException(500, "Failed to process uploaded PDF") from None

    paper = Paper(
        access_scope=access_scope,
        title=title,
        original_filename=file.filename,
        stored_filename=stored_path.name,
        file_size=file_size,
        page_count=page_count,
        tags=tags,
    )
    db.add(paper)
    try:
        await db.commit()
    except Exception:
        await db.rollback()
        stored_path.unlink(missing_ok=True)
        logger.exception("Failed to save paper record, cleaned up file")
        raise HTTPException(500, "Failed to save paper record") from None
    await db.refresh(paper)

    safe_path = await asyncio.to_thread(safe_pdf_for_use, stored_path)
    if safe_path != stored_path.resolve():
        logger.info("Prepared safe PDF preview for uploaded paper %s", paper.id)

    return _paper_to_response(paper, has_original=True)


@router.get("/{paper_id}")
async def get_paper(
    paper_id: str,
    db: Annotated[AsyncSession, Depends(get_session)],
    access_scope: AccessScope,
) -> PaperResponse:
    """Get a specific paper by ID.

    Args:
        paper_id: The paper's unique identifier

    Returns:
        PaperResponse with the paper data

    Raises:
        HTTPException: If paper not found (404)
    """
    paper = await _get_paper_or_404(paper_id, db, access_scope)
    # Check file existence safely (respects path traversal guard)
    has_original = _file_exists_safe(settings.papers_path, paper.stored_filename)
    has_translated = _file_exists_safe(settings.translations_path, paper.translated_filename)
    has_dual = _file_exists_safe(settings.translations_path, paper.dual_filename)
    has_qa_report = _qa_report_exists(paper)
    return _paper_to_response(
        paper,
        has_original=has_original,
        has_translated=has_translated,
        has_dual=has_dual,
        has_qa_report=has_qa_report,
    )


@router.delete("/{paper_id}")
async def delete_paper(
    paper_id: str,
    db: Annotated[AsyncSession, Depends(get_session)],
    access_scope: AccessScope,
) -> dict[str, bool]:
    """Delete a paper and its associated files.

    Args:
        paper_id: The paper's unique identifier

    Returns:
        Success status

    Raises:
        HTTPException: If paper not found (404) or translation in progress (409)
    """
    paper = await _get_paper_or_404(paper_id, db, access_scope)
    if paper.translation_status in {
        TranslationStatus.TRANSLATING.value,
        TranslationStatus.REPAIRING.value,
    }:
        raise HTTPException(409, "Cannot delete paper while translation is in progress")
    # Delete DB record first; if commit fails, files are untouched (no orphaned record)
    await db.delete(paper)
    await db.commit()
    # Clean up files after successful DB deletion
    await delete_paper_files(paper)
    return {"ok": True}


@router.post("/{paper_id}/translate")
async def start_translation(
    paper_id: str,
    _background_tasks: BackgroundTasks,
    db: Annotated[AsyncSession, Depends(get_session)],
    access_scope: AccessScope,
    backend: str = "",
    quality: str = "balanced",
    preserve_graphics_text: bool = True,
    skip_overflow: bool = False,
    qa_mode: str = "single",
    qa_max_passes: int = 4,
    ocr_mode: str = "off",
    ocr_language: str = "eng",
    ocr_dpi: int = 180,
) -> dict[str, bool | str]:
    """Start translation for a paper.

    Args:
        paper_id: The paper's unique identifier
        backend: Translation backend (deepseek, kimi, openai, google)
        quality: Quality preset (fast, balanced, quality)
        preserve_graphics_text: Keep text inside figures/tables unchanged
        skip_overflow: Leave original text when Chinese won't fit its bbox
        qa_mode: Post-translation QA mode: single or iterative
        qa_max_passes: Maximum QA/fix passes in iterative mode
        ocr_mode: OCR behavior for scanned PDFs: off, auto, or force

    Returns:
        Success status with translation status

    Raises:
        HTTPException: If paper not found (404), translation in progress (409),
        or invalid backend/quality values (400)
    """
    valid_backends = {
        "",
        *PROVIDER_SPECS,
        "google",
        "deepl",
        "ollama",
    }
    valid_qualities = {"fast", "balanced", "quality"}
    if backend not in valid_backends:
        raise HTTPException(400, f"Invalid backend: {backend}")
    if quality not in valid_qualities:
        raise HTTPException(400, f"Invalid quality: {quality}")
    if qa_mode not in _VALID_QA_MODES:
        raise HTTPException(400, f"Invalid QA mode: {qa_mode}")
    if qa_max_passes < 1 or qa_max_passes > 8:
        raise HTTPException(400, "qa_max_passes must be between 1 and 8")
    if ocr_mode not in _VALID_OCR_MODES:
        raise HTTPException(400, f"Invalid OCR mode: {ocr_mode}")
    if ocr_dpi < 96 or ocr_dpi > 300:
        raise HTTPException(400, "ocr_dpi must be between 96 and 300")

    selected_backend = backend or settings.translation_backend
    # Atomic check-and-set: prevents two concurrent requests from both
    # starting translation for the same paper (TOCTOU race condition).
    result = await db.execute(
        update(Paper)
        .where(
            Paper.id == paper_id,
            Paper.access_scope == access_scope,
            Paper.translation_status.not_in(
                [
                    TranslationStatus.TRANSLATING.value,
                    TranslationStatus.REPAIRING.value,
                ]
            ),
        )
        .values(
            translation_status=TranslationStatus.TRANSLATING.value,
            translation_progress=0.0,
            translation_error=None,
            translated_filename="",
            dual_filename="",
            translation_log="",
            translation_stage="已提交",
            translation_eta_seconds=None,
        ),
    )
    if result.rowcount == 0:
        # Either paper doesn't exist or already translating
        await _get_paper_or_404(paper_id, db, access_scope)  # raises 404 if missing
        raise HTTPException(409, "Translation already in progress")

    if quality != "fast" and selected_backend in PROVIDER_SPECS:
        try:
            credential = await load_provider_credential(db, access_scope, selected_backend)
        except CredentialConfigurationError as exc:
            await db.rollback()
            raise HTTPException(status_code=503, detail=str(exc)) from exc
        except CredentialDecryptionError as exc:
            await db.rollback()
            raise HTTPException(
                status_code=400,
                detail="保存的 API key 无法读取，请在 API 设置中重新填写",
            ) from exc
        if credential is None and not (
            access_scope == LOCAL_ACCESS_SCOPE
            and server_provider_credential(selected_backend) is not None
        ):
            await db.rollback()
            raise HTTPException(
                status_code=400,
                detail=f"请先在 API 设置中填写 {PROVIDER_SPECS[selected_backend].label} API key",
            )

    job_id = generate_job_id()
    job = TranslationJob(
        id=job_id,
        paper_id=paper_id,
        access_scope=access_scope,
        backend=selected_backend,
        quality=quality,
        preserve_graphics_text=preserve_graphics_text,
        skip_overflow=skip_overflow,
        qa_mode=qa_mode,
        qa_max_passes=qa_max_passes,
        ocr_mode=ocr_mode,
        ocr_language=ocr_language,
        ocr_dpi=ocr_dpi,
        status=TranslationJobStatus.QUEUED.value,
        max_attempts=recovery_attempt_limit(settings.translation_recovery_attempts),
        engine_revision=current_engine_revision(),
    )
    db.add(job)
    await db.commit()

    _schedule_background_task(
        _run_translation,
        paper_id,
        selected_backend,
        quality,
        preserve_graphics_text,
        skip_overflow,
        qa_mode,
        qa_max_passes,
        ocr_mode,
        ocr_language,
        ocr_dpi,
        job_id,
        access_scope,
    )

    return {"ok": True, "status": "translating", "job_id": job_id}


@router.post("/{paper_id}/cancel")
async def cancel_translation(
    paper_id: str,
    db: Annotated[AsyncSession, Depends(get_session)],
    access_scope: AccessScope,
) -> dict[str, bool | str]:
    """Request cancellation of an in-progress translation.

    The translation will stop at the next progress callback.
    """
    paper = await _get_paper_or_404(paper_id, db, access_scope)
    if paper.translation_status not in {
        TranslationStatus.TRANSLATING.value,
        TranslationStatus.REPAIRING.value,
    }:
        raise HTTPException(409, "Paper is not currently being translated")
    if paper.translation_status == TranslationStatus.REPAIRING.value:
        paper.translation_status = TranslationStatus.PENDING.value
        paper.translation_progress = 0.0
        paper.translation_error = None
        paper.translation_stage = "等待翻译"
        paper.translation_eta_seconds = None
        await db.execute(
            update(TranslationJob)
            .where(
                TranslationJob.paper_id == paper_id,
                TranslationJob.status == TranslationJobStatus.REPAIR_PENDING.value,
            )
            .values(
                status=TranslationJobStatus.CANCELLED.value,
                cancel_requested=True,
                error="System repair cancelled by user",
                finished_at=func.now(),
                updated_at=func.now(),
            ),
        )
        await db.commit()
        return {"ok": True, "status": "cancelled"}
    _mark_cancel_requested(paper_id)
    await db.execute(
        update(TranslationJob)
        .where(
            TranslationJob.paper_id == paper_id,
            TranslationJob.status.in_(
                [TranslationJobStatus.QUEUED.value, TranslationJobStatus.RUNNING.value]
            ),
        )
        .values(cancel_requested=True, updated_at=func.now()),
    )
    await db.commit()
    return {"ok": True, "status": "cancelling"}


@router.get("/{paper_id}/jobs")
async def list_translation_jobs(
    paper_id: str,
    db: Annotated[AsyncSession, Depends(get_session)],
    access_scope: AccessScope,
) -> list[TranslationJobResponse]:
    """List recent durable translation jobs for one paper."""
    await _get_paper_or_404(paper_id, db, access_scope)
    result = await db.execute(
        select(TranslationJob)
        .where(TranslationJob.paper_id == paper_id)
        .order_by(TranslationJob.created_at.desc())
        .limit(50)
    )
    return [_job_to_response(job) for job in result.scalars().all()]


_BACKEND_API_KEY_ATTRS = {
    "deepseek": "deepseek_api_key",
    "kimi": "moonshot_api_key",
    "openai": "openai_api_key",
    "anthropic": "anthropic_api_key",
    "glm": "glm_api_key",
    "deepl": "deepl_api_key",
}


def _resolve_backend_config(
    backend: str,
    quality_preset: QualityPreset,
    preserve_graphics_text: bool = True,
    skip_overflow: bool = False,
    ocr_mode: str = "off",
    ocr_language: str = "eng",
    ocr_dpi: int = 180,
    provider_credential: ResolvedProviderCredential | None = None,
    allow_server_default: bool = True,
) -> TranslationConfig:
    """Build TranslationConfig from backend name and quality preset.

    Resolves API keys from settings, handles fast-mode override to Google.
    Raises HTTPException if a required API key is missing.
    """
    api_key = ""
    base_url = ""
    model_name = ""

    if provider_credential is not None:
        if provider_credential.provider != backend:
            raise HTTPException(400, "Provider credential does not match translation backend")
        api_key = provider_credential.api_key
        base_url = provider_credential.base_url
        model_name = provider_credential.model
    elif backend == "deepseek":
        api_key = settings.deepseek_api_key.get_secret_value()
        model_name = settings.deepseek_model
    elif backend == "kimi":
        api_key = settings.moonshot_api_key.get_secret_value()
        base_url = settings.moonshot_base_url
        model_name = settings.kimi_model
    elif backend == "openai":
        api_key = settings.openai_api_key.get_secret_value()
        base_url = settings.openai_base_url
        model_name = settings.openai_model
    elif backend == "anthropic":
        api_key = settings.anthropic_api_key.get_secret_value()
        base_url = PROVIDER_SPECS[backend].base_url
        model_name = settings.anthropic_model
    elif backend == "glm":
        api_key = settings.glm_api_key.get_secret_value()
        base_url = PROVIDER_SPECS[backend].base_url
        model_name = settings.glm_model
    elif backend == "deepl":
        api_key = settings.deepl_api_key.get_secret_value()
    elif backend == "ollama":
        base_url = settings.ollama_host

    # Fast mode forces Google Translate (no API key needed)
    if quality_preset == QualityPreset.FAST:
        backend = "google"
        api_key = ""
    elif backend in _BACKEND_API_KEY_ATTRS:
        # Validate API key is configured (fail fast with clear error)
        # Check both prefixed (PAPER_CHINA_*) and unprefixed env vars
        # since _build_pdf2zh_envs falls back to unprefixed names
        attr = _BACKEND_API_KEY_ATTRS[backend]
        prefixed_key = f"PAPER_CHINA_{attr.upper()}"
        unprefixed_key = attr.upper()
        has_env_key = os.environ.get(prefixed_key, "") or os.environ.get(unprefixed_key, "")
        if not api_key and (not allow_server_default or not has_env_key):
            raise HTTPException(
                400,
                f"Backend '{backend}' requires an API key. Configure it in API 设置.",
            )

    return TranslationConfig(
        backend=backend,
        api_key=api_key,
        base_url=base_url,
        model=model_name,
        quality=quality_preset,
        preserve_graphics_text=preserve_graphics_text,
        skip_overflow=skip_overflow,
        ocr_mode=ocr_mode,
        ocr_language=ocr_language,
        ocr_dpi=ocr_dpi,
    )


async def _update_translation_job(
    db: AsyncSession,
    job_id: str | None,
    *,
    status: str | None = None,
    progress: float | None = None,
    attempt_count: int | None = None,
    max_attempts: int | None = None,
    engine_revision: str | None = None,
    last_issue_fingerprint: str | None = None,
    error: str | None = None,
    clear_error: bool = False,
    cancel_requested: bool | None = None,
    started: bool = False,
    finished: bool = False,
    heartbeat: bool = False,
) -> None:
    """Update a durable translation job when one exists."""
    if not job_id:
        return
    values: dict[str, object] = {"updated_at": func.now()}
    if status is not None:
        values["status"] = status
    if progress is not None:
        values["progress"] = max(0.0, min(1.0, progress))
    if attempt_count is not None:
        values["attempt_count"] = max(0, attempt_count)
    if max_attempts is not None:
        values["max_attempts"] = max(1, max_attempts)
    if engine_revision is not None:
        values["engine_revision"] = engine_revision[:64]
    if last_issue_fingerprint is not None:
        values["last_issue_fingerprint"] = last_issue_fingerprint[:128]
    if clear_error:
        values["error"] = None
    elif error is not None:
        values["error"] = error
    if cancel_requested is not None:
        values["cancel_requested"] = cancel_requested
    if started:
        values["started_at"] = func.now()
    if finished:
        values["finished_at"] = func.now()
    if heartbeat:
        values["heartbeat_at"] = func.now()
    await db.execute(
        update(TranslationJob).where(TranslationJob.id == job_id).values(**values)
    )


async def _force_translation_job_terminal_state(
    job_id: str | None,
    *,
    status: str | None,
    error: str | None = None,
    paper_id: str | None = None,
    paper_values: dict[str, object] | None = None,
) -> None:
    """Best-effort terminal sync after the main result transaction commits."""
    if status not in {
        TranslationJobStatus.COMPLETED.value,
        TranslationJobStatus.FAILED.value,
        TranslationJobStatus.CANCELLED.value,
    }:
        return
    if not job_id and not (paper_id and paper_values):
        return
    from app.core.database import async_session

    try:
        async with async_session() as db:
            if paper_id and paper_values:
                paper_filters = [Paper.id == paper_id]
                if job_id:
                    latest_job_id = (
                        select(TranslationJob.id)
                        .where(TranslationJob.paper_id == paper_id)
                        .order_by(TranslationJob.created_at.desc())
                        .limit(1)
                        .scalar_subquery()
                    )
                    paper_filters.append(latest_job_id == job_id)
                await db.execute(
                    update(Paper)
                    .where(*paper_filters)
                    .values(**paper_values),
                )
            values: dict[str, object] = {
                "status": status,
                "error": error,
                "finished_at": func.now(),
                "updated_at": func.now(),
            }
            if status == TranslationJobStatus.COMPLETED.value:
                values["progress"] = 1.0
            if job_id:
                await db.execute(
                    update(TranslationJob)
                    .where(TranslationJob.id == job_id)
                    .values(**values),
                )
            await db.commit()
    except Exception:
        logger.exception("Failed to force terminal translation job state for %s", job_id)


def _reset_paper_status(paper_id: str, error_message: str, job_id: str | None = None) -> None:
    """Reset a paper's translation status to failed (synchronous, for background threads)."""
    from app.core.database import async_session

    try:

        async def _do_reset():
            from sqlalchemy import update as sa_update

            async with async_session() as db:
                await db.execute(
                    sa_update(Paper)
                    .where(
                        Paper.id == paper_id,
                        Paper.translation_status == TranslationStatus.TRANSLATING.value,
                    )
                    .values(
                        translation_status=TranslationStatus.FAILED.value,
                        translation_error=error_message,
                    ),
                )
                await _update_translation_job(
                    db,
                    job_id,
                    status=TranslationJobStatus.FAILED.value,
                    error=error_message,
                    finished=True,
                )
                await db.commit()

        asyncio.run(_do_reset())
    except Exception:
        logger.exception("Failed to reset paper status for %s", paper_id)


def _run_translation(
    paper_id: str,
    backend: str,
    quality: str = "balanced",
    preserve_graphics_text: bool = True,
    skip_overflow: bool = False,
    qa_mode: str = "single",
    qa_max_passes: int = 4,
    ocr_mode: str = "off",
    ocr_language: str = "eng",
    ocr_dpi: int = 180,
    job_id: str | None = None,
    access_scope: str | None = None,
) -> None:
    logger.info("Translation job waiting for available worker slot: paper %s", paper_id)
    acquired = _translation_semaphore.acquire()
    if not acquired:
        logger.error("Translation queue full, rejecting paper %s", paper_id)
        if job_id:
            _reset_paper_status(
                paper_id,
                "Translation queue is busy, please try again later",
                job_id,
            )
        else:
            _reset_paper_status(paper_id, "Translation queue is busy, please try again later")
        return

    try:
        _clear_cancel_requested(paper_id)
        quality_preset = _quality_map.get(quality, QualityPreset.BALANCED)
        provider_credential = None
        if quality_preset != QualityPreset.FAST and backend in PROVIDER_SPECS:
            if access_scope is not None:
                async def _load_credential() -> ResolvedProviderCredential | None:
                    from app.core.database import async_session

                    async with async_session() as db:
                        return await load_provider_credential(db, access_scope, backend)

                provider_credential = asyncio.run(_load_credential())
                if provider_credential is None and access_scope == LOCAL_ACCESS_SCOPE:
                    provider_credential = server_provider_credential(backend)
                if provider_credential is None:
                    raise HTTPException(
                        400,
                        f"请先在 API 设置中填写 {PROVIDER_SPECS[backend].label} API key",
                    )
        config = _resolve_backend_config(
            backend,
            quality_preset,
            preserve_graphics_text=preserve_graphics_text,
            skip_overflow=skip_overflow,
            ocr_mode=ocr_mode,
            ocr_language=ocr_language,
            ocr_dpi=ocr_dpi,
            provider_credential=provider_credential,
            allow_server_default=access_scope in (None, LOCAL_ACCESS_SCOPE),
        )

        asyncio.run(_do_translate(paper_id, config, quality, qa_mode, qa_max_passes, job_id))

    except HTTPException as e:
        # Config validation errors (missing API key, etc.) — surface the real message
        if job_id:
            _reset_paper_status(paper_id, e.detail, job_id)
        else:
            _reset_paper_status(paper_id, e.detail)
    except Exception:
        # Safety net: if anything outside _do_translate fails, reset paper status
        # so it doesn't stay stuck as "translating" forever
        logger.exception("Unhandled error in _run_translation for paper %s", paper_id)
        if job_id:
            _reset_paper_status(paper_id, "Unexpected server error during translation", job_id)
        else:
            _reset_paper_status(paper_id, "Unexpected server error during translation")

    finally:
        _clear_cancel_requested(paper_id)
        _translation_semaphore.release()


async def _finalize_cancelled_translation(
    paper_id: str,
    loop: asyncio.AbstractEventLoop,
    output_dir: Path,
    start_time: float,
    job_id: str | None,
) -> None:
    """Reset state for a user-cancelled translation.

    Shared by the translate phase and the post-translation QA phase so a cancel
    request takes effect promptly during either.
    """
    from app.core.database import async_session

    elapsed = time.monotonic() - start_time
    logger.info("Translation cancelled for paper %s", paper_id)
    _append_log(paper_id, loop, f"翻译已取消 (耗时 {elapsed:.0f}秒)")
    async with async_session() as db:
        result = await db.execute(select(Paper).where(Paper.id == paper_id))
        paper = result.scalar_one_or_none()
        if paper:
            paper.translation_status = TranslationStatus.PENDING.value
            paper.translation_progress = 0.0
            paper.translation_error = None
            await _update_translation_job(
                db,
                job_id,
                status=TranslationJobStatus.CANCELLED.value,
                progress=0.0,
                error="Translation cancelled by user",
                cancel_requested=True,
                finished=True,
            )
            await db.commit()
    cleanup_output_dir(output_dir)
    _clear_cancel_requested(paper_id)


def _verify_translation_isolated(
    original_path: Path,
    translated_path: Path,
) -> list:
    """Run verify_translation_issues in a subprocess.

    PyMuPDF can segfault on specific produced documents (PhiZero, 2026-08-05:
    crash in page_get_textpage during QA), and the check runs in the web
    process — so it runs isolated instead. A crash degrades to a qa_failed
    WARNING (the translation itself is unaffected).
    """
    import subprocess
    import sys

    from pdf_zh_translator.pdf_layout import TranslationIssue

    work_dir = translated_path.parent
    token = uuid.uuid4().hex
    spec_path = work_dir / f".verify-{token}.spec.json"
    result_path = work_dir / f".verify-{token}.json"
    spec = {
        "mode": "verify",
        "original_path": str(original_path),
        "translated_path": str(translated_path),
        "result_path": str(result_path),
    }
    spec_path.write_text(json.dumps(spec), encoding="utf-8")
    env = dict(os.environ, PYTHONFAULTHANDLER="1")
    try:
        subprocess.run(
            [sys.executable, "-m", "app.services.worker", str(spec_path)],
            cwd=str(settings.base_dir),
            env=env,
            stdout=subprocess.DEVNULL,
            timeout=600,
            check=False,
        )
    except subprocess.TimeoutExpired:
        pass
    finally:
        spec_path.unlink(missing_ok=True)

    issues: list = []
    crashed = not result_path.exists()
    if not crashed:
        try:
            payload = json.loads(result_path.read_text(encoding="utf-8"))
            crashed = bool(payload.get("crashed"))
            for item in payload.get("issues", []):
                issues.append(
                    TranslationIssue(
                        page=int(item.get("page", 0)),
                        code=str(item.get("code", "unknown")),
                        message=str(item.get("message", "")),
                        severity=str(item.get("severity", "warning")),
                    )
                )
        except (json.JSONDecodeError, KeyError, TypeError, ValueError):
            crashed = True
    result_path.unlink(missing_ok=True)
    if crashed:
        logger.warning(
            "QA verification crashed in native code for %s; degrading to warning",
            translated_path,
        )
        issues.append(
            TranslationIssue(
                page=0,
                code="qa_failed",
                message="QA verification crashed in native code; skipped",
                severity="warning",
            )
        )
    return issues


async def _run_translate_in_worker(
    input_path: Path,
    output_dir: Path,
    config: TranslationConfig,
    on_progress: Callable[[float], None],
    *,
    paper_id: str = "",
) -> TranslationResult:
    """Run the translation pipeline in a dedicated subprocess.

    A native-layer crash (PyMuPDF/pikepdf heap corruption) then kills only
    the worker; the web server stays up and the job fails cleanly instead of
    taking the whole service down (observed on PhiZero, 2026-08-05:
    "free(): invalid next size" crashed the process mid-translation).
    Progress arrives via a JSONL file the worker appends to; cancellation
    terminates the worker.
    """
    import dataclasses
    import sys

    spec = {
        "input_path": str(input_path),
        "output_dir": str(output_dir),
        "config": {**dataclasses.asdict(config), "quality": config.quality.value},
    }
    api_key = spec["config"].pop("api_key", "")
    spec_path = output_dir / ".worker_spec.json"
    progress_file = output_dir / ".worker_progress.jsonl"
    result_file = output_dir / ".worker_result.json"
    progress_file.unlink(missing_ok=True)
    result_file.unlink(missing_ok=True)
    spec_path.write_text(json.dumps(spec), encoding="utf-8")

    env = dict(os.environ, PYTHONFAULTHANDLER="1")
    proc = await asyncio.create_subprocess_exec(
        sys.executable,
        "-m",
        "app.services.worker",
        str(spec_path),
        cwd=str(settings.base_dir),
        env=env,
        stdin=asyncio.subprocess.PIPE,
        # stderr inherits the server log so faulthandler stacks are kept.
        stdout=asyncio.subprocess.DEVNULL,
    )
    if proc.stdin is None:
        return TranslationResult(error="Translation worker secret channel is unavailable")
    try:
        proc.stdin.write(json.dumps({"api_key": api_key}).encode("utf-8"))
        drain_result = proc.stdin.drain()
        if hasattr(drain_result, "__await__"):
            await drain_result
        proc.stdin.close()
        close_result = proc.stdin.wait_closed()
        if hasattr(close_result, "__await__"):
            await close_result
    except (BrokenPipeError, ConnectionResetError):
        with contextlib.suppress(ProcessLookupError):
            proc.terminate()
        await proc.wait()
        return TranslationResult(error="Translation worker could not receive API credentials")

    last_progress = -1.0
    deadline = time.monotonic() + settings.translation_timeout_seconds
    while proc.returncode is None:
        if _is_cancel_requested(paper_id):
            with contextlib.suppress(ProcessLookupError):
                proc.terminate()
            await proc.wait()
            raise TranslationCancelledError("Translation cancelled by user")
        if time.monotonic() > deadline:
            with contextlib.suppress(ProcessLookupError):
                proc.kill()
            await proc.wait()
            return TranslationResult(error="Translation timed out")
        if progress_file.exists():
            try:
                last_line = (
                    progress_file.read_text(encoding="utf-8").strip().splitlines()[-1]
                )
                pct = float(json.loads(last_line)["progress"])
                if pct > last_progress:
                    last_progress = pct
                    on_progress(pct)
            except (json.JSONDecodeError, IndexError, ValueError, KeyError):
                pass
        try:
            await asyncio.wait_for(asyncio.shield(proc.wait()), timeout=1.5)
        except TimeoutError:
            pass

    if result_file.exists():
        try:
            data = json.loads(result_file.read_text(encoding="utf-8"))
            return TranslationResult(
                mono_path=Path(data["mono_path"]) if data.get("mono_path") else None,
                dual_path=Path(data["dual_path"]) if data.get("dual_path") else None,
                error=data.get("error"),
            )
        except (json.JSONDecodeError, KeyError, TypeError):
            pass
    return TranslationResult(
        error=f"Translation worker exited unexpectedly (code {proc.returncode})"
    )


async def _do_translate(
    paper_id: str,
    config: TranslationConfig,
    quality: str,
    qa_mode: str = "single",
    qa_max_passes: int = 4,
    job_id: str | None = None,
) -> None:
    """Execute translation in async context.

    Uses short DB sessions to avoid holding connections during translation:
    1. Load paper + validate paths, then close session
    2. Run translation (no DB session held)
    3. Open new session to write results
    """
    from app.core.database import async_session

    loop = asyncio.get_running_loop()

    # Phase 1: Load and validate (short session)
    async with async_session() as db:
        result = await db.execute(select(Paper).where(Paper.id == paper_id))
        paper = result.scalar_one_or_none()
        if not paper:
            logger.error("Paper %s not found for translation", paper_id)
            await _update_translation_job(
                db,
                job_id,
                status=TranslationJobStatus.FAILED.value,
                error="Paper not found",
                finished=True,
            )
            await db.commit()
            _clear_cancel_requested(paper_id)
            return

        stored_filename = paper.stored_filename
        await _update_translation_job(
            db,
            job_id,
            status=TranslationJobStatus.RUNNING.value,
            progress=0.0,
            started=True,
            heartbeat=True,
        )

        # Validate paths while session is open (can write error status directly)
        papers_base = settings.papers_path.resolve()
        input_path = (settings.papers_path / stored_filename).resolve()
        if not input_path.is_relative_to(papers_base):
            logger.error("Path traversal detected for paper %s", paper_id)
            paper.translation_status = TranslationStatus.FAILED.value
            paper.translation_error = "Invalid file path"
            await _update_translation_job(
                db,
                job_id,
                status=TranslationJobStatus.FAILED.value,
                error="Invalid file path",
                finished=True,
            )
            await db.commit()
            _clear_cancel_requested(paper_id)
            return

        if not input_path.exists():
            logger.error("Original file missing for paper %s", paper_id)
            paper.translation_status = TranslationStatus.FAILED.value
            paper.translation_error = "Original PDF file not found"
            await _update_translation_job(
                db,
                job_id,
                status=TranslationJobStatus.FAILED.value,
                error="Original PDF file not found",
                finished=True,
            )
            await db.commit()
            _clear_cancel_requested(paper_id)
            return

        input_path = await asyncio.to_thread(safe_pdf_for_use, input_path)

        if job_id:
            await db.commit()

    output_dir = settings.translations_path / paper_id
    # Clean up old translation files before starting re-translation
    cleanup_output_dir(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    start_time = time.monotonic()

    logger.info(
        "Starting translation for paper %s (backend=%s, quality=%s)",
        paper_id,
        config.backend,
        quality,
    )
    _append_log(paper_id, loop, f"开始翻译 (引擎: {config.backend}, 质量: {quality})")
    _append_log(paper_id, loop, f"共 {paper.page_count} 页, 文件大小: {paper.file_size // 1024}KB")
    _set_translation_stage(paper_id, loop, "解析 PDF")

    # Phase 2: Run translation (no DB session held)
    qa_eta_seconds = _estimate_post_translation_seconds(
        paper.page_count,
        qa_mode=qa_mode,
        qa_max_passes=qa_max_passes,
    )
    on_progress = _create_progress_handler(
        paper_id,
        loop,
        job_id=job_id,
        progress_end=_TRANSLATION_PROGRESS_END,
        postprocess_eta_seconds=qa_eta_seconds,
    )

    max_attempts = recovery_attempt_limit(settings.translation_recovery_attempts)
    engine_revision = current_engine_revision()
    trans_result = TranslationResult(error="Translation did not start")
    best_result: TranslationResult | None = None
    best_snapshots: dict[Path, bytes] = {}
    best_issues: list = []
    best_score: tuple[int, int] | None = None
    clean_result = False
    qa_failure_error: str | None = None
    for attempt in range(1, max_attempts + 1):
        if attempt > 1:
            delay = recovery_backoff_seconds(
                settings.translation_recovery_backoff_seconds,
                attempt,
            )
            _append_log(
                paper_id,
                loop,
                f"自动恢复第 {attempt}/{max_attempts} 次：重新生成并复查译文",
            )
            _set_translation_stage(
                paper_id,
                loop,
                f"自动恢复 {attempt}/{max_attempts}",
                job_id=job_id,
            )
            if delay > 0:
                await asyncio.sleep(delay)
        async with async_session() as db:
            await _update_translation_job(
                db,
                job_id,
                status=TranslationJobStatus.RUNNING.value,
                attempt_count=attempt,
                max_attempts=max_attempts,
                engine_revision=engine_revision,
                clear_error=True,
                heartbeat=True,
            )
            if job_id:
                await db.commit()
        try:
            trans_result = await _run_translate_in_worker(
                input_path,
                output_dir,
                config,
                on_progress,
                paper_id=paper_id,
            )
        except TranslationCancelledError:
            await _finalize_cancelled_translation(paper_id, loop, output_dir, start_time, job_id)
            return
        except Exception as e:
            logger.exception("Translation crashed for paper %s", paper_id)
            trans_result = TranslationResult(error=sanitize_error(e))

        if not trans_result.success:
            worker_error = trans_result.error or "Translation worker failed"
            async with async_session() as db:
                await _update_translation_job(
                    db,
                    job_id,
                    attempt_count=attempt,
                    last_issue_fingerprint=f"worker:{worker_error}",
                    error=worker_error,
                    heartbeat=True,
                )
                if job_id:
                    await db.commit()
            if attempt < max_attempts:
                _append_log(
                    paper_id,
                    loop,
                    f"本次翻译未完成：{worker_error}；系统将自动恢复",
                )
                continue
            break

        qa_failure_error = None
        unresolved_issues: list = []
        if trans_result.success and trans_result.mono_path:
            try:
                unresolved_issues = await asyncio.to_thread(
                    _run_post_translation_qa,
                    paper_id,
                    loop,
                    input_path,
                    trans_result,
                    qa_mode=qa_mode,
                    max_passes=qa_max_passes,
                    job_id=job_id,
                    estimated_seconds=qa_eta_seconds,
                )
            except TranslationCancelledError:
                await _finalize_cancelled_translation(
                    paper_id, loop, output_dir, start_time, job_id
                )
                return
        candidate_score = _qa_issue_score(unresolved_issues)
        if best_score is None or candidate_score < best_score:
            best_result = trans_result
            best_snapshots = _snapshot_translated_outputs(trans_result)
            best_issues = list(unresolved_issues)
            best_score = candidate_score

        if not _has_unresolved_error(unresolved_issues):
            clean_result = True
            qa_failure_error = None
            break

        blocking = _has_blocking_qa_error(unresolved_issues)
        qa_failure_error = _qa_failure_error_message(
            unresolved_issues,
            blocking=blocking,
        )
        fingerprint = issue_fingerprint(unresolved_issues)
        async with async_session() as db:
            await _update_translation_job(
                db,
                job_id,
                attempt_count=attempt,
                last_issue_fingerprint=fingerprint,
                error=qa_failure_error,
                heartbeat=True,
            )
            if job_id:
                await db.commit()
        if attempt < max_attempts:
            strategy = (
                "重新生成漏翻内容"
                if _has_self_healable_error(unresolved_issues)
                else "重新渲染并执行完整检查"
            )
            _append_log(
                paper_id,
                loop,
                f"译后检查仍有 {candidate_score[0]} 个错误；系统将{strategy}",
            )
            continue
        _append_log(
            paper_id,
            loop,
            "当前引擎已用完自动恢复次数，已保留最佳译文并等待系统修复",
        )

    if not clean_result and best_result is not None:
        _restore_translated_outputs(best_snapshots)
        trans_result = best_result
        qa_failure_error = _qa_failure_error_message(
            best_issues,
            blocking=_has_blocking_qa_error(best_issues),
        )

    elapsed = time.monotonic() - start_time
    terminal_job_status: str | None = None
    terminal_job_error: str | None = None
    terminal_paper_values: dict[str, object] | None = None

    # Phase 3: Write results (short session)
    async with async_session() as db:
        result = await db.execute(select(Paper).where(Paper.id == paper_id))
        paper = result.scalar_one_or_none()
        if not paper:
            logger.error("Paper %s disappeared during translation", paper_id)
            cleanup_output_dir(output_dir)
            _clear_cancel_requested(paper_id)
            return

        _update_paper_result(paper, trans_result, output_dir)
        if not clean_result:
            recovery_error = qa_failure_error or trans_result.error or "Translation failed"
            paper.translation_status = TranslationStatus.REPAIRING.value
            paper.translation_progress = 0.99 if trans_result.success else 0.0
            paper.translation_error = recovery_error
            paper.translation_stage = "等待系统修复"
            paper.translation_eta_seconds = None
            await _update_translation_job(
                db,
                job_id,
                status=TranslationJobStatus.REPAIR_PENDING.value,
                progress=paper.translation_progress,
                attempt_count=max_attempts,
                max_attempts=max_attempts,
                engine_revision=engine_revision,
                error=recovery_error,
                heartbeat=True,
            )
        else:
            paper.translation_error = None
            paper.translation_stage = "翻译完成"
            paper.translation_eta_seconds = None
            terminal_job_status = TranslationJobStatus.COMPLETED.value
            await _update_translation_job(
                db,
                job_id,
                status=TranslationJobStatus.COMPLETED.value,
                progress=1.0,
                attempt_count=min(max_attempts, attempt),
                max_attempts=max_attempts,
                engine_revision=engine_revision,
                clear_error=True,
                finished=True,
            )
        if clean_result:
            final_log_message = f"翻译完成! 耗时 {elapsed:.0f}秒"
        else:
            error = qa_failure_error or trans_result.error
            final_log_message = (
                f"等待系统修复: {error} (已自动尝试 {max_attempts} 次, "
                f"耗时 {elapsed:.0f}秒)"
            )
        paper.translation_log = _append_log_text(paper.translation_log, final_log_message)
        terminal_paper_values = {
            "translation_status": paper.translation_status,
            "translation_progress": paper.translation_progress,
            "translation_error": paper.translation_error,
            "translation_stage": paper.translation_stage,
            "translation_eta_seconds": paper.translation_eta_seconds,
            "translated_filename": paper.translated_filename,
            "dual_filename": paper.dual_filename,
            "translation_log": paper.translation_log,
        }
        await db.commit()

        # Send Feishu notification
        webhook_url = settings.feishu_webhook_url
        if isinstance(webhook_url, str) and webhook_url:
            from app.services.notify import notify_translation_complete

            notify_translation_complete(
                webhook_url,
                paper.title,
                paper_id,
                clean_result,
                qa_failure_error or trans_result.error,
                base_url=settings.base_url,
            )
    await _force_translation_job_terminal_state(
        job_id,
        status=terminal_job_status,
        error=terminal_job_error,
        paper_id=paper_id,
        paper_values=terminal_paper_values,
    )
    _clear_cancel_requested(paper_id)


def _run_post_translation_qa(
    paper_id: str,
    loop: asyncio.AbstractEventLoop,
    input_path: Path,
    trans_result: TranslationResult,
    *,
    qa_mode: str = "single",
    max_passes: int = 4,
    job_id: str | None = None,
    estimated_seconds: int | None = None,
) -> list:
    """Run layout/translation QA.

    single: one verification pass and one possible layout repair.
    iterative: verify/fix repeatedly until clean or max_passes is reached.
    """
    mono_path = trans_result.mono_path
    if mono_path is None:
        return []
    try:

        if _is_cancel_requested(paper_id):
            raise TranslationCancelledError("Translation cancelled by user")
        _append_log(paper_id, loop, "正在检查译文和版面")
        _set_translation_stage(
            paper_id,
            loop,
            "译后检查",
            progress=_QA_PROGRESS_START,
            eta_seconds=estimated_seconds,
            job_id=job_id,
        )
        _record_terminology_candidates(paper_id, loop, input_path)
        _audit_terminology_usage(paper_id, loop, input_path, mono_path)
        passes = max(1, max_passes if qa_mode == "iterative" else 1)
        issues = []
        pass_history = []
        passes_run = 0
        repair_attempted = False
        quality_agent = create_quality_agent()
        attempted_layout_fingerprints: set[str] = set()
        for pass_index in range(1, passes + 1):
            if _is_cancel_requested(paper_id):
                raise TranslationCancelledError("Translation cancelled by user")
            pass_start = _QA_PROGRESS_START + (
                (_QA_PROGRESS_END - _QA_PROGRESS_START) * (pass_index - 1) / passes
            )
            pass_end = _QA_PROGRESS_START + (
                (_QA_PROGRESS_END - _QA_PROGRESS_START) * pass_index / passes
            )
            pass_span = pass_end - pass_start

            def update_qa_stage(stage: str, fraction: float) -> None:
                progress = pass_start + pass_span * max(0.0, min(1.0, fraction))
                eta = None
                if estimated_seconds is not None:
                    remaining_ratio = max(
                        0.0,
                        (_QA_PROGRESS_END - progress)
                        / (_QA_PROGRESS_END - _QA_PROGRESS_START),
                    )
                    eta = max(1, round(estimated_seconds * remaining_ratio))
                _set_translation_stage(
                    paper_id,
                    loop,
                    stage,
                    progress=progress,
                    eta_seconds=eta,
                    job_id=job_id,
                )

            update_qa_stage(f"译后检查 {pass_index}/{passes}", 0.05)
            issues = _verify_translation_isolated(input_path, mono_path)
            passes_run += 1
            update_qa_stage(f"译后检查 {pass_index}/{passes}", 0.4)
            plan = quality_agent.plan(issues)
            fingerprint = issue_fingerprint(issues)
            if not issues:
                _set_translation_stage(
                    paper_id,
                    loop,
                    "质检通过",
                    progress=_QA_PROGRESS_END,
                    eta_seconds=0,
                    job_id=job_id,
                )
                pass_history.append(
                    _qa_pass_summary(
                        pass_index,
                        issues,
                        agent_action=plan.action,
                        agent_reason=plan.reason,
                        issue_fingerprint=fingerprint,
                    )
                )
                _append_log(paper_id, loop, f"译文检查通过 (第 {pass_index} 轮)")
                _write_qa_report(
                    trans_result,
                    issues,
                    qa_mode=qa_mode,
                    passes_run=passes_run,
                    repair_attempted=repair_attempted,
                    pass_history=pass_history,
                    status="passed",
                )
                return []

            _append_log(
                paper_id,
                loop,
                f"第 {pass_index} 轮检查发现 {len(issues)} 个潜在问题",
            )
            for issue in issues[:3]:
                _append_log(paper_id, loop, issue.message)

            if plan.action is not QualityAction.REPAIR_LAYOUT:
                pass_history.append(
                    _qa_pass_summary(
                        pass_index,
                        issues,
                        agent_action=plan.action,
                        agent_reason=plan.reason,
                        issue_fingerprint=fingerprint,
                    )
                )
                break

            if fingerprint in attempted_layout_fingerprints:
                pass_history.append(
                    _qa_pass_summary(
                        pass_index,
                        issues,
                        agent_action=QualityAction.STOP,
                        agent_reason="detector result repeated after a repair attempt",
                        issue_fingerprint=fingerprint,
                    )
                )
                _append_log(paper_id, loop, "巡检结果未变化，已停止无进展循环")
                break
            attempted_layout_fingerprints.add(fingerprint)
            update_qa_stage("版面修复", 0.58)
            before_repair_issues = list(issues)
            snapshot = _snapshot_translated_outputs(trans_result)
            try:
                fixed = _fix_translated_outputs(input_path, trans_result)
            except Exception:
                _restore_translated_outputs(snapshot)
                raise
            repair_attempted = True
            pass_history.append(
                _qa_pass_summary(
                    pass_index,
                    issues,
                    repair_attempted_after=True,
                    agent_action=plan.action,
                    agent_reason=plan.reason,
                    issue_fingerprint=fingerprint,
                )
            )
            if not fixed:
                break
            _append_log(paper_id, loop, "检测到可修复版面问题，已自动执行一次版面修复")
            update_qa_stage("复查版面", 0.78)
            issues = _verify_translation_isolated(input_path, mono_path)
            passes_run += 1
            update_qa_stage("复查版面", 0.95)
            candidate_plan = quality_agent.plan(issues)
            candidate_fingerprint = issue_fingerprint(issues)
            candidate_score = _qa_issue_score(issues)
            previous_score = _qa_issue_score(before_repair_issues)
            if candidate_score >= previous_score:
                _restore_translated_outputs(snapshot)
                issues = before_repair_issues
                reason = "使问题增多" if candidate_score > previous_score else "未减少问题"
                _append_log(
                    paper_id,
                    loop,
                    f"版面修复{reason}，已回滚到修复前译文",
                )
                pass_history.append(
                    _qa_pass_summary(
                        passes_run,
                        issues,
                        agent_action=QualityAction.STOP,
                        agent_reason="candidate did not strictly improve issue score",
                        issue_fingerprint=issue_fingerprint(issues),
                    )
                )
                break
            pass_history.append(
                _qa_pass_summary(
                    passes_run,
                    issues,
                    agent_action=candidate_plan.action,
                    agent_reason=candidate_plan.reason,
                    issue_fingerprint=candidate_fingerprint,
                )
            )
            if not issues:
                _set_translation_stage(
                    paper_id,
                    loop,
                    "质检通过",
                    progress=_QA_PROGRESS_END,
                    eta_seconds=0,
                    job_id=job_id,
                )
                _append_log(paper_id, loop, f"译文检查通过 (第 {passes_run} 轮)")
                _write_qa_report(
                    trans_result,
                    issues,
                    qa_mode=qa_mode,
                    passes_run=passes_run,
                    repair_attempted=repair_attempted,
                    pass_history=pass_history,
                    status="passed",
                )
                return []
            if qa_mode != "iterative":
                break

        _write_qa_report(
            trans_result,
            issues,
            qa_mode=qa_mode,
            passes_run=passes_run,
            repair_attempted=repair_attempted,
            pass_history=pass_history,
            status="failed" if _has_unresolved_error(issues) else "warning",
        )
        return issues
    except TranslationCancelledError:
        # Propagate so the caller resets the paper to pending (not a QA failure).
        raise
    except Exception as e:
        from pdf_zh_translator.pdf_layout import TranslationIssue

        error = sanitize_error(e)
        issue = TranslationIssue(
            page=0,
            code="qa_failed",
            message=f"Post-translation QA failed: {error}",
            severity="warning",
        )
        logger.warning("Post-translation QA failed for %s: %s", paper_id, error)
        _append_log(
            paper_id,
            loop,
            "译文已生成，但译后质检执行失败；已恢复修复前版本，请人工复核 QA 报告",
        )
        _write_qa_report(
            trans_result,
            [issue],
            qa_mode=qa_mode,
            passes_run=0,
            repair_attempted=False,
            pass_history=[],
            status="qa_failed",
            error=error,
        )
        return [issue]


def _write_qa_report(
    trans_result: TranslationResult,
    issues: list,
    *,
    qa_mode: str,
    passes_run: int,
    repair_attempted: bool,
    pass_history: list[dict] | None = None,
    status: str,
    error: str | None = None,
) -> None:
    """Write a machine-readable QA sidecar next to translated PDFs."""
    if not trans_result.mono_path:
        return
    try:
        issue_items = [_qa_issue_to_dict(issue) for issue in issues]
        report = {
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "qa_mode": qa_mode,
            "status": status,
            "passes_run": passes_run,
            "repair_attempted": repair_attempted,
            "pass_history": pass_history or [],
            "issue_count": len(issue_items),
            "error_count": sum(1 for item in issue_items if item["severity"] == "error"),
            "warning_count": sum(1 for item in issue_items if item["severity"] != "error"),
            "issues": issue_items,
        }
        if error:
            report["qa_error"] = error
        report_path = trans_result.mono_path.with_suffix(".qa.json")
        report_path.write_text(
            json.dumps(report, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
    except Exception as e:
        logger.warning("Failed to write QA report: %s", sanitize_error(e))


def _qa_pass_summary(
    pass_index: int,
    issues: list,
    *,
    repair_attempted_after: bool = False,
    agent_action: QualityAction | None = None,
    agent_reason: str | None = None,
    issue_fingerprint: str | None = None,
) -> dict:
    issue_items = [_qa_issue_to_dict(issue) for issue in issues]
    return {
        "pass": pass_index,
        "issue_count": len(issue_items),
        "error_count": sum(1 for item in issue_items if item["severity"] == "error"),
        "warning_count": sum(1 for item in issue_items if item["severity"] != "error"),
        "repair_attempted_after": repair_attempted_after,
        "issue_codes": sorted({item["code"] for item in issue_items})[:12],
        "agent_action": agent_action.value if agent_action else None,
        "agent_reason": agent_reason,
        "issue_fingerprint": issue_fingerprint,
    }


def _qa_issue_to_dict(issue: object) -> dict:
    page = getattr(issue, "page", 0)
    if not isinstance(page, int):
        page = 0
    code = getattr(issue, "code", "unknown")
    if not isinstance(code, str):
        code = "unknown"
    message = getattr(issue, "message", "")
    if not isinstance(message, str):
        message = str(message)
    severity = getattr(issue, "severity", "warning")
    if severity not in {"error", "warning"}:
        severity = "warning"
    return {
        "page": page,
        "code": code,
        "message": message,
        "severity": severity,
    }


def _has_fixable_layout_issue(issues: list) -> bool:
    return create_quality_agent().plan(issues).action is QualityAction.REPAIR_LAYOUT


def _has_unresolved_error(issues: list) -> bool:
    return any(getattr(issue, "severity", "warning") == "error" for issue in issues)


# QA error classes a fresh translation pass can plausibly fix: the supplier
# echoed the source text or the pipeline fell back to it. Layout/structural
# errors (overlaps, missing images, ...) do not benefit from re-translation.
_SELF_HEALABLE_QA_CODES = set(RETRANSLATABLE_ISSUE_CODES)
_SELF_HEAL_MAX_RETRIES = 1


def _only_self_healable_errors(issues: list) -> bool:
    """Whether every error-severity issue is a re-translatable untranslated case."""
    errors = [issue for issue in issues if getattr(issue, "severity", "warning") == "error"]
    return bool(errors) and all(
        getattr(issue, "code", "") in _SELF_HEALABLE_QA_CODES for issue in errors
    )


def _has_self_healable_error(issues: list) -> bool:
    """Whether any error is a re-translatable untranslated case.

    Mixed failures (e.g. untranslated captions plus a preserved-region
    complaint) still deserve one retry: the pass is nearly free thanks to
    the translation cache, the untranslated blocks regenerate, and the
    final QA gate decides the outcome anyway — the alternative is certain
    failure.
    """
    return has_retranslatable_error(issues)


_QA_ERROR_LABELS = {
    "caption_overlap": "图注重叠",
    "display_formula_misaligned": "公式对齐偏移",
    "empty_page": "空白页",
    "font_size_drift": "字号漂移",
    "formula_changed": "公式变更",
    "formula_clipped": "公式裁切",
    "formula_visible_ink_mismatch": "公式可见内容缺失",
    "inspection_failed": "视觉巡检执行失败",
    "list_font_inconsistent": "列表字号不一致",
    "missing_graphic": "矢量图缺失",
    "missing_image": "图片缺失",
    "page_count_mismatch": "页数不一致",
    "page_size_mismatch": "页面尺寸不一致",
    "preserved_ink_mismatch": "保留区渲染异常",
    "preserved_text_changed": "表格/算法保留区被改动",
    "qa_failed": "译后质检执行失败",
    "qa_open_failed": "PDF 无法检查",
    "reference_bold_style": "参考文献异常加粗",
    "reference_overlap": "参考文献重叠",
    "raster_heading_body_overlap": "标题与正文重叠",
    "table_structure_mismatch": "表格结构错位",
    "text_overlap": "正文重叠",
    "untranslated_block": "整段漏翻",
    "untranslated_caption": "图注漏翻",
    "untranslated_english": "正文疑似漏翻",
    "untranslated_formula_explanation": "公式说明漏翻",
    "untranslated_natural_language": "正文残留外语",
}


def _qa_failure_error_message(issues: list, *, blocking: bool) -> str:
    errors = [
        issue for issue in issues if getattr(issue, "severity", "warning") == "error"
    ]
    by_code: dict[str, set[int]] = {}
    for issue in errors:
        code = str(getattr(issue, "code", "unknown"))
        page = getattr(issue, "page", 0)
        by_code.setdefault(code, set())
        if isinstance(page, int) and page > 0:
            by_code[code].add(page)

    details: list[str] = []
    for code, pages in list(by_code.items())[:3]:
        label = _QA_ERROR_LABELS.get(code, code)
        if pages:
            page_text = "、".join(str(page) for page in sorted(pages)[:5])
            details.append(f"{label}（第 {page_text} 页）")
        else:
            details.append(label)
    if len(by_code) > 3:
        details.append(f"另有 {len(by_code) - 3} 类")

    prefix = "译后检查阻断" if blocking else "译后检查未通过"
    summary = "；".join(details) or "存在未解决错误"
    return f"{prefix}：{len(errors)} 个错误；{summary}。译文已保留，请查看 QA 报告。"


def _qa_issue_score(issues: list) -> tuple[int, int]:
    errors = sum(1 for issue in issues if getattr(issue, "severity", "warning") == "error")
    return errors, len(issues)


_BLOCKING_QA_ERROR_CODES = {
    "qa_open_failed",
    "page_count_mismatch",
    "empty_page",
    "page_size_mismatch",
}


def _has_blocking_qa_error(issues: list) -> bool:
    return any(
        getattr(issue, "severity", "warning") == "error"
        and getattr(issue, "code", "") in _BLOCKING_QA_ERROR_CODES
        for issue in issues
    )


def _fix_translated_outputs(
    input_path: Path,
    trans_result: TranslationResult,
) -> bool:
    from app.services.layout_fix import fix_translated_layout

    fixed = False
    if trans_result.mono_path:
        fixed = fix_translated_layout(trans_result.mono_path) or fixed
    if fixed and trans_result.mono_path and trans_result.dual_path:
        from pdf_zh_translator.pdf_layout import create_dual_pdf

        create_dual_pdf(input_path, trans_result.mono_path, trans_result.dual_path)
    return fixed


def _snapshot_translated_outputs(trans_result: TranslationResult) -> dict[Path, bytes]:
    snapshots: dict[Path, bytes] = {}
    for path in (trans_result.mono_path, trans_result.dual_path):
        if path and path.exists():
            snapshots[path] = path.read_bytes()
    return snapshots


def _restore_translated_outputs(snapshots: dict[Path, bytes]) -> None:
    staged: list[tuple[Path, Path]] = []
    try:
        for path, content in snapshots.items():
            temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.restore")
            temporary.write_bytes(content)
            staged.append((temporary, path))
        for temporary, path in staged:
            temporary.replace(path)
    finally:
        for temporary, _path in staged:
            temporary.unlink(missing_ok=True)


def _record_terminology_candidates(
    paper_id: str,
    loop: asyncio.AbstractEventLoop,
    input_path: Path,
) -> None:
    """Record candidate AI/ML terms for later reviewed corpus updates."""
    try:
        from pdf_zh_translator.corpus import record_candidate_terms

        texts = _read_pdf_texts(input_path)
        candidates_path = settings.base_dir / settings.data_dir / "terminology_candidates.jsonl"
        added = record_candidate_terms(
            texts,
            candidates_path,
            source=f"paper:{paper_id}",
            max_terms=80,
        )
        if added:
            _append_log(paper_id, loop, f"已记录 {added} 个待审核术语候选")
    except Exception as e:
        logger.debug("Terminology candidate recording skipped for %s: %s", paper_id, e)


def _set_translation_stage(
    paper_id: str,
    loop: asyncio.AbstractEventLoop,
    stage: str,
    *,
    progress: float | None = None,
    eta_seconds: int | None = None,
    job_id: str | None = None,
) -> None:
    """Update the live translation stage label (e.g. post-translation QA phase).

    Only applies while the paper is still translating so it never clobbers a
    terminal state. ETA is cleared by default because phases like QA/layout fix
    don't have a meaningful percentage-based estimate.
    """

    async def _update():
        from app.core.database import async_session

        async with async_session() as p_db:
            values: dict[str, object] = {
                "translation_stage": stage,
                "translation_eta_seconds": eta_seconds,
            }
            if progress is not None:
                values["translation_progress"] = max(0.0, min(1.0, progress))
            await p_db.execute(
                update(Paper)
                .where(
                    Paper.id == paper_id,
                    Paper.translation_status == TranslationStatus.TRANSLATING.value,
                )
                .values(**values),
            )
            await _update_translation_job(
                p_db,
                job_id,
                progress=progress,
                heartbeat=True,
            )
            await p_db.commit()

    with contextlib.suppress(Exception):
        asyncio.run_coroutine_threadsafe(_update(), loop)


def _read_pdf_texts(path: Path) -> list[str]:
    """Read non-empty page texts from a PDF (best-effort)."""
    import fitz

    texts: list[str] = []
    document = fitz.open(str(path))
    try:
        for page in document:
            page_text = page.get_text("text").strip()
            if page_text:
                texts.append(page_text)
    finally:
        document.close()
    return texts


def _audit_terminology_usage(
    paper_id: str,
    loop: asyncio.AbstractEventLoop,
    input_path: Path,
    mono_path: Path | None,
) -> None:
    """Log corpus-term adherence drift between source and translated PDFs.

    Advisory only: terminology is a soft prompt constraint, so this surfaces
    professional-consistency drift without failing the translation.
    """
    if mono_path is None:
        return
    try:
        from pdf_zh_translator.corpus import audit_terminology_usage
        from pdf_zh_translator.pdf_layout import translation_unit_source_texts

        # Only audit text that was actually sent for translation. References,
        # tables, and figure internals are intentionally kept in English, so
        # sourcing terms from the whole PDF would guarantee false advisories.
        violations = audit_terminology_usage(
            translation_unit_source_texts(input_path),
            _read_pdf_texts(mono_path),
        )
        if violations:
            sample = "、".join(f"{v['en']}→{v['expected_zh']}" for v in violations[:5])
            _append_log(
                paper_id,
                loop,
                f"术语一致性提示: {len(violations)} 个术语可能未用标准译法 ({sample})",
            )
    except Exception as e:
        logger.debug("Terminology audit skipped for %s: %s", paper_id, e)


def _append_log_text(current_log: str | None, message: str) -> str:
    from datetime import datetime

    timestamp = datetime.now().strftime("%H:%M:%S")
    line = f"[{timestamp}] {message}"
    new_log = f"{current_log}\n{line}" if current_log else line
    if len(new_log) > 2000:
        new_log = new_log[-2000:]
    return new_log


def _append_log(paper_id: str, loop: asyncio.AbstractEventLoop, message: str) -> None:
    """Append a log message to the paper's translation_log."""

    async def _update():
        from app.core.database import async_session

        async with async_session() as p_db:
            result = await p_db.execute(
                select(Paper.translation_log).where(Paper.id == paper_id),
            )
            current_log = result.scalar() or ""
            new_log = _append_log_text(current_log, message)
            await p_db.execute(
                update(Paper).where(Paper.id == paper_id).values(translation_log=new_log),
            )
            await p_db.commit()

    with contextlib.suppress(Exception):
        asyncio.run_coroutine_threadsafe(_update(), loop)


def _estimate_post_translation_seconds(
    page_count: int,
    *,
    qa_mode: str,
    qa_max_passes: int,
) -> int:
    """Estimate QA time from page count and the selected verification policy."""
    pages = max(1, page_count) if isinstance(page_count, int) else 1
    expected_passes = 1.0
    if qa_mode == "iterative" and qa_max_passes > 1:
        expected_passes = 1.35
    estimate = round((10.0 + pages * 1.4) * expected_passes)
    return max(15, min(180, estimate))


def _create_progress_handler(
    paper_id: str,
    loop: asyncio.AbstractEventLoop,
    *,
    job_id: str | None = None,
    progress_start: float = 0.0,
    progress_end: float = 1.0,
    postprocess_eta_seconds: int = 0,
) -> Callable:
    """Create a progress callback that updates the database."""
    _last_pct: list[float] = [0.0]
    _last_milestone: list[int] = [-1]
    started_at = time.monotonic()
    _last_eta_sample: list[tuple[float, float]] = [(0.0, started_at)]
    _smoothed_rate: list[float] = [0.0]

    def _on_progress(pct: float) -> None:
        # Check for cancellation at every progress callback
        if _is_cancel_requested(paper_id):
            _clear_cancel_requested(paper_id)
            raise TranslationCancelledError("Translation cancelled by user")

        pct = max(0.0, min(1.0, pct))
        if pct - _last_pct[0] < _PROGRESS_THROTTLE and pct < 1.0:
            return
        _last_pct[0] = pct
        pct_display = int(pct * 100)
        overall_pct = progress_start + (progress_end - progress_start) * pct
        now = time.monotonic()
        eta_seconds: int | None = None
        eta_text = ""
        if pct > 0.02 and pct < 1.0:
            eta_seconds = _estimate_translation_eta_seconds(
                pct,
                now,
                started_at=started_at,
                last_sample=_last_eta_sample,
                smoothed_rate=_smoothed_rate,
            )
            eta_seconds += max(0, postprocess_eta_seconds)
            eta_text = f"，预计剩余 {_format_duration(eta_seconds)}"
        elif pct >= 1.0 and postprocess_eta_seconds > 0:
            eta_seconds = postprocess_eta_seconds

        async def _update():
            from app.core.database import async_session

            async with async_session() as p_db:
                await p_db.execute(
                    update(Paper)
                    .where(
                        Paper.id == paper_id,
                        Paper.translation_status == TranslationStatus.TRANSLATING.value,
                    )
                    .values(
                        translation_progress=overall_pct,
                        translation_stage="翻译中",
                        translation_eta_seconds=eta_seconds,
                    ),
                )
                await _update_translation_job(
                    p_db,
                    job_id,
                    progress=overall_pct,
                    heartbeat=True,
                )
                await p_db.commit()

        with contextlib.suppress(Exception):
            asyncio.run_coroutine_threadsafe(_update(), loop)

        # Log milestone progress
        milestone = pct_display // _PROGRESS_LOG_STEP
        if pct >= 1.0 or (
            pct_display >= _PROGRESS_LOG_STEP and milestone > _last_milestone[0]
        ):
            _last_milestone[0] = milestone
            _append_log(paper_id, loop, f"翻译进度: {pct_display}%{eta_text}")

    return _on_progress


def _estimate_translation_eta_seconds(
    pct: float,
    now: float,
    *,
    started_at: float,
    last_sample: list[tuple[float, float]],
    smoothed_rate: list[float],
) -> int:
    """Estimate remaining translation time from recent progress velocity."""
    pct = max(0.0, min(1.0, pct))
    if pct <= 0.0 or pct >= 1.0:
        return 0

    last_pct, last_time = last_sample[0]
    delta_pct = pct - last_pct
    delta_time = max(0.0, now - last_time)
    if delta_pct > 0.0 and delta_time >= 0.5:
        instant_rate = delta_pct / delta_time
        if smoothed_rate[0] <= 0.0:
            smoothed_rate[0] = instant_rate
        else:
            smoothed_rate[0] = smoothed_rate[0] * 0.65 + instant_rate * 0.35
        last_sample[0] = (pct, now)

    elapsed = max(0.0, now - started_at)
    average_rate = pct / elapsed if elapsed > 0.0 else 0.0
    rate = smoothed_rate[0] if smoothed_rate[0] > 0.0 else average_rate
    if rate <= 0.0:
        return 0
    return max(0, int((1.0 - pct) / rate))


def _format_duration(seconds: int) -> str:
    if seconds < 60:
        return f"{seconds}秒"
    minutes, sec = divmod(seconds, 60)
    if minutes < 60:
        return f"{minutes}分{sec:02d}秒"
    hours, minutes = divmod(minutes, 60)
    return f"{hours}小时{minutes:02d}分"


def _update_paper_result(
    paper: Paper,
    trans_result: TranslationResult,
    output_dir: Path,
) -> None:
    """Update paper with translation result."""
    if trans_result.success:
        paper.translation_status = TranslationStatus.COMPLETED.value
        paper.translation_progress = 1.0
        translations_base = settings.translations_path.resolve()
        if trans_result.mono_path:
            resolved = trans_result.mono_path.resolve()
            if resolved.is_relative_to(translations_base):
                paper.translated_filename = str(resolved.relative_to(translations_base))
            else:
                logger.warning("Mono path outside translations dir: %s", trans_result.mono_path)
        if trans_result.dual_path:
            resolved = trans_result.dual_path.resolve()
            if resolved.is_relative_to(translations_base):
                paper.dual_filename = str(resolved.relative_to(translations_base))
            else:
                logger.warning("Dual path outside translations dir: %s", trans_result.dual_path)
    else:
        paper.translation_status = TranslationStatus.FAILED.value
        paper.translation_error = trans_result.error
        cleanup_output_dir(output_dir)
        logger.error("Translation failed for paper %s: %s", paper.id, trans_result.error)


async def _serve_paper_file(
    paper: Paper,
    file_attr: str,
    base_dir: Path,
    download_name: str | None = None,
) -> FileResponse:
    """Shared helper for download/view endpoints."""
    file_path = _get_paper_file(paper, file_attr, base_dir)
    return FileResponse(
        file_path,
        filename=download_name,
        media_type="application/pdf",
        headers={
            "Accept-Ranges": "bytes",
            "Cache-Control": "private, max-age=300, no-transform",
            "Content-Encoding": "identity",
        },
    )


@router.get("/{paper_id}/download/original")
async def download_original(
    paper_id: str,
    db: Annotated[AsyncSession, Depends(get_session)],
    access_scope: AccessScope,
) -> FileResponse:
    """Download the original PDF file."""
    paper = await _get_paper_or_404(paper_id, db, access_scope)
    return await _serve_paper_file(
        paper,
        "stored_filename",
        settings.papers_path,
        paper.original_filename,
    )


@router.get("/{paper_id}/download/translated")
async def download_translated(
    paper_id: str,
    db: Annotated[AsyncSession, Depends(get_session)],
    access_scope: AccessScope,
) -> FileResponse:
    """Download the translated PDF file."""
    paper = await _get_paper_or_404(paper_id, db, access_scope)
    name = f"{Path(paper.original_filename).stem}_zh.pdf"
    return await _serve_paper_file(
        paper,
        "translated_filename",
        settings.translations_path,
        name,
    )


@router.get("/{paper_id}/download/dual")
async def download_dual(
    paper_id: str,
    db: Annotated[AsyncSession, Depends(get_session)],
    access_scope: AccessScope,
) -> FileResponse:
    """Download the dual-language PDF file."""
    paper = await _get_paper_or_404(paper_id, db, access_scope)
    name = f"{Path(paper.original_filename).stem}_dual.pdf"
    return await _serve_paper_file(paper, "dual_filename", settings.translations_path, name)


@router.get("/{paper_id}/view/original")
async def view_original(
    paper_id: str,
    db: Annotated[AsyncSession, Depends(get_session)],
    access_scope: AccessScope,
) -> FileResponse:
    """View a browser-safe copy while preserving the raw original download."""
    paper = await _get_paper_or_404(paper_id, db, access_scope)
    source_path = _get_paper_file(paper, "stored_filename", settings.papers_path)
    view_path = await asyncio.to_thread(safe_pdf_for_use, source_path)
    return FileResponse(
        view_path,
        media_type="application/pdf",
        headers={
            "Accept-Ranges": "bytes",
            "Cache-Control": "private, max-age=300, no-transform",
            "Content-Encoding": "identity",
        },
    )


@router.get("/{paper_id}/view/translated")
async def view_translated(
    paper_id: str,
    db: Annotated[AsyncSession, Depends(get_session)],
    access_scope: AccessScope,
) -> FileResponse:
    """View the translated PDF file in browser."""
    paper = await _get_paper_or_404(paper_id, db, access_scope)
    return await _serve_paper_file(paper, "translated_filename", settings.translations_path)


_PREVIEW_DPI = 110
_PREVIEW_WEBP_QUALITY = 72


def _render_page_preview(pdf_path: Path, page_number: int, out_path: Path) -> None:
    """Render one PDF page to a WebP preview (cached alongside translations).

    WebP at ~90KB/page vs ~150KB for JPEG — every kilobyte matters through
    the throttled public tunnel."""
    import fitz
    from PIL import Image

    document = fitz.open(str(pdf_path))
    try:
        page = document[page_number - 1]
        pixmap = page.get_pixmap(dpi=_PREVIEW_DPI)
        image = Image.frombytes("RGB", (pixmap.width, pixmap.height), pixmap.samples)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        image.save(out_path, "WEBP", quality=_PREVIEW_WEBP_QUALITY, method=4)
    finally:
        document.close()


@router.get("/{paper_id}/preview/{which}/{page_number}")
async def preview_page(
    paper_id: str,
    which: str,
    page_number: int,
    db: Annotated[AsyncSession, Depends(get_session)],
    access_scope: AccessScope,
) -> FileResponse:
    """Per-page JPEG preview for slow connections.

    The public tunnel sustains only ~0.1-1MB/s, so streaming an 11MB PDF
    through pdf.js range requests feels sluggish; a ~150KB page image is
    what actually makes the reader usable on that link. Rendered on demand
    and cached on disk (cleared with the translation outputs on re-run).
    """
    if which not in ("original", "translated"):
        raise HTTPException(400, "which must be 'original' or 'translated'")
    paper = await _get_paper_or_404(paper_id, db, access_scope)
    if which == "original":
        pdf_path = _get_paper_file(paper, "stored_filename", settings.papers_path)
        pdf_path = await asyncio.to_thread(safe_pdf_for_use, pdf_path)
    else:
        if paper.translation_status != TranslationStatus.COMPLETED.value:
            raise HTTPException(409, "Translation not completed yet")
        pdf_path = _get_paper_file(paper, "translated_filename", settings.translations_path)
    if not 1 <= page_number <= max(paper.page_count, 1):
        raise HTTPException(404, "Page out of range")

    cache_path = (
        settings.translations_path
        / paper.id
        / "preview"
        / which
        / f"{pdf_path.stat().st_mtime_ns}-{page_number}.webp"
    )
    if not cache_path.exists():
        try:
            await asyncio.to_thread(_render_page_preview, pdf_path, page_number, cache_path)
        except Exception as exc:
            logger.warning("Preview render failed for %s page %d: %s", paper_id, page_number, exc)
            raise HTTPException(500, "Preview render failed") from None
    return FileResponse(
        cache_path,
        media_type="image/webp",
        headers={"Cache-Control": "private, max-age=3600"},
    )


@router.get("/{paper_id}/qa-report")
async def get_qa_report(
    paper_id: str,
    db: Annotated[AsyncSession, Depends(get_session)],
    access_scope: AccessScope,
) -> dict:
    """Return the machine-readable post-translation QA report."""
    paper = await _get_paper_or_404(paper_id, db, access_scope)
    report_path = _qa_report_path(paper)
    if not report_path.exists():
        raise HTTPException(404, "QA report not found")
    try:
        data = json.loads(report_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        raise HTTPException(500, "QA report is invalid") from None
    if not isinstance(data, dict):
        raise HTTPException(500, "QA report is invalid")
    return data


@router.get("/{paper_id}/editable-figures/source-manifest")
async def get_editable_figure_manifest(
    paper_id: str,
    db: Annotated[AsyncSession, Depends(get_session)],
    access_scope: AccessScope,
) -> dict[str, Any]:
    """Return UI-safe editable-figure source manifest metadata for a paper."""
    paper = await _get_paper_or_404(paper_id, db, access_scope)
    manifest_path = _editable_source_manifest_path(paper)
    if not manifest_path.exists():
        raise HTTPException(404, "Editable figure source manifest not found")
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        raise HTTPException(500, "Editable figure source manifest is invalid") from None
    if not isinstance(manifest, dict):
        raise HTTPException(500, "Editable figure source manifest is invalid")
    return _editable_figure_manifest_response(paper, manifest, manifest_path)


@router.post("/{paper_id}/editable-figures/extract")
async def extract_editable_figures(
    paper_id: str,
    db: Annotated[AsyncSession, Depends(get_session)],
    access_scope: AccessScope,
    max_figures: int = 100,
) -> dict[str, Any]:
    """Extract figure source images and write an editable-PPT provenance manifest."""
    if max_figures < 1 or max_figures > 200:
        raise HTTPException(400, "max_figures must be between 1 and 200")

    paper = await _get_paper_or_404(paper_id, db, access_scope)
    source_pdf = _get_paper_file(paper, "stored_filename", settings.papers_path)

    from pdf_zh_translator.editable_figures import extract_pdf_figures

    try:
        manifest = await asyncio.to_thread(
            extract_pdf_figures,
            source_pdf,
            _editable_figures_root(),
            paper_id=paper.id,
            max_figures=max_figures,
        )
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    except FileNotFoundError as exc:
        raise HTTPException(404, str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(500, str(exc)) from exc

    return _editable_figure_manifest_response(
        paper,
        manifest,
        _editable_source_manifest_path(paper),
    )


@router.patch("/{paper_id}")
async def update_paper(
    paper_id: str,
    request: PaperUpdateRequest,
    db: Annotated[AsyncSession, Depends(get_session)],
    access_scope: AccessScope,
) -> PaperResponse:
    """Update paper metadata (title, tags, notes)."""
    paper = await _get_paper_or_404(paper_id, db, access_scope)
    if request.title is not None:
        paper.title = request.title
    if request.tags is not None:
        paper.tags = request.tags
    if request.notes is not None:
        paper.notes = request.notes
    await db.commit()
    await db.refresh(paper)
    has_original = _file_exists_safe(settings.papers_path, paper.stored_filename)
    has_translated = _file_exists_safe(settings.translations_path, paper.translated_filename)
    has_dual = _file_exists_safe(settings.translations_path, paper.dual_filename)
    has_qa_report = _qa_report_exists(paper)
    return _paper_to_response(
        paper,
        has_original=has_original,
        has_translated=has_translated,
        has_dual=has_dual,
        has_qa_report=has_qa_report,
    )
