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

import fitz
import pytest

from pdf_zh_translator.page_inspector import (
    _LIST_MARKER_RE,
    INSPECTOR_ISSUE_CODES,
    _edge_cut,
    _mask_coverage,
    _rule_clusters,
    inspect_translation,
)

FIXTURES = Path(__file__).parent / "fixtures"


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
        codes = _codes("otf_p8_table_grid")
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
            "??????????????????????????????????"
            "?????????????????????",
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
        assert not _LIST_MARKER_RE.match("?????????")
