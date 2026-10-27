"""Keep cache replay diagnostics out of immutable repository fixtures."""

from pathlib import Path

import pytest

from pdf_zh_translator.translators import CacheOnlyTranslator


@pytest.fixture(autouse=True)
def isolate_fixture_cache_diagnostics(monkeypatch, tmp_path):
    fixture_root = (Path(__file__).parent / "fixtures").resolve()
    original_init = CacheOnlyTranslator.__init__

    def initialize_with_isolated_diagnostics(instance, cache_file, *args, **kwargs):
        original_init(instance, cache_file, *args, **kwargs)
        source = Path(cache_file).resolve()
        if source.is_relative_to(fixture_root):
            # All source records have already been read from their original
            # bytes. Only the diagnostic output path changes; translation,
            # cache contents, fixture PDFs and golden assertions do not.
            instance.cache_file = tmp_path / source.name

    monkeypatch.setattr(CacheOnlyTranslator, "__init__", initialize_with_isolated_diagnostics)
