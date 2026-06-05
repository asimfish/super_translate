"""PDF layout extraction and in-place text replacement."""

from __future__ import annotations

import statistics
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Sequence, Tuple


Color = Tuple[float, float, float]


@dataclass
class TextBlock:
    page_index: int
    bbox: Tuple[float, float, float, float]
    text: str
    font_size: float
    color: Color


@dataclass
class TranslationReport:
    input_pdf: Path
    output_pdf: Path
    page_count: int
    translated_blocks: int
    skipped_blocks: int
    warnings: List[str]


def translate_pdf(
    input_pdf: Path,
    output_pdf: Path,
    translator: object,
    font_name: str = "china-s",
    font_file: Optional[Path] = None,
    min_font_size: float = 5.0,
    font_scale: float = 0.92,
    margin: float = 0.8,
) -> TranslationReport:
    try:
        import fitz
    except ImportError as exc:
        raise RuntimeError("PyMuPDF is required. Install with: pip install -e .") from exc

    document = fitz.open(str(input_pdf))
    blocks = collect_text_blocks(document)
    translatable = [block for block in blocks if is_translatable(block.text)]
    skipped = len(blocks) - len(translatable)
    warnings: List[str] = []

    if not translatable:
        warnings.append("No extractable English text was found. Scanned PDFs need OCR before translation.")
        page_count = document.page_count
        document.save(str(output_pdf), garbage=4, deflate=True)
        document.close()
        return TranslationReport(input_pdf, output_pdf, page_count, 0, skipped, warnings)

    translations = translator.translate_batch([block.text for block in translatable])
    by_page: Dict[int, List[Tuple[TextBlock, str]]] = {}
    for block, translated_text in zip(translatable, translations):
        by_page.setdefault(block.page_index, []).append((block, translated_text))

    for page_index in range(document.page_count):
        page_items = by_page.get(page_index, [])
        if not page_items:
            continue
        page = document[page_index]
        register_font(page, font_name, font_file)
        redact_original_text(page, [block for block, _ in page_items], margin)
        for block, translated_text in page_items:
            inserted = insert_translated_text(
                page=page,
                bbox=block.bbox,
                text=translated_text,
                font_name=font_name,
                font_size=max(min_font_size, block.font_size * font_scale),
                color=block.color,
                min_font_size=min_font_size,
                margin=margin,
            )
            if not inserted:
                warnings.append(
                    "Page %d: translated text did not fully fit in bbox %s"
                    % (page_index + 1, compact_bbox(block.bbox))
                )

    output_pdf.parent.mkdir(parents=True, exist_ok=True)
    document.save(str(output_pdf), garbage=4, deflate=True)
    page_count = document.page_count
    document.close()
    return TranslationReport(input_pdf, output_pdf, page_count, len(translatable), skipped, warnings)


def collect_text_blocks(document: object) -> List[TextBlock]:
    blocks: List[TextBlock] = []
    for page_index in range(document.page_count):
        page = document[page_index]
        page_dict = page.get_text("dict")
        for raw_block in page_dict.get("blocks", []):
            if raw_block.get("type") != 0:
                continue
            parsed = parse_text_block(page_index, raw_block)
            if parsed is not None:
                blocks.append(parsed)
    return blocks


def parse_text_block(page_index: int, raw_block: dict) -> Optional[TextBlock]:
    lines: List[str] = []
    bboxes: List[Tuple[float, float, float, float]] = []
    font_sizes: List[float] = []
    colors: List[Color] = []

    for raw_line in raw_block.get("lines", []):
        line_parts: List[str] = []
        for span in raw_line.get("spans", []):
            span_text = normalize_span_text(span.get("text", ""))
            if not span_text:
                continue
            line_parts.append(span_text)
            if "bbox" in span:
                bboxes.append(tuple(float(x) for x in span["bbox"]))
            if "size" in span:
                font_sizes.append(float(span["size"]))
            if "color" in span:
                colors.append(int_to_rgb(span["color"]))
        line_text = "".join(line_parts).strip()
        if line_text:
            lines.append(line_text)

    text = "\n".join(lines).strip()
    if not text or not bboxes:
        return None

    return TextBlock(
        page_index=page_index,
        bbox=union_bbox(bboxes),
        text=text,
        font_size=median_or_default(font_sizes, 9.0),
        color=dominant_color(colors),
    )


def redact_original_text(page: object, blocks: Sequence[TextBlock], margin: float) -> None:
    try:
        import fitz
    except ImportError as exc:
        raise RuntimeError("PyMuPDF is required. Install with: pip install -e .") from exc

    background = render_background(page)
    for block in blocks:
        rect = expand_rect(fitz.Rect(block.bbox), margin)
        fill = sample_background_color(background, block.bbox, margin)
        page.add_redact_annot(rect, fill=fill)

    kwargs = {}
    if hasattr(fitz, "PDF_REDACT_IMAGE_NONE"):
        kwargs["images"] = fitz.PDF_REDACT_IMAGE_NONE
    if hasattr(fitz, "PDF_REDACT_LINE_ART_NONE"):
        kwargs["graphics"] = fitz.PDF_REDACT_LINE_ART_NONE
    try:
        page.apply_redactions(**kwargs)
    except TypeError:
        page.apply_redactions()


def insert_translated_text(
    page: object,
    bbox: Tuple[float, float, float, float],
    text: str,
    font_name: str,
    font_size: float,
    color: Color,
    min_font_size: float,
    margin: float,
) -> bool:
    try:
        import fitz
    except ImportError as exc:
        raise RuntimeError("PyMuPDF is required. Install with: pip install -e .") from exc

    rect = shrink_rect(fitz.Rect(bbox), margin)
    size = font_size
    while size >= min_font_size:
        shape = page.new_shape()
        result = shape.insert_textbox(
            rect,
            text,
            fontname=font_name,
            fontsize=size,
            color=color,
            align=fitz.TEXT_ALIGN_LEFT,
        )
        if result >= 0:
            shape.commit()
            return True
        size -= 0.5

    shape = page.new_shape()
    if rect.height <= min_font_size * 2.8:
        line_font_size = max(min_font_size, min(font_size, rect.height * 0.75))
        shape.insert_text(
            (rect.x0, rect.y0 + line_font_size * 1.15),
            " ".join(text.split()),
            fontname=font_name,
            fontsize=line_font_size,
            color=color,
        )
    else:
        shape.insert_textbox(
            rect,
            text,
            fontname=font_name,
            fontsize=min_font_size,
            color=color,
            align=fitz.TEXT_ALIGN_LEFT,
        )
    shape.commit()
    return False


def register_font(page: object, font_name: str, font_file: Optional[Path]) -> None:
    if font_file is None:
        return
    page.insert_font(fontname=font_name, fontfile=str(font_file))


def is_translatable(text: str) -> bool:
    stripped = text.strip()
    if len(stripped) < 2:
        return False
    if not any("A" <= char <= "Z" or "a" <= char <= "z" for char in stripped):
        return False
    return True


def normalize_span_text(text: str) -> str:
    return text.replace("\u00a0", " ")


def union_bbox(bboxes: Iterable[Tuple[float, float, float, float]]) -> Tuple[float, float, float, float]:
    boxes = list(bboxes)
    return (
        min(box[0] for box in boxes),
        min(box[1] for box in boxes),
        max(box[2] for box in boxes),
        max(box[3] for box in boxes),
    )


def median_or_default(values: Sequence[float], default: float) -> float:
    if not values:
        return default
    return float(statistics.median(values))


def dominant_color(colors: Sequence[Color]) -> Color:
    if not colors:
        return (0, 0, 0)
    rounded = [(round(r, 2), round(g, 2), round(b, 2)) for r, g, b in colors]
    return max(set(rounded), key=rounded.count)


def int_to_rgb(color: int) -> Color:
    red = ((color >> 16) & 255) / 255.0
    green = ((color >> 8) & 255) / 255.0
    blue = (color & 255) / 255.0
    return (red, green, blue)


def expand_rect(rect: object, amount: float) -> object:
    if amount <= 0:
        return rect
    rect.x0 -= amount
    rect.y0 -= amount
    rect.x1 += amount
    rect.y1 += amount
    return rect


def shrink_rect(rect: object, amount: float) -> object:
    if amount <= 0:
        return rect
    if rect.width > amount * 2:
        rect.x0 += amount
        rect.x1 -= amount
    if rect.height > amount * 2:
        rect.y0 += amount
        rect.y1 -= amount
    return rect


def compact_bbox(bbox: Tuple[float, float, float, float]) -> str:
    return "(%.1f, %.1f, %.1f, %.1f)" % bbox


@dataclass
class RenderedBackground:
    samples: bytes
    width: int
    height: int
    channels: int
    scale: float


def render_background(page: object, scale: float = 1.5) -> RenderedBackground:
    try:
        import fitz
    except ImportError as exc:
        raise RuntimeError("PyMuPDF is required. Install with: pip install -e .") from exc

    pixmap = page.get_pixmap(matrix=fitz.Matrix(scale, scale), alpha=False)
    return RenderedBackground(
        samples=pixmap.samples,
        width=pixmap.width,
        height=pixmap.height,
        channels=pixmap.n,
        scale=scale,
    )


def sample_background_color(
    background: RenderedBackground,
    bbox: Tuple[float, float, float, float],
    margin: float,
) -> Color:
    x0, y0, x1, y1 = bbox
    x0 -= margin
    y0 -= margin
    x1 += margin
    y1 += margin
    points = edge_sample_points(x0, y0, x1, y1)
    colors = [sample_pixel(background, x, y) for x, y in points]
    colors = [color for color in colors if color is not None]
    if not colors:
        return (1, 1, 1)
    return median_color(colors)


def edge_sample_points(x0: float, y0: float, x1: float, y1: float) -> List[Tuple[float, float]]:
    points: List[Tuple[float, float]] = []
    fractions = (0.08, 0.25, 0.5, 0.75, 0.92)
    for fraction in fractions:
        x = x0 + (x1 - x0) * fraction
        y = y0 + (y1 - y0) * fraction
        points.append((x, y0))
        points.append((x, y1))
        points.append((x0, y))
        points.append((x1, y))
    points.extend(
        [
            (x0, y0),
            (x1, y0),
            (x0, y1),
            (x1, y1),
        ]
    )
    return points


def sample_pixel(background: RenderedBackground, x: float, y: float) -> Optional[Color]:
    px = int(round(x * background.scale))
    py = int(round(y * background.scale))
    if px < 0 or py < 0 or px >= background.width or py >= background.height:
        return None
    offset = (py * background.width + px) * background.channels
    if offset + 2 >= len(background.samples):
        return None
    return (
        background.samples[offset] / 255.0,
        background.samples[offset + 1] / 255.0,
        background.samples[offset + 2] / 255.0,
    )


def median_color(colors: Sequence[Color]) -> Color:
    reds = sorted(color[0] for color in colors)
    greens = sorted(color[1] for color in colors)
    blues = sorted(color[2] for color in colors)
    middle = len(colors) // 2
    return (reds[middle], greens[middle], blues[middle])
