"""Golden-page regression tests.

Each fixture is a single page extracted from a real paper that previously
produced a layout/QA failure (equation rows torn apart, captions swallowed
by tables, figure captions overprinting graphics). The full native pipeline
runs with a deterministic Chinese stub translator, and the standard
verification must report no error-severity issues.
"""

import re
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
    # HDFlow p2: prose split beside and inside a multi-line forward-process
    # equation must translate while every display-math span remains intact.
    "hdflow_p2_formula_prose.pdf",
    # HDFlow p5: boxed proposition prose split around a tall fraction and a
    # formula-heavy where-clause must translate as complete statements.
    "hdflow_p5_formula_explanations.pdf",
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
    # DynaGuide p26: the final row of an unnumbered multi-line derivation must
    # remain native instead of being translated with the following paragraph.
    "dynaguide_p26_unnumbered_display_formula.pdf",
    # OTF algorithms are ruled floats whose title record also contains input
    # and initialization rows; the full float must remain verbatim.
    "otf_p5_algorithm.pdf",
    "otf_p14_algorithm.pdf",
    # Mixed prose/formula rows must translate without false preserved-region
    # failures, including compact multi-letter subscripts.
    "otf_p9_table_formula.pdf",
    "flashsac_p6_formula_prose.pdf",
    # Adjacent panels form one right-side float that body reflow must avoid.
    "otf_p10_float_wrap.pdf",
    # Captions above dense appendix tables must not grow into their headers.
    "otf_p17_appendix_table.pdf",
    "otf_p15_formula_statement.pdf",
    "otf_p16_appendix_tables.pdf",
    "otf_p19_formula42.pdf",
    # Real structural regressions from OTF and GEARS.
    "otf_p3_structure.pdf",
    "otf_p4_runin_formula.pdf",
    "otf_p6_theorem.pdf",
    "otf_p8_typography.pdf",
    "otf_p16_derivation_cue.pdf",
    "gears_p2_paragraphs.pdf",
    "gears_p5_structure.pdf",
    "gears_p6_inline_formulas.pdf",
    "gears_p8_untranslated.pdf",
    # MemoryWAM p8: a prose continuation beginning with "Fig. 5. In ..."
    # wraps around the real caption and must not be treated as a second caption.
    "memorywam_p8_float_wrap.pdf",
    # GuidedVLA p15: piecewise `if` / `otherwise` labels are part of the
    # display equation and must not merge into the following prose.
    "guidedvla_p15_piecewise.pdf",
    "guidedvla_p21_formula.pdf",
    # GuidedVLA p1: fragmented author and affiliation records must stay source
    # metadata while the paper title, abstract, and figure caption translate.
    "guidedvla_p1_metadata.pdf",
    # GuidedVLA p28: the table envelope and adjacent figure labels must not
    # absorb translated body prose into preserved-text comparison.
    "guidedvla_p28_preserved.pdf",
    # CDGS appendix records combine translatable labels with immutable action
    # calls and object identifiers; QA must not flag the preserved tokens as
    # untranslated prose or absorb adjacent prose into a float envelope.
    "cdgs_p25_preserved.pdf",
    "cdgs_p31_34_action_records.pdf",
]


def _unit_blocks(fixture):
    from pdf_zh_translator.pdf_layout import prepare_translation_units

    source = fitz.open(FIXTURES / fixture)
    units, _, _ = prepare_translation_units(source, preserve_graphics_text=True)
    source.close()
    return [block for block, _, _ in units]


def _plain_unit_texts(fixture):
    from pdf_zh_translator.pdf_layout import strip_sentinels

    return [
        " ".join(strip_sentinels(block.text).split())
        for block in _unit_blocks(fixture)
    ]


@pytest.mark.parametrize("fixture", ["otf_p5_algorithm.pdf", "otf_p14_algorithm.pdf"])
def test_otf_algorithm_float_body_is_not_translated(fixture):
    texts = _plain_unit_texts(fixture)

    assert not any("Initialize" in text for text in texts)
    assert not any("UpdateK" in text or "Updateq" in text for text in texts)


def test_otf_academic_labels_and_split_section_headings_keep_structure():
    blocks = _unit_blocks("otf_p3_structure.pdf")
    texts = [
        " ".join(block.text.replace("\ue000", "").replace("\ue001", "").split())
        for block in blocks
    ]

    proposition = next(
        block for block, text in zip(blocks, texts) if text.startswith("Proposition 1")
    )
    assert proposition.block_type in {"heading", "run_in_heading"}
    assert proposition.bold
    assert any(
        block.block_type == "heading" and "3 METHDOLOGY" in text
        for block, text in zip(blocks, texts)
    )
    assert any(
        block.block_type == "heading" and "3.1 OPTIMAL FLOW TRANSPORT" in text
        for block, text in zip(blocks, texts)
    )


def test_otf_formula_paragraph_translates_all_prose():
    from pdf_zh_translator.pdf_layout import strip_sentinels

    blocks = _unit_blocks("otf_p4_runin_formula.pdf")
    texts = [" ".join(strip_sentinels(block.text).split()) for block in blocks]

    formulation = next(
        block for block, text in zip(blocks, texts) if text == "Formulation of OFT"
    )
    prose = next(
        block for block, text in zip(blocks, texts) if text.startswith("We begin by de")
    )
    prose_text = " ".join(strip_sentinels(prose.text).split())
    assert formulation.block_type == "run_in_heading"
    assert formulation.bold
    assert "are two measures on the graph" in prose_text
    assert "β" in prose_text
    assert prose.formula_anchors


def test_otf_runin_heading_starts_a_new_translation_unit():
    from pdf_zh_translator.pdf_layout import strip_sentinels

    blocks = _unit_blocks("otf_p4_runin_formula.pdf")
    texts = [" ".join(strip_sentinels(block.text).split()) for block in blocks]

    exact = next(
        block for block, text in zip(blocks, texts) if text.startswith("Exact Solving for OFT")
    )
    assert exact.block_type == "run_in_heading"
    assert exact.bold
    assert all(
        "Exact Solving for OFT" not in text
        for text in texts
        if not text.startswith("Exact Solving for OFT")
    )


def test_otf_runin_labels_keep_body_size_and_method_bold_terms():
    blocks = _unit_blocks("otf_p8_typography.pdf")
    texts = [
        " ".join(block.text.replace("\ue000", "").replace("\ue001", "").split())
        for block in blocks
    ]

    for prefix in (
        "• NETGEN (Integer-precision):",
        "• Vision (Real Scene):",
        "Baselines.",
    ):
        block = next(block for block, text in zip(blocks, texts) if text.startswith(prefix))
        assert block.block_type == "run_in_heading"
        assert block.bold
        assert block.font_size < 10.1

    baseline_body = next(
        block
        for block, text in zip(blocks, texts)
        if text.startswith("To demonstrate the feasibility")
    )
    assert {"Real", "ZKW", "Gurobi", "pns", "lemon"}.issubset(
        baseline_body.bold_terms
    )


def test_gears_indented_paragraphs_remain_separate_translation_units():
    texts = _plain_unit_texts("gears_p2_paragraphs.pdf")
    body = [text for text in texts if len(text) > 80]

    assert len(body) >= 4
    assert any(text.startswith("This gap can be decomposed") for text in body)
    assert any(text.startswith("The second challenge") for text in body)


def test_gears_section_33_body_is_translated():
    texts = _plain_unit_texts("gears_p8_untranslated.pdf")

    assert any("The privileged expert cannot be deployed directly" in text for text in texts)
    assert any("Data Collection" in text for text in texts)


def test_math_bold_at_formula_led_continuation_does_not_create_run_in_heading():
    from pdf_zh_translator.pdf_layout import (
        can_merge_blocks,
        collect_text_blocks,
        strip_sentinels,
    )

    source = fitz.open(FIXTURES / "mcf_p3_equation_row.pdf")
    blocks, _ = collect_text_blocks(source)
    source.close()
    continuation_index = next(
        index
        for index, block in enumerate(blocks)
        if strip_sentinels(block.text).startswith("v=b/(K")
    )
    previous = blocks[continuation_index - 1]
    continuation = blocks[continuation_index]

    assert not continuation.starts_bold
    assert not continuation.bold_prefix
    assert continuation.bold_terms == ()
    assert can_merge_blocks(previous, continuation)


def test_formula_row_prose_suffix_is_not_a_second_overlapping_translation_unit():
    from pdf_zh_translator.pdf_layout import strip_sentinels

    blocks = _unit_blocks("dynaguide_p26_unnumbered_display_formula.pdf")
    matching = [
        " ".join(strip_sentinels(block.text).split())
        for block in blocks
        if "probability" in strip_sentinels(block.text).lower()
    ]

    assert len(matching) == 1
    assert "The log probability can be computed as follows" in matching[0]


def test_inline_formula_anchor_absorbs_detached_subscript_glyph():
    from pdf_zh_translator.pdf_layout import bbox_intersection_area, strip_sentinels

    block = next(
        block
        for block in _unit_blocks("oc_p4_figure_caption.pdf")
        if strip_sentinels(block.text).startswith("Assume that objects are static")
    )
    detached_subscript = (485.4790, 572.5324, 489.5866, 579.5063)
    subscript_area = (
        (detached_subscript[2] - detached_subscript[0])
        * (detached_subscript[3] - detached_subscript[1])
    )

    assert len(block.formula_anchors) == 1
    assert (
        bbox_intersection_area(block.formula_anchors[0], detached_subscript)
        / subscript_area
        >= 0.95
    )
    assert any(
        bbox_intersection_area(redact, detached_subscript) / subscript_area >= 0.95
        for redact in block.redact_bboxes or []
    )


def test_gears_stage_caption_and_architecture_keep_structure():
    blocks = _unit_blocks("gears_p5_structure.pdf")
    texts = [
        " ".join(block.text.replace("\ue000", "").replace("\ue001", "").split())
        for block in blocks
    ]

    caption = next(
        block for block, text in zip(blocks, texts) if text.startswith("Fig. 1")
    )
    caption_text = next(
        text for block, text in zip(blocks, texts) if block is caption
    )
    assert caption.block_type == "caption"
    assert all(label in caption_text for label in ("Stage 1", "Stage 2", "Stage 3"))
    assert {"Stage 1", "Stage 2", "Stage 3"}.issubset(caption.bold_terms)
    assert not any(text == "Stage 1:" for text in texts)
    architecture = next(
        block for block, text in zip(blocks, texts) if text.startswith("Architecture.")
    )
    assert architecture.block_type == "run_in_heading"
    assert architecture.bold
    architecture_body = next(
        block for block in blocks if block.bbox[1] > architecture.bbox[3]
        and block.block_type == "body"
    )
    assert architecture_body.keepout_bboxes
    bottom_redact = max(architecture_body.redact_bboxes or [], key=lambda bbox: bbox[3])
    first_formula_top = min(bbox[1] for bbox in architecture_body.keepout_bboxes)
    assert bottom_redact[3] <= first_formula_top - 1.19
    formula_suffix = next(
        block for block, text in zip(blocks, texts) if text == "from four"
    )
    assert formula_suffix.block_type == "formula_prose"
    assert formula_suffix.keepout_bboxes
    assert all(bbox[0] >= 339.0 for bbox in formula_suffix.redact_bboxes or [])


def test_gears_inline_formula_prefix_reflows_with_its_sentence():
    blocks = _unit_blocks("gears_p6_inline_formulas.pdf")
    formula_prefix = next(
        block
        for block in blocks
        if "convolution, and the [CLS] token" in block.text
    )

    assert formula_prefix.block_type == "body"
    assert len(formula_prefix.formula_anchors) == 4
    assert not formula_prefix.keepout_bboxes
    assert "1" in formula_prefix.text


def test_gears_data_collection_formula_fragments_keep_anchor_alignment():
    from pdf_zh_translator.pdf_layout import SENTINEL_RUN_RE

    blocks = _unit_blocks("gears_p8_untranslated.pdf")
    data_collection = next(
        block
        for block in blocks
        if "collect a dataset" in block.text
    )

    heading = next(block for block in blocks if block.text.strip() == "Data Collection.")
    assert heading.block_type == "run_in_heading"
    assert heading.bold
    assert len(data_collection.formula_anchors) == len(
        SENTINEL_RUN_RE.findall(data_collection.text)
    )
    assert data_collection.formula_anchors
    assert any("D={(I" in text for text in _plain_unit_texts("gears_p8_untranslated.pdf"))


def test_gears_runin_architecture_heading_is_split_and_bold():
    blocks = _unit_blocks("gears_p8_untranslated.pdf")
    architecture = next(
        block
        for block in blocks
        if "Architecture" in block.text
    )

    assert architecture.block_type == "run_in_heading"
    assert architecture.bold


def test_otf_multi_letter_formula_subscript_is_protected():
    from pdf_zh_translator.pdf_layout import protect_text

    block = next(
        block
        for block in _unit_blocks("otf_p9_table_formula.pdf")
        if "denoted as" in block.text
    )
    _protected, mapping = protect_text(block.text)

    assert "s_{soft}" in mapping.values()


def test_otf_inline_kernel_formula_stays_native_as_one_spatial_cluster():
    texts = _plain_unit_texts("otf_p9_table_formula.pdf")

    explanation = next(text for text in texts if "Convergence of EOFT" in text)
    assert "K(u)" not in explanation
    assert not any("K(u)" in text for text in texts)


def test_flashsac_formula_split_prose_is_one_translation_unit():
    texts = _plain_unit_texts("flashsac_p6_formula_prose.pdf")

    assert texts.count("Weight Normalization.") == 1
    assert any("This constrains the network" in text for text in texts)


def test_flashsac_inline_variance_script_moves_with_formula():
    from pdf_zh_translator.pdf_layout import SENTINEL_RUN_RE, protect_text

    block = next(
        block
        for block in _unit_blocks("flashsac_p6_formula_prose.pdf")
        if "discounted return variance" in block.text
    )
    _protected, mapping = protect_text(block.text)

    assert "σ^{2}_{t,G}" in mapping.values()
    assert len(block.formula_anchors) == len(SENTINEL_RUN_RE.findall(block.text))


def test_otf_formula42_discourse_cue_is_translated_separately():
    texts = _plain_unit_texts("otf_p19_formula42.pdf")

    assert any("Furthermore," in text for text in texts)


def test_otf_short_variable_prefix_stays_with_inline_formula():
    texts = _plain_unit_texts("otf_p15_formula_statement.pdf")

    assert not any("Kv⊙q−s+d" in text for text in texts)
    assert any(text.startswith("Then we can get the solution") for text in texts)


def test_otf_math_mixed_explanations_are_complete_translation_units():
    texts = _plain_unit_texts("otf_p15_formula_statement.pdf")

    explanations = [text for text in texts if text.startswith("Thus we can get")]
    assert len(explanations) == 2
    assert all("f=−g" in text for text in explanations)
    knowing = next(text for text in texts if text.startswith("KnowingP1"))
    assert "we can get" in knowing
    assert "and" not in texts


def test_otf_small_caps_appendix_subheading_is_a_translation_unit():
    texts = _plain_unit_texts("otf_p15_formula_statement.pdf")

    assert "C.2 WITH CONSTRAINS" in texts


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


class _CDGSRecordTranslator(_GoldenStubTranslator):
    _TRANSLATIONS = {
        "• Scene:": "• 场景：",
        "• Start:": "• 起始状态：",
        "• Goal:": "• 目标状态：",
        "Table with a hook, red cube, and blue cube": "带有钩子、红色立方体和蓝色立方体的桌子",
        "Table with a hook,red cube, and blue cube": "带有钩子、红色立方体和蓝色立方体的桌子",
        "All objects (hook, red cube, blue cube) are in workspace": (
            "所有物体（钩子、红色立方体、蓝色立方体）均在工作区内"
        ),
        "Hook and blue cube are in workspace, red cube is beyond workspace": (
            "钩子和蓝色立方体在工作空间内，红色立方体在工作空间外"
        ),
        "Put the red cube where the blue cube is": (
            "将红色立方体放到蓝色立方体所在的位置"
        ),
    }

    def translate_batch(self, texts):
        fallback = super().translate_batch(texts)
        outputs = []
        for source, default in zip(texts, fallback):
            compact = " ".join(source.split())
            direct = self._TRANSLATIONS.get(compact)
            if direct is not None:
                outputs.append(direct)
                continue
            matched = next(
                (
                    f"{translated_label}{self._TRANSLATIONS[remainder]}"
                    for label, translated_label in self._TRANSLATIONS.items()
                    if label.startswith("• ")
                    and compact.startswith(f"{label} ")
                    and (remainder := compact[len(label) + 1 :])
                    in self._TRANSLATIONS
                ),
                None,
            )
            outputs.append(matched or default)
        return outputs


class _OTFStructureTranslator(_GoldenStubTranslator):
    """Stable translations for the OTF theorem, table caption, and list fixture."""

    _LIST_TRANSLATIONS = {
        "• nodes": "• 节点数（nodes）– 节点数量（默认10）",
        "• sources": "• 源节点数（sources）– 源节点数量（默认3）",
        "• sinks": "• 汇点数量（sinks）– 汇节点数量（默认值为3）",
        "• density": "• 密度（density）– 弧的数量（如表4所示）",
        "• mincost": "• 最小成本（mincost）– 最小弧成本（本文设为10）",
        "• maxcost": "• 最大成本（maxcost）– 最大弧成本（本文设为100）",
        "• supply": "• 供给量（supply）– 总供给量（本文设为10000）",
        "• capacitated": "• 容量限制（capacitated）– 受容量限制的骨架弧比例（0–100，本文设为100）",
        "• mincap": "• 最小容量（mincap）– 最小弧容量（见表4）",
        "• maxcap": "• 最大容量（maxcap）– 最大弧容量（见表4）",
    }

    def translate_batch(self, texts):
        fallback = super().translate_batch(texts)
        outputs = []
        for source, default in zip(texts, fallback):
            if source.startswith("Thus:") and "Thus we can get" in source:
                placeholders = " ".join(re.findall(r"⟦\d+⟧", source))
                outputs.append(f"因此：{placeholders}，由此可得：")
                continue
            if source.strip() == "Formulation of OFT":
                outputs.append("最优流传输的公式化")
                continue
            if source.startswith("We begin by de"):
                outputs.append(
                    "我们首先定义最优流传输如下。考虑⟦0⟧和⟦1⟧"
                    "是图⟦2⟧上的两个测度，其中⟦3⟧和(⟦4⟧)是两个平衡向量，"
                    "满足⟦5⟧。最优流传输的公式可具体表示为："
                )
                continue
            if source.startswith("Theorem 1."):
                placeholders = " ".join(re.findall(r"⟦\d+⟧", source))
                outputs.append(
                    "定理 1. OFT-Sinkhorn 算法的迭代格式线性收敛。"
                    f"更精确地，有 {placeholders} 且"
                )
                continue
            matched = next(
                (
                    translated
                    for prefix, translated in self._LIST_TRANSLATIONS.items()
                    if source.startswith(prefix)
                ),
                None,
            )
            if matched is not None:
                outputs.append(matched)
            elif source.startswith("Table 7:"):
                outputs.append("表7：解的稀疏度")
            elif source.startswith("Table 6:"):
                outputs.append("表6：超大规模稀疏图的参数说明")
            else:
                outputs.append(default)
        return outputs


class _OTFAcceptanceTranslator(_OTFStructureTranslator):
    """Deterministic OTF translations used by full-structure acceptance tests."""

    def translate_batch(self, texts):
        fallback = super().translate_batch(texts)
        outputs = []
        for source, default in zip(texts, fallback):
            placeholders = " ".join(re.findall(r"⟦\d+⟧", source))
            if source.strip() == "Formulation of OFT":
                outputs.append("最优流传输的公式化")
            elif source.startswith("We begin by de"):
                outputs.append(
                    "我们首先定义最优流传输如下，考虑"
                    f"{placeholders}是图上的两个测度。"
                )
            elif source.strip() == "Capacitated Constraints on Nodes":
                outputs.append("节点容量约束")
            elif source.startswith("Initially, we consider"):
                outputs.append(
                    "首先，我们考虑节点上的容量约束，即对式5中的优化"
                    f"施加约束{placeholders}。为处理这些容量约束，我们执行截断操作，"
                    "其中该迭代量在式9中定义。"
                )
            elif source.strip() == "Convergence of EOFT":
                outputs.append("EOFT 的收敛性")
            elif source.startswith("In Figure 5"):
                outputs.append(
                    "在图5中，我们展示边际分布如何随迭代次数演化。"
                    f"{placeholders}结果表明算法稳定收敛。"
                )
            elif source.startswith("Published as a conference paper"):
                outputs.append("发表于 ICLR 2025 的会议论文")
            elif source.startswith("D DETAILS ABOUT EXPERIMENTS"):
                outputs.append("实验细节")
            elif source.startswith("D.1 DETAILS ABOUT PARAMETERS"):
                outputs.append("D.1 参数细节")
            elif source.startswith("D.2 DETAILS ABOUT DATASET"):
                outputs.append("D.2 数据集细节")
            elif re.match(r"Table [1-7]:", source):
                number = re.match(r"Table ([1-7]):", source).group(1)
                outputs.append(f"表{number}：译后表注需要与表格横线保持安全间距")
            else:
                outputs.append(default)
        return outputs


class _OTFNodeResidualTranslator(_OTFAcceptanceTranslator):
    """Intentionally leaves the page-7 prose in English for QA testing."""

    def translate_batch(self, texts):
        translated = super().translate_batch(texts)
        return [
            source if source.startswith("Initially, we consider") else target
            for source, target in zip(texts, translated)
        ]


class _GearsTitleTranslator(_GoldenStubTranslator):
    """Reproduce the production GEARS title wrapping deterministically."""

    _TITLE = "GEARS：通过几何感知与动作扩散实现零样本仿真到真实灵巧操作"

    def translate_batch(self, texts):
        fallback = super().translate_batch(texts)
        return [
            self._TITLE if source.startswith("GEARS: Seeing Geometry") else target
            for source, target in zip(texts, fallback)
        ]


def _page_spans(page):
    return [
        span
        for block in page.get_text("dict")["blocks"]
        if block.get("type") == 0
        for line in block.get("lines", [])
        for span in line.get("spans", [])
        if span.get("text", "").strip()
    ]


def _span_is_bold(span):
    font = span.get("font", "").casefold()
    return any(marker in font for marker in ("bold", "w6", "heiti", "cmbx"))


def test_gears_first_page_title_is_a_title_role():
    blocks = _unit_blocks("gears_p1_title.pdf")
    title = next(
        block
        for block in blocks
        if "GEARS: Seeing Geometry" in block.text
    )

    assert title.block_type == "title"


def test_gears_centered_title_has_no_orphan_cjk_line(tmp_path):
    output_pdf = tmp_path / "gears-title.pdf"
    translate_pdf(
        input_pdf=FIXTURES / "gears_p1_title.pdf",
        output_pdf=output_pdf,
        translator=_GearsTitleTranslator(),
        preserve_graphics_text=True,
    )

    output = fitz.open(output_pdf)
    page = output[0]
    page_center = float(page.rect.width) / 2.0
    lines: dict[float, list[dict]] = {}
    for span in _page_spans(page):
        if span["bbox"][1] >= 75.0 or not re.search(r"[\u3400-\u9fff]", span["text"]):
            continue
        lines.setdefault(round(float(span["origin"][1]), 1), []).append(span)
    output.close()

    title_lines = [lines[key] for key in sorted(lines)]
    assert len(title_lines) >= 2
    line_boxes = [
        (
            min(float(span["bbox"][0]) for span in line),
            max(float(span["bbox"][2]) for span in line),
        )
        for line in title_lines
    ]
    cjk_counts = [
        len(re.findall(r"[\u3400-\u9fff]", "".join(span["text"] for span in line)))
        for line in title_lines
    ]
    assert cjk_counts[-1] >= 2
    assert all(abs((x0 + x1) / 2.0 - page_center) <= 2.0 for x0, x1 in line_boxes)
    assert (line_boxes[-1][1] - line_boxes[-1][0]) >= 0.3 * max(
        x1 - x0 for x0, x1 in line_boxes[:-1]
    )


def test_gears_title_orphan_is_a_qa_error():
    issues = verify_translation_issues(
        FIXTURES / "gears_p1_title.pdf",
        FIXTURES / "gears_p1_title_orphan_translated.pdf",
    )

    orphan = next(issue for issue in issues if issue.code == "font_role_title_orphan")
    assert orphan.severity == "error"
    assert "x=" in orphan.message and "y=" in orphan.message


def test_otf_page7_capacitated_node_prose_is_a_translation_unit():
    blocks = _unit_blocks("otf_p7_capacitated_constraints.pdf")
    texts = [
        " ".join(block.text.replace("\ue000", "").replace("\ue001", "").split())
        for block in blocks
    ]

    heading = next(
        block
        for block, text in zip(blocks, texts)
        if text == "Capacitated Constraints on Nodes"
    )
    body = next(text for text in texts if text.startswith("Initially, we consider"))
    assert heading.block_type == "run_in_heading"
    assert heading.bold
    assert "q" in body
    assert "Eq. 5" in body
    assert "Eq. 9" in body


def test_otf_page7_node_prose_translates_while_math_and_eq_links_survive(tmp_path):
    fixture_pdf = FIXTURES / "otf_p7_capacitated_constraints.pdf"
    input_pdf = tmp_path / "otf-p7-linked.pdf"
    source = fitz.open(fixture_pdf)
    eq5_link = next(
        link
        for link in source[0].get_links()
        if link.get("nameddest") == "equation.3.5"
    )
    source[0].insert_link(
        {
            "kind": fitz.LINK_URI,
            "from": eq5_link["from"],
            "uri": "https://example.invalid/equation.3.5",
        }
    )
    source.save(input_pdf)
    source.close()
    output_pdf = tmp_path / "otf-p7.pdf"
    translate_pdf(
        input_pdf=input_pdf,
        output_pdf=output_pdf,
        translator=_OTFAcceptanceTranslator(),
        preserve_graphics_text=True,
    )

    output = fitz.open(output_pdf)
    text = " ".join(output[0].get_text("text").split())
    links = output[0].get_links()
    output.close()

    assert "Capacitated Constraints on Nodes" not in text
    assert "Initially, we consider" not in text
    assert "节点容量约束" in text
    assert "q" in text and "r" in text
    assert "式5" in text and "式9" in text
    assert any(
        link.get("uri") == "https://example.invalid/equation.3.5"
        for link in links
    )


def test_otf_page7_untranslated_prose_is_a_qa_error(tmp_path):
    input_pdf = FIXTURES / "otf_p7_capacitated_constraints.pdf"
    output_pdf = tmp_path / "otf-p7-residual.pdf"
    translate_pdf(
        input_pdf=input_pdf,
        output_pdf=output_pdf,
        translator=_OTFNodeResidualTranslator(),
        preserve_graphics_text=True,
    )

    issues = verify_translation_issues(input_pdf, output_pdf)
    assert any(
        issue.code == "untranslated_english" and issue.severity == "error"
        for issue in issues
    )


def test_otf_page4_runin_bold_range_and_reading_order(tmp_path):
    output_pdf = tmp_path / "otf-p4-structure.pdf"
    translate_pdf(
        input_pdf=FIXTURES / "otf_p4_runin_formula.pdf",
        output_pdf=output_pdf,
        translator=_OTFAcceptanceTranslator(),
        preserve_graphics_text=True,
    )

    output = fitz.open(output_pdf)
    page = output[0]
    spans = _page_spans(page)
    text = " ".join(page.get_text("text").split())
    output.close()

    heading = [span for span in spans if "最优流传输的公式化" in span["text"]]
    lines: dict[float, list[dict]] = {}
    for span in spans:
        lines.setdefault(round(float(span["origin"][1]), 1), []).append(span)
    body = next(
        sorted(line, key=lambda span: span["bbox"][0])
        for line in lines.values()
        if "我们首先" in "".join(
            span["text"] for span in sorted(line, key=lambda span: span["bbox"][0])
        )
    )
    body = [span for span in body if re.search(r"[\u4e00-\u9fff]", span["text"])]
    assert heading and all(_span_is_bold(span) for span in heading)
    assert "我们首先" in "".join(span["text"] for span in body)
    assert all(not _span_is_bold(span) for span in body)
    compact = text.replace(" ", "")
    assert "P1" in compact and "U(a,b)" in compact
    assert "in Eq. 1." not in text


def test_otf_page9_runin_bold_does_not_spill_into_body(tmp_path):
    output_pdf = tmp_path / "otf-p9-structure.pdf"
    translate_pdf(
        input_pdf=FIXTURES / "otf_p9_table_formula.pdf",
        output_pdf=output_pdf,
        translator=_OTFAcceptanceTranslator(),
        preserve_graphics_text=True,
    )

    output = fitz.open(output_pdf)
    spans = _page_spans(output[0])
    output.close()

    heading = [span for span in spans if "EOFT 的收敛性" in span["text"]]
    body = [span for span in spans if "在图5" in span["text"]]
    assert heading and all(_span_is_bold(span) for span in heading)
    assert body and all(not _span_is_bold(span) for span in body)


def test_otf_repeated_header_is_translated_on_every_page():
    from pdf_zh_translator.pdf_layout import prepare_translation_units, strip_sentinels

    source = fitz.open(FIXTURES / "otf_full_structure.pdf")
    units, _, _ = prepare_translation_units(source, preserve_graphics_text=True)
    pages = {
        block.page_index
        for block, _, _ in units
        if "Published as a conference paper" in strip_sentinels(block.text)
    }
    source.close()

    assert pages == set(range(19))


def test_otf_page18_formula_gap_keeps_prose_in_one_reading_order_unit():
    from pdf_zh_translator.pdf_layout import prepare_translation_units, strip_sentinels

    source = fitz.open(FIXTURES / "otf_full_structure.pdf")
    units, _, _ = prepare_translation_units(source, preserve_graphics_text=True)
    source.close()
    matches = [
        block
        for block, _, _ in units
        if block.page_index == 17
        and "stopping criteria" in strip_sentinels(block.text)
    ]

    assert len(matches) == 1
    text = " ".join(strip_sentinels(matches[0].text).split())
    assert "with a small error criteria" in text
    assert "Before analyzing the global convergence" in text
    assert matches[0].keepout_bboxes


def test_otf_appendix_heading_keeps_source_heading_role():
    blocks = _unit_blocks("otf_p16_appendix_tables.pdf")
    appendix = next(
        block for block in blocks if "DETAILS ABOUT EXPERIMENTS" in block.text
    )
    d1 = next(block for block in blocks if "D.1 DETAILS ABOUT PARAMETERS" in block.text)

    assert appendix.block_type == "heading"
    assert appendix.bold
    assert appendix.font_size >= d1.font_size + 0.5
    assert appendix.bbox[0] <= d1.bbox[0] + 0.5


def test_otf_fragmented_lettered_appendix_heading_keeps_source_role(tmp_path):
    blocks = _unit_blocks("otf_p17_appendix_table.pdf")
    heading = next(
        block for block in blocks if "CONVERGENCE OF OFT-SINKHRON" in block.text
    )

    assert heading.block_type == "heading"
    assert heading.bold
    assert heading.font_size >= 11.5

    class AppendixHeadingTranslator(_OTFStructureTranslator):
        def translate_batch(self, texts):
            fallback = super().translate_batch(texts)
            return [
                "E OFT-Sinkhorn 算法的收敛性"
                if "CONVERGENCE OF OFT-SINKHRON" in source
                else target
                for source, target in zip(texts, fallback)
            ]

    output_pdf = tmp_path / "otf-p17-appendix-heading.pdf"
    translate_pdf(
        input_pdf=FIXTURES / "otf_p17_appendix_table.pdf",
        output_pdf=output_pdf,
        translator=AppendixHeadingTranslator(),
        preserve_graphics_text=True,
    )
    output = fitz.open(output_pdf)
    spans = [
        span
        for block in output[0].get_text("dict")["blocks"]
        for line in block.get("lines", [])
        for span in line.get("spans", [])
        if "算法的收敛性" in span.get("text", "")
    ]
    output.close()

    assert spans
    assert min(span["size"] for span in spans) >= 10.5
    assert all(_span_is_bold(span) for span in spans)


def test_otf_qa_flags_fragmented_appendix_heading_rendered_as_body(tmp_path):
    fixture = FIXTURES / "otf_p17_appendix_table.pdf"
    degraded = tmp_path / "otf-p17-heading-as-body.pdf"
    document = fitz.open(fixture)
    page = document[0]
    page.add_redact_annot(fitz.Rect(107.0, 694.0, 382.0, 710.0), fill=(1, 1, 1))
    page.apply_redactions()
    font_file = FIXTURES.parent.parent / "data/fonts/SongtiSC-Regular.ttf"
    page.insert_font(fontname="qa-body", fontfile=str(font_file))
    page.insert_text(
        (109.0, 705.0),
        "E OFT-Sinkhorn 算法的收敛性",
        fontname="qa-body",
        fontsize=7.0,
    )
    document.save(degraded)
    document.close()

    issues = verify_translation_issues(fixture, degraded)
    heading_issues = [
        issue for issue in issues if issue.code == "font_role_heading_mismatch"
    ]

    assert heading_issues
    assert all(issue.severity == "error" for issue in heading_issues)
    assert any("x=" in issue.message and "y=" in issue.message for issue in heading_issues)


def test_memorywam_float_wrapped_figure_reference_stays_body():
    from pdf_zh_translator.pdf_layout import strip_sentinels

    blocks = _unit_blocks("memorywam_p8_float_wrap.pdf")
    reference = next(
        block
        for block in blocks
        if "Fig. 5. In Shell Game" in strip_sentinels(block.text)
    )
    actual_caption = next(
        block
        for block in blocks
        if "Figure 5: Illustration" in strip_sentinels(block.text)
    )

    assert reference.block_type == "body"
    assert actual_caption.block_type == "caption"


@pytest.mark.parametrize(
    "fixture",
    ["guidedvla_p15_piecewise.pdf", "guidedvla_p21_formula.pdf"],
)
def test_guidedvla_piecewise_condition_labels_stay_out_of_translation_units(fixture):
    from pdf_zh_translator.pdf_layout import strip_sentinels

    units = _unit_blocks(fixture)
    plain = {
        " ".join(strip_sentinels(block.text).split()).casefold()
        for block in units
    }

    assert "if" not in plain
    assert not any(text.startswith("otherwise") for text in plain)
    assert any(
        "smoothing parameter" in text or "final objective" in text
        for text in plain
    )


def test_guidedvla_fragmented_byline_and_affiliation_stay_metadata():
    from pdf_zh_translator.pdf_layout import strip_sentinels

    units = _unit_blocks("guidedvla_p1_metadata.pdf")
    translated = [
        " ".join(strip_sentinels(block.text).split())
        for block in units
        if block.should_translate
    ]

    assert not any("Xiaosong Jia" in text for text in translated)
    assert not any("Shanghai Jiao Tong University" in text for text in translated)
    assert any(text.startswith("GuidedVLA:") for text in translated)
    assert any(text.startswith("Abstract") for text in translated)
    assert any(text.startswith("Fig. 1:") for text in translated)


def test_guidedvla_figure_labels_survive_caption_redaction(tmp_path):
    input_pdf = FIXTURES / "guidedvla_p1_metadata.pdf"
    output_pdf = tmp_path / "guidedvla-p1.pdf"
    translate_pdf(
        input_pdf=input_pdf,
        output_pdf=output_pdf,
        translator=_GoldenStubTranslator(),
        preserve_graphics_text=True,
    )

    source = fitz.open(input_pdf)
    output = fitz.open(output_pdf)
    source_spans = _page_spans(source[0])
    output_spans = _page_spans(output[0])
    for label in ("Move", "Sweep", "Dump", "baseline", "ours"):
        assert sum(span["text"] == label for span in output_spans) == sum(
            span["text"] == label for span in source_spans
        )

    for bbox in (
        (80.7, 494.3, 95.6, 500.9),
        (80.9, 482.4, 98.1, 489.0),
        (80.1, 470.5, 96.0, 477.1),
        (421.0, 496.5, 448.2, 504.7),
        (497.7, 496.3, 512.1, 504.5),
        (254.5, 496.7, 281.6, 504.9),
        (334.4, 496.2, 348.8, 504.4),
    ):
        source_pixmap = source[0].get_pixmap(clip=fitz.Rect(bbox), dpi=180, alpha=False)
        output_pixmap = output[0].get_pixmap(clip=fitz.Rect(bbox), dpi=180, alpha=False)
        assert output_pixmap.samples == source_pixmap.samples
    source.close()
    output.close()


def test_cdgs_goal_prose_does_not_merge_into_preserved_action_skeleton():
    texts = _plain_unit_texts("cdgs_p25_preserved.pdf")

    assert sum("Put the red cube where the blue cube is" in text for text in texts) == 2
    assert not any("Action Skeleton" in text for text in texts)
    assert not any("pick(blue cube)" in text for text in texts)


def test_cdgs_runin_task_records_reflow_without_shrinking_or_bold_spill(tmp_path):
    input_pdf = FIXTURES / "cdgs_p25_preserved.pdf"
    output_pdf = tmp_path / "cdgs-p25.pdf"
    translate_pdf(
        input_pdf=input_pdf,
        output_pdf=output_pdf,
        translator=_CDGSRecordTranslator(),
        preserve_graphics_text=True,
    )

    issues = verify_translation_issues(input_pdf, output_pdf)
    assert not [
        issue
        for issue in issues
        if issue.code in {"font_role_bold_spill", "font_role_size_mismatch"}
    ]

    source = fitz.open(input_pdf)
    translated = fitz.open(output_pdf)
    source_text = source[0].get_text("text")
    output_spans = _page_spans(translated[0])
    output_text = translated[0].get_text("text")
    source.close()
    translated.close()

    for label in ("场景：", "起始状态：", "目标状态："):
        label_spans = [span for span in output_spans if label in span.get("text", "")]
        assert label_spans
        assert all(
            (span.get("flags", 0) & 16)
            or "Bold" in span.get("font", "")
            or "W6" in span.get("font", "")
            for span in label_spans
        )
    body_spans = [
        span
        for span in output_spans
        if any(
            term in span.get("text", "")
            for term in ("带有钩子", "所有物体", "钩子和蓝色", "将红色")
        )
    ]
    assert body_spans
    assert min(span["size"] for span in body_spans) >= 8.4
    assert all(not (span.get("flags", 0) & 16) for span in body_spans)
    assert output_text.count("Action Skeleton:") == source_text.count("Action Skeleton:")
    assert output_text.count("pick(blue cube)") == source_text.count("pick(blue cube)")


@pytest.mark.parametrize(
    ("fixture", "table_numbers"),
    [
        ("otf_p8_typography.pdf", (1,)),
        ("otf_p9_table_formula.pdf", (2, 3)),
        ("otf_p16_appendix_tables.pdf", (4, 5)),
        ("otf_p17_appendix_table.pdf", (6, 7)),
    ],
)
def test_otf_caption_union_glyph_clearance_is_at_least_three_points(
    tmp_path, fixture, table_numbers
):
    output_pdf = tmp_path / fixture
    translate_pdf(
        input_pdf=FIXTURES / fixture,
        output_pdf=output_pdf,
        translator=_OTFAcceptanceTranslator(),
        preserve_graphics_text=True,
    )

    output = fitz.open(output_pdf)
    page = output[0]
    source = fitz.open(FIXTURES / fixture)
    source_page = source[0]
    spans = _page_spans(page)
    source_rules = sorted(
        float(drawing["rect"].y0)
        for drawing in source_page.get_drawings()
        if drawing["rect"].width >= 100.0
        and drawing["rect"].height <= 0.25
    )
    for number in table_numbers:
        seeds = [span for span in spans if f"表{number}：" in span["text"]]
        assert seeds, f"missing translated caption for Table {number}"
        seed_top = min(span["bbox"][1] for span in seeds)
        seed_bottom = max(span["bbox"][3] for span in seeds)
        caption_spans = [
            span
            for span in spans
            if seed_top - 1.0 <= span["bbox"][1]
            and span["bbox"][3] <= seed_bottom + 24.0
            and re.search(r"[\u4e00-\u9fff]", span["text"])
        ]
        union_bottom = max(span["bbox"][3] for span in caption_spans)
        source_caption = next(
            block
            for block in _unit_blocks(fixture)
            if re.match(
                rf"Table {number}\s*:",
                " ".join(
                    block.text.replace("\ue000", "").replace("\ue001", "").split()
                ),
            )
        )
        first_rule = min(
            rule for rule in source_rules if rule >= source_caption.bbox[1]
        )
        assert first_rule - union_bottom >= 3.0
    source.close()
    output.close()


def test_otf_runin_heading_starts_at_source_column_before_inline_math(tmp_path):
    output_pdf = tmp_path / "otf-p4.pdf"
    translate_pdf(
        input_pdf=FIXTURES / "otf_p4_runin_formula.pdf",
        output_pdf=output_pdf,
        translator=_OTFStructureTranslator(),
        preserve_graphics_text=True,
    )

    output = fitz.open(output_pdf)
    heading_spans = [
        span
        for block in output[0].get_text("dict")["blocks"]
        if block.get("type") == 0
        for line in block.get("lines", [])
        for span in line.get("spans", [])
        if "最优流传输的公式化" in span.get("text", "")
    ]
    output.close()

    assert heading_spans
    assert min(span["bbox"][0] for span in heading_spans) <= 110.0
    assert min(span["size"] for span in heading_spans) >= 8.5


def test_otf_derivation_translates_both_cues_around_preserved_math(tmp_path):
    input_pdf = FIXTURES / "otf_p16_derivation_cue.pdf"
    texts = _plain_unit_texts(input_pdf.name)
    combined = [text for text in texts if text.startswith("Thus:")]

    assert len(combined) == 1
    assert "Thus we can get" in combined[0]

    output_pdf = tmp_path / "otf-p16.pdf"
    translate_pdf(
        input_pdf=input_pdf,
        output_pdf=output_pdf,
        translator=_OTFStructureTranslator(),
        preserve_graphics_text=True,
    )

    output = fitz.open(output_pdf)
    output_text = output[0].get_text("text")
    output.close()

    assert "Thus" not in output_text
    assert "因此：" in output_text
    assert "由此可得：" in output_text
    assert "Diag" in output_text


def test_otf_theorem_keeps_body_size_and_starts_before_the_formula(tmp_path):
    output_pdf = tmp_path / "otf-p6.pdf"
    translate_pdf(
        input_pdf=FIXTURES / "otf_p6_theorem.pdf",
        output_pdf=output_pdf,
        translator=_OTFStructureTranslator(),
        preserve_graphics_text=True,
    )

    output = fitz.open(output_pdf)
    theorem_spans = [
        span
        for block in output[0].get_text("dict")["blocks"]
        if block.get("type") == 0
        for line in block.get("lines", [])
        for span in line.get("spans", [])
        if "定理" in span.get("text", "")
    ]
    output.close()

    assert theorem_spans
    assert min(span["size"] for span in theorem_spans) >= 8.5
    assert min(span["bbox"][0] for span in theorem_spans) <= 110.0


def test_otf_appendix_list_has_uniform_size_and_table_caption_clearance(tmp_path):
    output_pdf = tmp_path / "otf-p17.pdf"
    translate_pdf(
        input_pdf=FIXTURES / "otf_p17_appendix_table.pdf",
        output_pdf=output_pdf,
        translator=_OTFStructureTranslator(),
        preserve_graphics_text=True,
    )

    output = fitz.open(output_pdf)
    page = output[0]
    spans = [
        span
        for block in page.get_text("dict")["blocks"]
        if block.get("type") == 0
        for line in block.get("lines", [])
        for span in line.get("spans", [])
    ]
    list_spans = [span for span in spans if span.get("text", "").startswith("•")]
    caption = next(span for span in spans if "表7：" in span.get("text", ""))
    rules_below = [
        float(drawing["rect"].y0)
        for drawing in page.get_drawings()
        if drawing["rect"].width >= 100.0
        and 0.0 <= drawing["rect"].y0 - caption["bbox"][3] <= 12.0
    ]
    output.close()

    assert len(list_spans) == 10
    sizes = [span["size"] for span in list_spans]
    assert min(sizes) >= 8.8
    assert max(sizes) - min(sizes) <= 0.25
    assert rules_below
    assert min(rules_below) - caption["bbox"][3] >= 3.0


def _insert_qa_cjk_text(
    source_pdf: Path,
    output_pdf: Path,
    *,
    point: tuple[float, float],
    text: str,
    size: float,
    bold: bool = False,
) -> None:
    document = fitz.open(source_pdf)
    page = document[0]
    font_file = FIXTURES.parent.parent / "data/fonts" / (
        "HiraginoSansGB-W6.ttf" if bold else "SongtiSC-Regular.ttf"
    )
    alias = "qa-bold" if bold else "qa-body"
    page.insert_font(fontname=alias, fontfile=str(font_file))
    page.insert_text(point, text, fontname=alias, fontsize=size)
    document.save(output_pdf)
    document.close()


def test_otf_qa_detects_runin_bold_scope_spill_with_coordinates(tmp_path):
    translated_pdf = tmp_path / "otf-p4-clean.pdf"
    translate_pdf(
        input_pdf=FIXTURES / "otf_p4_runin_formula.pdf",
        output_pdf=translated_pdf,
        translator=_OTFAcceptanceTranslator(),
        preserve_graphics_text=True,
    )
    corrupted_pdf = tmp_path / "otf-p4-bold-spill.pdf"
    _insert_qa_cjk_text(
        translated_pdf,
        corrupted_pdf,
        point=(235.0, 108.0),
        text="错误粗体扩散",
        size=9.0,
        bold=True,
    )

    issues = verify_translation_issues(
        FIXTURES / "otf_p4_runin_formula.pdf",
        corrupted_pdf,
    )
    spill = next(issue for issue in issues if issue.code == "font_role_bold_spill")
    assert spill.severity == "error"
    assert "x=" in spill.message and "y=" in spill.message


def test_otf_qa_detects_body_font_size_outlier_over_half_point(tmp_path):
    translated_pdf = tmp_path / "otf-p17-clean.pdf"
    translate_pdf(
        input_pdf=FIXTURES / "otf_p17_appendix_table.pdf",
        output_pdf=translated_pdf,
        translator=_OTFStructureTranslator(),
        preserve_graphics_text=True,
    )
    corrupted_pdf = tmp_path / "otf-p17-size-outlier.pdf"
    _insert_qa_cjk_text(
        translated_pdf,
        corrupted_pdf,
        point=(200.0, 518.0),
        text="异常字号",
        size=6.0,
    )

    issues = verify_translation_issues(
        FIXTURES / "otf_p17_appendix_table.pdf",
        corrupted_pdf,
    )
    outlier = next(issue for issue in issues if issue.code == "font_role_size_mismatch")
    assert outlier.severity == "error"
    assert "6.00pt" in outlier.message


def test_otf_qa_uses_raster_ink_to_detect_formula_text_collision(tmp_path):
    translated_pdf = tmp_path / "otf-p4-clean.pdf"
    translate_pdf(
        input_pdf=FIXTURES / "otf_p4_runin_formula.pdf",
        output_pdf=translated_pdf,
        translator=_OTFAcceptanceTranslator(),
        preserve_graphics_text=True,
    )
    document = fitz.open(translated_pdf)
    formula_span = next(
        span
        for block in document[0].get_text("dict")["blocks"]
        for line in block.get("lines", [])
        for span in line.get("spans", [])
        if "P1" in span.get("text", "")
    )
    document.close()
    corrupted_pdf = tmp_path / "otf-p4-raster-overlap.pdf"
    _insert_qa_cjk_text(
        translated_pdf,
        corrupted_pdf,
        point=(formula_span["bbox"][0], formula_span["origin"][1]),
        text="重叠",
        size=float(formula_span["size"]),
    )

    issues = verify_translation_issues(
        FIXTURES / "otf_p4_runin_formula.pdf",
        corrupted_pdf,
    )
    overlap = next(issue for issue in issues if issue.code == "raster_ink_overlap")
    assert overlap.severity == "error"
    assert "formula-text" in overlap.message


def test_gears_inline_formula_adjacency_is_not_an_ink_overlap():
    issues = verify_translation_issues(
        FIXTURES / "gears_p6_inline_formulas.pdf",
        FIXTURES / "gears_p6_inline_formulas_translated.pdf",
    )

    assert not [issue for issue in issues if issue.code == "raster_ink_overlap"]


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


def test_dynaguide_unnumbered_display_formula_is_not_a_translation_unit():
    from pdf_zh_translator.pdf_layout import (
        prepare_translation_units,
        protect_text,
        strip_sentinels,
    )

    source = fitz.open(
        FIXTURES / "dynaguide_p26_unnumbered_display_formula.pdf"
    )
    units, _, _ = prepare_translation_units(source, preserve_graphics_text=True)
    texts = [
        " ".join(strip_sentinels(block.text).split())
        for block, _, _ in units
    ]
    source.close()

    paragraph_index = next(
        index
        for index, text in enumerate(texts)
        if "There are different ways of combining" in text
    )
    protected, mapping = protect_text(units[paragraph_index][0].text)
    assert "in" in protected
    assert all("in" not in fragment for fragment in mapping.values())
    assert not any(text.startswith("2σ ||") for text in texts)


def test_hdflow_forward_process_translates_all_formula_adjacent_prose():
    from pdf_zh_translator.pdf_layout import (
        SENTINEL_RUN_RE,
        bbox_intersection_area,
        prepare_translation_units,
        protect_text,
        strip_sentinels,
    )

    source = fitz.open(FIXTURES / "hdflow_p2_formula_prose.pdf")
    units, _, _ = prepare_translation_units(source, preserve_graphics_text=True)
    forward_units = [
        block
        for block, _, _ in units
        if 410.0 <= block.bbox[1] <= 515.0
    ]
    source.close()

    texts = [" ".join(strip_sentinels(block.text).split()) for block in forward_units]
    assert any("according to a variance schedule" in text for text in texts)
    assert any("A key property is that we can" in text for text in texts)
    assert any(
        "As" in text
        and "approaches an isotropic Gaussian distribution" in text
        for text in texts
    )
    assert any("where" in text and "and" in text for text in texts)

    sample = next(
        block
        for block in forward_units
        if "at any timestep" in strip_sentinels(block.text)
    )
    protected, mapping = protect_text(sample.text)
    assert not re.search(r"\bx⟦", protected)
    assert any("x_{ℓ}" in formula for formula in mapping.values())
    assert len(SENTINEL_RUN_RE.findall(sample.text)) == len(sample.source_math_bboxes)

    gaussian = next(
        block
        for block in forward_units
        if "approaches an isotropic Gaussian" in strip_sentinels(block.text)
    )
    gaussian_protected, gaussian_mapping = protect_text(gaussian.text)
    assert "As⟦" in gaussian_protected
    assert any("ℓ→L" in formula and "x_{L}" in formula for formula in gaussian_mapping.values())
    assert any("N(0,I)" in formula for formula in gaussian_mapping.values())

    # The product lower limit sits less than one point above the wrapped
    # ``distribution`` line. No redaction may intersect it, even before the
    # normal redaction margin is applied.
    product_lower_limit = (316.8489, 489.8888, 341.1045, 502.1877)
    assert all(
        bbox_intersection_area(redact, product_lower_limit) == 0
        for redact in gaussian.redact_bboxes or []
    )


def test_hdflow_boxed_formula_explanations_are_complete_units():
    from pdf_zh_translator.pdf_layout import (
        prepare_translation_units,
        protect_text,
        strip_sentinels,
    )

    source = fitz.open(FIXTURES / "hdflow_p5_formula_explanations.pdf")
    units, _, _ = prepare_translation_units(source, preserve_graphics_text=True)
    source.close()

    theorem = next(
        block
        for block, _, _ in units
        if "Proposition 4.2" in strip_sentinels(block.text)
    )
    theorem_text = " ".join(strip_sentinels(theorem.text).split())
    assert "guidance gap" in theorem_text
    assert "high-dimensional latent spaces" in theorem_text
    assert "constant independent of dimensionality" in theorem_text
    theorem_protected, theorem_mapping = protect_text(theorem.text)
    assert "guidance gap" in theorem_protected
    assert "ga⟦" not in theorem_protected
    assert any("z_{t}" in formula for formula in theorem_mapping.values())

    guidance = next(
        block
        for block, _, _ in units
        if "is the EBM guidance and" in strip_sentinels(block.text)
    )
    guidance_text = " ".join(strip_sentinels(guidance.text).split())
    assert guidance_text.startswith("where")
    guidance_protected, guidance_mapping = protect_text(guidance.text)
    assert "where⟦" in guidance_protected
    assert len(guidance_mapping) >= 2

    caption = next(
        block
        for block, _, _ in units
        if strip_sentinels(block.text).startswith("Figure 2.")
    )
    caption_text = " ".join(strip_sentinels(caption.text).split())
    assert "To prevent manifold deviation" in caption_text
    assert "manifold devia-" not in caption_text
    caption_protected, caption_mapping = protect_text(caption.text)
    assert not re.search(r"(?:^|\s)tion,", caption_protected)
    assert sum(formula == "z^{temp}" for formula in caption_mapping.values()) == 2
    assert sum(formula == "_{ℓ}_{−}_{1}" for formula in caption_mapping.values()) == 2


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


def test_source_unit_qa_allows_affiliation_email_identity():
    from pdf_zh_translator.pdf_layout import (
        TextBlock,
        _translation_retains_source_prose_run,
    )

    block = TextBlock(
        page_index=0,
        bbox=(55.4, 656.9, 290.9, 677.4),
        text=(
            "Peking University Galbot University of Toronto. "
            "Correspondence to: Nandiraju Gireesh "
            "<2401112103@stu.pku.edu.cn>."
        ),
        font_size=8.0,
        color=(0.0, 0.0, 0.0),
    )

    assert not _translation_retains_source_prose_run(
        block,
        "北京大学 Galbot 多伦多大学。通讯作者：Nandiraju Gireesh "
        "<2401112103@stu.pku.edu.cn>。",
    )


def test_source_unit_qa_allows_term_before_author_year_citation():
    from pdf_zh_translator.pdf_layout import (
        TextBlock,
        _translation_retains_source_prose_run,
    )

    block = TextBlock(
        page_index=1,
        bbox=(307.4, 647.5, 543.1, 705.5),
        text=(
            "For conditional generation, classifier-free guidance (CFG) "
            "(Ho & Salimans, 2022) is commonly used. The model is trained "
            "on conditional and unconditional inputs."
        ),
        font_size=9.0,
        color=(0.0, 0.0, 0.0),
    )

    assert not _translation_retains_source_prose_run(
        block,
        "对于条件生成，通常采用无分类器引导（Classifier-Free Guidance, CFG）"
        "（Ho 和 Salimans，2022）。模型在条件输入和无条件输入上训练。",
    )


def test_source_unit_qa_still_flags_prose_after_author_year_citation():
    from pdf_zh_translator.pdf_layout import (
        TextBlock,
        _translation_retains_source_prose_run,
    )

    block = TextBlock(
        page_index=1,
        bbox=(307.4, 647.5, 543.1, 705.5),
        text=(
            "Classifier-free guidance (Ho & Salimans, 2022) is commonly used. "
            "The model is trained on both conditional and unconditional inputs."
        ),
        font_size=9.0,
        color=(0.0, 0.0, 0.0),
    )

    assert _translation_retains_source_prose_run(
        block,
        "常用无分类器引导（Ho 和 Salimans，2022）。"
        "The model is trained on both conditional and unconditional inputs.",
    )


def test_source_unit_qa_flags_short_formula_adjacent_phrase():
    from pdf_zh_translator.pdf_layout import (
        TextBlock,
        _translation_retains_source_prose_run,
    )

    block = TextBlock(
        page_index=1,
        bbox=(412.4, 452.8, 541.4, 462.9),
        text="A key property is that we can",
        font_size=10.0,
        color=(0.0, 0.0, 0.0),
    )

    assert _translation_retains_source_prose_run(
        block,
        "A key property is that we can",
    )
