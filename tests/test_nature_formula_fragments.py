"""A real published page lost short prose next to formulas before vendor calls."""

from pathlib import Path

import fitz

from pdf_zh_translator.pdf_layout import prepare_translation_units, strip_sentinels


def test_nature_formula_sentence_fragments_remain_translatable():
    fixture = Path(__file__).parent / "fixtures/nature_quantum_51162_p9.pdf"
    with fitz.open(fixture) as source:
        assert "As shown in the" in source[0].get_text()
        assert "A similar" in source[0].get_text()
        units, _, _ = prepare_translation_units(source, preserve_graphics_text=True)
        text = " ".join(strip_sentinels(unit[0].text) for unit in units)
    assert "As shown in the" in text, "Source prose was skipped before reaching the vendor"
    assert "A similar" in text, "Formula suffix prose was silently preserved in English"
