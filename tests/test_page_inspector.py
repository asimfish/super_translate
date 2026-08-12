"""Regression tests for the post-translation visual inspector.

Fixture pages come from the CC BY 4.0 OFT paper (see fixtures README) paired
with its production translation, one page per defect class reported from the
2026-08-11 production review:

- contribution bullets shrunk to 6.4pt (font_size_drift, list_font_inconsistent)
- inline formula sprites with clipped ascenders (formula_clipped)
- table header rules rebuilt at wrong offsets (table_structure_mismatch)
- bold author names overprinting references (reference_overlap, reference_bold_style)
- displayed equation pushed sideways (display_formula_misaligned)
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
    _font_size_issues,
    _mask_coverage,
    _rule_clusters,
    inspect_translation,
)

FIXTURES = Path(__file__).parent / "fixtures"


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

    def test_display_equation_alignment(self):
        codes = _codes("otf_p14_display_align")
        assert codes["display_formula_misaligned"] >= 1

    def test_clean_page_stays_clean(self):
        codes = _codes("otf_p1_clean")
        assert not set(codes) & INSPECTOR_ISSUE_CODES


class TestUntranslatedBlock:
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


class TestHelpers:
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
