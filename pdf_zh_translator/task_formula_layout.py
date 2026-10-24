"""Keep task success conditions native while translating their bold labels."""

import re


def join_task_condition_records(raw_blocks):
    """Join a native success-assignment label to its adjacent condition row."""
    result = []
    index = 0
    while index < len(raw_blocks):
        block = raw_blocks[index]
        text = " ".join(
            "".join(s.get("text", "") for s in line.get("spans", []))
            for line in block.get("lines", [])
        )
        if index + 1 < len(raw_blocks) and re.match(r"^\s*•\s*[^:=]{2,90}:\s*success\s*=", text):
            following = raw_blocks[index + 1]
            tail = " ".join(
                "".join(s.get("text", "") for s in line.get("spans", []))
                for line in following.get("lines", [])
            )
            first_box, next_box = (
                block.get("bbox", (0, 0, 0, 0)),
                following.get("bbox", (0, 0, 0, 0)),
            )
            if (
                following.get("type") == 0
                and "condition(" not in text.replace(" ", "")
                and re.search(r"\bcondition\s*\(", tail)
                and "&&" in tail
                and -3 <= next_box[1] - first_box[3] <= 12
                and 0 <= next_box[0] - first_box[0] <= 24
            ):
                result.append(
                    {
                        **block,
                        "lines": [*block.get("lines", []), *following.get("lines", [])],
                        "bbox": (
                            min(first_box[0], next_box[0]),
                            first_box[1],
                            max(first_box[2], next_box[2]),
                            next_box[3],
                        ),
                    }
                )
                index += 2
                continue
        result.append(block)
        index += 1
    return result


def task_condition_blocks(raw_block, page_index):
    """Return independently owned label and formula regions for one task.

    Require a native bullet, bold task label, explicit success assignment and
    a conditional expression. Ordinary bullet paragraphs keep their current
    translation path.
    """
    from . import pdf_layout as m

    lines = raw_block.get("lines", [])
    if not lines:
        return None
    first_text = "".join(span.get("text", "") for span in lines[0].get("spans", []))
    match = re.match(r"^\s*•\s*(?P<label>[^:=]{2,90}):\s*success\s*=", first_text)
    all_text = " ".join("".join(s.get("text", "") for s in row.get("spans", [])) for row in lines)
    if match is None or not re.search(r"(?:&&|\bcondition\s*\()", all_text):
        return None
    label = match.group("label").strip()
    spans = lines[0].get("spans", [])
    label_spans = [
        span
        for span in spans
        if m._BOLD_FONT_RE.search(span.get("font", "")) or int(span.get("flags", 0)) & m.FLAG_BOLD
    ]
    if not label_spans or "".join(s.get("text", "") for s in label_spans).strip() != label:
        return None
    boxes = [tuple(span["bbox"]) for span in label_spans]
    bbox = m.union_bbox(boxes)
    size = m.median_or_default([float(span["size"]) for span in label_spans], 10.0)
    text_owner = m.TextBlock(
        page_index,
        bbox,
        label,
        size,
        m.int_to_rgb(label_spans[0].get("color", 0)),
        bold=True,
        starts_bold=True,
        source_lines=1,
        block_type="table_prose",
        should_translate=True,
        preserve_position=True,
        nowrap=True,
        no_merge=True,
        # Native text redaction removes a whole glyph whose bbox intersects
        # the band. The middle of a proven single-line label is sufficient;
        # full font metrics can overlap the following condition's bbox.
        redact_bboxes=[
            (x0, y0 + (y1 - y0) * 0.25, x1, y1 - (y1 - y0) * 0.25) for x0, y0, x1, y1 in boxes
        ],
        source_line_bboxes=(bbox,),
        source_prose_bboxes=tuple(boxes),
    )
    result = [text_owner]
    for row in lines:
        # Keep the bullet and the inline assignment as separate owners so
        # their hull does not cover the translatable task label between them.
        group = []

        def flush():
            if group:
                owned = m.union_bbox([tuple(s["bbox"]) for s in group])
                equation = m.TextBlock(
                    page_index,
                    owned,
                    "".join(s.get("text", "") for s in group),
                    size,
                    (0, 0, 0),
                    block_type="equation",
                    should_translate=False,
                    preserve_position=True,
                    no_merge=True,
                )
                equation._equation_zone = True
                result.append(equation)
                group.clear()

        for span in row.get("spans", []):
            if any(span is label_span for label_span in label_spans):
                flush()
            elif span.get("text", "").strip():
                group.append(span)
        flush()
    text_owner.keepout_bboxes = [block.bbox for block in result[1:]]
    return result
