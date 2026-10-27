"""Prepare and audit checksummed real PDFs without network calls or model stubs.

Example: python scripts/check_layout_families.py prepare --source-root DIR --input-root DIR
         python scripts/check_layout_families.py check --source-root DIR --input-root DIR \
             --translated-root DIR --report report.json
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from pathlib import Path

import fitz

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from pdf_zh_translator.layout_families import load_layout_registry, page_layout  # noqa: E402
from pdf_zh_translator.layout_profiles import detect_layout_profile  # noqa: E402
from pdf_zh_translator.pdf_layout import verify_translation_issues  # noqa: E402


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def source_path(root: Path, name: str) -> Path:
    path = (root / name).resolve()
    if not path.is_relative_to(root.resolve()):
        raise ValueError("corpus source escapes its root")
    return path


def page_fingerprint(page: object) -> str:
    # Source appearance is an independent check on the prepared excerpt,
    # unaffected by random PDF IDs, new xref numbers or receipt contents.
    pix = page.get_pixmap(matrix=fitz.Matrix(0.5, 0.5), alpha=False)
    return hashlib.sha256(pix.samples).hexdigest()


def prepare_case(case: dict, source_root: Path, input_root: Path) -> dict:
    original = source_path(source_root, case["source_file"])
    if digest(original) != case["source_sha256"]:
        raise ValueError(f"{case['id']}: original source checksum mismatch")
    target = input_root / (case["id"] + ".pdf")
    receipt_path = target.with_suffix(".source.json")
    if target.exists():
        receipt = json.loads(receipt_path.read_text())
        if receipt["input_sha256"] != digest(target):
            raise ValueError(f"{case['id']}: prepared input checksum mismatch")
        return receipt
    with fitz.open(original) as document, fitz.open() as excerpt:
        for index in case["pages"]:
            if type(index) is not int or not 0 <= index < len(document):
                raise ValueError("corpus page index out of bounds")
            excerpt.insert_pdf(document, from_page=index, to_page=index)
        excerpt.save(target)
    receipt = {
        "source_sha256": digest(original), "pages": case["pages"],
        "input_sha256": digest(target),
    }
    receipt_path.write_text(json.dumps(receipt, indent=2) + "\n")
    return receipt


def audit_case(
    case: dict, source_root: Path, input_root: Path, translated_root: Path | None,
) -> dict:
    original = source_path(source_root, case["source_file"])
    input_pdf = input_root / (case["id"] + ".pdf")
    receipt = json.loads(input_pdf.with_suffix(".source.json").read_text())
    if (digest(original) != case["source_sha256"]
            or digest(input_pdf) != receipt["input_sha256"]
            or receipt["source_sha256"] != case["source_sha256"]
            or receipt["pages"] != case["pages"]):
        raise ValueError(f"{case['id']}: source/input binding failed")
    with fitz.open(original) as document, fitz.open(input_pdf) as excerpt:
        if len(excerpt) != len(case["pages"]):
            raise ValueError("prepared page count mismatch")
        for i, source_index in enumerate(case["pages"]):
            if page_fingerprint(document[source_index]) != page_fingerprint(excerpt[i]):
                raise ValueError(f"{case['id']}: prepared page appearance differs from original")
        profile = detect_layout_profile(document)
        expected = case["expected_profile"]
        if profile.name != expected["name"] or profile.columns != expected["columns"]:
            raise ValueError(f"{case['id']}: unexpected source profile {profile}")
        result = {
            "id": case["id"], "status": "detection_only", "profile": profile.name,
            "source_sha256": case["source_sha256"], "input_sha256": digest(input_pdf),
            "selected_pages": case["pages"], "source_pages": len(document),
            "page_roles": [page_layout(excerpt[i], i, kind=profile.kind).__dict__
                           for i in range(len(excerpt))],
        }
    translated = translated_root / (case["id"] + ".pdf") if translated_root else None
    if translated and translated.exists():
        result["translated_sha256"] = digest(translated)
        with fitz.open(translated) as output, fitz.open(input_pdf) as source:
            if len(output) != len(source):
                raise ValueError("translated page count mismatch")
            result["cjk_chars"] = len(re.findall(
                r"[\u4e00-\u9fff]", "".join(p.get_text() for p in output),
            ))
            if result["cjk_chars"] < 20:
                raise ValueError("translated output has insufficient selectable Chinese text")
            protected = []
            if profile.kind == "book":
                for index, page in enumerate(source):
                    role = page_layout(page, index, kind="book").role
                    for block in page.get_text("dict").get("blocks", []):
                        for line in block.get("lines", []):
                            spans = [s for s in line["spans"] if s["text"].strip()]
                            value = "".join(s["text"] for s in spans)
                            code = spans and all(
                                re.search(r"courier|\bcour\b|mono|cmtt|lmtt|sftt|inconsolata",
                                          s["font"], re.I) for s in spans
                            )
                            number = (
                                role == "contents" and line["bbox"][0] > page.rect.width * 0.75
                                and re.fullmatch(r"[0-9ivxlcdm]+", value.strip(), re.I)
                            )
                            if code or number:
                                clip = fitz.Rect(line["bbox"]) + (-0.8, -0.8, 0.8, 0.8)
                                copied = output[index].get_text("text", clip=clip).strip()
                                if copied != value.strip():
                                    raise ValueError(
                                        f"{case['id']}: source-owned code/page number changed"
                                    )
                                protected.append({
                                    "page": index, "kind": "code" if code else "page_number",
                                })
            result["source_owner_checks"] = protected
        issues = verify_translation_issues(input_pdf, translated)
        result["qa_errors"] = [i.__dict__ for i in issues if i.severity == "error"]
        result["qa_warnings"] = [i.__dict__ for i in issues if i.severity != "error"]
        result["status"] = "rendered_qa_pass" if not result["qa_errors"] else "rendered_qa_failed"
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=["prepare", "check"])
    parser.add_argument(
        "--manifest", type=Path, default=Path("benchmarks/layout-families/manifest.json"),
    )
    parser.add_argument("--source-root", type=Path, required=True)
    parser.add_argument("--input-root", type=Path, required=True)
    parser.add_argument("--translated-root", type=Path)
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--ids", nargs="*")
    parser.add_argument("--require-render", action="store_true")
    args = parser.parse_args()
    manifest = json.loads(args.manifest.read_text())
    cases = manifest["cases"]
    if args.ids:
        cases = [case for case in cases if case["id"] in args.ids]
        if len(cases) != len(set(args.ids)):
            parser.error("unknown or duplicate requested case ID")
    args.input_root.mkdir(parents=True, exist_ok=True)
    report = {"registry_version": load_layout_registry()["version"], "cases": [], "failures": []}
    for case in cases:
        if not re.fullmatch(r"[a-z0-9_-]+", case["id"]):
            raise ValueError("invalid corpus ID")
        try:
            if args.action == "prepare":
                prepare_case(case, args.source_root, args.input_root)
            else:
                result = audit_case(
                    case, args.source_root, args.input_root, args.translated_root,
                )
                report["cases"].append(result)
                if args.require_render and result["status"] != "rendered_qa_pass":
                    report["failures"].append({
                        "id": case["id"], "error": "render acceptance missing",
                    })
        except (OSError, ValueError, KeyError) as exc:
            report["failures"].append({"id": case["id"], "error": str(exc)})
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps({
        "cases": len(cases), "failures": len(report["failures"]), "report": str(args.report),
    }))
    return int(bool(report["failures"] or any(
        c["status"] == "rendered_qa_failed" for c in report["cases"]
    )))


if __name__ == "__main__":
    raise SystemExit(main())
