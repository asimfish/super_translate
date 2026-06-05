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
    def test_deepseek_env_vars_set(self, mock_get_model, mock_translate):
        """Test that DeepSeek API key is set in environment."""
        mock_get_model.return_value = MagicMock()

        captured_env = {}
        original_translate = mock_translate

        def capture_translate(*args, **kwargs):
            captured_env["DEEPSEEK_API_KEY"] = __import__("os").environ.get("DEEPSEEK_API_KEY")
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

        self.assertEqual(captured_env.get("DEEPSEEK_API_KEY"), "test-key-123")

    @patch("pdf2zh.translate")
    @patch("app.services.translator.get_model")
    def test_openai_env_vars_set(self, mock_get_model, mock_translate):
        """Test that OpenAI env vars are set correctly."""
        import os
        mock_get_model.return_value = MagicMock()
        captured_env = {}

        def capture_translate(*args, **kwargs):
            captured_env["OPENAI_API_KEY"] = os.environ.get("OPENAI_API_KEY")
            captured_env["OPENAI_BASE_URL"] = os.environ.get("OPENAI_BASE_URL")
            captured_env["OPENAI_MODEL"] = os.environ.get("OPENAI_MODEL")
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

        self.assertEqual(captured_env.get("OPENAI_API_KEY"), "sk-test")
        self.assertEqual(captured_env.get("OPENAI_BASE_URL"), "https://custom.openai.com/v1")
        self.assertEqual(captured_env.get("OPENAI_MODEL"), "gpt-4o")

    @patch("pdf2zh.translate")
    @patch("app.services.translator.get_model")
    def test_deepseek_base_url_and_model_env_vars(self, mock_get_model, mock_translate):
        """Test that DeepSeek base_url and model are set in env."""
        import os
        mock_get_model.return_value = MagicMock()
        captured_env = {}

        def capture_translate(*args, **kwargs):
            captured_env["DEEPSEEK_API_KEY"] = os.environ.get("DEEPSEEK_API_KEY")
            captured_env["DEEPSEEK_API_URL"] = os.environ.get("DEEPSEEK_API_URL")
            captured_env["DEEPSEEK_MODEL"] = os.environ.get("DEEPSEEK_MODEL")
            return None

        mock_translate.side_effect = capture_translate

        config = TranslationConfig(
            backend="deepseek",
            api_key="ds-key",
            base_url="https://custom.deepseek.com",
            model="deepseek-v4-pro",
            quality=QualityPreset.BALANCED,
        )

        with patch("pathlib.Path.exists", return_value=True):
            with patch("pathlib.Path.glob", return_value=[]):
                translate_pdf_sync(Path("/tmp/input.pdf"), Path("/tmp/output"), config)

        self.assertEqual(captured_env.get("DEEPSEEK_API_KEY"), "ds-key")
        self.assertEqual(captured_env.get("DEEPSEEK_API_URL"), "https://custom.deepseek.com")
        self.assertEqual(captured_env.get("DEEPSEEK_MODEL"), "deepseek-v4-pro")

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

        def fail_then_succeed(*args, **kwargs):
            call_count[0] += 1
            if call_count[0] == 1:
                raise Exception("Temporary API error")

        mock_translate.side_effect = fail_then_succeed

        config = TranslationConfig(
            backend="google",
            quality=QualityPreset.FAST,
            max_retries=2,
        )

        with tempfile.TemporaryDirectory() as tmpdir:
            output_dir = Path(tmpdir) / "output"
            output_dir.mkdir()
            # Create partial output files that should be cleaned up
            partial1 = output_dir / "partial1.pdf"
            partial2 = output_dir / "partial2.txt"
            partial1.write_bytes(b"partial")
            partial2.write_bytes(b"partial")

            # Create the expected output file for the second attempt
            (output_dir / "input-mono.pdf").write_bytes(b"translated")

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
    def test_env_vars_restored_after_translation(self, mock_get_model, mock_translate):
        """Test that environment variables are restored after translation."""
        import os
        mock_get_model.return_value = MagicMock()
        mock_translate.return_value = None

        # Set a pre-existing env var
        os.environ["DEEPSEEK_API_KEY"] = "original-key"

        config = TranslationConfig(
            backend="deepseek",
            api_key="temp-key",
            quality=QualityPreset.BALANCED,
        )

        with patch("pathlib.Path.exists", return_value=True):
            with patch("pathlib.Path.glob", return_value=[]):
                translate_pdf_sync(Path("/tmp/input.pdf"), Path("/tmp/output"), config)

        # Should be restored to original
        self.assertEqual(os.environ.get("DEEPSEEK_API_KEY"), "original-key")

        # Cleanup
        os.environ.pop("DEEPSEEK_API_KEY", None)

    @patch("pdf2zh.translate")
    @patch("app.services.translator.get_model")
    def test_env_vars_cleaned_up_when_not_previously_set(self, mock_get_model, mock_translate):
        """Test that env vars are removed if they weren't set before."""
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

        # Should be cleaned up (not left as "temp-key")
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


if __name__ == "__main__":
    unittest.main()
