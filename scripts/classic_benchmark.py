"""Classic-paper translation benchmark orchestrator.

Runs the strict quality benchmark over the curated classic paper set
(benchmarks/classic20/manifest.json):

    fetch      download PDFs from arXiv, record license/sha256/metadata
    translate  translate with the native engine (DeepSeek, cached, resumable)
    evaluate   full QA: verify_translation_issues (incl. visual inspector)
               plus render-based visual score, one JSON report per paper
    report     aggregate REPORT.md + showcase.json + page preview images

All artifacts live under data/benchmark/classic20/ (gitignored); the curated
manifest and this script are the reproducible part.

Examples:
    .venv/bin/python scripts/classic_benchmark.py fetch
    .venv/bin/python scripts/classic_benchmark.py translate --only word2vec,adam
    .venv/bin/python scripts/classic_benchmark.py evaluate
    .venv/bin/python scripts/classic_benchmark.py report
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
import time
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

MANIFEST = REPO / "benchmarks" / "classic20" / "manifest.json"
DEFAULT_WORKDIR = REPO / "data" / "benchmark" / "classic20"

_LICENSE_RE = re.compile(
    r"https?://(?:arxiv\.org/licenses|creativecommons\.org/(?:licenses|publicdomain))/[a-z0-9./-]+"
)
_USER_AGENT = "paper-china-benchmark/1.0 (translation quality benchmark)"


@dataclass(frozen=True)
class Entry:
    id: str
    arxiv_id: str
    title: str
    tags: tuple


def _load_entries(only: str | None) -> list[Entry]:
    data = json.loads(MANIFEST.read_text(encoding="utf-8"))
    entries = [
        Entry(
            id=item["id"],
            arxiv_id=item["arxiv_id"],
            title=item["title"],
            tags=tuple(item.get("tags", [])),
        )
        for item in data["papers"]
    ]
    if only:
        wanted = {token.strip() for token in only.split(",") if token.strip()}
        unknown = wanted - {entry.id for entry in entries}
        if unknown:
            raise SystemExit(f"unknown paper ids: {sorted(unknown)}")
        entries = [entry for entry in entries if entry.id in wanted]
    return entries


def _http_get(url: str, *, timeout: int = 120) -> bytes:
    request = urllib.request.Request(url, headers={"User-Agent": _USER_AGENT})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return response.read()


def _paths(workdir: Path) -> dict:
    return {
        "papers": workdir / "papers",
        "meta": workdir / "meta",
        "translations": workdir / "translations",
        "reports": workdir / "reports",
        "previews": workdir / "previews",
    }


# ---------------------------------------------------------------------------
# fetch
# ---------------------------------------------------------------------------


def cmd_fetch(entries: list[Entry], workdir: Path, args) -> int:
    paths = _paths(workdir)
    paths["papers"].mkdir(parents=True, exist_ok=True)
    paths["meta"].mkdir(parents=True, exist_ok=True)
    failures = 0
    for entry in entries:
        pdf_path = paths["papers"] / f"{entry.id}.pdf"
        meta_path = paths["meta"] / f"{entry.id}.json"
        if pdf_path.exists() and meta_path.exists() and not args.force:
            print(f"fetch {entry.id}: cached")
            continue
        try:
            abs_html = _http_get(f"https://arxiv.org/abs/{entry.arxiv_id}").decode(
                "utf-8", "ignore"
            )
            license_match = _LICENSE_RE.search(abs_html)
            license_url = license_match.group(0) if license_match else "unknown"
            time.sleep(3.0)  # arXiv politeness
            pdf_bytes = _http_get(f"https://arxiv.org/pdf/{entry.arxiv_id}")
            if not pdf_bytes.startswith(b"%PDF"):
                raise RuntimeError("response is not a PDF")
            pdf_path.write_bytes(pdf_bytes)
            meta = {
                "id": entry.id,
                "arxiv_id": entry.arxiv_id,
                "title": entry.title,
                "tags": list(entry.tags),
                "source_url": f"https://arxiv.org/abs/{entry.arxiv_id}",
                "license": license_url,
                "showcase_ok": "creativecommons" in license_url,
                "sha256": hashlib.sha256(pdf_bytes).hexdigest(),
                "bytes": len(pdf_bytes),
                "fetched_at": datetime.now(timezone.utc).isoformat(),
            }
            meta_path.write_text(
                json.dumps(meta, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
            license_tag = (
                license_url.rsplit("/", 2)[-2] if license_url != "unknown" else "unknown"
            )
            print(
                f"fetch {entry.id}: {len(pdf_bytes) / 1e6:.1f}MB license={license_tag}"
            )
            time.sleep(3.0)
        except Exception as exc:
            failures += 1
            print(f"fetch {entry.id}: FAILED {exc}", file=sys.stderr)
    return 1 if failures else 0


# ---------------------------------------------------------------------------
# translate
# ---------------------------------------------------------------------------


def _load_env_file() -> None:
    env_path = REPO / ".env"
    if not env_path.exists():
        return
    import os

    for line in env_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        os.environ.setdefault(key.strip(), value.strip().strip("'\""))


def _env(name: str, default: str = "") -> str:
    """Read a setting from the environment, accepting the app prefix too."""
    import os

    return os.environ.get(name) or os.environ.get(f"PAPER_CHINA_{name}") or default


def cmd_translate(entries: list[Entry], workdir: Path, args) -> int:
    _load_env_file()
    api_key = _env("DEEPSEEK_API_KEY")
    if not api_key:
        raise SystemExit(
            "DEEPSEEK_API_KEY / PAPER_CHINA_DEEPSEEK_API_KEY is not set"
        )

    from pdf_zh_translator.pdf_layout import translate_pdf
    from pdf_zh_translator.translators import CachedTranslator, VendorTranslator

    paths = _paths(workdir)
    paths["translations"].mkdir(parents=True, exist_ok=True)
    failures = 0
    for entry in entries:
        source = paths["papers"] / f"{entry.id}.pdf"
        if not source.exists():
            print(f"translate {entry.id}: missing source, run fetch first")
            failures += 1
            continue
        mono = paths["translations"] / f"{entry.id}-mono.pdf"
        timing_path = paths["translations"] / f"{entry.id}.timing.json"
        if mono.exists() and not args.force:
            print(f"translate {entry.id}: cached")
            continue
        vendor = VendorTranslator(
            api_url=_env("DEEPSEEK_API_URL", "https://api.deepseek.com"),
            api_key=api_key,
            mode="deepseek",
            model=_env("DEEPSEEK_MODEL", "deepseek-v4-pro"),
            source_lang="en",
            target_lang="zh",
            progress=False,
        )
        translator = CachedTranslator(
            vendor,
            paths["translations"] / f"{entry.id}.translation-cache.jsonl",
        )
        started = time.time()
        try:
            report = translate_pdf(
                input_pdf=source,
                output_pdf=mono,
                translator=translator,
                preserve_graphics_text=True,
            )
        except Exception as exc:
            failures += 1
            print(f"translate {entry.id}: FAILED {exc}", file=sys.stderr)
            continue
        elapsed = time.time() - started
        timing_path.write_text(
            json.dumps(
                {
                    "id": entry.id,
                    "seconds": round(elapsed, 1),
                    "pages": report.page_count,
                    "translated_blocks": report.translated_blocks,
                    "finished_at": datetime.now(timezone.utc).isoformat(),
                },
                ensure_ascii=False,
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )
        print(
            f"translate {entry.id}: {report.page_count}p "
            f"{report.translated_blocks} blocks in {elapsed / 60:.1f}min"
        )
    return 1 if failures else 0


# ---------------------------------------------------------------------------
# evaluate
# ---------------------------------------------------------------------------


def cmd_evaluate(entries: list[Entry], workdir: Path, args) -> int:
    from pdf_zh_translator.page_inspector import INSPECTOR_ISSUE_CODES
    from pdf_zh_translator.pdf_layout import verify_translation_issues
    from pdf_zh_translator.visual_qa import score_visual_layout

    paths = _paths(workdir)
    paths["reports"].mkdir(parents=True, exist_ok=True)
    failures = 0
    for entry in entries:
        source = paths["papers"] / f"{entry.id}.pdf"
        mono = paths["translations"] / f"{entry.id}-mono.pdf"
        report_path = paths["reports"] / f"{entry.id}.json"
        if not source.exists() or not mono.exists():
            print(f"evaluate {entry.id}: missing pair, skipping")
            continue
        if report_path.exists() and not args.force:
            print(f"evaluate {entry.id}: cached")
            continue
        started = time.time()
        try:
            issues = verify_translation_issues(source, mono)
            visual = score_visual_layout(source, mono)
        except Exception as exc:
            failures += 1
            print(f"evaluate {entry.id}: FAILED {exc}", file=sys.stderr)
            continue
        errors = [issue for issue in issues if issue.severity == "error"]
        legacy_errors = [
            issue for issue in errors if issue.code not in INSPECTOR_ISSUE_CODES
        ]
        by_code: dict[str, int] = {}
        error_pages: set[int] = set()
        for issue in issues:
            by_code[issue.code] = by_code.get(issue.code, 0) + 1
            if issue.severity == "error" and issue.page:
                error_pages.add(issue.page)
        payload = {
            "id": entry.id,
            "arxiv_id": entry.arxiv_id,
            "title": entry.title,
            "tags": list(entry.tags),
            "evaluated_at": datetime.now(timezone.utc).isoformat(),
            "seconds": round(time.time() - started, 1),
            "visual_score": round(visual.overall_score, 4),
            "visual_risk": visual.risk_level,
            "pages": visual.original_pages,
            "issue_count": len(issues),
            "error_count": len(errors),
            "legacy_error_count": len(legacy_errors),
            "error_pages": sorted(error_pages),
            "issues_by_code": dict(sorted(by_code.items())),
            "strict_pass": not errors and visual.overall_score >= 0.55,
            "legacy_pass": not legacy_errors and visual.overall_score >= 0.55,
            "issues": [
                {
                    "page": issue.page,
                    "code": issue.code,
                    "severity": issue.severity,
                    "message": issue.message,
                }
                for issue in issues
            ],
        }
        report_path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        print(
            f"evaluate {entry.id}: visual={payload['visual_score']:.2f} "
            f"errors={payload['error_count']} "
            f"(legacy {payload['legacy_error_count']}) "
            f"strict={'PASS' if payload['strict_pass'] else 'FAIL'}"
        )
    return 1 if failures else 0


# ---------------------------------------------------------------------------
# report
# ---------------------------------------------------------------------------


def _render_previews(
    entry_id: str,
    source: Path,
    mono: Path,
    error_pages: list[int],
    previews_dir: Path,
    *,
    max_pages: int = 6,
    dpi: int = 110,
) -> list[dict]:
    import fitz

    out_dir = previews_dir / entry_id
    out_dir.mkdir(parents=True, exist_ok=True)
    original = fitz.open(source)
    translated = fitz.open(mono)
    pages = list(dict.fromkeys([1, 2] + error_pages))[:max_pages]
    records = []
    for page_number in pages:
        index = page_number - 1
        if index < 0 or index >= min(original.page_count, translated.page_count):
            continue
        record = {"page": page_number}
        for label, document in (("original", original), ("translated", translated)):
            pixmap = document[index].get_pixmap(dpi=dpi, alpha=False)
            path = out_dir / f"p{page_number:03d}_{label}.jpg"
            pixmap.save(str(path), jpg_quality=82)
            record[label] = str(path.relative_to(previews_dir.parent))
        records.append(record)
    translated.close()
    original.close()
    return records


def cmd_report(entries: list[Entry], workdir: Path, args) -> int:
    paths = _paths(workdir)
    paths["previews"].mkdir(parents=True, exist_ok=True)
    rows = []
    showcase = []
    for entry in entries:
        report_path = paths["reports"] / f"{entry.id}.json"
        meta_path = paths["meta"] / f"{entry.id}.json"
        if not report_path.exists():
            continue
        report = json.loads(report_path.read_text(encoding="utf-8"))
        meta = (
            json.loads(meta_path.read_text(encoding="utf-8"))
            if meta_path.exists()
            else {}
        )
        rows.append((entry, report, meta))

    if not rows:
        print("no evaluation reports found; run evaluate first")
        return 1

    lines = [
        "# Classic paper translation benchmark",
        "",
        f"Generated: {datetime.now(timezone.utc).isoformat()}",
        "",
        f"Papers evaluated: {len(rows)} | strict pass: "
        f"{sum(1 for _, report, _ in rows if report['strict_pass'])} | "
        f"legacy pass: {sum(1 for _, report, _ in rows if report['legacy_pass'])}",
        "",
        "| paper | pages | visual | errors | top issue classes | strict | legacy |",
        "|---|---|---|---|---|---|---|",
    ]
    for entry, report, _meta in rows:
        top = sorted(
            report["issues_by_code"].items(), key=lambda item: -item[1]
        )[:3]
        top_text = ", ".join(f"{code}:{count}" for code, count in top) or "-"
        lines.append(
            f"| {entry.id} | {report['pages']} | {report['visual_score']:.2f} "
            f"| {report['error_count']} | {top_text} "
            f"| {'PASS' if report['strict_pass'] else 'FAIL'} "
            f"| {'PASS' if report['legacy_pass'] else 'FAIL'} |"
        )

    # layout-axis coverage
    axis_counts: dict[str, int] = {}
    for entry, _report, _meta in rows:
        for tag in entry.tags:
            axis_counts[tag] = axis_counts.get(tag, 0) + 1
    lines += [
        "",
        "## Layout coverage",
        "",
        *(f"- {axis}: {count}" for axis, count in sorted(axis_counts.items())),
    ]

    report_md = workdir / "REPORT.md"
    report_md.write_text("\n".join(lines) + "\n", encoding="utf-8")

    for entry, report, meta in rows:
        source = paths["papers"] / f"{entry.id}.pdf"
        mono = paths["translations"] / f"{entry.id}-mono.pdf"
        previews = []
        if not args.no_previews and source.exists() and mono.exists():
            previews = _render_previews(
                entry.id,
                source,
                mono,
                report.get("error_pages", []),
                paths["previews"],
            )
        showcase.append(
            {
                "id": entry.id,
                "arxiv_id": entry.arxiv_id,
                "title": entry.title,
                "tags": list(entry.tags),
                "license": meta.get("license", "unknown"),
                "showcase_ok": bool(meta.get("showcase_ok")),
                "pages": report["pages"],
                "visual_score": report["visual_score"],
                "error_count": report["error_count"],
                "issues_by_code": report["issues_by_code"],
                "strict_pass": report["strict_pass"],
                "legacy_pass": report["legacy_pass"],
                "previews": previews,
            }
        )
    (workdir / "showcase.json").write_text(
        json.dumps(
            {
                "generated_at": datetime.now(timezone.utc).isoformat(),
                "papers": showcase,
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    print(f"wrote {report_md} and showcase.json ({len(showcase)} papers)")
    return 0


# ---------------------------------------------------------------------------


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "command", choices=["fetch", "translate", "evaluate", "report"]
    )
    parser.add_argument("--only", help="comma-separated paper ids")
    parser.add_argument("--workdir", type=Path, default=DEFAULT_WORKDIR)
    parser.add_argument("--force", action="store_true", help="redo cached steps")
    parser.add_argument(
        "--no-previews", action="store_true", help="skip preview rendering in report"
    )
    args = parser.parse_args()

    entries = _load_entries(args.only)
    args.workdir.mkdir(parents=True, exist_ok=True)
    handler = {
        "fetch": cmd_fetch,
        "translate": cmd_translate,
        "evaluate": cmd_evaluate,
        "report": cmd_report,
    }[args.command]
    return handler(entries, args.workdir, args)


if __name__ == "__main__":
    sys.exit(main())
