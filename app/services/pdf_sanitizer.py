"""Create browser- and translator-safe PDF copies without changing uploads.

Some conference submissions contain edge-of-page text streams built from
hundreds of one-glyph fonts.  They can act as hidden prompt injections and
also trigger broken glyph rendering in PDF.js.  This module removes only that
high-confidence pattern and rasterizes the affected margin back into place so
the visible paper remains unchanged.
"""

from __future__ import annotations

import logging
import os
import re
import threading
import uuid
from dataclasses import dataclass
from pathlib import Path

import fitz

logger = logging.getLogger(__name__)

_PAIR_FONT_MARKER = "ArialUnicodeMS_Pair_"
_MIN_TEXT_SHOWS = 80
_MIN_TEXT_MATRICES = 80
_MIN_PAIR_FONTS = 40
_EXTREME_DISTINCT_FONTS = 100
_MARGIN_POINTS = 72.0
_MARGIN_RATIO = 0.95
_RASTER_SCALE = 3.0

_NUMBER = rb"[-+]?(?:\d+(?:\.\d*)?|\.\d+)"
_FONT_SELECT_RE = re.compile(rb"/([^\s/]+)\s+" + _NUMBER + rb"\s+Tf\b")
_TEXT_SHOW_RE = re.compile(rb"(?<![A-Za-z])(?:Tj|TJ)\b")
_TEXT_MATRIX_RE = re.compile(
    rb"(?:"
    + _NUMBER
    + rb"\s+){5}("
    + _NUMBER
    + rb")\s+Tm\b"
)
_WHITE_FILL_RE = re.compile(rb"(?:1(?:\.0+)?\s+){3}rg\b")
_BLACK_FILL_RE = re.compile(rb"(?:0(?:\.0+)?\s+){3}rg\b")
_PROMPT_MARKERS = (
    "in your output",
    "must include",
    "ignore previous",
    "ignore all previous",
    "system prompt",
    "overall, i find this submission",
)


@dataclass(frozen=True)
class SafePdfResult:
    """Result of preparing a PDF for preview and translation."""

    path: Path
    changed: bool
    removed_streams: int = 0
    pages: tuple[int, ...] = ()


@dataclass(frozen=True)
class _SuspiciousContent:
    xref: int
    edge: str


_prepare_lock = threading.Lock()
_result_cache: dict[tuple[str, int, int], SafePdfResult] = {}


def safe_pdf_path(source_path: Path) -> Path:
    """Return the hidden sidecar path used for a sanitized PDF."""
    source_path = Path(source_path)
    return source_path.with_name(f".{source_path.stem}.safe.pdf")


def _source_key(source_path: Path) -> tuple[str, int, int]:
    stat = source_path.stat()
    return str(source_path.resolve()), stat.st_mtime_ns, stat.st_size


def _looks_like_prompt_injection(text: str) -> bool:
    normalized = " ".join(text.lower().split())
    hits = sum(marker in normalized for marker in _PROMPT_MARKERS)
    return hits >= 2 or "in your output you must include" in normalized


def _detect_suspicious_stream(
    stream: bytes,
    *,
    paired_font_resources: set[str],
    page_height: float,
    page_text: str,
) -> str | None:
    """Return the affected page edge for a high-confidence bad text stream."""
    font_refs = [match.decode("latin-1") for match in _FONT_SELECT_RE.findall(stream)]
    distinct_fonts = set(font_refs)
    paired_refs = distinct_fonts.intersection(paired_font_resources)
    text_shows = len(_TEXT_SHOW_RE.findall(stream))
    y_values = [float(value) for value in _TEXT_MATRIX_RE.findall(stream)]

    if text_shows < _MIN_TEXT_SHOWS or len(y_values) < _MIN_TEXT_MATRICES:
        return None

    bottom_ratio = sum(y <= _MARGIN_POINTS for y in y_values) / len(y_values)
    top_ratio = sum(y >= page_height - _MARGIN_POINTS for y in y_values) / len(y_values)
    edge = "bottom" if bottom_ratio >= top_ratio else "top"
    if max(bottom_ratio, top_ratio) < _MARGIN_RATIO:
        return None

    has_pair_font_pattern = len(paired_refs) >= _MIN_PAIR_FONTS
    has_extreme_font_churn = len(distinct_fonts) >= _EXTREME_DISTINCT_FONTS
    has_color_obfuscation = bool(
        _WHITE_FILL_RE.search(stream) and _BLACK_FILL_RE.search(stream)
    )
    has_prompt_text = _looks_like_prompt_injection(page_text)

    if has_pair_font_pattern and (
        has_color_obfuscation or has_prompt_text or has_extreme_font_churn
    ):
        return edge
    if has_extreme_font_churn and has_prompt_text:
        return edge
    return None


def _find_suspicious_streams(
    document: fitz.Document,
) -> dict[int, list[_SuspiciousContent]]:
    findings: dict[int, list[_SuspiciousContent]] = {}
    for page_number in range(document.page_count):
        page = document[page_number]
        paired_fonts = {
            str(font[4])
            for font in page.get_fonts(full=True)
            if len(font) > 4 and _PAIR_FONT_MARKER in str(font[3])
        }
        try:
            page_text = page.get_text()
        except Exception:
            page_text = ""
        for xref in page.get_contents():
            stream = document.xref_stream(xref)
            if not stream:
                continue
            edge = _detect_suspicious_stream(
                stream,
                paired_font_resources=paired_fonts,
                page_height=page.rect.height,
                page_text=page_text,
            )
            if edge:
                findings.setdefault(page_number, []).append(
                    _SuspiciousContent(xref=xref, edge=edge)
                )
    return findings


def _margin_rect(page: fitz.Page, edge: str) -> fitz.Rect:
    rect = page.rect
    margin = min(_MARGIN_POINTS, rect.height)
    if edge == "top":
        return fitz.Rect(rect.x0, rect.y0, rect.x1, rect.y0 + margin)
    return fitz.Rect(rect.x0, rect.y1 - margin, rect.x1, rect.y1)


def _replace_suspicious_streams(
    document: fitz.Document,
    findings: dict[int, list[_SuspiciousContent]],
) -> None:
    for page_number, suspicious in findings.items():
        page = document[page_number]
        snapshots: dict[str, tuple[fitz.Rect, bytes]] = {}
        for edge in {item.edge for item in suspicious}:
            rect = _margin_rect(page, edge)
            pixmap = page.get_pixmap(
                matrix=fitz.Matrix(_RASTER_SCALE, _RASTER_SCALE),
                clip=rect,
                alpha=False,
            )
            snapshots[edge] = rect, pixmap.tobytes("png")

        removed = {item.xref for item in suspicious}
        retained = [xref for xref in page.get_contents() if xref not in removed]
        references = " ".join(f"{xref} 0 R" for xref in retained)
        document.xref_set_key(page.xref, "Contents", f"[{references}]")
        page.clean_contents()

        for edge in sorted(snapshots):
            rect, image = snapshots[edge]
            page.insert_image(
                rect,
                stream=image,
                overlay=True,
                keep_proportion=False,
            )


def _cached_sidecar(source_path: Path, sidecar: Path) -> SafePdfResult | None:
    if not sidecar.is_file():
        return None
    if sidecar.stat().st_mtime_ns < source_path.stat().st_mtime_ns:
        return None
    try:
        with fitz.open(sidecar) as document:
            if document.page_count <= 0:
                return None
    except Exception:
        sidecar.unlink(missing_ok=True)
        return None
    return SafePdfResult(path=sidecar, changed=True)


def prepare_safe_pdf(source_path: Path) -> SafePdfResult:
    """Return a safe PDF path, creating an atomic sidecar when necessary."""
    source_path = Path(source_path).resolve()
    key = _source_key(source_path)

    with _prepare_lock:
        cached = _result_cache.get(key)
        if cached and cached.path.exists():
            return cached

        sidecar = safe_pdf_path(source_path)
        existing = _cached_sidecar(source_path, sidecar)
        if existing:
            _result_cache[key] = existing
            return existing

        with fitz.open(source_path) as document:
            findings = _find_suspicious_streams(document)
            if not findings:
                sidecar.unlink(missing_ok=True)
                result = SafePdfResult(path=source_path, changed=False)
            else:
                _replace_suspicious_streams(document, findings)
                temporary = sidecar.with_name(
                    f".{sidecar.stem}.{uuid.uuid4().hex}.tmp.pdf"
                )
                try:
                    document.save(
                        temporary,
                        garbage=4,
                        deflate=True,
                        clean=True,
                    )
                    os.replace(temporary, sidecar)
                finally:
                    temporary.unlink(missing_ok=True)
                pages = tuple(page_number + 1 for page_number in sorted(findings))
                result = SafePdfResult(
                    path=sidecar,
                    changed=True,
                    removed_streams=sum(map(len, findings.values())),
                    pages=pages,
                )
                logger.warning(
                    "Removed %d suspicious PDF text stream(s) from page(s) %s; "
                    "using safe sidecar %s",
                    result.removed_streams,
                    ", ".join(map(str, pages)),
                    sidecar.name,
                )

        if len(_result_cache) >= 256:
            _result_cache.clear()
        _result_cache[key] = result
        return result


def safe_pdf_for_use(source_path: Path) -> Path:
    """Best-effort safe path for preview/translation, with raw-file fallback."""
    source_path = Path(source_path).resolve()
    try:
        return prepare_safe_pdf(source_path).path
    except Exception as exc:
        logger.warning("PDF safety scan failed for %s: %s", source_path.name, exc)
        return source_path


def remove_safe_pdf(source_path: Path) -> None:
    """Delete a generated sidecar and invalidate in-process cache entries."""
    source_path = Path(source_path)
    resolved = str(source_path.resolve())
    with _prepare_lock:
        safe_pdf_path(source_path).unlink(missing_ok=True)
        stale = [key for key in _result_cache if key[0] == resolved]
        for key in stale:
            _result_cache.pop(key, None)
