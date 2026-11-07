"""Cross-references translate; equivalent macrons keep their mathematical role."""

from types import SimpleNamespace

from pdf_zh_translator.pdf_layout import (
    _extract_formula_fragments,
    _missing_formula_fragments,
    _normalize_formula_fragment_for_compare,
)


def test_section_reference_does_not_become_part_of_formula():
    page = SimpleNamespace(get_text=lambda _: {"blocks": [{
        "type": 0,
        "lines": [{"spans": [{"text": text} for text in [
            "λ = ¯λ + β", " (Section 4.2), where ", "¯λ >= 0",
        ]]}],
    }]})
    fragments = _extract_formula_fragments(page)
    correct = _normalize_formula_fragment_for_compare("λ=̄λ+β（第4.2节），其中̄λ>=0")
    assert fragments and not _missing_formula_fragments(fragments, [correct])
    assert _missing_formula_fragments(fragments, [correct.replace("+β", "+γ")])


def test_macron_equivalence_does_not_accept_a_lost_decoration():
    assert _normalize_formula_fragment_for_compare("¯λ⋆k") == (
        _normalize_formula_fragment_for_compare("̄λ⋆k")
    )
    assert _missing_formula_fragments(["¯λ⋆k"], ["λ⋆k"])
