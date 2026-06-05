"""Paper library management service."""

import asyncio
import contextlib
import logging
import uuid
from pathlib import Path

import fitz

from app.core.config import settings
from app.models.paper import Paper

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
                title = _extract_title_from_first_page(doc[0])
                if title:
                    return title
    except Exception as e:
        logger.debug("Could not extract title from %s: %s", pdf_path.name, e)
    return pdf_path.stem.replace("_", " ").replace("-", " ").title()


def _extract_title_from_first_page(page: object) -> str | None:
    """Extract title from the first page's text blocks by font size."""
    blocks = page.get_text("dict")["blocks"]
    for block in blocks:
        if block.get("type") != 0:
            continue
        for line in block.get("lines", []):
            title = _find_large_text_in_line(line)
            if title:
                return title
    return None


def _find_large_text_in_line(line: dict) -> str | None:
    """Find text with font size > 14 and length > 5 in a line."""
    for span in line.get("spans", []):
        if span.get("size", 0) > 14:
            text = span.get("text", "").strip()
            if len(text) > 5:
                return text[:200]
    return None


async def save_uploaded_pdf(file_content: bytes, filename: str) -> Path:
    """Save uploaded PDF and return stored path."""
    stored_name = generate_stored_filename(filename)
    stored_path = settings.papers_path / stored_name

    def _write_file() -> None:
        stored_path.parent.mkdir(parents=True, exist_ok=True)
        stored_path.write_bytes(file_content)

    await asyncio.to_thread(_write_file)
    return stored_path


async def delete_paper_files(paper: Paper) -> None:
    """Delete all files associated with a paper."""
    def _delete_files() -> None:
        _safe_delete(settings.papers_path, paper.stored_filename)
        if paper.translated_filename:
            _safe_delete(settings.translations_path, paper.translated_filename)
        if paper.dual_filename:
            _safe_delete(settings.translations_path, paper.dual_filename)
        # Clean up empty translation output directory
        ref_filename = paper.translated_filename or paper.dual_filename
        if ref_filename:
            ref_path = (settings.translations_path / ref_filename).resolve()
            parent = ref_path.parent
            translations_base = settings.translations_path.resolve()
            if (
                parent != translations_base
                and parent.is_relative_to(translations_base)
                and parent.is_dir()
            ):
                with contextlib.suppress(OSError):
                    parent.rmdir()  # only removes if empty

    await asyncio.to_thread(_delete_files)


def _safe_delete(base_dir: Path, filename: str) -> None:
    """Delete a file if it exists and is within the base directory."""
    if not filename:
        return
    resolved_base = base_dir.resolve()
    file_path = (base_dir / filename).resolve()
    if not file_path.is_relative_to(resolved_base):
        logger.warning("Refusing to delete path outside base dir: %s", filename)
        return
    if file_path.exists():
        file_path.unlink()
