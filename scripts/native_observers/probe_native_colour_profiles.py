"""Read full current ICC profiles from the original Nature 54639 PDF.

This exercises the read-only C accessor through actual MuPDF device callbacks.
It makes no provider request and does not qualify any translated PDF.
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import sys
from pathlib import Path


def probe_native_colour_profiles(args: argparse.Namespace) -> dict:
    import fitz

    data = args.source.read_bytes()
    expected = "7096ca5062d2251b5d08397a1b1861a7d46b682bb714e2963ce76cafb06c693f"
    if hashlib.sha256(data).hexdigest() != expected:
        raise ValueError("ORIGINAL_NATURE_54639_SOURCE_REQUIRED")
    args.output_dir.mkdir(parents=True, exist_ok=False)
    spec = importlib.util.spec_from_file_location(
        "native_colour_probe_bootstrap",
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
    from c999_full_current_native_font_storage import make_observer as native_observer
    from c1033_current_paint_colour_windows import make_observer as colour_observer

    observations = []
    profiles = {}
    try:
        native, _ = native_observer(session.core)
        observe = colour_observer(session.core, native)
        with fitz.open(stream=data, filetype="pdf") as doc:
            assert len(doc) == 16
            for page in doc:
                observed = observe(page)
                observations.append(observed)
                stack = [observed]
                while stack:
                    value = stack.pop()
                    if isinstance(value, dict):
                        if value.get("kind") == "complete_current_native_ICC_buffer":
                            profiles[value["SHA256"]] = value
                        stack.extend(value.values())
                    elif isinstance(value, (list, tuple)):
                        stack.extend(value)
                print(json.dumps({"source_page_observed": page.number + 1}), flush=True)
    finally:
        close = session.close()
    assert all(close[key] for key in ("lifecycle_cleared", "core_unchanged", "native_unchanged"))
    assert {3144, 119508} <= {value["bytes"] for value in profiles.values()}
    assert args.source.read_bytes() == data
    encoded = json.dumps(observations, sort_keys=True, separators=(",", ":")) + "\n"
    (args.output_dir / "observations.json").write_text(encoded)
    receipt = {
        "scope": "CURRENT_NATIVE_ICC_SOURCE_PROBE_ONLY_NOT_TRANSLATION_ACCEPTANCE",
        "PASS": True,
        "whole20_PASS": False,
        "provider_requests": 0,
        "source_sha256": expected,
        "manifest_sha256": args.manifest_sha256,
        "source_pages_observed": len(observations),
        "complete_profiles": profiles,
        "observations_sha256": hashlib.sha256(encoded.encode()).hexdigest(),
        "close": close,
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
    result = probe_native_colour_profiles(parser.parse_args())
    print(
        json.dumps(
            {
                "PASS": result["PASS"],
                "pages": result["source_pages_observed"],
                "profiles": len(result["complete_profiles"]),
            }
        )
    )
