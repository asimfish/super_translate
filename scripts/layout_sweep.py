#!/usr/bin/env python3
"""Layout sweep: find engine defects on real papers without spending API credit.

For every PDF given, run the native engine in ``--dry-run`` mode (the
EchoTranslator emits Chinese filler at the measured EN?ZH length ratio, so the
whole layout pipeline runs: structure analysis, protection, refill, fonts),
audit the result with ``inspect``, and aggregate the findings.

    uv run python scripts/layout_sweep.py papers/*.pdf --out /tmp/sweep

Outputs, per paper, ``<out>/<stem>/dry.pdf``, ``inspect.json``,
``translate.log`` and a ``sheet_errors.png`` contact sheet (source | dry-run)
for the first pages with error-level findings, plus ``<out>/summary.md`` with
issue counts by code. Exit status is 1 when any paper reports error-level
issues, so the script doubles as a coarse regression gate.

Font-size drift reported by dry-run is only indicative: the filler is a
median-length translation, so a block that shrinks here may fit a real
translation at full size (and vice versa). Structural findings - reflowed
tables, text outside frames, untranslated blocks, reference tampering - do not
depend on filler length and are the ones worth chasing first.
"""

from __future__ import annotations

import argparse
import collections
import json
import re
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def run(cmd: list[str], log: Path) -> tuple[int, float]:
    start = time.time()
    with log.open("a", encoding="utf-8") as handle:
        handle.write("$ " + " ".join(cmd) + "\n")
        proc = subprocess.run(cmd, stdout=handle, stderr=subprocess.STDOUT, text=True)
    return proc.returncode, time.time() - start


def render_thumbs(pdf: Path, prefix: Path, dpi: int) -> None:
    subprocess.run(
        ["pdftoppm", "-png", "-r", str(dpi), str(pdf), str(prefix)],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        check=False,
    )


def contact_sheet(out_dir: Path, pages: list[int]) -> None:
    try:
        from PIL import Image
    except ImportError:
        return

    def page_image(prefix: str, page: int):
        for path in sorted(out_dir.glob(f"{prefix}-*.png")):
            match = re.search(r"-(\d+)\.png$", path.name)
            if match and int(match.group(1)) == page:
                return Image.open(path).convert("RGB")
        return None

    tiles = []
    for page in pages[:6]:
        source, dry = page_image("src", page), page_image("dry", page)
        if source is not None and dry is not None:
            tiles.append((source, dry))
    if not tiles:
        return
    width = max(a.width + b.width + 12 for a, b in tiles)
    height = sum(max(a.height, b.height) + 8 for a, b in tiles)
    sheet = Image.new("RGB", (width, height), (40, 40, 40))
    y = 0
    for source, dry in tiles:
        sheet.paste(source, (0, y))
        sheet.paste(dry, (source.width + 12, y))
        y += max(source.height, dry.height) + 8
    sheet.save(out_dir / "sheet_errors.png")


def sweep_one(python: str, pdf: Path, out_dir: Path, dpi: int) -> dict:
    out_dir.mkdir(parents=True, exist_ok=True)
    dry_pdf = out_dir / "dry.pdf"
    log = out_dir / "translate.log"
    log.write_text("", encoding="utf-8")
    translate_rc, translate_s = run(
        [
            python,
            "-m",
            "pdf_zh_translator",
            "translate",
            str(pdf),
            str(dry_pdf),
            "--dry-run",
            "--preserve-graphics-text",
        ],
        log,
    )
    issues: list[dict] = []
    inspect_s = 0.0
    if translate_rc == 0 and dry_pdf.exists():
        inspect_json = out_dir / "inspect.json"
        _rc, inspect_s = run(
            [
                python,
                "-m",
                "pdf_zh_translator",
                "inspect",
                str(pdf),
                str(dry_pdf),
                "--json-out",
                str(inspect_json),
            ],
            out_dir / "inspect.log",
        )
        if inspect_json.exists():
            try:
                issues = json.loads(inspect_json.read_text(encoding="utf-8"))["issues"]
            except (ValueError, KeyError):
                issues = []
        if dpi > 0:
            render_thumbs(pdf, out_dir / "src", dpi)
            render_thumbs(dry_pdf, out_dir / "dry", dpi)
            error_pages = sorted({int(i["page"]) for i in issues if i["severity"] == "error"})
            contact_sheet(out_dir, error_pages or [1])
    warnings = collections.Counter()
    for line in log.read_text(encoding="utf-8", errors="replace").splitlines():
        if line.startswith("Warning:"):
            key = re.sub(r"Page \d+: ", "", line[len("Warning:"):].strip())
            warnings[re.sub(r"\d+(\.\d+)?", "N", key)[:80]] += 1
    return {
        "paper": pdf.stem,
        "translate_rc": translate_rc,
        "translate_seconds": round(translate_s),
        "inspect_seconds": round(inspect_s),
        "issues": issues,
        "warnings": warnings,
    }


def write_summary(results: list[dict], out: Path) -> bool:
    grand = collections.Counter()
    lines = ["# Layout sweep summary", ""]
    any_errors = False
    for result in results:
        codes = collections.Counter((i["severity"], i["code"]) for i in result["issues"])
        grand.update(codes)
        errors = [i for i in result["issues"] if i["severity"] == "error"]
        any_errors = any_errors or bool(errors)
        lines.append(
            f"## {result['paper']} - translate rc={result['translate_rc']} "
            f"({result['translate_seconds']}s), inspect {result['inspect_seconds']}s, "
            f"{len(result['issues'])} issue(s)"
        )
        for (severity, code), count in codes.most_common():
            lines.append(f"- {severity} `{code}` x{count}")
        for issue in errors[:8]:
            lines.append(f"  - p{issue['page']}: {issue['message'][:160]}")
        for key, count in result["warnings"].most_common(5):
            lines.append(f"- engine warning: {key} x{count}")
        lines.append("")
    lines.append("## Totals")
    for (severity, code), count in grand.most_common():
        lines.append(f"- {severity} `{code}` x{count}")
    (out / "summary.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    return any_errors


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("pdfs", nargs="+", type=Path, help="Source PDFs to sweep")
    parser.add_argument(
        "--out", type=Path, default=Path("/tmp/layout-sweep"), help="Output directory"
    )
    parser.add_argument(
        "--python",
        default=str(ROOT / ".venv/bin/python"),
        help="Interpreter with the package installed",
    )
    parser.add_argument(
        "--dpi", type=int, default=60, help="Thumbnail DPI for contact sheets (0 disables)"
    )
    args = parser.parse_args()

    args.out.mkdir(parents=True, exist_ok=True)
    results = []
    for pdf in args.pdfs:
        if not pdf.exists():
            print(f"skip (missing): {pdf}", file=sys.stderr)
            continue
        print(f"== {pdf.name}", flush=True)
        result = sweep_one(args.python, pdf, args.out / pdf.stem, args.dpi)
        results.append(result)
        errors = sum(1 for i in result["issues"] if i["severity"] == "error")
        print(
            f"   translate rc={result['translate_rc']} {result['translate_seconds']}s | "
            f"inspect {result['inspect_seconds']}s | "
            f"{len(result['issues'])} issue(s), {errors} error(s)",
            flush=True,
        )
    any_errors = write_summary(results, args.out)
    print(f"summary: {args.out / 'summary.md'}")
    return 1 if any_errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
