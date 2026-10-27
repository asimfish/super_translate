"""Logical copy/search rows for raster formula tokens, independent of visible ink."""
from __future__ import annotations

from dataclasses import dataclass
from statistics import median

BBox = tuple[float, float, float, float]


@dataclass(frozen=True)
class FormulaCopyRow:
    text: str
    bbox: BBox
    baseline: float


def source_page_dict(document, page_index: int) -> dict:
    """Decode an immutable source page once, retaining char ownership and span text."""
    cache = getattr(document, "_pdfzh_accessibility_page_cache", None)
    if cache is None:
        cache = {}
        document._pdfzh_accessibility_page_cache = cache
    if page_index not in cache:
        import fitz

        raw = document[page_index].get_text(
            "rawdict", flags=fitz.TEXTFLAGS_RAWDICT & ~fitz.TEXT_PRESERVE_IMAGES,
        )
        for block in raw.get("blocks", []):
            for line in block.get("lines", []):
                for span in line.get("spans", []):
                    span["text"] = "".join(char["c"] for char in span.get("chars", []))
        cache[page_index] = raw
    return cache[page_index]


def _overlap_fraction(box: BBox, atom: BBox) -> float:
    area = max(0, min(box[2], atom[2]) - max(box[0], atom[0])) * max(
        0, min(box[3], atom[3]) - max(box[1], atom[1]),
    )
    return area / max(0.01, (box[2] - box[0]) * (box[3] - box[1]))


def formula_copy_rows(document, page_index: int, atom_bboxes, core_bbox: BBox, size: float):
    """Give each source glyph one row, even if a tall atom crosses row boundaries.

    Full-size relation glyphs establish logical baselines; fraction/script atoms
    and CMEX delimiters join the closest row by their physical centre. Their
    font origins alone can point into the previous row. Ambiguous single-row
    tokens keep the caller's existing semantic string.
    """
    # A fraction, radical or summation limit can span almost three em while
    # remaining one logical row. Preserve its canonical semantic token.
    if not atom_bboxes or core_bbox[3] - core_bbox[1] < size * 3.0:
        return []
    owned = []
    for block in source_page_dict(document, page_index).get("blocks", []):
        for line in block.get("lines", []):
            for span in line.get("spans", []):
                for char in span.get("chars", []):
                    box = tuple(char["bbox"])
                    if (
                        (char["c"].isprintable() or char["c"] in "\x00\x01\x0c\x12\x13")
                        and _overlap_fraction(box, core_bbox) >= 0.5
                        and any(
                            _overlap_fraction(box, atom) >= 0.5 for atom in atom_bboxes
                        )
                    ):
                        owned.append((char, span))
    anchors = [
        float(char["origin"][1]) for char, span in owned
        if char["c"] in "=≤≥→" and float(span.get("size", 0)) >= size * 0.8
    ]
    if len(anchors) < 2:
        anchors = [
            float(char["origin"][1]) for char, span in owned
            if float(span.get("size", 0)) >= size * 0.8
            and "CMEX" not in str(span.get("font", "")).upper()
        ]
    baselines = []
    for anchor in sorted(anchors):
        if not baselines or anchor - baselines[-1] > size * 0.5:
            baselines.append(anchor)
    if len(baselines) < 2 or any(
        right - left < size * 0.9 for left, right in zip(baselines, baselines[1:])
    ):
        return []
    offsets = [
        char["origin"][1] - (char["bbox"][1] + char["bbox"][3]) / 2
        for char, span in owned
        if char["c"] in "=≤≥→" and float(span.get("size", 0)) >= size * 0.8
    ]
    offset = median(offsets) if offsets else size * 0.3
    rows = [[] for _ in baselines]
    for char, span in owned:
        center = (char["bbox"][1] + char["bbox"][3]) / 2 + offset
        if "CMEX" in str(span.get("font", "")).upper() and char["c"] == "b":
            center = char["origin"][1]
        row = min(range(len(baselines)), key=lambda index: abs(center - baselines[index]))
        rows[row].append((char, span))
    result = []
    boundaries = [core_bbox[1], *[
        (left + right) / 2 - offset for left, right in zip(baselines, baselines[1:])
    ], core_bbox[3]]
    for index, row in enumerate(rows):
        if not row:
            continue
        # Retain source reading order: sorting individual glyphs by x would
        # interleave a fraction's numerator and denominator.
        text = []
        for char, span in row:
            value = char["c"]
            if "CMEX" in str(span.get("font", "")).upper():
                value = {
                    "P": "∑", "X": "∏", "p": "√", "\x00": "(", "\x01": ")",
                    "\x0c": "|", "\x12": "(", "\x13": ")",
                }.get(value, value)
            if value.isprintable():
                text.append(value)
        result.append(FormulaCopyRow(
            "".join(text).strip(),
            (min(char["bbox"][0] for char, _ in row), boundaries[index],
             max(char["bbox"][2] for char, _ in row), boundaries[index + 1]),
            baselines[index],
        ))
    return result
