"""Versioned family registry and source-owned book adaptation.

Recognition is not acceptance. Registry entries describe routing capabilities;
checksummed real-source/render evidence lives in the benchmark manifest.
"""

from __future__ import annotations

import json
import re
import statistics
from collections import Counter
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path


@lru_cache(maxsize=1)
def load_layout_registry() -> dict:
    registry = json.loads(Path(__file__).with_name("layout_families.json").read_text())
    validate_layout_registry(registry)
    return registry


def validate_layout_registry(registry: dict) -> None:
    if not isinstance(registry.get("version"), str):
        raise ValueError("layout registry must have a version")
    sampling = registry["sampling"]
    for key in ("front_pages", "spread_pages"):
        if type(sampling[key]) is not int or not 1 <= sampling[key] <= 32:
            raise ValueError(f"invalid sampling limit: {key}")
    geometry = registry["geometry"]
    for key in ("min_line_width_ratio", "wide_line_ratio", "wide_weight_ratio",
                "identity_top_ratio", "identity_bottom_ratio"):
        if type(geometry[key]) not in (float, int) or not 0 < geometry[key] < 1:
            raise ValueError(f"invalid geometry ratio: {key}")
    if not geometry["min_line_width_ratio"] < geometry["wide_line_ratio"]:
        raise ValueError("wide-line threshold must exceed narrow-line threshold")
    if not geometry["identity_top_ratio"] < geometry["identity_bottom_ratio"]:
        raise ValueError("identity bands overlap")
    for key in ("wide_line_min", "identity_pages"):
        if type(geometry[key]) is not int or not 1 <= geometry[key] <= 16:
            raise ValueError(f"invalid geometry limit: {key}")
    names = set()
    for family in registry["families"]:
        name = family["name"]
        if name in names or family["kind"] not in {"journal", "conference", "book", "generic"}:
            raise ValueError(f"invalid or duplicated layout family: {name}")
        if family.get("adapter", "generic") not in {"generic", "nature", "book"}:
            raise ValueError(f"unsupported layout adapter: {name}")
        names.add(name)
        policy = registry["policies"][family["policy"]]
        for key in ("caption_extra_height", "min_caption_font_size"):
            value = policy[key]
            if type(value) not in (float, int) or not 0 < value <= 72:
                raise ValueError(f"invalid layout policy: {name}.{key}")
        for pattern in family["markers"]:
            re.compile(pattern, re.I | re.M)
    if not {"unknown", "academic_book", "nature_portfolio"} <= names:
        raise ValueError("layout registry lacks required fallbacks/adapters")


def family_policy(name: str) -> dict[str, float | bool]:
    registry = load_layout_registry()
    entry = next((f for f in registry["families"] if f["name"] == name), None)
    return dict(registry["policies"][entry["policy"] if entry else "conservative"])


def _sample_pages(count: int) -> tuple[int, ...]:
    sampling = load_layout_registry()["sampling"]
    pages = set(range(min(count, sampling["front_pages"])))
    for i in range(1, sampling["spread_pages"] + 1):
        pages.add(round((count - 1) * i / sampling["spread_pages"]))
    return tuple(sorted(pages))


def _identity_text(page: object) -> str:
    """Publisher/venue bands on the first page, excluding ordinary body citations."""
    height = float(page.rect.height)
    geometry = load_layout_registry()["geometry"]
    lines = []
    for raw in page.get_text("dict").get("blocks", ()):
        for line in raw.get("lines", ()):
            if (line["bbox"][1] < height * geometry["identity_top_ratio"]
                    or line["bbox"][3] > height * geometry["identity_bottom_ratio"]):
                lines.append("".join(s.get("text", "") for s in line["spans"]))
    return "\n".join(lines).lower()


def infer_document_family(document: object) -> dict:
    from .journal_layout import is_nature_document
    from .layout_profiles import _caption_position, _text_blocks

    registry = load_layout_registry()
    sampled = _sample_pages(document.page_count)
    pages = [(index, document[index], _text_blocks(document[index])) for index in sampled]
    content = [(i, p, b) for i, p, b in pages if len(b) >= 3] or pages
    weights: Counter[int] = Counter()
    for _, page, boxes in content:
        weights[_source_column_count(boxes, float(page.rect.width))] += min(40, len(boxes)) or 1
    columns = weights.most_common(1)[0][0]
    _, page, boxes = max(content, key=lambda sample: len(sample[2]))
    text = "\n".join(document[i].get_text("text") for i in sampled)
    front = "\n".join(document[i].get_text("text") for i in sampled if i < 16)
    chapters = re.findall(r"(?im)^\s*chapter\s+(?:\d+|[IVX]+)\s*$", text)
    contents = bool(re.search(r"(?im)^\s*(?:table of )?contents\s*$", front))
    book_identity = bool(re.search(
        r"(?i)\bisbn(?:-1[03])?\b|\b\d+(?:st|nd|rd|th) edition\b", front,
    ))
    evidence = []
    kind = "generic"
    confidence = 0.65
    risk = "medium"
    name = "single_column" if columns == 1 else "generic_two_column"
    if is_nature_document(document):
        name, kind, confidence = "nature_portfolio", "journal", 0.96
        evidence.append("source Nature masthead and DOI")
    elif document.page_count >= 3 and ((contents and book_identity) or len(chapters) >= 2):
        name, kind, confidence = "academic_book", "book", 0.9
        evidence.append(
            "contents and edition/ISBN" if book_identity else "multiple chapter headings"
        )
    else:
        identity = "\n".join(_identity_text(document[i]) for i in range(min(
            registry["geometry"]["identity_pages"], document.page_count,
        )))
        for entry in registry["families"]:
            if columns not in entry.get("columns", [1, 2, 3]):
                continue
            match = next((m for m in entry["markers"] if re.search(m, identity, re.I | re.M)), None)
            if match:
                name, kind, confidence = entry["name"], entry["kind"], 0.88
                evidence.append("front-page publisher/venue band: " + match)
                break
        else:
            width, height = float(page.rect.width), float(page.rect.height)
            if columns == 1 and 380 <= width <= 460 and 560 <= height <= 720:
                # Compatibility label; geometry alone is weak publisher evidence.
                name, kind, confidence, risk = "springer_lncs", "conference", 0.55, "high"
                evidence.append("compact geometry only; venue unverified")
    if not any(boxes for _, _, boxes in pages):
        name, kind, confidence, risk = "unknown", "generic", 0.0, "high"
        evidence.append("no substantial extractable source lines")
    return dict(
        name=name, confidence=confidence, columns=columns,
        page_width=float(page.rect.width), page_height=float(page.rect.height),
        caption_position=_caption_position(boxes), risk=risk, kind=kind,
        sampled_pages=sampled, evidence=tuple(evidence), registry_version=registry["version"],
        text_frame=_body_text_frame(content),
    )


def _body_text_frame(content: list) -> tuple[float, float]:
    frames = []
    for _, page, boxes in content:
        wide = [box for box in boxes if box[2] - box[0] >= page.rect.width * 0.5]
        if len(wide) >= 3:
            frames.append((statistics.median(b[0] for b in wide),
                           statistics.median(b[2] for b in wide)))
    if not frames:
        return ()
    return (statistics.median(f[0] for f in frames), statistics.median(f[1] for f in frames))


@dataclass(frozen=True)
class PageLayout:
    page_index: int
    columns: int
    role: str


def _source_column_count(boxes: list, width: float) -> int:
    from .layout_profiles import _detect_column_count

    geometry = load_layout_registry()["geometry"]
    lines = [box for box in boxes if box[2] - box[0] >= width * geometry["min_line_width_ratio"]]
    wide = [box for box in lines if box[2] - box[0] >= width * geometry["wide_line_ratio"]]
    total = sum((box[2] - box[0]) ** 2 for box in lines)
    # Full-width prose outweighs narrow, indented code/table cells. In a true
    # two-column paper the full-width title contributes only a few lines.
    wide_weight = sum((box[2] - box[0]) ** 2 for box in wide)
    if (len(wide) >= geometry["wide_line_min"]
            and wide_weight >= total * geometry["wide_weight_ratio"]):
        return 1
    return _detect_column_count(boxes, width)


def page_layout(page: object, page_index: int, *, kind: str = "generic") -> PageLayout:
    from .layout_profiles import _text_blocks

    text = page.get_text("text")
    role = "body"
    if not text.strip():
        role = "scanned" if page.get_images() else "blank"
    elif kind == "book":
        if re.search(r"(?im)^\s*(?:table of )?contents\s*$", text) and len(
            re.findall(r"(?:\.\s*){4,}", text)
        ) >= 2:
            role = "contents"
        elif re.search(r"(?im)^\s*chapter\s+(?:\d+|[IVX]+)\s*$", text[:500]):
            role = "chapter"
        elif page_index <= 2 and len(text) < 600:
            role = "front_matter"
    return PageLayout(page_index, _source_column_count(_text_blocks(page), page.rect.width), role)


def split_book_contents_blocks(page: object, raw_blocks: list[dict]) -> list[dict]:
    """Keep each contents baseline and its source page number as one owner."""
    if page_layout(page, 0, kind="book").role != "contents":
        return raw_blocks
    lines = [line for raw in raw_blocks if raw.get("type") == 0 for line in raw["lines"]]
    groups: list[list[dict]] = []
    for line in sorted(lines, key=lambda line: (line["bbox"][1], line["bbox"][0])):
        baseline = line["spans"][0].get("origin", (0, line["bbox"][3]))[1]
        previous = groups[-1][0] if groups else None
        previous_y = (
            previous["spans"][0].get("origin", (0, previous["bbox"][3]))[1] if previous else 0
        )
        if previous is None or abs(baseline - previous_y) > 2:
            groups.append([])
        groups[-1].append(line)
    result = [raw for raw in raw_blocks if raw.get("type") != 0]
    for group in groups:
        page_numbers = [line for line in group if line["bbox"][0] > page.rect.width * 0.75
                        and re.fullmatch(r"[0-9ivxlcdm]+", "".join(
                            s["text"] for s in line["spans"]
                        ).strip(), re.I)]
        for line in page_numbers:
            result.append(dict(type=0, lines=[line], bbox=line["bbox"]))
        group = [line for line in group if line not in page_numbers]
        if not group:
            continue
        boxes = [line["bbox"] for line in group]
        result.append(dict(type=0, lines=group, bbox=(
            min(b[0] for b in boxes), min(b[1] for b in boxes),
            max(b[2] for b in boxes), max(b[3] for b in boxes),
        )))
    return result


def _monospaced_line(line: dict) -> bool:
    spans = [s for s in line["spans"] if s.get("text", "").strip()]
    mono = [s for s in spans if re.search(
        r"courier|\bcour\b|mono|cmtt|lmtt|sftt|inconsolata", s.get("font", ""), re.I,
    )]
    return bool(spans) and (
        sum(len(s["text"]) for s in mono) >= sum(len(s["text"]) for s in spans) * 0.9
    )


def split_book_code_blocks(raw_blocks: list[dict]) -> list[dict]:
    """Split code and prose inside the same PDF object by source font ownership."""
    result = []
    for raw in raw_blocks:
        if raw.get("type") != 0:
            result.append(raw)
            continue
        groups: list[list[dict]] = []
        previous = None
        for line in raw["lines"]:
            code = _monospaced_line(line)
            if code != previous:
                groups.append([])
            groups[-1].append(line)
            previous = code
        for group in groups:
            boxes = [line["bbox"] for line in group]
            result.append({**raw, "lines": group, "bbox": (
                min(b[0] for b in boxes), min(b[1] for b in boxes),
                max(b[2] for b in boxes), max(b[3] for b in boxes),
            )})
    return result


def _code_regions(page: object) -> list[tuple]:
    return [
        raw["bbox"] for raw in split_book_code_blocks(page.get_text("dict").get("blocks", []))
        if raw.get("type") == 0 and raw["lines"] and _monospaced_line(raw["lines"][0])
    ]


def merge_book_records_around_code(records: list, page: object, merge_prose: object) -> list:
    """Prevent formula-continuation merging from consuming code examples."""
    code = _code_regions(page)
    result = []
    prose = []
    for record in records:
        box = record.bbox
        if any(r[0] - 1 <= box[0] and r[1] - 1 <= box[1]
               and r[2] + 1 >= box[2] and r[3] + 1 >= box[3] for r in code):
            result.extend(merge_prose(prose))
            prose = []
            result.append(record)
        else:
            prose.append(record)
    result.extend(merge_prose(prose))
    return result


def apply_family_block_roles(blocks: list, page: object, page_index: int, profile: object) -> None:
    """Use source-owned book structures; all other families keep existing roles."""
    policy = family_policy(profile.name)
    if policy.get("anchor_article_structure"):
        apply_article_block_roles(blocks, page)
        return
    if getattr(profile, "kind", "generic") == "conference" and page_index == 0:
        apply_conference_byline_roles(blocks, page)
        return
    if getattr(profile, "kind", "generic") != "book":
        return
    layout = page_layout(page, page_index, kind="book")
    code = _code_regions(page) if policy.get("preserve_code") else []
    font_sizes = [b.font_size for b in blocks if b.font_size > 0]
    body_size = statistics.median(font_sizes) if font_sizes else 10.0
    for block in blocks:
        x0, y0, x1, y1 = block.bbox
        owned_code = any(
            region[0] - 1 <= x0 and region[1] - 1 <= y0
            and region[2] + 1 >= x1 and region[3] + 1 >= y1
            for region in code
        )
        if owned_code:
            block.block_type = "algorithm"
            block.should_translate = False
            block.no_merge = block.nowrap = block.preserve_position = True
        elif layout.role == "contents" and policy.get("anchor_contents"):
            block.no_merge = block.nowrap = block.preserve_position = True
            if x0 > page.rect.width * 0.75 and re.fullmatch(
                r"[0-9ivxlcdm]+", block.text.strip(), re.I,
            ):
                block.block_type = "metadata"
                block.should_translate = False
            elif block.font_size >= 16:
                block.block_type = "heading"
                block.fixed_translation_font_size = block.font_size
            else:
                # Section entries are not headings. Keep their original
                # weight/size instead of alternating generic heading guesses.
                block.block_type = "body"
                block.fixed_translation_font_size = block.font_size
                block.bold = _source_bold_weight(page, block.bbox)
                block.starts_bold = block.bold
                block.bold_prefix = False
                block.translated_bold_prefix_chars = 0
        elif (
            policy.get("source_chapter_fonts")
            and block.block_type not in {
                "metadata", "equation", "algorithm", "table", "bibliography",
            }
            and block.source_lines <= 4
            and ((layout.role == "chapter" and y0 < page.rect.height * 0.36)
                 or layout.role == "front_matter")
            and block.font_size >= 16
            and (layout.role == "front_matter" or block.font_size >= body_size * 1.25)
        ):
            block.block_type = "heading" if layout.role == "chapter" else "title"
            block.should_translate = True
            block.no_merge = True
            block.preserve_position = True
            block.fixed_translation_font_size = block.font_size


def bound_book_front_matter(blocks: list, page: object, page_index: int, profile: object) -> None:
    """Give translated display titles the source body frame's empty width."""
    if (getattr(profile, "kind", "generic") != "book" or not profile.text_frame
            or page_layout(page, page_index, kind="book").role != "front_matter"):
        return
    for block in blocks:
        if block.block_type != "title" or block.font_size < 16:
            continue
        x0, y0, x1, y1 = block.bbox
        left, right = profile.text_frame
        left, right = max(0.0, min(left, x0)), min(page.rect.width, max(right, x1))
        neighbors = [b.bbox for b in blocks if b is not block]
        neighbors.extend(tuple(info["bbox"]) for info in page.get_image_info())
        if any(b[1] < y1 and b[3] > y0 and b[0] < right and b[2] > left for b in neighbors):
            continue
        block.redact_bboxes = block.redact_bboxes or [block.bbox]
        block.bbox = (left, y0, right, y1)
        block.preserve_position = False


def _source_bold_weight(page: object, bbox: tuple) -> bool:
    spans = []
    for raw in page.get_text("dict").get("blocks", ()):
        for line in raw.get("lines", ()):
            for span in line["spans"]:
                box = span["bbox"]
                if (bbox[0] - 1 <= box[0] and bbox[1] - 1 <= box[1]
                        and bbox[2] + 1 >= box[2] and bbox[3] + 1 >= box[3]):
                    spans.append(span)
    total = sum(len(s["text"].strip()) for s in spans)
    bold = sum(len(s["text"].strip()) for s in spans if s.get("flags", 0) & 16)
    return total > 0 and bold >= total * 0.5


_ARTICLE_CONTACT_RE = re.compile(
    r"orcid|[\w.+-]+@[\w.-]+|https?://|www\.|"
    r"preprint submitted to|corresponding author|principal corresponding author", re.I,
)
_ARTICLE_SECTION_RE = re.compile(r"^\d+(?:\.\d+)*\.?\s+[A-Z][A-Za-z -]{2,80}$")


def _article_body_size(page: object) -> float:
    sizes = [round(float(s["size"]), 1)
             for raw in page.get_text("dict").get("blocks", ())
             for line in raw.get("lines", ()) for s in line["spans"]
             if len(s["text"].strip()) >= 30 and s["bbox"][1] < page.rect.height * 0.65]
    return statistics.median(sizes) if sizes else 10.0


def split_article_text_blocks(page: object, raw_blocks: list[dict]) -> list[dict]:
    """Split source font/role transitions before formula and paragraph merging."""
    body_size = _article_body_size(page)
    result = []
    for raw in raw_blocks:
        if raw.get("type") != 0:
            result.append(raw)
            continue
        groups: list[list[dict]] = []
        previous = None
        for source_line in raw["lines"]:
            line = _article_text_line(source_line)
            value = "".join(s["text"] for s in line["spans"]).strip()
            size = max((s["size"] for s in line["spans"]), default=body_size)
            footnote = size < body_size * 0.91 and line["bbox"][1] > page.rect.height * 0.5
            contact = footnote and _ARTICLE_CONTACT_RE.search(value)
            section = _ARTICLE_SECTION_RE.fullmatch(value)
            key = ("contact", len(groups)) if contact else (
                "section", len(groups)
            ) if section else ("footnote" if footnote else "body")
            if key != previous:
                groups.append([])
            groups[-1].append(line)
            previous = key
        for group in groups:
            boxes = [line["bbox"] for line in group]
            result.append({**raw, "lines": group, "bbox": (
                min(b[0] for b in boxes), min(b[1] for b in boxes),
                max(b[2] for b in boxes), max(b[3] for b in boxes),
            )})
    return result


def _article_text_line(line: dict) -> dict:
    """Use STIX text baselines when embedded metrics exaggerate blank ascent/descent."""
    spans = []
    for span in line["spans"]:
        bbox = span["bbox"]
        size = float(span.get("size", 0))
        origin = span.get("origin")
        if (size > 0 and origin and re.search(r"STIXGeneral", span.get("font", ""), re.I)
                and bbox[3] - bbox[1] > size * 1.7):
            bbox = (bbox[0], max(bbox[1], origin[1] - size * 0.85), bbox[2],
                    min(bbox[3], origin[1] + size * 0.25))
        spans.append({**span, "bbox": bbox})
    boxes = [s["bbox"] for s in spans]
    if not boxes:
        return line
    return {**line, "spans": spans, "bbox": (
        min(b[0] for b in boxes), min(b[1] for b in boxes),
        max(b[2] for b in boxes), max(b[3] for b in boxes),
    )}


def apply_article_block_roles(blocks: list, page: object) -> None:
    body_size = _article_body_size(page)
    code = _code_regions(page)
    bylines = _article_byline_regions(page)
    for block in blocks:
        x0, y0, x1, y1 = block.bbox
        if any(r[0] - 1 <= x0 and r[1] - 1 <= y0
               and r[2] + 1 >= x1 and r[3] + 1 >= y1 for r in code):
            block.block_type = "algorithm"
            block.should_translate = False
            block.no_merge = block.nowrap = block.preserve_position = True
            continue
        plain = block.text.replace("\ue000", "").replace("\ue001", "").strip()
        display_title = (
            y0 < page.rect.height * 0.14
            and block.font_size >= body_size * 1.4
            and len(re.findall(r"[A-Za-z]{3,}", plain)) >= 3
        )
        footnote = block.font_size < body_size * 0.91 and y0 > page.rect.height * 0.5
        contact = footnote and _ARTICLE_CONTACT_RE.search(plain)
        publisher = "preprint submitted to" in plain.lower() and y0 > page.rect.height * 0.85
        byline = any(r[0] - 1 <= x0 and r[1] - 1 <= y0
                     and r[2] + 1 >= x1 and r[3] + 1 >= y1 for r in bylines)
        if contact or publisher or byline or plain.rstrip(":") == "Keywords":
            block.block_type = "metadata"
            block.should_translate = False
            block.no_merge = block.nowrap = block.preserve_position = True
        elif footnote and block.block_type not in {
            "equation", "algorithm", "table", "footer", "header", "metadata", "bibliography",
        }:
            block.block_type = "footnote"
            block.should_translate = True
            block.no_merge = block.preserve_position = True
            block.fixed_translation_font_size = block.font_size
        elif display_title or _ARTICLE_SECTION_RE.fullmatch(plain):
            block.block_type = "heading"
            block.should_translate = True
            block.no_merge = True
            block.fixed_translation_font_size = block.font_size


def _article_byline_regions(page: object) -> list[tuple]:
    regions = []
    for raw in page.get_text("dict").get("blocks", ()):
        if raw.get("type") != 0 or raw["bbox"][1] > page.rect.height * 0.22:
            continue
        value = " ".join(s["text"] for line in raw["lines"] for s in line["spans"])
        initials = len(re.findall(r"[A-Z]\.\s*[A-Z]\.", value))
        scripts = sum(bool(s.get("flags", 0) & 1) for line in raw["lines"] for s in line["spans"])
        if initials >= 2 or (initials >= 1 and scripts >= 2):
            regions.append(raw["bbox"])
    return regions


def apply_conference_byline_roles(blocks: list, page: object) -> None:
    raw = [b for b in page.get_text("dict").get("blocks", ()) if b.get("type") == 0]
    abstracts = [line["bbox"][1] for b in raw for line in b["lines"]
                 if "".join(s["text"] for s in line["spans"]).strip().lower() == "abstract"]
    if not abstracts:
        return
    regions = [b["bbox"] for b in raw if b["bbox"][3] < min(abstracts)
               and len(b["lines"]) <= 6 and re.search(
                   r"[\w.+-]+@[\w.-]+", " ".join(
                       s["text"] for line in b["lines"] for s in line["spans"]
                   ),
               )]
    for block in blocks:
        x0, y0, x1, y1 = block.bbox
        if any(r[0] - 1 <= x0 and r[1] - 1 <= y0
               and r[2] + 1 >= x1 and r[3] + 1 >= y1 for r in regions):
            block.block_type = "metadata"
            block.should_translate = False
            block.no_merge = block.nowrap = block.preserve_position = True


def restore_article_metadata(page: object, source_page: object) -> int:
    """Keep native metadata appearance and one accurate, selectable text owner."""
    from types import SimpleNamespace

    import fitz

    from .pdf_layout import (
        _insert_source_region_raster,
        _restore_removed_page_links,
        is_injection_text,
    )

    body_size = _article_body_size(source_page)
    bylines = _article_byline_regions(source_page)
    lines = []
    for raw in source_page.get_text("rawdict").get("blocks", ()):
        raw_value = " ".join(c["c"] for line in raw.get("lines", ())
                             for s in line["spans"] for c in s["chars"])
        keywords = bool(re.match(r"^\s*Keywords\s*:", raw_value))
        for line in raw.get("lines", ()):
            value = "".join(c["c"] for s in line["spans"] for c in s["chars"]).strip()
            if is_injection_text(value):
                continue
            x0, y0, x1, y1 = line["bbox"]
            size = max((s["size"] for s in line["spans"]), default=body_size)
            contact = (size < body_size * 0.91 and y0 > source_page.rect.height * 0.5
                       and _ARTICLE_CONTACT_RE.search(value))
            byline = any(r[0] - 1 <= x0 and r[1] - 1 <= y0
                         and r[2] + 1 >= x1 and r[3] + 1 >= y1 for r in bylines)
            if contact or byline or keywords:
                lines.append(line)
    missing = []
    for line in lines:
        expected = "".join(c["c"] for s in line["spans"] for c in s["chars"])
        current = page.get_text("text", clip=fitz.Rect(line["bbox"]) + (-1, -1, 1, 1), sort=True)
        expected = " ".join(expected.split())
        if expected not in " ".join(current.split()):
            missing.extend(c for s in line["spans"] for c in s["chars"] if c["c"].strip())
    if not missing:
        return 0
    # Clear surviving fragments together before writing complete source words.
    for line in lines:
        page.add_redact_annot(fitz.Rect(line["bbox"]), fill=False)
    page.apply_redactions(images=fitz.PDF_REDACT_IMAGE_NONE, graphics=fitz.PDF_REDACT_LINE_ART_NONE)
    deleted_links = [link for link in source_page.get_links()
                     if any(fitz.Rect(link["from"]).intersects(fitz.Rect(line["bbox"]))
                            for line in lines)]
    # get_links keeps stale handles until reload_page, which would invalidate
    # the caller's page. Every link intersecting these redactions was deleted;
    # restore just that set without trusting the stale handles.
    _restore_removed_page_links(
        SimpleNamespace(get_links=lambda: [], insert_link=page.insert_link), deleted_links,
    )
    semantic_font = fitz.Font("cjk")
    for line in lines:
        source_spans = [{**s, "text": "".join(c["c"] for c in s["chars"])} for s in line["spans"]]
        clip = fitz.Rect(_article_text_line({**line, "spans": source_spans})["bbox"])
        # Do not regenerate visible glyphs from a subset font's unreliable
        # Unicode cmap. The original painted source is the visual authority.
        _insert_source_region_raster(page, source_page.parent, source_page.number, clip, clip)
        for span in line["spans"]:
            writer = fitz.TextWriter(page.rect)
            text = "".join(c["c"] for c in span["chars"])
            if not text:
                continue
            writer.append(span["origin"], text, font=semantic_font, fontsize=span["size"])
            advance = semantic_font.text_length(text, fontsize=span["size"])
            width = span["bbox"][2] - span["bbox"][0]
            scale_x = width / advance if advance > 0 and width > 0 else 1.0
            color = span.get("color", 0)
            writer.write_text(page, render_mode=3,
                              morph=(fitz.Point(span["origin"]), fitz.Matrix(scale_x, 1)), color=(
                (color >> 16 & 255) / 255, (color >> 8 & 255) / 255, (color & 255) / 255,
            ))
    return len(missing)


def book_definition_record(record: object, page: object) -> bool:
    """Do not interpret a textbook's ordinary input/output definition as pseudocode."""
    value = " ".join(line.text for line in record.lines)
    if not re.match(r"(?i)^\s*(?:input|output)\s*:", value):
        return False
    if ("\ue000" in value or not value.rstrip().endswith(".")
            or len(re.findall(r"[A-Za-z]{2,}", value)) < 8):
        return False
    if any(_monospaced_line({"spans": line.spans}) for line in record.lines):
        return False
    # An explicitly labelled algorithm remains an algorithm, even if its
    # inputs are described in a full natural-language sentence.
    if re.search(r"(?im)^\s*algorithm\s+\d+\b", page.get_text("text")):
        return False
    return True
