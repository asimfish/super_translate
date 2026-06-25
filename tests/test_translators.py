import tempfile
import unittest
from pathlib import Path

from pdf_zh_translator.translators import (
    CachedTranslator,
    Translator,
    chunked_by_size,
    coerce_translation_list,
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


if __name__ == "__main__":
    unittest.main()
