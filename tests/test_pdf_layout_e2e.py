"""End-to-end PDF layout tests for native translation."""

import base64
import re
import subprocess
from collections import Counter
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from threading import Thread
from unittest.mock import patch

import fitz
import pytest
from playwright.sync_api import sync_playwright

from pdf_zh_translator.pdf_layout import create_dual_pdf, translate_pdf, verify_translation

ROOT = Path(__file__).resolve().parents[1]


def _poppler_layout_text(pdf_path: Path, *, page: int | None = None) -> str:
    command = ["pdftotext", "-layout"]
    if page is not None:
        command.extend(["-f", str(page), "-l", str(page)])
    command.extend([str(pdf_path), "-"])
    result = subprocess.run(command, check=True, capture_output=True, text=True)
    return result.stdout


def _pdfjs_text_content(pdf_path: Path) -> str:
    payload = {
        "pdf": base64.b64encode(pdf_path.read_bytes()).decode("ascii"),
        "worker": base64.b64encode(
            (ROOT / "app/static/js/pdf.worker.min.js").read_bytes()
        ).decode("ascii"),
    }
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page()
        page.add_script_tag(path=str(ROOT / "app/static/js/pdf.min.js"))
        text = page.evaluate(
            """async payload => {
              const workerSource = atob(payload.worker);
              pdfjsLib.GlobalWorkerOptions.workerSrc = URL.createObjectURL(
                new Blob([workerSource], {type: 'application/javascript'})
              );
              const bytes = Uint8Array.from(atob(payload.pdf), char => char.charCodeAt(0));
              const document = await pdfjsLib.getDocument({
                data: bytes,
                disableWorker: true,
              }).promise;
              const parts = [];
              for (let pageNumber = 1; pageNumber <= document.numPages; pageNumber += 1) {
                const pdfPage = await document.getPage(pageNumber);
                const content = await pdfPage.getTextContent();
                parts.push(content.items.map(item => item.str).join(' '));
              }
              await document.destroy();
              return parts.join('\\n');
            }""",
            payload,
        )
        browser.close()
    return text


def _pdfjs_drag_copy(
    pdf_path: Path,
    *,
    start_text: str,
    end_text: str,
    screenshot_path: Path,
) -> tuple[str, str, int]:
    payload = {
        "pdf": base64.b64encode(pdf_path.read_bytes()).decode("ascii"),
        "worker": base64.b64encode(
            (ROOT / "app/static/js/pdf.worker.min.js").read_bytes()
        ).decode("ascii"),
    }

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            body = b"<!doctype html><html><body></body></html>"
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, _format, *_args):
            return

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True)
            context = browser.new_context(
                permissions=["clipboard-read", "clipboard-write"],
                viewport={"width": 1300, "height": 1000},
            )
            page = context.new_page()
            page.goto(f"http://127.0.0.1:{server.server_port}/")
            page.add_style_tag(
                content="""
                body { margin: 0; background: white; }
                #wrapper { position: relative; }
                canvas { display: block; user-select: none; }
                .textLayer {
                  --scale-factor: 1;
                  position: absolute;
                  inset: 0;
                  overflow: hidden;
                  line-height: 1;
                  text-align: initial;
                  user-select: text;
                  pointer-events: auto;
                }
                .textLayer span, .textLayer br {
                  color: transparent;
                  -webkit-text-fill-color: transparent;
                  position: absolute;
                  white-space: pre;
                  cursor: text;
                  transform-origin: 0% 0%;
                }
                .textLayer ::selection, .textLayer span::selection {
                  background: rgba(24, 169, 153, 0.35);
                }
                """
            )
            page.add_script_tag(path=str(ROOT / "app/static/js/pdf.min.js"))
            page.evaluate(
                """async payload => {
                  const workerSource = atob(payload.worker);
                  pdfjsLib.GlobalWorkerOptions.workerSrc = URL.createObjectURL(
                    new Blob([workerSource], {type: 'application/javascript'})
                  );
                  const bytes = Uint8Array.from(
                    atob(payload.pdf), char => char.charCodeAt(0)
                  );
                  const pdfDocument = await pdfjsLib.getDocument({
                    data: bytes,
                    disableWorker: true,
                  }).promise;
                  const pdfPage = await pdfDocument.getPage(1);
                  const viewport = pdfPage.getViewport({scale: 1.5});
                  const wrapper = document.createElement('div');
                  wrapper.id = 'wrapper';
                  wrapper.style.width = `${viewport.width}px`;
                  wrapper.style.height = `${viewport.height}px`;
                  const canvas = document.createElement('canvas');
                  canvas.width = Math.ceil(viewport.width);
                  canvas.height = Math.ceil(viewport.height);
                  wrapper.appendChild(canvas);
                  const textLayer = document.createElement('div');
                  textLayer.className = 'textLayer';
                  textLayer.style.width = `${viewport.width}px`;
                  textLayer.style.height = `${viewport.height}px`;
                  textLayer.style.setProperty('--scale-factor', viewport.scale);
                  wrapper.appendChild(textLayer);
                  document.body.appendChild(wrapper);
                  await pdfPage.render({
                    canvasContext: canvas.getContext('2d'),
                    viewport,
                  }).promise;
                  const textContent = await pdfPage.getTextContent();
                  const renderTask = pdfjsLib.renderTextLayer({
                    textContentSource: textContent,
                    container: textLayer,
                    viewport,
                    textDivs: [],
                  });
                  if (renderTask?.promise) await renderTask.promise;
                  textLayer.dataset.loaded = 'true';
                }""",
                payload,
            )
            spans = page.locator(".textLayer span")
            span_texts = spans.all_inner_texts()

            flat_text = ""
            char_span_indexes = []
            for index, text in enumerate(span_texts):
                for char in text:
                    if char.isspace():
                        continue
                    flat_text += char
                    char_span_indexes.append(index)
            start_compact = "".join(start_text.split())
            end_compact = "".join(end_text.split())
            start_offset = flat_text.index(start_compact)
            end_offset = flat_text.index(end_compact, start_offset)
            start_index = char_span_indexes[start_offset]
            end_index = char_span_indexes[end_offset + len(end_compact) - 1]
            start_box = spans.nth(start_index).bounding_box()
            end_box = spans.nth(end_index).bounding_box()
            assert start_box is not None and end_box is not None
            page.mouse.move(start_box["x"] + 1, start_box["y"] + start_box["height"] / 2)
            page.mouse.down()
            page.mouse.move(
                end_box["x"] + end_box["width"] - 1,
                end_box["y"] + end_box["height"] / 2,
                steps=20,
            )
            page.mouse.up()
            selected = page.evaluate("window.getSelection().toString()")
            selection_rects = page.evaluate(
                """() => {
                  const selection = window.getSelection();
                  if (!selection || selection.rangeCount === 0) return 0;
                  return selection.getRangeAt(0).getClientRects().length;
                }"""
            )
            shortcut = "Meta+C" if "Mac" in page.evaluate("navigator.platform") else "Control+C"
            page.keyboard.press(shortcut)
            copied = page.evaluate("navigator.clipboard.readText()")
            page.screenshot(path=str(screenshot_path), full_page=True)
            browser.close()
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
    return selected, copied, selection_rects


class _OtfP4SelectionTranslator:
    block_types: list[str] = []

    def translate_batch(self, texts):
        outputs = []
        for text in texts:
            placeholders = re.findall(r"⟦\d+⟧", text)
            if text.strip() == "Formulation of OFT":
                outputs.append("最优流传输的形式化定义")
            elif text.startswith("We begin by de"):
                outputs.append(
                    "我们首先定义最优流传输如下。考虑"
                    f"{placeholders[0]}和{placeholders[1]}是图上的两个测度，"
                    f"其中{''.join(placeholders[2:])}满足平衡约束。"
                    "最优流传输可表示为："
                )
            else:
                outputs.append("中文译文" + "".join(placeholders))
        return outputs


def test_pdfjs_drag_copy_otf_p4_selects_only_target_paragraph(tmp_path):
    source_pdf = ROOT / "tests/fixtures/otf_p4_runin_formula.pdf"
    output_pdf = tmp_path / "otf-p4-pdfjs.pdf"
    translate_pdf(
        input_pdf=source_pdf,
        output_pdf=output_pdf,
        translator=_OtfP4SelectionTranslator(),
        preserve_graphics_text=True,
    )

    pdfjs_text = "".join(_pdfjs_text_content(output_pdf).split())
    assert pdfjs_text.count("我们首先定义最优流传输如下") == 1
    assert "WebeginbydefiningtheOFT" not in pdfjs_text
    assert "FormulationofOFT" not in pdfjs_text

    selected, copied, selection_rects = _pdfjs_drag_copy(
        output_pdf,
        start_text="我们首先定义最优流传输如下",
        end_text="最优流传输可表示为",
        screenshot_path=tmp_path / "otf-p4-pdfjs-selection.png",
    )
    selected_compact = " ".join(selected.split())
    copied_compact = " ".join(copied.split())
    selected_no_space = "".join(selected.split())
    assert selection_rects > 0
    assert "我们首先定义最优流传输如下" in selected_no_space
    assert "最优流传输可表示为" in selected_no_space
    assert "α" in selected_compact and "β" in selected_compact
    assert "中文译文" not in selected_compact
    assert "Formulation of OFT" not in selected_compact
    assert "We begin by defining the OFT" not in selected_compact
    assert len(selected_compact) < 500
    assert copied_compact == selected_compact


def _build_source_clip_fixture() -> tuple[fitz.Document, fitz.Rect]:
    source = fitz.open()
    page = source.new_page(width=300, height=200)
    page.insert_text((20, 24), "PUBLISHED HEADER OFF CLIP", fontsize=9)
    page.insert_text((20, 170), "OFF CLIP BODY MUST NOT COPY", fontsize=9)
    formula_rect = fitz.Rect(105, 72, 150, 96)
    page.insert_text((108, 90), "x+y", fontsize=14)
    return source, formula_rect


def test_formula_clip_has_one_semantic_copy_without_source_page_text(tmp_path):
    from pdf_zh_translator.pdf_layout import _Token, emit_tokens

    source, formula_rect = _build_source_clip_fixture()
    output = fitz.open()
    page = output.new_page(width=300, height=200)
    page.insert_font(fontname="helv", fontfile=None)
    emit_tokens(
        page,
        [
            _Token(
                kind="formula",
                text="x+y",
                source_bbox=tuple(formula_rect),
                source_page=0,
                source_size=14,
                width=45,
            )
        ],
        [(fitz.Font("helv"), "helv")],
        14,
        (0.0, 0.0, 0.0),
        105,
        90,
        {},
        source_document=source,
    )
    output_pdf = tmp_path / "formula-clip.pdf"
    output.save(output_pdf)
    output.close()
    source.close()

    poppler_text = " ".join(_poppler_layout_text(output_pdf).split())
    pdfjs_text = " ".join(_pdfjs_text_content(output_pdf).split())
    for extracted in (poppler_text, pdfjs_text):
        assert extracted.replace(" ", "").count("x+y") == 1
        assert "PUBLISHED HEADER OFF CLIP" not in extracted
        assert "OFF CLIP BODY MUST NOT COPY" not in extracted

    rendered = fitz.open(output_pdf)
    pixmap = rendered[0].get_pixmap(matrix=fitz.Matrix(3, 3), alpha=False)
    rendered.close()
    assert min(pixmap.samples) < 245


def test_preserved_region_restore_has_visual_patch_without_semantic_text(tmp_path):
    from pdf_zh_translator.pdf_layout import _restore_redaction_damaged_preserved_regions

    source = fitz.open()
    source_page = source.new_page(width=300, height=200)
    source_page.insert_text((20, 24), "PUBLISHED HEADER OFF CLIP", fontsize=9)
    source_page.insert_text((20, 170), "OFF CLIP BODY MUST NOT COPY", fontsize=9)
    protected_rect = fitz.Rect(82, 72, 220, 96)
    source_page.insert_text((85, 90), "PROTECTED LABEL", fontsize=14)
    output = fitz.open()
    page = output.new_page(width=300, height=200)
    restored = _restore_redaction_damaged_preserved_regions(
        page,
        source,
        0,
        [tuple(protected_rect)],
        [],
        0.5,
    )
    output_pdf = tmp_path / "preserved-region.pdf"
    output.save(output_pdf)
    output.close()
    source.close()

    assert restored == 1
    for extracted in (
        _poppler_layout_text(output_pdf),
        _pdfjs_text_content(output_pdf),
    ):
        assert "PROTECTED LABEL" not in extracted
        assert "PUBLISHED HEADER OFF CLIP" not in extracted
        assert "OFF CLIP BODY MUST NOT COPY" not in extracted

    rendered = fitz.open(output_pdf)
    pixmap = rendered[0].get_pixmap(
        matrix=fitz.Matrix(3, 3),
        clip=protected_rect,
        alpha=False,
    )
    rendered.close()
    assert min(pixmap.samples) < 245


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


class _BulletListTranslator:
    def translate_batch(self, texts):
        outputs = []
        for text in texts:
            if "extend the vanilla OT" in text:
                outputs.append(
                    "我们将二分图上的普通最优传输扩展到更一般的图情形，"
                    "从而提出最优流传输，其中边际约束被流平衡约束所取代。"
                )
            elif "entropic OFT" in text:
                outputs.append(
                    "本文提出熵正则化最优传输（Entropic OFT），以推导出适用于 "
                    "GPU 的 OFT-Sinkhorn 算法，从而获得 OFT 问题的近似解。"
                    "该算法的全局收敛性在理论上得到保证。"
                )
            elif "capacity constraints" in text:
                outputs.append(
                    "本文将节点和边容量约束纳入 OFT，此时 OFT 等价于最小费用流问题。"
                    "通过考虑这些约束，本文修改了 OFT-Sinkhorn 算法，以确保输出满足"
                    "容量约束。在最小费用流问题上的实验结果展示了本文算法的优越性。"
                )
            else:
                outputs.append("中文译文")
        return outputs


class _WamP3Translator:
    def translate_batch(self, texts):
        outputs = []
        for text in texts:
            if "Prior approaches" in text and "temporal window" in text:
                outputs.append(
                    "视觉-语言-动作模型（VLA）[4, 62] 和世界动作模型（WAM）"
                    "[8, 14] 中的先前方法通常将近期观测映射为动作，依赖有限的"
                    "时间窗口"
                )
            else:
                outputs.append("中文译文")
        return outputs


def test_two_line_body_paragraph_keeps_body_scale(tmp_path):
    # MemoryWAM p3: the two-line paragraph before Eq. (1) rendered at 6.92pt
    # while the page body is 9.17pt. The line-height gate used raw Noto CJK
    # font-file metrics (2.86x size for two lines), so the Chinese could
    # never fit the English-sized bbox at body scale.
    input_pdf = Path(__file__).parent / "fixtures" / "memorywam_p3_inline_window.pdf"
    output_pdf = tmp_path / "wam_p3.zh.pdf"

    translate_pdf(
        input_pdf=input_pdf,
        output_pdf=output_pdf,
        translator=_WamP3Translator(),
        preserve_graphics_text=True,
    )

    translated = fitz.open(output_pdf)
    data = translated[0].get_text("dict")
    sizes = []
    for block in data.get("blocks", []):
        if block.get("type") != 0:
            continue
        text = "".join(
            span["text"]
            for line in block.get("lines", [])
            for span in line.get("spans", [])
        )
        if "视觉-语言-动作模型（VLA）" in text:
            sizes = [
                span["size"]
                for line in block.get("lines", [])
                for span in line.get("spans", [])
            ]
            break
    translated.close()

    assert sizes, "translated paragraph not found"
    assert min(sizes) >= 8.4, f"body paragraph over-shrunk: {sorted(set(sizes))}"


def test_sibling_contribution_bullets_share_font_size(tmp_path):
    # OTF p2 production defect: the three contribution bullets rendered at
    # 7.4/6.4/9.2pt because each bullet shrank independently against its own
    # source bbox. Siblings must come out at one consistent size.
    input_pdf = Path(__file__).parent / "fixtures" / "otf_p02_contributions.pdf"
    output_pdf = tmp_path / "otf_p02.zh.pdf"

    translate_pdf(
        input_pdf=input_pdf,
        output_pdf=output_pdf,
        translator=_BulletListTranslator(),
        preserve_graphics_text=True,
    )

    translated = fitz.open(output_pdf)
    page = translated[0]
    data = page.get_text("dict")
    sizes = {}
    for needle, key in (
        ("我们将二分图", "b1"),
        ("熵正则化最优传输（", "b2"),
        ("容量约束纳入", "b3"),
    ):
        for block in data.get("blocks", []):
            if block.get("type") != 0:
                continue
            # Contribution bullets live in the 560-680pt band on this page;
            # the stub translator reuses the same Chinese for body paragraphs
            # that mention the same needles higher up.
            if not 555.0 <= block["bbox"][1] <= 680.0:
                continue
            text = "".join(
                span["text"]
                for line in block.get("lines", [])
                for span in line.get("spans", [])
            )
            if needle in text:
                span_sizes = [
                    span["size"]
                    for line in block.get("lines", [])
                    for span in line.get("spans", [])
                ]
                sizes[key] = max(span_sizes)
                break
    translated.close()

    assert set(sizes) == {"b1", "b2", "b3"}, f"missing bullets: {sizes}"
    spread = max(sizes.values()) - min(sizes.values())
    assert spread <= 0.6, f"sibling bullet sizes diverge: {sizes}"
    # The group grows into the whitespace below it, so the shared size stays
    # at the page's body scale instead of harmonizing downwards.
    assert min(sizes.values()) >= 8.75, f"bullets over-shrunk: {sizes}"


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


def test_saved_pdf_compacts_fragmented_page_streams_without_semantic_drift(tmp_path):
    from pdf_zh_translator.pdf_layout import save_pdf_for_fast_web_view

    document = fitz.open()
    page = document.new_page(width=612, height=792)
    for index in range(240):
        page.insert_text(
            (36 + (index % 4) * 132, 48 + (index // 4) * 11),
            f"stream-{index:03d}",
            fontsize=7,
        )
    page.insert_link(
        {
            "kind": fitz.LINK_URI,
            "from": fitz.Rect(36, 730, 180, 750),
            "uri": "https://example.com/project",
        }
    )
    page = document.reload_page(page)

    source_text = page.get_text("text")
    source_pixels = page.get_pixmap(matrix=fitz.Matrix(2, 2), alpha=False).samples
    source_links = [link["uri"] for link in page.get_links() if "uri" in link]
    assert len(page.get_contents()) >= 240

    output_pdf = tmp_path / "compacted.pdf"
    warnings: list[str] = []
    save_pdf_for_fast_web_view(document, output_pdf, warnings)
    document.close()

    with fitz.open(output_pdf) as saved:
        saved_page = saved[0]
        saved_text = saved_page.get_text("text")
        saved_pixels = saved_page.get_pixmap(
            matrix=fitz.Matrix(2, 2), alpha=False
        ).samples
        saved_links = [
            link["uri"] for link in saved_page.get_links() if "uri" in link
        ]
        assert len(saved_page.get_contents()) <= 2
        assert saved.xref_length() < 80

    assert warnings == []
    assert saved_text == source_text
    assert saved_pixels == source_pixels
    assert saved_links == source_links == ["https://example.com/project"]


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
