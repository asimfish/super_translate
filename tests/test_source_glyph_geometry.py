"""Actual publisher-page glyph evidence; full-document replays remain a separate gate."""

from pathlib import Path

import fitz
import pytest

from pdf_zh_translator import pdf_layout as layout

FIXTURE = Path(__file__).parent / "fixtures/nature_quantum_51162_p9.pdf"


@pytest.fixture(scope="module")
def collected():
    seen = []
    original = layout._normalized_extracted_math_span

    def capture(span):
        result = original(span)
        if result.get("font") == "AdvP4C4E46":
            seen.append(result)
        return result

    layout._normalized_extracted_math_span = capture
    try:
        with fitz.open(FIXTURE) as doc:
            blocks, _ = layout.collect_text_blocks(doc)
    finally:
        layout._normalized_extracted_math_span = original
    return blocks, seen


def test_actual_sigma_uses_its_own_origin_and_embedded_outline(collected):
    _, spans = collected
    target = next(
        s for s in spans if s.get("text") == " P" and abs(s["origin"][0] - 496.0587) < 0.1
    )
    assert target["bbox"][3] < 625.6, target
    assert target["bbox"][3] >= 625.2076
    assert target["bbox"][1] <= 616.9886
    assert target["text"] == " P"  # preserve decoded text, do not substitute P with Sigma
    glyphs = target.get("_source_glyph_ink", ())
    assert len(glyphs) == 1
    assert glyphs[0]["glyph_id"] == 2
    assert glyphs[0]["origin"][1] == pytest.approx(616.988525, abs=0.001)
    assert target["origin"][1] == pytest.approx(623.111694, abs=0.001)  # leading synthetic space


def test_actual_paragraph_owner_excludes_phantom_tail(collected):
    blocks, _ = collected
    preceding = next(
        b for b in blocks if layout.strip_sentinels(b.text).startswith("which can be viewed")
    )
    merged_tail = "measurement scheme. Nontrivial" in layout.strip_sentinels(preceding.text)
    if not merged_tail:
        assert preceding.bbox[3] < 636.0, preceding.bbox
    next_body = [
        b
        for b in blocks
        if b.bbox[0] > 300
        and b.block_type == "body"
        and "measurement scheme. Nontrivial" in layout.strip_sentinels(b.text)
    ]
    # It can be merged into the same paragraph, or remain with disjoint layout owners.
    for following in next_body:
        if following is not preceding:
            assert (
                min(preceding.bbox[3], following.bbox[3])
                - max(preceding.bbox[1], following.bbox[1])
                <= 1.0
            )


@pytest.mark.parametrize(
    "font,text",
    [("OrdinaryBody", "P"), ("AdvP4C4E46", "P"), ("AdvP4C4E46", "X"), ("Unknown", "\x02")],
)
def test_without_physical_metadata_unknown_font_is_unchanged(font, text):
    span = {
        "font": font,
        "text": text,
        "size": 8.2191,
        "origin": (10.0, 20.0),
        "bbox": (10.0, 13.67, 22.0, 44.31),
    }
    assert layout._normalized_extracted_math_span(span) == span


def test_real_tall_operators_and_control_delimiters_keep_all_painted_ink(collected):
    _, spans = collected
    tall = [
        s for s in spans if s.get("text") in ("X", "\x02", "\x03") and s.get("_source_glyph_ink")
    ]
    assert len(tall) >= 10
    # Actual embedded X operator descends 1.4 em; these are not flat glyphs.
    products = [s for s in tall if s["text"] == "X"]
    assert len(products) >= 6
    for span in tall:
        for glyph in span["_source_glyph_ink"]:
            ink = glyph["ink_bbox"]
            box = span["bbox"]
            assert box[0] <= ink[0] and box[1] <= ink[1] and box[2] >= ink[2] and box[3] >= ink[3]
            if span["text"] == "X":
                assert ink[3] - glyph["origin"][1] == pytest.approx(glyph["size"] * 1.4)
            if span["text"] in ("\x02", "\x03"):
                assert ink[3] - glyph["origin"][1] == pytest.approx(glyph["size"] * 1.759)
                assert span["text"] in ("\x02", "\x03")  # no guessed Unicode


@pytest.mark.parametrize(
    "unsupported",
    [
        "rotated",
        "stroke",
        "hidden",
        "ambiguous_font",
        "no_outlines",
        "api_failure",
        "horizontal_stretch",
    ],
)
def test_real_page_unsupported_physical_evidence_falls_back(unsupported, monkeypatch):
    import copy

    with fitz.open(FIXTURE) as doc:
        page = doc[0]
        raw = page.get_text("dict")
        before = copy.deepcopy(raw)

        class Proxy:
            parent = doc

            def get_fonts(self, **kwargs):
                fonts = page.get_fonts(**kwargs)
                if unsupported == "api_failure":
                    raise RuntimeError("unavailable")
                if unsupported == "ambiguous_font":
                    fonts = [*fonts, *[(f[0] + 90000, *f[1:]) for f in fonts]]
                return fonts

            def get_text(self, *args, **kwargs):
                return page.get_text(*args, **kwargs)

            def get_texttrace(self):
                data = copy.deepcopy(page.get_texttrace())
                for trace in data:
                    if unsupported == "rotated":
                        trace["dir"] = (0.0, 1.0)
                    if unsupported == "stroke":
                        trace["type"] = 1
                    if unsupported == "hidden":
                        trace["type"] = 3
                    if unsupported == "horizontal_stretch":
                        trace["chars"] = [
                            (u, g, origin, (box[0], box[1], box[0] + (box[2] - box[0]) * 2, box[3]))
                            for u, g, origin, box in trace["chars"]
                        ]
                return data

        if unsupported == "no_outlines":
            monkeypatch.setattr(layout, "_embedded_font_glyph_ink_bounds", lambda *args: {})
        layout._attach_source_glyph_ink_bboxes(Proxy(), raw, {})
        assert raw == before


@pytest.fixture(scope="module")
def real_record_pair():
    captured = []
    original = layout._merge_wrapped_formula_continuation_records

    def capture(records):
        for current, following in zip(records, records[1:]):
            if 584 < current.bbox[1] < 585 and current.bbox[0] > 300:
                captured.append((current, following))
        return original(records)

    layout._merge_wrapped_formula_continuation_records = capture
    try:
        with fitz.open(FIXTURE) as doc:
            layout.collect_text_blocks(doc)
    finally:
        layout._merge_wrapped_formula_continuation_records = original
    assert len(captured) == 1
    return captured[0]


def test_real_same_row_formula_and_prose_tail_have_one_owner(real_record_pair, collected):
    current, following = real_record_pair
    assert layout._records_share_proven_inline_glyph_row(current, following)
    blocks, _ = collected
    owner = next(
        b for b in blocks if layout.strip_sentinels(b.text).startswith("which can be viewed")
    )
    assert "are in particular independent of the measurement scheme." in layout.strip_sentinels(
        owner.text
    )
    assert "fading memory" in layout.strip_sentinels(owner.text)
    # Lost formula prefix c/rho and subscripts must re-enter source math anchors.
    assert any(abs(box[0] - 521.2345) < 0.01 for box in owner.source_math_atom_bboxes)


@pytest.mark.parametrize(
    "case",
    [
        "other_column",
        "display_number",
        "heading",
        "no_glyph_evidence",
        "not_touching",
        "no_prose_tail",
        "pure_formula_lead",
    ],
)
def test_source_row_rejoin_rejects_other_owners(real_record_pair, case):
    import copy

    current, following = copy.deepcopy(real_record_pair)
    if case == "other_column":
        for line in following.lines:
            line.bbox = (line.bbox[0] - 250, line.bbox[1], line.bbox[2] - 250, line.bbox[3])
    elif case == "display_number":
        following.lines.append(layout._LineRec("(12)", (540, 650, 560, 660), []))
    elif case == "heading":
        following.lines.append(
            layout._LineRec(
                "4 Results",
                (306, 638, 561, 648),
                [{"text": "4 Results", "size": 12, "flags": 16, "font": "Bold"}],
            )
        )
    elif case == "no_glyph_evidence":
        for span in current.lines[-1].spans:
            span.pop("_source_glyph_ink", None)
    elif case == "not_touching":
        following.lines[0].math_bboxes = [(550, 660, 560, 670)]
    elif case == "no_prose_tail":
        following.lines = following.lines[:3]
    elif case == "pure_formula_lead":
        current.lines = current.lines[-1:]
    assert not layout._records_share_proven_inline_glyph_row(current, following)


def test_actual_independent_display_formula_does_not_join_with_a_paragraph():
    captured = []
    old = layout._merge_wrapped_formula_continuation_records

    def capture(records):
        captured.extend(records)
        return old(records)

    layout._merge_wrapped_formula_continuation_records = capture
    try:
        with fitz.open(FIXTURE) as doc:
            layout.collect_text_blocks(doc)
    finally:
        layout._merge_wrapped_formula_continuation_records = old
    displays = [
        r
        for r in captured
        if r.bbox[0] < 290 and 280 < r.bbox[1] < 335 and "X" in r.bare_text()
    ]
    assert displays
    for record in displays:
        i = captured.index(record)
        if i + 1 < len(captured):
            assert not layout._records_share_proven_inline_glyph_row(record, captured[i + 1])
