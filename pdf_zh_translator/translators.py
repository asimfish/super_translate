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
import threading
import time
import urllib.error
import urllib.request
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence


class TranslationError(RuntimeError):
    """Raised when the translation supplier returns an unusable response."""


_CACHE_PLACEHOLDER_RE = re.compile(r"⟦\d+⟧")


def placeholders_preserved(source: str, translation: str) -> bool:
    """Return whether every protected fragment marker is preserved exactly."""
    return Counter(_CACHE_PLACEHOLDER_RE.findall(source)) == Counter(
        _CACHE_PLACEHOLDER_RE.findall(translation)
    )


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
        # Guards cache-file appends so concurrent translate_batch calls (parallel
        # supplier requests) don't interleave/corrupt the JSONL.
        self._write_lock = threading.Lock()
        self._load()

    @property
    def block_types(self):
        """Forward structure-aware block types to the wrapped supplier so the
        Web path (ProgressTranslator → CachedTranslator → VendorTranslator) still
        gets caption/heading/title prompt hints."""
        return getattr(self.wrapped, "block_types", None)

    @block_types.setter
    def block_types(self, value) -> None:
        if hasattr(self.wrapped, "block_types"):
            self.wrapped.block_types = value

    def translate_batch(self, texts: Sequence[str]) -> List[str]:
        missing: List[str] = []
        for text in texts:
            key = cache_key(text)
            cached = self.cache.get(key)
            if cached is None:
                missing.append(text)
            elif not placeholders_preserved(text, cached):
                self.cache.pop(key, None)
                missing.append(text)

        if missing:
            self.cache_file.parent.mkdir(parents=True, exist_ok=True)
            batch_size = int(getattr(self.wrapped, "batch_size", len(missing)))
            max_chars = int(
                getattr(self.wrapped, "max_batch_chars", sum(len(text) for text in missing) or 1)
            )
            for chunk in chunked_by_size(missing, batch_size, max_chars):
                translations = self.wrapped.translate_batch(chunk)
                invalid_count = sum(
                    not placeholders_preserved(source, translation)
                    for source, translation in zip(chunk, translations)
                )
                if invalid_count:
                    raise TranslationError(
                        "translator changed protected placeholders in "
                        f"{invalid_count}/{len(chunk)} block(s)"
                    )
                with self._write_lock, self.cache_file.open("a", encoding="utf-8") as handle:
                    for source, translation in zip(chunk, translations):
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

    def invalidate(self, texts: Sequence[str]) -> None:
        """Drop selected in-memory entries so a quality retry reaches the supplier."""
        with self._write_lock:
            for text in texts:
                self.cache.pop(cache_key(text), None)

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
        unavailable = [
            text
            for text in texts
            if cache_key(text) not in self.cache
            or not placeholders_preserved(text, self.cache[cache_key(text)])
        ]
        if unavailable:
            missing_file = self.cache_file.with_name(self.cache_file.name + ".missing.jsonl")
            with missing_file.open("w", encoding="utf-8") as handle:
                for text in unavailable:
                    cached = self.cache.get(cache_key(text))
                    record = {
                        "key": cache_key(text),
                        "source": text,
                        "reason": "invalid_placeholders" if cached is not None else "missing",
                    }
                    handle.write(json.dumps(record, ensure_ascii=False) + "\n")
            raise TranslationError(
                "cache-only mode: %d/%d blocks missing or invalid. "
                "Unavailable blocks dumped to %s"
                % (len(unavailable), len(texts), missing_file)
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
    # Structure-aware: block types for context-aware prompts
    block_types: Optional[List[str]] = None

    def translate_batch(self, texts: Sequence[str]) -> List[str]:
        results: List[str] = []
        chunks = list(chunked_by_size(list(texts), self.batch_size, self.max_batch_chars))
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
        try:
            translated = self._translate_chunk(chunk)
        except (TranslationError, json.JSONDecodeError) as exc:
            if len(chunk) == 1:
                return [self._translate_single_plain(chunk[0], exc)]
            if self.progress:
                print(
                    "Warning: supplier response could not be parsed for %d inputs; "
                    "retrying one by one" % len(chunk),
                    file=sys.stderr,
                    flush=True,
                )
            return self._translate_items_one_by_one(chunk)

        if len(translated) == len(chunk):
            return translated
        if len(chunk) == 1:
            return [self._translate_single_plain(
                chunk[0],
                TranslationError(
                    "Supplier returned %d translations for 1 input" % len(translated)
                ),
            )]

        if self.progress:
            print(
                "Warning: supplier returned %d translations for %d inputs; retrying one by one"
                % (len(translated), len(chunk)),
                file=sys.stderr,
                flush=True,
            )
        return self._translate_items_one_by_one(chunk)

    def _translate_items_one_by_one(self, chunk: Sequence[str]) -> List[str]:
        singles: List[str] = []
        for item in chunk:
            single = self._translate_chunk_with_fallback([item])
            if len(single) != 1:
                raise TranslationError(
                    "Supplier returned %d translations for 1 input during fallback"
                    % len(single)
                )
            singles.extend(single)
        return singles

    def _translate_single_plain(self, text: str, reason: BaseException) -> str:
        if self.mode not in {"deepseek", "openai-compatible"}:
            raise TranslationError(
                "Supplier JSON response was unusable and plain-text fallback is unsupported: %s"
                % reason
            )
        if self.progress:
            print(
                "Warning: single-item JSON translation failed (%s); using plain-text fallback"
                % reason,
                file=sys.stderr,
                flush=True,
            )
        if self.mode == "deepseek":
            return self._translate_deepseek_plain(text)
        return self._translate_openai_plain(text)

    def _translate_openai_plain(self, text: str) -> str:
        payload = {
            "model": self.model or "gpt-4o-mini",
            "messages": [
                {"role": "system", "content": single_translation_prompt()},
                {"role": "user", "content": text},
            ],
            "temperature": 0,
            "max_tokens": self.max_output_tokens,
        }
        self._apply_openai_compatible_model_constraints(payload)
        data = self._post_json(normalize_chat_url(self.api_url), payload)
        return coerce_plain_translation(extract_openai_message(data))

    def _translate_deepseek_plain(self, text: str) -> str:
        payload: Dict[str, Any] = {
            "model": self.model or "deepseek-v4-pro",
            "messages": [
                {"role": "system", "content": single_translation_prompt()},
                {"role": "user", "content": text},
            ],
            "temperature": 0,
            "max_tokens": self.max_output_tokens,
            "stream": False,
            "thinking": {"type": self.deepseek_thinking},
        }
        if self.deepseek_thinking == "enabled":
            payload["reasoning_effort"] = self.reasoning_effort
        data = self._post_json(normalize_deepseek_chat_url(self.api_url), payload)
        return coerce_plain_translation(extract_openai_message(data))

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
        prompt = translation_array_prompt_with_types(self.block_types, list(texts))
        payload = {
            "model": self.model or "gpt-4o-mini",
            "messages": [
                {"role": "system", "content": prompt},
                {"role": "user", "content": json.dumps(list(texts), ensure_ascii=False)},
            ],
            "temperature": 0,
            "max_tokens": self.max_output_tokens,
        }
        self._apply_openai_compatible_model_constraints(payload)
        data = self._post_json(normalize_chat_url(self.api_url), payload)
        content = extract_openai_message(data)
        return parse_json_string_list(content)

    def _apply_openai_compatible_model_constraints(self, payload: Dict[str, Any]) -> None:
        """Apply model-specific request constraints without changing other providers."""
        if not (self.model or "").lower().startswith("kimi-k3"):
            return
        # Kimi K3 fixes its sampling values server-side and rejects temperature,
        # top_p, and penalty overrides. Its completion budget uses the current
        # OpenAI-compatible max_completion_tokens field.
        payload.pop("temperature", None)
        max_tokens = payload.pop("max_tokens", None)
        if max_tokens is not None:
            payload["max_completion_tokens"] = max_tokens
        payload["reasoning_effort"] = "max"

    def _translate_deepseek(self, texts: Sequence[str]) -> List[str]:
        prompt = translation_array_prompt_with_types(self.block_types, list(texts))
        payload: Dict[str, Any] = {
            "model": self.model or "deepseek-v4-pro",
            "messages": [
                {"role": "system", "content": prompt},
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
                time.sleep(0.8 * (2**attempt))
        raise TranslationError("Translation API request failed: %s" % last_error)


def build_translator_from_args(args: Any) -> Translator:
    if getattr(args, "dry_run", False):
        return EchoTranslator()

    if args.api_mode == "cache-only":
        if not args.cache_file:
            raise TranslationError("cache-only mode requires --cache-file.")
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
        raise TranslationError("Missing DeepSeek API key. Set DEEPSEEK_API_KEY.")

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
    "6. 【占位符保护】形如 ⟦0⟧、⟦12⟧ 的占位符代表公式或代码片段，"
    "必须逐字原样保留在原位置；不得删除、翻译、改写、合并或移到句末\n"
    "   输出前逐项检查：输入中的每一个 ⟦n⟧ 在对应译文中必须出现且只出现一次\n"
    '7. 【被动语态】英文被动翻译为中文无主句或"本文"作主语\n'
    "8. 【图表】Figure 1→图1，Table 2→表2\n"
    "9. 【长句拆分】英文长句拆分为多个中文短句\n"
)


def translation_array_prompt() -> str:
    return (
        _TRANSLATION_RULES + "\nTranslate each item in the JSON array "
        "from English to Simplified Chinese. "
        "Preserve numbers, citations, equations, URLs, "
        "product names, placeholders like ⟦0⟧, and line breaks where possible. "
        "Every placeholder token must be copied exactly once in its original position. "
        "Do not add commentary. Return only a valid "
        "JSON array of strings with the same length and order."
    )


def single_translation_prompt() -> str:
    return (
        _TRANSLATION_RULES + "\nTranslate the user's text from English to Simplified Chinese. "
        "Preserve numbers, citations, equations, URLs, product names, and placeholders "
        "like ⟦0⟧ exactly. Do not add commentary, labels, Markdown fences, or JSON. "
        "Return only the translated Chinese text."
    )


# Structure-aware type hints for block classification
_BLOCK_TYPE_HINTS: dict[str, str] = {
    "title": "【注意】这是论文标题，翻译要简洁准确，保留原标题的学术风格。",
    "heading": "【注意】这是章节标题，翻译为中文，保留编号格式（如 1、2.1、A.）。",
    "caption": "【注意】这是图注或表注。Figure N→图N，Table N→表N。保留描述准确性，保持简洁。",
    "body": "",  # standard prompt, no extra hint
}


def translation_array_prompt_with_types(
    block_types: list[str] | None = None,
    texts: list[str] | None = None,
) -> str:
    """Build translation prompt with block-type-specific hints and terminology.

    When block_types is provided, adds context about what each text block is
    (title, heading, caption, body) so the LLM can translate appropriately.
    When texts is provided, includes relevant terminology from the corpus.
    """
    base = _TRANSLATION_RULES

    if block_types:
        unique_types = set(block_types)
        hints = []
        for bt in unique_types:
            hint = _BLOCK_TYPE_HINTS.get(bt, "")
            if hint:
                hints.append(hint)
        if hints:
            base += "\n\n" + "\n".join(hints)

    # Add relevant terminology from corpus
    if texts:
        try:
            from pdf_zh_translator.corpus import build_terminology_prompt, get_relevant_terms

            terms = get_relevant_terms(texts)
            term_prompt = build_terminology_prompt(terms)
            if term_prompt:
                base += "\n\n" + term_prompt
        except Exception:
            pass  # Corpus is optional; don't fail translation if it's missing

    return (
        base + "\nTranslate each item in the JSON array "
        "from English to Simplified Chinese. "
        "Preserve numbers, citations, equations, URLs, "
        "product names, placeholders like ⟦0⟧, and line breaks where possible. "
        "Every placeholder token must be copied exactly once in its original position. "
        "Do not add commentary. Return only a valid "
        "JSON array of strings with the same length and order."
    )


def translation_object_prompt() -> str:
    return (
        _TRANSLATION_RULES + "\nTranslate each item in the input JSON array "
        "from English to Simplified Chinese. "
        "Preserve numbers, citations, equations, URLs, "
        "product names, placeholders like ⟦0⟧, and line breaks where possible. "
        "Every placeholder token must be copied exactly once in its original position. "
        "Return only a valid json object in this shape: "
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


def coerce_plain_translation(content: str) -> str:
    cleaned = strip_markdown_fence(content).strip()
    if not cleaned:
        raise TranslationError("Plain-text fallback returned an empty translation")
    try:
        parsed = load_json_from_content(cleaned)
    except json.JSONDecodeError:
        parsed = None
    if isinstance(parsed, str):
        cleaned = parsed.strip()
    elif isinstance(parsed, list) and len(parsed) == 1 and isinstance(parsed[0], str):
        cleaned = parsed[0].strip()
    elif isinstance(parsed, dict):
        parsed_list = parse_translation_list(parsed)
        if len(parsed_list) == 1:
            cleaned = parsed_list[0].strip()
    if not cleaned:
        raise TranslationError("Plain-text fallback returned an empty translation")
    return cleaned


def load_json_from_content(content: str) -> Any:
    cleaned = strip_markdown_fence(content)

    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        match = re.search(r"(\{[\s\S]*\}|\[[\s\S]*\])", cleaned)
        if not match:
            raise
        return json.loads(match.group(0))


def strip_markdown_fence(content: str) -> str:
    cleaned = content.strip()
    if cleaned.startswith("```"):
        cleaned = re.sub(r"^```(?:json|text)?\s*", "", cleaned)
        cleaned = re.sub(r"\s*```$", "", cleaned)
    return cleaned


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
