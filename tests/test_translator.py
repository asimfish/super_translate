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

    @patch("pdf2zh.translate")
    @patch("app.services.translator.get_model")
    def test_openai_no_api_key_falls_back(self, mock_get_model, mock_translate):
        """Test that OpenAI falls back to Google when no API key."""
        mock_get_model.return_value = MagicMock()
        mock_translate.return_value = None

        config = TranslationConfig(
            backend="openai",
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
                self.assertIsNone(result.error)

    @patch("pdf2zh.translate")
    @patch("app.services.translator.get_model")
    def test_openai_uses_env_var_key(self, mock_get_model, mock_translate):
        """Test that OpenAI reads API key from env when config has none."""
        import os
        mock_get_model.return_value = MagicMock()
        captured_envs = {}

        def capture_translate(*args, **kwargs):
            captured_envs.update(kwargs.get("envs") or {})
            return None

        mock_translate.side_effect = capture_translate

        os.environ["OPENAI_API_KEY"] = "env-key-123"
        try:
            config = TranslationConfig(backend="openai", api_key="", quality=QualityPreset.BALANCED)

            with patch("pathlib.Path.exists", return_value=True):
                with patch("pathlib.Path.glob", return_value=[]):
                    translate_pdf_sync(Path("/tmp/input.pdf"), Path("/tmp/output"), config)

            self.assertEqual(captured_envs.get("OPENAI_API_KEY"), "env-key-123")
        finally:
            os.environ.pop("OPENAI_API_KEY", None)

    @patch("pdf2zh.translate")
    @patch("app.services.translator.get_model")
    def test_deepseek_env_vars_set(self, mock_get_model, mock_translate):
        """Test that DeepSeek API key is passed via envs parameter."""
        mock_get_model.return_value = MagicMock()

        captured_envs = {}

        def capture_translate(*args, **kwargs):
            captured_envs.update(kwargs.get("envs") or {})
            return None

        mock_translate.side_effect = capture_translate

        config = TranslationConfig(
            backend="deepseek",
            api_key="test-key-123",
            model="deepseek-v4",
            quality=QualityPreset.BALANCED,
        )

        with patch("pathlib.Path.exists", return_value=True):
            with patch("pathlib.Path.glob", return_value=[]):
                translate_pdf_sync(Path("/tmp/input.pdf"), Path("/tmp/output"), config)

        self.assertEqual(captured_envs.get("DEEPSEEK_API_KEY"), "test-key-123")
        self.assertEqual(captured_envs.get("DEEPSEEK_MODEL"), "deepseek-v4")

    @patch("pdf2zh.translate")
    @patch("app.services.translator.get_model")
    def test_openai_env_vars_set(self, mock_get_model, mock_translate):
        """Test that OpenAI env vars are passed via envs parameter."""
        mock_get_model.return_value = MagicMock()
        captured_envs = {}

        def capture_translate(*args, **kwargs):
            captured_envs.update(kwargs.get("envs") or {})
            return None

        mock_translate.side_effect = capture_translate

        config = TranslationConfig(
            backend="openai",
            api_key="sk-test",
            base_url="https://custom.openai.com/v1",
            model="gpt-4o",
            quality=QualityPreset.BALANCED,
        )

        with patch("pathlib.Path.exists", return_value=True):
            with patch("pathlib.Path.glob", return_value=[]):
                translate_pdf_sync(Path("/tmp/input.pdf"), Path("/tmp/output"), config)

        self.assertEqual(captured_envs.get("OPENAI_API_KEY"), "sk-test")
        self.assertEqual(captured_envs.get("OPENAI_BASE_URL"), "https://custom.openai.com/v1")
        self.assertEqual(captured_envs.get("OPENAI_MODEL"), "gpt-4o")

    @patch("pdf2zh.translate")
    @patch("app.services.translator.get_model")
    def test_deepseek_model_env_var(self, mock_get_model, mock_translate):
        """Test that DeepSeek API key and model are passed via envs."""
        mock_get_model.return_value = MagicMock()
        captured_envs = {}

        def capture_translate(*args, **kwargs):
            captured_envs.update(kwargs.get("envs") or {})
            return None

        mock_translate.side_effect = capture_translate

        config = TranslationConfig(
            backend="deepseek",
            api_key="ds-key",
            model="deepseek-v4-pro",
            quality=QualityPreset.BALANCED,
        )

        with patch("pathlib.Path.exists", return_value=True):
            with patch("pathlib.Path.glob", return_value=[]):
                translate_pdf_sync(Path("/tmp/input.pdf"), Path("/tmp/output"), config)

        self.assertEqual(captured_envs.get("DEEPSEEK_API_KEY"), "ds-key")
        self.assertEqual(captured_envs.get("DEEPSEEK_MODEL"), "deepseek-v4-pro")

    @patch("pdf2zh.translate")
    @patch("app.services.translator.get_model")
    def test_retry_on_failure_then_succeed(self, mock_get_model, mock_translate):
        """Test that translation retries on failure and succeeds on second attempt."""
        mock_get_model.return_value = MagicMock()
        call_count = [0]

        def fail_then_succeed(*args, **kwargs):
            call_count[0] += 1
            if call_count[0] == 1:
                raise Exception("Temporary API error")
            # Second call succeeds (returns None)

        mock_translate.side_effect = fail_then_succeed

        config = TranslationConfig(
            backend="google",
            quality=QualityPreset.FAST,
            max_retries=2,
        )

        with patch("pathlib.Path.exists", return_value=True):
            with patch("pathlib.Path.glob", return_value=[]):
                result = translate_pdf_sync(
                    Path("/tmp/input.pdf"),
                    Path("/tmp/output"),
                    config,
                )
                self.assertIsNone(result.error)
                self.assertEqual(call_count[0], 2)

    @patch("pdf2zh.translate")
    @patch("app.services.translator.get_model")
    def test_retry_cleans_up_partial_files(self, mock_get_model, mock_translate):
        """Test that partial output files are deleted on retry failure."""
        import tempfile
        mock_get_model.return_value = MagicMock()
        call_count = [0]
        output_dir_ref = [None]

        def fail_then_succeed(*args, **kwargs):
            call_count[0] += 1
            if call_count[0] == 1:
                raise Exception("Temporary API error")
            # Second call succeeds: create the expected output file
            if output_dir_ref[0]:
                (output_dir_ref[0] / "input-mono.pdf").write_bytes(b"translated")

        mock_translate.side_effect = fail_then_succeed

        config = TranslationConfig(
            backend="google",
            quality=QualityPreset.FAST,
            max_retries=2,
        )

        with tempfile.TemporaryDirectory() as tmpdir:
            output_dir = Path(tmpdir) / "output"
            output_dir.mkdir()
            output_dir_ref[0] = output_dir
            # Create partial output files that should be cleaned up
            partial1 = output_dir / "partial1.pdf"
            partial2 = output_dir / "partial2.txt"
            partial1.write_bytes(b"partial")
            partial2.write_bytes(b"partial")

            result = translate_pdf_sync(
                Path(tmpdir) / "input.pdf",
                output_dir,
                config,
            )
            self.assertIsNone(result.error)
            self.assertEqual(call_count[0], 2)
            # Partial files should have been cleaned up
            self.assertFalse(partial1.exists())
            self.assertFalse(partial2.exists())

    @patch("app.services.layout_fix.fix_translated_layout")
    @patch("pdf2zh.translate")
    @patch("app.services.translator.get_model")
    def test_layout_fix_called_on_output(self, mock_get_model, mock_translate, mock_fix):
        """Test that layout fix is applied to translated output files."""
        import tempfile
        mock_get_model.return_value = MagicMock()
        mock_translate.return_value = None
        mock_fix.return_value = True

        config = TranslationConfig(backend="google", quality=QualityPreset.FAST)

        with tempfile.TemporaryDirectory() as tmpdir:
            output_dir = Path(tmpdir) / "output"
            output_dir.mkdir()
            mono = output_dir / "input-mono.pdf"
            dual = output_dir / "input-dual.pdf"
            mono.write_bytes(b"mono content")
            dual.write_bytes(b"dual content")

            result = translate_pdf_sync(
                Path(tmpdir) / "input.pdf",
                output_dir,
                config,
            )
            self.assertTrue(result.success)
            # Layout fix should have been called on both files
            self.assertEqual(mock_fix.call_count, 2)

    @patch("app.services.layout_fix.fix_translated_layout")
    @patch("pdf2zh.translate")
    @patch("app.services.translator.get_model")
    def test_layout_fix_failure_is_non_fatal(self, mock_get_model, mock_translate, mock_fix):
        """Test that layout fix failure doesn't break translation."""
        import tempfile
        mock_get_model.return_value = MagicMock()
        mock_translate.return_value = None
        mock_fix.side_effect = Exception("Font not found")

        config = TranslationConfig(backend="google", quality=QualityPreset.FAST)

        with tempfile.TemporaryDirectory() as tmpdir:
            output_dir = Path(tmpdir) / "output"
            output_dir.mkdir()
            mono = output_dir / "input-mono.pdf"
            mono.write_bytes(b"mono content")

            result = translate_pdf_sync(
                Path(tmpdir) / "input.pdf",
                output_dir,
                config,
            )
            # Translation should still succeed despite layout fix failure
            self.assertTrue(result.success)

    @patch("pdf2zh.translate")
    @patch("app.services.translator.get_model")
    def test_all_retries_exhausted(self, mock_get_model, mock_translate):
        """Test that error is returned when all retries are exhausted."""
        mock_get_model.return_value = MagicMock()
        mock_translate.side_effect = Exception("Persistent API error")

        config = TranslationConfig(
            backend="google",
            quality=QualityPreset.FAST,
            max_retries=1,
        )

        with patch("pathlib.Path.exists", return_value=True):
            with patch("pathlib.Path.glob", return_value=[]):
                result = translate_pdf_sync(
                    Path("/tmp/input.pdf"),
                    Path("/tmp/output"),
                    config,
                )
                self.assertIsNotNone(result.error)
                self.assertIn("Persistent API error", result.error)
                # Should have been called 2 times (initial + 1 retry)
                self.assertEqual(mock_translate.call_count, 2)

    @patch("pdf2zh.translate")
    @patch("app.services.translator.get_model")
    def test_finds_mono_dual_output_files(self, mock_get_model, mock_translate):
        """Test that translated/dual output files are correctly found."""
        import tempfile
        mock_get_model.return_value = MagicMock()
        mock_translate.return_value = None

        with tempfile.TemporaryDirectory() as tmpdir:
            output_dir = Path(tmpdir) / "output"
            output_dir.mkdir()
            (output_dir / "paper-mono.pdf").write_bytes(b"mono")
            (output_dir / "paper-dual.pdf").write_bytes(b"dual")

            config = TranslationConfig(backend="google", quality=QualityPreset.FAST)

            result = translate_pdf_sync(
                Path(tmpdir) / "paper.pdf",
                output_dir,
                config,
            )
            self.assertTrue(result.success)
            self.assertEqual(result.mono_path.name, "paper-mono.pdf")
            self.assertEqual(result.dual_path.name, "paper-dual.pdf")

    @patch("pdf2zh.translate")
    @patch("app.services.translator.get_model")
    def test_finds_alternative_output_names(self, mock_get_model, mock_translate):
        """Test fallback when expected file names don't match."""
        import tempfile
        mock_get_model.return_value = MagicMock()
        mock_translate.return_value = None

        with tempfile.TemporaryDirectory() as tmpdir:
            output_dir = Path(tmpdir) / "output"
            output_dir.mkdir()
            (output_dir / "translated_mono_v2.pdf").write_bytes(b"mono")
            (output_dir / "bilingual_dual.pdf").write_bytes(b"dual")

            config = TranslationConfig(backend="google", quality=QualityPreset.FAST)

            result = translate_pdf_sync(
                Path(tmpdir) / "paper.pdf",
                output_dir,
                config,
            )
            self.assertTrue(result.success)
            self.assertIsNotNone(result.mono_path)
            self.assertIsNotNone(result.dual_path)

    @patch("pdf2zh.translate")
    @patch("app.services.translator.get_model")
    def test_fallback_to_any_pdf_when_no_mono_dual(self, mock_get_model, mock_translate):
        """Test fallback to any PDF when no mono/dual files found."""
        import tempfile
        mock_get_model.return_value = MagicMock()
        mock_translate.return_value = None

        with tempfile.TemporaryDirectory() as tmpdir:
            output_dir = Path(tmpdir) / "output"
            output_dir.mkdir()
            (output_dir / "result.pdf").write_bytes(b"result")

            config = TranslationConfig(backend="google", quality=QualityPreset.FAST)

            result = translate_pdf_sync(
                Path(tmpdir) / "paper.pdf",
                output_dir,
                config,
            )
            self.assertTrue(result.success)
            self.assertEqual(result.mono_path.name, "result.pdf")

    @patch("pdf2zh.translate")
    @patch("app.services.translator.get_model")
    def test_no_output_files_returns_error(self, mock_get_model, mock_translate):
        """Test error when pdf2zh produces no output files."""
        import tempfile
        mock_get_model.return_value = MagicMock()
        mock_translate.return_value = None

        with tempfile.TemporaryDirectory() as tmpdir:
            output_dir = Path(tmpdir) / "output"
            output_dir.mkdir()

            config = TranslationConfig(backend="google", quality=QualityPreset.FAST)

            result = translate_pdf_sync(
                Path(tmpdir) / "paper.pdf",
                output_dir,
                config,
            )
            self.assertFalse(result.success)
            self.assertIn("no output", result.error)

    @patch("pdf2zh.translate")
    @patch("app.services.translator.get_model")
    def test_callback_with_two_args(self, mock_get_model, mock_translate):
        """Test pdf2zh callback with (current, total) arguments."""
        import tempfile
        mock_get_model.return_value = MagicMock()
        mock_translate.return_value = None

        config = TranslationConfig(backend="google", quality=QualityPreset.FAST)

        with tempfile.TemporaryDirectory() as tmpdir:
            output_dir = Path(tmpdir) / "output"
            output_dir.mkdir()
            (output_dir / "input-mono.pdf").write_bytes(b"translated")

            translate_pdf_sync(
                Path(tmpdir) / "input.pdf",
                output_dir,
                config,
            )

            # Extract the callback that was passed to pdf2zh.translate
            call_kwargs = mock_translate.call_args
            callback = call_kwargs.kwargs.get("callback") or call_kwargs[1].get("callback")
            if callback and callable(callback):
                # Simulate pdf2zh calling the callback with (current, total)
                callback(5, 10)
                callback(10, 10)

    @patch("pdf2zh.translate")
    @patch("app.services.translator.get_model")
    def test_callback_with_one_arg(self, mock_get_model, mock_translate):
        """Test pdf2zh callback with single (percentage) argument."""
        import tempfile
        mock_get_model.return_value = MagicMock()
        mock_translate.return_value = None

        config = TranslationConfig(backend="google", quality=QualityPreset.FAST)

        with tempfile.TemporaryDirectory() as tmpdir:
            output_dir = Path(tmpdir) / "output"
            output_dir.mkdir()
            (output_dir / "input-mono.pdf").write_bytes(b"translated")

            translate_pdf_sync(
                Path(tmpdir) / "input.pdf",
                output_dir,
                config,
            )

            call_kwargs = mock_translate.call_args
            callback = call_kwargs.kwargs.get("callback") or call_kwargs[1].get("callback")
            if callback and callable(callback):
                # Single arg (percentage)
                callback(0.5)
                callback(1.0)

    @patch("pdf2zh.translate")
    @patch("app.services.translator.get_model")
    def test_callback_with_no_args_is_noop(self, mock_get_model, mock_translate):
        """Test pdf2zh callback with no arguments returns early."""
        import tempfile
        mock_get_model.return_value = MagicMock()
        mock_translate.return_value = None

        config = TranslationConfig(backend="google", quality=QualityPreset.FAST)

        with tempfile.TemporaryDirectory() as tmpdir:
            output_dir = Path(tmpdir) / "output"
            output_dir.mkdir()
            (output_dir / "input-mono.pdf").write_bytes(b"translated")

            translate_pdf_sync(
                Path(tmpdir) / "input.pdf",
                output_dir,
                config,
            )

            call_kwargs = mock_translate.call_args
            callback = call_kwargs.kwargs.get("callback") or call_kwargs[1].get("callback")
            if callback and callable(callback):
                # No args — should return early without error
                callback()

    @patch("pdf2zh.translate")
    @patch("app.services.translator.get_model")
    def test_os_environ_not_mutated(self, mock_get_model, mock_translate):
        """Test that os.environ is NOT mutated during translation."""
        import os
        mock_get_model.return_value = MagicMock()
        mock_translate.return_value = None

        # Ensure env var is not set
        os.environ.pop("DEEPSEEK_API_KEY", None)

        config = TranslationConfig(
            backend="deepseek",
            api_key="temp-key",
            quality=QualityPreset.BALANCED,
        )

        with patch("pathlib.Path.exists", return_value=True):
            with patch("pathlib.Path.glob", return_value=[]):
                translate_pdf_sync(Path("/tmp/input.pdf"), Path("/tmp/output"), config)

        # os.environ should NOT contain the temp key
        self.assertIsNone(os.environ.get("DEEPSEEK_API_KEY"))


class TestSanitizeError(unittest.TestCase):
    """Test sanitize_error function."""

    def test_removes_unix_paths(self):
        from app.services.translator import sanitize_error
        err = FileNotFoundError("/Users/admin/project/data/papers/test.pdf not found")
        result = sanitize_error(err)
        self.assertNotIn("/Users/admin", result)
        self.assertIn("[path]", result)

    def test_removes_windows_paths(self):
        from app.services.translator import sanitize_error
        err = Exception("Error at C:\\Users\\admin\\file.txt")
        result = sanitize_error(err)
        self.assertNotIn("C:\\Users", result)

    def test_truncates_long_messages(self):
        from app.services.translator import sanitize_error
        err = Exception("x" * 500)
        result = sanitize_error(err)
        self.assertLessEqual(len(result), 210)  # 200 + "..."

    def test_preserves_short_messages(self):
        from app.services.translator import sanitize_error
        err = ValueError("Invalid format")
        result = sanitize_error(err)
        self.assertIn("Invalid format", result)

    def test_handles_api_errors(self):
        from app.services.translator import sanitize_error
        err = Exception("DeepSeek API error: 401 Unauthorized")
        result = sanitize_error(err)
        self.assertIn("401", result)
        self.assertIn("Unauthorized", result)

    def test_removes_ip_addresses(self):
        from app.services.translator import sanitize_error
        err = Exception("Timeout connecting to 192.168.1.100:3306")
        result = sanitize_error(err)
        self.assertNotIn("192.168.1.100", result)
        self.assertNotIn("3306", result)
        self.assertIn("[ip]", result)

    def test_removes_hostnames_with_ports(self):
        from app.services.translator import sanitize_error
        err = Exception("Connection to api.deepseek.com:443 failed")
        result = sanitize_error(err)
        self.assertNotIn("api.deepseek.com", result)
        self.assertIn("[host]", result)

    def test_removes_ipv6_bracketed(self):
        from app.services.translator import sanitize_error
        err = Exception("Connection to [::1]:8080 failed")
        result = sanitize_error(err)
        self.assertNotIn("[::1]", result)
        self.assertNotIn("8080", result)
        self.assertIn("[ip]", result)

    def test_removes_ipv6_bare(self):
        from app.services.translator import sanitize_error
        err = Exception("Connection to 2001:db8::1:443 failed")
        result = sanitize_error(err)
        self.assertNotIn("2001:db8::1", result)
        self.assertIn("[ip]", result)

    def test_removes_localhost_with_port(self):
        from app.services.translator import sanitize_error
        err = Exception("Connection refused at localhost:8080")
        result = sanitize_error(err)
        self.assertNotIn("localhost:8080", result)
        self.assertIn("[host]", result)

    def test_removes_bearer_token(self):
        from app.services.translator import sanitize_error
        err = Exception("HTTP 401: Authorization: Bearer sk-abc123def456ghi789jkl012mno345")
        result = sanitize_error(err)
        self.assertNotIn("sk-abc123def456ghi789jkl012mno345", result)
        self.assertIn("[redacted]", result)

    def test_removes_sk_prefixed_key(self):
        from app.services.translator import sanitize_error
        err = Exception("Invalid API key sk-proj-abc123def456ghi789jkl012mno345pqr678")
        result = sanitize_error(err)
        self.assertNotIn("sk-proj-", result)
        self.assertIn("[redacted]", result)

    def test_removes_api_key_query_param(self):
        from app.services.translator import sanitize_error
        err = Exception("Request failed: api_key=sk-secret123abc456def789")
        result = sanitize_error(err)
        self.assertNotIn("sk-secret123abc456def789", result)
        self.assertIn("[redacted]", result)

    def test_removes_env_var_api_key(self):
        from app.services.translator import sanitize_error
        err = Exception("DEEPSEEK_API_KEY=sk-abc123def456ghi789jkl012mno345pqr678stu901")
        result = sanitize_error(err)
        self.assertNotIn("sk-abc123def456ghi789", result)
        self.assertIn("[redacted]", result)

    def test_removes_openai_key_in_error(self):
        from app.services.translator import sanitize_error
        err = Exception("OPENAI_API_KEY=sk-proj-abc123def456ghi789jkl012")
        result = sanitize_error(err)
        self.assertNotIn("sk-proj-abc123", result)
        self.assertIn("[redacted]", result)

    def test_removes_bearer_short_token(self):
        from app.services.translator import sanitize_error
        err = Exception("Auth failed: Bearer abc123xyz")
        result = sanitize_error(err)
        self.assertNotIn("abc123xyz", result)
        self.assertIn("[redacted]", result)

    def test_removes_aws_access_key(self):
        from app.services.translator import sanitize_error
        err = Exception("AWS error with key AKIAIOSFODNN7EXAMPLE")
        result = sanitize_error(err)
        self.assertNotIn("AKIAIOSFODNN7EXAMPLE", result)
        self.assertIn("[redacted]", result)

    def test_removes_github_token(self):
        from app.services.translator import sanitize_error
        err = Exception("GitHub auth failed: ghp_ABCDEFGHIJKLMNOPQRSTUVWXYZabcdef12")
        result = sanitize_error(err)
        self.assertNotIn("ghp_ABCDEF", result)
        self.assertIn("[redacted]", result)


class TestResolveService(unittest.TestCase):
    """Test _resolve_service function."""

    def test_google_passthrough(self):
        from app.services.translator import _resolve_service
        config = TranslationConfig(backend="google")
        self.assertEqual(_resolve_service(config, "google"), "google")

    def test_deepseek_with_key(self):
        from app.services.translator import _resolve_service
        config = TranslationConfig(backend="deepseek", api_key="test-key")
        self.assertEqual(_resolve_service(config, "google"), "deepseek")

    def test_deepseek_no_key_falls_back(self):
        from app.services.translator import _resolve_service
        import os
        os.environ.pop("DEEPSEEK_API_KEY", None)
        config = TranslationConfig(backend="deepseek", api_key="")
        self.assertEqual(_resolve_service(config, "google"), "google")

    def test_openai_no_key_falls_back(self):
        from app.services.translator import _resolve_service
        import os
        os.environ.pop("OPENAI_API_KEY", None)
        config = TranslationConfig(backend="openai", api_key="")
        self.assertEqual(_resolve_service(config, "deepl"), "deepl")

    def test_unknown_backend_defaults_google(self):
        from app.services.translator import _resolve_service
        config = TranslationConfig(backend="unknown")
        self.assertEqual(_resolve_service(config, "google"), "google")

    def test_deepl_no_key_falls_back(self):
        from app.services.translator import _resolve_service
        import os
        os.environ.pop("DEEPL_API_KEY", None)
        config = TranslationConfig(backend="deepl", api_key="")
        self.assertEqual(_resolve_service(config, "google"), "google")


class TestBuildPdf2zhEnvs(unittest.TestCase):
    """Test _build_pdf2zh_envs function."""

    def test_deepseek_all_fields(self):
        from app.services.translator import _build_pdf2zh_envs
        config = TranslationConfig(backend="deepseek", api_key="key", model="ds-v4")
        env = _build_pdf2zh_envs("deepseek", config)
        self.assertEqual(env["DEEPSEEK_API_KEY"], "key")
        self.assertEqual(env["DEEPSEEK_MODEL"], "ds-v4")

    def test_openai_all_fields(self):
        from app.services.translator import _build_pdf2zh_envs
        config = TranslationConfig(
            backend="openai", api_key="sk-test",
            base_url="https://api.openai.com/v1", model="gpt-4o",
        )
        env = _build_pdf2zh_envs("openai", config)
        self.assertEqual(env["OPENAI_API_KEY"], "sk-test")
        self.assertEqual(env["OPENAI_BASE_URL"], "https://api.openai.com/v1")
        self.assertEqual(env["OPENAI_MODEL"], "gpt-4o")

    def test_google_returns_empty(self):
        from app.services.translator import _build_pdf2zh_envs
        config = TranslationConfig(backend="google")
        self.assertEqual(_build_pdf2zh_envs("google", config), {})

    def test_deepseek_no_optional_fields(self):
        from app.services.translator import _build_pdf2zh_envs
        config = TranslationConfig(backend="deepseek", api_key="key")
        env = _build_pdf2zh_envs("deepseek", config)
        self.assertEqual(env, {"DEEPSEEK_API_KEY": "key"})

    def test_no_api_key_returns_empty(self):
        from app.services.translator import _build_pdf2zh_envs
        import os
        os.environ.pop("DEEPSEEK_API_KEY", None)
        config = TranslationConfig(backend="deepseek", api_key="")
        self.assertEqual(_build_pdf2zh_envs("deepseek", config), {})

    def test_deepl_with_config_key(self):
        from app.services.translator import _build_pdf2zh_envs
        config = TranslationConfig(backend="deepl", api_key="dl-key")
        env = _build_pdf2zh_envs("deepl", config)
        self.assertEqual(env, {"DEEPL_API_KEY": "dl-key"})

    def test_deepl_falls_back_to_env(self):
        from app.services.translator import _build_pdf2zh_envs
        import os
        os.environ["DEEPL_API_KEY"] = "env-key"
        config = TranslationConfig(backend="deepl", api_key="")
        env = _build_pdf2zh_envs("deepl", config)
        self.assertEqual(env, {"DEEPL_API_KEY": "env-key"})
        os.environ.pop("DEEPL_API_KEY", None)

    def test_ollama_with_base_url(self):
        from app.services.translator import _build_pdf2zh_envs
        config = TranslationConfig(backend="ollama", base_url="http://localhost:11434")
        env = _build_pdf2zh_envs("ollama", config)
        self.assertEqual(env, {"OLLAMA_HOST": "http://localhost:11434"})

    def test_ollama_falls_back_to_env(self):
        from app.services.translator import _build_pdf2zh_envs
        import os
        os.environ["OLLAMA_HOST"] = "http://remote:11434"
        config = TranslationConfig(backend="ollama", base_url="")
        env = _build_pdf2zh_envs("ollama", config)
        self.assertEqual(env, {"OLLAMA_HOST": "http://remote:11434"})
        os.environ.pop("OLLAMA_HOST", None)


class TestTranslationResult(unittest.TestCase):
    """Test TranslationResult class."""

    def test_success_with_mono_path(self):
        from app.services.translator import TranslationResult
        result = TranslationResult(mono_path=Path("/tmp/mono.pdf"))
        self.assertTrue(result.success)
        self.assertIsNone(result.error)

    def test_not_success_without_paths(self):
        from app.services.translator import TranslationResult
        result = TranslationResult()
        self.assertFalse(result.success)

    def test_not_success_with_error(self):
        from app.services.translator import TranslationResult
        result = TranslationResult(mono_path=Path("/tmp/mono.pdf"), error="fail")
        self.assertFalse(result.success)

    def test_no_output_files_returns_error(self):
        from app.services.translator import TranslationResult
        result = TranslationResult(error="Translation produced no output files")
        self.assertFalse(result.success)
        self.assertIn("no output", result.error)


if __name__ == "__main__":
    unittest.main()
