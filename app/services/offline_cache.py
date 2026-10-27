"""Bounded cache import/export for one already-authorized paper directory.

Only JSON data is accepted. Client filenames and paths never select server files.
"""
from __future__ import annotations

import json
from pathlib import Path

from pdf_zh_translator.translators import cache_key, placeholders_preserved

MAX_CACHE_BYTES = 20 * 1024 * 1024
MAX_CACHE_RECORDS = 100_000


def validate_cache(payload: bytes) -> bytes:
    if not payload or len(payload) > MAX_CACHE_BYTES:
        raise ValueError("翻译缓存为空或超过 20 MiB")
    try:
        lines = payload.decode("utf-8-sig").splitlines()
    except UnicodeDecodeError as exc:
        raise ValueError("翻译缓存必须是 UTF-8 JSONL 文件") from exc
    records = []
    for number, line in enumerate(lines, 1):
        if not line.strip():
            continue
        if len(records) >= MAX_CACHE_RECORDS:
            raise ValueError("翻译缓存条目过多")
        try:
            row = json.loads(line)
        except (ValueError, RecursionError) as exc:
            raise ValueError(f"翻译缓存第 {number} 行不是合法 JSON") from exc
        if not isinstance(row, dict) or not all(
            isinstance(row.get(key), str) for key in ("source", "translation", "key")
        ):
            raise ValueError(f"翻译缓存第 {number} 行缺少 key/source/translation")
        source, translation = row["source"], row["translation"]
        if (
            not source.strip() or not translation.strip()
            or max(len(source), len(translation)) > 200_000
            or row["key"] != cache_key(source)
            or not placeholders_preserved(source, translation)
        ):
            raise ValueError(f"翻译缓存第 {number} 行内容或占位符校验失败")
        records.append(json.dumps(
            {"key": row["key"], "source": source, "translation": translation},
            ensure_ascii=False,
        ))
    if not records:
        raise ValueError("翻译缓存没有有效条目")
    normalized = ("\n".join(records) + "\n").encode("utf-8")
    if len(normalized) > MAX_CACHE_BYTES:
        raise ValueError("规范化后的翻译缓存超过 20 MiB")
    return normalized


def previous_cache(output_dir: Path, stored_stem: str) -> Path:
    from app.services.pdf_sanitizer import safe_pdf_path

    source_stems = (stored_stem, safe_pdf_path(Path(f"{stored_stem}.pdf")).stem)
    patterns = [f"{stem}.translation-cache*.jsonl" for stem in source_stems]
    patterns.append(".cache-only-*.jsonl")
    candidates = [
        path for pattern in patterns
        for path in output_dir.glob(pattern)
        if path.is_file() and not path.is_symlink() and not path.name.endswith(".missing.jsonl")
    ]
    if not candidates:
        raise ValueError("本篇尚无翻译缓存，请先上传 translation-cache.jsonl")
    return max(candidates, key=lambda path: (path.stat().st_mtime_ns, path.name))


def read_cache(path: Path) -> bytes:
    with path.open("rb") as handle:
        return validate_cache(handle.read(MAX_CACHE_BYTES + 1))


def job_cache(output_dir: Path, job_id: str) -> Path:
    # job_id is server generated, durable and never a client-supplied path.
    return output_dir / f".cache-only-{job_id}.jsonl"
