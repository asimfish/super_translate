"""Tests for app.services.translator module."""

import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from app.services.translator import (
    QualityPreset,
    TranslationConfig,
    TranslationResult,
    QUALITY_PRESETS,
    translate_pdf_sync,
)


class TestQualityPreset(unittest.TestCase):
    """Test QualityPreset enum."""

    def test_fast_preset_value(self):
        self.assertEqual(QualityPreset.FAST.value, "fast")

    def test_balanced_preset_value(self):
        self.assertEqual(QualityPreset.BALANCED.value, "balanced")

    def test_quality_preset_value(self):
        self.assertEqual(QualityPreset.QUALITY.value, "quality")

    def test_preset_from_string(self):
        self.assertEqual(QualityPreset("fast"), QualityPreset.FAST)
        self.assertEqual(QualityPreset("balanced"), QualityPreset.BALANCED)
        self.assertEqual(QualityPreset("quality"), QualityPreset.QUALITY)

    def test_invalid_preset_raises(self):
        with self.assertRaises(ValueError):
            QualityPreset("invalid")


class TestTranslationConfig(unittest.TestCase):
    """Test TranslationConfig dataclass."""

    def test_default_values(self):
        config = TranslationConfig()
        self.assertEqual(config.backend, "deepseek")
        self.assertEqual(config.lang_in, "en")
        self.assertEqual(config.lang_out, "zh")
        self.assertEqual(config.api_key, "")
        self.assertEqual(config.base_url, "")
        self.assertEqual(config.model, "")
        self.assertEqual(config.quality, QualityPreset.BALANCED)
        self.assertEqual(config.max_retries, 2)
        self.assertEqual(config.threads, 4)

    def test_custom_values(self):
        config = TranslationConfig(
            backend="openai",
            lang_in="fr",
            lang_out="en",
            api_key="test-key",
            base_url="https://api.example.com",
            model="gpt-4",
            quality=QualityPreset.QUALITY,
            max_retries=3,
            threads=8,
        )
        self.assertEqual(config.backend, "openai")
        self.assertEqual(config.lang_in, "fr")
        self.assertEqual(config.lang_out, "en")
        self.assertEqual(config.api_key, "test-key")
        self.assertEqual(config.base_url, "https://api.example.com")
        self.assertEqual(config.model, "gpt-4")
        self.assertEqual(config.quality, QualityPreset.QUALITY)
        self.assertEqual(config.max_retries, 3)
        self.assertEqual(config.threads, 8)

    def test_frozen(self):
        config = TranslationConfig()
        with self.assertRaises(AttributeError):
            config.backend = "openai"


class TestTranslationResult(unittest.TestCase):
    """Test TranslationResult class."""

    def test_success_with_paths(self):
        result = TranslationResult(
            mono_path=Path("/tmp/mono.pdf"),
            dual_path=Path("/tmp/dual.pdf"),
        )
        self.assertTrue(result.success)
        self.assertEqual(result.mono_path, Path("/tmp/mono.pdf"))
        self.assertEqual(result.dual_path, Path("/tmp/dual.pdf"))
        self.assertIsNone(result.error)

    def test_success_with_mono_only(self):
        result = TranslationResult(mono_path=Path("/tmp/mono.pdf"))
        self.assertTrue(result.success)
        self.assertIsNone(result.dual_path)

    def test_failure_with_error(self):
        result = TranslationResult(error="Translation failed")
        self.assertFalse(result.success)
        self.assertIsNone(result.mono_path)
        self.assertIsNone(result.dual_path)
        self.assertEqual(result.error, "Translation failed")

    def test_failure_without_mono(self):
        result = TranslationResult(dual_path=Path("/tmp/dual.pdf"))
        self.assertFalse(result.success)

    def test_failure_with_error_and_mono(self):
        result = TranslationResult(
            mono_path=Path("/tmp/mono.pdf"),
            error="Partial failure",
        )
        self.assertFalse(result.success)


class TestQualityPresets(unittest.TestCase):
    """Test QUALITY_PRESETS configuration."""

    def test_fast_preset_config(self):
        preset = QUALITY_PRESETS[QualityPreset.FAST]
        self.assertFalse(preset["compatible"])
        self.assertFalse(preset["skip_subset_fonts"])
        self.assertIsNone(preset["prompt"])
        self.assertEqual(preset["fallback_backend"], "google")
        self.assertEqual(preset["threads"], 8)

    def test_balanced_preset_config(self):
        preset = QUALITY_PRESETS[QualityPreset.BALANCED]
        self.assertTrue(preset["compatible"])
        self.assertTrue(preset["skip_subset_fonts"])
        self.assertIsNotNone(preset["prompt"])
        self.assertEqual(preset["fallback_backend"], "google")
        self.assertEqual(preset["threads"], 4)

    def test_quality_preset_config(self):
        preset = QUALITY_PRESETS[QualityPreset.QUALITY]
        self.assertTrue(preset["compatible"])
        self.assertTrue(preset["skip_subset_fonts"])
        self.assertIsNotNone(preset["prompt"])
        self.assertEqual(preset["fallback_backend"], "google")
        self.assertEqual(preset["threads"], 2)

    def test_all_presets_have_required_keys(self):
        required_keys = [
            "compatible",
            "skip_subset_fonts",
            "prompt",
            "vfont",
            "vchar",
            "fallback_backend",
            "threads",
        ]
        for preset in QUALITY_PRESETS.values():
            for key in required_keys:
                self.assertIn(key, preset)

    def test_balanced_prompt_contains_key_rules(self):
        preset = QUALITY_PRESETS[QualityPreset.BALANCED]
        prompt = preset["prompt"].safe_substitute(text="")
        self.assertIn("纯中文", prompt)
        self.assertIn("公式保护", prompt)
        self.assertIn("引用保护", prompt)

    def test_quality_prompt_contains_key_rules(self):
        preset = QUALITY_PRESETS[QualityPreset.QUALITY]
        prompt = preset["prompt"].safe_substitute(text="")
        self.assertIn("纯中文", prompt)
        self.assertIn("代码", prompt)
        self.assertIn("图表", prompt)
        self.assertIn("长句拆分", prompt)

    def test_fast_preset_has_no_prompt(self):
        preset = QUALITY_PRESETS[QualityPreset.FAST]
        self.assertIsNone(preset["prompt"])

    def test_balanced_vfont_matches_math_fonts(self):
        import re
        pattern = QUALITY_PRESETS[QualityPreset.BALANCED]["vfont"]
        self.assertTrue(re.search(pattern, "CMMI10"))  # Computer Modern Math Italic
        self.assertTrue(re.search(pattern, "CMSY10"))  # Computer Modern Symbols
        self.assertTrue(re.search(pattern, "STIXMath"))
        self.assertFalse(re.search(pattern, "CMR10"))  # CM Roman is text, not math

    def test_quality_vchar_matches_greek(self):
        import re
        pattern = QUALITY_PRESETS[QualityPreset.QUALITY]["vchar"]
        self.assertTrue(re.search(pattern, "α"))
        self.assertTrue(re.search(pattern, "∑"))
        self.assertTrue(re.search(pattern, "∫"))


class TestTranslatePdfSync(unittest.TestCase):
    """Test translate_pdf_sync function."""

    @patch("pdf2zh.translate")
    @patch("app.services.translator.get_model")
    def test_google_translate_no_api_key(self, mock_get_model, mock_translate):
        """Test that Google Translate works without API key."""
        mock_get_model.return_value = MagicMock()
        mock_translate.return_value = None

        config = TranslationConfig(
            backend="google",
            api_key="",
            quality=QualityPreset.FAST,
        )

        with patch("pathlib.Path.exists", return_value=True):
            with patch("pathlib.Path.glob", return_value=[]):
                result = translate_pdf_sync(
                    Path("/tmp/input.pdf"),
                    Path("/tmp/output"),
                    config,
                )
                # Should succeed (no error)
                self.assertIsNone(result.error)

    @patch("pdf2zh.translate")
    @patch("app.services.translator.get_model")
    def test_deepseek_no_api_key_falls_back(self, mock_get_model, mock_translate):
        """Test that DeepSeek falls back to Google when no API key."""
        mock_get_model.return_value = MagicMock()
        mock_translate.return_value = None

        config = TranslationConfig(
            backend="deepseek",
            api_key="",
            quality=QualityPreset.BALANCED,
        )

        with patch("pathlib.Path.exists", return_value=True):
            with patch("pathlib.Path.glob", return_value=[]):
                result = translate_pdf_sync(
                    Path("/tmp/input.pdf"),
                    Path("/tmp/output"),
                    config,
                )
                # Should succeed with fallback
                self.assertIsNone(result.error)

    @patch("pdf2zh.translate")
    @patch("app.services.translator.get_model")
    def test_translation_error_returns_error(self, mock_get_model, mock_translate):
        """Test that translation errors are captured."""
        mock_get_model.return_value = MagicMock()
        mock_translate.side_effect = Exception("API error")

        config = TranslationConfig(
            backend="deepseek",
            api_key="test-key",
            max_retries=0,  # Don't retry for test
        )

        result = translate_pdf_sync(
            Path("/tmp/input.pdf"),
            Path("/tmp/output"),
            config,
        )
        self.assertIsNotNone(result.error)
        self.assertIn("API error", result.error)

    def test_config_with_google_backend(self):
        """Test that Google backend works without API key."""
        config = TranslationConfig(backend="google")
        self.assertEqual(config.backend, "google")
        self.assertEqual(config.api_key, "")

    def test_config_with_deepseek_backend(self):
        """Test DeepSeek backend configuration."""
        config = TranslationConfig(
            backend="deepseek",
            api_key="test-key",
            model="deepseek-v4-pro",
        )
        self.assertEqual(config.backend, "deepseek")
        self.assertEqual(config.api_key, "test-key")
        self.assertEqual(config.model, "deepseek-v4-pro")


class TestSanitizeError(unittest.TestCase):
    """Test _sanitize_error function."""

    def test_removes_unix_paths(self):
        from app.services.translator import _sanitize_error
        err = FileNotFoundError("/Users/admin/project/data/papers/test.pdf not found")
        result = _sanitize_error(err)
        self.assertNotIn("/Users/admin", result)
        self.assertIn("[path]", result)

    def test_removes_windows_paths(self):
        from app.services.translator import _sanitize_error
        err = Exception("Error at C:\\Users\\admin\\file.txt")
        result = _sanitize_error(err)
        self.assertNotIn("C:\\Users", result)

    def test_truncates_long_messages(self):
        from app.services.translator import _sanitize_error
        err = Exception("x" * 500)
        result = _sanitize_error(err)
        self.assertLessEqual(len(result), 210)  # 200 + "..."

    def test_preserves_short_messages(self):
        from app.services.translator import _sanitize_error
        err = ValueError("Invalid format")
        result = _sanitize_error(err)
        self.assertIn("Invalid format", result)

    def test_handles_api_errors(self):
        from app.services.translator import _sanitize_error
        err = Exception("DeepSeek API error: 401 Unauthorized")
        result = _sanitize_error(err)
        self.assertIn("401", result)
        self.assertIn("Unauthorized", result)


if __name__ == "__main__":
    unittest.main()
