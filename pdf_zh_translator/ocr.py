"""Optional OCR support for scanned PDFs.

The native translator works on selectable PDF text. Scanned papers need a
searchable text layer first. This module builds one page-by-page through
PyMuPDF's OCR bridge when local Tesseract data is available.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class OcrReport:
    input_pdf: Path
    output_pdf: Path
    page_count: int
    text_pages_before: int
    text_pages_after: int


def count_text_pages(pdf_path: Path, min_chars: int = 20) -> int:
    """Count pages with enough extractable text to translate."""
    import fitz

    document = fitz.open(str(pdf_path))
    try:
        count = 0
        for page in document:
            if len(page.get_text("text").strip()) >= min_chars:
                count += 1
        return count
    finally:
        document.close()


def is_scanned_pdf(pdf_path: Path, min_text_pages_ratio: float = 0.2) -> bool:
    """Return True when most pages have no selectable text."""
    import fitz

    document = fitz.open(str(pdf_path))
    try:
        if document.page_count == 0:
            return False
        text_pages = 0
        for page in document:
            if len(page.get_text("text").strip()) >= 20:
                text_pages += 1
        return text_pages / document.page_count < min_text_pages_ratio
    finally:
        document.close()


def ocr_pdf_to_searchable(
    input_pdf: Path,
    output_pdf: Path,
    *,
    language: str = "eng",
    dpi: int = 180,
    tessdata: str | None = None,
) -> OcrReport:
    """Create a searchable PDF via local OCR.

    Raises RuntimeError with a user-facing message when the local OCR runtime
    cannot process the file.
    """
    import fitz

    before = count_text_pages(input_pdf)
    source = fitz.open(str(input_pdf))
    output = fitz.open()
    try:
        for page in source:
            pixmap = page.get_pixmap(dpi=dpi, alpha=False)
            try:
                page_pdf = pixmap.pdfocr_tobytes(language=language, tessdata=tessdata)
            except Exception as exc:
                raise RuntimeError(
                    "OCR failed. Install Tesseract language data or set tessdata path."
                ) from exc
            ocr_page = fitz.open("pdf", page_pdf)
            try:
                output.insert_pdf(ocr_page)
            finally:
                ocr_page.close()

        output_pdf.parent.mkdir(parents=True, exist_ok=True)
        output.save(str(output_pdf), garbage=4, deflate=True)
    finally:
        output.close()
        source.close()

    after = count_text_pages(output_pdf)
    result_doc = fitz.open(str(output_pdf))
    try:
        page_count = result_doc.page_count
    finally:
        result_doc.close()
    return OcrReport(
        input_pdf=input_pdf,
        output_pdf=output_pdf,
        page_count=page_count,
        text_pages_before=before,
        text_pages_after=after,
    )
