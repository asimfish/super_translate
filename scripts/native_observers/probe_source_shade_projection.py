"""Exercise the Nature 52519 page 6 shade path without a translation provider.

This is a regression probe for the two native libraries, not PDF translation
acceptance. The original PDF and the normal font cache remain external inputs.
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import sys
from io import BytesIO
from pathlib import Path


def probe_source_shade_projection(args: argparse.Namespace) -> dict:
    import fitz
    import pikepdf

    source_bytes = args.source.read_bytes()
    expected = "ebb36034ca8d2ab8bf895194e5adc243beb1b3942299f6e564a471fae31071ff"
    if hashlib.sha256(source_bytes).hexdigest() != expected:
        raise ValueError("ORIGINAL_NATURE_52519_SOURCE_REQUIRED")
    args.output_dir.mkdir(parents=True, exist_ok=False)
    spec = importlib.util.spec_from_file_location(
        "native_observer_probe_bootstrap",
        args.bundle / "candidate_bootstrap.py",
    )
    bootstrap = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(bootstrap)
    session = bootstrap.initialize_candidate_runtime(
        args.source,
        args.output_dir / "runtime",
        args.font_cache_dir,
        expected_manifest_sha256=args.manifest_sha256,
    )
    from c1335_source_shade_projection_erasure import install, projection_certificate

    checks = []
    try:
        with fitz.open(stream=source_bytes, filetype="pdf") as original:
            certificate = projection_certificate(original[5])
            assert certificate["actual_native_images"] == 0
            assert len(certificate["projection"]["text_image_projections"]) == 3
            checks.append("original_three_shade_projections_zero_real_images")
        with fitz.open(stream=source_bytes, filetype="pdf") as original:
            assert projection_certificate(original[5]) == certificate
            checks.append("unchanged_cold_document_exact")
        with fitz.open(stream=source_bytes, filetype="pdf") as modified:
            pixmap = fitz.Pixmap(fitz.csRGB, fitz.IRect(0, 0, 2, 2), False)
            pixmap.clear_with(255)
            modified[5].insert_image(fitz.Rect(1, 1, 3, 3), pixmap=pixmap)
            try:
                projection_certificate(modified[5])
            except ValueError:
                pass
            else:
                raise AssertionError("ACTUAL_IMAGE_MUST_NOT_BE_CLASSIFIED_AS_SHADE")
            checks.append("added_real_image_refused")
        with fitz.open() as blank:
            try:
                projection_certificate(blank.new_page())
            except ValueError:
                pass
            else:
                raise AssertionError("EMPTY_CERTIFICATE_FORBIDDEN")
            checks.append("blank_page_refused")
        for mutation in ("geometry", "clip"):
            with pikepdf.open(BytesIO(source_bytes)) as parsed:
                page = parsed.pages[5]
                if mutation == "geometry":
                    operation = next(
                        op for op in pikepdf.parse_content_stream(page) if str(op.operator) == "sh"
                    )
                    shade = page.Resources["/Shading"][operation.operands[0]]
                    shade.Coords[0] = float(shade.Coords[0]) + 0.125
                else:
                    page.Contents = parsed.make_stream(
                        b"q 0 0 590 790 re W n\n" + page.Contents.read_bytes() + b"\nQ"
                    )
                buffer = BytesIO()
                parsed.save(buffer)
            with fitz.open(stream=buffer.getvalue(), filetype="pdf") as modified:
                changed = projection_certificate(modified[5])
                assert changed != certificate
                assert (
                    changed["complete_current_native_shades"]
                    != certificate["complete_current_native_shades"]
                )
                checks.append(f"actual_{mutation}_mutation_detected")
        from pdf_zh_translator import source_plain_reference_erasure as module

        previous = module._erase_source_held_plain_reference_page_v1
        handle = install(session.core)
        assert module._erase_source_held_plain_reference_page_v1 is not previous
        assert handle["binding"]["observer_removal_inverse_source_and_AST_exact"]
        handle["restore"]()
        assert module._erase_source_held_plain_reference_page_v1 is previous
        handle["restore"]()
        assert module._erase_source_held_plain_reference_page_v1 is previous
        checks.append("install_exact_restore_twice")
    finally:
        close = session.close()
    assert all(close[key] for key in ("lifecycle_cleared", "core_unchanged", "native_unchanged"))
    assert args.source.read_bytes() == source_bytes
    receipt = {
        "scope": "SOURCE_SHADE_NATIVE_REGRESSION_ONLY_NOT_TRANSLATION_ACCEPTANCE",
        "PASS": True,
        "whole20_PASS": False,
        "provider_requests": 0,
        "source_sha256": expected,
        "manifest_sha256": args.manifest_sha256,
        "checks": checks,
        "close": close,
        "certificate": certificate,
    }
    (args.output_dir / "receipt.json").write_text(json.dumps(receipt, indent=2) + "\n")
    return receipt


if __name__ == "__main__":
    sys.dont_write_bytecode = True
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--bundle", type=Path, required=True)
    parser.add_argument("--manifest-sha256", required=True)
    parser.add_argument("--font-cache-dir", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    result = probe_source_shade_projection(parser.parse_args())
    print(
        json.dumps(
            {"PASS": result["PASS"], "checks": len(result["checks"]), "provider_requests": 0}
        )
    )
