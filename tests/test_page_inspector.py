"""Regression tests for the post-translation visual inspector.

Fixture pages come from the CC BY 4.0 OFT paper (see fixtures README) paired
with its production translation, one page per defect class reported from the
2026-08-11 production review:

- contribution bullets shrunk to 6.4pt (font_size_drift, list_font_inconsistent)
- inline formula sprites with clipped ascenders (formula_clipped)
- table header rules rebuilt at wrong offsets (table_structure_mismatch)
- bold author names overprinting references (reference_overlap, reference_bold_style)
- source display equation shifted synthetically (display_formula_misaligned)
"""

from __future__ import annotations

from collections import Counter
from pathlib import Path
from types import SimpleNamespace

import fitz
import pytest

from pdf_zh_translator.page_inspector import (
    _LIST_MARKER_RE,
    INSPECTOR_ISSUE_CODES,
    _edge_cut,
    _figure_graphic_regions,
    _font_size_issues,
    _line_table_bboxes,
    _mask_coverage,
    _reference_issues,
    _rule_clusters,
    _table_structure_issues,
    _text_blocks,
    _untranslated_block_issues,
    inspect_translation,
)

FIXTURES = Path(__file__).parent / "fixtures"


def _reference_pair_page(*, overlap: bool) -> fitz.Document:
    document = fitz.open()
    page = document.new_page(width=300, height=180)
    page.insert_text((40, 60), "Proceedings of The 6th", fontsize=10)
    page.insert_text(
        (70, 65 if overlap else 82),
        "Proceedings of Machine Learning",
        fontsize=10,
    )
    return document


def test_reference_overlap_ignores_source_native_tight_lines():
    original = _reference_pair_page(overlap=True)
    translated = _reference_pair_page(overlap=True)

    issues = _reference_issues(original[0], translated[0], 1, 0.0)

    assert not [issue for issue in issues if issue.code == "reference_overlap"]
    translated.close()
    original.close()


def test_reference_overlap_still_reports_translation_added_collision():
    original = _reference_pair_page(overlap=False)
    translated = _reference_pair_page(overlap=True)

    issues = _reference_issues(original[0], translated[0], 1, 0.0)

    assert [issue for issue in issues if issue.code == "reference_overlap"]
    translated.close()
    original.close()


def test_reference_content_ignores_confirmed_translation_unit_inside_broad_region():
    from pdf_zh_translator.pdf_layout import (
        _reference_section_start_y,
        ensure_font_pack_files,
    )

    body_font, _bold_font = ensure_font_pack_files([])
    assert body_font is not None
    source_body = (
        "3. One can approximately model all conditional distributions by training a "
        "family of shared models, and this numbered body paragraph must still be "
        "translated before the bibliography begins."
    )
    reference_text = (
        "[1] Smith, J., Doe, A., and Lee, R. Reliable representation learning "
        "for scientific documents with robust evaluation across multiple domains and "
        "practical deployment settings. Journal of Machine Learning Research, 2024."
    )

    original = fitz.open()
    original_page = original.new_page(width=360, height=240)
    original_page.insert_textbox(
        fitz.Rect(30, 25, 330, 75), source_body, fontsize=9
    )
    original_page.insert_text((30, 95), "References", fontsize=12)
    original_page.insert_textbox(
        fitz.Rect(30, 105, 330, 185), reference_text, fontsize=9
    )

    translated = fitz.open()
    translated_page = translated.new_page(width=360, height=240)
    translated_page.insert_font(fontname="cjkbody", fontfile=str(body_font))
    translated_page.insert_textbox(
        fitz.Rect(30, 25, 330, 75),
        "该附录段落位于参考文献之后，应当正常翻译，不能计为参考文献内容发生变化。",
        fontsize=9,
        fontname="cjkbody",
    )
    translated_page.insert_text(
        (30, 95), "参考文献", fontsize=12, fontname="cjkbody"
    )
    translated_page.insert_textbox(
        fitz.Rect(30, 105, 330, 185), reference_text, fontsize=9
    )
    source_role = SimpleNamespace(
        bbox=(30.0, 25.0, 330.0, 75.0),
        text=source_body,
    )

    issues = _reference_issues(
        original_page,
        translated_page,
        1,
        _reference_section_start_y(original_page),
        source_role_blocks=[source_role],
    )

    assert not [issue for issue in issues if issue.code == "reference_content_changed"]
    translated.close()
    original.close()


def test_reference_content_still_flags_reference_like_translation_unit():
    from pdf_zh_translator.pdf_layout import ensure_font_pack_files

    body_font, _bold_font = ensure_font_pack_files([])
    assert body_font is not None
    reference_text = (
        "[1] Smith, J., Doe, A., and Lee, R. Reliable representation learning "
        "for scientific documents with robust evaluation across multiple domains and "
        "practical deployment settings. Journal of Machine Learning Research, 2024."
    )

    original = fitz.open()
    original_page = original.new_page(width=360, height=200)
    original_page.insert_textbox(
        fitz.Rect(30, 45, 330, 135), reference_text, fontsize=9
    )

    translated = fitz.open()
    translated_page = translated.new_page(width=360, height=200)
    translated_page.insert_font(fontname="cjkbody", fontfile=str(body_font))
    translated_page.insert_textbox(
        fitz.Rect(30, 45, 330, 135),
        "史密斯、杜和李。面向科学文档的可靠表征学习。机器学习研究期刊，二〇二四年。",
        fontsize=9,
        fontname="cjkbody",
    )
    source_role = SimpleNamespace(
        bbox=(30.0, 45.0, 330.0, 135.0),
        text=reference_text,
    )

    issues = _reference_issues(
        original_page,
        translated_page,
        1,
        0.0,
        source_role_blocks=[source_role],
    )

    assert [issue for issue in issues if issue.code == "reference_content_changed"]
    translated.close()
    original.close()


def test_reference_continuation_stops_when_appendix_heading_starts_new_page(
    tmp_path, monkeypatch
):
    from pdf_zh_translator.pdf_layout import TextBlock, ensure_font_pack_files

    body_font, _bold_font = ensure_font_pack_files([])
    assert body_font is not None
    reference_text = (
        "[1] Smith, J. Reliable world models. Proceedings of Learning Systems, 2024. "
        "[2] Doe, A. Diffusion planning. Journal of Machine Learning, 2023."
    )
    appendix_body = (
        "We describe how sampling observations from a diffusion world model works. "
        "The solver follows prior work while retaining the learned score and the "
        "continuous process for stable prediction."
    )

    original = fitz.open()
    original_reference = original.new_page(width=360, height=240)
    original_reference.insert_text((30, 30), "References", fontsize=12)
    original_reference.insert_textbox(
        fitz.Rect(30, 70, 330, 205), reference_text, fontsize=9
    )
    original_appendix = original.new_page(width=360, height=240)
    original_appendix.insert_text(
        (30, 35), "A Sampling observations in DIAMOND", fontsize=12
    )
    original_appendix.insert_textbox(
        fitz.Rect(30, 48, 330, 105), appendix_body, fontsize=9
    )

    translated = fitz.open()
    translated_reference = translated.new_page(width=360, height=240)
    translated_reference.insert_font(fontname="cjkbody", fontfile=str(body_font))
    translated_reference.insert_text(
        (30, 30), "References", fontsize=12
    )
    translated_reference.insert_textbox(
        fitz.Rect(30, 70, 330, 205), reference_text, fontsize=9
    )
    translated_appendix = translated.new_page(width=360, height=240)
    translated_appendix.insert_font(fontname="cjkbody", fontfile=str(body_font))
    translated_appendix.insert_text(
        (30, 35), "A 在 DIAMOND 中采样观测", fontsize=12, fontname="cjkbody"
    )
    translated_appendix.insert_textbox(
        fitz.Rect(30, 125, 330, 220),
        (
            "我们描述如何从扩散世界模型中采样观测，并保留学习到的评分与连续过程，"
            "以获得稳定预测 (2020) (2021) (2022) (2023)。"
        ),
        fontsize=9,
        fontname="cjkbody",
    )

    original_path = tmp_path / "reference-then-appendix.pdf"
    translated_path = tmp_path / "reference-then-appendix-zh.pdf"
    original.save(original_path)
    translated.save(translated_path)
    translated.close()
    original.close()

    heading_role = TextBlock(
        page_index=1,
        bbox=(30.0, 20.0, 250.0, 40.0),
        text="A Sampling observations in DIAMOND",
        font_size=12.0,
        color=(0.0, 0.0, 0.0),
        block_type="heading",
        preserve_position=True,
    )

    def fake_prepare(*_args, **_kwargs):
        return [(heading_role, "", {})], [], []

    monkeypatch.setattr(
        "pdf_zh_translator.pdf_layout.prepare_translation_units",
        fake_prepare,
    )

    issues = inspect_translation(original_path, translated_path)

    assert not [issue for issue in issues if issue.code == "reference_content_changed"]


def test_reference_continuation_can_end_per_column_on_mixed_appendix_page(
    tmp_path, monkeypatch
):
    from pdf_zh_translator.pdf_layout import TextBlock, ensure_font_pack_files

    body_font, _bold_font = ensure_font_pack_files([])
    assert body_font is not None
    references = (
        "Smith, J. Reliable world models. Proceedings of Learning Systems, 2024. "
        "Doe, A. Diffusion planning. Journal of Machine Learning, 2023. "
        "Lee, K. Scene synthesis from images. Computer Vision Review, 2022. "
        "Wu, T. Rasterized Gaussian models. Graphics Research, 2021."
    )

    original = fitz.open()
    original_reference = original.new_page(width=400, height=260)
    original_reference.insert_text((20, 25), "References", fontsize=12)
    original_reference.insert_textbox(
        fitz.Rect(20, 45, 380, 230), references, fontsize=9
    )
    original_mixed = original.new_page(width=400, height=260)
    original_mixed.insert_textbox(
        fitz.Rect(20, 25, 185, 135), references, fontsize=8
    )
    original_mixed.insert_text((20, 160), "A Gradient details", fontsize=11)
    original_mixed.insert_textbox(
        fitz.Rect(20, 175, 185, 235),
        "We derive the covariance gradients used by the renderer.",
        fontsize=9,
    )
    original_mixed.insert_text((215, 35), "B Optimization", fontsize=11)
    original_mixed.insert_textbox(
        fitz.Rect(215, 50, 380, 120),
        "The optimization schedule alternates densification and pruning.",
        fontsize=9,
    )

    translated = fitz.open()
    translated_reference = translated.new_page(width=400, height=260)
    translated_reference.insert_text((20, 25), "References", fontsize=12)
    translated_reference.insert_textbox(
        fitz.Rect(20, 45, 380, 230), references, fontsize=9
    )
    translated_mixed = translated.new_page(width=400, height=260)
    translated_mixed.insert_font(fontname="cjkbody", fontfile=str(body_font))
    translated_mixed.insert_textbox(
        fitz.Rect(20, 25, 185, 135), references, fontsize=8
    )
    translated_mixed.insert_text(
        (20, 160), "A 梯度细节", fontsize=11, fontname="cjkbody"
    )
    translated_mixed.insert_textbox(
        fitz.Rect(20, 175, 185, 235),
        "我们推导渲染器使用的协方差梯度。",
        fontsize=9,
        fontname="cjkbody",
    )
    translated_mixed.insert_text(
        (215, 35), "B 优化", fontsize=11, fontname="cjkbody"
    )
    translated_mixed.insert_textbox(
        fitz.Rect(215, 50, 380, 120),
        "优化日程交替执行致密化与剪枝。",
        fontsize=9,
        fontname="cjkbody",
    )

    original_path = tmp_path / "references-mixed-appendix.pdf"
    translated_path = tmp_path / "references-mixed-appendix-zh.pdf"
    original.save(original_path)
    translated.save(translated_path)
    translated.close()
    original.close()

    headings = [
        TextBlock(
            page_index=1,
            bbox=(20.0, 149.0, 180.0, 162.0),
            text="A Gradient details",
            font_size=11.0,
            color=(0.0, 0.0, 0.0),
            block_type="heading",
            preserve_position=True,
        ),
        TextBlock(
            page_index=1,
            bbox=(215.0, 24.0, 380.0, 37.0),
            text="B Optimization",
            font_size=11.0,
            color=(0.0, 0.0, 0.0),
            block_type="heading",
            preserve_position=True,
        ),
    ]

    def fake_prepare(*_args, **_kwargs):
        return [(heading, "", {}) for heading in headings], [], []

    monkeypatch.setattr(
        "pdf_zh_translator.pdf_layout.prepare_translation_units",
        fake_prepare,
    )

    issues = inspect_translation(original_path, translated_path)

    assert not [issue for issue in issues if issue.code == "untranslated_block"]
    assert not [issue for issue in issues if issue.code == "reference_content_changed"]


def test_font_size_pairs_against_source_semantic_role_before_raw_pdf_block():
    original = fitz.open()
    original_page = original.new_page(width=300, height=200)
    original_page.insert_text((40, 60), "Table 2: compact caption", fontsize=9)
    original_page.insert_text((40, 100), "Large source body block", fontsize=10)
    translated = fitz.open()
    translated_page = translated.new_page(width=300, height=200)
    translated_page.insert_text((40, 60), "表2：紧凑图注内容", fontsize=8.3)
    for y in (100, 120, 140):
        translated_page.insert_text((40, y), "普通中文正文内容足够长", fontsize=9.2)
    caption_role = SimpleNamespace(
        bbox=(40.0, 45.0, 180.0, 65.0),
        font_size=9.0,
    )

    issues = _font_size_issues(
        original_page,
        translated_page,
        1,
        exclusion_bboxes=(),
        source_role_blocks=(caption_role,),
    )

    assert not [issue for issue in issues if issue.code == "font_size_drift"]
    translated.close()
    original.close()


def test_font_size_cohort_cannot_be_stricter_than_page_scale():
    original = fitz.open()
    original_page = original.new_page(width=300, height=360)
    translated = fitz.open()
    translated_page = translated.new_page(width=300, height=360)
    roles = []
    expected_sizes = (10.0, 10.0, 10.0, 10.0, 12.0, 12.0, 12.0)
    translated_sizes = (9.2, 9.2, 11.0, 11.0, 11.04, 11.04, 11.04)
    for index, (y, expected, actual) in enumerate(
        zip((40, 80, 120, 160, 200, 240, 280), expected_sizes, translated_sizes)
    ):
        original_page.insert_text(
            (30, y),
            f"Source paragraph number {index}",
            fontsize=expected,
        )
        translated_page.insert_text(
            (30, y),
            "正常中文段落内容用于字号一致性检测",
            fontsize=actual,
            fontname="china-ss",
        )
        roles.append(
            SimpleNamespace(
                bbox=(30.0, y - 13.0, 270.0, y + 4.0),
                font_size=expected,
            )
        )

    issues = _font_size_issues(
        original_page,
        translated_page,
        1,
        exclusion_bboxes=(),
        source_role_blocks=roles,
    )

    assert not [issue for issue in issues if issue.code == "font_size_drift"]
    translated.close()
    original.close()


def _codes(stem: str) -> Counter:
    issues = inspect_translation(
        FIXTURES / f"{stem}.pdf",
        FIXTURES / f"{stem}_translated.pdf",
    )
    return Counter(issue.code for issue in issues)


class TestProductionRegressions:
    def test_contribution_bullets_font_drift(self):
        codes = _codes("otf_p2_font_drift")
        assert codes["font_size_drift"] >= 2
        assert codes["list_font_inconsistent"] >= 1

    def test_inline_formula_sprites_clipped(self):
        codes = _codes("otf_p4_formula_clip")
        assert codes["formula_clipped"] >= 2

    def test_table_header_grid_mismatch(self):
        # First-round production p9: the caption redaction fill painted over
        # the Table 2 toprule (ink coverage 1.00 -> 0.24). The vector rule
        # object survives, so only the pixel-coverage comparison catches it.
        codes = _codes("otf_p9_table_grid")
        assert codes["table_structure_mismatch"] >= 1

    def test_reference_bold_overprint(self):
        codes = _codes("otf_p11_12_refs")
        assert codes["reference_overlap"] >= 1
        assert codes["reference_bold_style"] >= 1

    def test_display_equation_alignment(self, tmp_path):
        original_path = FIXTURES / "otf_p14_display_align.pdf"
        shifted_path = tmp_path / "otf-p14-formula-shifted.pdf"
        formula = fitz.Rect(211.8, 395.9, 341.4, 406.3)

        with fitz.open(original_path) as original:
            formula_pixmap = original[0].get_pixmap(
                matrix=fitz.Matrix(4.0, 4.0),
                clip=formula,
                alpha=False,
            )
            shifted = fitz.open()
            shifted.insert_pdf(original)
        page = shifted[0]
        page.add_redact_annot(formula, fill=(1.0, 1.0, 1.0))
        page.apply_redactions(
            images=fitz.PDF_REDACT_IMAGE_NONE,
            graphics=fitz.PDF_REDACT_LINE_ART_NONE,
        )
        page.insert_image(
            fitz.Rect(formula.x0 + 36.0, formula.y0, formula.x1 + 36.0, formula.y1),
            pixmap=formula_pixmap,
            overlay=True,
        )
        shifted.save(shifted_path)
        shifted.close()

        codes = Counter(
            issue.code for issue in inspect_translation(original_path, shifted_path)
        )
        assert codes["display_formula_misaligned"] >= 1

        from pdf_zh_translator.page_inspector import (
            _display_alignment_issues,
            _InkCache,
        )

        with fitz.open(original_path) as original, fitz.open(shifted_path) as translated:
            original_ink = _InkCache(original[0])
            translated_ink = _InkCache(translated[0])
            assert not _display_alignment_issues(
                original_ink,
                translated_ink,
                1,
                [tuple(formula)],
                algorithm_regions=[tuple(formula)],
            )
            assert not _display_alignment_issues(
                original_ink,
                translated_ink,
                1,
                [tuple(formula)],
                table_regions=[tuple(formula)],
            )
            assert not _display_alignment_issues(
                original_ink,
                translated_ink,
                1,
                [tuple(formula)],
                source_role_blocks=[
                    SimpleNamespace(bbox=tuple(formula), formula_anchors=(formula,))
                ],
            )
            crossing_anchor = (
                formula.x0 + 10.0,
                formula.y0 - 2.0,
                formula.x0 + 24.0,
                formula.y0 + 4.0,
            )
            assert not _display_alignment_issues(
                original_ink,
                translated_ink,
                1,
                [tuple(formula)],
                source_role_blocks=[
                    SimpleNamespace(
                        bbox=(
                            formula.x0 - 80.0,
                            formula.y0 - 24.0,
                            formula.x1,
                            formula.y0 + 4.0,
                        ),
                        formula_anchors=(crossing_anchor,),
                        flow_inline_math=False,
                        keepout_formula_atom_groups=(),
                    )
                ],
            )
            assert not _display_alignment_issues(
                original_ink,
                translated_ink,
                1,
                [tuple(formula)],
                source_role_blocks=[
                    SimpleNamespace(
                        bbox=(formula.x0 - 80.0, formula.y0, formula.x0, formula.y1),
                        block_type="formula_prose",
                        formula_anchors=(),
                        flow_inline_math=True,
                        preserve_position=True,
                        keepout_bboxes=(tuple(formula),),
                        redaction_formula_restore_groups=((tuple(formula),),),
                    )
                ],
            )
            author_row = SimpleNamespace(
                bbox=tuple(formula),
                text=(
                    "Pamela Mishkin^{∗} Chong Zhang Sandhini Agarwal "
                    "Katarina Slama Alex Ray"
                ),
                block_type="body",
                formula_anchors=(),
                flow_inline_math=False,
                preserve_position=False,
                keepout_formula_atom_groups=(),
                redaction_formula_restore_groups=(),
            )
            assert not _display_alignment_issues(
                original_ink,
                translated_ink,
                1,
                [tuple(formula)],
                source_role_blocks=[author_row],
            )
            assert _display_alignment_issues(
                original_ink,
                translated_ink,
                2,
                [tuple(formula)],
                source_role_blocks=[author_row],
            )

    def test_inline_formula_keepout_group_is_not_scored_as_fixed_display(self):
        from pdf_zh_translator.page_inspector import (
            _display_alignment_issues,
            _InkCache,
        )

        original = fitz.open()
        original_page = original.new_page(width=300, height=160)
        original_page.insert_text((90.0, 60.0), "x = y", fontsize=12.0)
        translated = fitz.open()
        translated_page = translated.new_page(width=300, height=160)
        translated_page.insert_text((155.0, 60.0), "x = y", fontsize=12.0)
        equation_row = (60.0, 42.0, 230.0, 66.0)
        formula_atoms = ((86.0, 44.0, 126.0, 64.0),)

        issues = _display_alignment_issues(
            _InkCache(original_page),
            _InkCache(translated_page),
            1,
            [equation_row],
            source_role_blocks=[
                SimpleNamespace(
                    bbox=(40.0, 40.0, 260.0, 72.0),
                    block_type="body",
                    formula_anchors=(),
                    flow_inline_math=True,
                    preserve_position=False,
                    keepout_formula_atom_groups=(formula_atoms,),
                    redaction_formula_restore_groups=(),
                )
            ],
        )

        translated.close()
        original.close()
        assert issues == []

    def test_clean_page_stays_clean(self):
        codes = _codes("otf_p1_clean")
        assert not set(codes) & INSPECTOR_ISSUE_CODES


class TestUntranslatedBlock:
    def test_short_formula_explanation_left_in_english_is_flagged(self):
        raw_page = {
            "blocks": [
                {
                    "type": 0,
                    "bbox": (105.0, 639.4, 180.2, 653.1),
                    "lines": [
                        {
                            "spans": [
                                {
                                    "text": "SGD, where ",
                                    "font": "Times-Roman",
                                    "size": 9.7,
                                    "bbox": (105.0, 640.3, 152.4, 652.0),
                                },
                                {
                                    "text": "bar theta_t = 1",
                                    "font": "CMMI10",
                                    "size": 9.7,
                                    "bbox": (154.3, 639.4, 180.2, 653.1),
                                },
                            ]
                        }
                    ],
                }
            ]
        }
        page = SimpleNamespace(get_text=lambda _kind: raw_page)

        issues = _untranslated_block_issues(
            page,
            9,
            reference_y=None,
        )

        assert [issue for issue in issues if issue.code == "untranslated_block"]

    @staticmethod
    def _build_pair(tmp_path: Path) -> tuple[Path, Path]:
        english = (
            "The proposed method achieves strong results on all benchmark "
            "suites and clearly outperforms every baseline model in the "
            "distribution shift setting across seven perturbation axes."
        )
        original = tmp_path / "original.pdf"
        translated = tmp_path / "translated.pdf"

        document = fitz.open()
        page = document.new_page(width=595, height=842)
        page.insert_textbox(fitz.Rect(72, 90, 520, 200), english, fontsize=10)
        page.insert_textbox(
            fitz.Rect(72, 220, 520, 330),
            english.replace("proposed", "presented"),
            fontsize=10,
        )
        document.save(original)
        document.close()

        document = fitz.open()
        page = document.new_page(width=595, height=842)
        page.insert_textbox(
            fitz.Rect(72, 90, 520, 200),
            # "The presented method achieves excellent results on all
            # benchmark suites" in Chinese (escaped to keep the source ASCII).
            "\u672c\u6587\u63d0\u51fa\u7684\u65b9\u6cd5\u5728\u6240\u6709\u57fa"
            "\u51c6\u5957\u4ef6\u4e0a\u90fd\u53d6\u5f97\u4e86\u4f18\u5f02\u7684"
            "\u7ed3\u679c\uff0c\u5e76\u5728\u5206\u5e03\u504f\u79fb\u8bbe\u7f6e"
            "\u4e2d\u660e\u663e\u4f18\u4e8e\u6bcf\u4e00\u4e2a\u57fa\u7ebf\u6a21"
            "\u578b\uff0c\u8986\u76d6\u4e03\u4e2a\u6270\u52a8\u7ef4\u5ea6\u3002",
            fontsize=10,
            fontname="china-s",
        )
        # Second paragraph left verbatim in English: the defect.
        page.insert_textbox(
            fitz.Rect(72, 220, 520, 330),
            english.replace("proposed", "presented"),
            fontsize=10,
        )
        document.save(translated)
        document.close()
        return original, translated

    def test_untranslated_paragraph_is_flagged(self, tmp_path):
        original, translated = self._build_pair(tmp_path)
        issues = inspect_translation(original, translated)
        codes = Counter(issue.code for issue in issues)
        assert codes["untranslated_block"] >= 1

    def test_citation_rich_untranslated_paragraph_is_flagged(self, tmp_path):
        prose = (
            "We take inspiration from Li et al. (2018) and Aghajanyan et al. "
            "(2020), which show that learned models occupy a low intrinsic "
            "dimension. We therefore propose a low-rank adaptation method."
        )
        original = tmp_path / "original-citations.pdf"
        translated = tmp_path / "translated-citations.pdf"
        for path in (original, translated):
            document = fitz.open()
            page = document.new_page(width=595, height=842)
            page.insert_textbox(fitz.Rect(72, 90, 520, 220), prose, fontsize=10)
            document.save(path)
            document.close()

        issues = inspect_translation(original, translated)

        assert [issue for issue in issues if issue.code == "untranslated_block"]

    def test_preserved_region_does_not_hide_running_prose(self):
        prose = (
            "We further report segmentation results on the benchmark dataset. "
            "The dataset contains fine annotations for every evaluation image "
            "and provides a standard validation split for comparison."
        )
        original = fitz.open()
        original_page = original.new_page(width=595, height=842)
        original_page.insert_textbox(fitz.Rect(72, 90, 520, 220), prose, fontsize=10)
        translated = fitz.open()
        translated_page = translated.new_page(width=595, height=842)
        translated_page.insert_textbox(fitz.Rect(72, 90, 520, 220), prose, fontsize=10)

        issues = _untranslated_block_issues(
            translated_page,
            1,
            original_page=original_page,
            reference_y=None,
            preserved_regions=((65.0, 80.0, 530.0, 230.0),),
        )

        translated.close()
        original.close()
        assert [issue for issue in issues if issue.code == "untranslated_block"]

    def test_preserved_aligned_parameter_rows_are_not_untranslated_prose(self):
        original = fitz.open()
        original_page = original.new_page(width=400, height=300)
        translated = fitz.open()
        translated_page = translated.new_page(width=400, height=300)
        rows = (
            ("Encoder dimension", "256"),
            ("MLP dimension", "512"),
            ("Latent state dimension", "512"),
            ("Task embedding dimension", "96"),
            ("Number of Q functions", "5"),
            ("Number of reward bins", "101"),
        )
        for page in (original_page, translated_page):
            for index, (label, value) in enumerate(rows):
                y = 80 + index * 12
                page.insert_text((60, y), label, fontsize=9)
                page.insert_text((220, y), value, fontsize=9)

        translated_block = _text_blocks(translated_page)[0]
        issues = _untranslated_block_issues(
            translated_page,
            27,
            original_page=original_page,
            reference_y=None,
            preserved_regions=tuple(span.bbox for span in translated_block.spans),
        )

        translated.close()
        original.close()
        assert not [issue for issue in issues if issue.code == "untranslated_block"]

    def test_first_page_author_order_note_is_not_untranslated_prose(self):
        note = (
            "Google DeepMind. Authors listed in alphabetical order, with "
            "contributions listed in Appendix A."
        )
        original = fitz.open()
        original_page = original.new_page(width=595, height=842)
        original_page.insert_text((62, 270), note, fontsize=8)
        translated = fitz.open()
        translated_page = translated.new_page(width=595, height=842)
        translated_page.insert_text((62, 270), note, fontsize=8)

        issues = _untranslated_block_issues(
            translated_page,
            1,
            original_page=original_page,
            reference_y=None,
        )

        translated.close()
        original.close()
        assert not [issue for issue in issues if issue.code == "untranslated_block"]

    def test_table_in_right_column_does_not_hide_left_column_prose(self):
        prose = (
            "In this paper, we propose a compound scaling method that uniformly "
            "scales network width, depth, and resolution in a principled way."
        )
        original = fitz.open()
        original_page = original.new_page(width=595, height=842)
        original_page.insert_textbox(fitz.Rect(50, 100, 285, 190), prose, fontsize=10)
        translated = fitz.open()
        translated_page = translated.new_page(width=595, height=842)
        translated_page.insert_textbox(fitz.Rect(50, 100, 285, 190), prose, fontsize=10)

        issues = _untranslated_block_issues(
            translated_page,
            1,
            original_page=original_page,
            reference_y=None,
            table_bands=((310.0, 80.0, 550.0, 220.0),),
        )

        translated.close()
        original.close()
        assert [issue for issue in issues if issue.code == "untranslated_block"]

    def test_sparse_ruled_table_cell_is_not_untranslated_prose(self, tmp_path):
        prose = (
            "We compare the model with several strong baselines and report "
            "every result using exactly the same evaluation protocol."
        )
        original = tmp_path / "original-sparse-table.pdf"
        translated = tmp_path / "translated-sparse-table.pdf"
        for path in (original, translated):
            document = fitz.open()
            page = document.new_page(width=400, height=300)
            for y in (80, 100, 210, 230):
                page.draw_line((40, y), (360, y), width=0.5)
            for x in (40, 200, 360):
                page.draw_line((x, 80), (x, 230), width=0.5)
            page.insert_textbox(
                fitz.Rect(48, 120, 192, 200),
                prose,
                fontsize=8,
            )
            document.save(path)
            document.close()

        issues = inspect_translation(original, translated)

        assert not [issue for issue in issues if issue.code == "untranslated_block"]

    def test_horizontal_only_table_with_tall_rows_is_not_untranslated_prose(
        self, tmp_path
    ):
        prose = (
            "We compare the model with several strong baselines and report "
            "every result using exactly the same evaluation protocol."
        )
        original = tmp_path / "original-horizontal-table.pdf"
        translated = tmp_path / "translated-horizontal-table.pdf"
        for path in (original, translated):
            document = fitz.open()
            page = document.new_page(width=400, height=320)
            page.insert_text(
                (110, 55),
                "Table 1: Detailed benchmark results for every evaluation task.",
                fontsize=8,
            )
            for y in (70, 90, 205, 230):
                page.draw_line((110, y), (290, y), width=0.5)
            page.insert_textbox(
                fitz.Rect(118, 105, 282, 195),
                prose,
                fontsize=8,
            )
            document.save(path)
            document.close()

        issues = inspect_translation(original, translated)

        assert not [issue for issue in issues if issue.code == "untranslated_block"]

    def test_open_bottom_table_last_row_is_not_untranslated_prose(self, tmp_path):
        prose = (
            "Videos are composed of four different subjects performing seven "
            "types of daily activities with segmentation masks of hands."
        )
        original = tmp_path / "original-open-bottom-table.pdf"
        translated = tmp_path / "translated-open-bottom-table.pdf"
        for path in (original, translated):
            document = fitz.open()
            page = document.new_page(width=400, height=320)
            for y in (70, 90, 205):
                page.draw_line((40, y), (360, y), width=0.5)
            for x in (40, 120, 360):
                page.draw_line((x, 70), (x, 205), width=0.5)
                page.draw_line((x, 205.3), (x, 240), width=0.5)
            page.insert_textbox(
                fitz.Rect(128, 210, 352, 238),
                prose,
                fontsize=8,
            )
            page.insert_text(
                (40, 258),
                "Table 2: Segmentation datasets used for evaluation.",
                fontsize=8,
            )
            document.save(path)
            document.close()

        issues = inspect_translation(original, translated)

        assert not [issue for issue in issues if issue.code == "untranslated_block"]

    @pytest.mark.parametrize(
        "sample",
        [
            (
                "QUESTION: There are thirty six penguins sunbathing in the snow. "
                "One third of them jump into the ocean. How many remain?"
            ),
            (
                "Example for combining two unrelated things: The point indicates "
                "the lizard, but the mask covers the bird as well."
            ),
            (
                "Context → Article: Informal conversation is an important part "
                "of daily life and this dataset example stays verbatim."
            ),
            (
                "Target Completion → The truth is that the model may emit more "
                "than one valid answer for this benchmark example."
            ),
        ],
    )
    def test_preserved_labeled_sample_is_not_untranslated_prose(self, sample):
        original = fitz.open()
        original_page = original.new_page(width=400, height=300)
        original_page.insert_textbox(fitz.Rect(40, 80, 360, 150), sample, fontsize=8)
        translated = fitz.open()
        translated_page = translated.new_page(width=400, height=300)
        translated_page.insert_textbox(
            fitz.Rect(40, 80, 360, 150), sample, fontsize=8
        )

        issues = _untranslated_block_issues(
            translated_page,
            1,
            original_page=original_page,
            reference_y=None,
            preserved_regions=((35.0, 75.0, 365.0, 155.0),),
        )

        translated.close()
        original.close()
        assert not [issue for issue in issues if issue.code == "untranslated_block"]

    def test_formatted_dataset_payload_split_from_label_is_not_untranslated(self):
        payload = (
            "Article: Informal conversation is an important part of daily life, "
            "and the complete benchmark passage remains verbatim in this sample."
        )
        original = fitz.open()
        original_page = original.new_page(width=612, height=792)
        translated = fitz.open()
        translated_page = translated.new_page(width=612, height=792)
        for page in (original_page, translated_page):
            page.draw_line((72, 80), (540, 80), width=0.5)
            page.draw_line((72, 150), (540, 150), width=0.5)
            page.insert_text((78, 104), "Context ->", fontsize=8)
            page.insert_textbox(fitz.Rect(180, 88, 532, 142), payload, fontsize=8)
            page.insert_text(
                (185, 168),
                "Figure G.31: Formatted dataset example for RTE",
                fontsize=8,
            )

        issues = _untranslated_block_issues(
            translated_page,
            1,
            original_page=original_page,
            reference_y=None,
            preserved_regions=((72.0, 80.0, 540.0, 150.0),),
        )

        translated.close()
        original.close()
        assert not [issue for issue in issues if issue.code == "untranslated_block"]

    def test_untranslated_subfigure_caption_near_numbered_caption_is_flagged(
        self, tmp_path
    ):
        panel_caption = (
            "(b) Exploring the impact of the filter ratio on model size and "
            "image classification accuracy across several benchmark settings."
        )
        original = tmp_path / "original-subfigure.pdf"
        translated = tmp_path / "translated-subfigure.pdf"
        for path in (original, translated):
            document = fitz.open()
            page = document.new_page(width=612, height=792)
            page.draw_rect(fitz.Rect(60, 60, 550, 260))
            page.insert_textbox(
                fitz.Rect(310, 195, 535, 250),
                panel_caption,
                fontsize=8,
            )
            page.insert_text(
                (190, 275),
                "Figure 3: Microarchitectural design space exploration.",
                fontsize=8,
            )
            document.save(path)
            document.close()

        issues = inspect_translation(original, translated)

        assert [issue for issue in issues if issue.code == "untranslated_block"]

    def test_verbatim_figure_panel_text_is_not_flagged(self, tmp_path):
        label = (
            "(c) 1x1 Convolutional Filters called Pointwise Convolution "
            "in the context of Depthwise Separable Convolution"
        )
        original = tmp_path / "original-figure.pdf"
        translated = tmp_path / "translated-figure.pdf"
        for path in (original, translated):
            document = fitz.open()
            page = document.new_page(width=612, height=792)
            shape = page.new_shape()
            shape.draw_rect(fitz.Rect(310, 300, 545, 390))
            shape.finish(color=(0, 0, 0))
            shape.commit()
            page.insert_textbox(
                fitz.Rect(313, 360, 541, 410),
                label,
                fontsize=8,
            )
            document.save(path)
            document.close()

        issues = inspect_translation(original, translated)

        assert not [issue for issue in issues if issue.code == "untranslated_block"]

    def test_composite_figure_gap_text_is_not_untranslated_prose(self, tmp_path):
        original = tmp_path / "original-composite-figure.pdf"
        translated = tmp_path / "translated-composite-figure.pdf"
        image = b"P6\n16 4\n255\n" + bytes([96, 96, 96]) * 64
        panel_text = (
            "Overall mask quality is subjective, and each reviewer must score "
            "the visible object using the complete annotation guidelines."
        )
        for path in (original, translated):
            document = fitz.open()
            page = document.new_page(width=400, height=500)
            for top in (40, 110, 180, 250, 320):
                page.insert_image(
                    fitz.Rect(40, top, 360, top + 40),
                    stream=image,
                )
            page.insert_textbox(
                fitz.Rect(48, 82, 352, 106),
                panel_text,
                fontsize=7,
            )
            page.insert_text(
                (40, 382),
                "Figure 4: Complete annotation guide with visual examples.",
                fontsize=8,
            )
            document.save(path)
            document.close()

        issues = inspect_translation(original, translated)

        assert not [issue for issue in issues if issue.code == "untranslated_block"]

    def test_figure_text_block_may_span_adjacent_graphic_regions(self):
        prose = (
            "Document one contains classic American literature and describes "
            "the author. Document two contains another retrieved passage and "
            "describes the novel in detail."
        )
        original = fitz.open()
        original_page = original.new_page(width=400, height=300)
        original_page.insert_textbox(fitz.Rect(40, 80, 260, 160), prose, fontsize=8)
        translated = fitz.open()
        translated_page = translated.new_page(width=400, height=300)
        translated_page.insert_textbox(
            fitz.Rect(40, 80, 260, 160), prose, fontsize=8
        )
        block = _text_blocks(translated_page)[0]
        split = (block.bbox[1] + block.bbox[3]) / 2.0

        issues = _untranslated_block_issues(
            translated_page,
            1,
            original_page=original_page,
            reference_y=None,
            graphic_regions=(
                (block.bbox[0], block.bbox[1], block.bbox[2], split),
                (block.bbox[0], split, block.bbox[2], block.bbox[3]),
            ),
        )

        translated.close()
        original.close()
        assert not [issue for issue in issues if issue.code == "untranslated_block"]

    def test_labeled_generated_poem_is_not_flagged(self, tmp_path):
        original = tmp_path / "original-poem.pdf"
        translated = tmp_path / "translated-poem.pdf"
        poem = (
            "The sun was all we had. All is changed. White fields remain. "
            "Ancient gleams surround the roots. The great dark books of reverie "
            "follow the labyrinth of the sea."
        )
        for path in (original, translated):
            document = fitz.open()
            page = document.new_page(width=420, height=520)
            page.insert_text((50, 70), "-------- Generated Poem 1 --------")
            page.insert_textbox(fitz.Rect(50, 85, 240, 300), poem, fontsize=9)
            document.save(path)
            document.close()

        issues = inspect_translation(original, translated)

        assert not [issue for issue in issues if issue.code == "untranslated_block"]


class TestHelpers:
    def test_line_table_bbox_detects_captioned_two_rule_table(self):
        document = fitz.open()
        page = document.new_page(width=400, height=300)
        page.insert_text((40, 60), "Table 25: Few-shot prompt exemplars", fontsize=9)
        page.draw_line((40, 70), (360, 70), width=0.5)
        page.insert_text((45, 95), "Prompt for StrategyQA", fontsize=9)
        page.insert_text((45, 125), "Question: Do hamsters provide food?", fontsize=9)
        page.insert_text((45, 155), "Answer: Hamsters are prey animals.", fontsize=9)
        page.draw_line((40, 240), (360, 240), width=0.5)

        assert _line_table_bboxes(page) == [(40.0, 70.0, 360.0, 240.0)]

        document.close()

    def test_line_table_bbox_extends_through_open_bottom_row(self):
        class FakePage:
            rect = SimpleNamespace(width=400.0, height=300.0)

            @staticmethod
            def find_tables(*, strategy):
                assert strategy == "lines"
                return SimpleNamespace(
                    tables=[
                        SimpleNamespace(
                            row_count=8,
                            col_count=4,
                            bbox=(40.0, 70.0, 360.0, 205.0),
                        )
                    ]
                )

            @staticmethod
            def get_drawings():
                return [
                    {
                        "items": [
                            ("l", fitz.Point(x, 205.2), fitz.Point(x, 240.0))
                            for x in (120.0, 200.0, 280.0)
                        ]
                    }
                ]

        assert _line_table_bboxes(FakePage()) == [(40.0, 70.0, 360.0, 240.0)]

    def test_dominant_size_uses_cjk_body_not_hidden_formula_copy(self):
        from pdf_zh_translator.page_inspector import _Block, _Span

        block = _Block(
            bbox=(100.0, 100.0, 500.0, 120.0),
            spans=(
                _Span(
                    text="z=(z_{1},...,z_{n})",
                    font="STSongti-SC-Regular",
                    size=8.08,
                    bbox=(100.0, 100.0, 220.0, 120.0),
                ),
                _Span(
                    text="给定表示后，解码器生成下一个输出元素",
                    font="STSongti-SC-Regular",
                    size=9.17,
                    bbox=(220.0, 100.0, 500.0, 120.0),
                ),
            ),
        )

        assert block.dominant_size() == 9.17

    def test_formula_mask_ignores_foreign_ink_at_image_bbox_edge(self, tmp_path):
        def ppm(width: int, height: int) -> bytes:
            header = f"P6\n{width} {height}\n255\n".encode("ascii")
            return header + bytes([0, 0, 0]) * (width * height)

        def clean_formula_mask(width: int, height: int) -> bytes:
            pixels = bytearray(width * height)
            for y in range(3, height - 3):
                for x in range(4, width - 4):
                    if y in (4, height - 5) or x in (5, width - 6):
                        pixels[y * width + x] = 255
            header = f"P5\n{width} {height}\n255\n".encode("ascii")
            return header + bytes(pixels)

        original = tmp_path / "original.pdf"
        translated = tmp_path / "translated.pdf"
        document = fitz.open()
        document.new_page(width=300, height=200)
        document.save(original)
        document.close()

        document = fitz.open()
        page = document.new_page(width=300, height=200)
        image_rect = fitz.Rect(60, 80, 180, 100)
        page.insert_image(
            image_rect,
            stream=ppm(60, 20),
            mask=clean_formula_mask(60, 20),
        )
        # The vector belongs to adjacent content. Page-raster sampling sees it
        # inside the image bbox, but the formula's own alpha mask stays clean.
        page.draw_rect(
            fitz.Rect(70, 80, 105, 81.5),
            color=(0, 0, 0),
            fill=(0, 0, 0),
        )
        document.save(translated)
        document.close()

        issues = inspect_translation(original, translated)
        assert not [issue for issue in issues if issue.code == "formula_clipped"]

    def test_marginal_formula_edge_ink_is_reported_as_warning(self, tmp_path):
        width, height = 60, 20
        rgb = f"P6\n{width} {height}\n255\n".encode("ascii") + bytes(
            [0, 0, 0]
        ) * (width * height)
        pixels = bytearray(width * height)
        for y in (0, 1):
            for x in range(5, 9):
                pixels[y * width + x] = 255
        for y in range(2, height - 3):
            for x in range(5, width - 5):
                if x == 5 or y == 2:
                    pixels[y * width + x] = 255
        mask = f"P5\n{width} {height}\n255\n".encode("ascii") + bytes(pixels)
        original = tmp_path / "original.pdf"
        translated = tmp_path / "translated.pdf"
        document = fitz.open()
        document.new_page(width=300, height=200)
        document.save(original)
        document.close()
        document = fitz.open()
        page = document.new_page(width=300, height=200)
        page.insert_image(fitz.Rect(60, 80, 180, 100), stream=rgb, mask=mask)
        document.save(translated)
        document.close()

        issues = [
            issue
            for issue in inspect_translation(original, translated)
            if issue.code == "formula_clipped"
        ]
        assert len(issues) == 1
        assert issues[0].severity == "warning"

    def test_fully_transparent_formula_sprite_is_an_error(self, tmp_path):
        width, height = 60, 20
        rgb = f"P6\n{width} {height}\n255\n".encode("ascii") + bytes(
            [0, 0, 0]
        ) * (width * height)
        mask = f"P5\n{width} {height}\n255\n".encode("ascii") + bytes(
            width * height
        )
        original = tmp_path / "original.pdf"
        translated = tmp_path / "translated.pdf"
        document = fitz.open()
        document.new_page(width=300, height=200)
        document.save(original)
        document.close()
        document = fitz.open()
        page = document.new_page(width=300, height=200)
        page.insert_image(fitz.Rect(60, 80, 180, 100), stream=rgb, mask=mask)
        document.save(translated)
        document.close()

        issues = [
            issue
            for issue in inspect_translation(original, translated)
            if issue.code == "formula_visible_ink_mismatch"
        ]
        assert len(issues) == 1
        assert issues[0].severity == "error"

    def test_edge_cut_needs_partial_ink_on_both_rows(self):
        assert _edge_cut([0.2, 0.1, 0.0, 0.0], 0, 1)
        assert not _edge_cut([0.0, 0.4, 0.0, 0.0], 0, 1)  # clean outer row
        assert not _edge_cut([0.99, 0.99, 0.0, 0.0], 0, 1)  # rule, not a glyph
        assert not _edge_cut([0.01, 0.2, 0.0, 0.0], 0, 1)  # noise

    def test_mask_coverage_identical_and_disjoint(self):
        mask = {(x, y) for x in range(6) for y in range(6)}
        assert _mask_coverage(mask, set(mask), 96, 48) == 1.0
        shifted = {(x + 20, y + 20) for x, y in mask}
        assert _mask_coverage(mask, shifted, 96, 48) < 0.2

    def test_rule_clusters_group_stacked_rules(self):
        rules = [
            (100.0, 100.0, 500.0),
            (118.0, 100.0, 500.0),
            (140.0, 100.0, 500.0),
            (162.0, 100.0, 500.0),
            # isolated rule far below: not a table
            (700.0, 100.0, 500.0),
        ]
        clusters = _rule_clusters(rules)
        assert len(clusters) == 1
        assert len(clusters[0]) == 4

    def test_graphic_envelope_exempts_plot_rules_but_not_neighbor_table(self):
        plot_rules = [
            (100.0, 40.0, 220.0),
            (120.0, 40.0, 220.0),
            (140.0, 40.0, 220.0),
        ]
        table_rules = [
            (300.0, 40.0, 260.0),
            (320.0, 40.0, 260.0),
            (340.0, 40.0, 260.0),
        ]
        translated = plot_rules + table_rules[:2]

        issues = _table_structure_issues(
            1,
            plot_rules + table_rules,
            [],
            translated,
            graphic_regions=[(30.0, 90.0, 230.0, 150.0)],
        )

        assert len(issues) == 1
        assert "y=300" in issues[0].message
        assert "2 rules vs 3" in issues[0].message

    def test_identical_adjacent_table_rules_do_not_cross_match(self):
        rules = [
            (106.2, 127.2, 282.9),
            (111.2, 310.5, 503.4),
            (121.5, 127.2, 282.9),
            (126.5, 310.5, 503.4),
            (171.7, 310.5, 503.4),
            (176.7, 127.2, 282.9),
            (214.4, 108.0, 515.6),
            (239.7, 108.0, 515.6),
            (264.8, 108.0, 515.6),
            (290.0, 108.0, 515.6),
            (315.3, 108.0, 515.6),
        ]

        issues = _table_structure_issues(8, rules, [], list(rules))

        assert not issues

    def test_figure_caption_pairs_with_slightly_overlapping_graphic_envelope(self):
        region = (56.0, 80.9, 558.5, 720.1)
        # Inspector expands caption bands by 2pt before pairing.
        caption = (224.0, 711.5, 388.0, 727.5)

        assert _figure_graphic_regions([region], [(caption, "figure")]) == [region]
        assert _figure_graphic_regions([region], [(caption, "table")]) == []

    def test_preserved_figure_regions_do_not_extend_below_caption(self):
        from pdf_zh_translator.page_inspector import (
            _preserved_regions_above_figure_captions,
        )

        caption = (80.0, 100.0, 320.0, 120.0)
        above = (60.0, 40.0, 340.0, 92.0)
        body_formula_keepout = (170.0, 132.0, 240.0, 144.0)

        assert _preserved_regions_above_figure_captions(
            [above, body_formula_keepout],
            [(caption, "figure")],
        ) == [above]

    @pytest.mark.parametrize(
        "text",
        ["\u2022 ???", "1) ??", "(2) ??", "- item", "iv) ??"],
    )
    def test_list_marker_matches(self, text):
        assert _LIST_MARKER_RE.match(text)

    def test_list_marker_rejects_plain_prose(self):
        # "This paper proposes a new method" in Chinese, no list marker.
        assert not _LIST_MARKER_RE.match(
            "\u672c\u6587\u63d0\u51fa\u4e00\u79cd\u65b0\u65b9\u6cd5"
        )

    def test_example_label_pattern_separates_sample_boxes_from_prose(self):
        from pdf_zh_translator.page_inspector import _EXAMPLE_LABEL_RE

        # Quoted sample boxes: labels survive PyMuPDF's space-less line merge.
        news = (
            "Title: United Methodists Agree to Historic SplitSubtitle: "
            "Those who oppose gay marriage will form their own denomination"
        )
        grammar = (
            "Poor English input: I eated the purple berries. "
            "Good English output: I ate the purple berries."
        )
        assert len(_EXAMPLE_LABEL_RE.findall(news)) >= 2
        assert len(_EXAMPLE_LABEL_RE.findall(grammar)) >= 2
        # Analysis prose, including colon-bearing references, stays flagged.
        prose = (
            "On tasks that involve choosing one correct completion from "
            "several options, as shown in Table 3: we observe larger models "
            "perform better."
        )
        assert len(_EXAMPLE_LABEL_RE.findall(prose)) < 2
