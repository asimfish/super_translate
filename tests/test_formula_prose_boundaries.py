"""Translated prose between exact equations must not become a formula signature."""

from types import SimpleNamespace

import pytest

from pdf_zh_translator.pdf_layout import (
    _extract_formula_fragments,
    _missing_formula_fragments,
    _normalize_formula_fragment_for_compare,
)


@pytest.mark.parametrize(
    "spans,translated,damaged",
    [
        (["e(t) >= 0", ", so ", "b(t+1) >= b(t)+a/2"],
         "e(t) >= 0，因此 b(t+1) >= b(t)+a/2", "e(t) >= 0，因此 b(t+1) >= b(t)+a/3"),
        (["0.41", " at ", "B=5,12.5,25"],
         "0.41，当 B=5,12.5,25", "0.41，当 B=5,12.5,26"),
        (["c=1", " gives ", "4.51/9.79"],
         "c=1 时得到 4.51/9.79", "c=1 时得到 4.51/9.78"),
        (["c=1", " in only ", "4/10"],
         "c=1，仅在 4/10", "c=1，仅在 3/10"),
        (["t", " large, ", "e(t) >= e/2"],
         "t 足够大，e(t) >= e/2", "t 足够大，e(t) >= e/3"),
    ],
)
def test_prose_connectors_can_translate_but_equations_stay_exact(spans, translated, damaged):
    page = SimpleNamespace(get_text=lambda _: {"blocks": [{
        "type": 0,
        "lines": [{"spans": [{"text": text} for text in spans]}],
    }]})
    fragments = _extract_formula_fragments(page)
    assert fragments
    assert not _missing_formula_fragments(
        fragments, [_normalize_formula_fragment_for_compare(translated)]
    )
    assert _missing_formula_fragments(
        fragments, [_normalize_formula_fragment_for_compare(damaged)]
    ), fragments


def test_math_function_names_are_kept_in_formula_signatures():
    page = SimpleNamespace(get_text=lambda _: {"blocks": [{
        "type": 0,
        "lines": [{"spans": [{"text": "sin(x) = α+1"}]}],
    }]})
    fragments = _extract_formula_fragments(page)
    assert any("sin(x)" in text for text in fragments)
    assert _missing_formula_fragments(fragments, ["sin(x)=α+2"])


def test_comparison_and_purpose_prose_do_not_join_math_signatures():
    page = SimpleNamespace(get_text=lambda _: {"blocks": [{
        "type": 0,
        "lines": [{"spans": [{"text": text} for text in [
            "B=12.5, 64/100", " versus ", "59/100", " at 25; ",
            "q(0)", " to ensure ", "q(l+1) >= t",
        ]]}],
    }]})
    fragments = _extract_formula_fragments(page)
    correct = _normalize_formula_fragment_for_compare(
        "B=12.5,64/100 与 59/100；q(0) 以确保 q(l+1) >= t"
    )
    assert fragments and not _missing_formula_fragments(fragments, [correct])
    assert _missing_formula_fragments(fragments, [correct.replace("59/100", "58/100")])
