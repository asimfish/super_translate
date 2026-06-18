"""Translation API adapters.

The default adapter intentionally supports two common supplier contracts:

* ``generic``: POST ``{"source_lang": "...", "target_lang": "...", "texts": [...]}``
  and read a same-length list from common response shapes.
* ``openai-compatible``: call a chat-completions endpoint and ask the model to
  return a JSON array of translated strings.
* ``deepseek``: call DeepSeek's official OpenAI-compatible endpoint with
  ``deepseek-v4-pro`` by default.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence


class TranslationError(RuntimeError):
    """Raised when the translation supplier returns an unusable response."""


class Translator:
    """Interface for batch translators."""

    def translate_batch(self, texts: Sequence[str]) -> List[str]:
        raise NotImplementedError


class EchoTranslator(Translator):
    """Development translator used by ``--dry-run``."""

    def translate_batch(self, texts: Sequence[str]) -> List[str]:
        output: List[str] = []
        for index, text in enumerate(texts, start=1):
            repeat_count = max(1, min(4, len(text) // 60 + 1))
            output.append(("第%d段中文占位译文。" % index) + ("用于检查版面。" * repeat_count))
        return output


class CachedTranslator(Translator):
    """Persistent JSONL cache wrapper for expensive supplier translations."""

    def __init__(self, wrapped: Translator, cache_file: Path) -> None:
        self.wrapped = wrapped
        self.cache_file = cache_file
        self.cache: Dict[str, str] = {}
        self._load()

    def translate_batch(self, texts: Sequence[str]) -> List[str]:
        missing: List[str] = []
        for text in texts:
            if cache_key(text) not in self.cache:
                missing.append(text)

        if missing:
            translations = self.wrapped.translate_batch(missing)
            self.cache_file.parent.mkdir(parents=True, exist_ok=True)
            with self.cache_file.open("a", encoding="utf-8") as handle:
                for source, translation in zip(missing, translations):
                    key = cache_key(source)
                    self.cache[key] = translation
                    handle.write(
                        json.dumps(
                            {"key": key, "source": source, "translation": translation},
                            ensure_ascii=False,
                        )
                        + "\n"
                    )

        return [self.cache[cache_key(text)] for text in texts]

    def _load(self) -> None:
        if not self.cache_file.exists():
            return
        with self.cache_file.open("r", encoding="utf-8") as handle:
            for line in handle:
                line = line.strip()
                if not line:
                    continue
                try:
                    record = json.loads(line)
                except json.JSONDecodeError:
                    continue
                key = record.get("key")
                translation = record.get("translation")
                if isinstance(key, str) and isinstance(translation, str):
                    self.cache[key] = translation


class CacheOnlyTranslator(Translator):
    """Render strictly from a pre-filled cache (e.g. translations produced
    directly by an LLM assistant). Never calls a supplier API.

    On cache misses it writes the missing source blocks to
    ``<cache>.missing.jsonl`` and aborts, so the caller can fill the cache
    and re-run.
    """

    def __init__(self, cache_file: Path) -> None:
        self.cache_file = Path(cache_file)
        self.cache: Dict[str, str] = {}
        if self.cache_file.exists():
            with self.cache_file.open("r", encoding="utf-8") as handle:
                for line in handle:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        record = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    key = record.get("key")
                    translation = record.get("translation")
                    if isinstance(key, str) and isinstance(translation, str):
                        self.cache[key] = translation

    def translate_batch(self, texts: Sequence[str]) -> List[str]:
        missing = [text for text in texts if cache_key(text) not in self.cache]
        if missing:
            missing_file = self.cache_file.with_name(self.cache_file.name + ".missing.jsonl")
            with missing_file.open("w", encoding="utf-8") as handle:
                for text in missing:
                    record = {
                        "key": cache_key(text),
                        "source": text,
                    }
                    handle.write(
                        json.dumps(record, ensure_ascii=False) + "\n"
                    )
            raise TranslationError(
                "cache-only mode: %d/%d blocks missing from cache. Missing blocks dumped to %s"
                % (len(missing), len(texts), missing_file)
            )
        return [self.cache[cache_key(text)] for text in texts]


@dataclass
class VendorTranslator(Translator):
    api_url: str
    api_key: Optional[str] = None
    mode: str = "generic"
    model: Optional[str] = None
    source_lang: str = "en"
    target_lang: str = "zh"
    auth_header: str = "Authorization"
    auth_scheme: str = "Bearer"
    batch_size: int = 8
    max_batch_chars: int = 2500
    max_output_tokens: int = 8192
    timeout: float = 60.0
    retries: int = 2
    deepseek_thinking: str = "disabled"
    reasoning_effort: str = "high"
    progress: bool = True

    def translate_batch(self, texts: Sequence[str]) -> List[str]:
        results: List[str] = []
        chunks = list(chunked_by_size(
            list(texts), self.batch_size, self.max_batch_chars
        ))
        total_chunks = len(chunks)
        translated_count = 0
        for chunk_index, chunk in enumerate(chunks, start=1):
            if self.progress:
                char_count = sum(len(text) for text in chunk)
                print(
                    "Translating batch %d/%d: %d blocks, %d chars"
                    % (chunk_index, total_chunks, len(chunk), char_count),
                    file=sys.stderr,
                    flush=True,
                )
            translated = self._translate_chunk_with_fallback(chunk)
            results.extend(translated)
            translated_count += len(chunk)
            if self.progress:
                print(
                    "Translated %d/%d text blocks" % (translated_count, len(texts)),
                    file=sys.stderr,
                    flush=True,
                )
        return results

    def _translate_chunk_with_fallback(self, chunk: Sequence[str]) -> List[str]:
        translated = self._translate_chunk(chunk)
        if len(translated) == len(chunk):
            return translated
        if len(chunk) == 1:
            raise TranslationError(
                "Supplier returned %d translations for 1 input" % len(translated)
            )

        if self.progress:
            print(
                "Warning: supplier returned %d translations for %d inputs; retrying one by one"
                % (len(translated), len(chunk)),
                file=sys.stderr,
                flush=True,
            )

        singles: List[str] = []
        for item in chunk:
            single = self._translate_chunk([item])
            if len(single) != 1:
                raise TranslationError(
                    "Supplier returned %d translations for 1 input during fallback" % len(single)
                )
            singles.extend(single)
        return singles

    def _translate_chunk(self, chunk: Sequence[str]) -> List[str]:
        if self.mode == "generic":
            return self._translate_generic(chunk)
        if self.mode == "openai-compatible":
            return self._translate_openai_compatible(chunk)
        if self.mode == "deepseek":
            return self._translate_deepseek(chunk)
        raise TranslationError("Unsupported translation mode: %s" % self.mode)

    def _translate_generic(self, texts: Sequence[str]) -> List[str]:
        payload = {
            "source_lang": self.source_lang,
            "target_lang": self.target_lang,
            "texts": list(texts),
        }
        data = self._post_json(self.api_url, payload)
        return parse_translation_list(data)

    def _translate_openai_compatible(self, texts: Sequence[str]) -> List[str]:
        payload = {
            "model": self.model or "gpt-4o-mini",
            "messages": [
                {"role": "system", "content": translation_array_prompt()},
                {"role": "user", "content": json.dumps(list(texts), ensure_ascii=False)},
            ],
            "temperature": 0,
            "max_tokens": self.max_output_tokens,
        }
        data = self._post_json(normalize_chat_url(self.api_url), payload)
        content = extract_openai_message(data)
        return parse_json_string_list(content)

    def _translate_deepseek(self, texts: Sequence[str]) -> List[str]:
        payload: Dict[str, Any] = {
            "model": self.model or "deepseek-v4-pro",
            "messages": [
                {"role": "system", "content": translation_object_prompt()},
                {"role": "user", "content": json.dumps(list(texts), ensure_ascii=False)},
            ],
            "temperature": 0,
            "max_tokens": self.max_output_tokens,
            "stream": False,
            "response_format": {"type": "json_object"},
            "thinking": {"type": self.deepseek_thinking},
        }
        if self.deepseek_thinking == "enabled":
            payload["reasoning_effort"] = self.reasoning_effort
        data = self._post_json(normalize_deepseek_chat_url(self.api_url), payload)
        content = extract_openai_message(data)
        return parse_json_translations(content)

    def _post_json(self, url: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json",
        }
        if self.api_key:
            auth_value = self.api_key
            if self.auth_scheme:
                auth_value = "%s %s" % (self.auth_scheme, self.api_key)
            headers[self.auth_header] = auth_value

        last_error: Optional[BaseException] = None
        for attempt in range(self.retries + 1):
            request = urllib.request.Request(url, data=body, headers=headers, method="POST")
            try:
                with urllib.request.urlopen(request, timeout=self.timeout) as response:
                    response_body = response.read().decode("utf-8")
                parsed = json.loads(response_body)
                if not isinstance(parsed, dict):
                    raise TranslationError("Supplier response must be a JSON object")
                return parsed
            except (
                urllib.error.URLError,
                urllib.error.HTTPError,
                TimeoutError,
                json.JSONDecodeError,
            ) as exc:
                last_error = exc
                if attempt >= self.retries:
                    break
                time.sleep(0.8 * (2 ** attempt))
        raise TranslationError("Translation API request failed: %s" % last_error)


def build_translator_from_args(args: Any) -> Translator:
    if getattr(args, "dry_run", False):
        return EchoTranslator()

    if args.api_mode == "cache-only":
        if not args.cache_file:
            raise TranslationError(
                "cache-only mode requires --cache-file."
            )
        return CacheOnlyTranslator(Path(args.cache_file))

    if args.api_mode == "deepseek":
        api_url = args.api_url or os.getenv("DEEPSEEK_API_URL") or "https://api.deepseek.com"
    else:
        api_url = args.api_url or os.getenv("PDF_TRANSLATOR_API_URL")
    if not api_url:
        raise TranslationError("Missing API URL. Pass --api-url or set PDF_TRANSLATOR_API_URL.")

    api_key = args.api_key
    if not api_key and args.api_key_env:
        api_key = os.getenv(args.api_key_env)
    if not api_key and args.api_mode == "deepseek":
        api_key = os.getenv("DEEPSEEK_API_KEY")
    if not api_key:
        api_key = os.getenv("PDF_TRANSLATOR_API_KEY")
    if args.api_mode == "deepseek" and not api_key:
        raise TranslationError(
            "Missing DeepSeek API key. Set DEEPSEEK_API_KEY."
        )

    translator: Translator = VendorTranslator(
        api_url=api_url,
        api_key=api_key,
        mode=args.api_mode,
        model=args.model or ("deepseek-v4-pro" if args.api_mode == "deepseek" else None),
        source_lang=args.source_lang,
        target_lang=args.target_lang,
        auth_header=args.auth_header,
        auth_scheme=args.auth_scheme,
        batch_size=args.batch_size,
        max_batch_chars=args.max_batch_chars,
        max_output_tokens=args.max_output_tokens,
        timeout=args.timeout,
        retries=args.retries,
        deepseek_thinking=args.deepseek_thinking,
        reasoning_effort=args.reasoning_effort,
        progress=not args.quiet,
    )
    if args.cache_file:
        translator = CachedTranslator(translator, Path(args.cache_file))
    return translator


def cache_key(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def parse_translation_list(data: Dict[str, Any]) -> List[str]:
    """Parse several common supplier response formats into a list of strings."""

    candidates: Iterable[Any] = (
        data.get("translations"),
        data.get("translated_texts"),
        data.get("results"),
        data.get("data", {}).get("translations") if isinstance(data.get("data"), dict) else None,
        data.get("data", {}).get("results") if isinstance(data.get("data"), dict) else None,
    )
    for candidate in candidates:
        parsed = coerce_translation_list(candidate)
        if parsed is not None:
            return parsed
    raise TranslationError("Could not find translations in supplier response")


def coerce_translation_list(value: Any) -> Optional[List[str]]:
    if isinstance(value, list):
        output: List[str] = []
        for item in value:
            if isinstance(item, str):
                output.append(item)
            elif isinstance(item, dict):
                text = item.get("text") or item.get("translated_text") or item.get("translation")
                if not isinstance(text, str):
                    return None
                output.append(text)
            else:
                return None
        return output
    return None


def extract_openai_message(data: Dict[str, Any]) -> str:
    choices = data.get("choices")
    if not isinstance(choices, list) or not choices:
        raise TranslationError("OpenAI-compatible response has no choices")
    first = choices[0]
    if not isinstance(first, dict):
        raise TranslationError("OpenAI-compatible choice must be an object")
    message = first.get("message")
    if isinstance(message, dict) and isinstance(message.get("content"), str):
        return message["content"]
    if isinstance(first.get("text"), str):
        return first["text"]
    raise TranslationError("OpenAI-compatible response has no message content")


_TRANSLATION_RULES = (
    "你是顶级学术论文翻译专家，擅长计算机科学、机器学习、数学等领域。\n\n"
    "【核心铁律】翻译必须是纯中文，严禁中英混杂！\n"
    '错误示范："我们使用了 attention mechanism 来 improve 性能"\n'
    '正确示范："本文采用注意力机制以提升性能"\n\n'
    "翻译规则：\n"
    "1. 【纯中文输出】每个句子必须是完整中文，不得夹杂英文单词或短语\n"
    "   专有名词首次出现时格式为：中文术语（English Term），之后只用中文\n"
    '   例如：首次"神经网络（Neural Network）"，之后"神经网络"\n'
    "2. 【学术用语】必须使用学术论文标准表达：\n"
    '   "本文提出"（非"我们建议"）、"达到"（非"搞定"）\n'
    '   "显著的"（非"重要的"）、"利用"（非"借助"）\n'
    '   "新颖的"（非"新的"）、"最先进的"（非"最新的"）\n'
    '   "此外"（非"另外"）、"因此"（非"所以"）、"然而"（非"但是"）\n'
    "3. 【公式保护】数学公式、方程、变量名原样保留（如 $x^2$、$\\alpha$、E=mc²）\n"
    "4. 【引用保护】引用标记 [1]、[2] 保持不变\n"
    "5. 【符号保护】数学符号 ∈、∀、∃、∑、∫ 等保持不变\n"
    '6. 【被动语态】英文被动翻译为中文无主句或"本文"作主语\n'
    "7. 【图表】Figure 1→图1，Table 2→表2\n"
    "8. 【长句拆分】英文长句拆分为多个中文短句\n"
)


def translation_array_prompt() -> str:
    return (
        _TRANSLATION_RULES
        + "\nTranslate each item in the JSON array "
        "from English to Simplified Chinese. "
        "Preserve numbers, citations, equations, URLs, "
        "product names, and line breaks where possible. "
        "Do not add commentary. Return only a valid "
        "JSON array of strings with the same length and order."
    )


def translation_object_prompt() -> str:
    return (
        _TRANSLATION_RULES
        + "\nTranslate each item in the input JSON array "
        "from English to Simplified Chinese. "
        "Preserve numbers, citations, equations, URLs, "
        "product names, and line breaks where possible. "
        'Return only a valid json object in this shape: '
        '{"translations":["译文1","译文2"]}. '
        "The translations array must have the same "
        "length and order as the input array."
    )


def parse_json_translations(content: str) -> List[str]:
    parsed = load_json_from_content(content)
    if isinstance(parsed, list) and all(isinstance(item, str) for item in parsed):
        return parsed
    if isinstance(parsed, dict):
        return parse_translation_list(parsed)
    raise TranslationError("Model response must contain a JSON translations array")


def parse_json_string_list(content: str) -> List[str]:
    parsed = load_json_from_content(content)
    if not isinstance(parsed, list) or not all(isinstance(item, str) for item in parsed):
        raise TranslationError("Model response must be a JSON array of strings")
    return parsed


def load_json_from_content(content: str) -> Any:
    cleaned = content.strip()
    if cleaned.startswith("```"):
        cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned)
        cleaned = re.sub(r"\s*```$", "", cleaned)

    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        match = re.search(r"(\{[\s\S]*\}|\[[\s\S]*\])", cleaned)
        if not match:
            raise
        return json.loads(match.group(0))


def normalize_chat_url(api_url: str) -> str:
    stripped = api_url.rstrip("/")
    if stripped.endswith("/chat/completions"):
        return stripped
    if stripped.endswith("/v1"):
        return stripped + "/chat/completions"
    return stripped + "/v1/chat/completions"


def normalize_deepseek_chat_url(api_url: str) -> str:
    stripped = api_url.rstrip("/")
    if stripped.endswith("/chat/completions"):
        return stripped
    if stripped == "https://api.deepseek.com/v1":
        return "https://api.deepseek.com/chat/completions"
    return stripped + "/chat/completions"


def chunked(items: Sequence[str], size: int) -> Iterable[Sequence[str]]:
    if size <= 0:
        raise TranslationError("batch_size must be greater than 0")
    for index in range(0, len(items), size):
        yield items[index : index + size]


def chunked_by_size(
    items: Sequence[str],
    max_items: int,
    max_chars: int,
) -> Iterable[Sequence[str]]:
    if max_items <= 0:
        raise TranslationError("batch_size must be greater than 0")
    if max_chars <= 0:
        raise TranslationError("max_batch_chars must be greater than 0")

    current: List[str] = []
    current_chars = 0
    for item in items:
        item_chars = len(item)
        would_exceed_items = len(current) >= max_items
        would_exceed_chars = current and current_chars + item_chars > max_chars
        if would_exceed_items or would_exceed_chars:
            yield current
            current = []
            current_chars = 0
        current.append(item)
        current_chars += item_chars

    if current:
        yield current
