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


@dataclass(frozen=True)
class VisualLayoutScore:
    overall_score: float
    pages: list[PageVisualScore]


def score_visual_layout(
    original_pdf: Path,
    translated_pdf: Path,
    *,
    max_pages: int = 8,
    dpi: int = 36,
) -> VisualLayoutScore:
    """Score gross visual similarity using rendered ink density.

    The score is not text-content similarity. It catches blank pages, missing
    figures, and extreme layout density changes that bbox heuristics can miss.
    """
    import fitz

    original = fitz.open(str(original_pdf))
    translated = fitz.open(str(translated_pdf))
    pages: list[PageVisualScore] = []
    try:
        page_count = min(original.page_count, translated.page_count, max_pages)
        for index in range(page_count):
            orig_ratio = _page_ink_ratio(original[index], dpi=dpi)
            trans_ratio = _page_ink_ratio(translated[index], dpi=dpi)
            denominator = max(orig_ratio, trans_ratio, 0.02)
            score = 1.0 - min(1.0, abs(orig_ratio - trans_ratio) / denominator)
            pages.append(
                PageVisualScore(
                    page=index + 1,
                    score=max(0.0, min(1.0, score)),
                    original_ink_ratio=orig_ratio,
                    translated_ink_ratio=trans_ratio,
                )
            )
    finally:
        translated.close()
        original.close()

    if not pages:
        return VisualLayoutScore(0.0, [])
    overall = sum(page.score for page in pages) / len(pages)
    return VisualLayoutScore(overall_score=overall, pages=pages)


def _page_ink_ratio(page: object, *, dpi: int) -> float:
    pixmap = page.get_pixmap(dpi=dpi, alpha=False)
    samples = pixmap.samples
    stride = pixmap.n
    if not samples or stride <= 0:
        return 0.0
    ink = 0
    pixels = len(samples) // stride
    for offset in range(0, len(samples), stride):
        channels = samples[offset : offset + min(stride, 3)]
        if channels and min(channels) < 245:
            ink += 1
    return ink / max(pixels, 1)
