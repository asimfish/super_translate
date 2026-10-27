"""Regression seams for fresh queue failures, with generated source geometry."""

from types import SimpleNamespace

import fitz
import pytest

from pdf_zh_translator import page_inspector as pi
from pdf_zh_translator import pdf_layout as m


def test_caption_cannot_expand_into_a_wrapped_body_line():
    caption = m.TextBlock(
        0,
        (306, 299, 550, 333),
        "Figure 6: Policy architecture.",
        9,
        (0, 0, 0),
        block_type="caption",
    )
    body = m.TextBlock(
        0,
        (62, 318, 550, 383),
        "Our policy and training details.",
        11,
        (0, 0, 0),
        source_line_bboxes=((62, 318, 292, 331), (62, 345, 550, 357)),
    )
    m.relax_caption_boxes(
        SimpleNamespace(rect=SimpleNamespace(height=792)),
        [(caption, "图6：策略架构"), (body, "正文")],
    )
    assert caption.bbox[3] <= 342
    assert caption.redact_bboxes == [(306, 299, 550, 333)]


def test_two_bold_labels_on_one_prose_row_share_a_single_flow():
    left = m.TextBlock(0, (72, 100, 354, 111), "Zero-shot evaluation.", 11, (0, 0, 0), bold=True)
    heading = m.TextBlock(
        0,
        (365, 100, 455, 111),
        "Benchmark Goal.",
        11,
        (0, 0, 0),
        bold=True,
        block_type="run_in_heading",
        source_line_bboxes=((365, 100, 455, 111),),
    )
    body = m.TextBlock(
        0,
        (72, 112, 540, 180),
        "We evaluate the benchmark using the same inputs.",
        11,
        (0, 0, 0),
        source_line_bboxes=((455, 100, 540, 111), (72, 114, 540, 127)),
    )
    items = m._combine_inline_style_translation_items(
        [(left, "零样本评估。"), (heading, "基准目标。"), (body, "我们评估该基准。")]
    )
    assert len(items) == 1
    block, text = items[0]
    assert text.startswith("零样本评估。 基准目标。")
    assert block.translated_bold_prefix_chars == len("零样本评估。 基准目标。")
    assert block.bbox == (72, 100, 540, 180)


def test_source_echo_does_not_treat_json_identifiers_as_prose():
    source = (
        "Read state_NN.json, log_NN.json, images, depth maps, and world maps. "
        "Then decide the next command."
    )
    block = m.TextBlock(0, (72, 100, 540, 180), source, 10, (0, 0, 0))
    assert not m._translation_retains_source_prose_run(
        block, "读取 state_NN.json, log_NN.json、图像、深度图和世界坐标图，再决定下一条命令。"
    )
    assert m._translation_retains_source_prose_run(block, "读取结果。Then decide the next command.")
    files = "grasp.md, localize.md, transport.md, manipulation.md, task-exploration.md"
    file_block = m.TextBlock(0, block.bbox, "The library contains " + files, 10, (0, 0, 0))
    assert not m._translation_retains_source_prose_run(file_block, "技能库包含 " + files)


def test_long_author_year_lists_are_owned_citations_but_not_bracketed_prose():
    citation = "[" + ", ".join(f"Smith et al., {year}" for year in range(1980, 2026)) + "]"
    source = "The models improve through skills and memories " + citation + "."
    block = m.TextBlock(0, (72, 100, 540, 180), source, 10, (0, 0, 0))
    assert not m._translation_retains_source_prose_run(
        block, "模型通过技能和记忆得到改进 " + citation
    )
    assert m._translation_retains_source_prose_run(
        block, "模型说明：[The models improve through skills and memories in 2026]"
    )


def test_model_citations_are_not_running_english_but_body_still_is():
    text = (
        "Sonnet 5.5 [Anthropic, 2026f]、Haiku 5.5 [Anthropic, 2026b] "
        "和 Fable 5.1 [Anthropic, 2026a]"
    )
    assert not m._looks_like_untranslated_english(text)
    assert m._looks_like_untranslated_english(
        "The method uses a large model for each task [Smith et al., 2026]."
    )


def test_document_tree_paths_are_not_an_english_paragraph():
    text = (
        "docs/.........................................api-reference.md,"
        "run-artifacts.md,setup configs/"
    )
    assert not m._looks_like_untranslated_english(text)
    assert m._looks_like_untranslated_english(
        text + " The method requires these files to perform the evaluation correctly."
    )


def test_preserved_numeric_grid_allows_abbreviated_task_labels():
    spans = []
    for row, label in enumerate(
        ("Betw. plate+ramekin", "From table center", "In top drawer", "Next to plate", "On cabinet")
    ):
        y = 100 + row * 12
        spans.append(pi._Span(label, "Times", 8, (150, y, 260, y + 9)))
        for x, value in zip((300, 335, 370, 405), ("2%", "10%", "94%", "94%")):
            spans.append(pi._Span(value, "Times", 8, (x, y, x + 23, y + 9)))
    block = pi._Block((150, 100, 428, 157), tuple(spans))
    assert pi._looks_like_aligned_preserved_rows(block)
    centered = tuple(
        pi._Span(
            span.text,
            span.font,
            span.size,
            (span.bbox[0] - (i % 4) * 3, span.bbox[1], span.bbox[2] + (i % 4) * 3, span.bbox[3]),
        )
        for i, span in enumerate(spans)
    )
    assert pi._looks_like_aligned_preserved_rows(pi._Block(block.bbox, centered))
    footer = tuple(
        pi._Span(value, "Times", 8, (x, 170, x + 23, 179))
        for x, value in zip((300, 335, 350, 405), ("9.0%", "42.0%", "54.0%", "51.0%"))
    )
    assert pi._looks_like_aligned_preserved_rows(pi._Block(block.bbox, (*centered, *footer)))


def test_joint_run_in_labels_retain_bold_role_without_exempting_regular_labels(monkeypatch):
    left = m.TextBlock(0, (72, 100, 354, 111), "Zero-shot evaluation.", 11, (0, 0, 0), bold=True)
    heading = m.TextBlock(
        0,
        (365, 100, 455, 111),
        "Benchmark Goal.",
        11,
        (0, 0, 0),
        bold=True,
        block_type="run_in_heading",
    )
    body = m.TextBlock(
        0,
        (72, 112, 540, 180),
        "We evaluate the benchmark.",
        11,
        (0, 0, 0),
        source_line_bboxes=((455, 100, 540, 111), (72, 114, 540, 127)),
    )
    prefix = {
        "text": "零样本评估。基准目标。",
        "font": "NotoSansCJKsc-Bold",
        "flags": 16,
        "size": 10,
        "origin": (73, 110),
        "bbox": (73, 101, 290, 112),
    }
    regular = {
        "text": "我们在严格的设置下评估该基准",
        "font": "NotoSerifCJKsc-Regular",
        "flags": 0,
        "size": 10,
        "origin": (293, 110),
        "bbox": (293, 101, 539, 112),
    }
    monkeypatch.setattr(m, "_page_cjk_spans", lambda page: [prefix, regular])
    assert not m._font_role_consistency_issues([left, heading, body], object(), 1)
    prefix.update(font="NotoSerifCJKsc-Regular", flags=0)
    assert any(
        i.code == "font_role_heading_mismatch"
        for i in m._font_role_consistency_issues([left, heading, body], object(), 1)
    )


def source_line(text, bbox, size=10, bold=False, mono=False):
    return m._LineRec(
        text,
        bbox,
        [
            {
                "text": text,
                "bbox": bbox,
                "size": size,
                "font": "Times-Bold" if bold else "CustomMono" if mono else "Times",
                "flags": 8 if mono else 16 if bold else 0,
            }
        ],
        prose_bboxes=[bbox],
    )


def test_exhausted_wrap_fallback_does_not_paint_across_a_side_float(monkeypatch):
    doc = fitz.open()
    try:
        page = doc.new_page(width=400, height=300)
        fonts = m.build_font_pack(None, [])
        m.register_font_pack(page, fonts)
        block = m.TextBlock(
            0,
            (50, 60, 350, 160),
            "An ordinary paragraph",
            10,
            (0, 0, 0),
            keepout_bboxes=[(180, 50, 350, 110)],
        )
        monkeypatch.setattr(m, "_formula_aware_slot_baselines", lambda *a, **k: None)
        assert m.insert_translated_text(
            page, block, "这是一段用于检测浮动图注避让的中文正文。" * 3, fonts, 10, 6, 0.8
        )
        assert all(span["bbox"][2] < 180 for span in m._page_cjk_spans(page))
    finally:
        doc.close()


def test_small_formula_anchor_does_not_clip_the_whole_body_column():
    block = m.TextBlock(0, (34, 548, 380, 606), "Ordinary body", 10, (0, 0, 0))
    rect = fitz.Rect(block.bbox)
    assert m._clip_exhausted_wrap_to_side_column(block, rect, [(323, 578, 336, 585)]) is None


def test_upper_float_is_avoided_even_when_its_center_is_above_body():
    float_box = (323, 591, 508, 688)
    block = m.TextBlock(
        0, (108, 645, 504, 732), "Body beside a caption", 10, (0, 0, 0),
        keepout_bboxes=[float_box],
    )
    assert m._block_keepouts(block, fitz.Rect(block.bbox)) == [float_box]
    # A distant float above the paragraph does not obstruct it.
    block.keepout_bboxes = [(323, 530, 508, 620)]
    assert m._block_keepouts(block, fitz.Rect(block.bbox)) == []


def test_wide_body_line_crossing_oversized_figure_bounds_remains_translatable():
    text = (
        "We now run imitation learning from a dataset containing "
        "multi-modal, long-horizon behaviors of various"
    )
    region = (187, 102, 562, 315)
    body = m.TextBlock(8, (62, 108, 550, 119), text, 10.8, (0, 0, 0))
    assert not m.should_preserve_original_block(body, [region])
    # Long text wholly inside the graphic remains protected.
    internal = m.replace(body, bbox=(200, 150, 550, 161))
    assert m.should_preserve_original_block(internal, [region])
    label = m.replace(body, text="Image Tokens", bbox=(150, 108, 400, 119))
    assert m.should_preserve_original_block(label, [region])


@pytest.mark.parametrize(
    "mode,method",
    [
        ("deepseek", "_translate_deepseek_plain"),
        ("openai-compatible", "_translate_openai_plain"),
        ("anthropic", "_translate_anthropic_plain"),
    ],
)
def test_document_instructions_are_sent_as_quoted_translation_data(monkeypatch, mode, method):
    import json

    from pdf_zh_translator.translators import VendorTranslator

    source = (
        'Output format: a single JSON object with exactly two keys. "routes" and "explanations".'
    )
    expected = "输出格式：仅包含两个键的单个 JSON 对象。"
    vendor = VendorTranslator(
        api_url="https://example.invalid", api_key="test-key", mode=mode, progress=False
    )
    requests = []

    def post(url, payload):
        requests.append(payload)
        return (
            {"content": [{"type": "text", "text": expected}]}
            if mode == "anthropic"
            else {"choices": [{"message": {"content": expected}}]}
        )

    monkeypatch.setattr(vendor, "_post_json", post)
    assert getattr(vendor, method)(source) == expected
    assert json.loads(requests[0]["messages"][-1]["content"]) == {"document_text": source}


def test_executed_document_output_is_not_accepted_as_a_translation():
    from pdf_zh_translator.translators import TranslationError, coerce_plain_translation

    with pytest.raises(TranslationError):
        coerce_plain_translation(
            '{"routes": [["invented answer"]], "explanations": ["新生成的答案"]}'
        )


def test_stacked_typographic_headings_are_separate_from_supplement_prose():
    record = m._RawBlockRec(
        [
            source_line("Lab-Sim", (130, 55, 216, 67), 12, bold=True),
            source_line("Annotation Pipeline", (54, 70, 190, 81), 11, bold=True),
            source_line(
                "We first assign the model a role to annotate each image.", (54, 85, 292, 95)
            ),
            source_line(
                "The instructions provide the requirements and the expected outputs.",
                (54, 96, 292, 106),
            ),
            source_line(
                "The model then creates all annotations from the available frames.",
                (54, 107, 292, 117),
            ),
        ]
    )
    blocks = m.segments_from_record(3, record)
    assert [b.block_type for b in blocks[:2]] == ["heading", "heading"]
    assert any("We first assign" in b.text and b.font_size == 10 for b in blocks)


def test_prose_before_a_monospaced_listing_is_not_classified_as_table_cells():
    record = m._RawBlockRec(
        [
            *[
                source_line(text, (54, 100 + i * 12, 292, 110 + i * 12))
                for i, text in enumerate(
                    (
                        "We provide several question variants for each input.",
                        "The model selects one variant for each dimension.",
                        "The generated answers follow the visual evidence.",
                    )
                )
            ],
            source_line("1 {", (54, 145, 100, 154), 9, mono=True),
            source_line('2 "id": "sample",', (54, 155, 220, 164), 9, mono=True),
            source_line('3 "messages": [', (54, 165, 220, 174), 9, mono=True),
        ]
    )
    parts = m._split_leading_prose_before_code(record)
    assert len(parts) == 2
    assert len(parts[0].lines) == 3
    assert not m.record_is_table(parts[0])
    assert all(m.line_has_code_font(line) for line in parts[1].lines)
    numbered = m._RawBlockRec(
        [
            *record.lines[:3],
            *[
                line
                for row, text in enumerate(("{", '"id": "sample",', '"messages": ['))
                for line in (
                    source_line(str(row + 1), (54, 145 + row * 10, 60, 154 + row * 10), 9),
                    source_line(text, (72, 145 + row * 10, 250, 154 + row * 10), 7, mono=True),
                )
            ],
        ]
    )
    assert len(m._split_leading_prose_before_code(numbered)) == 2
    for line in numbered.lines[3:]:
        for span in line.spans:
            if span["font"] == "CustomMono":
                span.update(font="NimbusMonL-Regu", flags=4)
    assert len(m._split_leading_prose_before_code(numbered)) == 2


def test_wrapped_json_tail_is_separate_from_following_regular_prose():
    code = [
        source_line('with the gripper, move the robotic arm', (370, 192, 529, 199), 7, mono=True),
        source_line('forward to close the door."}', (370, 202, 487, 209), 7, mono=True),
        source_line('10', (314, 211, 323, 220), 9),
        source_line(']', (342, 212, 346, 219), 7, mono=True),
        source_line('11', (314, 221, 323, 230), 9),
        source_line('}', (333, 222, 337, 229), 7, mono=True),
    ]
    prose = [
        source_line('The remaining dimensions follow the same format,', (329, 235, 558, 245)),
        source_line(
            'with each question and answer pair stored as an independent', (320, 246, 558, 256)
        ),
        source_line('training sample.', (320, 257, 403, 267)),
    ]
    for line in code:
        for span in line.spans:
            if span['font'] == 'CustomMono':
                span.update(font='NimbusMonL-Regu', flags=4)
    parts = m._split_leading_prose_before_code(m._RawBlockRec([*code, *prose]))
    assert len(parts) == 2
    assert parts[0].lines == code and parts[1].lines == prose
    assert m.record_is_algorithm(parts[0])
    assert not m.record_is_algorithm(parts[1])
    assert m.line_has_code_font(code[3]) and m.line_is_code_like(code[3])
    assert m.line_has_code_font(code[5]) and m.line_is_code_like(code[5])
