"""Paper management API routes."""

import asyncio
import contextlib
import logging
import os
import re
import shutil
import threading
from collections.abc import Callable
from pathlib import Path

from fastapi import APIRouter, BackgroundTasks, Depends, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel, field_validator
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.database import get_session
from app.models.paper import Paper, TranslationStatus
from app.services.library import (
    delete_paper_files,
    extract_title_from_pdf,
    generate_stored_filename,
    get_pdf_info,
)
from app.services.translator import (
    QualityPreset,
    TranslationConfig,
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

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/papers", tags=["papers"])

# Paper ID format: 12-character hex string (uuid4 hex[:12])
_PAPER_ID_RE = re.compile(r"^[0-9a-f]{12}$")


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
            if len(v) > 500:
                raise ValueError("Title must be 500 characters or less")
        return v

    @field_validator("tags")
    @classmethod
    def validate_tags(cls, v: str | None) -> str | None:
        if v is not None:
            v = v.strip()
            if not v:
                return None
            if len(v) > 1000:
                raise ValueError("Tags must be 1000 characters or less")
        return v

    @field_validator("notes")
    @classmethod
    def validate_notes(cls, v: str | None) -> str | None:
        if v is not None:
            v = v.strip()
            if not v:
                return None
            if len(v) > 10000:
                raise ValueError("Notes must be 10000 characters or less")
        return v


def _paper_to_response(
    paper: Paper,
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
        tags=paper.tags,
        notes=paper.notes,
        has_original=has_original,
        has_translated=has_translated,
        has_dual=has_dual,
        created_at=paper.created_at.isoformat() if paper.created_at else "",
        updated_at=paper.updated_at.isoformat() if paper.updated_at else "",
    )


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


@router.get("/", response_model=PaperListResponse)
async def list_papers(
    search: str = "",
    status: str = "",
    offset: int = 0,
    limit: int = 50,
    db: AsyncSession = Depends(get_session),
) -> PaperListResponse:
    """List papers with optional filtering and pagination.

    Args:
        search: Search term for paper title
        status: Filter by translation status
        offset: Number of papers to skip
        limit: Maximum number of papers to return (1-200)

    Returns:
        PaperListResponse with papers and total count
    """
    # Clamp limit and offset
    limit = min(max(limit, 1), 200)
    offset = max(offset, 0)

    # Base query
    query = select(Paper).order_by(Paper.created_at.desc())
    count_query = select(func.count(Paper.id))

    if search:
        query = query.where(Paper.title.contains(search))
        count_query = count_query.where(Paper.title.contains(search))
    if status:
        query = query.where(Paper.translation_status == status)
        count_query = count_query.where(Paper.translation_status == status)

    # Get total count
    total = await db.scalar(count_query) or 0

    # Apply pagination
    query = query.offset(offset).limit(limit)
    result = await db.execute(query)
    papers = result.scalars().all()

    # Check file existence per paper (safe path validation, max 200 papers)
    # Pre-resolve base dirs once to avoid repeated resolve() calls per paper
    papers_base = settings.papers_path.resolve()
    trans_base = settings.translations_path.resolve()

    def _check_files() -> list[PaperResponse]:
        responses = []
        for p in papers:
            responses.append(_paper_to_response(
                p,
                has_original=_file_exists_safe(
                    settings.papers_path, p.stored_filename, papers_base
                ),
                has_translated=_file_exists_safe(
                    settings.translations_path, p.translated_filename, trans_base
                ),
                has_dual=_file_exists_safe(
                    settings.translations_path, p.dual_filename, trans_base
                ),
            ))
        return responses

    paper_responses = await asyncio.to_thread(_check_files)

    return PaperListResponse(papers=paper_responses, total=total)


@router.post("/upload", response_model=PaperResponse)
async def upload_paper(
    file: UploadFile = File(...),
    tags: str = Form(""),
    db: AsyncSession = Depends(get_session),
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

    if len(tags) > 1000:
        raise HTTPException(400, "Tags must be 1000 characters or less")

    # Stream upload: write chunks directly to disk to minimize memory usage
    stored_name = generate_stored_filename(file.filename)
    stored_path = settings.papers_path / stored_name

    def _write_chunks() -> tuple[int, bool]:
        stored_path.parent.mkdir(parents=True, exist_ok=True)
        total = 0
        is_first = True
        with stored_path.open("wb") as f:
            while chunk := file.file.read(settings.upload_chunk_size):
                total += len(chunk)
                if total > settings.max_upload_size:
                    max_mb = settings.max_upload_size // (1024 * 1024)
                    raise HTTPException(400, f"File too large (max {max_mb}MB)")
                if is_first:
                    if not chunk[:5].startswith(b'%PDF'):
                        raise HTTPException(400, "Invalid PDF file (missing PDF header)")
                    is_first = False
                f.write(chunk)
        return total, is_first

    try:
        total_size, first_chunk = await asyncio.to_thread(_write_chunks)
    except Exception:
        stored_path.unlink(missing_ok=True)
        raise

    if first_chunk:
        stored_path.unlink(missing_ok=True)
        raise HTTPException(400, "Empty PDF file")

    try:
        page_count, file_size = await asyncio.to_thread(get_pdf_info, stored_path)
        title = await asyncio.to_thread(extract_title_from_pdf, stored_path)
    except Exception:
        stored_path.unlink(missing_ok=True)
        raise

    paper = Paper(
        title=title,
        original_filename=file.filename,
        stored_filename=stored_path.name,
        file_size=file_size,
        page_count=page_count,
        tags=tags,
    )
    db.add(paper)
    await db.commit()
    await db.refresh(paper)

    return _paper_to_response(paper, has_original=True)


@router.get("/{paper_id}", response_model=PaperResponse)
async def get_paper(paper_id: str, db: AsyncSession = Depends(get_session)) -> PaperResponse:
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
async def delete_paper(paper_id: str, db: AsyncSession = Depends(get_session)) -> dict[str, bool]:
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
    await delete_paper_files(paper)
    await db.delete(paper)
    await db.commit()
    return {"ok": True}


@router.post("/{paper_id}/translate")
async def start_translation(
    paper_id: str,
    background_tasks: BackgroundTasks,
    backend: str = "",
    quality: str = "balanced",
    db: AsyncSession = Depends(get_session),
) -> dict[str, bool | str]:
    """Start translation for a paper.

    Args:
        paper_id: The paper's unique identifier
        backend: Translation backend (deepseek, openai, google)
        quality: Quality preset (fast, balanced, quality)

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

    paper = await _get_paper_or_404(paper_id, db)
    if paper.translation_status == TranslationStatus.TRANSLATING.value:
        raise HTTPException(409, "Translation already in progress")

    paper.translation_status = TranslationStatus.TRANSLATING.value
    paper.translation_progress = 0.0
    paper.translation_error = None
    await db.commit()

    background_tasks.add_task(
        _run_translation,
        paper.id,
        backend or settings.translation_backend,
        quality,
    )

    return {"ok": True, "status": "translating"}


_BACKEND_API_KEY_ATTRS = {
    "deepseek": "deepseek_api_key",
    "openai": "openai_api_key",
    "deepl": "deepl_api_key",
}


def _resolve_backend_config(backend: str, quality_preset: QualityPreset) -> TranslationConfig:
    """Build TranslationConfig from backend name and quality preset.

    Resolves API keys from settings, handles fast-mode override to Google.
    Raises HTTPException if a required API key is missing.
    """
    api_key = ""
    base_url = ""
    model_name = ""

    if backend == "deepseek":
        api_key = settings.deepseek_api_key
        model_name = settings.deepseek_model
    elif backend == "openai":
        api_key = settings.openai_api_key
        base_url = settings.openai_base_url
        model_name = settings.openai_model
    elif backend == "deepl":
        api_key = settings.deepl_api_key
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
        if not api_key and not os.environ.get(prefixed_key, "") and not os.environ.get(unprefixed_key, ""):
            raise HTTPException(
                400,
                f"Backend '{backend}' requires an API key. "
                f"Set {prefixed_key} in your .env file.",
            )

    return TranslationConfig(
        backend=backend,
        api_key=api_key,
        base_url=base_url,
        model=model_name,
        quality=quality_preset,
    )


def _reset_paper_status(paper_id: str, error_message: str) -> None:
    """Reset a paper's translation status to failed (synchronous, for background threads)."""
    from app.core.database import async_session

    try:
        async def _do_reset():
            async with async_session() as db:
                result = await db.execute(select(Paper).where(Paper.id == paper_id))
                paper = result.scalar_one_or_none()
                if paper and paper.translation_status == TranslationStatus.TRANSLATING.value:
                    paper.translation_status = TranslationStatus.FAILED.value
                    paper.translation_error = error_message
                    await db.commit()

        asyncio.run(_do_reset())
    except Exception:
        logger.exception("Failed to reset paper status for %s", paper_id)


def _run_translation(paper_id: str, backend: str, quality: str = "balanced") -> None:
    acquired = _translation_semaphore.acquire(timeout=300)
    if not acquired:
        logger.error("Translation queue full, rejecting paper %s", paper_id)
        _reset_paper_status(paper_id, "Translation queue is busy, please try again later")
        return

    try:
        quality_preset = _quality_map.get(quality, QualityPreset.BALANCED)
        config = _resolve_backend_config(backend, quality_preset)

        asyncio.run(_do_translate(paper_id, config, quality))

    except Exception:
        # Safety net: if anything outside _do_translate fails, reset paper status
        # so it doesn't stay stuck as "translating" forever
        logger.exception("Unhandled error in _run_translation for paper %s", paper_id)
        _reset_paper_status(paper_id, "Unexpected server error during translation")

    finally:
        _translation_semaphore.release()


async def _do_translate(
    paper_id: str,
    config: TranslationConfig,
    quality: str,
) -> None:
    """Execute translation in async context."""
    from app.core.database import async_session

    loop = asyncio.get_running_loop()

    async with async_session() as db:
        result = await db.execute(select(Paper).where(Paper.id == paper_id))
        paper = result.scalar_one_or_none()
        if not paper:
            logger.error("Paper %s not found for translation", paper_id)
            return

        # Validate paths (defense-in-depth)
        papers_base = settings.papers_path.resolve()
        input_path = (settings.papers_path / paper.stored_filename).resolve()
        if not input_path.is_relative_to(papers_base):
            logger.error("Path traversal detected for paper %s", paper_id)
            paper.translation_status = TranslationStatus.FAILED.value
            paper.translation_error = "Invalid file path"
            await db.commit()
            return

        output_dir = settings.translations_path / paper.id

        if not input_path.exists():
            logger.error("Original file missing for paper %s", paper_id)
            paper.translation_status = TranslationStatus.FAILED.value
            paper.translation_error = "Original PDF file not found"
            await db.commit()
            return

        logger.info(
            "Starting translation for paper %s (backend=%s, quality=%s)",
            paper_id, config.backend, quality,
        )

        on_progress = _create_progress_handler(paper_id, loop)

        try:
            trans_result = await loop.run_in_executor(
                None, lambda: translate_pdf_sync(input_path, output_dir, config, on_progress)
            )
        except Exception as e:
            logger.exception("Translation crashed for paper %s", paper_id)
            paper.translation_status = TranslationStatus.FAILED.value
            paper.translation_error = sanitize_error(e)
            if output_dir.exists():
                try:
                    shutil.rmtree(output_dir)
                except OSError as cleanup_err:
                    logger.warning("Failed to clean up %s: %s", output_dir, cleanup_err)
            await db.commit()
            return

        _update_paper_result(paper, trans_result, output_dir)
        await db.commit()


def _create_progress_handler(
    paper_id: str,
    loop: asyncio.AbstractEventLoop,
) -> Callable:
    """Create a progress callback that updates the database."""
    _last_pct: list[float] = [0.0]

    def _on_progress(pct: float) -> None:
        if pct - _last_pct[0] < 0.01 and pct < 1.0:
            return
        _last_pct[0] = pct

        async def _update():
            from app.core.database import async_session
            async with async_session() as p_db:
                p = await p_db.get(Paper, paper_id)
                if p and p.translation_status == TranslationStatus.TRANSLATING.value:
                    p.translation_progress = pct
                    await p_db.commit()
        with contextlib.suppress(Exception):
            asyncio.run_coroutine_threadsafe(_update(), loop)

    return _on_progress


def _update_paper_result(
    paper: Paper,
    trans_result: object,
    output_dir: Path,
) -> None:
    """Update paper with translation result."""
    if trans_result.success:
        paper.translation_status = TranslationStatus.COMPLETED.value
        paper.translation_progress = 1.0
        if trans_result.mono_path:
            rel_path = trans_result.mono_path.relative_to(settings.translations_path)
            paper.translated_filename = str(rel_path)
        if trans_result.dual_path:
            rel_path = trans_result.dual_path.relative_to(settings.translations_path)
            paper.dual_filename = str(rel_path)
        logger.info("Translation completed for paper %s", paper.id)
    else:
        paper.translation_status = TranslationStatus.FAILED.value
        paper.translation_error = trans_result.error
        if output_dir.exists():
            try:
                shutil.rmtree(output_dir)
            except OSError as cleanup_err:
                logger.warning("Failed to clean up %s: %s", output_dir, cleanup_err)
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
    paper_id: str, db: AsyncSession = Depends(get_session)
) -> FileResponse:
    """Download the original PDF file."""
    paper = await _get_paper_or_404(paper_id, db)
    return await _serve_paper_file(
        paper, "stored_filename", settings.papers_path, paper.original_filename
    )


@router.get("/{paper_id}/download/translated")
async def download_translated(
    paper_id: str, db: AsyncSession = Depends(get_session)
) -> FileResponse:
    """Download the translated PDF file."""
    paper = await _get_paper_or_404(paper_id, db)
    name = f"{Path(paper.original_filename).stem}_zh.pdf"
    return await _serve_paper_file(
        paper, "translated_filename", settings.translations_path, name
    )


@router.get("/{paper_id}/download/dual")
async def download_dual(paper_id: str, db: AsyncSession = Depends(get_session)) -> FileResponse:
    """Download the dual-language PDF file."""
    paper = await _get_paper_or_404(paper_id, db)
    name = f"{Path(paper.original_filename).stem}_dual.pdf"
    return await _serve_paper_file(paper, "dual_filename", settings.translations_path, name)


@router.get("/{paper_id}/view/original")
async def view_original(paper_id: str, db: AsyncSession = Depends(get_session)) -> FileResponse:
    """View the original PDF file in browser."""
    paper = await _get_paper_or_404(paper_id, db)
    return await _serve_paper_file(paper, "stored_filename", settings.papers_path)


@router.get("/{paper_id}/view/translated")
async def view_translated(paper_id: str, db: AsyncSession = Depends(get_session)) -> FileResponse:
    """View the translated PDF file in browser."""
    paper = await _get_paper_or_404(paper_id, db)
    return await _serve_paper_file(paper, "translated_filename", settings.translations_path)


@router.patch("/{paper_id}")
async def update_paper(
    paper_id: str,
    request: PaperUpdateRequest,
    db: AsyncSession = Depends(get_session),
) -> dict[str, bool]:
    """Update paper metadata (title, tags, notes)."""
    paper = await _get_paper_or_404(paper_id, db)
    if request.title is not None:
        paper.title = request.title
    if request.tags is not None:
        paper.tags = request.tags
    if request.notes is not None:
        paper.notes = request.notes
    await db.commit()
    return {"ok": True}
