"""Independent post-translation visual inspector.

Detects the semantic-visual defect classes that density/text QA cannot see:

- ``font_size_drift``: translated block set at a size inconsistent with the
  original block it replaces (contribution bullets shrunk to 6.4pt, body
  paragraphs shrunk to 6.5pt, ...).
- ``list_font_inconsistent``: sibling list items rendered at diverging sizes.
- ``preserved_ink_mismatch``: preserved table/algorithm/display-formula region
  whose rendered ink no longer matches the original (misaligned table header
  rules, rebuilt rows, damaged formulas).
- ``formula_clipped``: math line whose glyph ink is cut at the rendered edge
  (lost ascenders/superscripts of preserved formula atoms).
- ``formula_visible_ink_mismatch``: a formula sprite exists geometrically but
  its alpha mask contains no visible formula ink.
- ``reference_overlap``: overprinted text inside the references section
  (exempted from the generic text_overlap check).
- ``reference_bold_style``: bold prose injected into references entries that
  the original set in regular weight.
- ``untranslated_block``: full paragraphs of English left in the translated
  page outside preserved/figure/reference regions.
- ``display_formula_misaligned``: displayed equation whose horizontal
  placement drifted from the original row.

Every check compares the translated page against the original page - the
original is the ground truth for sizes, ink and placement. All issues are
returned as :class:`pdf_zh_translator.pdf_layout.TranslationIssue` so the
existing QA loop, sidecar reports and UI labels pick them up unchanged.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple

BBox = Tuple[float, float, float, float]

# Issue codes emitted by this module. The golden-page contract predates the
# inspector and the rendering engine still exhibits these defect classes, so
# legacy suites filter on this set while engine fixes land class by class.
INSPECTOR_ISSUE_CODES = frozenset(
    {
        "font_size_drift",
        "list_font_inconsistent",
        "formula_clipped",
        "formula_visible_ink_mismatch",
        "table_structure_mismatch",
        "preserved_ink_mismatch",
        "reference_overlap",
        "reference_bold_style",
        "untranslated_block",
        "display_formula_misaligned",
    }
)

_CJK_RE = re.compile(r"[\u3400-\u9fff\u3000-\u303f\uff00-\uffef]")
_CJK_HAN_RE = re.compile(r"[\u3400-\u9fff]")
_LATIN_WORD_RE = re.compile(r"[A-Za-z]{2,}")
_LIST_MARKER_RE = re.compile(
    r"^\s*(?:[\u2022\u25aa\u25e6\u2023\u00b7]|[-*]\s|\(?\d{1,2}[).]\s|\(?[ivxIVX]{1,4}\)\s)"
)
_MATH_FONT_RE = re.compile(r"CM(MI|SY|EX|R\d)|MSAM|MSBM|.*Math|StandardSym", re.I)

# Ink threshold on grayscale renders: sample < value counts as ink.
_INK_GRAY_THRESHOLD = 205
_INK_LUT = bytes(1 if value < _INK_GRAY_THRESHOLD else 0 for value in range(256))

# font_size_drift: a block is flagged when its size ratio against the original
# deviates from the page median ratio by more than this fraction.
_SIZE_RATIO_TOLERANCE = 0.075
_MIN_BLOCK_CJK_CHARS = 14
# list_font_inconsistent: sibling items whose sizes differ by more than 5%.
_SIBLING_RATIO_TOLERANCE = 1.05
# preserved_ink_mismatch: bidirectional dilated coverage below this fails.
_PRESERVED_COVERAGE_MIN = 0.88
_PRESERVED_MIN_AREA = 900.0  # pt^2, skip tiny atoms
# untranslated_block: latin words needed before a CJK-free block is flagged.
_UNTRANSLATED_MIN_WORDS = 12
_DISPLAY_ALIGN_TOLERANCE_PT = 14.0
_DISPLAY_ALIGN_WINDOW_PAD_PT = 48.0


@dataclass(frozen=True)
class _Span:
    text: str
    font: str
    size: float
    bbox: BBox


@dataclass(frozen=True)
class _Block:
    bbox: BBox
    spans: Tuple[_Span, ...]

    @property
    def text(self) -> str:
        return "".join(span.text for span in self.spans)

    def cjk_chars(self) -> int:
        return sum(len(_CJK_HAN_RE.findall(span.text)) for span in self.spans)

    def latin_words(self) -> int:
        return len(_LATIN_WORD_RE.findall(self.text))

    def dominant_size(self) -> float:
        weights: Dict[float, int] = {}
        has_cjk = self.cjk_chars() > 0
        for span in self.spans:
            stripped = span.text.strip()
            if not stripped:
                continue
            weight = (
                len(_CJK_HAN_RE.findall(stripped))
                if has_cjk
                else len(stripped)
            )
            if weight <= 0:
                continue
            key = round(span.size, 2)
            weights[key] = weights.get(key, 0) + weight
        if not weights:
            return 0.0
        return max(weights, key=lambda size: (weights[size], size))

    def bold_char_ratio(self) -> float:
        total = 0
        bold = 0
        for span in self.spans:
            stripped = span.text.strip()
            if not stripped:
                continue
            total += len(stripped)
            if "bold" in span.font.lower():
                bold += len(stripped)
        return bold / total if total else 0.0


def _issue(page: int, code: str, message: str, severity: str = "error"):
    from .pdf_layout import TranslationIssue

    return TranslationIssue(page=page, code=code, message=message, severity=severity)


def _text_blocks(page: object) -> List[_Block]:
    blocks: List[_Block] = []
    for raw in page.get_text("dict").get("blocks", []):
        if raw.get("type") != 0:
            continue
        spans: List[_Span] = []
        for line in raw.get("lines", []):
            for span in line.get("spans", []):
                if not span.get("text"):
                    continue
                spans.append(
                    _Span(
                        text=span["text"],
                        font=str(span.get("font", "")),
                        size=float(span.get("size", 0.0)),
                        bbox=tuple(float(v) for v in span["bbox"]),
                    )
                )
        if spans:
            blocks.append(
                _Block(
                    bbox=tuple(float(v) for v in raw["bbox"]),
                    spans=tuple(spans),
                )
            )
    return blocks


def _bbox_overlap_area(a: BBox, b: BBox) -> float:
    width = min(a[2], b[2]) - max(a[0], b[0])
    height = min(a[3], b[3]) - max(a[1], b[1])
    if width <= 0.0 or height <= 0.0:
        return 0.0
    return width * height


def _bbox_area(bbox: BBox) -> float:
    return max(0.0, bbox[2] - bbox[0]) * max(0.0, bbox[3] - bbox[1])


def _bbox_intersects(a: BBox, b: BBox, *, margin: float = 0.0) -> bool:
    return (
        a[0] - margin < b[2]
        and b[0] - margin < a[2]
        and a[1] - margin < b[3]
        and b[1] - margin < a[3]
    )


class _PageInk:
    """Single full-page grayscale ink bitmap with windowed queries.

    Rendering once per page (instead of once per probed clip) matters on
    raster-heavy documents where every ``get_pixmap`` call re-decodes large
    background images.
    """

    def __init__(self, page: object, *, zoom: float = 3.0) -> None:
        import fitz

        self.zoom = zoom
        self.origin_x = float(page.rect.x0)
        self.origin_y = float(page.rect.y0)
        pixmap = page.get_pixmap(
            matrix=fitz.Matrix(zoom, zoom),
            colorspace=fitz.csGRAY,
            alpha=False,
        )
        self.width = pixmap.width
        self.height = pixmap.height
        self.flags = pixmap.samples.translate(_INK_LUT)

    def _window(self, bbox: BBox) -> Optional[Tuple[int, int, int, int]]:
        x0 = max(0, int((bbox[0] - self.origin_x) * self.zoom))
        y0 = max(0, int((bbox[1] - self.origin_y) * self.zoom))
        x1 = min(self.width, int((bbox[2] - self.origin_x) * self.zoom) + 1)
        y1 = min(self.height, int((bbox[3] - self.origin_y) * self.zoom) + 1)
        if x1 - x0 < 2 or y1 - y0 < 2:
            return None
        return x0, y0, x1, y1

    def row_profile(self, bbox: BBox) -> List[float]:
        window = self._window(bbox)
        if window is None:
            return []
        x0, y0, x1, y1 = window
        span = x1 - x0
        profile: List[float] = []
        for y in range(y0, y1):
            row = self.flags[y * self.width + x0 : y * self.width + x1]
            profile.append(row.count(1) / span)
        return profile

    def mask(
        self,
        bbox: BBox,
        *,
        columns: int,
        rows: int,
        exclude_bands: Sequence[Tuple[float, float]] = (),
    ) -> Optional[set]:
        window = self._window(bbox)
        if window is None:
            return None
        x0, y0, x1, y1 = window
        span_x = x1 - x0
        span_y = y1 - y0
        banned_rows = set()
        for band_y0, band_y1 in exclude_bands:
            top = max(0, int((band_y0 - self.origin_y) * self.zoom) - y0)
            bottom = min(span_y, int((band_y1 - self.origin_y) * self.zoom) - y0 + 1)
            banned_rows.update(range(max(0, top), max(0, bottom)))
        mask = set()
        stride = max(1, span_x // (columns * 2))
        for y_offset in range(span_y):
            if y_offset in banned_rows:
                continue
            y = y0 + y_offset
            row = self.flags[y * self.width + x0 : y * self.width + x1]
            if 1 not in row:
                continue
            start = 0
            while True:
                index = row.find(1, start)
                if index < 0:
                    break
                mask.add(
                    (
                        min(columns - 1, index * columns // span_x),
                        min(rows - 1, y_offset * rows // span_y),
                    )
                )
                start = index + stride
        return mask

    def centroid_x(self, bbox: BBox) -> Optional[float]:
        window = self._window(bbox)
        if window is None:
            return None
        x0, y0, x1, y1 = window
        weighted = 0
        total = 0
        for y in range(y0, y1):
            row = self.flags[y * self.width + x0 : y * self.width + x1]
            start = 0
            while True:
                index = row.find(1, start)
                if index < 0:
                    break
                weighted += index
                total += 1
                start = index + 1
        if not total:
            return None
        return self.origin_x + (x0 + weighted / total) / self.zoom


class _InkCache:
    """Lazy per-page ink bitmaps so text-only checks never render."""

    def __init__(self, page: object) -> None:
        self._page = page
        self._ink: Optional[_PageInk] = None

    def get(self) -> _PageInk:
        if self._ink is None:
            self._ink = _PageInk(self._page)
        return self._ink


def _dilate(mask: set, columns: int, rows: int) -> set:
    return {
        (x + dx, y + dy)
        for x, y in mask
        for dx in (-1, 0, 1)
        for dy in (-1, 0, 1)
        if 0 <= x + dx < columns and 0 <= y + dy < rows
    }


def _mask_coverage(source: set, translated: set, columns: int, rows: int) -> float:
    if not source and not translated:
        return 1.0
    if not source or not translated:
        return 0.0
    source_hit = len(source & _dilate(translated, columns, rows)) / len(source)
    translated_hit = len(translated & _dilate(source, columns, rows)) / len(translated)
    return min(source_hit, translated_hit)




# ---------------------------------------------------------------------------
# font size checks
# ---------------------------------------------------------------------------


def _median(values: Sequence[float]) -> float:
    ordered = sorted(values)
    count = len(ordered)
    if not count:
        return 0.0
    middle = count // 2
    if count % 2:
        return ordered[middle]
    return (ordered[middle - 1] + ordered[middle]) / 2.0


def _font_size_issues(
    original_page: object,
    translated_page: object,
    page_number: int,
    *,
    exclusion_bboxes: Sequence[BBox],
    source_role_blocks: Sequence[object] = (),
) -> List[object]:
    original_blocks = [
        block for block in _text_blocks(original_page) if block.dominant_size() > 0
    ]
    translated_blocks = _text_blocks(translated_page)

    candidates: List[Tuple[_Block, float, float]] = []  # block, size, expected
    for block in translated_blocks:
        if block.cjk_chars() < _MIN_BLOCK_CJK_CHARS:
            continue
        if block.bold_char_ratio() > 0.5:
            continue
        if any(
            _bbox_overlap_area(block.bbox, banned) > 0.35 * _bbox_area(block.bbox)
            for banned in exclusion_bboxes
        ):
            continue
        size = block.dominant_size()
        if size <= 0:
            continue
        area = _bbox_area(block.bbox)
        if area <= 0:
            continue
        # Pair by symmetric (Dice-style) bbox similarity. Raw overlap area
        # lets a page-wide extraction artefact swallow every caption inside
        # it; plain source-coverage lets a tiny embedded formula block win
        # against the true paragraph. overlap^2/(area*source_area) prefers
        # the source that mutually matches the translated block best.
        best_overlap = 0.0
        best_score = 0.0
        expected = 0.0
        for source in source_role_blocks:
            source_bbox = tuple(float(value) for value in source.bbox)
            overlap = _bbox_overlap_area(block.bbox, source_bbox)
            if overlap <= 0.25 * area:
                continue
            source_area = _bbox_area(source_bbox)
            if source_area <= 0:
                continue
            score = (overlap * overlap) / (area * source_area)
            if score > best_score:
                best_score = score
                best_overlap = overlap
                expected = float(source.font_size)
        for source in original_blocks:
            overlap = _bbox_overlap_area(block.bbox, source.bbox)
            if overlap <= 0.25 * area:
                continue
            source_area = _bbox_area(source.bbox)
            if source_area <= 0:
                continue
            score = (overlap * overlap) / (area * source_area)
            if score > best_score:
                best_score = score
                best_overlap = overlap
                expected = source.dominant_size()
        if best_overlap <= 0.25 * area or expected <= 0:
            continue
        candidates.append((block, size, expected))

    if len(candidates) < 3:
        return []
    ratios = [size / expected for _, size, expected in candidates]
    median_ratio = _median(ratios)
    if median_ratio <= 0:
        return []
    cohorts: Dict[float, List[float]] = {}
    for _, size, expected in candidates:
        cohorts.setdefault(round(expected, 1), []).append(size / expected)

    issues: List[object] = []
    for block, size, expected in candidates:
        ratio = size / expected
        # Only the shrink direction is a reader-visible defect (crushed
        # bullets/paragraphs); a block that keeps its size while its source
        # ran slightly smaller still renders uniform with its neighbours.
        # Judge against the block's own expectation cohort: pages mixing
        # run-in headings (14.4pt sources) with body prose (12.1pt sources)
        # render both at one uniform Chinese size, so their ratios form two
        # healthy modes and the page-wide median would flag whichever mode
        # loses (dreamerv3 appendix). A cohort that is itself crushed still
        # fails the absolute floor.
        cohort = cohorts[round(expected, 1)]
        reference = (
            min(_median(cohort), median_ratio)
            if len(cohort) >= 2
            else median_ratio
        )
        uniform_with_cohort = ratio >= reference - _SIZE_RATIO_TOLERANCE * reference
        cohort_itself_crushed = reference < 0.80 and ratio < 0.72
        if uniform_with_cohort and not cohort_itself_crushed:
            continue
        issues.append(
            _issue(
                page_number,
                "font_size_drift",
                (
                    f"Page {page_number}: block set at {size:.2f}pt but the "
                    f"original at this position is {expected:.2f}pt "
                    f"(ratio {ratio:.2f} vs page median {median_ratio:.2f}) at "
                    f"x={block.bbox[0]:.1f}, y={block.bbox[1]:.1f}: "
                    f"{' '.join(block.text.split())[:48]}"
                ),
            )
        )
        if len(issues) >= 4:
            break

    issues.extend(
        _sibling_list_issues(translated_blocks, page_number, exclusion_bboxes)
    )
    return issues


def _sibling_list_issues(
    translated_blocks: Sequence[_Block],
    page_number: int,
    exclusion_bboxes: Sequence[BBox],
) -> List[object]:
    """Sibling list items (same marker style, same column) with size drift."""
    groups: Dict[Tuple[str, int], List[Tuple[_Block, float]]] = {}
    for block in translated_blocks:
        if block.cjk_chars() < _MIN_BLOCK_CJK_CHARS:
            continue
        if any(
            _bbox_overlap_area(block.bbox, banned) > 0.35 * _bbox_area(block.bbox)
            for banned in exclusion_bboxes
        ):
            continue
        match = _LIST_MARKER_RE.match(block.text)
        if not match:
            continue
        marker = match.group(0).strip()
        style = "digit" if any(ch.isdigit() for ch in marker) else marker[:1]
        column = int(block.bbox[0] // 24)
        size = block.dominant_size()
        if size <= 0:
            continue
        groups.setdefault((style, column), []).append((block, size))

    issues: List[object] = []
    for (style, _column), members in groups.items():
        if len(members) < 2:
            continue
        sizes = [size for _, size in members]
        largest, smallest = max(sizes), min(sizes)
        if smallest <= 0 or largest / smallest <= _SIBLING_RATIO_TOLERANCE:
            continue
        offender = min(members, key=lambda item: item[1])[0]
        issues.append(
            _issue(
                page_number,
                "list_font_inconsistent",
                (
                    f"Page {page_number}: sibling list items ('{style}' markers) "
                    f"render at {smallest:.2f}-{largest:.2f}pt "
                    f"(x{largest / smallest:.2f} spread) at "
                    f"x={offender.bbox[0]:.1f}, y={offender.bbox[1]:.1f}: "
                    f"{' '.join(offender.text.split())[:48]}"
                ),
            )
        )
    return issues


# ---------------------------------------------------------------------------
# preserved regions / formulas
# ---------------------------------------------------------------------------


def _preserved_region_issues(
    original_ink: _InkCache,
    translated_ink: _InkCache,
    page_number: int,
    regions: Sequence[BBox],
    caption_bands: Sequence[BBox],
) -> List[object]:
    issues: List[object] = []
    flagged = 0
    for region in regions:
        if _bbox_area(region) < _PRESERVED_MIN_AREA:
            continue
        # Running headers sit in the top margin band and are legitimately
        # translated even when region extraction labels them preserved.
        if region[3] <= 48.0 and (region[3] - region[1]) <= 16.0:
            continue
        bands = [
            (band[1] - 2.0, band[3] + 2.0)
            for band in caption_bands
            if _bbox_intersects(region, band)
        ]
        width = max(1.0, region[2] - region[0])
        height = max(1.0, region[3] - region[1])
        columns = max(24, min(128, int(width / 4)))
        rows = max(12, min(96, int(height / 4)))
        source_mask = original_ink.get().mask(
            region, columns=columns, rows=rows, exclude_bands=bands
        )
        translated_mask = translated_ink.get().mask(
            region, columns=columns, rows=rows, exclude_bands=bands
        )
        if source_mask is None or translated_mask is None:
            continue
        if not source_mask:
            continue
        coverage = _mask_coverage(source_mask, translated_mask, columns, rows)
        if coverage >= _PRESERVED_COVERAGE_MIN:
            continue
        issues.append(
            _issue(
                page_number,
                "preserved_ink_mismatch",
                (
                    f"Page {page_number}: preserved region at "
                    f"x={region[0]:.0f}, y={region[1]:.0f} "
                    f"({region[2] - region[0]:.0f}x{region[3] - region[1]:.0f}pt) "
                    f"renders with ink coverage {coverage:.2f} vs original"
                ),
            )
        )
        flagged += 1
        if flagged >= 4:
            break
    return issues


def _edge_cut(profile: List[float], first: int, second: int) -> bool:
    """Partial glyph ink on the outermost rendered rows.

    Full-width rows (>0.85) are rules (fraction bars, cmidrules) that
    legitimately sit on the line boundary and must not count as clipping.
    """
    return (
        0.03 < profile[first] <= 0.85
        and 0.03 < profile[second] <= 0.85
    )


_SPRITE_MAX_HEIGHT_PT = 34.0
_SPRITE_MIN_WIDTH_PT = 8.0
_SPRITE_MAX_INK = 0.42
_SPRITE_EDGE_MIN = 0.05
_SPRITE_BLOCKING_EDGE_MIN = 0.12
_MASK_OPACITY_MIN = 16


def _image_mask_row_profile(raw: dict) -> Optional[List[float]]:
    """Return per-row opacity density for an image's own alpha mask."""
    mask = raw.get("mask")
    if not isinstance(mask, bytes) or not mask:
        return None
    try:
        import fitz

        pixmap = fitz.Pixmap(mask)
    except Exception:
        return None
    if pixmap.width <= 0 or pixmap.height <= 0 or pixmap.n <= 0:
        return None
    samples = pixmap.samples
    profile: List[float] = []
    for row in range(pixmap.height):
        start = row * pixmap.stride
        pixels = samples[start : start + pixmap.width * pixmap.n : pixmap.n]
        profile.append(
            sum(value >= _MASK_OPACITY_MIN for value in pixels) / pixmap.width
        )
    return profile
def _edge_is_decoration_bar(profile: Sequence[float], *, from_top: bool) -> bool:
    """Dense edge rows followed by a whitespace gap: an overline/underline."""
    rows = list(profile) if from_top else list(reversed(profile))
    if len(rows) < 5:
        return False
    edge = min(rows[0], rows[1])
    if edge < 0.35:
        return False
    return min(rows[2:5]) <= 0.06


def _math_clip_issues(
    original_page: object,
    original_ink: _InkCache,
    translated_page: object,
    translated_ink: _InkCache,
    page_number: int,
) -> List[object]:
    """Inline formula sprites whose glyph ink is cut at the sprite edge.

    The engine rasterises prose rows that carry inline formulas into small
    image sprites (~8-12pt tall). A too-tight crop cuts ascenders,
    superscripts or descenders, which shows up as ink on the outermost
    pixel rows of the sprite; intact sprites keep a clean margin.
    Real figures are excluded by the height/ink limits, and image tiles
    that keep their source position (fragments of preserved diagrams,
    which have edge ink by design) are exempt: formula sprites are stamped
    at reflowed positions, so they never coincide with a source image.
    """
    source_images = [
        tuple(float(v) for v in raw["bbox"])
        for raw in original_page.get_text("dict").get("blocks", [])
        if raw.get("type") == 1
    ]
    issues: List[object] = []
    checked = 0
    for raw in translated_page.get_text("dict").get("blocks", []):
        if raw.get("type") != 1:
            continue
        bbox = tuple(float(v) for v in raw["bbox"])
        height = bbox[3] - bbox[1]
        width = bbox[2] - bbox[0]
        if height <= 3.0 or height > _SPRITE_MAX_HEIGHT_PT:
            continue
        if width < _SPRITE_MIN_WIDTH_PT:
            continue
        area = max(1.0, width * height)
        if any(
            _bbox_overlap_area(bbox, source) >= 0.5 * area
            for source in source_images
        ):
            continue
        # Vector diagrams restored in place (redaction repair) have no
        # source image block; recognise them by unchanged source ink.
        columns = max(12, min(96, int(width / 2)))
        rows = max(8, min(64, int(height / 2)))
        source_mask = original_ink.get().mask(bbox, columns=columns, rows=rows)
        translated_mask = translated_ink.get().mask(bbox, columns=columns, rows=rows)
        if (
            source_mask
            and translated_mask
            and _mask_coverage(source_mask, translated_mask, columns, rows) >= 0.75
        ):
            continue
        checked += 1
        if checked > 120:
            break
        # Page-raster sampling includes foreign text and rules that happen to
        # overlap the image bbox. Formula sprites carry an alpha mask, which
        # is the exact glyph silhouette and therefore the authoritative clip
        # signal. Old/external images without a mask retain the raster fallback.
        profile = _image_mask_row_profile(raw)
        if profile is None:
            profile = translated_ink.get().row_profile(bbox)
        if len(profile) < 8:
            continue
        mean_ink = sum(profile) / len(profile)
        if raw.get("mask") and mean_ink < 0.001:
            issues.append(
                _issue(
                    page_number,
                    "formula_visible_ink_mismatch",
                    (
                        f"Page {page_number}: inline formula sprite at "
                        f"x={bbox[0]:.1f}, y={bbox[1]:.1f} "
                        f"({width:.0f}x{height:.0f}pt) has no visible ink"
                    ),
                )
            )
            if len(issues) >= 5:
                break
            continue
        if not 0.01 <= mean_ink <= _SPRITE_MAX_INK:
            continue
        top = min(profile[0], profile[1])
        bottom = min(profile[-1], profile[-2])
        top_cut = _SPRITE_EDGE_MIN <= top <= 0.85
        bottom_cut = _SPRITE_EDGE_MIN <= bottom <= 0.85
        # Overlined and underlined symbols (Ā, x̲) carry a solid stroke at
        # the sprite boundary by design: a dense edge followed by a
        # whitespace gap before the glyph body is decoration, not a cut.
        # Sliced glyphs leave fragmentary edge ink (well under bar density)
        # or continue straight into the body without any gap.
        if top_cut and _edge_is_decoration_bar(profile, from_top=True):
            top_cut = False
        if bottom_cut and _edge_is_decoration_bar(profile, from_top=False):
            bottom_cut = False
        if not top_cut and not bottom_cut:
            continue
        edge = "top" if top_cut else "bottom"
        edge_density = top if top_cut else bottom
        issues.append(
            _issue(
                page_number,
                "formula_clipped",
                (
                    f"Page {page_number}: inline formula sprite at "
                    f"x={bbox[0]:.1f}, y={bbox[1]:.1f} "
                    f"({width:.0f}x{height:.0f}pt) has ink cut at its {edge} "
                    f"edge (density {edge_density:.2f})"
                ),
                severity=(
                    "error"
                    if edge_density >= _SPRITE_BLOCKING_EDGE_MIN
                    else "warning"
                ),
            )
        )
        if len(issues) >= 5:
            break
    return issues


# ---------------------------------------------------------------------------
# table structure
# ---------------------------------------------------------------------------


def _page_line_art(
    page: object,
) -> Tuple[List[Tuple[float, float, float]], List[float]]:
    """Horizontal rules (y, x0, x1) plus y-centres of bezier curve items.

    Curves are how plot lines are drawn; their presence around a rule
    cluster marks a chart frame rather than a table grid. Dashed rules are
    chart gridlines, never table rules, and are dropped at the source.
    """
    rules: List[Tuple[float, float, float]] = []
    curves: List[float] = []
    try:
        drawings = page.get_drawings()
    except Exception:
        return rules, curves
    for drawing in drawings:
        dashes = drawing.get("dashes")
        dashed = bool(dashes) and dashes not in ("", "[] 0")
        for item in drawing.get("items", []):
            kind = item[0]
            if kind == "l":
                if dashed:
                    continue
                p1, p2 = item[1], item[2]
                if abs(p1.y - p2.y) <= 0.6 and abs(p2.x - p1.x) >= 40.0:
                    rules.append(
                        (
                            round((p1.y + p2.y) / 2.0, 1),
                            min(p1.x, p2.x),
                            max(p1.x, p2.x),
                        )
                    )
            elif kind == "re":
                if dashed:
                    continue
                rect = item[1]
                if rect.height <= 1.6 and rect.width >= 40.0:
                    rules.append((round((rect.y0 + rect.y1) / 2.0, 1), rect.x0, rect.x1))
            elif kind == "c":
                curves.append(float(item[1].y))
    merged: Dict[float, Tuple[float, float]] = {}
    for y, x0, x1 in rules:
        if y in merged:
            merged[y] = (min(merged[y][0], x0), max(merged[y][1], x1))
        else:
            merged[y] = (x0, x1)
    return sorted((y, x0, x1) for y, (x0, x1) in merged.items()), curves


def _page_vertical_lines(page: object) -> List[Tuple[float, float, float]]:
    """Vertical line segments (x, y0, y1) at least 24pt tall.

    Academic tables draw horizontal rules only; vertical edges around a
    rule cluster mean plot frames or legend boxes.
    """
    verticals: List[Tuple[float, float, float]] = []
    try:
        drawings = page.get_drawings()
    except Exception:
        return verticals
    for drawing in drawings:
        for item in drawing.get("items", []):
            kind = item[0]
            if kind == "l":
                p1, p2 = item[1], item[2]
                if abs(p1.x - p2.x) <= 0.6 and abs(p2.y - p1.y) >= 24.0:
                    verticals.append(
                        (
                            (p1.x + p2.x) / 2.0,
                            min(p1.y, p2.y),
                            max(p1.y, p2.y),
                        )
                    )
            elif kind == "re":
                rect = item[1]
                if rect.width <= 1.6 and rect.height >= 24.0:
                    verticals.append(((rect.x0 + rect.x1) / 2.0, rect.y0, rect.y1))
                elif rect.width >= 40.0 and rect.height >= 24.0:
                    # A stroked rectangle frame contributes its side edges.
                    verticals.append((rect.x0, rect.y0, rect.y1))
                    verticals.append((rect.x1, rect.y0, rect.y1))
    return verticals


def _rule_clusters(
    rules: Sequence[Tuple[float, float, float]],
) -> List[List[Tuple[float, float, float]]]:
    """Group stacked rules into table candidates."""
    clusters: List[List[Tuple[float, float, float]]] = []
    for rule in rules:
        if (
            clusters
            and rule[0] - clusters[-1][-1][0] <= 70.0
            and min(rule[2], clusters[-1][-1][2]) - max(rule[1], clusters[-1][-1][1])
            > 0.5 * min(rule[2] - rule[1], clusters[-1][-1][2] - clusters[-1][-1][1])
        ):
            clusters[-1].append(rule)
        else:
            clusters.append([rule])
    return [
        cluster
        for cluster in clusters
        if 3 <= len(cluster) <= 16
        and (cluster[-1][0] - cluster[0][0]) >= 18.0
    ]


def _line_table_bboxes(page: object) -> List[BBox]:
    """Return ruled table boxes that sparse horizontal clustering misses."""
    try:
        finder = page.find_tables(strategy="lines")
    except (AttributeError, RuntimeError, TypeError, ValueError):
        finder = None

    page_area = max(1.0, float(page.rect.width) * float(page.rect.height))
    page_width = max(1.0, float(page.rect.width))
    verticals = _page_vertical_lines(page)
    bboxes: List[BBox] = []
    for table in (finder.tables if finder is not None else []):
        if table.row_count < 2 or table.col_count < 2:
            continue
        left, top, right, bottom = (float(value) for value in table.bbox)
        bottom_extensions = [
            y1
            for x, y0, y1 in verticals
            if left - 2.0 <= x <= right + 2.0
            and -1.0 <= y0 - bottom <= 1.0
            and y1 > bottom + 2.0
        ]
        if len(bottom_extensions) >= 2:
            bottom = max(bottom, max(bottom_extensions))
        bbox = (left, top, right, bottom)
        area = _bbox_area(bbox)
        if area < 400.0 or area > page_area * 0.85:
            continue
        bboxes.append(bbox)

    # Many academic tables intentionally omit vertical rules.  Pair a run of
    # wide, aligned horizontal rules with its explicit caption instead of
    # treating every chart grid as a table.
    rules, _curves = _page_line_art(page)
    wide_rules = [rule for rule in rules if rule[2] - rule[1] >= page_width * 0.4]
    groups: List[List[Tuple[float, float, float]]] = []
    for rule in wide_rules:
        previous = groups[-1][-1] if groups else None
        compatible = False
        if previous is not None:
            overlap = min(rule[2], previous[2]) - max(rule[1], previous[1])
            compatible = (
                rule[0] - previous[0] <= 180.0
                and overlap >= 0.8 * min(
                    rule[2] - rule[1],
                    previous[2] - previous[1],
                )
            )
        if compatible:
            groups[-1].append(rule)
        else:
            groups.append([rule])

    try:
        page_blocks = _text_blocks(page)
        captions = [
            block
            for block in page_blocks
            if re.match(
                r"^(?:Table|Tab\.|\u8868)\s*\d",
                " ".join(block.text.split()),
                re.IGNORECASE,
            )
        ]
    except (AttributeError, RuntimeError, TypeError, ValueError):
        page_blocks = []
        captions = []
    for top_rule, bottom_rule in zip(wide_rules, wide_rules[1:]):
        top, left, top_right = top_rule
        bottom, bottom_left, right = bottom_rule
        if not 20.0 <= bottom - top <= 520.0:
            continue
        if abs(left - bottom_left) > 8.0 or abs(top_right - right) > 8.0:
            continue
        left = min(left, bottom_left)
        right = max(top_right, right)
        anchors = [
            caption
            for caption in captions
            if caption.bbox[0] < right
            and caption.bbox[2] > left
            and (
                -4.0 <= top - caption.bbox[3] <= 70.0
                or -4.0 <= caption.bbox[1] - bottom <= 70.0
            )
        ]
        cell_blocks = [
            block
            for block in page_blocks
            if block not in captions
            and left <= (block.bbox[0] + block.bbox[2]) / 2.0 <= right
            and top <= (block.bbox[1] + block.bbox[3]) / 2.0 <= bottom
        ]
        if anchors and len(cell_blocks) >= 3:
            bboxes.append((left, top, right, bottom))
    for group in groups:
        if len(group) < 3:
            continue
        left = min(rule[1] for rule in group)
        top = group[0][0]
        right = max(rule[2] for rule in group)
        bottom = group[-1][0]
        for caption in captions:
            horizontal_overlap = min(right, caption.bbox[2]) - max(
                left, caption.bbox[0]
            )
            if horizontal_overlap < 0.4 * min(
                right - left,
                caption.bbox[2] - caption.bbox[0],
            ):
                continue
            above_gap = top - caption.bbox[3]
            below_gap = caption.bbox[1] - bottom
            if -4.0 <= above_gap <= 70.0:
                bboxes.append((left, top, right, bottom))
                break
            if -4.0 <= below_gap <= 70.0:
                bboxes.append((left, top, right, max(bottom, caption.bbox[1] - 2.0)))
                break
    return bboxes


def _rule_is_text_underline(page: object, rule: Tuple[float, float, float]) -> bool:
    """True when the rule underlines a source text span.

    Underlines of replaced source text are legitimately erased together with
    the text they decorate; only free-standing table rules must survive.
    """
    rule_y, rule_x0, rule_x1 = rule
    width = max(1.0, rule_x1 - rule_x0)
    try:
        data = page.get_text("dict")
    except Exception:
        return False
    for block in data.get("blocks", []):
        if block.get("type") != 0:
            continue
        for line in block.get("lines", []):
            for span in line.get("spans", []):
                bx0, _by0, bx1, by1 = span["bbox"]
                # Underlines sit between the baseline and the glyph-box
                # bottom (descender space), up to a couple of points below.
                if not (by1 - 2.5 <= rule_y <= by1 + 2.8):
                    continue
                # An underline hugs its text horizontally; a table rule that
                # merely touches a caption's glyph box extends well past it.
                if rule_x0 < bx0 - 6.0 or rule_x1 > bx1 + 6.0:
                    continue
                overlap = min(rule_x1, bx1) - max(rule_x0, bx0)
                if overlap >= 0.7 * width:
                    return True
    return False


def _rule_pixel_coverage(
    page: object,
    rule: Tuple[float, float, float],
) -> float:
    """Fraction of the rule's span showing ink at 216dpi."""
    import fitz

    rule_y, rule_x0, rule_x1 = rule
    if rule_x1 - rule_x0 < 8.0:
        return 1.0
    scale = 3.0
    clip = fitz.Rect(rule_x0 + 0.5, rule_y - 1.2, rule_x1 - 0.5, rule_y + 1.6)
    try:
        pix = page.get_pixmap(matrix=fitz.Matrix(scale, scale), clip=clip)
    except Exception:
        return 1.0
    if pix.width <= 0 or pix.height <= 0:
        return 1.0
    samples, stride, n = pix.samples, pix.stride, pix.n
    dark_columns = 0
    for xx in range(pix.width):
        if any(samples[yy * stride + xx * n] < 130 for yy in range(pix.height)):
            dark_columns += 1
    return dark_columns / pix.width


def _rule_inside_regions(
    rule: Tuple[float, float, float],
    regions: Sequence[BBox],
) -> bool:
    y, x0, x1 = rule
    width = max(1.0, x1 - x0)
    for rx0, ry0, rx1, ry1 in regions:
        if not (ry0 - 2.0 <= y <= ry1 + 2.0):
            continue
        covered = min(x1, rx1 + 2.0) - max(x0, rx0 - 2.0)
        if covered >= width * 0.7:
            return True
    return False


def _figure_graphic_regions(
    graphic_regions: Sequence[BBox],
    caption_kinds: Sequence[Tuple[BBox, str]],
) -> List[BBox]:
    """Graphic envelopes paired with a nearby figure caption.

    ``collect_graphic_regions`` also finds boxed tables, so passing every
    envelope to table QA would hide genuine grid damage.  Figure art is
    exempt only when a figure caption provides an independent semantic cue.
    """
    figures: List[BBox] = []
    for region in graphic_regions:
        rx0, ry0, rx1, ry1 = region
        region_width = max(1.0, rx1 - rx0)
        for (cx0, cy0, cx1, cy1), kind in caption_kinds:
            if kind != "figure":
                continue
            caption_width = max(1.0, cx1 - cx0)
            horizontal_overlap = min(rx1, cx1) - max(rx0, cx0)
            if horizontal_overlap < 0.25 * min(region_width, caption_width):
                continue
            below_gap = cy0 - ry1
            above_gap = ry0 - cy1
            # Drawing envelopes often include axis labels or whitespace that
            # reaches into the caption's extracted line box by a few points.
            if -16.0 <= below_gap <= 180.0 or -16.0 <= above_gap <= 80.0:
                figures.append(region)
                break
    return figures


def _preserved_regions_above_figure_captions(
    preserved_regions: Sequence[BBox],
    caption_kinds: Sequence[Tuple[BBox, str]],
) -> List[BBox]:
    """Preserved figure parts above captions, excluding following body math."""
    figures: List[BBox] = []
    for region in preserved_regions:
        rx0, _ry0, rx1, ry1 = region
        region_width = max(1.0, rx1 - rx0)
        for (cx0, cy0, cx1, _cy1), kind in caption_kinds:
            if kind != "figure":
                continue
            caption_width = max(1.0, cx1 - cx0)
            horizontal_overlap = min(rx1, cx1) - max(rx0, cx0)
            if horizontal_overlap < 0.25 * min(region_width, caption_width):
                continue
            if -16.0 <= cy0 - ry1 <= 180.0:
                figures.append(region)
                break
    return figures


def _table_structure_issues(
    page_number: int,
    original_rules: Sequence[Tuple[float, float, float]],
    original_curves: Sequence[float],
    translated_rules: Sequence[Tuple[float, float, float]],
    original_page: object = None,
    translated_page: object = None,
    preserved_regions: Sequence[BBox] = (),
    graphic_regions: Sequence[BBox] = (),
) -> List[object]:
    """Compare table rule geometry between the original and the translation.

    Tables are re-typeset cell by cell, so a header row set at the wrong
    baseline shifts (or drops) its rules relative to the original grid.
    Vector counting alone misses fill-overpainted rules (the rule object
    survives redaction but background paint covers its middle), so each
    cluster rule is also compared by rendered ink coverage.

    Rules living inside preserved graphic regions (plot frames, legend
    boxes) are restored verbatim and verified by the preserved-ink module;
    counting them here only manufactures chart-shaped false tables.
    """
    excluded_regions = list(preserved_regions) + list(graphic_regions)
    if excluded_regions:
        original_rules = [
            rule
            for rule in original_rules
            if not _rule_inside_regions(rule, excluded_regions)
        ]
        translated_rules = [
            rule
            for rule in translated_rules
            if not _rule_inside_regions(rule, excluded_regions)
        ]
    original_clusters = _rule_clusters(original_rules)
    if not original_clusters:
        return []
    rule_geometry_identical = bool(
        len(original_rules) == len(translated_rules)
        and all(
            abs(original[0] - translated[0]) <= 0.2
            and abs(original[1] - translated[1]) <= 0.5
            and abs(original[2] - translated[2]) <= 0.5
            for original, translated in zip(original_rules, translated_rules)
        )
    )
    original_verticals = (
        _page_vertical_lines(original_page) if original_page is not None else []
    )
    issues: List[object] = []
    for cluster in original_clusters:
        top, bottom = cluster[0][0], cluster[-1][0]
        # Chart frames: narrow rules (subplot boxes) or bezier plot lines
        # inside the band mean this is a figure, not a re-typeset table.
        if max(rule[2] - rule[1] for rule in cluster) < 120.0:
            continue
        curves_in_band = sum(
            1 for y in original_curves if top - 6.0 <= y <= bottom + 6.0
        )
        if curves_in_band >= 10:
            continue
        band_height = max(1.0, bottom - top)
        cluster_x0 = min(rule[1] for rule in cluster)
        cluster_x1 = max(rule[2] for rule in cluster)
        frame_verticals = sum(
            1
            for x, y0, y1 in original_verticals
            if cluster_x0 - 4.0 <= x <= cluster_x1 + 4.0
            and min(bottom, y1) - max(top, y0) >= band_height * 0.55
        )
        if frame_verticals >= 2:
            # Plot frames and legend boxes: vertical edges spanning the rule
            # band mean boxed chart art, not an academic table.
            continue
        # Match per source rule, mirroring the cluster criterion: a short
        # in-table rule (equation underline glued into the cluster) must not
        # be dropped for covering little of the whole cluster envelope,
        # otherwise the original never matches its own geometry.
        counterpart = (
            list(cluster)
            if rule_geometry_identical
            else [
                rule
                for rule in translated_rules
                if top - 12.0 <= rule[0] <= bottom + 12.0
                and any(
                    min(rule[2], source[2]) - max(rule[1], source[1])
                    > 0.5 * min(rule[2] - rule[1], source[2] - source[1])
                    for source in cluster
                )
            ]
        )
        if not counterpart:
            issues.append(
                _issue(
                    page_number,
                    "table_structure_mismatch",
                    (
                        f"Page {page_number}: table grid at y={top:.0f}-{bottom:.0f} "
                        f"lost its rules in the translation"
                    ),
                )
            )
            continue
        offsets = sorted(rule[0] - top for rule in cluster)
        translated_top = counterpart[0][0]
        translated_offsets = sorted(rule[0] - translated_top for rule in counterpart)
        if len(offsets) != len(translated_offsets):
            issues.append(
                _issue(
                    page_number,
                    "table_structure_mismatch",
                    (
                        f"Page {page_number}: table at y={top:.0f} has "
                        f"{len(translated_offsets)} rules vs {len(offsets)} in the "
                        f"original"
                    ),
                )
            )
            continue
        drift = max(
            abs(original_offset - translated_offset)
            for original_offset, translated_offset in zip(offsets, translated_offsets)
        )
        if drift > 2.5:
            issues.append(
                _issue(
                    page_number,
                    "table_structure_mismatch",
                    (
                        f"Page {page_number}: table at y={top:.0f} rules drifted "
                        f"up to {drift:.1f}pt from the original grid"
                    ),
                )
            )
            if len(issues) >= 3:
                break
            continue
        if original_page is not None and translated_page is not None:
            for rule in cluster:
                source_coverage = _rule_pixel_coverage(original_page, rule)
                if source_coverage < 0.85:
                    continue
                translated_coverage = _rule_pixel_coverage(translated_page, rule)
                if source_coverage - translated_coverage > 0.25 and not (
                    _rule_is_text_underline(original_page, rule)
                ):
                    issues.append(
                        _issue(
                            page_number,
                            "table_structure_mismatch",
                            (
                                f"Page {page_number}: table rule at y={rule[0]:.1f} "
                                f"is partially erased in the translation "
                                f"(ink coverage {source_coverage:.2f} -> "
                                f"{translated_coverage:.2f})"
                            ),
                        )
                    )
                    break
        if len(issues) >= 3:
            break
    return issues


def _display_alignment_issues(
    original_ink: _InkCache,
    translated_ink: _InkCache,
    page_number: int,
    equation_rows: Sequence[BBox],
    *,
    algorithm_regions: Sequence[BBox] = (),
    source_role_blocks: Sequence[object] = (),
) -> List[object]:
    issues: List[object] = []
    page_width = float(original_ink._page.rect.width)
    for row in equation_rows:
        row_area = _bbox_area(row)
        if row_area < 400.0:
            continue
        if any(
            _bbox_overlap_area(row, region) >= row_area * 0.80
            for region in algorithm_regions
        ):
            continue
        if any(
            getattr(block, "formula_anchors", ())
            and _bbox_overlap_area(row, block.bbox) >= row_area * 0.80
            for block in source_role_blocks
        ):
            continue
        observation_window = (
            max(0.0, row[0] - _DISPLAY_ALIGN_WINDOW_PAD_PT),
            row[1],
            min(page_width, row[2] + _DISPLAY_ALIGN_WINDOW_PAD_PT),
            row[3],
        )
        source_profile = original_ink.get().centroid_x(observation_window)
        translated_profile = translated_ink.get().centroid_x(observation_window)
        if source_profile is None or translated_profile is None:
            continue
        drift = abs(source_profile - translated_profile)
        if drift <= _DISPLAY_ALIGN_TOLERANCE_PT:
            continue
        issues.append(
            _issue(
                page_number,
                "display_formula_misaligned",
                (
                    f"Page {page_number}: displayed equation at y={row[1]:.0f} "
                    f"shifted horizontally by {drift:.0f}pt vs original"
                ),
                severity="warning",
            )
        )
        if len(issues) >= 3:
            break
    return issues


# ---------------------------------------------------------------------------
# references
# ---------------------------------------------------------------------------


def _reference_issues(
    original_page: object,
    translated_page: object,
    page_number: int,
    reference_y: Optional[float],
) -> List[object]:
    if reference_y is None:
        return []
    issues: List[object] = []

    def reference_spans(page: object) -> List[_Span]:
        page_spans: List[_Span] = []
        for page_block in _text_blocks(page):
            if page_block.bbox[1] < reference_y - 4.0:
                continue
            page_spans.extend(
                span for span in page_block.spans if span.text.strip()
            )
        return page_spans[:400]

    original_spans = reference_spans(original_page)
    spans: List[_Span] = []
    spans.extend(reference_spans(translated_page))

    def same_source_span(candidate: _Span, source: _Span) -> bool:
        if candidate.text.strip() != source.text.strip():
            return False
        return all(
            abs(float(candidate.bbox[index]) - float(source.bbox[index])) <= 2.0
            for index in range(4)
        )

    def source_has_same_overlap(first: _Span, second: _Span) -> bool:
        for source_index, source_first in enumerate(original_spans):
            for source_second in original_spans[source_index + 1 :]:
                same_order = same_source_span(first, source_first) and same_source_span(
                    second, source_second
                )
                reverse_order = same_source_span(first, source_second) and same_source_span(
                    second, source_first
                )
                if not (same_order or reverse_order):
                    continue
                source_width = min(source_first.bbox[2], source_second.bbox[2]) - max(
                    source_first.bbox[0], source_second.bbox[0]
                )
                source_height = min(source_first.bbox[3], source_second.bbox[3]) - max(
                    source_first.bbox[1], source_second.bbox[1]
                )
                if source_width > 5.0 and source_height > 4.5:
                    return True
        return False

    overlap_reported = 0
    for i in range(len(spans)):
        for j in range(i + 1, len(spans)):
            first, second = spans[i], spans[j]
            width = min(first.bbox[2], second.bbox[2]) - max(
                first.bbox[0], second.bbox[0]
            )
            height = min(first.bbox[3], second.bbox[3]) - max(
                first.bbox[1], second.bbox[1]
            )
            if width <= 5.0 or height <= 4.5:
                continue
            if first.text.strip() == second.text.strip():
                continue
            if len(first.text.strip()) < 4 or len(second.text.strip()) < 4:
                continue
            if source_has_same_overlap(first, second):
                continue
            issues.append(
                _issue(
                    page_number,
                    "reference_overlap",
                    (
                        f"Page {page_number}: references text overlap at "
                        f"x={max(first.bbox[0], second.bbox[0]):.1f}, "
                        f"y={max(first.bbox[1], second.bbox[1]):.1f}: "
                        f"'{first.text.strip()[:32]}' over "
                        f"'{second.text.strip()[:32]}'"
                    ),
                )
            )
            overlap_reported += 1
            if overlap_reported >= 3:
                break
        if overlap_reported >= 3:
            break

    original_has_bold = any(
        "bold" in span.font.lower()
        and len(_CJK_HAN_RE.findall(span.text)) + len(span.text.strip()) >= 6
        for block in _text_blocks(original_page)
        if block.bbox[1] >= reference_y - 4.0
        for span in block.spans
    )
    if not original_has_bold:
        for span in spans:
            if "bold" not in span.font.lower():
                continue
            stripped = span.text.strip()
            if len(stripped) < 6:
                continue
            issues.append(
                _issue(
                    page_number,
                    "reference_bold_style",
                    (
                        f"Page {page_number}: bold weight injected into "
                        f"references at x={span.bbox[0]:.1f}, "
                        f"y={span.bbox[1]:.1f}: '{stripped[:40]}'"
                    ),
                    severity="warning",
                )
            )
            break
    return issues


# ---------------------------------------------------------------------------
# untranslated paragraphs
# ---------------------------------------------------------------------------


_STOPWORD_RE = re.compile(
    r"\b(?:the|is|are|was|were|of|and|to|in|for|with|that|this|which|from|by)\b",
    re.IGNORECASE,
)
_SHORT_FORMULA_PROSE_CUE_RE = re.compile(
    r"\b(?:where|with|such\s+that|for\s+any|denotes?|represents?|is|are)\b",
    re.IGNORECASE,
)
_AFFILIATION_RE = re.compile(
    r"University|Institute|Institution|School|Sch\.|Laboratory|Lab\b|"
    r"Department|Dept\.|College|Academy|Corresponding|Email|@|"
    r"\.edu\b|\.com\b|\.cn\b|\.org\b|Univ\.",
    re.IGNORECASE,
)
_BIBLIOGRAPHY_MARKER_RE = re.compile(
    r"\bIn:\s|\(\d{4}\)|\bpp\.\s?\d|\bvol\.\s?\d|arXiv|\bdoi\b|proceedings of",
    re.IGNORECASE,
)
_PSEUDOCODE_LINE_RE = re.compile(r"\b\d{1,2}:\s*[A-Z]")
# Quoted sample/prompt boxes ("Title: ...", "Poor English input: ...",
# "Q: ... A: ...") are evidentiary content that stays verbatim by design.
# Scans anywhere because PyMuPDF merges consecutive lines without spaces
# ("...SplitSubtitle: Those..."), hiding line-initial labels.
_EXAMPLE_LABEL_RE = re.compile(
    r"[A-Z][a-zA-Z]{0,20}(?:\s[a-zA-Z]{1,15}){0,3}:\s*[\"'A-Z0-9]"
)
_PSEUDOCODE_KEYWORD_RE = re.compile(
    r"(?<![A-Za-z])(?:Update|Initialize|Compute|Return|Require|Ensure|Input|"
    r"Output|Apply|Sample|repeat|until|while|end\s+(?:for|while|if))(?![a-z])"
)


_SAMPLE_LABEL_PREFIX_RE = re.compile(
    r"^\s*(?:(?:Q|A|Question|Answer|Input|Output|Prompt|Human|Assistant|User|"
    r"System|Title|Subtitle)\s*[:\uff1a]|"
    r"(?:Context|Target\s+Completion)\s*(?:→|->|\?)|"
    r"Example\s+for\b[^:\uff1a]{0,120}[:\uff1a])",
    re.IGNORECASE,
)
_AUTHOR_NOTE_RE = re.compile(
    r"\b(?:authors?\s+(?:are\s+)?listed|author(?:s|ship)?\s+order|"
    r"alphabetical\s+order|equal(?:ly)?\s+contribut(?:ed|ion|ions)?|"
    r"contributions?\s+(?:are\s+)?listed)\b",
    re.IGNORECASE,
)


def _looks_like_aligned_preserved_rows(block: _Block) -> bool:
    """Recognize borderless parameter and formula-description tables."""
    spans = [span for span in block.spans if span.text.strip()]
    rows: List[List[_Span]] = []
    for span in sorted(
        spans,
        key=lambda item: ((item.bbox[1] + item.bbox[3]) / 2.0, item.bbox[0]),
    ):
        center_y = (span.bbox[1] + span.bbox[3]) / 2.0
        for row in rows:
            row_center = _median(
                [(item.bbox[1] + item.bbox[3]) / 2.0 for item in row]
            )
            if abs(center_y - row_center) <= 2.5:
                row.append(span)
                break
        else:
            rows.append([span])
    if len(rows) < 4 or re.search(r"[.!?](?:\s|$)", block.text):
        return False

    structural_rows = 0
    for row in rows:
        ordered = sorted(row, key=lambda item: item.bbox[0])
        gaps = [
            (ordered[index + 1].bbox[0] - ordered[index].bbox[2], index)
            for index in range(len(ordered) - 1)
        ]
        if not gaps:
            continue
        largest_gap, split_index = max(gaps)
        if largest_gap < 12.0:
            continue
        value_spans = ordered[split_index + 1 :]
        value_text = "".join(span.text for span in value_spans)
        if not (
            re.search(r"\d|[=<>▷τλ%]", value_text)
            or any(_MATH_FONT_RE.search(span.font) for span in value_spans)
        ):
            continue
        structural_rows += 1
    return structural_rows >= max(4, int(len(rows) * 0.6))


def _on_float_side_of_caption(
    block_bbox: BBox,
    caption_kinds: Sequence[Tuple[BBox, str]],
) -> bool:
    """Whether the block sits on the float side of a nearby caption.

    Figure captions sit below their float, table captions on either side
    of theirs (arXiv styles use both). Quoted evidence inside the float is
    verbatim by design; prose on the body side of a figure caption is
    ordinary text (the GuidedVLA V-A case: analysis prose below a figure
    caption must stay flagged).
    """
    bx0, by0, bx1, by1 = block_bbox
    for (cx0, cy0, cx1, cy1), kind in caption_kinds:
        if min(bx1, cx1) - max(bx0, cx0) <= 0.0:
            continue
        if kind == "figure" and by1 <= cy0 + 4.0 and cy0 - by1 <= 150.0:
            return True
        if kind == "table" and by0 >= cy1 - 4.0 and by0 - cy1 <= 150.0:
            return True
        if kind == "table" and by1 <= cy0 + 4.0:
            # Bottom-set table caption: the table body is everything above.
            return True
    return False


def _looks_like_subfigure_caption(
    block_bbox: BBox,
    text: str,
    caption_kinds: Sequence[Tuple[BBox, str]],
) -> bool:
    """Identify prose panel captions next to the numbered figure caption."""
    if not re.match(r"^\s*\([a-z]\)\s+[A-Z]", text):
        return False
    if len(_STOPWORD_RE.findall(text)) < 2:
        return False
    bx0, by0, bx1, by1 = block_bbox
    for (cx0, cy0, cx1, cy1), kind in caption_kinds:
        if kind != "figure":
            continue
        horizontal_overlap = min(bx1, cx1) - max(bx0, cx0)
        if horizontal_overlap <= 0:
            continue
        if -6.0 <= cy0 - by1 <= 48.0 or -6.0 <= by0 - cy1 <= 48.0:
            return True
    return False


def _untranslated_block_issues(
    translated_page: object,
    page_number: int,
    *,
    original_page: Optional[object] = None,
    reference_y: Optional[float],
    table_bands: Sequence[BBox] = (),
    preserved_regions: Sequence[BBox] = (),
    algorithm_regions: Sequence[BBox] = (),
    caption_kinds: Sequence[Tuple[BBox, str]] = (),
    graphic_regions: Sequence[BBox] = (),
) -> List[object]:
    """Paragraphs of running English prose left in the translated page.

    Gates are text-based rather than region-based on purpose: a
    mis-detected preserved region is precisely how prose escapes
    translation, so trusting those regions here would hide the defect
    (GuidedVLA section V-A, 2026-08-11). Author lists, figure legends,
    references and config tables carry almost no English stopwords and
    stay exempt. Two deliberate exceptions cover quoted evidence kept
    verbatim by policy: algorithm floats (GPT-3 style prompt/completion
    samples), and preserved content that starts with a sample label or
    sits on the float side of an adjacent caption (chain-of-thought
    exemplar boxes, dataset table cells, figure-embedded instructions).
    """
    from .pdf_layout import (
        _VERBATIM_GENERATED_SAMPLE_RE,
        _formatted_dataset_example_region_bboxes,
        _is_reference_or_formula_text,
    )

    issues: List[object] = []
    source_blocks = _text_blocks(original_page) if original_page is not None else []
    generated_sample_labels = [
        source.bbox
        for source in source_blocks
        if _VERBATIM_GENERATED_SAMPLE_RE.search(" ".join(source.text.split()))
    ]
    formatted_dataset_regions = (
        _formatted_dataset_example_region_bboxes(original_page, source_blocks)
        if original_page is not None
        else []
    )
    for block in _text_blocks(translated_page):
        text = " ".join(block.text.split())
        if not text:
            continue
        if _VERBATIM_GENERATED_SAMPLE_RE.search(text):
            continue
        if any(
            min(block.bbox[2], label[2]) - max(block.bbox[0], label[0]) > 0.0
            and -4.0 <= block.bbox[1] - label[3] <= 520.0
            for label in generated_sample_labels
        ):
            continue
        words = block.latin_words()
        stopwords = len(_STOPWORD_RE.findall(text))
        cjk = block.cjk_chars()
        math_chars = sum(
            len(span.text.strip())
            for span in block.spans
            if _MATH_FONT_RE.search(span.font)
        )
        total_chars = sum(len(span.text.strip()) for span in block.spans)
        short_formula_prose = bool(
            cjk == 0
            and 2 <= words < _UNTRANSLATED_MIN_WORDS
            and math_chars >= 1
            and total_chars
            and math_chars / total_chars <= 0.85
            and _SHORT_FORMULA_PROSE_CUE_RE.search(text)
        )
        panel_caption = _looks_like_subfigure_caption(
            block.bbox,
            text,
            caption_kinds,
        )
        content_spans = [span for span in block.spans if span.text.strip()]
        formatted_sample_spans = sum(
            1
            for span in content_spans
            if any(
                region[0] - 2.0
                <= (span.bbox[0] + span.bbox[2]) / 2.0
                <= region[2] + 2.0
                and region[1] - 2.0
                <= (span.bbox[1] + span.bbox[3]) / 2.0
                <= region[3] + 2.0
                for region in formatted_dataset_regions
            )
        )
        if (
            content_spans
            and formatted_sample_spans >= 0.7 * len(content_spans)
        ):
            continue
        if algorithm_regions and any(
            _bbox_overlap_area(block.bbox, region) > 0.6 * _bbox_area(block.bbox)
            for region in algorithm_regions
        ):
            continue
        graphic_covered_spans = sum(
            1
            for span in content_spans
            if any(
                region[0] - 2.0
                <= (span.bbox[0] + span.bbox[2]) / 2.0
                <= region[2] + 2.0
                and region[1] - 2.0
                <= (span.bbox[1] + span.bbox[3]) / 2.0
                <= region[3] + 2.0
                for region in graphic_regions
            )
        )
        if not panel_caption and graphic_regions and (
            any(
                _bbox_overlap_area(block.bbox, region) > 0.6 * _bbox_area(block.bbox)
                for region in graphic_regions
            )
            or (
                content_spans
                and graphic_covered_spans >= 0.7 * len(content_spans)
            )
        ):
            # preserve_graphics_text keeps figure-internal text verbatim by
            # design; English inside a graphic envelope is not a miss.
            continue
        # Table walls fragment into one preserved strip per cell while the
        # extracted block spans whole rows including the gaps, so coverage
        # is judged span by span rather than on the block envelope.
        covered_spans = sum(
            1
            for span in content_spans
            if any(
                region[0] - 2.0 <= (span.bbox[0] + span.bbox[2]) / 2.0 <= region[2] + 2.0
                and region[1] - 2.0 <= (span.bbox[1] + span.bbox[3]) / 2.0 <= region[3] + 2.0
                for region in preserved_regions
            )
        )
        if content_spans and covered_spans >= 0.7 * len(content_spans):
            text_head = " ".join(block.text.split())
            if _SAMPLE_LABEL_PREFIX_RE.match(text_head):
                continue
            if _looks_like_aligned_preserved_rows(block):
                continue
            if (
                not panel_caption
                and stopwords < max(2.0, words / 7.0)
                and _on_float_side_of_caption(block.bbox, caption_kinds)
            ):
                continue
        if reference_y is not None and block.bbox[1] >= reference_y - 4.0:
            continue
        if words < _UNTRANSLATED_MIN_WORDS and not short_formula_prose:
            continue
        if cjk >= max(4, words // 8):
            continue
        if re.match(r"^\s*(Figure|Table|Algorithm)\s+\d", text):
            continue
        if stopwords < 2 and not short_formula_prose:
            continue
        if page_number == 1 and _AUTHOR_NOTE_RE.search(text):
            continue
        affiliation_hits = len(_AFFILIATION_RE.findall(text))
        if affiliation_hits >= 2 or (page_number == 1 and affiliation_hits >= 1):
            continue
        if (
            len(_PSEUDOCODE_LINE_RE.findall(text)) >= 2
            or len(_PSEUDOCODE_KEYWORD_RE.findall(text)) >= 2
        ):
            continue
        if len(_EXAMPLE_LABEL_RE.findall(text)) >= 2:
            continue
        if _is_reference_or_formula_text(text) and not short_formula_prose:
            continue
        # Rows inside a rendered table grid are preserved cell text, not
        # missed prose.
        center_x = (block.bbox[0] + block.bbox[2]) / 2.0
        center_y = (block.bbox[1] + block.bbox[3]) / 2.0
        if any(
            left - 4.0 <= center_x <= right + 4.0
            and top - 4.0 <= center_y <= bottom + 4.0
            for left, top, right, bottom in table_bands
        ):
            continue
        # Keyword-style preserved content (hyperparameter grids, config
        # dumps) is intentionally verbatim. Flowing prose has a much higher
        # stopword density, so it stays flagged even when a mis-detected
        # preserved region covers it.
        if not panel_caption and stopwords < max(2.0, words / 7.0) and any(
            _bbox_overlap_area(block.bbox, region) > 0.6 * _bbox_area(block.bbox)
            for region in preserved_regions
        ):
            continue
        if total_chars and math_chars / total_chars > 0.4 and not short_formula_prose:
            continue
        issues.append(
            _issue(
                page_number,
                "untranslated_block",
                (
                    f"Page {page_number}: paragraph still in English "
                    f"({words} words, {cjk} CJK chars) at "
                    f"x={block.bbox[0]:.1f}, y={block.bbox[1]:.1f}: {text[:56]}"
                ),
            )
        )
        if len(issues) >= 4:
            break
    return issues


# ---------------------------------------------------------------------------
# entry point
# ---------------------------------------------------------------------------


def inspect_translation(
    original_pdf: Path,
    translated_pdf: Path,
    *,
    max_pages: Optional[int] = None,
) -> List[object]:
    """Run the visual inspection suite over an original/translated pair."""

    from .pdf_layout import (
        _page_looks_like_reference_continuation,
        _reference_section_start_y,
        expand_bbox,
        open_pdf_detached,
        prepare_translation_units,
    )

    # Detached opens: mupdf mmaps path-opened documents and an iCloud-style
    # eviction mid-inspection turns page faults into SIGBUS.
    original = open_pdf_detached(original_pdf)
    translated = open_pdf_detached(translated_pdf)
    issues: List[object] = []
    try:
        preserved_regions: Dict[int, List[BBox]] = {}
        equation_rows: Dict[int, List[BBox]] = {}
        algorithm_regions: Dict[int, List[BBox]] = {}
        caption_bands: Dict[int, List[BBox]] = {}
        keepouts: Dict[int, List[BBox]] = {}
        source_role_blocks: Dict[int, List[object]] = {}
        try:
            units, _gutters, _skipped = prepare_translation_units(
                original,
                preserve_graphics_text=True,
                preserved_regions_out=preserved_regions,
                equation_rows_out=equation_rows,
                algorithm_regions_out=algorithm_regions,
            )
        except TypeError:
            units, _gutters, _skipped = prepare_translation_units(
                original,
                preserve_graphics_text=True,
                preserved_regions_out=preserved_regions,
            )
        except Exception:
            units = []
        graphic_regions_by_page: Dict[int, List[BBox]] = {}
        try:
            from .pdf_layout import collect_graphic_regions

            graphic_regions_by_page = collect_graphic_regions(original)
        except Exception:
            graphic_regions_by_page = {}
        caption_kinds: Dict[int, List[Tuple[BBox, str]]] = {}
        for unit_block, _prompt, _mapping in units:
            source_role_blocks.setdefault(unit_block.page_index, []).append(unit_block)
            if unit_block.block_type == "caption":
                caption_bands.setdefault(unit_block.page_index, []).append(
                    expand_bbox(unit_block.bbox, 2.0)
                )
                caption_text = " ".join(unit_block.text.split()).lstrip()
                kind = (
                    "table"
                    if re.match(r"^(?:Table|Tab\.|表)\s*\d", caption_text, re.IGNORECASE)
                    else "figure"
                )
                caption_kinds.setdefault(unit_block.page_index, []).append(
                    (expand_bbox(unit_block.bbox, 2.0), kind)
                )
            for keepout in unit_block.keepout_bboxes or []:
                keepouts.setdefault(unit_block.page_index, []).append(keepout)

        page_count = min(original.page_count, translated.page_count)
        if max_pages is not None:
            page_count = min(page_count, max_pages)
        reference_active = False
        for index in range(page_count):
            original_page = original[index]
            translated_page = translated[index]
            page_number = index + 1
            page_regions = preserved_regions.get(index, [])
            raw_graphic_regions = graphic_regions_by_page.get(index, [])
            page_figure_regions = _figure_graphic_regions(
                raw_graphic_regions,
                caption_kinds.get(index, []),
            )
            page_figure_regions.extend(
                _preserved_regions_above_figure_captions(
                    page_regions,
                    caption_kinds.get(index, []),
                )
            )
            page_keepouts = keepouts.get(index, [])
            page_captions = caption_bands.get(index, [])
            reference_y = _reference_section_start_y(translated_page)
            if reference_y is None:
                reference_y = _reference_section_start_y(original_page)
            if reference_y is not None:
                reference_active = True
            elif reference_active and (
                _page_looks_like_reference_continuation(translated_page)
                or _page_looks_like_reference_continuation(original_page)
                # Continuation pages whose entries merged into large blocks
                # defeat the entry-level heuristics; bibliography markers
                # (In:, years, arXiv, pp.) still give them away.
                or len(
                    _BIBLIOGRAPHY_MARKER_RE.findall(translated_page.get_text("text"))
                )
                >= 4
            ):
                reference_y = 0.0
            else:
                reference_active = False

            exclusions: List[BBox] = list(page_regions) + list(page_keepouts)
            original_ink = _InkCache(original_page)
            translated_ink = _InkCache(translated_page)
            original_rules, original_curves = _page_line_art(original_page)
            translated_rules, _ = _page_line_art(translated_page)
            table_bands: List[BBox] = []
            for cluster in _rule_clusters(translated_rules):
                left = max(rule[1] for rule in cluster)
                right = min(rule[2] for rule in cluster)
                if right <= left:
                    left = min(rule[1] for rule in cluster)
                    right = max(rule[2] for rule in cluster)
                table_bands.append(
                    (left, cluster[0][0], right, cluster[-1][0])
                )
            table_bands.extend(_line_table_bboxes(translated_page))

            issues.extend(
                _font_size_issues(
                    original_page,
                    translated_page,
                    page_number,
                    exclusion_bboxes=exclusions,
                    source_role_blocks=source_role_blocks.get(index, ()),
                )
            )
            issues.extend(
                _preserved_region_issues(
                    original_ink,
                    translated_ink,
                    page_number,
                    page_regions,
                    page_captions,
                )
            )
            issues.extend(
                _math_clip_issues(
                    original_page,
                    original_ink,
                    translated_page,
                    translated_ink,
                    page_number,
                )
            )
            issues.extend(
                _table_structure_issues(
                    page_number,
                    original_rules,
                    original_curves,
                    translated_rules,
                    original_page=original_page,
                    translated_page=translated_page,
                    preserved_regions=page_regions,
                    graphic_regions=page_figure_regions,
                )
            )
            issues.extend(
                _display_alignment_issues(
                    original_ink,
                    translated_ink,
                    page_number,
                    equation_rows.get(index, []),
                    algorithm_regions=algorithm_regions.get(index, []),
                    source_role_blocks=source_role_blocks.get(index, ()),
                )
            )
            issues.extend(
                _reference_issues(
                    original_page,
                    translated_page,
                    page_number,
                    reference_y,
                )
            )
            issues.extend(
                _untranslated_block_issues(
                    translated_page,
                    page_number,
                    original_page=original_page,
                    reference_y=reference_y,
                    table_bands=table_bands,
                    preserved_regions=page_regions,
                    algorithm_regions=algorithm_regions.get(index, []),
                    caption_kinds=caption_kinds.get(index, []),
                    graphic_regions=page_figure_regions,
                )
            )
    finally:
        translated.close()
        original.close()
    return issues
