"""Paper management API routes."""

import asyncio
import logging
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, BackgroundTasks, Depends, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse, StreamingResponse
from pydantic import BaseModel
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
from app.services.translator import QualityPreset, TranslationConfig, translate_pdf_sync

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/papers", tags=["papers"])


class PaperResponse(BaseModel):
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
    papers: list[PaperResponse]
    total: int


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


@router.get("/", response_model=PaperListResponse)
async def list_papers(
    search: str = "",
    status: str = "",
    offset: int = 0,
    limit: int = 50,
    db: AsyncSession = Depends(get_session),
):
    # Clamp limit
    limit = min(max(limit, 1), 200)

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

    # Pre-check file existence in batch (avoids N sync syscalls per paper)
    originals = {f.name for f in settings.papers_path.iterdir()} if settings.papers_path.exists() else set()
    translated_dir = settings.translations_path
    translated_files = {f.name for f in translated_dir.iterdir()} if translated_dir.exists() else set()

    paper_responses = []
    for p in papers:
        paper_responses.append(_paper_to_response(
            p,
            has_original=p.stored_filename in originals,
            has_translated=p.translated_filename is not None and p.translated_filename in translated_files,
            has_dual=p.dual_filename is not None and p.dual_filename in translated_files,
        ))

    return PaperListResponse(papers=paper_responses, total=total)


@router.post("/upload", response_model=PaperResponse)
async def upload_paper(
    file: UploadFile = File(...),
    tags: str = Form(""),
    db: AsyncSession = Depends(get_session),
):
    if not file.filename or not file.filename.lower().endswith(".pdf"):
        raise HTTPException(400, "Only PDF files are accepted")

    content = await file.read()
    if len(content) > 100 * 1024 * 1024:
        raise HTTPException(400, "File too large (max 100MB)")

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
    result = await db.execute(select(Paper).where(Paper.id == paper_id))
    paper = result.scalar_one_or_none()
    if not paper:
        raise HTTPException(404, "Paper not found")
    return _paper_to_response(
        paper,
        has_original=(settings.papers_path / paper.stored_filename).exists(),
        has_translated=paper.translated_filename is not None and (settings.translations_path / paper.translated_filename).exists(),
        has_dual=paper.dual_filename is not None and (settings.translations_path / paper.dual_filename).exists(),
    )


@router.delete("/{paper_id}")
async def delete_paper(paper_id: str, db: AsyncSession = Depends(get_session)):
    result = await db.execute(select(Paper).where(Paper.id == paper_id))
    paper = result.scalar_one_or_none()
    if not paper:
        raise HTTPException(404, "Paper not found")
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
    result = await db.execute(select(Paper).where(Paper.id == paper_id))
    paper = result.scalar_one_or_none()
    if not paper:
        raise HTTPException(404, "Paper not found")

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


def _run_translation(paper_id: str, backend: str, quality: str = "balanced"):
    import asyncio
    from app.core.database import async_session

    async def _do_translate():
        async with async_session() as db:
            result = await db.execute(select(Paper).where(Paper.id == paper_id))
            paper = result.scalar_one_or_none()
            if not paper:
                logger.error("Paper %s not found for translation", paper_id)
                return

            input_path = settings.papers_path / paper.stored_filename
            output_dir = settings.translations_path / paper.id

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

            # Map quality string to enum
            quality_map = {
                "fast": QualityPreset.FAST,
                "balanced": QualityPreset.BALANCED,
                "quality": QualityPreset.QUALITY,
            }
            quality_preset = quality_map.get(quality, QualityPreset.BALANCED)

            # Fast mode forces Google Translate (no API key needed)
            if quality_preset == QualityPreset.FAST:
                backend = "google"
                api_key = ""

            logger.info("Starting translation for paper %s (backend=%s, quality=%s, key=%s)", paper_id, backend, quality, "SET" if api_key else "NONE")

            config = TranslationConfig(
                backend=backend,
                api_key=api_key,
                base_url=base_url,
                model=model_name,
                quality=quality_preset,
            )

            # Run translation synchronously (this function runs in a background thread
            # via BackgroundTasks, so blocking is fine)
            try:
                trans_result = translate_pdf_sync(input_path, output_dir, config)
            except Exception as e:
                logger.exception("Translation crashed for paper %s", paper_id)
                paper.translation_status = TranslationStatus.FAILED.value
                paper.translation_error = str(e)
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
                logger.error("Translation failed for paper %s: %s", paper_id, trans_result.error)

            await db.commit()

    # Run in a new event loop (BackgroundTasks runs sync functions in a thread)
    loop = asyncio.new_event_loop()
    try:
        loop.run_until_complete(_do_translate())
    finally:
        loop.close()


@router.get("/{paper_id}/download/original")
async def download_original(paper_id: str, db: AsyncSession = Depends(get_session)):
    result = await db.execute(select(Paper).where(Paper.id == paper_id))
    paper = result.scalar_one_or_none()
    if not paper:
        raise HTTPException(404, "Paper not found")
    file_path = (settings.papers_path / paper.stored_filename).resolve()
    if not str(file_path).startswith(str(settings.papers_path.resolve())):
        raise HTTPException(403, "Access denied")
    if not file_path.exists():
        raise HTTPException(404, "File not found")
    return FileResponse(file_path, filename=paper.original_filename, media_type="application/pdf")


@router.get("/{paper_id}/download/translated")
async def download_translated(paper_id: str, db: AsyncSession = Depends(get_session)):
    result = await db.execute(select(Paper).where(Paper.id == paper_id))
    paper = result.scalar_one_or_none()
    if not paper or not paper.translated_filename:
        raise HTTPException(404, "Translated PDF not found")
    file_path = (settings.translations_path / paper.translated_filename).resolve()
    if not str(file_path).startswith(str(settings.translations_path.resolve())):
        raise HTTPException(403, "Access denied")
    if not file_path.exists():
        raise HTTPException(404, "File not found")
    name = f"{Path(paper.original_filename).stem}_zh.pdf"
    return FileResponse(file_path, filename=name, media_type="application/pdf")


@router.get("/{paper_id}/download/dual")
async def download_dual(paper_id: str, db: AsyncSession = Depends(get_session)):
    result = await db.execute(select(Paper).where(Paper.id == paper_id))
    paper = result.scalar_one_or_none()
    if not paper or not paper.dual_filename:
        raise HTTPException(404, "Dual PDF not found")
    file_path = (settings.translations_path / paper.dual_filename).resolve()
    if not str(file_path).startswith(str(settings.translations_path.resolve())):
        raise HTTPException(403, "Access denied")
    if not file_path.exists():
        raise HTTPException(404, "File not found")
    name = f"{Path(paper.original_filename).stem}_dual.pdf"
    return FileResponse(file_path, filename=name, media_type="application/pdf")


@router.get("/{paper_id}/view/original")
async def view_original(paper_id: str, db: AsyncSession = Depends(get_session)):
    result = await db.execute(select(Paper).where(Paper.id == paper_id))
    paper = result.scalar_one_or_none()
    if not paper:
        raise HTTPException(404, "Paper not found")
    file_path = (settings.papers_path / paper.stored_filename).resolve()
    if not str(file_path).startswith(str(settings.papers_path.resolve())):
        raise HTTPException(403, "Access denied")
    if not file_path.exists():
        raise HTTPException(404, "File not found")
    return FileResponse(file_path, media_type="application/pdf")


@router.get("/{paper_id}/view/translated")
async def view_translated(paper_id: str, db: AsyncSession = Depends(get_session)):
    result = await db.execute(select(Paper).where(Paper.id == paper_id))
    paper = result.scalar_one_or_none()
    if not paper or not paper.translated_filename:
        raise HTTPException(404, "Translated PDF not found")
    file_path = (settings.translations_path / paper.translated_filename).resolve()
    if not str(file_path).startswith(str(settings.translations_path.resolve())):
        raise HTTPException(403, "Access denied")
    if not file_path.exists():
        raise HTTPException(404, "File not found")
    return FileResponse(file_path, media_type="application/pdf")


@router.patch("/{paper_id}")
async def update_paper(
    paper_id: str,
    title: Optional[str] = None,
    tags: Optional[str] = None,
    notes: Optional[str] = None,
    db: AsyncSession = Depends(get_session),
):
    result = await db.execute(select(Paper).where(Paper.id == paper_id))
    paper = result.scalar_one_or_none()
    if not paper:
        raise HTTPException(404, "Paper not found")
    if title is not None:
        paper.title = title
    if tags is not None:
        paper.tags = tags
    if notes is not None:
        paper.notes = notes
    await db.commit()
    return {"ok": True}
