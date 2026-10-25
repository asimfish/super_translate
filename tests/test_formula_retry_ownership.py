"""Source formula delimiters and detached scripts must never reach translation."""

from pdf_zh_translator import pdf_layout as m


def test_unbalanced_interval_closer_is_protected_with_its_formula():
    source = (
        "The change is "
        + m.SENTINEL_OPEN
        + "-2.67 pp [-4.10, -1.30"
        + m.SENTINEL_CLOSE
        + "] on these families."
    )
    protected, mapping = m.protect_text(source)
    assert "[-4.10, -1.30]" in next(iter(mapping.values()))
    assert "] on" not in protected
    assert "on these families" in protected


def test_prose_parenthesis_after_complete_math_remains_translatable():
    source = "For (" + m.SENTINEL_OPEN + "x" + m.SENTINEL_CLOSE + "), we evaluate the method."
    protected, mapping = m.protect_text(source)
    assert list(mapping.values()) == ["x"]
    assert "), we evaluate" in protected


def test_inverse_trigonometric_operators_are_formula_syntax():
    for operator in ("arcsin", "arccos", "arctan"):
        assert m._formula_like_plain_span_text("= " + operator)
    assert not m._formula_like_plain_span_text("We examine the site")


def test_detached_subscript_after_same_size_superscript_uses_body_reference_size():
    exponent = {"text": "2", "font": "CMR7", "size": 7, "bbox": (120, 99, 124, 106)}
    script = {"text": "site", "font": "CMR7", "size": 7, "bbox": (120, 107, 132, 114)}
    rest = {"text": " = norm", "font": "CMR10", "size": 10, "bbox": (132, 104, 170, 114)}
    previous = m._LineRec(
        m.SENTINEL_OPEN + "^{2}" + m.SENTINEL_CLOSE,
        exponent["bbox"],
        [exponent],
        math_bboxes=[exponent["bbox"]],
        math_run_bboxes=[exponent["bbox"]],
    )
    current = m._LineRec(
        "site = norm",
        (120, 104, 170, 114),
        [script, rest],
        prose_bboxes=[script["bbox"], rest["bbox"]],
    )
    assert m._promote_detached_inline_formula_scripts([previous, current])
    assert "_{site}" in previous.text
    assert current.text == "= norm"


def test_full_sentence_next_to_equation_is_not_formula_only():
    text = (
        "Here"
        + m.SENTINEL_OPEN
        + "∆t=1/S"
        + m.SENTINEL_CLOSE
        + ", with"
        + m.SENTINEL_OPEN
        + "S=50"
        + m.SENTINEL_CLOSE
        + " steps in the reported evaluations."
    )
    line = m._LineRec(
        text,
        (100, 100, 450, 111),
        [],
        prose_bboxes=[(100, 100, 450, 111)],
        math_bboxes=[(120, 100, 160, 111)],
    )
    assert m.line_is_prose(line)
    assert not m._equation_record_is_formula_only(m._RawBlockRec([line]))


def test_unnumbered_prose_lead_to_numbered_equation_stays_translatable():
    prose = m._LineRec(
        "To replace step velocities by a common bound, observe",
        (100, 100, 350, 111),
        [],
        prose_bboxes=[(100, 100, 350, 111)],
    )
    equation = m._LineRec(
        m.SENTINEL_OPEN + "(m+1)A-mA=(1-β)A" + m.SENTINEL_CLOSE, (200, 120, 450, 133), []
    )
    assert not m._equation_record_is_formula_only(m._RawBlockRec([prose, equation]))


def test_small_script_sees_math_operator_across_empty_font_span():
    spans = [
        {"text": "site", "font": "CMR7", "size": 7, "flags": 4, "bbox": (100, 105, 112, 112)},
        {"text": " ", "font": "CMR10", "size": 10, "flags": 4, "bbox": (112, 102, 116, 112)},
        {"text": "=", "font": "CMR10", "size": 10, "flags": 5, "bbox": (116, 100, 124, 110)},
    ]
    assert m._span_is_adjacent_math_script(0, spans, line_max_size=10)
    spans[2]["text"] = "the following sentence"
    spans[2]["flags"] = 4
    assert not m._span_is_adjacent_math_script(0, spans, line_max_size=10)
