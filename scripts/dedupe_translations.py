#!/usr/bin/env python3
"""One-off: dedupe byte-identical images in already-translated PDFs.

Older translations were saved before save_pdf_for_fast_web_view learned to
dedupe images, so math-heavy outputs carry orphan image copies (up to ~30% of
file size). Rewrites each PDF in place (atomic replace, .bak kept alongside).

Usage: .venv/bin/python scripts/dedupe_translations.py [data/translations]
"""

from __future__ import annotations

import os
import shutil
import sys
import tempfile
from pathlib import Path


def dedupe_file(path: Path) -> tuple[int, int]:
    """Dedupe one PDF in place. Returns (old_size, new_size)."""
    import pikepdf

    from pdf_zh_translator.pdf_layout import dedupe_pdf_images

    old_size = path.stat().st_size
    fd, tmp_name = tempfile.mkstemp(dir=path.parent, suffix=".tmp")
    os.close(fd)
    tmp = Path(tmp_name)
    try:
        with pikepdf.open(path) as pdf:
            removed = dedupe_pdf_images(pdf)
            if not removed:
                return old_size, old_size
            pdf.save(tmp, linearize=True, compress_streams=True)
        new_size = tmp.stat().st_size
        if new_size >= old_size:
            return old_size, old_size
        backup = path.with_suffix(path.suffix + ".bak")
        shutil.copy2(path, backup)
        tmp.replace(path)
        return old_size, new_size
    finally:
        tmp.unlink(missing_ok=True)


def main() -> None:
    root = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("data/translations")
    total_old = total_new = 0
    for pdf_path in sorted(root.rglob("*.pdf")):
        if pdf_path.name.startswith(".") or pdf_path.suffix == ".bak":
            continue
        try:
            old, new = dedupe_file(pdf_path)
        except Exception as exc:  # noqa: BLE001 - report and continue batch
            print(f"SKIP {pdf_path}: {exc}")
            continue
        if new < old:
            print(f"{pdf_path}: {old / 1e6:.1f}MB -> {new / 1e6:.1f}MB")
        total_old += old
        total_new += new
    print(f"TOTAL: {total_old / 1e6:.1f}MB -> {total_new / 1e6:.1f}MB")


if __name__ == "__main__":
    main()
