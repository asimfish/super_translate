"""Post-processing to fix text block layout in translated PDFs.

pdf2zh's ONNX layout detection model sometimes creates text regions with
incorrect positions/widths. This module corrects them using PyMuPDF.
"""

import logging
import re
import statistics
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger(__name__)

# Thresholds
MIN_COL_WIDTH_RATIO = 0.6  # blocks narrower than 60% of column width need fixing
X0_TOLERANCE = 40.0  # points of deviation from dominant left margin
MIN_BLOCK_WIDTH = 80.0  # blocks narrower than this are always fixed
LINE_NUMBER_RE = re.compile(r"^[\d\n\s]{1,3}$")
BODY_TEXT_MIN_SIZE = 7.0  # ignore blocks with font size below this (line numbers, footnotes)
PAGE_MARGIN_TOP = 60.0  # ignore blocks in top margin (headers)
PAGE_MARGIN_BOTTOM = 50.0  # ignore blocks in bottom margin (footers)


@dataclass
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
    try:
        import fitz
    except ImportError:
        logger.warning("PyMuPDF not available, skipping layout fix")
        return False

    translated_path = Path(translated_path)
    if output_path is None:
        output_path = translated_path
    else:
        output_path = Path(output_path)

    doc = fitz.open(str(translated_path))
    try:
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
                doc.close()
                tmp_path.replace(translated_path)
            else:
                doc.save(str(output_path), garbage=4, deflate=True)
                doc.close()
            logger.info("Layout fix: corrected %d blocks in %s", total_fixed, translated_path)
        else:
            logger.debug("Layout fix: no corrections needed for %s", translated_path)
            doc.close()

        return total_fixed > 0
    except Exception:
        doc.close()
        raise


def _fix_page_layout(page: object) -> int:
    """Fix text blocks on a single page. Returns number of blocks fixed."""
    blocks = _extract_text_blocks(page)
    if not blocks:
        return 0

    # Analyze page layout
    left_margin, col_width = _analyze_page_layout(blocks)
    if col_width < 100:
        return 0  # Can't determine layout, skip

    # Find blocks that need fixing
    page_height = page.rect.height
    to_fix: list[TextBlockInfo] = []
    for block in blocks:
        if _needs_fix(block, left_margin, col_width, page_height):
            to_fix.append(block)

    if not to_fix:
        return 0

    # Redact and reinsert
    _redact_blocks(page, to_fix)
    return _reinsert_blocks(page, to_fix, left_margin, col_width)


def _redact_blocks(page: object, blocks: list[TextBlockInfo]) -> None:
    """Redact text blocks from the page."""
    try:
        import fitz
    except ImportError:
        return

    for block in blocks:
        text = block.text.strip()
        if not text:
            continue
        rect = fitz.Rect(block.bbox)
        page.add_redact_annot(rect, fill=(1, 1, 1))

    # Apply all redactions at once
    try:
        import fitz as _fitz
        kwargs = {}
        if hasattr(_fitz, "PDF_REDACT_IMAGE_NONE"):
            kwargs["images"] = _fitz.PDF_REDACT_IMAGE_NONE
        if hasattr(_fitz, "PDF_REDACT_LINE_ART_NONE"):
            kwargs["graphics"] = _fitz.PDF_REDACT_LINE_ART_NONE
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
    try:
        import fitz
    except ImportError:
        return 0

    fixed_count = 0
    font_name = _find_chinese_font(page)

    for block in blocks:
        text = _clean_text(block.text)
        if not text or len(text) < 2:
            continue

        # Skip very short text fragments (likely artifacts)
        if len(text) <= 3 and block.avg_font_size < 8:
            continue

        # Skip blocks in the right margin area (table cells, figure elements)
        x0 = block.bbox[0]
        page_width = page.rect.width
        if x0 > page_width * 0.7 and (block.bbox[2] - x0) < 80:
            continue

        # Skip short fragments that are already at correct x position
        block_width = block.bbox[2] - x0
        if abs(x0 - left_margin) <= X0_TOLERANCE and len(text) < 10 and block_width < 80:
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
    try:
        import fitz
    except ImportError:
        return False

    size = max(9.0, min(avg_font_size, 12.0))
    while size >= 5.0:
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

        # Skip blocks with null bytes or binary content
        if "\x00" in text or any(ord(c) < 32 and c not in "\n\r\t" for c in text):
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
        if height < 5 or width < 30:
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
    full_widths = [w for w in width_values if w > 300]
    if full_widths:
        col_width = float(max(set(full_widths), key=full_widths.count))
    else:
        col_width = float(max(set(width_values), key=width_values.count))

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
    if height < 3 or width < 10:
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
    if len(text_stripped) < 25 and width < 100:
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
    if width < col_width * MIN_COL_WIDTH_RATIO and width < MIN_BLOCK_WIDTH:
        return True

    return False


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
    if len(lines) >= 2 and all(LINE_NUMBER_RE.match(l.strip()) for l in lines):
        return True
    return False


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
        if (re.match(r"^\d{1,3}\s+[A-Z一-鿿]", stripped)
                and not re.search(r"\d{1,3}$", stripped[3:])):
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


# Pattern for lines that are just line numbers
_LINE_NUM_LINE_RE = re.compile(r"^[\d\s]{1,3}$")


def _clean_text(text: str) -> str:
    """Remove line number artifacts from translated text.

    Line numbers appear as:
    - Standalone lines: "24" or "24\n25\n26"
    - Trailing numbers attached to CJK text: "正文内容24" → "正文内容"
    Does NOT remove:
    - Leading section numbers like "1 引言"
    - English references like "Figure 3", "Table 2", "Chapter 5"
    """
    lines = text.split("\n")
    cleaned: list[str] = []
    for line in lines:
        stripped = line.strip()
        # Skip lines that are just line numbers
        if _LINE_NUM_LINE_RE.match(stripped):
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
        font_name = font_info[3] if len(font_info) > 3 else ""
        # Check for Source Han Serif (pdf2zh's default Chinese font)
        if "SourceHanSerif" in font_name or "Source Han Serif" in font_name:
            return font_info[4] if len(font_info) > 4 else "noto"
        # Check for other common Chinese fonts
        if any(name in font_name for name in _CHINESE_FONT_NAMES):
            return font_info[4] if len(font_info) > 4 else font_name

    # Fallback: use PyMuPDF's built-in Chinese font
    return "china-ss"
