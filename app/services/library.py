"""Paper library management service."""

import logging
import shutil
import uuid
from pathlib import Path
from typing import Optional

import fitz

from app.core.config import settings
from app.models.paper import Paper, TranslationStatus

logger = logging.getLogger(__name__)


def generate_stored_filename(original_filename: str) -> str:
    ext = Path(original_filename).suffix or ".pdf"
    return f"{uuid.uuid4().hex}{ext}"


def get_pdf_info(pdf_path: Path) -> tuple[int, int]:
    """Get PDF page count and file size."""
    try:
        with fitz.open(str(pdf_path)) as doc:
            return doc.page_count, pdf_path.stat().st_size
    except Exception as e:
        logger.debug("Could not read PDF info from %s: %s", pdf_path.name, e)
        return 0, pdf_path.stat().st_size


def extract_title_from_pdf(pdf_path: Path) -> str:
    """Try to extract title from PDF metadata or first page."""
    try:
        with fitz.open(str(pdf_path)) as doc:
            meta_title = doc.metadata.get("title", "").strip()
            if meta_title:
                return meta_title

            if doc.page_count > 0:
                page = doc[0]
                blocks = page.get_text("dict")["blocks"]
                for block in blocks:
                    if block.get("type") == 0:
                        for line in block.get("lines", []):
                            for span in line.get("spans", []):
                                if span.get("size", 0) > 14:
                                    text = span.get("text", "").strip()
                                    if len(text) > 5:
                                        return text[:200]
    except Exception as e:
        logger.debug("Could not extract title from %s: %s", pdf_path.name, e)
    return pdf_path.stem.replace("_", " ").replace("-", " ").title()


async def save_uploaded_pdf(file_content: bytes, filename: str) -> Path:
    """Save uploaded PDF and return stored path."""
    stored_name = generate_stored_filename(filename)
    stored_path = settings.papers_path / stored_name
    stored_path.parent.mkdir(parents=True, exist_ok=True)
    stored_path.write_bytes(file_content)
    return stored_path


async def delete_paper_files(paper: Paper) -> None:
    """Delete all files associated with a paper."""
    original = settings.papers_path / paper.stored_filename
    if original.exists():
        original.unlink()

    if paper.translated_filename:
        translated = settings.translations_path / paper.translated_filename
        if translated.exists():
            translated.unlink()

    if paper.dual_filename:
        dual = settings.translations_path / paper.dual_filename
        if dual.exists():
            dual.unlink()
