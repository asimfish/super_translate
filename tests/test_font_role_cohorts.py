"""Font QA must compare the source typography role it is replacing."""

from types import SimpleNamespace

import pytest

from pdf_zh_translator import page_inspector as inspector


def role_size_issues(monkeypatch, styles, *, expanded_roles=False):
    """Exercise the real matcher with captured sizes and independent roles."""
    source_blocks = []
    translated_blocks = []
    roles = []
    for index, (expected, actual, role) in enumerate(styles):
        bbox = (40.0, 80.0 + index * 70, 520.0, 110.0 + index * 70)
        source_blocks.append(
            inspector._Block(
                bbox,
                (
                    inspector._Span(
                        "Source text for the typography role", "Regular", expected, bbox
                    ),
                ),
            )
        )
        translated_blocks.append(
            inspector._Block(
                bbox,
                (
                    inspector._Span(
                        "中文内容长度足够进行字体字号角色一致性检测", "Regular", actual, bbox
                    ),
                ),
            )
        )
        role_bbox = (bbox[0] - 5, bbox[1] - 5, bbox[2] + 5, bbox[3] + 5) if expanded_roles else bbox
        roles.append(SimpleNamespace(bbox=role_bbox, font_size=expected, block_type=role))
    original_page = object()
    translated_page = object()
    monkeypatch.setattr(
        inspector,
        "_text_blocks",
        lambda page: source_blocks if page is original_page else translated_blocks,
    )
    return inspector._font_size_issues(
        original_page,
        translated_page,
        1,
        exclusion_bboxes=(),
        source_role_blocks=roles,
    )


def test_intact_nature_title_is_not_shrinkage_when_body_fonts_grow(monkeypatch):
    # Captured Nature Communications p1: the title stays 25.90pt while the
    # 9.9626pt body becomes 11.46pt. It must not inherit the body's 1.15 scale.
    issues = role_size_issues(
        monkeypatch,
        [(25.903, 25.90, "title")] + [(9.9626, 11.46, "body")] * 4,
    )
    assert not [issue for issue in issues if issue.code == "font_size_drift"]


@pytest.mark.parametrize("expanded_roles", [False, True])
def test_same_source_size_different_roles_do_not_share_growth_cohort(monkeypatch, expanded_roles):
    # A more precise raw PDF block must not erase its matched semantic role.
    issues = role_size_issues(
        monkeypatch,
        [(12.0, 12.0, "title")] + [(12.0, 13.8, "body")] * 4,
        expanded_roles=expanded_roles,
    )
    assert not [issue for issue in issues if issue.code == "font_size_drift"]


def test_smaller_body_outlier_is_still_detected_in_its_own_cohort(monkeypatch):
    issues = role_size_issues(
        monkeypatch,
        [(25.9, 25.9, "title"), (10.0, 7.0, "body")] + [(10.0, 11.46, "body")] * 4,
    )
    drift = [issue for issue in issues if issue.code == "font_size_drift"]
    assert len(drift) == 1 and "7.00pt" in drift[0].message


def test_singleton_crushed_title_is_not_exempt_from_source_expectation(monkeypatch):
    issues = role_size_issues(
        monkeypatch,
        [(25.9, 18.0, "title")] + [(10.0, 11.46, "body")] * 4,
    )
    assert any(issue.code == "font_size_drift" and "18.00pt" in issue.message for issue in issues)


def test_singleton_small_figure_text_drift_is_not_hidden_by_title_fix(monkeypatch):
    # Captured p9: this figure/legend is actually crowded, unlike the intact
    # p1 title. Its 6.28/7.11 ratio remains below the unchanged tolerance.
    issues = role_size_issues(
        monkeypatch,
        [(7.11, 6.28, "figure_label")] + [(10.0, 11.46, "body")] * 4,
    )
    assert any(issue.code == "font_size_drift" and "6.28pt" in issue.message for issue in issues)


def test_entire_crushed_body_cohort_is_still_rejected(monkeypatch):
    issues = role_size_issues(monkeypatch, [(10.0, 6.5, "body")] * 4)
    assert any(issue.code == "font_size_drift" for issue in issues)


def test_healthy_page_scale_keeps_original_tolerance(monkeypatch):
    issues = role_size_issues(
        monkeypatch,
        [(9.0, 8.3, "caption")] + [(10.0, 9.2, "body")] * 4,
    )
    assert not [issue for issue in issues if issue.code == "font_size_drift"]
    assert inspector._SIZE_RATIO_TOLERANCE == 0.075
