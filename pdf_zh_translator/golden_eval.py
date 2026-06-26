"""Golden-set regression evaluation for translated papers."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .pdf_layout import verify_translation_issues
from .visual_qa import score_visual_layout


@dataclass(frozen=True)
class GoldenCase:
    id: str
    original_pdf: Path
    translated_pdf: Path
    min_visual_score: float = 0.55


@dataclass(frozen=True)
class GoldenCaseResult:
    id: str
    passed: bool
    visual_score: float
    issue_count: int
    issues: list[str]
    layout_profile: str = "unknown"
    profile_confidence: float = 0.0
    visual_risk: str = "unknown"
    min_visual_region_score: float = 1.0


@dataclass(frozen=True)
class GoldenEvaluationResult:
    target_cases: int
    evaluated_cases: int
    passed_cases: int
    results: list[GoldenCaseResult]

    @property
    def ready_for_release(self) -> bool:
        return (
            self.evaluated_cases >= self.target_cases
            and self.passed_cases == self.evaluated_cases
        )

    @property
    def profile_summary(self) -> dict[str, int]:
        summary: dict[str, int] = {}
        for result in self.results:
            summary[result.layout_profile] = summary.get(result.layout_profile, 0) + 1
        return summary


def write_manifest_template(path: Path, *, target_cases: int = 100) -> None:
    """Create a manifest template for a 100-paper regression set."""
    path.parent.mkdir(parents=True, exist_ok=True)
    data = {
        "target_cases": target_cases,
        "description": "Populate with real paper pairs before release evaluation.",
        "cases": [],
    }
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def discover_golden_pairs(
    root_dir: Path,
    manifest_path: Path,
    *,
    target_cases: int = 100,
    original_suffix: str = "-original.pdf",
    translated_suffix: str = "-translated.pdf",
    min_visual_score: float = 0.55,
) -> int:
    """Discover original/translated PDF pairs and write a golden manifest.

    Expected file naming:
    ``paper-id-original.pdf`` and ``paper-id-translated.pdf`` in any nested
    directory under ``root_dir``.
    """
    cases = []
    for translated in sorted(root_dir.rglob(f"*{translated_suffix}")):
        prefix = translated.name[: -len(translated_suffix)]
        original = translated.with_name(prefix + original_suffix)
        if not original.exists():
            continue
        case_id = str(translated.relative_to(root_dir).with_suffix(""))
        if case_id.endswith(translated_suffix[:-4]):
            case_id = case_id[: -len(translated_suffix[:-4])]
        profile_name, profile_confidence = _detect_case_profile(original)
        cases.append(
            {
                "id": case_id.replace("/", "__"),
                "original_pdf": os.path.relpath(original, manifest_path.parent),
                "translated_pdf": os.path.relpath(translated, manifest_path.parent),
                "min_visual_score": min_visual_score,
                "layout_profile": profile_name,
                "profile_confidence": profile_confidence,
            }
        )

    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    data = {
        "target_cases": target_cases,
        "description": "Golden regression set discovered from paired PDFs.",
        "cases": cases,
    }
    manifest_path.write_text(
        json.dumps(data, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return len(cases)


def load_golden_manifest(path: Path) -> tuple[int, list[GoldenCase]]:
    data = json.loads(path.read_text(encoding="utf-8"))
    target_cases = int(data.get("target_cases", 100))
    cases = [_parse_case(item, path.parent) for item in data.get("cases", [])]
    return target_cases, cases


def evaluate_golden_set(manifest_path: Path) -> GoldenEvaluationResult:
    target_cases, cases = load_golden_manifest(manifest_path)
    results: list[GoldenCaseResult] = []
    for case in cases:
        issues = verify_translation_issues(case.original_pdf, case.translated_pdf)
        visual = score_visual_layout(case.original_pdf, case.translated_pdf)
        profile_name, profile_confidence = _detect_case_profile(case.original_pdf)
        messages = [issue.message for issue in issues]
        passed = not issues and visual.overall_score >= case.min_visual_score
        results.append(
            GoldenCaseResult(
                id=case.id,
                passed=passed,
                visual_score=visual.overall_score,
                issue_count=len(issues),
                issues=messages,
                layout_profile=profile_name,
                profile_confidence=profile_confidence,
                visual_risk=visual.risk_level,
                min_visual_region_score=visual.min_zone_score,
            )
        )
    return GoldenEvaluationResult(
        target_cases=target_cases,
        evaluated_cases=len(results),
        passed_cases=sum(1 for result in results if result.passed),
        results=results,
    )


def _detect_case_profile(original_pdf: Path) -> tuple[str, float]:
    try:
        import fitz

        from .layout_profiles import detect_layout_profile

        document = fitz.open(str(original_pdf))
        try:
            profile = detect_layout_profile(document)
            return profile.name, profile.confidence
        finally:
            document.close()
    except Exception:
        return "unknown", 0.0


def _parse_case(item: dict[str, Any], base_dir: Path) -> GoldenCase:
    original = Path(str(item["original_pdf"]))
    translated = Path(str(item["translated_pdf"]))
    if not original.is_absolute():
        original = base_dir / original
    if not translated.is_absolute():
        translated = base_dir / translated
    return GoldenCase(
        id=str(item.get("id") or original.stem),
        original_pdf=original,
        translated_pdf=translated,
        min_visual_score=float(item.get("min_visual_score", 0.55)),
    )
