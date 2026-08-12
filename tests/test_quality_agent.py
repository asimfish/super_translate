from types import SimpleNamespace

import pytest

from app.services.quality_agent import (
    QualityAction,
    create_quality_agent,
    issue_fingerprint,
)


def issue(code: str, severity: str = "error") -> SimpleNamespace:
    return SimpleNamespace(code=code, severity=severity)


@pytest.mark.parametrize(
    ("issues", "expected"),
    [
        ([], QualityAction.ACCEPT),
        ([issue("text_overlap", "warning")], QualityAction.REPAIR_LAYOUT),
        ([issue("untranslated_block")], QualityAction.RETRANSLATE),
        ([issue("untranslated_natural_language")], QualityAction.RETRANSLATE),
        ([issue("formula_changed")], QualityAction.STOP),
        (
            [issue("untranslated_block"), issue("text_overlap", "warning")],
            QualityAction.REPAIR_LAYOUT,
        ),
    ],
)
def test_deterministic_quality_agent_plans_typed_action(issues, expected):
    plan = create_quality_agent("deterministic").plan(issues)
    assert plan.action is expected
    assert plan.issue_codes == tuple(sorted({item.code for item in issues}))


def test_quality_agent_rejects_unknown_implementation():
    with pytest.raises(KeyError):
        create_quality_agent("llm-mutates-pdf")


def test_issue_fingerprint_is_order_independent_and_detects_page_change():
    page_1 = issue("text_overlap")
    page_1.page = 1
    page_1.message = "overlap at x=10"
    page_2 = issue("formula_changed")
    page_2.page = 2
    page_2.message = "formula changed"

    original = issue_fingerprint([page_1, page_2])
    assert original == issue_fingerprint([page_2, page_1])

    page_2.page = 3
    assert original != issue_fingerprint([page_1, page_2])
