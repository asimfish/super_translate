"""Deterministic repair planning for the independent translation QA loop."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from enum import Enum
from typing import Callable, Protocol, Sequence


class QualityAction(str, Enum):
    ACCEPT = "accept"
    REPAIR_LAYOUT = "repair_layout"
    RETRANSLATE = "retranslate"
    STOP = "stop"


@dataclass(frozen=True)
class QualityPlan:
    action: QualityAction
    issue_codes: tuple[str, ...]
    reason: str


class QualityAgent(Protocol):
    def plan(self, issues: Sequence[object]) -> QualityPlan: ...


LAYOUT_REPAIR_ISSUE_CODES = frozenset({"caption_overlap", "text_overlap"})
RETRANSLATABLE_ISSUE_CODES = frozenset(
    {
        "untranslated_english",
        "untranslated_caption",
        "untranslated_formula_explanation",
        "untranslated_natural_language",
        "untranslated_block",
    }
)


def has_retranslatable_error(issues: Sequence[object]) -> bool:
    return any(
        getattr(issue, "severity", "warning") == "error"
        and getattr(issue, "code", "") in RETRANSLATABLE_ISSUE_CODES
        for issue in issues
    )


def issue_fingerprint(issues: Sequence[object]) -> str:
    """Return a stable identity for one detector result, independent of ordering."""
    rows = sorted(
        (
            str(getattr(issue, "severity", "warning")),
            str(getattr(issue, "code", "unknown")),
            int(getattr(issue, "page", 0) or 0),
            str(getattr(issue, "message", "")),
        )
        for issue in issues
    )
    payload = "\n".join("\t".join(map(str, row)) for row in rows)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]


class DeterministicQualityAgent:
    """Map validated issue codes to the only safe next pipeline action."""

    def plan(self, issues: Sequence[object]) -> QualityPlan:
        codes = tuple(sorted({str(getattr(issue, "code", "unknown")) for issue in issues}))
        if not issues:
            return QualityPlan(QualityAction.ACCEPT, codes, "all detectors passed")
        if any(code in LAYOUT_REPAIR_ISSUE_CODES for code in codes):
            return QualityPlan(
                QualityAction.REPAIR_LAYOUT,
                codes,
                "conservative PDF layout fixer supports an overlap issue",
            )
        if has_retranslatable_error(issues):
            return QualityPlan(
                QualityAction.RETRANSLATE,
                codes,
                "translation cache can regenerate untranslated blocks",
            )
        return QualityPlan(
            QualityAction.STOP,
            codes,
            "no registered deterministic repair supports these issues",
        )


_QUALITY_AGENT_FACTORIES: dict[str, Callable[[], QualityAgent]] = {
    "deterministic": DeterministicQualityAgent,
}


def create_quality_agent(name: str = "deterministic") -> QualityAgent:
    try:
        factory = _QUALITY_AGENT_FACTORIES[name]
    except KeyError as exc:
        raise KeyError(
            f"Unknown quality agent {name!r}; available: {sorted(_QUALITY_AGENT_FACTORIES)}"
        ) from exc
    return factory()
