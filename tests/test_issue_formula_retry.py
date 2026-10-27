"""Word2vec's quoted operands are formulas; surrounding English is still prose."""
import pytest

from pdf_zh_translator import pdf_layout as m

FORMULA = 'X=vector("biggest")-vector("big")+vector("small")'

@pytest.mark.parametrize("prefix", ["", "例如："])
def test_quoted_formula_is_not_untranslated_english(prefix):
    assert m._is_reference_or_formula_text(FORMULA)
    assert not m._looks_like_untranslated_english(prefix + FORMULA)
    assert not m._contains_untranslated_english_run(prefix + FORMULA)
    block = m.TextBlock(0, (20, 20, 400, 40), FORMULA, 10, (0, 0, 0))
    assert not m._translated_block_still_english(block, prefix + FORMULA)


def test_formula_exemption_does_not_hide_adjacent_english_sentence():
    text = 'We evaluate the following vector analogy in our experiments: ' + FORMULA
    assert not m._is_reference_or_formula_text(text)
    assert m._looks_like_untranslated_english(text)
    block = m.TextBlock(0, (20, 20, 400, 40), text, 10, (0, 0, 0))
    assert m._translated_block_still_english(block, text)


def test_formula_only_cached_block_never_invalidates_or_calls_supplier(tmp_path, monkeypatch):
    import json

    import fitz

    from pdf_zh_translator.translators import CachedTranslator, cache_key

    source = tmp_path / "word-vector.pdf"
    with fitz.open() as document:
        document.new_page().insert_text((72, 100), FORMULA, fontsize=10)
        document.save(source)
    block = m.TextBlock(0, (72, 88, 500, 108), FORMULA, 10, (0, 0, 0))
    protected = "⟦0⟧"
    path = tmp_path / "translation-cache.jsonl"
    record = {"key": cache_key(protected), "source": protected, "translation": protected}
    path.write_text(json.dumps(record) + "\n")
    before = path.read_bytes()
    class NoSupplier:
        def translate_batch(self, texts):
            pytest.fail("An unchanged formula must not trigger a forced API retry")
    translator = CachedTranslator(NoSupplier(), path)
    monkeypatch.setattr(m, "prepare_translation_units", lambda *a, **kw: (
        [(block, protected, {protected: FORMULA})], {}, [],
    ))
    output = tmp_path / "translated.pdf"
    m.translate_pdf(source, output, translator, preserve_graphics_text=True)
    assert output.is_file()
    assert path.read_bytes() == before
    assert translator.cache[cache_key(protected)] == protected
