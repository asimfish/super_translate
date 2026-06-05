"""Translation service using pdf2zh with progress tracking and quality presets."""

import asyncio
import logging
import os
import threading
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from string import Template
from typing import Callable, Optional

logger = logging.getLogger(__name__)

_model = None
_env_lock = threading.Lock()


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
        return TranslationResult(error=str(e))


def _translate_sync(
    input_path: Path,
    output_dir: Path,
    config: TranslationConfig,
    progress_callback: Optional[Callable] = None,
) -> TranslationResult:
    """Synchronous translation via pdf2zh with retry logic."""
    from pdf2zh import translate

    preset = QUALITY_PRESETS.get(config.quality, QUALITY_PRESETS[QualityPreset.BALANCED])

    # Determine service
    service_map = {
        "deepseek": "deepseek",
        "openai": "openai",
        "google": "google",
        "deepl": "deepl",
        "ollama": "ollama",
    }
    service = service_map.get(config.backend, "google")

    # Resolve API key
    api_key = config.api_key
    base_url = config.base_url
    model_name = config.model

    if config.backend == "deepseek" and not api_key:
        api_key = os.environ.get("DEEPSEEK_API_KEY", "")
        if not api_key:
            logger.warning("No DeepSeek API key, falling back to %s", preset["fallback_backend"])
            service = preset["fallback_backend"]
    elif config.backend == "openai" and not api_key:
        api_key = os.environ.get("OPENAI_API_KEY", "")
        if not api_key:
            logger.warning("No OpenAI API key, falling back to %s", preset["fallback_backend"])
            service = preset["fallback_backend"]

    # Set environment variables
    env_overrides = {}
    if service == "deepseek" and api_key:
        env_overrides["DEEPSEEK_API_KEY"] = api_key
        if base_url:
            env_overrides["DEEPSEEK_API_URL"] = base_url
        if model_name:
            env_overrides["DEEPSEEK_MODEL"] = model_name
    elif service == "openai" and api_key:
        env_overrides["OPENAI_API_KEY"] = api_key
        if base_url:
            env_overrides["OPENAI_BASE_URL"] = base_url
        if model_name:
            env_overrides["OPENAI_MODEL"] = model_name

    old_env = {}
    with _env_lock:
        for k, v in env_overrides.items():
            old_env[k] = os.environ.get(k)
            os.environ[k] = v

    # Shared progress state for sync->async communication
    progress_state = {"pct": 0.0, "msg": ""}

    try:
        last_error = None
        for attempt in range(config.max_retries + 1):
            try:
                onnx_model = get_model()

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

                threads = preset.get("threads", config.threads)

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
                )
                break  # Success

            except Exception as e:
                last_error = e
                if attempt < config.max_retries:
                    logger.warning("Translation attempt %d failed: %s. Retrying...", attempt + 1, e)
                    for f in output_dir.glob("*"):
                        f.unlink()
                else:
                    logger.error("All translation attempts failed for %s", input_path)
                    raise

    finally:
        with _env_lock:
            for k, v in old_env.items():
                if v is None:
                    os.environ.pop(k, None)
                else:
                    os.environ[k] = v

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
