"""Command line interface for PDF translation."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Optional, Sequence

from .pdf_layout import translate_pdf
from .translators import TranslationError, build_translator_from_args


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.command == "translate":
        return run_translate(args)
    if args.command == "export":
        return run_export(args)
    if args.command == "corpus-add":
        return run_corpus_add(args)
    if args.command == "corpus-stats":
        return run_corpus_stats(args)
    if args.command == "corpus-health":
        return run_corpus_health(args)
    if args.command == "corpus-review":
        return run_corpus_review(args)
    if args.command == "corpus-audit":
        return run_corpus_audit(args)
    if args.command == "corpus-promote":
        return run_corpus_promote(args)
    if args.command == "corpus-release":
        return run_corpus_release(args)
    if args.command == "golden-init":
        return run_golden_init(args)
    if args.command == "golden-discover":
        return run_golden_discover(args)
    if args.command == "golden-eval":
        return run_golden_eval(args)
    if args.command == "layout-learn":
        return run_layout_learn(args)
    if args.command == "figure-ppt-prepare":
        return run_figure_ppt_prepare(args)
    if args.command == "figure-ppt-extract":
        return run_figure_ppt_extract(args)
    if args.command == "figure-ppt-batch-prepare":
        return run_figure_ppt_batch_prepare(args)
    if args.command == "figure-ppt-batch-register":
        return run_figure_ppt_batch_register(args)
    if args.command == "figure-ppt-source-audit":
        return run_figure_ppt_source_audit(args)
    if args.command == "figure-ppt-register":
        return run_figure_ppt_register(args)
    if args.command == "figure-ppt-audit":
        return run_figure_ppt_audit(args)

    parser.print_help()
    return 2


def run_export(args: argparse.Namespace) -> int:
    if not args.input_pdf.exists():
        print("Input PDF does not exist: %s" % args.input_pdf, file=sys.stderr)
        return 1

    import json

    import fitz

    from .pdf_layout import prepare_translation_units
    from .translators import cache_key

    document = fitz.open(str(args.input_pdf))
    units, _, skipped = prepare_translation_units(document)
    document.close()

    args.blocks_jsonl.parent.mkdir(parents=True, exist_ok=True)
    with args.blocks_jsonl.open("w", encoding="utf-8") as handle:
        for block, protected, _ in units:
            record = {
                "key": cache_key(protected),
                "page": block.page_index + 1,
                "source": protected,
            }
            handle.write(json.dumps(record, ensure_ascii=False) + "\n")
    total = len(units)
    print(
        "Exported %d translatable blocks (%d skipped) -> %s" % (total, skipped, args.blocks_jsonl)
    )
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="pdf-zh-translator",
        description="Translate English PDFs into Chinese PDFs while preserving page layout.",
    )
    subparsers = parser.add_subparsers(dest="command")

    translate = subparsers.add_parser("translate", help="Translate a PDF")
    translate.add_argument("input_pdf", type=Path, help="English source PDF")
    translate.add_argument("output_pdf", type=Path, help="Chinese output PDF")
    translate.add_argument(
        "--api-url",
        help="Supplier API URL. Or set PDF_TRANSLATOR_API_URL.",
    )
    translate.add_argument(
        "--api-key",
        help="Supplier API key. Prefer --api-key-env for safety.",
    )
    translate.add_argument(
        "--api-key-env",
        default="PDF_TRANSLATOR_API_KEY",
        help="Environment variable containing the supplier API key.",
    )
    translate.add_argument(
        "--api-mode",
        choices=("generic", "openai-compatible", "deepseek", "cache-only"),
        default="deepseek",
        help="Supplier protocol. 'cache-only' renders from cache.",
    )
    translate.add_argument(
        "--model",
        help="Model name. Defaults to deepseek-v4-pro.",
    )
    translate.add_argument("--source-lang", default="en", help="Source language code.")
    translate.add_argument("--target-lang", default="zh", help="Target language code.")
    translate.add_argument("--auth-header", default="Authorization", help="API key header name.")
    translate.add_argument(
        "--auth-scheme",
        default="Bearer",
        help="Auth scheme prefix. Use '' for raw key.",
    )
    translate.add_argument(
        "--batch-size",
        type=int,
        default=8,
        help="Text blocks per translation request.",
    )
    translate.add_argument(
        "--max-batch-chars",
        type=int,
        default=2500,
        help="Max source characters per translation request.",
    )
    translate.add_argument(
        "--max-output-tokens",
        type=int,
        default=8192,
        help="Maximum model output tokens per request.",
    )
    translate.add_argument(
        "--timeout",
        type=float,
        default=60.0,
        help="Supplier request timeout in seconds.",
    )
    translate.add_argument(
        "--retries",
        type=int,
        default=2,
        help="Supplier request retry count.",
    )
    translate.add_argument(
        "--deepseek-thinking",
        choices=("disabled", "enabled"),
        default="disabled",
        help="DeepSeek thinking mode.",
    )
    translate.add_argument(
        "--reasoning-effort",
        choices=("low", "medium", "high"),
        default="high",
        help="DeepSeek reasoning effort level.",
    )
    translate.add_argument(
        "--font-name",
        default="china-s",
        help="PDF font alias for inserted Chinese text.",
    )
    translate.add_argument(
        "--font-file",
        type=Path,
        help="Optional TTF/OTF font file for Chinese text.",
    )
    translate.add_argument(
        "--min-font-size",
        type=float,
        default=5.0,
        help="Smallest font size allowed.",
    )
    translate.add_argument(
        "--font-scale",
        type=float,
        default=0.92,
        help="Scale factor applied to original font size.",
    )
    translate.add_argument(
        "--margin",
        type=float,
        default=0.8,
        help="Redaction/insertion padding in PDF points.",
    )
    translate.add_argument(
        "--cache-file",
        type=Path,
        help="JSONL translation cache path.",
    )
    translate.add_argument(
        "--quiet",
        action="store_true",
        help="Suppress per-batch translation progress.",
    )
    translate.add_argument(
        "--dry-run",
        action="store_true",
        help="Insert placeholder translations without API call.",
    )
    translate.add_argument(
        "--preserve-graphics-text",
        action="store_true",
        help=(
            "Keep text inside figures/tables and math-heavy labels unchanged "
            "while still translating surrounding captions and prose."
        ),
    )
    translate.add_argument(
        "--skip-overflow",
        action="store_true",
        help="Leave original text unchanged when the Chinese translation cannot fit its bbox.",
    )

    export = subparsers.add_parser(
        "export",
        help="Extract translation blocks to JSONL for manual translation.",
    )
    export.add_argument("input_pdf", type=Path, help="English source PDF")
    export.add_argument(
        "blocks_jsonl",
        type=Path,
        help="Output JSONL: {key, page, source} per block",
    )

    corpus_add = subparsers.add_parser(
        "corpus-add",
        help="Add or update approved terminology in the academic corpus.",
    )
    corpus_add.add_argument("field", help="Corpus field/category, e.g. ai_conference")
    corpus_add.add_argument(
        "terms",
        nargs="+",
        help="Approved term pair in English=中文 format. May be repeated.",
    )
    corpus_add.add_argument("--source", default="cli", help="Source label for metadata.")
    corpus_add.add_argument(
        "--corpus-file",
        type=Path,
        help="Optional corpus JSON path. Defaults to the package corpus.",
    )

    subparsers.add_parser("corpus-stats", help="Print terminology counts by field.")

    corpus_health = subparsers.add_parser(
        "corpus-health",
        help="Report AI conference corpus coverage and pending candidate terms.",
    )
    corpus_health.add_argument(
        "--candidates-jsonl",
        type=Path,
        help="Optional terminology_candidates.jsonl file to include pending review count.",
    )
    corpus_health.add_argument("--json", action="store_true", help="Print JSON report.")

    corpus_review = subparsers.add_parser(
        "corpus-review",
        help="Deduplicate terminology candidates into a review JSON file.",
    )
    corpus_review.add_argument("candidates_jsonl", type=Path)
    corpus_review.add_argument("review_json", type=Path)

    corpus_audit = subparsers.add_parser(
        "corpus-audit",
        help="Audit, deduplicate, and auto-classify terminology candidates.",
    )
    corpus_audit.add_argument("candidates_jsonl", type=Path)
    corpus_audit.add_argument("review_json", type=Path)

    corpus_promote = subparsers.add_parser(
        "corpus-promote",
        help="Promote approved review terms into the official corpus.",
    )
    corpus_promote.add_argument("review_json", type=Path)
    corpus_promote.add_argument(
        "field",
        help="Target field/category, or 'auto' to use review fields.",
    )
    corpus_promote.add_argument("--source", default="candidate-review")
    corpus_promote.add_argument("--corpus-file", type=Path)

    corpus_release = subparsers.add_parser(
        "corpus-release",
        help="Stamp a reviewed corpus version.",
    )
    corpus_release.add_argument("version")
    corpus_release.add_argument("--corpus-file", type=Path)

    golden_init = subparsers.add_parser(
        "golden-init",
        help="Create a 100-paper golden regression manifest template.",
    )
    golden_init.add_argument("manifest", type=Path)
    golden_init.add_argument("--target-cases", type=int, default=100)

    golden_discover = subparsers.add_parser(
        "golden-discover",
        help="Discover paired original/translated PDFs and write a golden manifest.",
    )
    golden_discover.add_argument("root_dir", type=Path)
    golden_discover.add_argument("manifest", type=Path)
    golden_discover.add_argument("--target-cases", type=int, default=100)
    golden_discover.add_argument("--original-suffix", default="-original.pdf")
    golden_discover.add_argument("--translated-suffix", default="-translated.pdf")
    golden_discover.add_argument("--min-visual-score", type=float, default=0.55)

    golden_eval = subparsers.add_parser(
        "golden-eval",
        help="Evaluate translated papers listed in a golden manifest.",
    )
    golden_eval.add_argument("manifest", type=Path)

    layout_learn = subparsers.add_parser(
        "layout-learn",
        help="Learn a template-level layout profile from representative PDFs.",
    )
    layout_learn.add_argument("template_name")
    layout_learn.add_argument("output_json", type=Path)
    layout_learn.add_argument("pdfs", type=Path, nargs="+")
    layout_learn.add_argument("--max-pages-per-pdf", type=int, default=6)

    figure_prepare = subparsers.add_parser(
        "figure-ppt-prepare",
        help="Create an image-to-editable-ppt editppt run for one figure image.",
    )
    figure_prepare.add_argument("source_image", type=Path)
    figure_prepare.add_argument("output_root", type=Path)
    figure_prepare.add_argument("--figure-id")
    figure_prepare.add_argument(
        "--with-text-hints",
        action="store_true",
        help="Do not pass --no-text-hints to editppt prepare.",
    )

    figure_extract = subparsers.add_parser(
        "figure-ppt-extract",
        help="Extract PDF figure regions into image-to-editable-ppt source folders.",
    )
    figure_extract.add_argument("input_pdf", type=Path)
    figure_extract.add_argument("output_root", type=Path)
    figure_extract.add_argument("--paper-id")
    figure_extract.add_argument("--min-width", type=float, default=24.0)
    figure_extract.add_argument("--min-height", type=float, default=24.0)
    figure_extract.add_argument("--min-area", type=float, default=1000.0)
    figure_extract.add_argument("--max-figures", type=int)
    figure_extract.add_argument("--dpi", type=int, default=200)

    figure_batch_prepare = subparsers.add_parser(
        "figure-ppt-batch-prepare",
        help="Run editppt prepare for every extracted figure in a source manifest.",
    )
    figure_batch_prepare.add_argument("source_manifest", type=Path)
    figure_batch_prepare.add_argument("--limit", type=int)
    figure_batch_prepare.add_argument(
        "--with-text-hints",
        action="store_true",
        help="Do not pass --no-text-hints to editppt prepare.",
    )

    figure_batch_register = subparsers.add_parser(
        "figure-ppt-batch-register",
        help="Register every finalized editppt figure run in a source manifest.",
    )
    figure_batch_register.add_argument("source_manifest", type=Path)
    figure_batch_register.add_argument("--limit", type=int)

    figure_source_audit = subparsers.add_parser(
        "figure-ppt-source-audit",
        help="Audit extracted figure source manifests and editppt/register status.",
    )
    figure_source_audit.add_argument("source_manifest", type=Path)
    figure_source_audit.add_argument("--require-prepared", action="store_true")
    figure_source_audit.add_argument("--require-registered", action="store_true")
    figure_source_audit.add_argument(
        "--allow-empty",
        action="store_true",
        help="Return success when the source manifest has no figures.",
    )

    figure_register = subparsers.add_parser(
        "figure-ppt-register",
        help="Register an editppt-finalized figure PPTX with provenance.",
    )
    figure_register.add_argument("figure_id")
    figure_register.add_argument("source_image", type=Path)
    figure_register.add_argument("editppt_run", type=Path)
    figure_register.add_argument("output_dir", type=Path)
    figure_register.add_argument("--pptx", type=Path)

    figure_audit = subparsers.add_parser(
        "figure-ppt-audit",
        help="Audit editable figure PPT manifests under a directory.",
    )
    figure_audit.add_argument("root", type=Path)
    figure_audit.add_argument(
        "--allow-empty",
        action="store_true",
        help="Return success when no editable figure manifests are found.",
    )

    return parser


def run_corpus_add(args: argparse.Namespace) -> int:
    from .corpus import upsert_terms

    pairs: dict[str, str] = {}
    for item in args.terms:
        if "=" not in item:
            print("Invalid term pair, expected English=中文: %s" % item, file=sys.stderr)
            return 1
        english, chinese = item.split("=", 1)
        english = english.strip()
        chinese = chinese.strip()
        if not english or not chinese:
            print("Invalid empty term pair: %s" % item, file=sys.stderr)
            return 1
        pairs[english] = chinese

    changed = upsert_terms(
        args.field,
        pairs,
        source=args.source,
        corpus_path=args.corpus_file,
    )
    print("Updated %d terminology entr%s." % (changed, "y" if changed == 1 else "ies"))
    return 0


def run_corpus_stats(args: argparse.Namespace) -> int:
    from .corpus import corpus_stats

    stats = corpus_stats()
    for field, count in sorted(stats.items()):
        print(f"{field}: {count}")
    return 0


def run_corpus_health(args: argparse.Namespace) -> int:
    import json

    from .corpus import corpus_health

    health = corpus_health(args.candidates_jsonl)
    if args.json:
        print(json.dumps(health, ensure_ascii=False, indent=2))
        return 0

    print("Total terms: %s" % health["total_terms"])
    print("Top-conference terms: %s" % health["top_conference_terms"])
    for field, count in sorted(health["top_conference_fields"].items()):
        print(f"{field}: {count}")
    print("Candidate terms pending review: %s" % health["candidate_terms"])
    if health["missing_top_conference_fields"]:
        missing = ", ".join(health["missing_top_conference_fields"])
        print("Missing top-conference fields: " + missing)
    else:
        print("Top-conference fields: complete")
    if health["extra_corpora"]:
        print("Extra corpora: " + ", ".join(health["extra_corpora"]))
    return 0


def run_corpus_review(args: argparse.Namespace) -> int:
    from .corpus import write_candidate_review

    count = write_candidate_review(args.candidates_jsonl, args.review_json)
    print("Wrote %d deduplicated candidate term%s." % (count, "" if count == 1 else "s"))
    return 0


def run_corpus_audit(args: argparse.Namespace) -> int:
    from .corpus import write_candidate_review

    count = write_candidate_review(args.candidates_jsonl, args.review_json)
    print("Audited %d candidate term%s." % (count, "" if count == 1 else "s"))
    print("Review file: %s" % args.review_json)
    return 0


def run_corpus_promote(args: argparse.Namespace) -> int:
    from .corpus import promote_reviewed_terms

    changed = promote_reviewed_terms(
        args.review_json,
        field=args.field,
        corpus_path=args.corpus_file,
        source=args.source,
    )
    print("Promoted %d reviewed term%s." % (changed, "" if changed == 1 else "s"))
    return 0


def run_corpus_release(args: argparse.Namespace) -> int:
    from .corpus import release_corpus_version

    metadata = release_corpus_version(version=args.version, corpus_path=args.corpus_file)
    print("Released corpus %s with %s terms." % (metadata["version"], metadata["total_terms"]))
    return 0


def run_golden_init(args: argparse.Namespace) -> int:
    from .golden_eval import write_manifest_template

    write_manifest_template(args.manifest, target_cases=args.target_cases)
    print("Wrote golden manifest template: %s" % args.manifest)
    return 0


def run_golden_discover(args: argparse.Namespace) -> int:
    from .golden_eval import discover_golden_pairs

    count = discover_golden_pairs(
        args.root_dir,
        args.manifest,
        target_cases=args.target_cases,
        original_suffix=args.original_suffix,
        translated_suffix=args.translated_suffix,
        min_visual_score=args.min_visual_score,
    )
    print("Discovered %d golden PDF pair%s." % (count, "" if count == 1 else "s"))
    return 0 if count >= args.target_cases else 1


def run_golden_eval(args: argparse.Namespace) -> int:
    from .golden_eval import evaluate_golden_set

    result = evaluate_golden_set(args.manifest)
    print(
        "Evaluated %d/%d cases; %d passed."
        % (result.evaluated_cases, result.target_cases, result.passed_cases)
    )
    if result.profile_summary:
        profiles = ", ".join(
            "%s=%d" % (profile, count)
            for profile, count in sorted(result.profile_summary.items())
        )
        print("Layout profiles: %s" % profiles)
    risky = sum(1 for item in result.results if item.visual_risk != "low")
    if risky:
        print("Visual risk cases: %d" % risky)
    return 0 if result.ready_for_release else 1


def run_layout_learn(args: argparse.Namespace) -> int:
    from .layout_profiles import write_learned_layout_template

    missing = [path for path in args.pdfs if not path.exists()]
    if missing:
        print("Input PDF does not exist: %s" % missing[0], file=sys.stderr)
        return 1
    profile = write_learned_layout_template(
        args.pdfs,
        args.output_json,
        template_name=args.template_name,
        max_pages_per_pdf=args.max_pages_per_pdf,
    )
    print(
        "Learned layout template %s from %d PDF%s -> %s"
        % (
            profile["template_name"],
            profile["_metadata"]["source_count"],
            "" if profile["_metadata"]["source_count"] == 1 else "s",
            args.output_json,
        )
    )
    return 0


def run_figure_ppt_prepare(args: argparse.Namespace) -> int:
    from .editable_figures import prepare_editable_figure_run

    try:
        run_dir = prepare_editable_figure_run(
            args.source_image,
            args.output_root,
            figure_id=args.figure_id,
            no_text_hints=not args.with_text_hints,
        )
    except Exception as exc:
        print("Figure PPT prepare failed: %s" % exc, file=sys.stderr)
        return 1
    print("Prepared image-to-editable-ppt run: %s" % run_dir)
    print("Next: reconstruct pages with editppt, then run figure-ppt-register.")
    return 0


def run_figure_ppt_extract(args: argparse.Namespace) -> int:
    from .editable_figures import SOURCE_FIGURES_MANIFEST_FILENAME, extract_pdf_figures

    try:
        manifest = extract_pdf_figures(
            args.input_pdf,
            args.output_root,
            paper_id=args.paper_id,
            min_width=args.min_width,
            min_height=args.min_height,
            min_area=args.min_area,
            max_figures=args.max_figures,
            dpi=args.dpi,
        )
    except Exception as exc:
        print("Figure PPT extract failed: %s" % exc, file=sys.stderr)
        return 1
    manifest_path = (
        args.output_root
        / manifest["paper_id"]
        / SOURCE_FIGURES_MANIFEST_FILENAME
    )
    print(
        "Extracted %d figure source%s -> %s"
        % (
            manifest["figure_count"],
            "" if manifest["figure_count"] == 1 else "s",
            manifest_path,
        )
    )
    print("Next: run figure-ppt-batch-prepare on the source manifest.")
    return 0 if manifest["figure_count"] else 1


def run_figure_ppt_batch_prepare(args: argparse.Namespace) -> int:
    from .editable_figures import prepare_extracted_figures

    try:
        manifest = prepare_extracted_figures(
            args.source_manifest,
            no_text_hints=not args.with_text_hints,
            limit=args.limit,
        )
    except Exception as exc:
        print("Figure PPT batch prepare failed: %s" % exc, file=sys.stderr)
        return 1
    print(
        "Prepared %d/%d extracted figure%s."
        % (
            manifest.get("prepared_count", 0),
            manifest.get("figure_count", 0),
            "" if manifest.get("figure_count", 0) == 1 else "s",
        )
    )
    print("Next: reconstruct/finalize each editppt run, then run figure-ppt-register.")
    return 0


def run_figure_ppt_batch_register(args: argparse.Namespace) -> int:
    from .editable_figures import register_finalized_figures

    try:
        manifest = register_finalized_figures(args.source_manifest, limit=args.limit)
    except Exception as exc:
        print("Figure PPT batch register failed: %s" % exc, file=sys.stderr)
        return 1
    print(
        "Registered %d finalized figure%s; %d issue%s."
        % (
            manifest.get("_batch_registered", 0),
            "" if manifest.get("_batch_registered", 0) == 1 else "s",
            manifest.get("_batch_failed", 0),
            "" if manifest.get("_batch_failed", 0) == 1 else "s",
        )
    )
    for issue in manifest.get("registration_issues", []):
        print("Issue: %s" % issue, file=sys.stderr)
    return 0 if manifest.get("_batch_failed", 0) == 0 else 1


def run_figure_ppt_source_audit(args: argparse.Namespace) -> int:
    from .editable_figures import audit_figure_source_manifest

    result = audit_figure_source_manifest(
        args.source_manifest,
        require_prepared=args.require_prepared,
        require_registered=args.require_registered,
    )
    print(
        "Editable figure source audit: %d checked, %d passed, %d failed."
        % (result.checked, result.passed, result.failed)
    )
    for issue in result.issues:
        print("Issue: %s" % issue, file=sys.stderr)
    if result.checked == 0 and not args.allow_empty:
        print("No extracted figure sources found.", file=sys.stderr)
        return 1
    return 0 if result.failed == 0 else 1


def run_figure_ppt_register(args: argparse.Namespace) -> int:
    from .editable_figures import register_editable_figure

    try:
        manifest = register_editable_figure(
            figure_id=args.figure_id,
            source_image=args.source_image,
            editppt_run=args.editppt_run,
            output_dir=args.output_dir,
            pptx_path=args.pptx,
        )
    except Exception as exc:
        print("Figure PPT register failed: %s" % exc, file=sys.stderr)
        return 1
    print("Registered editable figure PPT: %s" % manifest["editppt_output"])
    print("Manifest: %s" % (args.output_dir / "editable_figure_manifest.json"))
    return 0


def run_figure_ppt_audit(args: argparse.Namespace) -> int:
    from .editable_figures import audit_editable_figure_manifests

    result = audit_editable_figure_manifests(args.root)
    print(
        "Editable figure PPT audit: %d checked, %d passed, %d failed."
        % (result.checked, result.passed, result.failed)
    )
    for issue in result.issues:
        print("Issue: %s" % issue, file=sys.stderr)
    if result.checked == 0 and not args.allow_empty:
        print(
            "No editable figure manifests found; current figures are not proven to use "
            "image-to-editable-ppt.",
            file=sys.stderr,
        )
        return 1
    return 0 if result.failed == 0 else 1


def run_translate(args: argparse.Namespace) -> int:
    if not args.input_pdf.exists():
        print("Input PDF does not exist: %s" % args.input_pdf, file=sys.stderr)
        return 1
    if args.input_pdf.resolve() == args.output_pdf.resolve():
        print("Output PDF must be different from input PDF.", file=sys.stderr)
        return 1
    if args.font_file and not args.font_file.exists():
        print("Font file does not exist: %s" % args.font_file, file=sys.stderr)
        return 1
    if not args.dry_run and args.cache_file is None:
        cache_name = args.output_pdf.name + ".translation-cache.jsonl"
        args.cache_file = args.output_pdf.with_name(cache_name)

    try:
        translator = build_translator_from_args(args)
        report = translate_pdf(
            input_pdf=args.input_pdf,
            output_pdf=args.output_pdf,
            translator=translator,
            font_name=args.font_name,
            font_file=args.font_file,
            min_font_size=args.min_font_size,
            font_scale=args.font_scale,
            margin=args.margin,
            preserve_graphics_text=args.preserve_graphics_text,
            skip_overflow=args.skip_overflow,
        )
    except TranslationError as exc:
        print("Translation failed: %s" % exc, file=sys.stderr)
        return 1
    except Exception as exc:
        print("PDF processing failed: %s" % exc, file=sys.stderr)
        return 1

    print("Wrote: %s" % report.output_pdf)
    print("Pages: %d" % report.page_count)
    print("Translated text blocks: %d" % report.translated_blocks)
    print("Skipped text blocks: %d" % report.skipped_blocks)
    for warning in report.warnings:
        print("Warning: %s" % warning, file=sys.stderr)
    return 0
