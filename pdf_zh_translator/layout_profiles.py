"""Template-level layout profiling for academic PDFs.

This is intentionally heuristic: it classifies common conference/journal
templates well enough to choose safer layout policies and to report risk.
"""

from __future__ import annotations

import json
import statistics
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Sequence

BBox = tuple[float, float, float, float]


@dataclass(frozen=True)
class LayoutProfile:
    name: str
    confidence: float
    columns: int
    page_width: float
    page_height: float
    caption_position: str = "unknown"
    risk: str = "medium"


def detect_layout_profile(document: object) -> LayoutProfile:
    """Detect a broad paper template family."""
    if getattr(document, "page_count", 0) == 0:
        return LayoutProfile("unknown", 0.0, 1, 0.0, 0.0, risk="high")

    page = document[0]
    width = float(page.rect.width)
    height = float(page.rect.height)
    blocks = _text_blocks(page)
    columns = _detect_column_count(blocks, width)
    text = page.get_text("text").lower()
    caption_position = _caption_position(blocks)

    if columns >= 2 and _is_us_letter(width, height):
        if "permission to make digital" in text or "acm reference format" in text:
            return LayoutProfile("acm_two_column", 0.92, columns, width, height, caption_position)
        if "ieee" in text or "index terms" in text:
            return LayoutProfile("ieee_two_column", 0.86, columns, width, height, caption_position)
        return LayoutProfile("generic_two_column", 0.72, columns, width, height, caption_position)

    if columns >= 2 and _looks_like_acl(text, width, height):
        return LayoutProfile("acl_anthology", 0.82, columns, width, height, caption_position)

    if columns == 1 and (380 <= width <= 460) and (560 <= height <= 720):
        return LayoutProfile("springer_lncs", 0.78, columns, width, height, caption_position)

    if columns == 1:
        return LayoutProfile("single_column", 0.65, columns, width, height, caption_position)

    return LayoutProfile("unknown", 0.35, columns, width, height, caption_position, risk="high")


def profile_policy(profile: LayoutProfile) -> dict[str, float | bool]:
    """Return layout policy knobs for a detected profile."""
    if profile.name in {"acm_two_column", "ieee_two_column", "acl_anthology"}:
        return {
            "caption_extra_height": 46.0,
            "min_caption_font_size": 3.6,
            "warn_complex_floats": True,
        }
    if profile.name == "springer_lncs":
        return {
            "caption_extra_height": 40.0,
            "min_caption_font_size": 3.8,
            "warn_complex_floats": True,
        }
    return {
        "caption_extra_height": 36.0,
        "min_caption_font_size": 3.8,
        "warn_complex_floats": False,
    }


def learn_layout_template(
    pdf_paths: Sequence[Path],
    *,
    template_name: str | None = None,
    max_pages_per_pdf: int = 6,
) -> dict:
    """Learn a reusable layout profile from representative paper PDFs.

    The output is intentionally JSON-friendly so it can be versioned and used
    as a regression artifact for ACM/IEEE/Springer/ACL style families.
    """
    paths = [Path(path) for path in pdf_paths]
    if not paths:
        raise ValueError("at least one PDF is required")

    import fitz

    samples = []
    for pdf_path in paths:
        if not pdf_path.exists():
            raise FileNotFoundError(str(pdf_path))
        document = fitz.open(str(pdf_path))
        try:
            samples.append(_sample_document_layout(document, pdf_path, max_pages_per_pdf))
        finally:
            document.close()

    detected_counts: dict[str, int] = {}
    for sample in samples:
        detected = str(sample["detected_profile"])
        detected_counts[detected] = detected_counts.get(detected, 0) + 1
    dominant_profile = max(detected_counts.items(), key=lambda item: item[1])[0]
    learned_name = template_name or dominant_profile
    columns = _mode_int([int(sample["columns"]) for sample in samples], default=1)
    caption_position = _mode_str(
        [str(sample["caption_position"]) for sample in samples],
        default="unknown",
    )
    policy = dict(
        profile_policy(
            LayoutProfile(
                learned_name,
                0.85,
                columns,
                _median_float(sample["page_width"] for sample in samples),
                _median_float(sample["page_height"] for sample in samples),
                caption_position=caption_position,
            )
        )
    )
    policy["learned"] = True
    policy["min_body_font_size"] = max(
        4.0,
        round(_median_float(sample["font_size_median"] for sample in samples) * 0.72, 2),
    )

    return {
        "_metadata": {
            "version": "1.0",
            "generated_at": _utc_now(),
            "source_count": len(samples),
            "max_pages_per_pdf": max_pages_per_pdf,
        },
        "template_name": learned_name,
        "detected_profiles": detected_counts,
        "page": {
            "width_median": _round(_median_float(sample["page_width"] for sample in samples)),
            "height_median": _round(_median_float(sample["page_height"] for sample in samples)),
        },
        "layout": {
            "columns": columns,
            "caption_position": caption_position,
            "left_margin_median": _round(
                _median_float(sample["left_margin"] for sample in samples)
            ),
            "right_margin_median": _round(
                _median_float(sample["right_margin"] for sample in samples)
            ),
            "top_margin_median": _round(_median_float(sample["top_margin"] for sample in samples)),
            "bottom_margin_median": _round(
                _median_float(sample["bottom_margin"] for sample in samples)
            ),
            "column_gap_median": _round(_median_float(sample["column_gap"] for sample in samples)),
        },
        "typography": {
            "font_size_median": _round(
                _median_float(sample["font_size_median"] for sample in samples)
            ),
            "line_gap_median": _round(
                _median_float(sample["line_gap_median"] for sample in samples)
            ),
        },
        "risk": {
            "complex_float_samples": sum(1 for sample in samples if sample["complex_floats"]),
            "table_like_samples": sum(1 for sample in samples if sample["table_like"]),
        },
        "policy": policy,
        "samples": samples,
    }


def write_learned_layout_template(
    pdf_paths: Sequence[Path],
    output_path: Path,
    *,
    template_name: str | None = None,
    max_pages_per_pdf: int = 6,
) -> dict:
    """Learn a template profile and write it to disk."""
    profile = learn_layout_template(
        pdf_paths,
        template_name=template_name,
        max_pages_per_pdf=max_pages_per_pdf,
    )
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        json.dumps(profile, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return profile


def _text_blocks(page: object) -> list[BBox]:
    boxes: list[BBox] = []
    for block in page.get_text("dict").get("blocks", []):
        if block.get("type") != 0:
            continue
        for line in block.get("lines", []):
            text = ""
            span_boxes: list[BBox] = []
            for span in line.get("spans", []):
                text += span.get("text", "")
                if "bbox" in span:
                    span_boxes.append(tuple(float(v) for v in span["bbox"]))
            if len(text.strip()) < 20 or not span_boxes:
                continue
            boxes.append(
                (
                    min(bbox[0] for bbox in span_boxes),
                    min(bbox[1] for bbox in span_boxes),
                    max(bbox[2] for bbox in span_boxes),
                    max(bbox[3] for bbox in span_boxes),
                )
            )
    return boxes


def _sample_document_layout(document: object, pdf_path: Path, max_pages: int) -> dict:
    profile = detect_layout_profile(document)
    page_count = min(getattr(document, "page_count", 0), max(1, max_pages))
    page_widths: list[float] = []
    page_heights: list[float] = []
    left_margins: list[float] = []
    right_margins: list[float] = []
    top_margins: list[float] = []
    bottom_margins: list[float] = []
    column_gaps: list[float] = []
    font_sizes: list[float] = []
    line_gaps: list[float] = []
    complex_floats = False
    table_like = False

    for page_index in range(page_count):
        page = document[page_index]
        width = float(page.rect.width)
        height = float(page.rect.height)
        page_widths.append(width)
        page_heights.append(height)
        blocks = _text_blocks(page)
        if blocks:
            left_margins.append(min(bbox[0] for bbox in blocks))
            right_margins.append(width - max(bbox[2] for bbox in blocks))
            top_margins.append(min(bbox[1] for bbox in blocks))
            bottom_margins.append(height - max(bbox[3] for bbox in blocks))
            columns = _column_lefts(blocks, width)
            if len(columns) >= 2:
                column_gaps.append(max(0.0, columns[1] - columns[0]))
            line_gaps.extend(_line_gaps(blocks))
        font_sizes.extend(_font_sizes(page))
        complex_floats = complex_floats or _drawing_count(page) >= 40
        table_like = table_like or _table_like_rows(blocks)

    return {
        "source": str(pdf_path),
        "detected_profile": profile.name,
        "confidence": _round(profile.confidence),
        "columns": profile.columns,
        "caption_position": profile.caption_position,
        "page_width": _round(_median_or_zero(page_widths)),
        "page_height": _round(_median_or_zero(page_heights)),
        "left_margin": _round(_median_or_zero(left_margins)),
        "right_margin": _round(_median_or_zero(right_margins)),
        "top_margin": _round(_median_or_zero(top_margins)),
        "bottom_margin": _round(_median_or_zero(bottom_margins)),
        "column_gap": _round(_median_or_zero(column_gaps)),
        "font_size_median": _round(_median_or_zero(font_sizes)),
        "line_gap_median": _round(_median_or_zero(line_gaps)),
        "complex_floats": complex_floats,
        "table_like": table_like,
    }


def _block_text(block: dict) -> str:
    text = ""
    for line in block.get("lines", []):
        for span in line.get("spans", []):
            text += span.get("text", "")
    return text


def _detect_column_count(blocks: Sequence[BBox], page_width: float) -> int:
    lefts = []
    for x0, _y0, x1, _y1 in blocks:
        width = x1 - x0
        if width < page_width * 0.18:
            continue
        lefts.append(round(x0 / 10.0) * 10.0)
    if len(lefts) < 3:
        return 1

    clusters: list[float] = []
    for x0 in sorted(lefts):
        if not clusters or abs(x0 - clusters[-1]) > page_width * 0.25:
            clusters.append(x0)
    return min(max(len(clusters), 1), 3)


def _column_lefts(blocks: Sequence[BBox], page_width: float) -> list[float]:
    lefts = []
    for x0, _y0, x1, _y1 in blocks:
        width = x1 - x0
        if width >= page_width * 0.18:
            lefts.append(round(x0 / 10.0) * 10.0)
    clusters: list[float] = []
    for x0 in sorted(lefts):
        if not clusters or abs(x0 - clusters[-1]) > page_width * 0.25:
            clusters.append(x0)
    return clusters


def _caption_position(blocks: Sequence[BBox]) -> str:
    if not blocks:
        return "unknown"
    heights = [y1 - y0 for _x0, y0, _x1, y1 in blocks]
    median_height = sorted(heights)[len(heights) // 2]
    return "compact" if median_height < 18 else "loose"


def _is_us_letter(width: float, height: float) -> bool:
    return 580 <= width <= 630 and 760 <= height <= 820


def _looks_like_acl(text: str, width: float, height: float) -> bool:
    return _is_us_letter(width, height) and (
        "association for computational linguistics" in text
        or "proceedings of" in text
        and "acl" in text
    )


def _font_sizes(page: object) -> list[float]:
    sizes: list[float] = []
    for block in page.get_text("dict").get("blocks", []):
        if block.get("type") != 0:
            continue
        for line in block.get("lines", []):
            for span in line.get("spans", []):
                size = float(span.get("size", 0.0))
                if size > 0:
                    sizes.append(size)
    return sizes


def _line_gaps(blocks: Sequence[BBox]) -> list[float]:
    by_left: dict[int, list[BBox]] = {}
    for bbox in blocks:
        by_left.setdefault(int(round(bbox[0] / 20.0)), []).append(bbox)
    gaps: list[float] = []
    for column_blocks in by_left.values():
        ordered = sorted(column_blocks, key=lambda bbox: bbox[1])
        for first, second in zip(ordered, ordered[1:]):
            gap = second[1] - first[3]
            if 0 <= gap <= 40:
                gaps.append(gap)
    return gaps


def _drawing_count(page: object) -> int:
    try:
        return len(page.get_drawings())
    except Exception:
        return 0


def _table_like_rows(blocks: Sequence[BBox]) -> bool:
    rows: dict[int, int] = {}
    for x0, y0, x1, _y1 in blocks:
        if x1 - x0 > 160:
            continue
        row = int(y0 // 8)
        rows[row] = rows.get(row, 0) + 1
    return sum(1 for count in rows.values() if count >= 4) >= 3


def _median_or_zero(values: Sequence[float]) -> float:
    return float(statistics.median(values)) if values else 0.0


def _median_float(values: Sequence[float] | object) -> float:
    collected = [float(value) for value in values if float(value) > 0.0]
    return _median_or_zero(collected)


def _mode_int(values: Sequence[int], *, default: int) -> int:
    if not values:
        return default
    return max(sorted(set(values)), key=values.count)


def _mode_str(values: Sequence[str], *, default: str) -> str:
    cleaned = [value for value in values if value]
    if not cleaned:
        return default
    return max(sorted(set(cleaned)), key=cleaned.count)


def _round(value: float) -> float:
    return round(float(value), 2)


def _utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
