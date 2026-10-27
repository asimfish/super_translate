"""Regressions minimized from a 17-page real-paper acceptance failure."""

import json
from pathlib import Path

import pytest

from pdf_zh_translator import pdf_layout as m

FIXTURE = Path(__file__).parent / "fixtures" / "gflownet_inline_records.json"


def test_short_proof_with_arrow_and_repeated_functions_is_prose():
    text = (
        "Next, take any edge s → s′ with s′ ≠ sf. By definition of d(s), "
        "there exists a shortest path from s0 to s of length d(s):"
    )
    assert not m.looks_like_action_skeleton_sequence(text)
    block = m.TextBlock(
        page_index=0, bbox=(55, 372, 528, 388), text=text, font_size=10, color=(0, 0, 0)
    )
    assert not m.should_preserve_original_block(block, [])


@pytest.mark.parametrize("arrow", ["→", "⇒", "->"])
def test_real_action_chains_remain_native(arrow):
    assert m.looks_like_action_skeleton_sequence(
        f"pick(hook) {arrow} pull(cube, hook) {arrow} place(hook)"
    )
    assert m.looks_like_action_skeleton_sequence("place(cube, rack)")
    assert m.looks_like_action_skeleton_sequence(
        f"• Action Skeleton: pick(blue cube) {arrow} place(blue cube)"
    )
    assert not m.looks_like_action_skeleton_sequence(f"We use f(x) and g(x) along s {arrow} t.")


def test_plain_denominator_rejoins_its_cross_object_inline_formula():
    data = json.loads(FIXTURE.read_text())
    records = [
        m._RawBlockRec(
            lines=[
                m._LineRec(
                    **{
                        **line,
                        "bbox": tuple(line["bbox"]),
                        **{
                            key: [tuple(box) for box in line[key]]
                            for key in ("math_bboxes", "prose_bboxes", "math_run_bboxes")
                        },
                    }
                )
                for line in record["lines"]
            ]
        )
        for record in data["records"]
    ]
    merged = m._merge_wrapped_formula_continuation_records(records)
    assert len(merged) == 1
    blocks = m.segments_from_record(0, merged[0], equation_record=False)
    assert len(blocks) == 1
    assert blocks[0].flow_inline_math
    assert f"{m.SENTINEL_OPEN}2{m.SENTINEL_CLOSE}" in blocks[0].text
    assert "All results are averaged" in blocks[0].text


def test_math_signatures_exclude_translatable_span_connectors():
    data = json.loads(FIXTURE.read_text())
    for block in data["qa_blocks"]:
        fragments = m._extract_formula_fragments(None, blocks=[block])
        assert not any(word in s.lower() for s in fragments for word in ("asd(", "onx", "here"))
    fragments = m._extract_formula_fragments(None, blocks=[data["qa_blocks"][0]])
    equation = next(s for s in fragments if "d(u,x)=" in s)
    assert "τ" in equation
    assert m._missing_formula_fragments([equation], ["任意边满足d(u,x)=0"])


def test_cmex_control_delimiter_signature_matches_portable_text():
    source = m._normalize_inline_formula_signature("P^{n}_{k}_{=1} I{s(k)=k}\x01/Z")
    copied = m._normalize_inline_formula_signature("P^{n}_{k}_{=1} I{s(k)=k}\u00a0/Z")
    assert source == copied
    assert m._missing_inline_formula_anchors([source], [copied.replace("=k", "=j")])


def test_prose_parenthesis_after_formula_is_not_a_formula_atom():
    spans = [
        {"text": "Z", "font": "CMSY10", "flags": 4, "size": 10.0, "bbox": (20.0, 20.0, 27.0, 30.0)},
        {
            "text": " (",
            "font": "Times-Roman",
            "flags": 4,
            "size": 10.0,
            "bbox": (27.0, 20.0, 35.0, 30.0),
        },
        {
            "text": "Morozov et al.",
            "font": "Times-Roman",
            "flags": 4,
            "size": 10.0,
            "bbox": (35.0, 20.0, 100.0, 30.0),
        },
    ]
    assert m._expanded_math_span_indexes(spans, [0], 10.0) == {0}
    mathematical = [spans[0], dict(spans[1], text="("), dict(spans[2], text="x", font="CMMI10")]
    assert m._expanded_math_span_indexes(mathematical, [0, 2], 10.0) == {0, 1, 2}


@pytest.mark.parametrize(
    "text",
    [
        "Brunswic et al. (2024) and Morozov et al. (2025) extend "
        "the theoretical construction of GFlowNets.",
        "Morozov et al. (2025) defines state and edge flows through",
    ],
)
def test_narrative_author_year_citations_are_body(text):
    assert not m._looks_like_reference_entry_text(text)
    assert m._looks_like_reference_entry_text("Morozov et al. (2025). Learning flows. ICML, 2025.")


@pytest.mark.parametrize(
    "prose",
    [
        "and clearly {M} for all {M}.",
        "Now assume (ii). If {M}, define",
        "If {M}, then {M}",
        ", so any choice of {M} works.",
        "We also define padded vectors {M} by",
        "From {M}, we obtain",
        "Renaming {M} as {M} yields (23).",
        "Step 1: every feasible {M} satisfies {M}.",
        "Since {M}, we obtain",
    ],
)
def test_short_proof_prose_is_translatable_despite_math_density(prose):
    text = prose.replace("{M}", m.SENTINEL_OPEN + "F(s)→v≥0" + m.SENTINEL_CLOSE)
    block = m.TextBlock(
        page_index=0,
        bbox=(55, 100, 300, 112),
        text=text,
        font_size=10,
        color=(0, 0, 0),
        source_math_bboxes=((100, 100, 140, 112),),
        source_prose_bboxes=((55, 100, 95, 112),),
    )
    assert m._looks_like_translatable_formula_prose_block(block)
    assert not m.should_preserve_original_block(block, [])


@pytest.mark.parametrize(
    ("key", "expected"),
    [
        ("raw-proof-13-19", "The dual function is"),
        ("raw-proof-14-20", "every feasible solution"),
        ("raw-proof-15-16", "First,"),
        ("raw-proof-11-29", "Hence"),
        ("raw-proof-11-25", "Then"),
    ],
)
def test_display_equation_records_keep_short_prose(key, expected):
    data = json.loads(FIXTURE.read_text())
    record, _ = m.parse_block_lines(data["proof_records"][key], page_width=612)
    blocks = m.segments_from_record(0, record, equation_record=True)
    assert any(expected in m.strip_sentinels(b.text) for b in blocks)
    assert not any(m.strip_sentinels(b.text).strip() == "inf" for b in blocks)


def test_wrapped_preposition_and_formula_stay_in_complete_sentence():
    data = json.loads(FIXTURE.read_text())
    record, _ = m.parse_block_lines(data["proof_records"]["raw-proof-16-8"], page_width=612)
    blocks = m.segments_from_record(0, record, equation_record=False)
    assert len(blocks) == 1
    assert "of" in m._text_outside_sentinels(blocks[0].text).split()[-1:]
    assert "π" in blocks[0].text


def test_short_conditional_lead_rejoins_detached_sum_and_sentence():
    data = json.loads(FIXTURE.read_text())
    records = [
        m.parse_block_lines(data["proof_records"][key], page_width=612)[0]
        for key in ("raw-proof-11-if", "raw-proof-11-imply")
    ]
    joined = m._merge_wrapped_formula_continuation_records(records)
    assert len(joined) == 1
    blocks = m.segments_from_record(0, joined[0], equation_record=False)
    assert len(blocks) == 1
    assert "imply" in blocks[0].text and "works." in blocks[0].text


@pytest.mark.parametrize("text", ["Since {M}, we obtain", "The dual function is", "First,"])
def test_qa_rejects_untranslated_short_proof_clauses(text):
    source = text.replace("{M}", m.SENTINEL_OPEN + "F(s)=0" + m.SENTINEL_CLOSE)
    block = m.TextBlock(
        page_index=0, bbox=(55, 100, 300, 112), text=source, font_size=10, color=(0, 0, 0)
    )
    assert m._translation_retains_source_prose_run(block, m.strip_sentinels(source))
    assert not m._translation_retains_source_prose_run(block, "由 F(s)=0 可得如下结果。")


@pytest.mark.parametrize("operator", ["in", "out"])
def test_plain_graph_operator_in_math_call_is_protected(operator):
    texts = ["∆(", operator, "(", "s", "))", " works."]
    spans = [
        dict(
            text=t,
            font="CMMI10" if i == 3 else "Times-Roman",
            size=10,
            flags=4,
            bbox=(i * 10, 10, (i + 1) * 10, 20),
        )
        for i, t in enumerate(texts)
    ]
    assert m._expanded_math_span_indexes(spans, [3], 10) == {0, 1, 2, 3, 4}
    prose = [dict(s) for s in spans]
    prose[2]["text"] = " the "
    assert 1 not in m._expanded_math_span_indexes(prose, [3], 10)


def test_so_before_math_is_translatable_prose():
    d = json.loads(FIXTURE.read_text())
    record, _ = m.parse_block_lines(d["proof_records"]["raw-proof-15-so"], page_width=612)
    assert m._text_outside_sentinels(record.bare_text()).startswith("So")
    for equation_record in (False, True):
        blocks = m.segments_from_record(0, record, equation_record=equation_record)
        assert len(blocks) == 1
        assert blocks[0].flow_inline_math
        assert "feasible" in blocks[0].text


def test_equation_reference_in_is_not_a_graph_operator():
    texts = ["X", " in", " (", "8", ")"]
    spans = [
        dict(
            text=t,
            font="CMMI10" if i == 0 else "Times-Roman",
            size=10,
            flags=4,
            bbox=(i * 10, 10, (i + 1) * 10, 20),
        )
        for i, t in enumerate(texts)
    ]
    assert 1 not in m._expanded_math_span_indexes(spans, [0], 10)


def test_math_font_does_not_split_an_otherwise_bold_heading():
    d = json.loads(FIXTURE.read_text())
    record, _ = m.parse_block_lines(d["proof_records"]["raw-proof-15-step3"], page_width=612)
    blocks = m.segments_from_record(0, record, equation_record=False)
    assert len(blocks) == 1
    assert blocks[0].bold
    assert "Step 3:" in blocks[0].text and "dual-optimal" in blocks[0].text
    assert blocks[0].source_math_bboxes
