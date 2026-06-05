"""Paper management API routes."""

import asyncio
import logging
import shutil
import threading
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, BackgroundTasks, Depends, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel, field_validator
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.database import get_session
from app.models.paper import Paper, TranslationStatus
from app.services.library import (
    delete_paper_files,
    extract_title_from_pdf,
    get_pdf_info,
    save_uploaded_pdf,
)
from app.services.translator import QualityPreset, TranslationConfig, translate_pdf_sync, sanitize_error

# Limit concurrent translations to prevent resource exhaustion
_translation_semaphore = threading.Semaphore(2)
_quality_map = {
    "fast": QualityPreset.FAST,
    "balanced": QualityPreset.BALANCED,
    "quality": QualityPreset.QUALITY,
}
_MAX_UPLOAD_SIZE = 100 * 1024 * 1024  # 100MB
_UPLOAD_CHUNK_SIZE = 1024 * 1024  # 1MB

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/papers", tags=["papers"])


class PaperResponse(BaseModel):
    """Response model for paper data."""

    id: str
    title: str
    original_filename: str
    file_size: int
    page_count: int
    translation_status: str
    translation_progress: float
    translation_error: Optional[str]
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

    title: Optional[str] = None
    tags: Optional[str] = None
    notes: Optional[str] = None

    @field_validator("title")
    @classmethod
    def validate_title(cls, v: str | None) -> str | None:
        if v is not None and len(v) > 500:
            raise ValueError("Title must be 500 characters or less")
        return v

    @field_validator("tags")
    @classmethod
    def validate_tags(cls, v: str | None) -> str | None:
        if v is not None and len(v) > 1000:
            raise ValueError("Tags must be 1000 characters or less")
        return v

    @field_validator("notes")
    @classmethod
    def validate_notes(cls, v: str | None) -> str | None:
        if v is not None and len(v) > 10000:
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
        translation_progress=paper.translation_progress,
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
    if not str(file_path).startswith(str(resolved_base)):
        raise HTTPException(403, "Access denied")
    if not file_path.exists():
        raise HTTPException(404, "File not found")
    return file_path


def _file_exists_safe(base_dir: Path, filename: str | None) -> bool:
    """Check if a file exists safely (no path traversal)."""
    if not filename:
        return False
    resolved_base = base_dir.resolve()
    file_path = (base_dir / filename).resolve()
    if not str(file_path).startswith(str(resolved_base)):
        return False
    return file_path.exists()


async def _get_paper_or_404(paper_id: str, db: AsyncSession) -> Paper:
    """Fetch paper by ID or raise 404."""
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
):
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
    paper_responses = []
    for p in papers:
        paper_responses.append(_paper_to_response(
            p,
            has_original=_file_exists_safe(settings.papers_path, p.stored_filename),
            has_translated=_file_exists_safe(settings.translations_path, p.translated_filename),
            has_dual=_file_exists_safe(settings.translations_path, p.dual_filename),
        ))

    return PaperListResponse(papers=paper_responses, total=total)


@router.post("/upload", response_model=PaperResponse)
async def upload_paper(
    file: UploadFile = File(...),
    tags: str = Form(""),
    db: AsyncSession = Depends(get_session),
):
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

    # Stream upload to reject oversized files before loading fully into memory
    chunks: list[bytes] = []
    total_size = 0
    while chunk := await file.read(_UPLOAD_CHUNK_SIZE):
        total_size += len(chunk)
        if total_size > _MAX_UPLOAD_SIZE:
            raise HTTPException(400, "File too large (max 100MB)")
        chunks.append(chunk)
    content = b"".join(chunks)

    # Validate PDF magic bytes
    if not content[:5].startswith(b'%PDF'):
        raise HTTPException(400, "Invalid PDF file (missing PDF header)")

    stored_path = await save_uploaded_pdf(content, file.filename)
    page_count, file_size = await asyncio.to_thread(get_pdf_info, stored_path)
    title = await asyncio.to_thread(extract_title_from_pdf, stored_path)

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
async def get_paper(paper_id: str, db: AsyncSession = Depends(get_session)):
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
async def delete_paper(paper_id: str, db: AsyncSession = Depends(get_session)):
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
):
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


def _resolve_backend_config(backend: str, quality_preset: QualityPreset) -> TranslationConfig:
    """Build TranslationConfig from backend name and quality preset.

    Resolves API keys from settings, handles fast-mode override to Google.
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

    # Fast mode forces Google Translate (no API key needed)
    if quality_preset == QualityPreset.FAST:
        backend = "google"
        api_key = ""

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
        loop = asyncio.new_event_loop()

        async def _do_reset():
            async with async_session() as db:
                result = await db.execute(select(Paper).where(Paper.id == paper_id))
                paper = result.scalar_one_or_none()
                if paper and paper.translation_status == TranslationStatus.TRANSLATING.value:
                    paper.translation_status = TranslationStatus.FAILED.value
                    paper.translation_error = error_message
                    await db.commit()

        try:
            loop.run_until_complete(_do_reset())
        finally:
            loop.close()
    except Exception:
        logger.exception("Failed to reset paper status for %s", paper_id)


def _run_translation(paper_id: str, backend: str, quality: str = "balanced"):
    from app.core.database import async_session

    acquired = _translation_semaphore.acquire(timeout=300)
    if not acquired:
        logger.error("Translation queue full, rejecting paper %s", paper_id)
        _reset_paper_status(paper_id, "Translation queue is busy, please try again later")
        return

    try:
        quality_preset = _quality_map.get(quality, QualityPreset.BALANCED)
        config = _resolve_backend_config(backend, quality_preset)

        async def _do_translate():
            async with async_session() as db:
                result = await db.execute(select(Paper).where(Paper.id == paper_id))
                paper = result.scalar_one_or_none()
                if not paper:
                    logger.error("Paper %s not found for translation", paper_id)
                    return

                input_path = settings.papers_path / paper.stored_filename
                output_dir = settings.translations_path / paper.id

                logger.info("Starting translation for paper %s (backend=%s, quality=%s, key=%s)", paper_id, config.backend, quality, "SET" if config.api_key else "NONE")

                try:
                    trans_result = translate_pdf_sync(input_path, output_dir, config)
                except Exception as e:
                    logger.exception("Translation crashed for paper %s", paper_id)
                    paper.translation_status = TranslationStatus.FAILED.value
                    paper.translation_error = sanitize_error(e)
                    if output_dir.exists():
                        shutil.rmtree(output_dir, ignore_errors=True)
                    await db.commit()
                    return

                if trans_result.success:
                    paper.translation_status = TranslationStatus.COMPLETED.value
                    paper.translation_progress = 1.0
                    if trans_result.mono_path:
                        paper.translated_filename = str(trans_result.mono_path.relative_to(settings.translations_path))
                    if trans_result.dual_path:
                        paper.dual_filename = str(trans_result.dual_path.relative_to(settings.translations_path))
                    logger.info("Translation completed for paper %s", paper_id)
                else:
                    paper.translation_status = TranslationStatus.FAILED.value
                    paper.translation_error = trans_result.error
                    if output_dir.exists():
                        shutil.rmtree(output_dir, ignore_errors=True)
                    logger.error("Translation failed for paper %s: %s", paper_id, trans_result.error)

                await db.commit()

        loop = asyncio.new_event_loop()
        try:
            loop.run_until_complete(_do_translate())
        finally:
            loop.close()

    except Exception:
        # Safety net: if anything outside _do_translate fails, reset paper status
        # so it doesn't stay stuck as "translating" forever
        logger.exception("Unhandled error in _run_translation for paper %s", paper_id)
        _reset_paper_status(paper_id, "Unexpected server error during translation")

    finally:
        _translation_semaphore.release()


@router.get("/{paper_id}/download/original")
async def download_original(paper_id: str, db: AsyncSession = Depends(get_session)):
    paper = await _get_paper_or_404(paper_id, db)
    file_path = _get_paper_file(paper, "stored_filename", settings.papers_path)
    return FileResponse(file_path, filename=paper.original_filename, media_type="application/pdf")


@router.get("/{paper_id}/download/translated")
async def download_translated(paper_id: str, db: AsyncSession = Depends(get_session)):
    paper = await _get_paper_or_404(paper_id, db)
    file_path = _get_paper_file(paper, "translated_filename", settings.translations_path)
    name = f"{Path(paper.original_filename).stem}_zh.pdf"
    return FileResponse(file_path, filename=name, media_type="application/pdf")


@router.get("/{paper_id}/download/dual")
async def download_dual(paper_id: str, db: AsyncSession = Depends(get_session)):
    paper = await _get_paper_or_404(paper_id, db)
    file_path = _get_paper_file(paper, "dual_filename", settings.translations_path)
    name = f"{Path(paper.original_filename).stem}_dual.pdf"
    return FileResponse(file_path, filename=name, media_type="application/pdf")


@router.get("/{paper_id}/view/original")
async def view_original(paper_id: str, db: AsyncSession = Depends(get_session)):
    paper = await _get_paper_or_404(paper_id, db)
    file_path = _get_paper_file(paper, "stored_filename", settings.papers_path)
    return FileResponse(file_path, media_type="application/pdf")


@router.get("/{paper_id}/view/translated")
async def view_translated(paper_id: str, db: AsyncSession = Depends(get_session)):
    paper = await _get_paper_or_404(paper_id, db)
    file_path = _get_paper_file(paper, "translated_filename", settings.translations_path)
    return FileResponse(file_path, media_type="application/pdf")


@router.patch("/{paper_id}")
async def update_paper(
    paper_id: str,
    request: PaperUpdateRequest,
    db: AsyncSession = Depends(get_session),
):
    paper = await _get_paper_or_404(paper_id, db)
    if request.title is not None:
        paper.title = request.title
    if request.tags is not None:
        paper.tags = request.tags
    if request.notes is not None:
        paper.notes = request.notes
    await db.commit()
    return {"ok": True}
