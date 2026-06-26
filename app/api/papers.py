"""Paper management API routes."""

from __future__ import annotations

import asyncio
import contextlib
import logging
import os
import re
import threading
import time
from collections.abc import Callable
from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, BackgroundTasks, Depends, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel, field_validator
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.database import get_session
from app.models.paper import Paper, TranslationStatus
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

# Paper ID format: 12-character hex string (uuid4 hex[:12])
_PAPER_ID_RE = re.compile(r"^[0-9a-f]{12}$")

# Validation limits
_MAX_TITLE_LEN = 500
_MAX_TAGS_LEN = 1000
_MAX_NOTES_LEN = 10_000
_MAX_SEARCH_LEN = 200
_PROGRESS_THROTTLE = 0.05


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
    tags: str
    notes: str
    has_original: bool
    has_translated: bool
    has_dual: bool
    created_at: str
    updated_at: str

    model_config = {"from_attributes": True}


class PaperListResponse(BaseModel):
    """Response model for paper list with pagination."""

    papers: list[PaperResponse]
    total: int
    offset: int
    limit: int


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
) -> PaperResponse:
    """Convert a Paper model to a PaperResponse."""
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
        tags=paper.tags,
        notes=paper.notes,
        has_original=has_original,
        has_translated=has_translated,
        has_dual=has_dual,
        created_at=paper.created_at.isoformat() if paper.created_at else "",
        updated_at=paper.updated_at.isoformat() if paper.updated_at else "",
    )


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


async def _get_paper_or_404(paper_id: str, db: AsyncSession) -> Paper:
    """Fetch paper by ID or raise 404."""
    _validate_paper_id(paper_id)
    result = await db.execute(select(Paper).where(Paper.id == paper_id))
    paper = result.scalar_one_or_none()
    if not paper:
        raise HTTPException(404, "Paper not found")
    return paper


@router.get("/")
async def list_papers(
    db: Annotated[AsyncSession, Depends(get_session)],
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
    base = select(Paper).order_by(Paper.created_at.desc())
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
            )
            for p in papers
        ]

    paper_responses = await asyncio.to_thread(_check_files)

    return PaperListResponse(papers=paper_responses, total=total, offset=offset, limit=limit)


@router.post("/upload")
async def upload_paper(
    db: Annotated[AsyncSession, Depends(get_session)],
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
) -> PaperResponse:
    """Get a specific paper by ID.

    Args:
        paper_id: The paper's unique identifier

    Returns:
        PaperResponse with the paper data

    Raises:
        HTTPException: If paper not found (404)
    """
    paper = await _get_paper_or_404(paper_id, db)
    # Check file existence safely (respects path traversal guard)
    has_original = _file_exists_safe(settings.papers_path, paper.stored_filename)
    has_translated = _file_exists_safe(settings.translations_path, paper.translated_filename)
    has_dual = _file_exists_safe(settings.translations_path, paper.dual_filename)
    return _paper_to_response(
        paper,
        has_original=has_original,
        has_translated=has_translated,
        has_dual=has_dual,
    )


@router.delete("/{paper_id}")
async def delete_paper(
    paper_id: str,
    db: Annotated[AsyncSession, Depends(get_session)],
) -> dict[str, bool]:
    """Delete a paper and its associated files.

    Args:
        paper_id: The paper's unique identifier

    Returns:
        Success status

    Raises:
        HTTPException: If paper not found (404) or translation in progress (409)
    """
    paper = await _get_paper_or_404(paper_id, db)
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
    background_tasks: BackgroundTasks,
    db: Annotated[AsyncSession, Depends(get_session)],
    backend: str = "",
    quality: str = "balanced",
    preserve_graphics_text: bool = True,
    skip_overflow: bool = False,
) -> dict[str, bool | str]:
    """Start translation for a paper.

    Args:
        paper_id: The paper's unique identifier
        backend: Translation backend (deepseek, openai, google)
        quality: Quality preset (fast, balanced, quality)
        preserve_graphics_text: Keep text inside figures/tables unchanged
        skip_overflow: Leave original text when Chinese won't fit its bbox

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

    # Atomic check-and-set: prevents two concurrent requests from both
    # starting translation for the same paper (TOCTOU race condition).
    result = await db.execute(
        update(Paper)
        .where(
            Paper.id == paper_id,
            Paper.translation_status != TranslationStatus.TRANSLATING.value,
        )
        .values(
            translation_status=TranslationStatus.TRANSLATING.value,
            translation_progress=0.0,
            translation_error=None,
            translated_filename="",
            dual_filename="",
            translation_log="",
        ),
    )
    if result.rowcount == 0:
        # Either paper doesn't exist or already translating
        await _get_paper_or_404(paper_id, db)  # raises 404 if missing
        raise HTTPException(409, "Translation already in progress")
    await db.commit()

    background_tasks.add_task(
        _run_translation,
        paper_id,
        backend or settings.translation_backend,
        quality,
        preserve_graphics_text,
        skip_overflow,
    )

    return {"ok": True, "status": "translating"}


@router.post("/{paper_id}/cancel")
async def cancel_translation(
    paper_id: str,
    db: Annotated[AsyncSession, Depends(get_session)],
) -> dict[str, bool | str]:
    """Request cancellation of an in-progress translation.

    The translation will stop at the next progress callback.
    """
    paper = await _get_paper_or_404(paper_id, db)
    if paper.translation_status != TranslationStatus.TRANSLATING.value:
        raise HTTPException(409, "Paper is not currently being translated")
    _mark_cancel_requested(paper_id)
    return {"ok": True, "status": "cancelling"}


_BACKEND_API_KEY_ATTRS = {
    "deepseek": "deepseek_api_key",
    "openai": "openai_api_key",
    "deepl": "deepl_api_key",
}


def _resolve_backend_config(
    backend: str,
    quality_preset: QualityPreset,
    preserve_graphics_text: bool = False,
    skip_overflow: bool = False,
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
    )


def _reset_paper_status(paper_id: str, error_message: str) -> None:
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
) -> None:
    acquired = _translation_semaphore.acquire(timeout=300)
    if not acquired:
        logger.error("Translation queue full, rejecting paper %s", paper_id)
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
        )

        asyncio.run(_do_translate(paper_id, config, quality))

    except HTTPException as e:
        # Config validation errors (missing API key, etc.) — surface the real message
        _reset_paper_status(paper_id, e.detail)
    except Exception:
        # Safety net: if anything outside _do_translate fails, reset paper status
        # so it doesn't stay stuck as "translating" forever
        logger.exception("Unhandled error in _run_translation for paper %s", paper_id)
        _reset_paper_status(paper_id, "Unexpected server error during translation")

    finally:
        _clear_cancel_requested(paper_id)
        _translation_semaphore.release()


async def _do_translate(
    paper_id: str,
    config: TranslationConfig,
    quality: str,
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
            _clear_cancel_requested(paper_id)
            return

        stored_filename = paper.stored_filename

        # Validate paths while session is open (can write error status directly)
        papers_base = settings.papers_path.resolve()
        input_path = (settings.papers_path / stored_filename).resolve()
        if not input_path.is_relative_to(papers_base):
            logger.error("Path traversal detected for paper %s", paper_id)
            paper.translation_status = TranslationStatus.FAILED.value
            paper.translation_error = "Invalid file path"
            await db.commit()
            _clear_cancel_requested(paper_id)
            return

        if not input_path.exists():
            logger.error("Original file missing for paper %s", paper_id)
            paper.translation_status = TranslationStatus.FAILED.value
            paper.translation_error = "Original PDF file not found"
            await db.commit()
            _clear_cancel_requested(paper_id)
            return

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

    # Phase 2: Run translation (no DB session held)
    on_progress = _create_progress_handler(paper_id, loop)

    try:
        trans_result = translate_pdf_sync(input_path, output_dir, config, on_progress)
    except TranslationCancelledError:
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
                await db.commit()
        cleanup_output_dir(output_dir)
        _clear_cancel_requested(paper_id)
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
                await db.commit()
        cleanup_output_dir(output_dir)
        _clear_cancel_requested(paper_id)
        return

    if trans_result.success and trans_result.mono_path:
        _run_post_translation_qa(paper_id, loop, input_path, trans_result)

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
            _append_log(paper_id, loop, f"翻译完成! 耗时 {elapsed:.0f}秒")
        else:
            _append_log(paper_id, loop, f"翻译失败: {trans_result.error} (耗时 {elapsed:.0f}秒)")
        await db.commit()

        # Send Feishu notification
        if settings.feishu_webhook_url:
            from app.services.notify import notify_translation_complete

            notify_translation_complete(
                settings.feishu_webhook_url,
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
) -> None:
    """Run a fast layout/translation QA pass and try one layout repair."""
    mono_path = trans_result.mono_path
    if mono_path is None:
        return
    try:
        from pdf_zh_translator.pdf_layout import verify_translation

        _append_log(paper_id, loop, "正在检查译文和版面")
        _record_terminology_candidates(paper_id, loop, input_path)
        issues = verify_translation(input_path, mono_path)
        if any("overlap" in issue for issue in issues):
            from app.services.layout_fix import fix_translated_layout

            fixed = fix_translated_layout(mono_path)
            if trans_result.dual_path:
                fix_translated_layout(trans_result.dual_path)
            if fixed:
                _append_log(paper_id, loop, "检测到文字重叠，已自动执行一次版面修复")
                issues = verify_translation(input_path, mono_path)
        if issues:
            _append_log(paper_id, loop, f"检查发现 {len(issues)} 个潜在问题")
            for issue in issues[:3]:
                _append_log(paper_id, loop, issue)
        else:
            _append_log(paper_id, loop, "译文检查通过")
    except Exception as e:
        logger.warning("Post-translation QA failed for %s: %s", paper_id, sanitize_error(e))


def _record_terminology_candidates(
    paper_id: str,
    loop: asyncio.AbstractEventLoop,
    input_path: Path,
) -> None:
    """Record candidate AI/ML terms for later reviewed corpus updates."""
    try:
        import fitz

        from pdf_zh_translator.corpus import record_candidate_terms

        texts: list[str] = []
        document = fitz.open(str(input_path))
        try:
            for page in document:
                page_text = page.get_text("text").strip()
                if page_text:
                    texts.append(page_text)
        finally:
            document.close()

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
) -> Callable:
    """Create a progress callback that updates the database."""
    _last_pct: list[float] = [0.0]
    _last_milestone: list[int] = [-1]
    started_at = time.monotonic()

    def _on_progress(pct: float) -> None:
        # Check for cancellation at every progress callback
        if _is_cancel_requested(paper_id):
            _clear_cancel_requested(paper_id)
            raise TranslationCancelledError("Translation cancelled by user")

        if pct - _last_pct[0] < _PROGRESS_THROTTLE and pct < 1.0:
            return
        _last_pct[0] = pct
        pct_display = int(pct * 100)
        elapsed = max(0.0, time.monotonic() - started_at)
        eta_text = ""
        if pct > 0.02 and pct < 1.0:
            eta_seconds = max(0, int((elapsed / pct) - elapsed))
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
                    .values(translation_progress=pct),
                )
                await p_db.commit()

        with contextlib.suppress(Exception):
            asyncio.run_coroutine_threadsafe(_update(), loop)

        # Log milestone progress
        milestone = pct_display // 25
        if pct >= 1.0 or (pct_display >= 25 and milestone > _last_milestone[0]):
            _last_milestone[0] = milestone
            _append_log(paper_id, loop, f"翻译进度: {pct_display}%{eta_text}")

    return _on_progress


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
) -> FileResponse:
    """Download the original PDF file."""
    paper = await _get_paper_or_404(paper_id, db)
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
) -> FileResponse:
    """Download the translated PDF file."""
    paper = await _get_paper_or_404(paper_id, db)
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
) -> FileResponse:
    """Download the dual-language PDF file."""
    paper = await _get_paper_or_404(paper_id, db)
    name = f"{Path(paper.original_filename).stem}_dual.pdf"
    return await _serve_paper_file(paper, "dual_filename", settings.translations_path, name)


@router.get("/{paper_id}/view/original")
async def view_original(
    paper_id: str,
    db: Annotated[AsyncSession, Depends(get_session)],
) -> FileResponse:
    """View the original PDF file in browser."""
    paper = await _get_paper_or_404(paper_id, db)
    return await _serve_paper_file(paper, "stored_filename", settings.papers_path)


@router.get("/{paper_id}/view/translated")
async def view_translated(
    paper_id: str,
    db: Annotated[AsyncSession, Depends(get_session)],
) -> FileResponse:
    """View the translated PDF file in browser."""
    paper = await _get_paper_or_404(paper_id, db)
    return await _serve_paper_file(paper, "translated_filename", settings.translations_path)


@router.patch("/{paper_id}")
async def update_paper(
    paper_id: str,
    request: PaperUpdateRequest,
    db: Annotated[AsyncSession, Depends(get_session)],
) -> PaperResponse:
    """Update paper metadata (title, tags, notes)."""
    paper = await _get_paper_or_404(paper_id, db)
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
    return _paper_to_response(
        paper,
        has_original=has_original,
        has_translated=has_translated,
        has_dual=has_dual,
    )
