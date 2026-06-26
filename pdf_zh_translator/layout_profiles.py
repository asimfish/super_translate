"""Template-level layout profiling for academic PDFs.

This is intentionally heuristic: it classifies common conference/journal
templates well enough to choose safer layout policies and to report risk.
"""

from __future__ import annotations

from dataclasses import dataclass
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
