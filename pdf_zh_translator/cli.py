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

    return parser


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
