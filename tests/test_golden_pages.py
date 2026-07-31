"""Golden-page regression tests.

Each fixture is a single page extracted from a real paper that previously
produced a layout/QA failure (equation rows torn apart, captions swallowed
by tables, figure captions overprinting graphics). The full native pipeline
runs with a deterministic Chinese stub translator, and the standard
verification must report no error-severity issues.
"""

from pathlib import Path

import fitz
import pytest

from pdf_zh_translator.pdf_layout import translate_pdf, verify_translation_issues

FIXTURES = Path(__file__).parent / "fixtures"

GOLDEN_PAGES = [
    # MCF p3: a "where U(a,b) = {...}" clause sharing the visual row of
    # display equation (1) must not be torn out and reflowed over the math.
    "mcf_p3_equation_row.pdf",
    # MCF p16: table captions whose record looked tabular must still be
    # translated, anchored above the table instead of inside its first row.
    "mcf_p16_caption_table.pdf",
    # oc p4: figure bottom labels fused with the Figure 4 caption must not
    # drag the translated caption onto the figure.
    "oc_p4_figure_caption.pdf",
    # IPMF p3: prose sentences wedged inside an equation zone must be
    # translated without the preserved formula rows being flagged changed.
    "ipmf_p3_equation_zone.pdf",
    # DynaGuide p1: a final word split from a figure-internal label must not
    # be translated independently over the original diagram text.
    "dynaguide_p1_figure_label.pdf",
    # HDFlow p15: tall inline fractions and a short "Let" formula lead must
    # remain anchored while the surrounding explanation is translated.
    "hdflow_p15_inline_formula.pdf",
]


class _GoldenStubTranslator:
    """Deterministic Chinese rendering, placeholder-preserving.

    Output length tracks the source at roughly the typical EN→ZH ratio so
    the layout engine sees realistic line counts instead of artificial
    overflow or underflow.
    """

    _PHRASE = "这是一段用于验证版面稳定性的中文译文"

    def __init__(self):
        self.block_types: list[str] = []

    def translate_batch(self, texts):
        import re

        outputs = []
        for text in texts:
            parts = re.split(r"(⟦\d+⟧)", text)
            rendered = []
            for part in parts:
                if re.fullmatch(r"⟦\d+⟧", part):
                    rendered.append(part)
                elif part.strip():
                    target = max(4, int(len(part.strip()) * 0.45))
                    repeats = target // len(self._PHRASE) + 1
                    rendered.append((self._PHRASE * repeats)[:target])
            outputs.append("".join(rendered))
        return outputs


@pytest.mark.parametrize("fixture", GOLDEN_PAGES)
def test_golden_page_has_no_error_issues(tmp_path, fixture):
    input_pdf = FIXTURES / fixture
    if not input_pdf.exists():
        pytest.skip(f"fixture missing: {fixture}")
    output_pdf = tmp_path / "out.pdf"

    translate_pdf(
        input_pdf=input_pdf,
        output_pdf=output_pdf,
        translator=_GoldenStubTranslator(),
        preserve_graphics_text=True,
    )

    issues = verify_translation_issues(input_pdf, output_pdf)
    errors = [
        f"p{issue.page} {issue.code}: {issue.message}"
        for issue in issues
        if issue.severity == "error"
    ]
    assert errors == []


@pytest.mark.parametrize("fixture", GOLDEN_PAGES)
def test_golden_page_has_no_ink_overlap(tmp_path, fixture):
    """Reflowed CJK text must not sit mostly inside preserved regions.

    Text-level QA can miss geometric collisions when the overlapping text is
    itself Chinese; this checks every CJK span in the output against the
    preserved-region registry (formula rows, table cells, algorithm floats).
    """
    from pdf_zh_translator.pdf_layout import (
        bbox_intersection_area,
        bbox_area,
        prepare_translation_units,
    )

    input_pdf = FIXTURES / fixture
    if not input_pdf.exists():
        pytest.skip(f"fixture missing: {fixture}")
    output_pdf = tmp_path / "out.pdf"

    translate_pdf(
        input_pdf=input_pdf,
        output_pdf=output_pdf,
        translator=_GoldenStubTranslator(),
        preserve_graphics_text=True,
    )

    preserved: dict[int, list] = {}
    source = fitz.open(input_pdf)
    prepare_translation_units(
        source, preserve_graphics_text=True, preserved_regions_out=preserved
    )
    source.close()

    import re

    cjk_re = re.compile(r"[一-鿿]")
    violations = []
    translated = fitz.open(output_pdf)
    for page_index in range(translated.page_count):
        regions = preserved.get(page_index, [])
        if not regions:
            continue
        for block in translated[page_index].get_text("dict")["blocks"]:
            if block.get("type") != 0:
                continue
            for line in block["lines"]:
                for span in line["spans"]:
                    text = span.get("text", "")
                    if not cjk_re.search(text):
                        continue
                    bbox = tuple(float(v) for v in span["bbox"])
                    area = max(bbox_area(bbox), 0.1)
                    for region in regions:
                        ratio = bbox_intersection_area(bbox, region) / area
                        if ratio >= 0.5:
                            violations.append(
                                f"p{page_index + 1} span {text[:20]!r} overlaps "
                                f"preserved region {region} by {ratio:.0%}"
                            )
    translated.close()
    assert violations == []
