"""Source-derived Nature typography and disclosure-form geometry.

Rules use publisher identity, source font transitions and actual checkbox paths;
they do not depend on an article title, DOI suffix or fixed page coordinates.
"""

from __future__ import annotations

import re
import statistics
from dataclasses import replace


def is_nature_document(document: object) -> bool:
    if not getattr(document, "page_count", 0):
        return False
    text = document[0].get_text("text").lower()
    return bool(
        "10.1038/" in text and re.search(r"(?:^|\n)nature(?: [a-z]+){0,4}(?:\n|\s*\|)", text)
    )


def is_reporting_summary(page: object) -> bool:
    """Recognize the disclosure form, rather than an article's link to it."""
    text = " ".join(page.get_text("text").lower().split())
    if "reporting summary" not in text or not re.search(
        r"\bnature (?:portfolio|research)\b", text
    ):
        return False
    form_introduction = (
        "this form provides structure for consistency and transparency" in text
    )
    for raw in page.get_text("dict").get("blocks", ()):
        if raw.get("type") != 0:
            continue
        for line in raw.get("lines", ()):
            spans = line.get("spans", ())
            value = " ".join(
                " ".join(span.get("text", "") for span in spans).lower().split()
            )
            bbox = line.get("bbox", ())
            if len(bbox) != 4:
                continue
            direction = line.get("dir", (1.0, 0.0))
            rotated = abs(direction[1]) > abs(direction[0])
            if re.fullmatch(
                r"nature (?:portfolio|research)\s*\|\s*reporting summary", value
            ):
                # Continuation sheets carry the complete masthead down their
                # outer sidebar; other sheets put it in their page header.
                if rotated and (
                    bbox[0] >= page.rect.width * 0.85
                    or bbox[2] <= page.rect.width * 0.15
                ):
                    return True
                if not rotated and bbox[1] < page.rect.height * 0.15:
                    return True
            if (
                value == "reporting summary"
                and not rotated
                and bbox[1] < page.rect.height * 0.25
                and max((span.get("size", 0.0) for span in spans), default=0.0) >= 16
                and form_introduction
            ):
                # The current flat publisher template omits the text sidebar.
                # Its display title and explicit form introduction remain.
                return True
    return False



def _checkbox_rows(page: object) -> list[float]:
    rows = set()
    for drawing in page.get_drawings():
        rect = drawing["rect"]
        if (
            rect.x1 < page.rect.width * 0.15
            and 8 <= rect.width <= 12
            and 8 <= rect.height <= 12
            and any(item[0] == "re" for item in drawing.get("items", ()))
        ):
            rows.add(round((rect.y0 + rect.y1) / 2.0, 1))
    return sorted(rows)


def split_nature_text_blocks(page: object, raw_blocks: list[dict]) -> list[dict]:
    """Separate mixed title/header records and independent form responses."""
    form = is_reporting_summary(page)
    form_rows = _checkbox_rows(page) if form else []
    result = []
    for raw in raw_blocks:
        lines = raw.get("lines", ())
        if raw.get("type") != 0 or not lines:
            result.append(raw)
            continue
        sizes = [
            statistics.median(
                float(span["size"]) for span in line["spans"] if span.get("text", "").strip()
            )
            if any(span.get("text", "").strip() for span in line["spans"])
            else 0.0
            for line in lines
        ]
        large = max(sizes, default=0.0)
        mixed_title = large >= 16 and any(0 < size < large * 0.65 for size in sizes)
        doi_header = len(lines) <= 3 and any(
            "doi.org/" in span.get("text", "") for line in lines for span in line["spans"]
        )
        groups: list[list[dict]] = []
        previous_key = None
        for index, (line, size) in enumerate(zip(lines, sizes)):
            bbox = line["bbox"]
            row = None
            if form_rows and bbox[0] >= page.rect.width * 0.075:
                center = (bbox[1] + bbox[3]) / 2.0
                # Calibri's text box sits about 5.5pt above the checkbox
                # centre; include the first baseline as well as its wrap.
                eligible = [y for y in form_rows if y - 8 <= center]
                if eligible and center <= form_rows[-1] + 18:
                    row = max(eligible)
            if row is not None:
                key = ("checkbox", row)
            elif doi_header or (mixed_title and size < large * 0.65):
                key = ("header", index)
            elif mixed_title:
                key = ("title",)
            else:
                key = ("original",)
            parallel_field = False
            if form and groups and key == previous_key:
                previous = groups[-1][-1]["bbox"]
                overlap = min(previous[3], bbox[3]) - max(previous[1], bbox[1])
                gap = max(bbox[0] - previous[2], previous[0] - bbox[2], 0.0)
                parallel_field = overlap > 0 and gap > 8.0
            if key != previous_key or parallel_field:
                groups.append([])
            groups[-1].append(line)
            previous_key = key
        for group in groups:
            boxes = [line["bbox"] for line in group]
            result.append(
                {
                    **raw,
                    "lines": group,
                    "bbox": (
                        min(box[0] for box in boxes),
                        min(box[1] for box in boxes),
                        max(box[2] for box in boxes),
                        max(box[3] for box in boxes),
                    ),
                }
            )
    return result


def apply_nature_block_roles(blocks: list, page: object, page_index: int) -> None:
    """Keep identifying metadata intact and select readable CJK body sizes."""
    form = is_reporting_summary(page)
    byline_regions = []
    for raw in page.get_text("dict").get("blocks", ()):
        if raw.get("type") != 0:
            continue
        spans = [span for line in raw["lines"] for span in line["spans"]]
        text = " ".join(span.get("text", "") for span in spans)
        names = re.findall(r"\b[A-Z][a-z]+\s+[A-Z][A-Za-z-]+", text)
        if (
            page_index == 0
            and len(names) >= 6
            and len(re.findall(r"\b[a-z]{3,}\b", text)) <= max(3, len(names) // 3)
            and raw["bbox"][1] < page.rect.height * 0.48
            and max((span.get("size", 0) for span in spans), default=0) < 12
        ):
            byline_regions.append(raw["bbox"])
        if form and "Corresponding author(s):" in text:
            for line in raw["lines"]:
                value = " ".join(span.get("text", "") for span in line["spans"]).strip()
                if (
                    re.fullmatch(r"(?:[A-Z][\w-]+\s+){1,3}[A-Z][\w-]+", value)
                    and "author" not in value.lower()
                ):
                    byline_regions.append(line["bbox"])
    for block in blocks:
        # Printed journal URLs can contain a space after the scheme. Keep
        # their canonical address in the protected token and copied output.
        block.text = re.sub(r"(?i)(https?://)\s+(?=[A-Za-z0-9])", r"\1", block.text)
        plain = re.sub(r"⟦[^⟧]*⟧", " ", block.text)
        names = re.findall(r"\b[A-Z][a-z]+\s+[A-Z][A-Za-z-]+", plain)
        lower_words = re.findall(r"\b[a-z]{3,}\b", plain)
        owned_byline = any(
            region[0] - 1 <= block.bbox[0]
            and region[1] - 1 <= block.bbox[1]
            and region[2] + 1 >= block.bbox[2]
            and region[3] + 1 >= block.bbox[3]
            for region in byline_regions
        )
        byline = owned_byline or (
            page_index == 0
            and len(names) >= 6
            and len(lower_words) <= max(3, len(names) // 3)
            and block.bbox[1] < page.rect.height * 0.48
            and block.font_size < 12
        )
        affiliation = (
            page_index == 0
            and block.bbox[1] > page.rect.height * 0.85
            and block.font_size < 9
            and re.search(r"\b(?:University|Institute|Department)\b", plain)
        )
        publisher = (
            page_index == 0
            and block.bbox[1] < page.rect.height * 0.10
            and plain.strip().lower().startswith("nature ")
        )
        doi = "doi.org/" in block.text and len(plain.strip()) < 15
        footer = block.bbox[1] > page.rect.height * 0.94 and "Nature " in plain
        if byline or affiliation or publisher or doi or footer:
            block.block_type = "metadata"
            block.should_translate = False
            block.preserve_position = True
            block.no_merge = True
            continue
        if plain.strip() == "Article" and block.bbox[1] < page.rect.height * 0.2:
            block.block_type = "header"
            block.preserve_position = True
            block.nowrap = True
            block.no_merge = True
            block.fixed_translation_font_size = block.font_size
            continue
        if form:
            block.no_merge = True
            block.preserve_position = True
            if block.font_size >= 13 and len(plain.strip()) <= 120:
                block.block_type = "heading"
            block.fixed_translation_font_size = block.font_size
        elif block.block_type == "title":
            block.fixed_translation_font_size = block.font_size
        elif block.block_type == "heading":
            block.fixed_translation_font_size = block.font_size * 1.25
        elif (
            block.block_type in {"body", "formula_prose", "run_in_heading"}
            and block.should_translate
            and block.font_size < 13
        ):
            block.fixed_translation_font_size = block.font_size * 1.15
        elif block.block_type == "caption":
            block.fixed_translation_font_size = block.font_size * 1.15


def preserve_nature_comparison_labels(blocks: list, page: object) -> None:
    """Keep small row labels aligned with a source vector comparison matrix."""
    drawings = page.get_drawings()
    rows = {}
    for drawing in drawings:
        color, rect = drawing.get("fill"), drawing["rect"]
        if (color and max(color) - min(color) > 0.25
                and 2 <= rect.width <= 9 and 2 <= rect.height <= 9):
            rows.setdefault(round((rect.y0 + rect.y1) / 2), []).append(rect)
    groups = []
    for y, marks in sorted(rows.items()):
        if len({round(rect.x0) for rect in marks}) < 3:
            continue
        if not groups or y - groups[-1][-1][0] > 24:
            groups.append([])
        groups[-1].append((y, marks))
    for group in groups:
        if len(group) < 4:
            continue
        marks = [rect for _, row in group for rect in row]
        x0, x1 = min(rect.x0 for rect in marks), max(rect.x1 for rect in marks)
        y0, y1 = min(rect.y0 for rect in marks), max(rect.y1 for rect in marks)
        rules = [drawing["rect"] for drawing in drawings
                 if drawing["rect"].height < 1 and drawing["rect"].x0 < x0
                 and drawing["rect"].x1 >= x1 and 0 < y0 - drawing["rect"].y0 < 25]
        if not rules:
            continue
        rule = max(rules, key=lambda rect: rect.y0)
        for block in blocks:
            if (block.font_size <= 7 and block.bbox[0] >= rule.x0 - 2
                    and block.bbox[2] < x0 and block.bbox[1] >= y0 - 3
                    and block.bbox[3] <= y1 + 3):
                block.block_type = "metadata"
                block.should_translate = False
                block.no_merge = True
                block.preserve_position = True


def bound_reporting_summary_items(items: list, page: object, *, font_pack, margin: float) -> list:
    """Fit CJK in each form field without using a neighboring row or sidebar."""
    if not is_reporting_summary(page):
        return items
    right = page.rect.width - 16.0
    for drawing in page.get_drawings():
        rect = drawing["rect"]
        if (
            rect.x0 > page.rect.width * 0.85
            and rect.height > page.rect.height * 0.7
            and rect.width > 5
        ):
            right = min(right, rect.x0 - 10.0)
    from .pdf_layout import (
        _tokenize_translation_with_formula_clips,
        break_lines,
        line_block_height,
    )

    checkbox_boxes = [
        drawing["rect"]
        for drawing in page.get_drawings()
        if 8 <= drawing["rect"].width <= 12
        and 8 <= drawing["rect"].height <= 12
        and any(item[0] == "re" for item in drawing.get("items", ()))
    ]
    updated = []
    for block, text in items:
        x0, y0, x1, y1 = block.bbox
        field_right = right
        for other, _ in items:
            if other is block:
                continue
            if min(y1, other.bbox[3]) > max(y0, other.bbox[1]) and other.bbox[0] > x0:
                field_right = min(field_right, other.bbox[0] - 5.0)
        for box in checkbox_boxes:
            if min(y1, box.y1) > max(y0, box.y0) and box.x0 > x0:
                field_right = min(field_right, box.x0 - 5.0)
        x1 = min(right, max(x1, field_right))
        size = block.fixed_translation_font_size or block.font_size
        fonts = font_pack.fonts_for(block.bold)
        bold_fonts = font_pack.fonts_for(True)
        tokens = _tokenize_translation_with_formula_clips(text, block)
        lines = break_lines(
            tokens, fonts, size, max(1, x1 - x0 - margin * 2), bold_fonts=bold_fonts
        )
        desired = line_block_height(lines, size, 1.26, fonts, bold_fonts) + margin * 2
        original_y0 = min((box[1] for box in block.source_line_bboxes), default=y0)
        original_y1 = max((box[3] for box in block.source_line_bboxes), default=y1)
        top = max(0.0, min(y0, original_y0 - 2.0))
        bottom = page.rect.height - 20.0
        for other, _ in items:
            if other is block or other.bbox[1] <= original_y0 + 1.0:
                continue
            if min(x1, other.bbox[2]) > max(x0, other.bbox[0]):
                bottom = min(bottom, other.bbox[1] - 2.0)
        if block.block_type == "heading":
            # Grow above a heading's baseline, leaving its rule and the next
            # field anchored. Heading whitespace is not a form response row.
            bottom = min(bottom, max(y1, original_y1 + 1.0))
            top = max(0.0, min(top, bottom - desired))
        y1 = min(bottom, max(y1, top + desired))
        updated.append((replace(block, bbox=(x0, top, x1, y1)), text))
    _reflow_form_bullet_notes(updated, page, font_pack, margin)
    return updated


def _reflow_form_bullet_notes(items: list, page: object, font_pack, margin: float) -> None:
    """Use the free space before the next field for a policy note and its list."""
    from .pdf_layout import _tokenize_translation_with_formula_clips, break_lines, line_block_height

    order = sorted(range(len(items)), key=lambda i: items[i][0].bbox[1])
    processed = set()
    for position, index in enumerate(order):
        block, _ = items[index]
        if index in processed or not re.match(r"^\s*[-•]\s+", block.text):
            continue
        group = [index]
        for following in order[position + 1 :]:
            other = items[following][0]
            if not re.match(r"^\s*[-•]\s+", other.text) or abs(other.bbox[0] - block.bbox[0]) > 8:
                break
            group.append(following)
        if len(group) < 2:
            continue
        for previous in reversed(order[:position]):
            other = items[previous][0]
            first = items[group[0]][0]
            if (
                other.block_type not in {"body", "formula_prose"}
                or first.bbox[1] - other.bbox[3] > other.font_size * 1.6
                or not 0 <= block.bbox[0] - other.bbox[0] <= 20
            ):
                break
            group.insert(0, previous)
        top = min(items[i][0].bbox[1] for i in group)
        end = max(items[i][0].bbox[3] for i in group)
        bottom = min(
            (
                other.bbox[1] - 2
                for i, (other, _) in enumerate(items)
                if i not in group and other.bbox[1] >= end
            ),
            default=page.rect.height - 20,
        )
        heights = []
        for i in group:
            current, text = items[i]
            size = current.fixed_translation_font_size or current.font_size
            fonts, bold = font_pack.fonts_for(current.bold), font_pack.fonts_for(True)
            lines = break_lines(
                _tokenize_translation_with_formula_clips(text, current),
                fonts,
                size,
                current.bbox[2] - current.bbox[0] - margin * 2,
                bold_fonts=bold,
            )
            heights.append(line_block_height(lines, size, 1.26, fonts, bold) + margin * 2)
        gap = 0.3
        if sum(heights) + gap * (len(group) - 1) > bottom - top:
            continue
        for i, height in zip(group, heights):
            current, text = items[i]
            items[i] = (
                replace(current, bbox=(current.bbox[0], top, current.bbox[2], top + height)),
                text,
            )
            top += height + gap
            processed.add(i)


def restore_reporting_summary_field_rules(page: object, source_page: object) -> None:
    """Restore the faint publisher field outlines covered by redaction fill."""
    if not is_reporting_summary(source_page):
        return
    for drawing in source_page.get_drawings():
        color = drawing.get("color")
        if not color or min(color) < 0.65 or max(color) - min(color) > 0.06:
            continue
        if drawing.get("type") not in {"s", "fs"}:
            continue
        shape = page.new_shape()
        for item in drawing.get("items", ()):
            if item[0] == "l":
                shape.draw_line(item[1], item[2])
            elif item[0] == "c":
                shape.draw_bezier(item[1], item[2], item[3], item[4])
            elif item[0] == "re":
                shape.draw_rect(item[1])
            elif item[0] == "qu":
                shape.draw_quad(item[1])
        shape.finish(
            color=color,
            width=drawing.get("width") or 0.5,
            closePath=drawing.get("closePath", False),
            dashes=drawing.get("dashes"),
            stroke_opacity=drawing.get("stroke_opacity", 1.0),
        )
        shape.commit()


def restore_reporting_summary_metadata(page: object, source_page: object) -> None:
    """Restore a lost author field as selectable source text, with source style."""
    if not is_reporting_summary(source_page):
        return
    import fitz

    current = " ".join(page.get_text("text").split())
    lines = [
        line
        for raw in source_page.get_text("dict").get("blocks", ())
        if raw.get("type") == 0
        for line in raw["lines"]
    ]
    labels = [span["bbox"] for line in lines for span in line["spans"]
              if "Corresponding author(s):" in span.get("text", "")]
    candidates = []
    for line in lines:
        if any("Corresponding author(s):" in span.get("text", "") for span in line["spans"]):
            candidates.extend({"bbox": span["bbox"], "spans": [span]}
                              for span in line["spans"])
        else:
            candidates.append(line)
    for line in candidates:
        name = " ".join(span.get("text", "") for span in line["spans"]).strip()
        if not re.fullmatch(r"(?:[A-Z][\w-]+\s+){1,3}[A-Z][\w-]+", name) or name in current:
            continue
        if not any(
            line["bbox"][0] >= label[2] - 2
            and min(line["bbox"][3], label[3]) > max(line["bbox"][1], label[1])
            for label in labels
        ):
            continue
        for span in line["spans"]:
            text = span.get("text", "")
            font = None
            for entry in source_page.get_fonts(full=True):
                if entry[3].split("+")[-1] != span["font"]:
                    continue
                buffer = source_page.parent.extract_font(entry[0])[3]
                if buffer:
                    candidate = fitz.Font(fontbuffer=buffer)
                    if all(candidate.has_glyph(ord(char)) for char in text):
                        font = candidate
                        break
            if font is None:
                font = fitz.Font("helv")
            size = span["size"]
            width = span["bbox"][2] - span["bbox"][0]
            advance = font.text_length(text, fontsize=size)
            if advance > width:
                size *= width / advance
            writer = fitz.TextWriter(page.rect)
            writer.append(span["origin"], text, font=font, fontsize=size)
            color = span.get("color", 0)
            writer.write_text(
                page,
                color=((color >> 16 & 255) / 255, (color >> 8 & 255) / 255, (color & 255) / 255),
            )
