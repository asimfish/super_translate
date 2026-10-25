"""Regression seams for the remaining real PDF failures, without private fixtures."""

from types import SimpleNamespace

import fitz

from pdf_zh_translator import page_inspector as pi
from pdf_zh_translator import pdf_layout as m


def line(text, bbox, *, math_boxes=(), prose=True):
    return m._LineRec(
        text,
        bbox,
        [{"text": text, "size": 11, "font": "SFRM1000", "bbox": bbox}],
        prose_bboxes=[bbox] if prose else [],
        math_bboxes=list(math_boxes),
        math_run_bboxes=list(math_boxes),
    )


def math(text):
    return m.SENTINEL_OPEN + text + m.SENTINEL_CLOSE


def test_let_definition_chain_keeps_its_fraction_and_prose_in_one_sentence():
    records = [
        m._RawBlockRec(
            [
                line(
                    "Let" + math("x=1") + ", and" + math("z=2+Σ"),
                    (100, 100, 300, 116),
                    math_boxes=[(120, 100, 150, 111), (270, 100, 300, 116)],
                )
            ]
        ),
        m._RawBlockRec(
            [
                line(
                    math("i a_i/Σ"),
                    (300, 101, 350, 117),
                    math_boxes=[(300, 101, 350, 117)],
                    prose=False,
                )
            ]
        ),
        m._RawBlockRec(
            [
                line(
                    math("a_j")
                    + ". The velocity statistics use the original parent at initial time.",
                    (350, 101, 500, 119),
                    math_boxes=[(350, 101, 370, 117)],
                )
            ]
        ),
    ]
    merged = m._merge_wrapped_formula_continuation_records(records)
    assert len(merged) == 1
    assert merged[0].flow_inline_math
    assert "Let" in merged[0].bare_text() and "initial time." in merged[0].bare_text()


def test_wrapped_math_sentence_is_separate_from_the_next_proof():
    records = [
        m._RawBlockRec(
            [
                line(
                    "These carriers form a finite layered representation of"
                    + math("D_R")
                    + ", and the component encodings, validity",
                    (72, 100, 540, 114),
                    math_boxes=[(330, 100, 350, 114)],
                )
            ]
        ),
        m._RawBlockRec(
            [
                line(
                    "predicates, "
                    + math("I_R, K_R")
                    + " and "
                    + math("δ_R")
                    + " form an exact bounded encoding.",
                    (72, 113, 500, 136),
                    math_boxes=[(120, 113, 250, 136)],
                )
            ]
        ),
        m._RawBlockRec(
            [
                line(
                    "Proof. The displayed carriers are finite by the definitions.",
                    (72, 140, 540, 154),
                )
            ]
        ),
    ]
    merged = m._merge_wrapped_formula_continuation_records(records)
    assert len(merged) == 2
    assert merged[0].flow_inline_math
    assert merged[1].bare_text().startswith("Proof.")


def test_boxed_python_listing_preserves_code_but_translates_caption_and_body():
    doc = fitz.open()
    try:
        page = doc.new_page(width=600, height=600)
        page.draw_rect((70, 60, 540, 300))
        page.insert_text(
            (80, 80),
            (
                "class Sample:\n    def __init__(self):\n        self.reset()\n"
                "    def reset(self):\n        pass\n    def process(self, xs):\n"
                "        maximum = 0\n        for i in range(32):\n"
                "            maximum = max(maximum, xs[i])\n        return maximum"
            ),
            fontname="cour",
            fontsize=9,
        )
        page.insert_text((72, 325), "Figure 9: Model structure and computation.", fontsize=10)
        page.insert_text(
            (72, 360),
            "We evaluate the model using the same inputs and compare the outputs.",
            fontsize=10,
        )
        units, _, _ = m.prepare_translation_units(doc, preserve_graphics_text=True)
        assert not [b for b, _, _ in units if b.bbox[1] < 300]
        assert any("Figure 9" in t for _, t, _ in units)
        assert any("We evaluate" in t for _, t, _ in units)
    finally:
        doc.close()


def test_font_size_uses_merged_source_role_before_its_smaller_pdf_objects():
    source = fitz.open()
    target = fitz.open()
    try:
        s = source.new_page(width=320, height=300)
        t = target.new_page(width=320, height=300)
        for y, size in ((60, 9), (72, 9), (84, 10), (130, 10), (160, 10), (190, 10)):
            s.insert_text((40, y), "Original text at this position", fontsize=size)
            t.insert_text(
                (40, y),
                "这是一段足够长的中文正文内容用于检测字号变化",
                fontname="china-s",
                fontsize=8.28 if y < 100 else 9.2,
            )
        role = SimpleNamespace(bbox=(35, 45, 290, 100), font_size=9)
        issues = pi._font_size_issues(s, t, 1, exclusion_bboxes=(), source_role_blocks=[role])
        assert not [i for i in issues if i.code == "font_size_drift"]
    finally:
        source.close()
        target.close()


def test_wrapped_three_column_table_row_is_not_a_prose_paragraph():
    spans = [
        SimpleNamespace(text=text, bbox=bbox, font="Times")
        for text, bbox in [
            ("Artifact failure", (72, 100, 133, 109)),
            ("failure transition", (152, 100, 220, 109)),
            ("partial operations and internal", (255, 100, 380, 109)),
            ("failures of the admitted semantics", (255, 111, 394, 120)),
            ("and assertions designated by the", (255, 122, 394, 131)),
            ("admitted RTL semantics", (255, 133, 355, 142)),
        ]
    ]
    assert pi._looks_like_aligned_preserved_rows(
        SimpleNamespace(spans=spans, text=" ".join(s.text for s in spans))
    )


def test_incorrect_merged_role_cannot_hide_real_body_font_shrink(monkeypatch):
    def block(y, size, translated):
        bbox = (40, y, 200, y + 12)
        text = (
            "这是一段足够长的中文正文内容用于检测字号变化"
            if translated
            else "Original paragraph fragment"
        )
        return pi._Block(bbox, (pi._Span(text, "Regular", size, bbox),))

    source = object()
    target = object()
    original = [block(80, 10, False), *[block(y, 10, False) for y in (160, 190, 220)]]
    translated = [block(80, 8.28, True), *[block(y, 9.2, True) for y in (160, 190, 220)]]
    monkeypatch.setattr(pi, "_text_blocks", lambda page: original if page is source else translated)
    role = SimpleNamespace(bbox=(35, 40, 210, 140), font_size=9)
    assert any(
        i.code == "font_size_drift"
        for i in pi._font_size_issues(
            source, target, 1, exclusion_bboxes=(), source_role_blocks=[role]
        )
    )


def test_tex_typewriter_font_flag_owns_code_even_with_unrecognized_font_name():
    span = {
        "text": "class Model:",
        "font": "SFTT0900",
        "flags": 12,
        "size": 9,
        "bbox": (80, 80, 160, 90),
    }
    record = line(span["text"], span["bbox"])
    record.spans = [span]
    assert m.line_has_code_font(record)


def test_singular_acknowledgment_starts_a_heading_instead_of_shrinking_body():
    doc = fitz.open()
    try:
        doc.new_page(width=340, height=400)
        p = doc.new_page(width=340, height=400)
        p.insert_text((54, 80), "ACKNOWLEDGMENT", fontsize=8)
        p.insert_textbox(
            (54, 90, 300, 200),
            (
                "The authors used an assistant to prepare the manuscript. All text was "
                "reviewed and verified by the authors, who take responsibility for its content."
            ),
            fontsize=10,
        )
        units, _, _ = m.prepare_translation_units(doc, preserve_graphics_text=True)
        body = [b for b, t, _ in units if "The authors" in t]
        assert len(body) == 1 and body[0].font_size == 10
        assert "ACKNOWLEDGMENT" not in body[0].text
        assert m.line_looks_like_section_heading(line("ACKNOWLEDGMENT", (54, 72, 140, 80)))
    finally:
        doc.close()


def test_ite_function_label_is_not_a_translatable_equation_prose_slot():
    record = m._RawBlockRec(
        [
            line(
                math("(x,y)="), (100, 100, 180, 120), math_boxes=[(100, 100, 180, 120)], prose=False
            ),
            line("ite", (190, 102, 210, 114)),
            line(
                math("(κ,Pack(x),y)"),
                (210, 100, 320, 120),
                math_boxes=[(210, 100, 320, 120)],
                prose=False,
            ),
        ]
    )
    assert m._compact_display_formula_prose_slot(0, record, 1) is None


def test_split_single_line_listing_statements_keep_code_ownership():
    for text in ("class Model:", "def reset(self):", "self.reset()", "pass", "maximum = 0"):
        rec = line(text, (80, 80, 240, 90))
        rec.spans[0].update(font="SFTT0900", flags=12)
        assert m.record_is_algorithm(m._RawBlockRec([rec])), text
    prose = line("The model calls reset() for each input.", (80, 80, 400, 90))
    prose.spans[0].update(font="SFTT0900", flags=12)
    assert not m.record_is_algorithm(m._RawBlockRec([prose]))


def test_boolean_literals_and_error_constructor_stay_in_display_math():
    for word in ("true", "false", "Err"):
        rec = m._RawBlockRec(
            [
                line(
                    math("(x,y)="),
                    (100, 100, 180, 120),
                    math_boxes=[(100, 100, 180, 120)],
                    prose=False,
                ),
                line(word, (190, 102, 220, 114)),
                line(
                    math("(κ,x,y)"),
                    (220, 100, 320, 120),
                    math_boxes=[(220, 100, 320, 120)],
                    prose=False,
                ),
            ]
        )
        assert m._compact_display_formula_prose_slot(0, rec, 1) is None, word


def test_conditional_proof_prose_does_not_absorb_its_centered_display():
    record = m._RawBlockRec(
        [
            line(
                "Proof. We prove this claim by structural induction on each command",
                (72, 100, 540, 114),
            ),
            line(
                "under the path condition and any assignment encoding all defined",
                (72, 114, 540, 128),
            ),
            line(
                "components of " + math("ρ") + ". If",
                (72, 128, 170, 142),
                math_boxes=[(150, 128, 160, 142)],
            ),
            line(
                math("(κ,ρ,ε,h)=SymC(c;ρ,π),"),
                (237, 142, 375, 162),
                math_boxes=[(237, 142, 375, 162)],
                prose=False,
            ),
        ]
    )
    assert m._prose_trailing_display_start(record) == 3
    blocks = m.segments_from_record(0, record)
    assert blocks and "Proof." in blocks[0].text
    assert blocks[0].bbox[3] <= 142
    assert "SymC" not in blocks[0].text
    inline = m._RawBlockRec(
        [
            *record.lines[:3],
            line(
                math("q=F(x)"), (170, 129, 220, 142), math_boxes=[(170, 129, 220, 142)], prose=False
            ),
        ]
    )
    assert m._prose_trailing_display_start(inline) is None


def test_translated_let_and_equivalent_angle_brackets_keep_exact_math_values():
    spans = [
        {"text": t, "bbox": (50, 50, 200, 65)} for t in ("Let", "x1=x(z1),ζ=4,", " and", "⟨f⟩m=∑i")
    ]
    block = {"type": 0, "lines": [{"spans": spans}]}
    fragments = m._extract_formula_fragments(None, blocks=[block])
    views = [m._normalize_formula_fragment_for_compare("设 x1=x(z1),ζ=4，且〈f〉m=∑i")]
    assert not m._missing_formula_fragments(fragments, views)
    changed = [m._normalize_formula_fragment_for_compare("设 x1=x(z1),ζ=5，且〈f〉m=∑i")]
    assert m._missing_formula_fragments(fragments, changed)
    assert m._normalize_inline_formula_signature(
        "⟨f⟩m=∑i"
    ) == m._normalize_inline_formula_signature("〈f〉m=∑i")


def test_math_accent_and_phi_have_portable_but_not_missing_signature():
    source = "x1=x(z1),˙ζ0,i=˙ζϕ,i(z0,0)"
    target = "x1=x(z1), ̇ζ0,i= ̇ζφ,i(z0,0)"
    assert m._normalize_formula_fragment_for_compare(
        source
    ) == m._normalize_formula_fragment_for_compare(target)
    assert m._normalize_formula_fragment_for_compare(
        source
    ) != m._normalize_formula_fragment_for_compare(target.replace("̇", ""))


def test_formula_connective_is_native_only_when_all_nonmath_words_are_connectives():
    bbox = (100, 100, 400, 115)
    formula = pi._Block(
        bbox,
        (
            pi._Span("Reach := {γ ∈ Γ | ∃x < N, γ ∈ H}", "CMR10", 11, bbox),
            pi._Span("x", "CMBX10", 11, bbox),
            pi._Span("such that", "SFRM1000", 11, bbox),
        ),
    )
    assert pi._native_formula_connectives_only(formula)
    explanation = pi._Block(
        bbox,
        (
            pi._Span("x = y", "CMR10", 11, bbox),
            pi._Span("where the velocity is evaluated at every step", "SFRM1000", 11, bbox),
        ),
    )
    assert not pi._native_formula_connectives_only(explanation)


def test_source_owned_supplement_references_cover_their_extraction_fragments():
    fragment = "Tables II identify the refined structures and the measured transition temperatures."
    references = (
        "S3. A. Smith. A measured crystal (1991). doi:10.1000/one. "
        + fragment
        + " S4. B. Jones. Another crystal (2001). doi:10.1000/two."
    )
    source = pi._Block(
        (100, 100, 500, 300), (pi._Span(references, "Times", 10, (100, 100, 500, 300)),)
    )
    target = pi._Block(
        (110, 180, 490, 205), (pi._Span(fragment, "Times", 10, (110, 180, 490, 205)),)
    )
    assert pi._source_reference_list_owns_block(target, [source])
    outside = pi._Block((110, 310, 490, 335), target.spans)
    assert not pi._source_reference_list_owns_block(outside, [source])


def test_single_prose_line_borrows_measured_whitespace_above_a_fixed_equation(monkeypatch):
    previous = m.TextBlock(0, (108, 536, 504, 614.6), "A preceding paragraph", 10, (0, 0, 0))
    body = m.TextBlock(
        0,
        (108, 614.64, 375, 624.6),
        "For eligible sites, the parameterization is",
        10,
        (0, 0, 0),
        keepout_bboxes=[(201, 621.2, 335, 639)],
    )
    items = [(previous, "上一段正文"), (body, "合格位点的角度参数化为")]
    monkeypatch.setattr(m, "translated_text_fits", lambda b, *a, **k: b.bbox[1] <= 610)
    monkeypatch.setattr(
        m,
        "_translated_block_ink_bbox",
        lambda b, *a, **k: (
            (108, 580, 500, 600) if b is previous else (108, b.bbox[1] + 0.5, 375, b.bbox[1] + 10)
        ),
    )
    kwargs = dict(
        page_rect=SimpleNamespace(width=612, height=792),
        font_pack=object(),
        min_font_size=5.5,
        font_scale=0.92,
        margin=0.8,
        source_document=object(),
    )
    moved = m._clear_adjacent_formula_ink_overlaps(items, [False, False], **kwargs)
    assert moved[1][0].bbox[1] < body.bbox[1]
    assert moved[1][0].redact_bboxes == [body.bbox]
    assert moved[1][0].keepout_bboxes == body.keepout_bboxes
    blocked = m._clear_adjacent_formula_ink_overlaps(
        items, [False, False], obstacles=[(108, 605, 504, 613)], **kwargs
    )
    assert blocked[1][0].bbox == body.bbox


def test_measured_safe_body_size_is_not_undone_by_estimated_predecessor_height(monkeypatch):
    previous = m.TextBlock(
        0,
        (108, 536, 504, 614.6),
        "A preceding paragraph",
        10,
        (0, 0, 0),
        flow_inline_math=True,
        formula_anchors=((200, 590, 280, 605),),
    )
    body = m.TextBlock(
        0,
        (108, 614.64, 375, 624.6),
        "For eligible sites, the parameterization is",
        10,
        (0, 0, 0),
        keepout_bboxes=[(201, 621.2, 335, 639)],
    )
    monkeypatch.setattr(
        m,
        "translated_text_fits",
        lambda b, text, fonts, size, floor, *a, **k: floor < 9 or b.bbox[1] <= 610.64,
    )
    monkeypatch.setattr(m, "_sibling_group_item_height", lambda *a, **k: 75)
    monkeypatch.setattr(
        m,
        "_translated_block_ink_bbox",
        lambda b, *a, **k: (
            (108, 538, 504, 608) if b is previous else (109, b.bbox[1] + 2.2, 301, b.bbox[1] + 12.2)
        ),
    )
    moved = m._clear_adjacent_formula_ink_overlaps(
        [(previous, "上一段正文"), (body, "合格位点的角度参数化为")],
        [False, False],
        page_rect=SimpleNamespace(width=612, height=792),
        font_pack=object(),
        min_font_size=5,
        font_scale=0.92,
        margin=0.8,
        source_document=object(),
    )
    assert moved[1][0].bbox[1] <= 610.64
