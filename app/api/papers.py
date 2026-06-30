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
from collections.abc import Callable
from datetime import datetime, timezone
from pathlib import Path
from typing import Annotated, Any

from fastapi import APIRouter, BackgroundTasks, Depends, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel, field_validator
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.access import get_request_access_scope
from app.core.config import settings
from app.core.database import get_session
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
from app.services.translator import (
    QualityPreset,
    TranslationConfig,
    TranslationResult,
    sanitize_error,
    translate_pdf_sync,
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

    if status == TranslationStatus.TRANSLATING.value:
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
    if paper.translation_status == TranslationStatus.TRANSLATING.value:
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
        backend: Translation backend (deepseek, openai, google)
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
    valid_backends = {"", "deepseek", "openai", "google", "deepl", "ollama"}
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

    # Atomic check-and-set: prevents two concurrent requests from both
    # starting translation for the same paper (TOCTOU race condition).
    result = await db.execute(
        update(Paper)
        .where(
            Paper.id == paper_id,
            Paper.access_scope == access_scope,
            Paper.translation_status != TranslationStatus.TRANSLATING.value,
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
    job_id = generate_job_id()
    job = TranslationJob(
        id=job_id,
        paper_id=paper_id,
        backend=backend or settings.translation_backend,
        quality=quality,
        preserve_graphics_text=preserve_graphics_text,
        skip_overflow=skip_overflow,
        qa_mode=qa_mode,
        qa_max_passes=qa_max_passes,
        ocr_mode=ocr_mode,
        ocr_language=ocr_language,
        ocr_dpi=ocr_dpi,
        status=TranslationJobStatus.QUEUED.value,
    )
    db.add(job)
    await db.commit()

    _schedule_background_task(
        _run_translation,
        paper_id,
        backend or settings.translation_backend,
        quality,
        preserve_graphics_text,
        skip_overflow,
        qa_mode,
        qa_max_passes,
        ocr_mode,
        ocr_language,
        ocr_dpi,
        job_id,
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
    if paper.translation_status != TranslationStatus.TRANSLATING.value:
        raise HTTPException(409, "Paper is not currently being translated")
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
    "openai": "openai_api_key",
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
) -> TranslationConfig:
    """Build TranslationConfig from backend name and quality preset.

    Resolves API keys from settings, handles fast-mode override to Google.
    Raises HTTPException if a required API key is missing.
    """
    api_key = ""
    base_url = ""
    model_name = ""

    if backend == "deepseek":
        api_key = settings.deepseek_api_key.get_secret_value()
        model_name = settings.deepseek_model
    elif backend == "openai":
        api_key = settings.openai_api_key.get_secret_value()
        base_url = settings.openai_base_url
        model_name = settings.openai_model
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
        if not api_key and not has_env_key:
            raise HTTPException(
                400,
                f"Backend '{backend}' requires an API key. Set {prefixed_key} in your .env file.",
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
    error: str | None = None,
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
    if error is not None:
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
) -> None:
    acquired = _translation_semaphore.acquire(timeout=300)
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
        config = _resolve_backend_config(
            backend,
            quality_preset,
            preserve_graphics_text=preserve_graphics_text,
            skip_overflow=skip_overflow,
            ocr_mode=ocr_mode,
            ocr_language=ocr_language,
            ocr_dpi=ocr_dpi,
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
    on_progress = _create_progress_handler(paper_id, loop, job_id=job_id)

    try:
        trans_result = await asyncio.to_thread(
            translate_pdf_sync,
            input_path,
            output_dir,
            config,
            on_progress,
        )
    except TranslationCancelledError:
        await _finalize_cancelled_translation(paper_id, loop, output_dir, start_time, job_id)
        return
    except Exception as e:
        elapsed = time.monotonic() - start_time
        logger.exception("Translation crashed for paper %s", paper_id)
        _append_log(paper_id, loop, f"翻译失败: {sanitize_error(e)} (耗时 {elapsed:.0f}秒)")
        async with async_session() as db:
            result = await db.execute(select(Paper).where(Paper.id == paper_id))
            paper = result.scalar_one_or_none()
            if paper:
                paper.translation_status = TranslationStatus.FAILED.value
                paper.translation_error = sanitize_error(e)
                await _update_translation_job(
                    db,
                    job_id,
                    status=TranslationJobStatus.FAILED.value,
                    error=sanitize_error(e),
                    finished=True,
                )
                await db.commit()
        cleanup_output_dir(output_dir)
        _clear_cancel_requested(paper_id)
        return

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
            )
        except TranslationCancelledError:
            await _finalize_cancelled_translation(paper_id, loop, output_dir, start_time, job_id)
            return
        if _has_blocking_qa_error(unresolved_issues):
            trans_result = TranslationResult(
                error="QA found blocking layout or translation issues after post-checks"
            )
        elif _has_unresolved_error(unresolved_issues):
            _append_log(
                paper_id,
                loop,
                "译后检查仍有未解决问题，已保留译文并生成 QA 报告供复核",
            )

    elapsed = time.monotonic() - start_time

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
        if trans_result.success:
            await _update_translation_job(
                db,
                job_id,
                status=TranslationJobStatus.COMPLETED.value,
                progress=1.0,
                finished=True,
            )
        else:
            await _update_translation_job(
                db,
                job_id,
                status=TranslationJobStatus.FAILED.value,
                error=trans_result.error or "Translation failed",
                finished=True,
            )
        if trans_result.success:
            _append_log(paper_id, loop, f"翻译完成! 耗时 {elapsed:.0f}秒")
        else:
            _append_log(paper_id, loop, f"翻译失败: {trans_result.error} (耗时 {elapsed:.0f}秒)")
        await db.commit()

        # Send Feishu notification
        webhook_url = settings.feishu_webhook_url
        if isinstance(webhook_url, str) and webhook_url:
            from app.services.notify import notify_translation_complete

            notify_translation_complete(
                webhook_url,
                paper.title,
                paper_id,
                trans_result.success,
                trans_result.error,
                base_url=settings.base_url,
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
) -> list:
    """Run layout/translation QA.

    single: one verification pass and one possible layout repair.
    iterative: verify/fix repeatedly until clean or max_passes is reached.
    """
    mono_path = trans_result.mono_path
    if mono_path is None:
        return []
    try:
        from pdf_zh_translator.pdf_layout import verify_translation_issues

        if _is_cancel_requested(paper_id):
            raise TranslationCancelledError("Translation cancelled by user")
        _append_log(paper_id, loop, "正在检查译文和版面")
        _set_translation_stage(paper_id, loop, "译后检查")
        _record_terminology_candidates(paper_id, loop, input_path)
        _audit_terminology_usage(paper_id, loop, input_path, mono_path)
        passes = max(1, max_passes if qa_mode == "iterative" else 1)
        issues = []
        pass_history = []
        passes_run = 0
        repair_attempted = False
        for pass_index in range(1, passes + 1):
            if _is_cancel_requested(paper_id):
                raise TranslationCancelledError("Translation cancelled by user")
            issues = verify_translation_issues(input_path, mono_path)
            passes_run += 1
            if not issues:
                pass_history.append(_qa_pass_summary(pass_index, issues))
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

            if not _has_fixable_layout_issue(issues):
                pass_history.append(_qa_pass_summary(pass_index, issues))
                break
            _set_translation_stage(paper_id, loop, "版面修复")
            fixed = _fix_translated_outputs(trans_result)
            repair_attempted = True
            pass_history.append(
                _qa_pass_summary(pass_index, issues, repair_attempted_after=True)
            )
            if not fixed:
                break
            _append_log(paper_id, loop, "检测到可修复版面问题，已自动执行一次版面修复")
            if qa_mode != "iterative":
                issues = verify_translation_issues(input_path, mono_path)
                passes_run += 1
                pass_history.append(_qa_pass_summary(passes_run, issues))
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
        logger.warning("Post-translation QA failed for %s: %s", paper_id, sanitize_error(e))
        _write_qa_report(
            trans_result,
            [],
            qa_mode=qa_mode,
            passes_run=0,
            repair_attempted=False,
            pass_history=[],
            status="qa_failed",
            error=sanitize_error(e),
        )
        return []


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
) -> dict:
    issue_items = [_qa_issue_to_dict(issue) for issue in issues]
    return {
        "pass": pass_index,
        "issue_count": len(issue_items),
        "error_count": sum(1 for item in issue_items if item["severity"] == "error"),
        "warning_count": sum(1 for item in issue_items if item["severity"] != "error"),
        "repair_attempted_after": repair_attempted_after,
        "issue_codes": sorted({item["code"] for item in issue_items})[:12],
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
    return any(
        getattr(issue, "code", "") in {"caption_overlap", "text_overlap"} for issue in issues
    )


def _has_unresolved_error(issues: list) -> bool:
    return any(getattr(issue, "severity", "warning") == "error" for issue in issues)


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


def _fix_translated_outputs(trans_result: TranslationResult) -> bool:
    from app.services.layout_fix import fix_translated_layout

    fixed = False
    if trans_result.mono_path:
        fixed = fix_translated_layout(trans_result.mono_path) or fixed
    if trans_result.dual_path:
        fixed = fix_translated_layout(trans_result.dual_path) or fixed
    return fixed


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
    eta_seconds: int | None = None,
) -> None:
    """Update the live translation stage label (e.g. post-translation QA phase).

    Only applies while the paper is still translating so it never clobbers a
    terminal state. ETA is cleared by default because phases like QA/layout fix
    don't have a meaningful percentage-based estimate.
    """

    async def _update():
        from app.core.database import async_session

        async with async_session() as p_db:
            await p_db.execute(
                update(Paper)
                .where(
                    Paper.id == paper_id,
                    Paper.translation_status == TranslationStatus.TRANSLATING.value,
                )
                .values(translation_stage=stage, translation_eta_seconds=eta_seconds),
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

        violations = audit_terminology_usage(
            _read_pdf_texts(input_path),
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


def _append_log(paper_id: str, loop: asyncio.AbstractEventLoop, message: str) -> None:
    """Append a log message to the paper's translation_log."""
    from datetime import datetime

    timestamp = datetime.now().strftime("%H:%M:%S")
    line = f"[{timestamp}] {message}"

    async def _update():
        from app.core.database import async_session

        async with async_session() as p_db:
            result = await p_db.execute(
                select(Paper.translation_log).where(Paper.id == paper_id),
            )
            current_log = result.scalar() or ""
            new_log = f"{current_log}\n{line}" if current_log else line
            # Keep last 2000 chars to avoid unbounded growth
            if len(new_log) > 2000:
                new_log = new_log[-2000:]
            await p_db.execute(
                update(Paper).where(Paper.id == paper_id).values(translation_log=new_log),
            )
            await p_db.commit()

    with contextlib.suppress(Exception):
        asyncio.run_coroutine_threadsafe(_update(), loop)


def _create_progress_handler(
    paper_id: str,
    loop: asyncio.AbstractEventLoop,
    *,
    job_id: str | None = None,
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

        if pct - _last_pct[0] < _PROGRESS_THROTTLE and pct < 1.0:
            return
        _last_pct[0] = pct
        pct_display = int(pct * 100)
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
            eta_text = f"，预计剩余 {_format_duration(eta_seconds)}"

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
                        translation_progress=pct,
                        translation_stage="翻译中",
                        translation_eta_seconds=eta_seconds,
                    ),
                )
                await _update_translation_job(
                    p_db,
                    job_id,
                    progress=pct,
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
        logger.info("Translation completed for paper %s", paper.id)
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
    return FileResponse(file_path, filename=download_name, media_type="application/pdf")


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
    """View the original PDF file in browser."""
    paper = await _get_paper_or_404(paper_id, db, access_scope)
    return await _serve_paper_file(paper, "stored_filename", settings.papers_path)


@router.get("/{paper_id}/view/translated")
async def view_translated(
    paper_id: str,
    db: Annotated[AsyncSession, Depends(get_session)],
    access_scope: AccessScope,
) -> FileResponse:
    """View the translated PDF file in browser."""
    paper = await _get_paper_or_404(paper_id, db, access_scope)
    return await _serve_paper_file(paper, "translated_filename", settings.translations_path)


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
