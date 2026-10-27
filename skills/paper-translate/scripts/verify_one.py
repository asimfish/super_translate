"""Apply the same full verification policy as classic_benchmark to one PDF pair."""
from __future__ import annotations

import hashlib
import json
import os
import sys
from dataclasses import asdict
from pathlib import Path


def main() -> int:
    root = Path(os.environ.get("SUPER_TRANSLATE_HOME", Path(__file__).resolve().parents[3]))
    sys.path.insert(0, str(root))
    from pdf_zh_translator.pdf_layout import verify_translation_issues
    from pdf_zh_translator.visual_qa import score_visual_layout
    from scripts.classic_benchmark import _is_actionable_warning, _strict_evaluation

    original, translated, report = map(Path, sys.argv[1:])
    issues = verify_translation_issues(original, translated)
    visual = score_visual_layout(original, translated)
    passed = _strict_evaluation(issues, visual.overall_score)
    payload = {
        "strict_pass": passed,
        "source_sha256": hashlib.sha256(original.read_bytes()).hexdigest(),
        "translated_sha256": hashlib.sha256(translated.read_bytes()).hexdigest(),
        "error_count": sum(issue.severity == "error" for issue in issues),
        "actionable_warning_count": sum(_is_actionable_warning(issue) for issue in issues),
        "visual_score": visual.overall_score,
        "issues": [asdict(issue) for issue in issues],
    }
    report.parent.mkdir(parents=True, exist_ok=True)
    report.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({key: value for key, value in payload.items() if key != "issues"}))
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
