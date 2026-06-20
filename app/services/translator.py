"""Translation service using pdf2zh with progress tracking and quality presets."""

from __future__ import annotations

import logging
import os
import re
import threading
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FutureTimeout
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from string import Template

logger = logging.getLogger(__name__)

# Error sanitization constants
_MAX_INITIAL_ERROR_LEN = 500  # max error message length before regex
_MAX_FINAL_ERROR_LEN = 200  # max error message length after sanitization


def sanitize_error(error: Exception) -> str:
    """Sanitize error message for user-facing display.

    Removes file paths, hostnames, IPs, and internal details that could
    leak server configuration or network topology.
    """
    msg = str(error)
    # Truncate early to bound regex processing time (prevents ReDoS)
    if len(msg) > _MAX_INITIAL_ERROR_LEN:
        msg = msg[:_MAX_INITIAL_ERROR_LEN]
    # Remove API keys (Bearer tokens, query params, env vars, sk- prefixed keys)
    msg = re.sub(r"Bearer\s+\S+", "Bearer [redacted]", msg, flags=re.IGNORECASE)
    msg = re.sub(r"(?i)(api[_-]?key|token|secret)\s*[=:]\s*\S+", r"\1=[redacted]", msg)
    msg = re.sub(r"\bsk-[a-zA-Z0-9_-]{8,}", "[redacted]", msg)
    # AWS access keys
    msg = re.sub(r"\bAKIA[0-9A-Z]{16}\b", "[redacted]", msg)
    # GitHub tokens
    msg = re.sub(r"\bgh[pousr]_[a-zA-Z0-9]{20,}\b", "[redacted]", msg)
    # Remove env var assignments that look like secrets
    msg = re.sub(r"\b[A-Z_]+(?:API_KEY|SECRET|TOKEN|PASSWORD)=[^\s,;]+", "[redacted]", msg)
    # JWT tokens (eyJ header.payload.signature)
    msg = re.sub(r"\beyJ[a-zA-Z0-9_-]+\.[a-zA-Z0-9_-]+\.[a-zA-Z0-9_-]+", "[redacted]", msg)
    # Connection strings with passwords (mongodb://, postgresql://, mysql://, redis://)
    msg = re.sub(r"\b(?:mongodb|postgresql|mysql|redis|amqp)://[^\s]+", "[redacted]", msg)
    # Private key headers
    msg = re.sub(r"-----BEGIN [A-Z ]+ KEY-----", "[redacted]", msg)
    # Remove file paths (Unix and Windows) — negative lookbehinds avoid matching URL schemes
    msg = re.sub(r"(?<![:/])/[a-zA-Z0-9._/-]+", "[path]", msg)
    msg = re.sub(r"[A-Z]:\\[^\s:]+", "[path]", msg)
    # Remove line numbers from tracebacks
    msg = re.sub(r'File "[^"]*", line \d+', 'File "[module]"', msg)
    # Remove IP addresses (IPv4 with optional port, and IPv6)
    msg = re.sub(r"\b\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}(:\d+)?\b", "[ip]", msg)
    # IPv6: bracketed form [::1]:port or bare form with 2+ colons
    msg = re.sub(r"\[[0-9a-fA-F:%]+\](?::\d+)?", "[ip]", msg)
    ipv6_re = (
        r"(?<![a-zA-Z0-9])[0-9a-fA-F]*(?::[0-9a-fA-F]*){2,}"
        r"(?:%\w+)?(?::\d+)?(?![a-zA-Z0-9])"
    )
    msg = re.sub(ipv6_re, "[ip]", msg)
    # Remove hostnames with ports (e.g., api.example.com:443, localhost:8080)
    msg = re.sub(r"\b[a-zA-Z0-9.-]+\.\w{2,}:\d+\b", "[host]", msg)
    msg = re.sub(r"\blocalhost:\d+\b", "[host]", msg)
    # Truncate long messages
    if len(msg) > _MAX_FINAL_ERROR_LEN:
        msg = msg[:_MAX_FINAL_ERROR_LEN] + "..."
    return msg

_model = None
_model_lock = threading.Lock()


class QualityPreset(str, Enum):
    """Translation quality preset enum."""

    FAST = "fast"  # Google translate, no frills
    BALANCED = "balanced"  # DeepSeek, compatible mode
    QUALITY = "quality"  # DeepSeek, full options, custom prompt


@dataclass(frozen=True)
class TranslationConfig:
    """Translation configuration."""

    backend: str = "deepseek"
    lang_in: str = "en"
    lang_out: str = "zh"
    api_key: str = ""
    base_url: str = ""
    model: str = ""
    quality: QualityPreset = QualityPreset.BALANCED
    max_retries: int = 2
    threads: int = 8  # concurrent page translation threads


# Quality presets configuration
QUALITY_PRESETS = {
    QualityPreset.FAST: {
        "compatible": False,
        "skip_subset_fonts": False,
        "prompt": None,
        "vfont": "",
        "vchar": "",
        "fallback_backend": "google",
        "threads": 12,  # Google Translate can handle high concurrency
        "skip_layout_fix": True,  # Google Translate doesn't need layout correction
    },
    QualityPreset.BALANCED: {
        "compatible": True,
        "skip_subset_fonts": True,
        "prompt": Template(
            "你是顶级学术论文翻译专家，擅长计算机科学、机器学习、数学等领域。\n\n"
            "【核心铁律】翻译必须是纯中文，严禁中英混杂！\n"
            '错误示范（绝对禁止）："我们使用了 attention mechanism 来 improve 性能"\n'
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
            "7. 【格式保护】原文中的加粗文本、斜体文本必须用对应标记保留\n"
            "   加粗用 **文本** 标记，斜体用 *文本* 标记\n"
            "8. 【标题保留】章节标题（如 Method、Introduction、Conclusion）必须翻译为中文\n"
            "   但保留其标题层级和格式\n\n"
            "待翻译文本：\n$text",
        ),
        "vfont": r"(CM[^R]|MS[MH]|EU[RS]|STIX|Lucida|Math|Symbol|Times.*Math|Cambria.*Math)",
        "vchar": r"[αβγδεζηθικλμνξπρστυφχψωΑΒΓΔΕΖΗΘΙΚΛΜΝΞΠΡΣΤΥΦΧΨΩ∑∏∫∂∇∞≈≠≤≥±×÷√∝∈∉⊂⊃∪∩¬∧∨∃∀]",
        "fallback_backend": "google",
        "threads": 8,  # DeepSeek can handle more concurrent requests
    },
    QualityPreset.QUALITY: {
        "compatible": True,
        "skip_subset_fonts": True,
        "prompt": Template(
            "你是顶级学术论文翻译专家，专攻计算机科学、机器学习、深度学习、数学等领域。\n"
            "你的翻译必须达到学术期刊发表水平。\n\n"
            "【最高铁律】输出必须是纯中文，严禁任何中英混杂！\n"
            '错误（禁止）："我们使用 attention mechanism 来 improve 性能"\n'
            '正确："本文采用注意力机制以提升性能"\n'
            '错误（禁止）："该方法 significantly 优于 baseline"\n'
            '正确："该方法显著优于基线方法"\n\n'
            "翻译规则：\n"
            "1. 【纯中文】每句话必须100%中文，不得夹杂任何英文单词\n"
            "   专有名词首次：中文术语（English Term），之后只用中文\n"
            '   例：首次"注意力机制（Attention Mechanism）"，之后"注意力机制"\n'
            "2. 【学术用语标准】\n"
            '   "本文提出"（禁"我们建议"）"达到"（禁"搞定"）\n'
            '   "显著的"（禁"重要的"）"利用"（禁"借助"）\n'
            '   "新颖的"（禁"新的"）"最先进的"（禁"最新的"）\n'
            '   "此外"（禁"另外"）"因此"（禁"所以"）\n'
            '   "然而"（禁"但是"）"综上所述"（禁"总的来说"）\n'
            "3. 【公式】数学公式、变量名原样保留（$x^2$、$\\alpha$）\n"
            "4. 【引用】[1]、[2] 保持不变\n"
            "5. 【符号】∈、∀、∃、∑、∫ 等保持不变\n"
            "6. 【代码】代码块、伪代码保持原样\n"
            "7. 【图表】Figure 1→图1，Table 2→表2\n"
            '8. 【被动语态】翻译为中文无主句或"本文"作主语\n'
            "9. 【长句拆分】英文长句拆分为多个中文短句\n"
            "10. 【格式保护】加粗用 **文本** 标记，斜体用 *文本* 标记\n"
            "11. 【标题保留】章节标题必须翻译为中文，保留标题层级\n\n"
            "待翻译文本：\n$text",
        ),
        "vfont": (
            r"(CM[^R]|MS[MH]|EU[RS]|STIX|Lucida|Math|Symbol"
            r"|Times.*Math|Cambria.*Math|CMEX|CMSY|CMMI)"
        ),
        "vchar": (
            r"[αβγδεζηθικλμνξπρστυφχψωΑΒΓΔΕΖΗΘΙΚΛΜΝΞΠΡΣΤΥΦΧΨΩ"
            r"∑∏∫∂∇∞≈≠≤≥±×÷√∝∈∉⊂⊃∪∩¬∧∨∃∀⟨⟩⌈⌉⌊⌋‖]"
        ),
        "fallback_backend": "google",
        "threads": 4,  # Quality mode uses more complex prompts, moderate concurrency
    },
}


def get_model() -> object:
    global _model
    with _model_lock:
        if _model is None:
            from pdf2zh.doclayout import OnnxModel
            logger.info("Loading layout detection model...")
            _model = OnnxModel.from_pretrained()
            logger.info("Model loaded")
    return _model


@dataclass(frozen=True)
class TranslationResult:
    """Translation result with output paths or error."""

    mono_path: Path | None = None
    dual_path: Path | None = None
    error: str | None = None

    @property
    def success(self) -> bool:
        return self.error is None and self.mono_path is not None



def translate_pdf_sync(
    input_path: Path,
    output_dir: Path,
    config: TranslationConfig,
    progress_callback: Callable | None = None,
) -> TranslationResult:
    """Synchronous translation entry point for use in thread pools.

    Delegates to _translate_sync which handles API key resolution and fallback.

    Args:
        progress_callback: Optional callable(float) receiving progress 0.0-1.0
    """
    try:
        return _translate_sync(input_path, output_dir, config, progress_callback)
    except Exception as e:
        logger.exception("Translation failed for %s", input_path)
        return TranslationResult(error=sanitize_error(e))


_BACKEND_ENV_KEYS = {
    "deepseek": "DEEPSEEK_API_KEY",
    "openai": "OPENAI_API_KEY",
    "deepl": "DEEPL_API_KEY",
}


def _resolve_service(config: TranslationConfig, fallback: str) -> str:
    """Resolve translation service name from config backend."""
    valid_backends = set(_BACKEND_ENV_KEYS) | {"google", "ollama"}
    service = config.backend if config.backend in valid_backends else "google"

    env_key = _BACKEND_ENV_KEYS.get(config.backend)
    if env_key and not config.api_key and not os.environ.get(env_key, ""):
        logger.warning("No %s, falling back to %s", config.backend, fallback)
        return fallback

    return service


# Service → (env_key_for_api_key, env_fallback, optional_model_env, optional_base_url_env)
_SERVICE_ENV_MAP: dict[str, tuple[str, str, str | None, str | None]] = {
    "deepseek": ("DEEPSEEK_API_KEY", "DEEPSEEK_API_KEY", "DEEPSEEK_MODEL", None),
    "openai": ("OPENAI_API_KEY", "OPENAI_API_KEY", "OPENAI_MODEL", "OPENAI_BASE_URL"),
    "deepl": ("DEEPL_API_KEY", "DEEPL_API_KEY", None, None),
    "ollama": (None, None, None, "OLLAMA_HOST"),
}


def _build_pdf2zh_envs(
    service: str, config: TranslationConfig,
) -> dict[str, str | None]:
    """Build envs dict for pdf2zh's translate() envs parameter.

    Passes API keys directly instead of mutating os.environ,
    enabling safe concurrent translations with different keys.
    """
    envs: dict[str, str | None] = {}
    mapping = _SERVICE_ENV_MAP.get(service)
    if not mapping:
        return envs

    key_env, fallback_env, model_env, url_env = mapping

    # API key: prefer config, fall back to environment
    if key_env:
        api_key = config.api_key or os.environ.get(fallback_env, "")
        if api_key:
            envs[key_env] = api_key

    # Model name
    if model_env and config.model:
        envs[model_env] = config.model

    # Base URL (for openai/ollama)
    if url_env:
        url = config.base_url or os.environ.get(url_env, "")
        if url:
            envs[url_env] = url

    return envs


# Backends the native engine can drive (LLM APIs speaking the DeepSeek or
# OpenAI chat protocol). Others (google/deepl/ollama) stay on pdf2zh.
_NATIVE_ENGINE_BACKENDS = {"deepseek", "openai"}


def _use_native_engine(config: TranslationConfig) -> bool:
    from app.core.config import settings

    return (
        settings.translation_engine == "native"
        and config.backend in _NATIVE_ENGINE_BACKENDS
    )


_TRANSLATION_TIMEOUT = 600  # 10 minutes max for the entire translation


class _ProgressTranslator:
    """Wrap a pdf_zh_translator Translator to report batch progress."""

    def __init__(self, inner, progress_callback: Callable | None, group_size: int = 4):
        self._inner = inner
        self._callback = progress_callback
        self._group_size = max(1, group_size)

    def translate_batch(self, texts):
        results = []
        total = len(texts)
        for start in range(0, total, self._group_size):
            group = list(texts[start : start + self._group_size])
            results.extend(self._inner.translate_batch(group))
            if self._callback and total:
                self._callback(min(1.0, (start + len(group)) / total))
        return results


def _translate_sync_native(
    input_path: Path,
    output_dir: Path,
    config: TranslationConfig,
    progress_callback: Callable | None = None,
) -> TranslationResult:
    """Synchronous translation via the in-house pdf_zh_translator engine.

    Preserves original equation typesetting (incl. sub/superscripts), strips
    gutter line numbers and prompt-injection lines, and reflows translated
    paragraphs with CJK line-breaking rules.
    """
    from app.services.library import cleanup_output_dir
    from pdf_zh_translator.pdf_layout import translate_pdf
    from pdf_zh_translator.translators import CachedTranslator, VendorTranslator

    if config.backend == "deepseek":
        api_url = os.environ.get("DEEPSEEK_API_URL", "https://api.deepseek.com")
        api_key = config.api_key or os.environ.get("DEEPSEEK_API_KEY", "")
        mode = "deepseek"
        model = config.model or "deepseek-v4-pro"
    else:  # openai
        api_url = config.base_url or "https://api.openai.com/v1"
        api_key = config.api_key or os.environ.get("OPENAI_API_KEY", "")
        mode = "openai-compatible"
        model = config.model or "gpt-4o-mini"

    mono_path = output_dir / f"{input_path.stem}-mono.pdf"
    cache_path = output_dir / f"{input_path.stem}.translation-cache.jsonl"

    vendor = VendorTranslator(
        api_url=api_url,
        api_key=api_key,
        mode=mode,
        model=model,
        source_lang=config.lang_in,
        target_lang=config.lang_out,
        progress=False,
    )
    translator = _ProgressTranslator(
        CachedTranslator(vendor, cache_path), progress_callback
    )

    for attempt in range(config.max_retries + 1):
        try:
            with ThreadPoolExecutor(max_workers=1) as pool:
                future = pool.submit(
                    translate_pdf,
                    input_pdf=input_path,
                    output_pdf=mono_path,
                    translator=translator,
                )
                report = future.result(timeout=_TRANSLATION_TIMEOUT)
            for warning in report.warnings:
                logger.warning("Native engine: %s", warning)
            break
        except FutureTimeout:
            cleanup_output_dir(output_dir)
            output_dir.mkdir(parents=True, exist_ok=True)
            logger.error(
                "Native translation timed out after %ds for %s",
                _TRANSLATION_TIMEOUT, input_path.name,
            )
            raise TimeoutError(
                f"Translation timed out after {_TRANSLATION_TIMEOUT}s"
            )
        except Exception as e:
            cleanup_output_dir(output_dir)
            output_dir.mkdir(parents=True, exist_ok=True)
            if attempt < config.max_retries:
                logger.warning(
                    "Native translation attempt %d failed: %s. Retrying...",
                    attempt + 1, sanitize_error(e),
                )
            else:
                logger.error("All native translation attempts failed for %s", input_path.name)
                raise

    if not mono_path.exists():
        return TranslationResult(error="Translation produced no output files")
    return TranslationResult(mono_path=mono_path, dual_path=None)


def _translate_sync(
    input_path: Path,
    output_dir: Path,
    config: TranslationConfig,
    progress_callback: Callable | None = None,
) -> TranslationResult:
    """Synchronous translation via pdf2zh with retry logic."""
    if _use_native_engine(config):
        return _translate_sync_native(input_path, output_dir, config, progress_callback)

    from pdf2zh import translate

    from app.services.library import cleanup_output_dir

    preset = QUALITY_PRESETS.get(config.quality, QUALITY_PRESETS[QualityPreset.BALANCED])

    service = _resolve_service(config, preset["fallback_backend"])
    envs = _build_pdf2zh_envs(service, config)

    onnx_model = get_model()
    threads = preset.get("threads", config.threads)

    pdf2zh_callback = _create_progress_callback(progress_callback)

    for attempt in range(config.max_retries + 1):
        try:
            translate(
                files=[str(input_path)],
                lang_in=config.lang_in,
                lang_out=config.lang_out,
                service=service,
                output=str(output_dir),
                model=onnx_model,
                thread=threads,
                callback=pdf2zh_callback,
                compatible=preset["compatible"],
                skip_subset_fonts=preset["skip_subset_fonts"],
                prompt=preset["prompt"],
                vfont=preset.get("vfont", ""),
                vchar=preset.get("vchar", ""),
                envs=envs or None,
            )
            break  # Success

        except Exception as e:
            # Clean up partial output from this attempt (files and subdirs)
            cleanup_output_dir(output_dir)
            output_dir.mkdir(parents=True, exist_ok=True)
            if attempt < config.max_retries:
                logger.warning(
                    "Translation attempt %d failed: %s. Retrying...",
                    attempt + 1, sanitize_error(e),
                )
            else:
                logger.error("All translation attempts failed for %s", input_path.name)
                raise

    skip_layout = preset.get("skip_layout_fix", False)
    return _collect_output(input_path, output_dir, skip_layout_fix=skip_layout)


def _create_progress_callback(
    progress_callback: Callable | None,
) -> Callable:
    """Create a pdf2zh-compatible progress callback."""
    def pdf2zh_callback(*args: object) -> None:
        try:
            pct = None
            if len(args) == 1 and hasattr(args[0], "n") and hasattr(args[0], "total"):
                # tqdm progress object
                p = args[0]
                pct = p.n / p.total if p.total > 0 else 0
            elif len(args) == 2:  # noqa: PLR2004 - pdf2zh callback (current, total) arity
                current, total = args
                pct = current / total if total > 0 else 0
            elif len(args) == 1 and isinstance(args[0], (int, float)):
                pct = args[0]

            if pct is not None:
                pct = max(0.0, min(1.0, pct))
                logger.debug("Translation progress: %.0f%%", pct * 100)
                if progress_callback:
                    progress_callback(pct)
        except Exception as e:
            logger.warning("Progress callback error: %s", e)

    return pdf2zh_callback


def _collect_output(
    input_path: Path,
    output_dir: Path,
    *,
    skip_layout_fix: bool = False,
) -> TranslationResult:
    """Find and validate translation output files."""
    stem = input_path.stem
    mono_path = output_dir / f"{stem}-mono.pdf"
    dual_path = output_dir / f"{stem}-dual.pdf"

    if not mono_path.exists():
        mono_candidates = list(output_dir.glob("*mono*"))
        mono_path = mono_candidates[0] if mono_candidates else None

    if not dual_path.exists():
        dual_candidates = list(output_dir.glob("*dual*"))
        dual_path = dual_candidates[0] if dual_candidates else None

    if mono_path is None and dual_path is None:
        any_pdf = list(output_dir.glob("*.pdf"))
        if any_pdf:
            mono_path = any_pdf[0]
        else:
            return TranslationResult(error="Translation produced no output files")

    # Post-process: fix text block layout issues from pdf2zh
    if not skip_layout_fix:
        try:
            from app.services.layout_fix import fix_translated_layout

            if mono_path and mono_path.exists():
                fix_translated_layout(mono_path)
            if dual_path and dual_path.exists():
                fix_translated_layout(dual_path)
        except Exception as e:
            logger.warning("Layout post-processing failed (non-fatal): %s", e)

    return TranslationResult(mono_path=mono_path, dual_path=dual_path)
