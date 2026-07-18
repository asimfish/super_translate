"""End-to-end PDF layout tests for native translation."""

import fitz

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
