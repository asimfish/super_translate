"""Source ownership and shared typography for Markdown prompt appendices."""

import re
from dataclasses import replace
from statistics import median

_MONO = re.compile(r"SFTT|CMTT|NimbusMon|Courier|Mono|Typewriter|Consolas|Menlo|Inconsolata", re.I)
_FENCE = re.compile(r"^(`{3,}|~{3,})([\w+-]*)\s*$")


def paired_markdown_openings(document):
    """Only paired fences may claim source ownership across page boundaries."""
    valid = {}
    pending = None
    for page_index in range(document.page_count):
        for block in document[page_index].get_text("dict").get("blocks", []):
            for line in block.get("lines", []):
                spans = line.get("spans", [])
                marker = _FENCE.fullmatch("".join(s.get("text", "") for s in spans).strip())
                if not marker or not any(_MONO.search(s.get("font", "")) for s in spans):
                    continue
                if pending is None:
                    pending = (page_index, tuple(line["bbox"]), marker[1])
                elif (
                    marker[1][0] == pending[2][0]
                    and len(marker[1]) >= len(pending[2])
                    and not marker[2]
                ):
                    valid.setdefault(pending[0], set()).add(pending[1])
                    pending = None
    return valid


def split_fenced_code_blocks(raw_blocks, fence=None, *, valid_openings=None):
    """Separate prose from native code, carrying open fences across pages."""
    prose, code_boxes, code_run = [], [], []

    def finish_code_run():
        if code_run:
            code_boxes.append(
                (
                    min(b[0] for b in code_run),
                    min(b[1] for b in code_run),
                    max(b[2] for b in code_run),
                    max(b[3] for b in code_run),
                )
            )
            code_run.clear()

    for block in raw_blocks:
        if block.get("type") != 0:
            prose.append(block)
            continue
        group = []

        def flush():
            if group:
                boxes = [line["bbox"] for line in group]
                prose.append(
                    {
                        **block,
                        "lines": list(group),
                        "bbox": (
                            min(b[0] for b in boxes),
                            min(b[1] for b in boxes),
                            max(b[2] for b in boxes),
                            max(b[3] for b in boxes),
                        ),
                    }
                )
                group.clear()

        for line in block.get("lines", []):
            text = "".join(s.get("text", "") for s in line.get("spans", [])).strip()
            marker = _FENCE.fullmatch(text)
            monospaced = any(_MONO.search(s.get("font", "")) for s in line.get("spans", []))
            closing = bool(
                fence
                and marker
                and marker[1][0] == fence[0]
                and len(marker[1]) >= len(fence)
                and not marker[2]
            )
            opening = bool(
                fence is None
                and marker
                and monospaced
                and (valid_openings is None or tuple(line["bbox"]) in valid_openings)
            )
            if fence or opening:
                flush()
                code_run.append(tuple(line["bbox"]))
                if closing:
                    fence = None
                    finish_code_run()
                elif opening:
                    fence = marker[1]
            else:
                group.append(line)
        flush()
    finish_code_run()
    return prose, code_boxes, fence


def reflow_dense_prompt_items(items, source_page, font_pack, margin=0.8):
    """Lay out nested prompt instructions together above the next source owner.

    Scope is a dense list in a small monospaced source frame. Normal article
    paragraphs and code remain on their existing paths. Reuse measured line
    metrics and reserve room for every item before changing any rectangles.
    """
    from . import pdf_layout as m

    rows = []
    for raw in source_page.get_text("dict")["blocks"]:
        for line in raw.get("lines", []):
            spans = [s for s in line.get("spans", []) if s.get("text", "").strip()]
            if spans and all(
                _MONO.search(s.get("font", "")) and 4 <= s.get("size", 0) <= 7.5 for s in spans
            ):
                rows.append((tuple(line["bbox"]), "".join(s["text"] for s in spans)))
    if sum(bool(re.match(r"^\s*-\s+", text)) for _, text in rows) < 5:
        return items
    source_size = median(
        [
            s["size"]
            for raw in source_page.get_text("dict")["blocks"]
            for line in raw.get("lines", [])
            for s in line.get("spans", [])
            if _MONO.search(s.get("font", "")) and 4 <= s.get("size", 0) <= 7.5
        ]
    )
    # The first code fence is a hard boundary below the instruction stream.
    fence_top = min(
        (box[1] for box, text in rows if _FENCE.fullmatch(text.strip())),
        default=float(source_page.rect.height),
    )
    prose_rows = [(box, text) for box, text in rows if box[1] < fence_top - 0.1]
    if not prose_rows:
        return items
    top = min(box[1] for box, _ in prose_rows)
    right = max(box[2] for box, _ in prose_rows)
    last_bottom = max(box[3] for box, _ in prose_rows)
    floor = min(fence_top - 1.0, float(source_page.rect.height) - 36.0)
    for raw in source_page.get_text("dict")["blocks"]:
        if raw.get("type") == 0 and raw["bbox"][1] > last_bottom + 0.1:
            floor = min(floor, raw["bbox"][1] - 1.0)
    selected = []
    for index, (block, text) in enumerate(items):
        original = m.union_bbox(block.source_line_bboxes or block.redact_bboxes or [block.bbox])
        if (
            abs(block.font_size - source_size) <= 0.5
            and top - 0.5 <= original[1]
            and original[3] < fence_top
        ):
            selected.append((index, original, block, text))
    if len(selected) < 5 or any(
        block.source_math_bboxes or block.formula_anchors for _, _, block, _ in selected
    ):
        return items
    selected.sort(key=lambda entry: (entry[1][1], entry[1][0]))
    trials = []
    for index, original, block, text in selected:
        candidate = replace(
            block,
            bbox=(original[0], original[1], right, original[3]),
            preserve_position=False,
            nowrap=False,
            keepout_bboxes=None,
            fixed_translation_font_size=None,
        )
        trials.append((index, candidate, text))
    size = source_size * 0.92
    while size >= source_size * 0.88 - 1e-6:
        heights = [
            m._sibling_group_item_height(block, text, font_pack, size, margin)
            for _, block, text in trials
        ]
        if all(h is not None for h in heights):
            # One margin at each edge is already included in the line metrics
            # by the caller's rectangle; add it explicitly for this new stack.
            padded = [h + 2 * margin + 0.35 for h in heights]
            if sum(padded) <= floor - top:
                result = list(items)
                cursor = top
                for (index, block, text), height in zip(trials, padded):
                    result[index] = (
                        replace(
                            block,
                            bbox=(block.bbox[0], cursor, right, cursor + height),
                            fixed_translation_font_size=size,
                        ),
                        text,
                    )
                    cursor += height
                return result
        size -= 0.1
    return items


def join_prompt_list_continuations(blocks, source_page):
    """Translate each nested instruction with its wrapped continuation."""
    from . import pdf_layout as m

    source_rows = [
        line for raw in source_page.get_text("dict")["blocks"] for line in raw.get("lines", [])
    ]
    prompt_bullets = sum(
        bool(re.match(r"^\s*-\s+", "".join(s.get("text", "") for s in line.get("spans", []))))
        and any(_MONO.search(s.get("font", "")) for s in line.get("spans", []))
        for line in source_rows
    )
    if prompt_bullets < 5:
        return blocks
    result = []
    for block in blocks:
        previous = result[-1] if result else None
        plain = m.strip_sentinels(block.text).strip()
        previous_plain = m.strip_sentinels(previous.text).strip() if previous else ""
        if (
            previous is not None
            and previous.page_index == block.page_index
            and 4 <= previous.font_size <= 7.5
            and abs(previous.font_size - block.font_size) <= 0.3
            and previous.block_type == block.block_type == "body"
            and re.match(r"^-\s+", previous_plain)
            and not re.match(r"^-\s+", plain)
            and not re.search(r"[.!?;:]\s*$", previous_plain)
            and not (previous.source_math_bboxes or block.source_math_bboxes)
            and not (previous.nowrap or block.nowrap)
            and -1.5 <= block.bbox[1] - previous.bbox[3] <= block.font_size * 0.9
            and 0 <= block.bbox[0] - previous.bbox[0] <= block.font_size * 5
        ):
            result[-1] = replace(m.merge_two_blocks(previous, block), no_merge=True)
        else:
            result.append(block)
    return result
