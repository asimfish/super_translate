"""Post-processing to fix text block layout in translated PDFs.

pdf2zh's ONNX layout detection model sometimes creates text regions with
incorrect positions/widths. This module corrects them using PyMuPDF.
"""

import logging
import re
import statistics
from dataclasses import dataclass
from pathlib import Path

import fitz

logger = logging.getLogger(__name__)

# Thresholds
MIN_COL_WIDTH_RATIO = 0.6  # blocks narrower than 60% of column width need fixing
X0_TOLERANCE = 40.0  # points of deviation from dominant left margin
MIN_BLOCK_WIDTH = 80.0  # blocks narrower than this are always fixed
LINE_NUMBER_RE = re.compile(r"^[\d\s]{1,3}$")
BODY_TEXT_MIN_SIZE = 7.0  # ignore blocks with font size below this (line numbers, footnotes)
PAGE_MARGIN_TOP = 60.0  # ignore blocks in top margin (headers)
PAGE_MARGIN_BOTTOM = 50.0  # ignore blocks in bottom margin (footers)

# Layout analysis constants
_MIN_COL_WIDTH = 100  # minimum column width to attempt fixing
_MIN_TEXT_LEN = 2  # minimum text length to process
_ARTIFACT_TEXT_LEN = 3  # text length threshold for artifact detection
_ARTIFACT_FONT_SIZE = 8.0  # font size threshold for artifact detection
_RIGHT_MARGIN_RATIO = 0.7  # blocks past 70% of page width are in right margin
_SHORT_TEXT_LEN = 10  # short text fragment threshold
_MIN_INSERT_FONT = 5.0  # minimum font size for text insertion
_MAX_TITLE_FONT = 14.0  # font size threshold for title detection
_MIN_TITLE_LEN = 5  # minimum title text length
_MAX_TITLE_LEN = 200  # maximum title text length
_SMALL_BLOCK_HEIGHT = 5  # minimum block height
_SMALL_BLOCK_WIDTH = 30  # minimum block width
_TINY_BLOCK_HEIGHT = 3  # minimum block height for needs_fix
_TINY_BLOCK_WIDTH = 10  # minimum block width for needs_fix
_SHORT_HEADING_LEN = 25  # short heading text threshold
_HEADING_WIDTH = 100  # heading block width threshold
_FULL_WIDTH_THRESHOLD = 300  # minimum width for full-width blocks
_LINE_NUMBER_MIN = 2  # minimum lines for multi-line number detection
_FONT_INFO_TYPE_IDX = 3  # font info tuple index for font name
_FONT_INFO_NAME_IDX = 4  # font info tuple index for font name string
_MAX_SANITIZE_LEN = 500  # max error message length for sanitization
_MAX_ERROR_LEN = 200  # max error message length after sanitization
_ASCII_CONTROL_MAX = 32  # ASCII control characters below this value (except \n\r\t)


@dataclass(frozen=True)
class TextBlockInfo:
    """Extracted info for a single text block."""
    bbox: tuple[float, float, float, float]
    text: str
    avg_font_size: float
    block_index: int


def fix_translated_layout(
    translated_path: Path | str,
    output_path: Path | str | None = None,
) -> bool:
    """Fix text block positions in a translated PDF.

    Args:
        translated_path: Path to the translated PDF.
        output_path: Where to save the fixed PDF. Overwrites input if None.

    Returns:
        True if any blocks were fixed, False otherwise.
    """
    translated_path = Path(translated_path)
    output_path = translated_path if output_path is None else Path(output_path)

    with fitz.open(str(translated_path)) as doc:
        total_fixed = 0

        for page_idx in range(doc.page_count):
            page = doc[page_idx]
            fixed = _fix_page_layout(page)
            total_fixed += fixed

        if total_fixed > 0:
            output_path.parent.mkdir(parents=True, exist_ok=True)
            # Always save to a temp file first, then replace (avoids incremental save issues)
            if output_path == translated_path:
                tmp_path = translated_path.with_suffix(".tmp.pdf")
                doc.save(str(tmp_path), garbage=4, deflate=True)
                try:
                    tmp_path.replace(translated_path)
                except OSError:
                    tmp_path.unlink(missing_ok=True)
                    raise
            else:
                doc.save(str(output_path), garbage=4, deflate=True)
            logger.info("Layout fix: corrected %d blocks in %s", total_fixed, translated_path)
        else:
            logger.debug("Layout fix: no corrections needed for %s", translated_path)

        return total_fixed > 0


def _fix_page_layout(page: object) -> int:
    """Fix text blocks on a single page. Returns number of blocks fixed."""
    blocks = _extract_text_blocks(page)
    if not blocks:
        return 0

    # First pass: clean control character artifacts from all blocks
    # (null bytes, SOH, etc. from pdf2zh font embedding)
    _clean_page_artifacts(page, blocks)

    # Analyze page layout
    left_margin, col_width = _analyze_page_layout(blocks)
    if col_width < _MIN_COL_WIDTH:
        return 0  # Can't determine layout, skip

    # Find blocks that need fixing
    page_height = page.rect.height
    to_fix = [b for b in blocks if _needs_fix(b, left_margin, col_width, page_height)]

    if not to_fix:
        return 0

    # Redact and reinsert
    _redact_blocks(page, to_fix)
    return _reinsert_blocks(page, to_fix, left_margin, col_width)


_CONTROL_CHAR_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")


def _clean_page_artifacts(page: object, blocks: list[TextBlockInfo]) -> None:
    """Clean control character artifacts from all text blocks on a page.

    pdf2zh's font embedding can produce null bytes and other control chars
    in the rendered text. This pass redacts and reinserts affected blocks
    at their original positions with cleaned text.
    """
    # Check raw page text for control characters (not block text, which is already cleaned)
    raw_page_text = page.get_text("text")
    if not _CONTROL_CHAR_RE.search(raw_page_text) and "\xa0" not in raw_page_text:
        return

    font_name = _find_chinese_font(page)
    dirty_blocks = []

    for block in blocks:
        # Check raw text at this bbox (not the cleaned block text)
        rect = fitz.Rect(block.bbox)
        raw_text = page.get_text("text", clip=rect)
        if _CONTROL_CHAR_RE.search(raw_text) or "\xa0" in raw_text:
            dirty_blocks.append(block)

    if not dirty_blocks:
        return

    # Redact first
    for block in dirty_blocks:
        rect = fitz.Rect(block.bbox)
        page.add_redact_annot(rect, fill=(1, 1, 1))
    try:
        kwargs = {}
        if hasattr(fitz, "PDF_REDACT_IMAGE_NONE"):
            kwargs["images"] = fitz.PDF_REDACT_IMAGE_NONE
        if hasattr(fitz, "PDF_REDACT_LINE_ART_NONE"):
            kwargs["graphics"] = fitz.PDF_REDACT_LINE_ART_NONE
        page.apply_redactions(**kwargs)
    except (TypeError, AttributeError):
        page.apply_redactions()

    # Then reinsert with cleaned text
    for block in dirty_blocks:
        text = block.text
        text = _CONTROL_CHAR_RE.sub("", text)
        text = text.replace("\xa0", " ")
        text = text.strip()
        if not text or len(text) < _MIN_TEXT_LEN:
            continue

        rect = fitz.Rect(block.bbox)
        height = rect.height
        if height < block.avg_font_size * 1.5:
            rect = fitz.Rect(rect.x0, rect.y0, rect.x1, rect.y0 + block.avg_font_size * 2.5)

        _insert_text_with_fallback(page, rect, text, font_name, block.avg_font_size)


def _redact_blocks(page: object, blocks: list[TextBlockInfo]) -> None:
    """Redact text blocks from the page."""
    for block in blocks:
        text = block.text.strip()
        if not text:
            continue
        rect = fitz.Rect(block.bbox)
        page.add_redact_annot(rect, fill=(1, 1, 1))

    # Apply all redactions at once
    try:
        kwargs = {}
        if hasattr(fitz, "PDF_REDACT_IMAGE_NONE"):
            kwargs["images"] = fitz.PDF_REDACT_IMAGE_NONE
        if hasattr(fitz, "PDF_REDACT_LINE_ART_NONE"):
            kwargs["graphics"] = fitz.PDF_REDACT_LINE_ART_NONE
        page.apply_redactions(**kwargs)
    except (TypeError, AttributeError):
        page.apply_redactions()


def _reinsert_blocks(
    page: object,
    blocks: list[TextBlockInfo],
    left_margin: float,
    col_width: float,
) -> int:
    """Reinsert cleaned text blocks at correct positions. Returns count of blocks reinserted."""
    fixed_count = 0
    font_name = _find_chinese_font(page)

    for block in blocks:
        text = _clean_text(block.text)
        if not text or len(text) < _MIN_TEXT_LEN:
            continue

        # Skip very short text fragments (likely artifacts)
        if len(text) <= _ARTIFACT_TEXT_LEN and block.avg_font_size < _ARTIFACT_FONT_SIZE:
            continue

        # Skip blocks in the right margin area (table cells, figure elements)
        x0 = block.bbox[0]
        page_width = page.rect.width
        if x0 > page_width * _RIGHT_MARGIN_RATIO and (block.bbox[2] - x0) < MIN_BLOCK_WIDTH:
            continue

        # Skip short fragments that are already at correct x position
        block_width = block.bbox[2] - x0
        if (abs(x0 - left_margin) <= X0_TOLERANCE
                and len(text) < _SHORT_TEXT_LEN
                and block_width < MIN_BLOCK_WIDTH):
            continue

        # Calculate correct rect
        y0 = block.bbox[1]
        y1 = block.bbox[3]
        height = y1 - y0
        if height < block.avg_font_size * 1.5:
            y1 = y0 + block.avg_font_size * 2.5
        correct_rect = fitz.Rect(left_margin, y0, left_margin + col_width, y1)

        # Try to insert with appropriate font size
        if _insert_text_with_fallback(page, correct_rect, text, font_name, block.avg_font_size):
            fixed_count += 1

    return fixed_count


def _insert_text_with_fallback(
    page: object,
    rect: object,
    text: str,
    font_name: str,
    avg_font_size: float,
) -> bool:
    """Insert text into rect, trying decreasing font sizes. Returns True if inserted."""
    size = max(9.0, min(avg_font_size, 12.0))
    while size >= _MIN_INSERT_FONT:
        shape = page.new_shape()
        result = shape.insert_textbox(
            rect,
            text,
            fontname=font_name,
            fontsize=size,
            color=(0, 0, 0),
            align=fitz.TEXT_ALIGN_LEFT,
        )
        if result >= 0:
            shape.commit()
            return True
        size -= 0.5

    # Fallback: insert at minimum size
    shape = page.new_shape()
    shape.insert_textbox(
        rect,
        text,
        fontname=font_name,
        fontsize=5.0,
        color=(0, 0, 0),
        align=fitz.TEXT_ALIGN_LEFT,
    )
    shape.commit()
    return True


def _extract_text_blocks(page: object) -> list[TextBlockInfo]:
    """Extract text blocks with font size info from a page."""
    blocks: list[TextBlockInfo] = []
    page_dict = page.get_text("dict")

    for idx, raw_block in enumerate(page_dict.get("blocks", [])):
        if raw_block.get("type") != 0:
            continue

        text_parts: list[str] = []
        font_sizes: list[float] = []

        for raw_line in raw_block.get("lines", []):
            line_parts: list[str] = []
            for span in raw_line.get("spans", []):
                span_text = span.get("text", "").replace(" ", " ")
                if span_text:
                    line_parts.append(span_text)
                    font_sizes.append(float(span.get("size", 9.0)))
            line_text = "".join(line_parts).strip()
            if line_text:
                text_parts.append(line_text)

        text = "\n".join(text_parts).strip()
        if not text or not font_sizes:
            continue

        # Clean control characters (font embedding artifacts from pdf2zh)
        text = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]", "", text)
        text = text.replace("\xa0", " ")
        text = text.strip()
        if not text:
            continue

        avg_size = statistics.median(font_sizes)
        blocks.append(TextBlockInfo(
            bbox=tuple(float(x) for x in raw_block["bbox"]),
            text=text,
            avg_font_size=avg_size,
            block_index=idx,
        ))

    return blocks


def _analyze_page_layout(
    blocks: list[TextBlockInfo],
) -> tuple[float, float]:
    """Analyze text blocks to find dominant left margin and column width.

    Returns (left_margin, column_width).
    """
    # Collect x0 and width from blocks that look like body text,
    # weighted by text length (longer blocks are more likely body text)
    x0_weighted: dict[float, float] = {}
    width_values: list[float] = []

    for block in blocks:
        x0, y0, x1, y1 = block.bbox
        width = x1 - x0
        height = y1 - y0

        # Skip very small blocks (line numbers, artifacts)
        if height < _SMALL_BLOCK_HEIGHT or width < _SMALL_BLOCK_WIDTH:
            continue
        # Skip blocks with tiny font (line numbers)
        if block.avg_font_size < BODY_TEXT_MIN_SIZE:
            continue
        # Skip line number text
        if _is_line_number_text(block.text):
            continue

        # Weight by text length (body paragraphs are longer than headers/labels)
        text_len = len(block.text.strip())
        x0_rounded = round(x0, 0)
        x0_weighted[x0_rounded] = x0_weighted.get(x0_rounded, 0) + text_len
        width_values.append(round(width, 0))

    if not x0_weighted:
        return (0, 0)

    # Find x0 with most text content (dominant left margin)
    left_margin = max(x0_weighted, key=x0_weighted.get)

    # Find most common width among "full-width" blocks
    full_widths = [w for w in width_values if w > _FULL_WIDTH_THRESHOLD]
    widths_to_use = full_widths or width_values
    col_width = float(statistics.mode(widths_to_use))

    return (left_margin, col_width)


def _needs_fix(
    block: TextBlockInfo,
    left_margin: float,
    col_width: float,
    page_height: float = 792.0,
) -> bool:
    """Check if a text block needs position/width correction."""
    x0, y0, x1, y1 = block.bbox
    width = x1 - x0
    height = y1 - y0

    # Skip very small blocks (images, decorations)
    if height < _TINY_BLOCK_HEIGHT or width < _TINY_BLOCK_WIDTH:
        return False

    # Skip blocks in page margins (headers/footers)
    if y0 < PAGE_MARGIN_TOP or y1 > page_height - PAGE_MARGIN_BOTTOM:
        return False

    # Always fix line number artifacts (before short text filter)
    if _is_line_number_text(block.text):
        return True

    # Fix blocks that contain embedded line numbers
    if _has_embedded_line_numbers(block.text):
        return True

    # Skip short text that's likely figure labels or annotations
    # (e.g., "Time", "Opportunity", "Blur and Occlusion")
    text_stripped = block.text.strip()
    if len(text_stripped) < _SHORT_HEADING_LEN and width < _HEADING_WIDTH:
        return False

    # Skip blocks with tiny font (likely footnotes/line numbers)
    if block.avg_font_size < BODY_TEXT_MIN_SIZE:
        return True

    # Fix blocks with wrong left margin AND too narrow
    # (section headers at x=108 with reasonable width are OK)
    margin_offset = abs(x0 - left_margin)
    if margin_offset > X0_TOLERANCE:
        return True

    # Fix blocks that are too narrow
    return width < col_width * MIN_COL_WIDTH_RATIO and width < MIN_BLOCK_WIDTH


def _is_line_number_text(text: str) -> bool:
    """Check if text is just line number artifacts."""
    stripped = text.strip()
    if not stripped:
        return False
    # Single line number: "24", "024"
    if LINE_NUMBER_RE.match(stripped):
        return True
    # Multiple line numbers: "24\n25\n26"
    lines = stripped.split("\n")
    return bool(
        len(lines) >= _LINE_NUMBER_MIN
        and all(LINE_NUMBER_RE.match(line.strip()) for line in lines)
    )


def _has_embedded_line_numbers(text: str) -> bool:
    """Check if text contains line numbers embedded within content.

    Detects patterns like:
    - "正文内容24" (number directly attached to CJK text)
    - "正文内容 24" (number after CJK text with space)
    - "。35" (number after CJK punctuation)
    - "25\ntext content" (standalone number as first line of block)
    Does NOT flag:
    - Citation references: "[26, 27, 28, 25]"
    - Pure section numbers: "1 Introduction"
    - English references: "Figure 3", "Table 2", "abc24"
    """
    lines = text.split("\n")
    for i, line in enumerate(lines):
        stripped = line.strip()
        if not stripped:
            continue
        # Skip lines with citation brackets (references like [1, 2, 3])
        if "[" in stripped and "]" in stripped:
            continue
        # Pure section number: "1 Introduction" (no trailing number)
        sec_match = re.match(r"^\d{1,3}\s+", stripped)
        if (
            sec_match
            and re.match(r"[A-Z一-鿿]", stripped[sec_match.end():])
            and not re.search(r"\d{1,3}$", stripped[sec_match.end():])
        ):
            continue
        # Standalone line number as first line: "25\ntext..."
        if i == 0 and re.match(r"^\d{1,3}$", stripped):
            return True
        # Trailing number after CJK text: "正文内容24", "正文内容 24", "。35"
        # Only flag when preceded by a CJK character (not English like "Figure 3")
        if re.search(r"[一-鿿　-〿＀-￯]\s\d{1,3}$", stripped):
            return True
        if re.search(r"[一-鿿　-〿＀-￯]\d{1,3}$", stripped):
            return True
    return False


def _clean_text(text: str) -> str:
    """Remove line number artifacts and control characters from translated text.

    Line numbers appear as:
    - Standalone lines: "24" or "24\n25\n26"
    - Trailing numbers attached to CJK text: "正文内容24" → "正文内容"
    Does NOT remove:
    - Leading section numbers like "1 引言"
    - English references like "Figure 3", "Table 2", "Chapter 5"
    """
    # Remove control characters (null bytes, SOH, etc.) from font embedding artifacts
    # Keep \n, \r, \t for text structure
    text = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]", "", text)
    # Normalize non-breaking spaces to regular spaces
    text = text.replace("\xa0", " ")

    lines = text.split("\n")
    cleaned: list[str] = []
    for line in lines:
        stripped = line.strip()
        # Skip lines that are just line numbers
        if LINE_NUMBER_RE.match(stripped):
            continue
        # Remove trailing line numbers directly attached to CJK text
        # Pattern: CJK char + 1-3 digits at end (no space between)
        # Matches: "正文内容24" → "正文内容", "。35" → "。"
        # Does NOT match: "Figure 3" (space before digit), "图 3" (space)
        stripped = re.sub(r"([一-鿿　-〿＀-￯])\d{1,3}$", r"\1", stripped).strip()
        if stripped:
            cleaned.append(stripped)
    return "\n".join(cleaned)


_CHINESE_FONT_NAMES = frozenset(["Noto", "SimSun", "SimHei", "Ming", "Song"])


def _find_chinese_font(page: object) -> str:
    """Find a Chinese font already embedded in the page."""
    fonts = page.get_fonts()
    for font_info in fonts:
        font_name = font_info[_FONT_INFO_TYPE_IDX] if len(font_info) > _FONT_INFO_TYPE_IDX else ""
        # Check for Source Han Serif (pdf2zh's default Chinese font)
        if "SourceHanSerif" in font_name or "Source Han Serif" in font_name:
            if len(font_info) > _FONT_INFO_NAME_IDX:
                return font_info[_FONT_INFO_NAME_IDX]
            return "noto"
        # Check for other common Chinese fonts
        if any(name in font_name for name in _CHINESE_FONT_NAMES):
            if len(font_info) > _FONT_INFO_NAME_IDX:
                return font_info[_FONT_INFO_NAME_IDX]
            return font_name

    # Fallback: use PyMuPDF's built-in Chinese font
    return "china-ss"
