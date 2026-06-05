"""Translation service using pdf2zh with progress tracking and quality presets."""

import asyncio
import logging
import os
import re
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from string import Template
from typing import Callable, Optional

logger = logging.getLogger(__name__)


def sanitize_error(error: Exception) -> str:
    """Sanitize error message for user-facing display.

    Removes file paths, hostnames, IPs, and internal details that could
    leak server configuration or network topology.
    """
    msg = str(error)
    # Remove API keys (Bearer tokens, query params, env vars, sk- prefixed keys)
    msg = re.sub(r"Bearer\s+\S+", "Bearer [redacted]", msg, flags=re.IGNORECASE)
    msg = re.sub(r"(?i)(api[_-]?key|token|secret)\s*[=:]\s*\S+", r"\1=[redacted]", msg)
    msg = re.sub(r"\bsk-[a-zA-Z0-9_-]{8,}", "[redacted]", msg)
    # Remove env var assignments that look like secrets
    msg = re.sub(r"\b[A-Z_]+(?:API_KEY|SECRET|TOKEN|PASSWORD)=[^\s,;]+", "[redacted]", msg)
    # Remove file paths (Unix and Windows)
    msg = re.sub(r"(/[^\s:]+)+", "[path]", msg)
    msg = re.sub(r"([A-Z]:\\[^\s:]+)+", "[path]", msg)
    # Remove line numbers from tracebacks
    msg = re.sub(r'File "[^"]*", line \d+', 'File "[module]"', msg)
    # Remove IP addresses (IPv4 with optional port, and IPv6)
    msg = re.sub(r"\b\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}(:\d+)?\b", "[ip]", msg)
    # IPv6: bracketed form [::1]:port or bare form with 2+ colons
    msg = re.sub(r"\[[0-9a-fA-F:%]+\](?::\d+)?", "[ip]", msg)
    msg = re.sub(r"(?<![a-zA-Z0-9])[0-9a-fA-F]*(?::[0-9a-fA-F]*){2,}(?:%\w+)?(?::\d+)?(?![a-zA-Z0-9])", "[ip]", msg)
    # Remove hostnames with ports (e.g., api.example.com:443, localhost:8080)
    msg = re.sub(r"\b[a-zA-Z0-9.-]+\.\w{2,}:\d+\b", "[host]", msg)
    msg = re.sub(r"\blocalhost:\d+\b", "[host]", msg)
    # Truncate long messages
    if len(msg) > 200:
        msg = msg[:200] + "..."
    return msg

_model = None


class QualityPreset(str, Enum):
    FAST = "fast"  # Google translate, no frills
    BALANCED = "balanced"  # DeepSeek, compatible mode
    QUALITY = "quality"  # DeepSeek, full options, custom prompt


@dataclass(frozen=True)
class TranslationConfig:
    backend: str = "deepseek"
    lang_in: str = "en"
    lang_out: str = "zh"
    api_key: str = ""
    base_url: str = ""
    model: str = ""
    quality: QualityPreset = QualityPreset.BALANCED
    max_retries: int = 2
    threads: int = 4  # concurrent page translation threads


# Quality presets configuration
QUALITY_PRESETS = {
    QualityPreset.FAST: {
        "compatible": False,
        "skip_subset_fonts": False,
        "prompt": None,
        "vfont": "",
        "vchar": "",
        "fallback_backend": "google",
        "threads": 8,  # Google Translate can handle more concurrency
    },
    QualityPreset.BALANCED: {
        "compatible": True,
        "skip_subset_fonts": True,
        "prompt": Template(
            "你是顶级学术论文翻译专家，擅长计算机科学、机器学习、数学等领域。\n\n"
            "【核心铁律】翻译必须是纯中文，严禁中英混杂！\n"
            "错误示范（绝对禁止）：\"我们使用了 attention mechanism 来 improve 性能\"\n"
            "正确示范：\"本文采用注意力机制以提升性能\"\n\n"
            "翻译规则：\n"
            "1. 【纯中文输出】每个句子必须是完整中文，不得夹杂英文单词或短语\n"
            "   专有名词首次出现时格式为：中文术语（English Term），之后只用中文\n"
            "   例如：首次\"神经网络（Neural Network）\"，之后\"神经网络\"\n"
            "2. 【学术用语】必须使用学术论文标准表达：\n"
            "   \"本文提出\"（非\"我们建议\"）、\"达到\"（非\"搞定\"）、\"显著的\"（非\"重要的\"）\n"
            "   \"利用\"（非\"借助\"）、\"新颖的\"（非\"新的\"）、\"最先进的\"（非\"最新的\"）\n"
            "   \"此外\"（非\"另外\"）、\"因此\"（非\"所以\"）、\"然而\"（非\"但是\"）\n"
            "3. 【公式保护】数学公式、方程、变量名原样保留（如 $x^2$、$\\alpha$、E=mc²）\n"
            "4. 【引用保护】引用标记 [1]、[2] 保持不变\n"
            "5. 【符号保护】数学符号 ∈、∀、∃、∑、∫ 等保持不变\n"
            "6. 【被动语态】英文被动翻译为中文无主句或\"本文\"作主语\n\n"
            "待翻译文本：\n$text"
        ),
        "vfont": r"(CM[^R]|MS[MH]|EU[RS]|STIX|Lucida|Math|Symbol|Times.*Math|Cambria.*Math)",
        "vchar": r"[αβγδεζηθικλμνξπρστυφχψωΑΒΓΔΕΖΗΘΙΚΛΜΝΞΠΡΣΤΥΦΧΨΩ∑∏∫∂∇∞≈≠≤≥±×÷√∝∈∉⊂⊃∪∩¬∧∨∃∀]",
        "fallback_backend": "google",
        "threads": 4,  # DeepSeek needs rate limiting
    },
    QualityPreset.QUALITY: {
        "compatible": True,
        "skip_subset_fonts": True,
        "prompt": Template(
            "你是顶级学术论文翻译专家，专攻计算机科学、机器学习、深度学习、数学等领域。\n"
            "你的翻译必须达到学术期刊发表水平。\n\n"
            "【最高铁律】输出必须是纯中文，严禁任何中英混杂！\n"
            "错误（禁止）：\"我们使用 attention mechanism 来 improve 性能\"\n"
            "正确：\"本文采用注意力机制以提升性能\"\n"
            "错误（禁止）：\"该方法 significantly 优于 baseline\"\n"
            "正确：\"该方法显著优于基线方法\"\n\n"
            "翻译规则：\n"
            "1. 【纯中文】每句话必须100%中文，不得夹杂任何英文单词\n"
            "   专有名词首次：中文术语（English Term），之后只用中文\n"
            "   例：首次\"注意力机制（Attention Mechanism）\"，之后\"注意力机制\"\n"
            "2. 【学术用语标准】\n"
            "   \"本文提出\"（禁\"我们建议\"）\"达到\"（禁\"搞定\"）\"显著的\"（禁\"重要的\"）\n"
            "   \"利用\"（禁\"借助\"）\"新颖的\"（禁\"新的\"）\"最先进的\"（禁\"最新的\"）\n"
            "   \"此外\"（禁\"另外\"）\"因此\"（禁\"所以\"）\"然而\"（禁\"但是\"）\"综上所述\"（禁\"总的来说\"）\n"
            "3. 【公式】数学公式、变量名原样保留（$x^2$、$\\alpha$）\n"
            "4. 【引用】[1]、[2] 保持不变\n"
            "5. 【符号】∈、∀、∃、∑、∫ 等保持不变\n"
            "6. 【代码】代码块、伪代码保持原样\n"
            "7. 【图表】Figure 1→图1，Table 2→表2\n"
            "8. 【被动语态】翻译为中文无主句或\"本文\"作主语\n"
            "9. 【长句拆分】英文长句拆分为多个中文短句\n\n"
            "待翻译文本：\n$text"
        ),
        "vfont": r"(CM[^R]|MS[MH]|EU[RS]|STIX|Lucida|Math|Symbol|Times.*Math|Cambria.*Math|CMEX|CMSY|CMMI)",
        "vchar": r"[αβγδεζηθικλμνξπρστυφχψωΑΒΓΔΕΖΗΘΙΚΛΜΝΞΠΡΣΤΥΦΧΨΩ∑∏∫∂∇∞≈≠≤≥±×÷√∝∈∉⊂⊃∪∩¬∧∨∃∀⟨⟩⌈⌉⌊⌋‖]",
        "fallback_backend": "google",
        "threads": 2,  # Quality mode uses more complex prompts, fewer concurrent
    },
}


def get_model():
    global _model
    if _model is None:
        from pdf2zh.doclayout import OnnxModel
        logger.info("Loading layout detection model...")
        _model = OnnxModel.from_pretrained()
        logger.info("Model loaded")
    return _model


class TranslationResult:
    def __init__(
        self,
        mono_path: Optional[Path] = None,
        dual_path: Optional[Path] = None,
        error: Optional[str] = None,
    ):
        self.mono_path = mono_path
        self.dual_path = dual_path
        self.error = error

    @property
    def success(self) -> bool:
        return self.error is None and self.mono_path is not None



def translate_pdf_sync(
    input_path: Path,
    output_dir: Path,
    config: TranslationConfig,
) -> TranslationResult:
    """Synchronous translation entry point for use in thread pools.

    Delegates to _translate_sync which handles API key resolution and fallback.
    """
    try:
        return _translate_sync(input_path, output_dir, config)
    except Exception as e:
        logger.exception("Translation failed for %s", input_path)
        return TranslationResult(error=sanitize_error(e))


def _resolve_service(config: TranslationConfig, fallback: str) -> str:
    """Resolve translation service name from config backend."""
    service_map = {
        "deepseek": "deepseek",
        "openai": "openai",
        "google": "google",
        "deepl": "deepl",
        "ollama": "ollama",
    }
    service = service_map.get(config.backend, "google")

    if config.backend == "deepseek" and not config.api_key:
        if not os.environ.get("DEEPSEEK_API_KEY", ""):
            logger.warning("No DeepSeek API key, falling back to %s", fallback)
            return fallback
    elif config.backend == "openai" and not config.api_key:
        if not os.environ.get("OPENAI_API_KEY", ""):
            logger.warning("No OpenAI API key, falling back to %s", fallback)
            return fallback

    return service


def _build_pdf2zh_envs(
    service: str, config: TranslationConfig
) -> dict[str, str | None]:
    """Build envs dict for pdf2zh's translate() envs parameter.

    Passes API keys directly instead of mutating os.environ,
    enabling safe concurrent translations with different keys.
    """
    envs: dict[str, str | None] = {}
    api_key = config.api_key
    model_name = config.model

    if service == "deepseek":
        # Fall back to environment if config has no key
        if not api_key:
            api_key = os.environ.get("DEEPSEEK_API_KEY", "")
        if api_key:
            envs["DEEPSEEK_API_KEY"] = api_key
        if model_name:
            envs["DEEPSEEK_MODEL"] = model_name
    elif service == "openai":
        if not api_key:
            api_key = os.environ.get("OPENAI_API_KEY", "")
        if api_key:
            envs["OPENAI_API_KEY"] = api_key
        if config.base_url:
            envs["OPENAI_BASE_URL"] = config.base_url
        if model_name:
            envs["OPENAI_MODEL"] = model_name

    return envs


def _translate_sync(
    input_path: Path,
    output_dir: Path,
    config: TranslationConfig,
    progress_callback: Optional[Callable] = None,
) -> TranslationResult:
    """Synchronous translation via pdf2zh with retry logic."""
    from pdf2zh import translate

    preset = QUALITY_PRESETS.get(config.quality, QUALITY_PRESETS[QualityPreset.BALANCED])

    service = _resolve_service(config, preset["fallback_backend"])
    envs = _build_pdf2zh_envs(service, config)

    # Shared progress state for sync->async communication
    progress_state: dict[str, float | str] = {"pct": 0.0, "msg": ""}

    onnx_model = get_model()
    threads = preset.get("threads", config.threads)

    def pdf2zh_callback(*args):
        try:
            if len(args) == 2:
                current, total = args
                pct = current / total if total > 0 else 0
            elif len(args) == 1:
                pct = args[0]
            else:
                return

            progress_state["pct"] = pct
            progress_state["msg"] = f"Translating... {pct*100:.0f}%"

            if progress_callback:
                try:
                    loop = asyncio.get_event_loop()
                    if loop.is_running():
                        asyncio.run_coroutine_threadsafe(
                            progress_callback(pct, progress_state["msg"]),
                            loop
                        )
                except Exception:
                    pass
        except Exception as e:
            logger.debug("Progress callback error: %s", e)

    last_error = None
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
            last_error = e
            # Clean up partial output files from this attempt
            for f in output_dir.glob("*"):
                f.unlink()
            if attempt < config.max_retries:
                logger.warning("Translation attempt %d failed: %s. Retrying...", attempt + 1, e)
            else:
                logger.error("All translation attempts failed for %s", input_path)
                raise

    # Find output files
    stem = input_path.stem
    mono_path = output_dir / f"{stem}-mono.pdf"
    dual_path = output_dir / f"{stem}-dual.pdf"

    if not mono_path.exists():
        mono_candidates = list(output_dir.glob("*mono*"))
        if mono_candidates:
            mono_path = mono_candidates[0]
        else:
            mono_path = None

    if not dual_path.exists():
        dual_candidates = list(output_dir.glob("*dual*"))
        if dual_candidates:
            dual_path = dual_candidates[0]
        else:
            dual_path = None

    if mono_path is None and dual_path is None:
        any_pdf = list(output_dir.glob("*.pdf"))
        if any_pdf:
            mono_path = any_pdf[0]

    # Post-process: fix text block layout issues from pdf2zh
    try:
        from app.services.layout_fix import fix_translated_layout

        if mono_path and mono_path.exists():
            fix_translated_layout(mono_path)
        if dual_path and dual_path.exists():
            fix_translated_layout(dual_path)
    except Exception as e:
        logger.warning("Layout post-processing failed (non-fatal): %s", e)

    return TranslationResult(mono_path=mono_path, dual_path=dual_path)
