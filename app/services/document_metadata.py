"""Conservative PDF identity, title extraction and portable export names."""

from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from pathlib import Path

import fitz


def clean_title(value: str) -> str:
    return " ".join("".join(c if unicodedata.category(c) != "Cc" else " " for c in value).split())[
        :500
    ]


def title_key(value: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFKC", value).casefold() if c.isalnum())[:500]


def useful_metadata_title(value: str) -> bool:
    value = clean_title(value)
    return len(value) >= 5 and not re.search(
        r"^(untitled|document\d*|microsoft\s+(word|powerpoint)|latex|template|"
        r"els[- ]cas|arxiv:|https?://|[0-9a-f]{20,}|\d{4}\.\d{4,})(?:\b|$)|\.(pdf|docx?)$",
        value,
        re.I,
    )


def first_page_title(page) -> str | None:
    blocks = page.get_text("dict").get("blocks", [])
    height = page.rect.height if isinstance(page, fitz.Page) else 800
    candidates = []
    for block in blocks:
        if block.get("type") != 0:
            continue
        lines = []
        sizes = []
        for line in block.get("lines", []):
            direction = line.get("dir", (1, 0))
            if abs(direction[1]) > abs(direction[0]) * 0.5:
                # arXiv identifiers commonly run vertically in the margin.
                continue
            spans = [s for s in line.get("spans", []) if str(s.get("text", "")).strip()]
            if not spans:
                continue
            size = max(s.get("size", 0) for s in spans)
            text_value = clean_title("".join(s.get("text", "") for s in spans))
            if not useful_metadata_title(text_value) or re.match(
                r"^(doi\s*:|https?://doi\.)", text_value, re.I
            ):
                continue
            y = line.get("bbox", block.get("bbox", [0, 0, 0, 0]))[1]
            if size > 14 and y <= height * 0.55 and len(text_value) > 5:
                if re.match(
                    r"^(abstract|contents|references|article|research article)$", text_value, re.I
                ):
                    continue
                lines.append(text_value)
                sizes.append(size)
        if lines:
            y = block.get("bbox", [0, height * 0.15])[1]
            # Running publisher mastheads at the very top do not outrank a title.
            penalty = 0.6 if y < height * 0.065 else 1
            candidates.append(
                (max(sizes) * penalty, " ".join(lines), block.get("bbox"), max(sizes))
            )
    if not candidates:
        return None
    chosen = max(candidates, key=lambda candidate: candidate[0])
    title = chosen[1]
    bottom = chosen[2][3] if chosen[2] else 0
    # PDF producers may put consecutive title lines in separate text blocks.
    # Join only adjacent lines with the same font size, never smaller bylines.
    for candidate in sorted(candidates, key=lambda item: item[2][1] if item[2] else 0):
        bbox = candidate[2]
        if candidate is chosen or not bbox or not chosen[2]:
            continue
        if abs(candidate[3] - chosen[3]) < 0.75 and 0 <= bbox[1] - bottom <= chosen[3]:
            title += " " + candidate[1]
            bottom = bbox[3]
    return clean_title(title)


def pdf_fingerprints(path: Path) -> tuple[str, str]:
    """Ignore container metadata/compression; require all pages to look identical.

    Text, links, annotations and attachments participate as well. Changed layout,
    figures, page order, or content becomes a separate version, never a fuzzy merge.
    """
    binary = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            binary.update(chunk)
    file_sha = binary.hexdigest()
    content = hashlib.sha256(b"paper-content-v1\0")
    try:
        with fitz.open(path) as document:
            content.update(str(document.page_count).encode())
            for page in document:
                scale = min(1.0, 1600 / max(page.rect.width, page.rect.height, 1))
                pixmap = page.get_pixmap(matrix=fitz.Matrix(scale, scale), alpha=False)
                content.update(json.dumps([pixmap.width, pixmap.height, list(page.rect)]).encode())
                content.update(pixmap.samples)
                content.update(page.get_text().encode("utf-8"))
                drawings = [
                    {k: v for k, v in drawing.items() if k not in {"seqno", "layer"}}
                    for drawing in page.get_drawings()
                ]
                content.update(json.dumps(drawings, sort_keys=True, default=str).encode())
                for image in page.get_images(full=True):
                    # Embedded image identity prevents low-resolution rendering from
                    # hiding a small changed figure or fine scientific image detail.
                    content.update(document.extract_image(image[0])["image"])
                links = [
                    {k: v for k, v in link.items() if k not in {"xref", "id"}}
                    for link in page.get_links()
                ]
                content.update(json.dumps(links, sort_keys=True, default=list).encode())
                annotations = [(a.type, a.info) for a in (page.annots() or [])]
                content.update(json.dumps(annotations, sort_keys=True, default=str).encode())
            for name in sorted(document.embfile_names()):
                content.update(name.encode())
                content.update(document.embfile_get(name))
        return file_sha, content.hexdigest()
    except (RuntimeError, ValueError):
        # A damaged/encrypted PDF is deduplicated only by its exact original bytes.
        return file_sha, hashlib.sha256(("exact-bytes:" + file_sha).encode()).hexdigest()


def export_filename(paper, variant: str) -> str:
    label = {"original": "原文", "translated": "中文", "dual": "双语"}[variant]
    name = re.sub(r'[<>:"/\\|?*\x00-\x1f]', " ", clean_title(paper.title)).strip(" .")
    name = name or "论文"
    while len(name.encode("utf-8")) > 160:
        name = name[:-1]
    stamp = paper.created_at.strftime("%Y%m%d-%H%M%S") if paper.created_at else "未注明时间"
    return f"{name.strip()}--{stamp}--{paper.id}--{label}.pdf"
