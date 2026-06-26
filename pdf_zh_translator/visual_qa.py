"""Render-based visual layout scoring."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class PageVisualScore:
    page: int
    score: float
    original_ink_ratio: float
    translated_ink_ratio: float
    zone_scores: tuple[float, ...] = ()
    min_zone_score: float = 1.0


@dataclass(frozen=True)
class VisualLayoutScore:
    overall_score: float
    pages: list[PageVisualScore]
    min_page_score: float = 1.0
    min_zone_score: float = 1.0
    risk_level: str = "low"


def score_visual_layout(
    original_pdf: Path,
    translated_pdf: Path,
    *,
    max_pages: int = 8,
    dpi: int = 36,
    grid_rows: int = 3,
    grid_cols: int = 2,
) -> VisualLayoutScore:
    """Score visual similarity using global and region-level ink density.

    The score is not text-content similarity. It catches blank pages, missing
    figures, local float/caption failures, and extreme layout density changes
    that bbox heuristics can miss.
    """
    import fitz

    original = fitz.open(str(original_pdf))
    translated = fitz.open(str(translated_pdf))
    pages: list[PageVisualScore] = []
    try:
        page_count = min(original.page_count, translated.page_count, max_pages)
        for index in range(page_count):
            orig_ratio, orig_zones = _page_ink_profile(
                original[index], dpi=dpi, grid_rows=grid_rows, grid_cols=grid_cols
            )
            trans_ratio, trans_zones = _page_ink_profile(
                translated[index], dpi=dpi, grid_rows=grid_rows, grid_cols=grid_cols
            )
            global_score = _ink_similarity(orig_ratio, trans_ratio)
            zone_scores = tuple(
                _ink_similarity(orig_zone, trans_zone)
                for orig_zone, trans_zone in zip(orig_zones, trans_zones)
            )
            mean_zone_score = (
                sum(zone_scores) / len(zone_scores) if zone_scores else global_score
            )
            min_zone_score = min(zone_scores) if zone_scores else global_score
            score = (
                global_score * 0.65
                + mean_zone_score * 0.25
                + min_zone_score * 0.10
            )
            pages.append(
                PageVisualScore(
                    page=index + 1,
                    score=max(0.0, min(1.0, score)),
                    original_ink_ratio=orig_ratio,
                    translated_ink_ratio=trans_ratio,
                    zone_scores=zone_scores,
                    min_zone_score=max(0.0, min(1.0, min_zone_score)),
                )
            )
    finally:
        translated.close()
        original.close()

    if not pages:
        return VisualLayoutScore(0.0, [], min_page_score=0.0, min_zone_score=0.0, risk_level="high")
    overall = sum(page.score for page in pages) / len(pages)
    min_page_score = min(page.score for page in pages)
    min_zone_score = min(page.min_zone_score for page in pages)
    return VisualLayoutScore(
        overall_score=overall,
        pages=pages,
        min_page_score=min_page_score,
        min_zone_score=min_zone_score,
        risk_level=_visual_risk_level(overall, min_page_score, min_zone_score),
    )


def _page_ink_ratio(page: object, *, dpi: int) -> float:
    ratio, _zones = _page_ink_profile(page, dpi=dpi, grid_rows=1, grid_cols=1)
    return ratio


def _page_ink_profile(
    page: object,
    *,
    dpi: int,
    grid_rows: int,
    grid_cols: int,
) -> tuple[float, tuple[float, ...]]:
    pixmap = page.get_pixmap(dpi=dpi, alpha=False)
    samples = pixmap.samples
    stride = pixmap.n
    if not samples or stride <= 0:
        return 0.0, ()
    width = max(1, int(pixmap.width))
    height = max(1, int(pixmap.height))
    rows = max(1, int(grid_rows))
    cols = max(1, int(grid_cols))
    zone_ink = [0 for _ in range(rows * cols)]
    zone_pixels = [0 for _ in range(rows * cols)]
    ink = 0
    pixels = len(samples) // stride
    for pixel_index, offset in enumerate(range(0, len(samples), stride)):
        channels = samples[offset : offset + min(stride, 3)]
        x = pixel_index % width
        y = min(height - 1, pixel_index // width)
        col = min(cols - 1, int(x * cols / width))
        row = min(rows - 1, int(y * rows / height))
        zone_index = row * cols + col
        zone_pixels[zone_index] += 1
        if channels and min(channels) < 245:
            ink += 1
            zone_ink[zone_index] += 1
    zones = tuple(
        zone_ink[index] / max(zone_pixels[index], 1)
        for index in range(len(zone_ink))
    )
    return ink / max(pixels, 1), zones


def _ink_similarity(first: float, second: float) -> float:
    denominator = max(first, second, 0.02)
    return max(0.0, min(1.0, 1.0 - abs(first - second) / denominator))


def _visual_risk_level(overall: float, min_page: float, min_zone: float) -> str:
    if overall < 0.45 or min_page < 0.35 or min_zone < 0.20:
        return "high"
    if overall < 0.72 or min_page < 0.60 or min_zone < 0.45:
        return "medium"
    return "low"
