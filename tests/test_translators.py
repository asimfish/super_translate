import argparse
import tempfile
import unittest
from pathlib import Path

from pdf_zh_translator.cli import build_parser
from pdf_zh_translator.translators import (
    CacheOnlyTranslator,
    CachedTranslator,
    TranslationError,
    Translator,
    cache_key,
    chunked_by_size,
    coerce_translation_list,
    extract_openai_message,
    normalize_chat_url,
    normalize_deepseek_chat_url,
    parse_json_string_list,
    parse_json_translations,
    parse_translation_list,
)


class CountingTranslator(Translator):
    def __init__(self):
        self.calls = 0

    def translate_batch(self, texts):
        self.calls += 1
        return ["译:" + text for text in texts]


class FailingSecondCallTranslator(Translator):
    batch_size = 1
    max_batch_chars = 100

    def __init__(self):
        self.calls = 0

    def translate_batch(self, texts):
        self.calls += 1
        if self.calls == 2:
            raise RuntimeError("boom")
        return ["译:" + text for text in texts]


class TranslatorParsingTests(unittest.TestCase):
    def test_parse_generic_string_list(self):
        self.assertEqual(
            parse_translation_list({"translations": ["你好", "世界"]}),
            ["你好", "世界"],
        )

    def test_parse_generic_object_list(self):
        self.assertEqual(
            parse_translation_list({"data": {"translations": [{"text": "你好"}]}}),
            ["你好"],
        )

    def test_coerce_rejects_unknown_objects(self):
        self.assertIsNone(coerce_translation_list([{"value": "你好"}]))

    def test_parse_json_string_list_from_fenced_json(self):
        self.assertEqual(
            parse_json_string_list('```json\n["第一段", "第二段"]\n```'),
            ["第一段", "第二段"],
        )

    def test_parse_json_translations_object(self):
        self.assertEqual(
            parse_json_translations('{"translations": ["第一段", "第二段"]}'),
            ["第一段", "第二段"],
        )

    def test_normalize_chat_url(self):
        self.assertEqual(
            normalize_chat_url("https://example.com/v1"),
            "https://example.com/v1/chat/completions",
        )
        self.assertEqual(
            normalize_chat_url("https://example.com/v1/chat/completions"),
            "https://example.com/v1/chat/completions",
        )

    def test_normalize_deepseek_chat_url(self):
        self.assertEqual(
            normalize_deepseek_chat_url("https://api.deepseek.com"),
            "https://api.deepseek.com/chat/completions",
        )
        self.assertEqual(
            normalize_deepseek_chat_url("https://api.deepseek.com/v1"),
            "https://api.deepseek.com/chat/completions",
        )
        self.assertEqual(
            normalize_deepseek_chat_url("https://proxy.example.com/v1"),
            "https://proxy.example.com/v1/chat/completions",
        )

    def test_chunked_by_size_splits_on_chars_and_items(self):
        self.assertEqual(
            list(chunked_by_size(["aa", "bbb", "c", "dddd"], max_items=2, max_chars=4)),
            [["aa"], ["bbb", "c"], ["dddd"]],
        )

    def test_cache_key_is_stable_sha256(self):
        key = cache_key("hello world")
        self.assertEqual(len(key), 64)  # SHA-256 hex digest
        self.assertEqual(key, cache_key("hello world"))  # deterministic

    def test_cache_key_differs_for_different_text(self):
        self.assertNotEqual(cache_key("hello"), cache_key("world"))

    def test_coerce_rejects_non_list(self):
        self.assertIsNone(coerce_translation_list("not a list"))
        self.assertIsNone(coerce_translation_list(None))

    def test_coerce_rejects_mixed_types(self):
        self.assertIsNone(coerce_translation_list(["ok", 42]))

    def test_coerce_extracts_text_from_dicts(self):
        self.assertEqual(
            coerce_translation_list([{"text": "你好"}, {"translated_text": "世界"}]),
            ["你好", "世界"],
        )

    def test_coerce_extracts_translation_key(self):
        self.assertEqual(
            coerce_translation_list([{"translation": "你好"}]),
            ["你好"],
        )

    def test_extract_openai_message_from_content(self):
        data = {"choices": [{"message": {"content": "你好世界"}}]}
        self.assertEqual(extract_openai_message(data), "你好世界")

    def test_extract_openai_message_from_text(self):
        data = {"choices": [{"text": "你好世界"}]}
        self.assertEqual(extract_openai_message(data), "你好世界")

    def test_extract_openai_message_raises_on_no_choices(self):
        with self.assertRaises(TranslationError):
            extract_openai_message({"choices": []})

    def test_extract_openai_message_raises_on_missing_content(self):
        with self.assertRaises(TranslationError):
            extract_openai_message({"choices": [{"message": {}}]})

    def test_cached_translator_reuses_jsonl_cache(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            cache_file = Path(tmpdir) / "cache.jsonl"
            wrapped = CountingTranslator()
            cached = CachedTranslator(wrapped, cache_file)

            self.assertEqual(cached.translate_batch(["a", "b", "a"]), ["译:a", "译:b", "译:a"])
            self.assertEqual(wrapped.calls, 1)

            cached_again = CachedTranslator(wrapped, cache_file)
            self.assertEqual(cached_again.translate_batch(["a"]), ["译:a"])
            self.assertEqual(wrapped.calls, 1)

    def test_cached_translator_persists_completed_chunks_before_failure(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            cache_file = Path(tmpdir) / "cache.jsonl"
            cached = CachedTranslator(FailingSecondCallTranslator(), cache_file)

            with self.assertRaises(RuntimeError):
                cached.translate_batch(["a", "b"])

            cached_again = CachedTranslator(CountingTranslator(), cache_file)
            self.assertEqual(cached_again.translate_batch(["a"]), ["译:a"])


class CacheOnlyTranslatorTests(unittest.TestCase):
    def test_returns_cached_translations(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            cache_file = Path(tmpdir) / "cache.jsonl"
            # Pre-populate cache
            with cache_file.open("w") as f:
                f.write('{"key": "%s", "source": "hello", "translation": "你好"}\n' % cache_key("hello"))
                f.write('{"key": "%s", "source": "world", "translation": "世界"}\n' % cache_key("world"))

            translator = CacheOnlyTranslator(cache_file)
            result = translator.translate_batch(["hello", "world"])
            self.assertEqual(result, ["你好", "世界"])

    def test_raises_on_cache_miss(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            cache_file = Path(tmpdir) / "cache.jsonl"
            # Pre-populate with only one entry
            with cache_file.open("w") as f:
                f.write('{"key": "%s", "source": "hello", "translation": "你好"}\n' % cache_key("hello"))

            translator = CacheOnlyTranslator(cache_file)
            with self.assertRaises(TranslationError) as ctx:
                translator.translate_batch(["hello", "missing"])
            self.assertIn("1/2", str(ctx.exception))

    def test_empty_cache_file(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            cache_file = Path(tmpdir) / "cache.jsonl"
            cache_file.touch()

            translator = CacheOnlyTranslator(cache_file)
            with self.assertRaises(TranslationError):
                translator.translate_batch(["anything"])

    def test_ignores_malformed_json_lines(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            cache_file = Path(tmpdir) / "cache.jsonl"
            with cache_file.open("w") as f:
                f.write("not json\n")
                f.write('{"key": "%s", "source": "ok", "translation": "好的"}\n' % cache_key("ok"))

            translator = CacheOnlyTranslator(cache_file)
            result = translator.translate_batch(["ok"])
            self.assertEqual(result, ["好的"])


class CLIParserTests(unittest.TestCase):
    def test_translate_subcommand_basic_args(self):
        parser = build_parser()
        args = parser.parse_args(["translate", "in.pdf", "out.pdf"])
        self.assertEqual(args.command, "translate")
        self.assertEqual(args.input_pdf, Path("in.pdf"))
        self.assertEqual(args.output_pdf, Path("out.pdf"))
        self.assertFalse(args.dry_run)
        self.assertFalse(args.preserve_graphics_text)
        self.assertFalse(args.skip_overflow)

    def test_translate_preserve_graphics_text_flag(self):
        parser = build_parser()
        args = parser.parse_args(["translate", "in.pdf", "out.pdf", "--preserve-graphics-text"])
        self.assertTrue(args.preserve_graphics_text)

    def test_translate_skip_overflow_flag(self):
        parser = build_parser()
        args = parser.parse_args(["translate", "in.pdf", "out.pdf", "--skip-overflow"])
        self.assertTrue(args.skip_overflow)

    def test_translate_model_and_font_options(self):
        parser = build_parser()
        args = parser.parse_args([
            "translate", "in.pdf", "out.pdf",
            "--model", "gpt-4o", "--font-name", "china-ss",
        ])
        self.assertEqual(args.model, "gpt-4o")
        self.assertEqual(args.font_name, "china-ss")

    def test_export_subcommand(self):
        parser = build_parser()
        args = parser.parse_args(["export", "in.pdf", "out.jsonl"])
        self.assertEqual(args.command, "export")
        self.assertEqual(args.input_pdf, Path("in.pdf"))
        self.assertEqual(args.blocks_jsonl, Path("out.jsonl"))

    def test_no_command_shows_help(self):
        parser = build_parser()
        args = parser.parse_args([])
        self.assertIsNone(args.command)


if __name__ == "__main__":
    unittest.main()
