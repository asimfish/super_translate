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
    # MCF p5: the fraction rows of display equation (8) must stay with the
    # equation, not be glued onto the preceding paragraph and redacted.
    "mcf_p5_fraction_equation.pdf",
    # DynaGuide p1: a final word split from a figure-internal label must not
    # be translated independently over the original diagram text.
    "dynaguide_p1_figure_label.pdf",
    # HDFlow p15: tall inline fractions and a short "Let" formula lead must
    # remain anchored while the surrounding explanation is translated.
    "hdflow_p15_inline_formula.pdf",
    # CDGS p24: clipped sprite bboxes must not turn adjacent task-description
    # lists into figure labels; action-call chains remain verbatim pseudocode.
    "cdgs_p24_clipped_sprites.pdf",
    # ComDiffuser p19: Table 9's caption opener must not merge backward into
    # the math-heavy final table row and become ineligible for translation.
    "comdiffuser_p19_table_caption.pdf",
    # ComDiffuser p4: prose carrying two inline distributions directly above
    # equation (4) must translate instead of being preserved as a table row.
    "comdiffuser_p4_formula_explanation.pdf",
    # DynaGuide p4: a narrow paragraph fragmented around many inline formulas
    # must translate as a whole while the adjacent algorithm stays untouched.
    "dynaguide_p4_fragmented_inline_math.pdf",
    # DynaGuide p26: prose ending in an inline Gaussian before a display
    # derivation must not be mistaken for part of the preserved equation.
    "dynaguide_p26_formula_explanation.pdf",
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
    """Reflowed CJK text must not overlap preserved or translated ink.

    Text-level QA can miss geometric collisions when the overlapping text is
    itself Chinese; this checks every CJK span in the output against the
    preserved-region registry and against other translated spans.
    """
    from pdf_zh_translator.pdf_layout import (
        bbox_area,
        bbox_intersection_area,
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
        cjk_spans = []
        for block in translated[page_index].get_text("dict")["blocks"]:
            if block.get("type") != 0:
                continue
            for line in block["lines"]:
                for span in line["spans"]:
                    text = span.get("text", "")
                    if not cjk_re.search(text):
                        continue
                    bbox = tuple(float(v) for v in span["bbox"])
                    cjk_spans.append((bbox, text))
                    area = max(bbox_area(bbox), 0.1)
                    for region in regions:
                        ratio = bbox_intersection_area(bbox, region) / area
                        if ratio >= 0.5:
                            violations.append(
                                f"p{page_index + 1} span {text[:20]!r} overlaps "
                                f"preserved region {region} by {ratio:.0%}"
                            )
        for index, (first_bbox, first_text) in enumerate(cjk_spans):
            first_area = max(bbox_area(first_bbox), 0.1)
            for second_bbox, second_text in cjk_spans[index + 1 :]:
                smaller_area = min(
                    first_area,
                    max(bbox_area(second_bbox), 0.1),
                )
                ratio = (
                    bbox_intersection_area(first_bbox, second_bbox)
                    / smaller_area
                )
                if ratio >= 0.5:
                    violations.append(
                        f"p{page_index + 1} translated spans "
                        f"{first_text[:16]!r} and {second_text[:16]!r} "
                        f"overlap by {ratio:.0%}"
                    )
    translated.close()
    assert violations == []


def test_cdgs_clipped_sprite_page_translates_all_action_labels():
    from pdf_zh_translator.pdf_layout import prepare_translation_units, strip_sentinels

    source = fitz.open(FIXTURES / "cdgs_p24_clipped_sprites.pdf")
    units, _, _ = prepare_translation_units(source, preserve_graphics_text=True)
    labels = [
        " ".join(strip_sentinels(block.text).split())
        for block, _, _ in units
        if "Action Skeleton" in strip_sentinels(block.text)
    ]
    source.close()

    assert len(labels) == 4


def test_cdgs_clipped_sprite_page_preserves_every_action_call(tmp_path):
    import re

    input_pdf = FIXTURES / "cdgs_p24_clipped_sprites.pdf"
    output_pdf = tmp_path / "cdgs-out.pdf"
    translate_pdf(
        input_pdf=input_pdf,
        output_pdf=output_pdf,
        translator=_GoldenStubTranslator(),
        preserve_graphics_text=True,
    )

    call_re = re.compile(r"\b(?:pick|pull|place|push)\s*\([^()]+\)")
    arrow_re = re.compile(r"(?:→|⇒|->)")
    source = fitz.open(input_pdf)
    translated = fitz.open(output_pdf)
    source_text = source[0].get_text("text")
    translated_text = translated[0].get_text("text")
    source.close()
    translated.close()

    assert call_re.findall(translated_text) == call_re.findall(source_text)
    assert len(arrow_re.findall(translated_text)) == len(arrow_re.findall(source_text))


def test_comdiffuser_formula_explanation_is_a_translation_unit():
    from pdf_zh_translator.pdf_layout import prepare_translation_units, strip_sentinels

    source = fitz.open(FIXTURES / "comdiffuser_p4_formula_explanation.pdf")
    units, _, _ = prepare_translation_units(source, preserve_graphics_text=True)
    texts = [
        " ".join(strip_sentinels(block.text).split())
        for block, _, _ in units
    ]
    source.close()

    explanation = next(text for text in texts if "to represent the distributions" in text)
    assert explanation.startswith("Representing Initial States and Goals.")
    assert explanation.endswith("training objective")


@pytest.mark.parametrize(
    ("fixture", "needle", "expected_start", "expected_end"),
    [
        (
            "dynaguide_p4_fragmented_inline_math.pdf",
            "reasonable approximation of",
            "With this dynamics model trained",
            "and planning [57].",
        ),
        (
            "dynaguide_p26_formula_explanation.pdf",
            "The log probability can be computed as follows:",
            "We start from the very rough approximation",
            "computed as follows:",
        ),
    ],
)
def test_dynaguide_fragmented_formula_prose_is_one_translation_unit(
    fixture,
    needle,
    expected_start,
    expected_end,
):
    from pdf_zh_translator.pdf_layout import prepare_translation_units, strip_sentinels

    source = fitz.open(FIXTURES / fixture)
    units, _, _ = prepare_translation_units(source, preserve_graphics_text=True)
    texts = [
        " ".join(strip_sentinels(block.text).split())
        for block, _, _ in units
    ]
    source.close()

    paragraph = next(text for text in texts if needle in text)
    assert paragraph.startswith(expected_start)
    assert paragraph.endswith(expected_end)


def test_source_unit_qa_flags_formula_adjacent_source_phrase():
    from pdf_zh_translator.pdf_layout import (
        TextBlock,
        _translation_retains_source_prose_run,
    )

    block = TextBlock(
        page_index=0,
        bbox=(108.0, 430.0, 504.0, 476.0),
        text=(
            "We start from a rough Gaussian approximation. "
            "The log probability can be computed as follows:"
        ),
        font_size=9.0,
        color=(0.0, 0.0, 0.0),
    )

    assert _translation_retains_source_prose_run(
        block,
        "概率建模如下。The log probability can be computed as follows:",
    )


def test_source_unit_qa_allows_short_technical_name():
    from pdf_zh_translator.pdf_layout import (
        TextBlock,
        _translation_retains_source_prose_run,
    )

    block = TextBlock(
        page_index=0,
        bbox=(108.0, 430.0, 504.0, 476.0),
        text=(
            "We use a Vision Language Action model to control the robot "
            "during long-horizon manipulation."
        ),
        font_size=9.0,
        color=(0.0, 0.0, 0.0),
    )

    assert not _translation_retains_source_prose_run(
        block,
        "我们使用 Vision Language Action model 控制机器人完成长程操作。",
    )


def test_source_unit_qa_allows_named_program_and_url():
    from pdf_zh_translator.pdf_layout import (
        TextBlock,
        _translation_retains_source_prose_run,
    )

    program = TextBlock(
        page_index=0,
        bbox=(108.0, 430.0, 504.0, 476.0),
        text=(
            "This work was supported by the NSF Graduate Research "
            "Fellowships Program and our university."
        ),
        font_size=9.0,
        color=(0.0, 0.0, 0.0),
    )
    linked = TextBlock(
        page_index=0,
        bbox=(108.0, 480.0, 504.0, 526.0),
        text=(
            "Please see the submission guidelines at "
            "https://nips.cc/public/guides/CodeSubmissionPolicy for details."
        ),
        font_size=9.0,
        color=(0.0, 0.0, 0.0),
    )

    assert not _translation_retains_source_prose_run(
        program,
        "本研究受 NSF Graduate Research Fellowships Program 资助。",
    )
    assert not _translation_retains_source_prose_run(
        linked,
        "请参阅 https://nips.cc/public/guides/CodeSubmissionPolicy。",
    )
