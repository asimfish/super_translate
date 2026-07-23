import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from pdf_zh_translator.cli import build_parser
from pdf_zh_translator.translators import (
    CachedTranslator,
    CacheOnlyTranslator,
    TranslationError,
    Translator,
    VendorTranslator,
    cache_key,
    placeholders_preserved,
    chunked_by_size,
    coerce_plain_translation,
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


class DroppingPlaceholderTranslator(Translator):
    def __init__(self):
        self.calls = 0

    def translate_batch(self, texts):
        self.calls += 1
        return ["公式已翻译" for _text in texts]


class BatchDropsSingleKeepsTranslator(Translator):
    """Loses placeholders in batch mode, preserves them one-by-one."""

    def __init__(self):
        self.calls = []

    def translate_batch(self, texts):
        self.calls.append(list(texts))
        if len(texts) > 1:
            return ["占位符丢了" for _ in texts]
        return ["译:" + texts[0]]


class JsonFailingVendorTranslator(VendorTranslator):
    def __init__(self):
        super().__init__(api_url="https://example.com", mode="deepseek", progress=False)
        self.plain_calls = []

    def _translate_chunk(self, chunk):
        raise TranslationError("bad json")

    def _translate_single_plain(self, text, reason):
        self.plain_calls.append((text, str(reason)))
        return "纯文本:" + text


class RawJsonFailingVendorTranslator(JsonFailingVendorTranslator):
    def _translate_chunk(self, chunk):
        raise json.JSONDecodeError("bad json", "not-json", 0)


class MultiFailingVendorTranslator(VendorTranslator):
    def __init__(self):
        super().__init__(api_url="https://example.com", mode="deepseek", progress=False)
        self.calls = []

    def _translate_chunk(self, chunk):
        self.calls.append(list(chunk))
        if len(chunk) > 1:
            raise TranslationError("bad json")
        return ["译:" + chunk[0]]


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

    def test_coerce_plain_translation_from_markdown_fence(self):
        self.assertEqual(coerce_plain_translation("```text\n纯文本译文\n```"), "纯文本译文")

    def test_coerce_plain_translation_from_single_json_item(self):
        self.assertEqual(coerce_plain_translation('{"translations": ["纯文本译文"]}'), "纯文本译文")

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

    def test_cached_translator_invalidate_forces_supplier_retry(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            cache_file = Path(tmpdir) / "cache.jsonl"
            wrapped = CountingTranslator()
            cached = CachedTranslator(wrapped, cache_file)

            self.assertEqual(cached.translate_batch(["a"]), ["译:a"])
            cached.invalidate(["a"])
            self.assertEqual(cached.translate_batch(["a"]), ["译:a"])

            self.assertEqual(wrapped.calls, 2)

    def test_cached_translator_persists_completed_chunks_before_failure(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            cache_file = Path(tmpdir) / "cache.jsonl"
            cached = CachedTranslator(FailingSecondCallTranslator(), cache_file)

            with self.assertRaises(RuntimeError):
                cached.translate_batch(["a", "b"])

            cached_again = CachedTranslator(CountingTranslator(), cache_file)
            self.assertEqual(cached_again.translate_batch(["a"]), ["译:a"])

    def test_cached_translator_retranslates_invalid_placeholder_entry(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            cache_file = Path(tmpdir) / "cache.jsonl"
            source = "Formula ⟦0⟧ remains unchanged."
            cache_file.write_text(
                json.dumps(
                    {
                        "key": cache_key(source),
                        "source": source,
                        "translation": "公式丢失了占位符。",
                    },
                    ensure_ascii=False,
                )
                + "\n",
                encoding="utf-8",
            )
            wrapped = CountingTranslator()

            translated = CachedTranslator(wrapped, cache_file).translate_batch([source])

            self.assertEqual(translated, ["译:" + source])
            self.assertEqual(wrapped.calls, 1)

    def test_persistently_invalid_placeholder_block_falls_back_to_source(self):
        """A block whose placeholders keep getting mangled degrades to
        untranslated source text instead of failing the whole document, and
        the fallback is never persisted to the cache file."""
        with tempfile.TemporaryDirectory() as tmpdir:
            cache_file = Path(tmpdir) / "cache.jsonl"
            source = "Formula ⟦0⟧ remains unchanged."
            wrapped = DroppingPlaceholderTranslator()
            cached = CachedTranslator(wrapped, cache_file)

            translated = cached.translate_batch([source])

            self.assertEqual(translated, [source])
            self.assertEqual(cached.placeholder_fallbacks, [source])
            # The offending block must be retried individually first.
            self.assertGreater(wrapped.calls, 1)
            # A later run must reach the supplier again, so nothing persisted.
            persisted = (
                cache_file.read_text(encoding="utf-8") if cache_file.exists() else ""
            )
            self.assertEqual(persisted.strip(), "")

    def test_marker_dense_block_recovers_via_split_translation(self):
        """Object-Centric p6 regression: a long block with 15 placeholders
        mangled markers in every full-block call; translating it in two
        halves recovers instead of leaking the English source."""
        long_source = (
            "We conduct experiments with ResNet50 ⟦0⟧, ResNet101, V2-99 ⟦1⟧ "
            "and ViT ⟦2⟧ backbones under different pre-training settings. "
            "Following previous methods ⟦3⟧, the performance of ResNet50 and "
            "ResNet101 models with pre-trained weights ImageNet ⟦4⟧ and "
            "nuImages ⟦5⟧ are provided on the nuScenes val set thoroughly."
        )

        class ManglesLongKeepsHalves(Translator):
            def translate_batch(self, texts):
                outputs = []
                for text in texts:
                    if text == long_source:
                        outputs.append("翻译丢失占位符")
                    else:
                        outputs.append("译:" + text)
                return outputs

        with tempfile.TemporaryDirectory() as tmpdir:
            cache_file = Path(tmpdir) / "cache.jsonl"
            cached = CachedTranslator(ManglesLongKeepsHalves(), cache_file)

            translated = cached.translate_batch([long_source])

            self.assertNotEqual(translated, [long_source])
            self.assertTrue(placeholders_preserved(long_source, translated[0]))
            self.assertEqual(cached.placeholder_fallbacks, [])

    def test_cached_translator_retries_placeholder_loss_item_by_item(self):
        """A batch response that mangles placeholders is retried one block at
        a time; only blocks that fail again abort the translation."""
        with tempfile.TemporaryDirectory() as tmpdir:
            cache_file = Path(tmpdir) / "cache.jsonl"
            sources = [
                "Formula ⟦0⟧ remains unchanged.",
                "Equation ⟦1⟧ also stays.",
            ]
            wrapped = BatchDropsSingleKeepsTranslator()
            cached = CachedTranslator(wrapped, cache_file)

            translated = cached.translate_batch(sources)

            self.assertEqual(
                translated, ["译:" + sources[0], "译:" + sources[1]]
            )
            # First the failed batch, then each block individually.
            self.assertEqual(
                wrapped.calls, [sources, [sources[0]], [sources[1]]]
            )

    def test_cached_translator_concurrent_writes_stay_valid_jsonl(self):
        import threading

        with tempfile.TemporaryDirectory() as tmpdir:
            cache_file = Path(tmpdir) / "cache.jsonl"
            cached = CachedTranslator(CountingTranslator(), cache_file)
            errors: list = []

            def worker(items):
                try:
                    cached.translate_batch(items)
                except Exception as exc:  # pragma: no cover - failure path
                    errors.append(exc)

            threads = [
                threading.Thread(target=worker, args=([f"t{i}-{j}" for j in range(5)],))
                for i in range(6)
            ]
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join()

            self.assertEqual(errors, [])
            # The write lock must keep every appended line individually valid.
            for line in cache_file.read_text(encoding="utf-8").splitlines():
                if line.strip():
                    json.loads(line)

    def test_cached_translator_forwards_block_types_to_wrapped(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            cache_file = Path(tmpdir) / "cache.jsonl"
            vendor = VendorTranslator(
                api_url="https://example.com", mode="deepseek", progress=False
            )
            cached = CachedTranslator(vendor, cache_file)
            cached.block_types = ["title", "caption"]
            # Structure-aware hints must reach the supplier through the cache wrapper.
            self.assertEqual(vendor.block_types, ["title", "caption"])
            self.assertEqual(cached.block_types, ["title", "caption"])

    def test_single_item_parse_failure_uses_plain_text_fallback(self):
        translator = JsonFailingVendorTranslator()

        self.assertEqual(
            translator._translate_chunk_with_fallback(["Model Details bullet list"]),
            ["纯文本:Model Details bullet list"],
        )
        self.assertEqual(translator.plain_calls[0][0], "Model Details bullet list")

    def test_raw_json_decode_failure_uses_plain_text_fallback(self):
        translator = RawJsonFailingVendorTranslator()

        self.assertEqual(
            translator._translate_chunk_with_fallback(["Model Details bullet list"]),
            ["纯文本:Model Details bullet list"],
        )

    def test_openai_plain_fallback_keeps_relevant_terminology(self):
        translator = VendorTranslator(
            api_url="https://example.com",
            mode="openai-compatible",
            progress=False,
        )
        response = {"choices": [{"message": {"content": "我们使用神经网络。"}}]}

        with patch.object(translator, "_post_json", return_value=response) as post_json:
            translated = translator._translate_openai_plain("We use a neural network.")

        prompt = post_json.call_args.args[1]["messages"][0]["content"]
        self.assertEqual(translated, "我们使用神经网络。")
        self.assertIn("neural network → 神经网络", prompt)

    def test_multi_item_parse_failure_retries_one_by_one(self):
        translator = MultiFailingVendorTranslator()

        self.assertEqual(
            translator._translate_chunk_with_fallback(["first", "second"]),
            ["译:first", "译:second"],
        )
        self.assertEqual(translator.calls, [["first", "second"], ["first"], ["second"]])


class CacheOnlyTranslatorTests(unittest.TestCase):
    def test_returns_cached_translations(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            cache_file = Path(tmpdir) / "cache.jsonl"
            # Pre-populate cache
            with cache_file.open("w") as f:
                f.write(
                    '{"key": "%s", "source": "hello", "translation": "你好"}\n'
                    % cache_key("hello")
                )
                f.write(
                    '{"key": "%s", "source": "world", "translation": "世界"}\n'
                    % cache_key("world")
                )

            translator = CacheOnlyTranslator(cache_file)
            result = translator.translate_batch(["hello", "world"])
            self.assertEqual(result, ["你好", "世界"])

    def test_raises_on_cache_miss(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            cache_file = Path(tmpdir) / "cache.jsonl"
            # Pre-populate with only one entry
            with cache_file.open("w") as f:
                f.write(
                    '{"key": "%s", "source": "hello", "translation": "你好"}\n'
                    % cache_key("hello")
                )

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

    def test_rejects_cached_translation_with_duplicate_placeholders(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            cache_file = Path(tmpdir) / "cache.jsonl"
            source = "Variables ⟦0⟧ and ⟦1⟧."
            cache_file.write_text(
                json.dumps(
                    {
                        "key": cache_key(source),
                        "source": source,
                        "translation": "变量 ⟦0⟧ 和 ⟦0⟧。",
                    },
                    ensure_ascii=False,
                )
                + "\n",
                encoding="utf-8",
            )

            with self.assertRaisesRegex(TranslationError, "missing or invalid"):
                CacheOnlyTranslator(cache_file).translate_batch([source])

            missing_file = cache_file.with_name(cache_file.name + ".missing.jsonl")
            record = json.loads(missing_file.read_text(encoding="utf-8"))
            self.assertEqual(record["reason"], "invalid_placeholders")

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


class EchoTranslatorPlaceholderTests(unittest.TestCase):
    def test_echo_translator_preserves_placeholders(self):
        from pdf_zh_translator.translators import EchoTranslator, placeholders_preserved

        source = "The bound ⟦0⟧ holds for ⟦1⟧ under mild assumptions."
        output = EchoTranslator().translate_batch([source])[0]

        self.assertTrue(placeholders_preserved(source, output))

    def test_echo_translator_plain_text_unchanged_format(self):
        from pdf_zh_translator.translators import EchoTranslator

        output = EchoTranslator().translate_batch(["Plain sentence."])[0]

        self.assertIn("中文占位译文", output)
        self.assertNotIn("⟦", output)
