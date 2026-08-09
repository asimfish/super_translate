"""End-to-end PDF layout tests for native translation."""

from collections import Counter
from pathlib import Path
from unittest.mock import patch

import fitz
import pytest

from pdf_zh_translator.pdf_layout import create_dual_pdf, translate_pdf, verify_translation


class _RetryingStubTranslator:
    def __init__(self):
        self.calls: list[list[str]] = []
        self.block_types: list[str] = []

    def translate_batch(self, texts):
        batch = list(texts)
        self.calls.append(batch)
        outputs = []
        first_call = len(self.calls) == 1
        for text in batch:
            if first_call and "proposed method" in text:
                outputs.append(text)
            elif text.startswith("1 Introduction"):
                outputs.append("1 引言")
            elif text.startswith("Figure 1"):
                outputs.append("图1：系统流程图，展示输入、模型和输出。")
            elif text.startswith("References"):
                outputs.append("参考文献")
            elif "proposed method" in text:
                outputs.append("所提出的方法提升训练目标，并降低推理延迟。")
            elif "paragraph after the figure" in text:
                outputs.append("图后的段落说明版面仍然稳定，并且不会与图注重叠。")
            else:
                outputs.append("中文译文")
        return outputs


class _InvalidationRequiredTranslator:
    def __init__(self):
        self.invalidated: list[list[str]] = []
        self.block_types: list[str] = []

    def invalidate(self, texts):
        self.invalidated.append(list(texts))

    def translate_batch(self, texts):
        invalidated = {text for batch in self.invalidated for text in batch}
        outputs = []
        for text in texts:
            if "proposed method" in text and text not in invalidated:
                outputs.append(text)
            elif "proposed method" in text:
                outputs.append("所提出的方法提升训练目标，并降低推理延迟。")
            elif text.startswith("1 Introduction"):
                outputs.append("1 引言")
            elif text.startswith("Figure 1"):
                outputs.append("图1：系统流程图，展示输入、模型和输出。")
            elif "paragraph after the figure" in text:
                outputs.append("图后的段落说明版面仍然稳定，并且不会与图注重叠。")
            else:
                outputs.append("中文译文")
        return outputs


def _build_academic_fixture(path):
    document = fitz.open()
    page = document.new_page(width=500, height=700)

    page.insert_text((72, 54), "1 Introduction", fontsize=14)
    page.insert_text(
        (72, 88),
        "The proposed method improves the training objective and reduces inference latency.",
        fontsize=10,
    )
    page.insert_text((150, 132), "x = y = z + 0", fontsize=11)

    figure_rect = fitz.Rect(92, 175, 408, 285)
    page.draw_rect(figure_rect, color=(0.1, 0.2, 0.5), width=1)
    page.draw_line((115, 230), (385, 230), color=(0.1, 0.2, 0.5), width=1)
    pixmap = fitz.Pixmap(fitz.csRGB, fitz.IRect(0, 0, 4, 4), False)
    pixmap.clear_with(0x336699)
    page.insert_image(fitz.Rect(110, 190, 170, 250), pixmap=pixmap)
    page.insert_text((112, 267), "Input", fontsize=7)

    page.insert_text(
        (92, 312),
        "Figure 1: System overview with input, model, and output components.",
        fontsize=9,
    )
    page.insert_text(
        (72, 374),
        "This paragraph after the figure should keep enough distance from the caption.",
        fontsize=10,
    )
    page.insert_text((72, 568), "References", fontsize=12)
    page.insert_text(
        (72, 594),
        "[1] Smith et al. Learning representations for AI systems. 2024.",
        fontsize=8,
    )

    document.save(path)
    document.close()


def test_translate_pdf_preserves_formula_image_and_translates_caption(tmp_path):
    input_pdf = tmp_path / "paper.pdf"
    output_pdf = tmp_path / "paper.zh.pdf"
    _build_academic_fixture(input_pdf)

    translator = _RetryingStubTranslator()
    report = translate_pdf(
        input_pdf=input_pdf,
        output_pdf=output_pdf,
        translator=translator,
        preserve_graphics_text=True,
    )

    original = fitz.open(input_pdf)
    translated = fitz.open(output_pdf)
    original_text = original[0].get_text("text")
    translated_text = translated[0].get_text("text")
    original_images = len(original[0].get_images())
    translated_images = len(translated[0].get_images())
    original_drawings = len(original[0].get_drawings())
    translated_drawings = len(translated[0].get_drawings())
    translated.close()
    original.close()

    issues = verify_translation(input_pdf, output_pdf)

    assert report.translated_blocks >= 4
    assert len(translator.calls) == 2
    assert "The proposed method" not in translated_text
    assert "Figure 1:" not in translated_text
    assert "所提出的方法" in translated_text
    assert "图1" in translated_text
    assert "x = y = z + 0" in translated_text
    assert "Input" in translated_text
    assert "[1] Smith et al." in translated_text
    assert "x = y = z + 0" in original_text
    assert translated_images == original_images == 1
    assert translated_drawings >= max(1, original_drawings // 2)
    assert b"/Linearized" in output_pdf.read_bytes()[:2048]
    assert issues == []


def test_create_dual_pdf_saves_linearized_output(tmp_path):
    original_pdf = tmp_path / "paper.pdf"
    translated_pdf = tmp_path / "paper.zh.pdf"
    dual_pdf = tmp_path / "paper.dual.pdf"
    _build_academic_fixture(original_pdf)
    _build_academic_fixture(translated_pdf)

    create_dual_pdf(original_pdf, translated_pdf, dual_pdf)

    assert b"/Linearized" in dual_pdf.read_bytes()[:2048]


def test_create_dual_pdf_preserves_previous_output_when_save_fails(tmp_path):
    original_pdf = tmp_path / "paper.pdf"
    translated_pdf = tmp_path / "paper.zh.pdf"
    dual_pdf = tmp_path / "paper.dual.pdf"
    _build_academic_fixture(original_pdf)
    _build_academic_fixture(translated_pdf)
    dual_pdf.write_bytes(b"previous dual output")

    def fail_after_partial_write(_document, output_path):
        output_path.write_bytes(b"partial")
        raise OSError("disk full")

    with (
        patch(
            "pdf_zh_translator.pdf_layout.save_pdf_for_fast_web_view",
            side_effect=fail_after_partial_write,
        ),
        pytest.raises(OSError, match="disk full"),
    ):
        create_dual_pdf(original_pdf, translated_pdf, dual_pdf)

    assert dual_pdf.read_bytes() == b"previous dual output"


def test_translate_pdf_invalidates_bad_cached_output_before_retry(tmp_path):
    input_pdf = tmp_path / "paper.pdf"
    output_pdf = tmp_path / "paper.zh.pdf"
    _build_academic_fixture(input_pdf)
    translator = _InvalidationRequiredTranslator()

    translate_pdf(
        input_pdf=input_pdf,
        output_pdf=output_pdf,
        translator=translator,
        preserve_graphics_text=True,
    )

    translated = fitz.open(output_pdf)
    translated_text = translated[0].get_text("text")
    translated.close()

    assert len(translator.invalidated) == 1
    assert any("proposed method" in text for text in translator.invalidated[0])
    assert "The proposed method" not in translated_text
    assert "所提出的方法" in translated_text


def test_saved_pdf_subsets_large_embedded_cjk_font(tmp_path):
    """The full CJK font file is ~16MB; the saved PDF must carry only the
    glyphs actually used."""
    from pdf_zh_translator.pdf_layout import (
        find_default_font_file,
        save_pdf_for_fast_web_view,
    )

    font_file = find_default_font_file()
    if not font_file:
        pytest.skip("no CJK font available on this machine")

    document = fitz.open()
    page = document.new_page(width=612, height=792)
    page.insert_font(fontname="zhbody", fontfile=font_file)
    page.insert_text(
        (72, 100), "这是一段用于验证字体子集化的中文文本。", fontname="zhbody", fontsize=11
    )
    page.insert_text(
        (72, 130), "第二行覆盖更多汉字：翻译、版面、公式、图注。", fontname="zhbody", fontsize=11
    )

    output_pdf = tmp_path / "subset.pdf"
    warnings: list[str] = []
    save_pdf_for_fast_web_view(document, output_pdf, warnings)
    document.close()

    assert output_pdf.stat().st_size < 5_000_000, warnings

    reopened = fitz.open(output_pdf)
    text = reopened[0].get_text("text")
    reopened.close()
    assert "字体子集化" in text
    assert "图注" in text


def test_saved_pdf_dedupes_repeated_images(tmp_path):
    """show_pdf_page copies a source page's images once per call; the saved
    output must not carry dozens of byte-identical image streams."""
    import pikepdf

    from pdf_zh_translator.pdf_layout import save_pdf_for_fast_web_view

    source = fitz.open()
    src_page = source.new_page(width=200, height=200)
    pixmap = fitz.Pixmap(fitz.csRGB, fitz.IRect(0, 0, 64, 64))
    pixmap.clear_with(200)
    src_page.insert_image(fitz.Rect(0, 0, 200, 200), pixmap=pixmap)

    document = fitz.open()
    for _ in range(3):
        page = document.new_page(width=612, height=792)
        for row in range(4):
            page.show_pdf_page(
                fitz.Rect(50, 50 + row * 100, 250, 150 + row * 100),
                source,
                0,
                clip=fitz.Rect(0, 0, 200, 200),
            )

    output_pdf = tmp_path / "deduped.pdf"
    warnings: list[str] = []
    save_pdf_for_fast_web_view(document, output_pdf, warnings)
    document.close()
    source.close()

    image_sizes = []
    with pikepdf.open(output_pdf) as pdf:
        for obj in pdf.objects:
            if isinstance(obj, pikepdf.Stream) and obj.get("/Subtype") == pikepdf.Name("/Image"):
                image_sizes.append(len(obj.read_raw_bytes()))
    large = [size for size in image_sizes if size > 1024]
    assert len(large) <= 1, f"expected one canonical image copy, got sizes {image_sizes}"


def test_saved_pdf_avoids_unsafe_cross_object_image_rewrite(tmp_path):
    from pdf_zh_translator.pdf_layout import save_pdf_for_fast_web_view

    document = fitz.open()
    page = document.new_page(width=200, height=200)
    page.insert_text((24, 40), "browser-compatible PDF", fontsize=12)
    output_pdf = tmp_path / "browser-compatible.pdf"
    warnings: list[str] = []

    with patch(
        "pdf_zh_translator.pdf_layout.dedupe_pdf_images",
        side_effect=AssertionError("unsafe image XObject rewrite was invoked"),
    ):
        save_pdf_for_fast_web_view(document, output_pdf, warnings)
    document.close()

    assert warnings == []
    assert b"/Linearized" in output_pdf.read_bytes()[:2048]


def test_saved_pdf_keeps_images_with_distinct_soft_masks(tmp_path):
    """Equal RGB streams with different soft masks are different icons."""
    from pdf_zh_translator.pdf_layout import save_pdf_for_fast_web_view

    fixture = (
        Path(__file__).parent
        / "fixtures"
        / "comdiffuser_p4_masked_icons.pdf"
    )
    source = fitz.open(fixture)
    source_shapes = Counter(
        (image[2], image[3]) for image in source[0].get_images(full=True)
    )
    output_pdf = tmp_path / "masked-icons.pdf"
    warnings: list[str] = []
    save_pdf_for_fast_web_view(source, output_pdf, warnings)
    source.close()

    output = fitz.open(output_pdf)
    output_images = output[0].get_images(full=True)
    output_shapes = Counter((image[2], image[3]) for image in output_images)
    output.close()

    assert source_shapes[(64, 64)] == 2
    assert output_shapes[(64, 64)] >= source_shapes[(64, 64)], warnings
    assert len(
        {
            (image[0], image[1])
            for image in output_images
            if (image[2], image[3]) == (64, 64)
        }
    ) >= 2

    reopened = fitz.open(output_pdf)
    for page in reopened:
        pixmap = page.get_pixmap(dpi=36)
        assert pixmap.width > 0
    reopened.close()


def test_saved_gears_float_renders_in_poppler(tmp_path):
    """The web-ready save must retain a vector/image float outside MuPDF."""
    import re
    import shutil
    import subprocess

    if shutil.which("pdftoppm") is None:
        pytest.skip("pdftoppm is required for cross-renderer PDF validation")

    class PlaceholderPreservingTranslator:
        block_types: list[str] = []

        def translate_batch(self, texts):
            outputs = []
            for text in texts:
                placeholders = re.findall(r"⟦\d+⟧", text)
                outputs.append("这是一段用于验证跨渲染器图形保留的中文译文" + "".join(placeholders))
            return outputs

    source_pdf = Path(__file__).parent / "fixtures" / "gears_p5_structure.pdf"
    output_pdf = tmp_path / "gears-p5.zh.pdf"
    translate_pdf(
        input_pdf=source_pdf,
        output_pdf=output_pdf,
        translator=PlaceholderPreservingTranslator(),
        preserve_graphics_text=True,
    )

    def poppler_float_ink(pdf_path: Path, stem: str) -> float:
        prefix = tmp_path / stem
        subprocess.run(
            [
                "pdftoppm",
                "-png",
                "-singlefile",
                "-r",
                "72",
                "-f",
                "1",
                "-l",
                "1",
                "-x",
                "30",
                "-y",
                "25",
                "-W",
                "355",
                "-H",
                "190",
                str(pdf_path),
                str(prefix),
            ],
            check=True,
            capture_output=True,
        )
        pixmap = fitz.Pixmap(str(prefix.with_suffix(".png")))
        components = pixmap.n
        pixels = len(pixmap.samples) // components
        dark_pixels = sum(
            min(pixmap.samples[offset : offset + min(3, components)]) < 235
            for offset in range(0, len(pixmap.samples), components)
        )
        return dark_pixels / max(pixels, 1)

    source_ink = poppler_float_ink(source_pdf, "source-float")
    translated_ink = poppler_float_ink(output_pdf, "translated-float")

    assert source_ink >= 0.20
    assert translated_ink >= source_ink * 0.85


def test_math_symbols_fall_back_to_math_font(tmp_path):
    """Math glyphs missing from the CJK body font (like angle brackets) must
    pick a math-capable fallback instead of rendering notdef boxes."""
    from pdf_zh_translator.pdf_layout import build_font_pack, pick_font_alias

    warnings: list[str] = []
    pack = build_font_pack(None, warnings)
    if pack.math_fallback is None:
        pytest.skip("no math fallback font on this machine")

    fonts = pack.fonts_for(bold=False)
    for char in "⟨⟩⊤≻":
        alias = pick_font_alias(char, fonts)
        chosen = next(font for font, name in fonts if name == alias)
        assert chosen.has_glyph(ord(char)), (char, alias)
