"""Tests for the native pdf_zh_translator engine path in the web translator."""

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app.services.translator import (
    TranslationConfig,
    _ProgressTranslator,
    _translate_sync_native,
    _use_native_engine,
)


class _EchoInner:
    def __init__(self):
        self.batches = []

    def translate_batch(self, texts):
        self.batches.append(list(texts))
        return ["译:" + text for text in texts]


class UseNativeEngineTests(unittest.TestCase):
    def test_native_engine_with_deepseek(self):
        with patch("app.core.config.settings") as settings:
            settings.translation_engine = "native"
            self.assertTrue(_use_native_engine(TranslationConfig(backend="deepseek")))

    def test_native_engine_with_openai(self):
        with patch("app.core.config.settings") as settings:
            settings.translation_engine = "native"
            self.assertTrue(_use_native_engine(TranslationConfig(backend="openai")))

    def test_google_backend_stays_on_pdf2zh(self):
        with patch("app.core.config.settings") as settings:
            settings.translation_engine = "native"
            self.assertFalse(_use_native_engine(TranslationConfig(backend="google")))

    def test_pdf2zh_engine_disables_native(self):
        with patch("app.core.config.settings") as settings:
            settings.translation_engine = "pdf2zh"
            self.assertFalse(_use_native_engine(TranslationConfig(backend="deepseek")))


class ProgressTranslatorTests(unittest.TestCase):
    def test_groups_and_reports_progress(self):
        inner = _EchoInner()
        seen = []
        wrapper = _ProgressTranslator(inner, seen.append, group_size=2)
        result = wrapper.translate_batch(["a", "b", "c"])
        self.assertEqual(result, ["译:a", "译:b", "译:c"])
        self.assertEqual(inner.batches, [["a", "b"], ["c"]])
        self.assertEqual(seen[-1], 1.0)
        self.assertTrue(all(0.0 < value <= 1.0 for value in seen))

    def test_no_callback_is_safe(self):
        wrapper = _ProgressTranslator(_EchoInner(), None, group_size=4)
        self.assertEqual(wrapper.translate_batch(["x"]), ["译:x"])


class TranslateSyncNativeTests(unittest.TestCase):
    def test_produces_mono_result(self):
        config = TranslationConfig(backend="deepseek", api_key="test-key")
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            input_pdf = tmp_path / "paper.pdf"
            input_pdf.write_bytes(b"%PDF-1.4 fake")
            output_dir = tmp_path / "out"
            output_dir.mkdir()

            def fake_translate_pdf(input_pdf, output_pdf, translator, **kwargs):
                output_pdf.write_bytes(b"%PDF-1.4 translated")

                class Report:
                    warnings = []

                return Report()

            with patch(
                "pdf_zh_translator.pdf_layout.translate_pdf",
                side_effect=fake_translate_pdf,
            ):
                result = _translate_sync_native(input_pdf, output_dir, config)

        self.assertTrue(result.success)
        self.assertIsNone(result.dual_path)
        self.assertEqual(result.mono_path.name, "paper-mono.pdf")

    def test_failure_returns_error_after_retries(self):
        config = TranslationConfig(backend="deepseek", api_key="k", max_retries=0)
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            input_pdf = tmp_path / "paper.pdf"
            input_pdf.write_bytes(b"%PDF-1.4 fake")
            output_dir = tmp_path / "out"
            output_dir.mkdir()

            with (
                patch(
                    "pdf_zh_translator.pdf_layout.translate_pdf",
                    side_effect=RuntimeError("boom"),
                ),
                self.assertRaises(RuntimeError),
            ):
                _translate_sync_native(input_pdf, output_dir, config)


if __name__ == "__main__":
    unittest.main()
