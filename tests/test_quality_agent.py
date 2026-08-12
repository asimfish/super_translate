from types import SimpleNamespace

import pytest

from app.services.quality_agent import (
    QualityAction,
    create_quality_agent,
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
