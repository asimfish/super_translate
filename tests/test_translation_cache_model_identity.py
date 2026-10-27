"""Exercise the real cache at the native supplier boundary across model changes."""

from types import SimpleNamespace

import fitz
import pytest

from app.services.translator import TranslationConfig, _translate_sync_native
from pdf_zh_translator import pdf_layout, translators


@pytest.mark.parametrize("terminology", [None, {"electron": "电子"}])
def test_native_cache_separates_models_and_reuses_same_model(tmp_path, monkeypatch, terminology):
    source = tmp_path / "paper.pdf"
    with fitz.open() as doc:
        doc.new_page().insert_text((72, 100), "A sentence shared across models")
        doc.save(source)
    calls = []
    translations = []

    def supplier(self, texts):
        calls.append(self.model)
        return [self.model + ":译文" for _ in texts]

    def render(input_pdf, output_pdf, translator, **kwargs):
        translations.extend(translator.translate_batch(["A sentence shared across models"]))
        output_pdf.write_bytes(source.read_bytes())
        return SimpleNamespace(warnings=[])

    monkeypatch.setattr(translators.VendorTranslator, "translate_batch", supplier)
    monkeypatch.setattr(pdf_layout, "translate_pdf", render)
    monkeypatch.setattr(pdf_layout, "verify_translation", lambda *a, **kw: [])
    for model in ("deepseek-v4-pro", "deepseek-v4-pro", "deepseek-v4-flash"):
        result = _translate_sync_native(
            source, tmp_path / "out",
            TranslationConfig(
                backend="deepseek", model=model, api_key="test", max_retries=0,
                terminology=terminology,
            ),
        )
        assert result.success
    assert calls == ["deepseek-v4-pro", "deepseek-v4-flash"]
    assert translations == [
        "deepseek-v4-pro:译文", "deepseek-v4-pro:译文", "deepseek-v4-flash:译文",
    ]
