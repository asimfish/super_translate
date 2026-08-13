"""Classic-paper translation benchmark orchestrator.

Runs the strict quality benchmark over the curated classic paper set
(benchmarks/classic20/manifest.json):

    fetch      download PDFs from arXiv, record license/sha256/metadata
    translate  translate with the native engine (DeepSeek, cached, resumable)
    evaluate   full QA: verify_translation_issues (incl. visual inspector)
               plus render-based visual score, one JSON report per paper
    report     aggregate REPORT.md + showcase.json + page preview images
    gate       enforce release thresholds, provenance, and no regressions

All artifacts live under data/benchmark/classic20/ (gitignored); the curated
manifest and this script are the reproducible part.

Examples:
    .venv/bin/python scripts/classic_benchmark.py fetch
    .venv/bin/python scripts/classic_benchmark.py translate --only word2vec,adam
    .venv/bin/python scripts/classic_benchmark.py evaluate
    .venv/bin/python scripts/classic_benchmark.py report
    .venv/bin/python scripts/classic_benchmark.py gate
"""

from __future__ import annotations

import argparse
import functools
import hashlib
import json
import os
import re
import subprocess
import sys
import tempfile
import time
import urllib.request
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

MANIFEST = REPO / "benchmarks" / "classic20" / "manifest.json"
DEFAULT_WORKDIR = REPO / "data" / "benchmark" / "classic20"
_LOCK_ENV = "PAPER_CHINA_BENCHMARK_LOCK"
_LOCK_FD_ENV = "PAPER_CHINA_BENCHMARK_LOCK_FD"

_LICENSE_RE = re.compile(
    r"https?://(?:arxiv\.org/licenses|creativecommons\.org/(?:licenses|publicdomain))/[a-z0-9./-]+"
)
_USER_AGENT = "paper-china-benchmark/1.0 (translation quality benchmark)"
_QA_FILES = (
    REPO / "pdf_zh_translator" / "pdf_layout.py",
    REPO / "pdf_zh_translator" / "page_inspector.py",
    REPO / "pdf_zh_translator" / "visual_qa.py",
)
_ENGINE_FILES = (
    REPO / "pdf_zh_translator" / "pdf_layout.py",
    REPO / "pdf_zh_translator" / "layout_profiles.py",
    REPO / "pdf_zh_translator" / "translators.py",
    REPO / "pdf_zh_translator" / "corpus.py",
    REPO / "pdf_zh_translator" / "corpus.json",
    REPO / "pdf_zh_translator" / "corpora" / "ai_conferences.json",
    REPO / "pdf_zh_translator" / "corpora" / "top_venue_tracks.json",
)
_TRANSLATION_FILES = (
    REPO / "pdf_zh_translator" / "translators.py",
    REPO / "pdf_zh_translator" / "corpus.py",
    REPO / "pdf_zh_translator" / "corpus.json",
    REPO / "pdf_zh_translator" / "corpora" / "ai_conferences.json",
    REPO / "pdf_zh_translator" / "corpora" / "top_venue_tracks.json",
)


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


def _content_fingerprint(paths: tuple[Path, ...]) -> str:
    digest = hashlib.sha256()
    for path in paths:
        digest.update(str(path.relative_to(REPO)).encode("utf-8"))
        digest.update(b"\0")
        digest.update(path.read_bytes())
        digest.update(b"\0")
    return digest.hexdigest()


@functools.lru_cache(maxsize=1)
def _qa_fingerprint() -> str:
    return _content_fingerprint(_QA_FILES)


def _current_engine_fingerprint() -> str:
    return _content_fingerprint(_ENGINE_FILES)


@functools.lru_cache(maxsize=1)
def _engine_fingerprint() -> str:
    return _current_engine_fingerprint()


@functools.lru_cache(maxsize=1)
def _translation_fingerprint() -> str:
    return _content_fingerprint(_TRANSLATION_FILES)


def _current_font_fingerprint() -> str:
    from pdf_zh_translator.pdf_layout import build_font_pack

    pack = build_font_pack(None, [])
    digest = hashlib.sha256()
    for role, path in (
        ("regular", pack.regular_file),
        ("bold", pack.bold_file),
        ("fallback", pack.fallback_file),
        ("math", pack.math_fallback_file),
    ):
        digest.update(role.encode("ascii"))
        digest.update(b"\0")
        if path is not None:
            digest.update(Path(path).read_bytes())
        digest.update(b"\0")
    return digest.hexdigest()


@functools.lru_cache(maxsize=1)
def _font_fingerprint() -> str:
    return _current_font_fingerprint()


def _atomic_write_bytes(path: Path, payload: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    handle, temporary_name = tempfile.mkstemp(
        dir=path.parent,
        prefix=f".{path.name}.",
        suffix=".tmp",
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(handle, "wb") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _atomic_write_text(path: Path, payload: str) -> None:
    _atomic_write_bytes(path, payload.encode("utf-8"))


@contextmanager
def _benchmark_workdir_lock(workdir: Path, command: str):
    """Keep one benchmark writer on a workdir, including across worktrees."""
    import fcntl

    lock_path = workdir.resolve() / ".benchmark.lock"
    inherited_lock = os.environ.get(_LOCK_ENV)
    inherited_fd = os.environ.get(_LOCK_FD_ENV)
    if inherited_lock == str(lock_path) and inherited_fd:
        try:
            descriptor = int(inherited_fd)
            descriptor_stat = os.fstat(descriptor)
            lock_stat = lock_path.stat()
        except (OSError, ValueError):
            pass
        else:
            if (descriptor_stat.st_dev, descriptor_stat.st_ino) == (
                lock_stat.st_dev,
                lock_stat.st_ino,
            ):
                yield
                return

    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with lock_path.open("a+", encoding="utf-8") as handle:
        try:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            handle.seek(0)
            owner = handle.read().strip() or "owner metadata unavailable"
            raise SystemExit(
                f"benchmark workdir is already in use: {lock_path}\n{owner}"
            ) from exc

        owner = {
            "pid": os.getpid(),
            "command": command,
            "repo": str(REPO),
            "started_at": datetime.now(timezone.utc).isoformat(),
        }
        handle.seek(0)
        handle.truncate()
        handle.write(json.dumps(owner, ensure_ascii=False) + "\n")
        handle.flush()
        previous = os.environ.get(_LOCK_ENV)
        previous_fd = os.environ.get(_LOCK_FD_ENV)
        os.environ[_LOCK_ENV] = str(lock_path)
        os.environ[_LOCK_FD_ENV] = str(handle.fileno())
        try:
            yield
        finally:
            if previous is None:
                os.environ.pop(_LOCK_ENV, None)
            else:
                os.environ[_LOCK_ENV] = previous
            if previous_fd is None:
                os.environ.pop(_LOCK_FD_ENV, None)
            else:
                os.environ[_LOCK_FD_ENV] = previous_fd
            handle.seek(0)
            handle.truncate()
            handle.flush()
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


@functools.lru_cache(maxsize=1)
def _git_commit() -> str:
    try:
        return subprocess.check_output(
            ["git", "rev-parse", "HEAD"],
            cwd=REPO,
            text=True,
            stderr=subprocess.DEVNULL,
        ).strip()
    except (OSError, subprocess.SubprocessError):
        return "unknown"


def _evaluation_cache_matches(
    report: dict,
    source: Path,
    translated: Path,
    timing_path: Path | None = None,
) -> bool:
    if not source.exists() or not translated.exists():
        return False
    source_sha256 = _sha256_file(source)
    translated_sha256 = _sha256_file(translated)
    matches = (
        report.get("schema_version") == 2
        and report.get("source_sha256") == source_sha256
        and report.get("translated_sha256") == translated_sha256
        and report.get("qa_fingerprint") == _qa_fingerprint()
    )
    if not matches or timing_path is None:
        return matches
    provenance = _translation_provenance(
        timing_path,
        source_sha256,
        translated_sha256,
    )
    return all(report.get(key) == value for key, value in provenance.items())


def _translation_provenance(
    timing_path: Path,
    source_sha256: str,
    translated_sha256: str,
) -> dict:
    try:
        timing = json.loads(timing_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        timing = {}
    if (
        timing.get("schema_version") != 2
        or timing.get("source_sha256") != source_sha256
        or timing.get("translated_sha256") != translated_sha256
    ):
        return {
            "engine_fingerprint": "unknown",
            "engine_commit": "unknown",
            "translation_model": "unknown",
            "font_fingerprint": "unknown",
        }
    return {
        "engine_fingerprint": str(timing.get("engine_fingerprint") or "unknown"),
        "engine_commit": str(timing.get("engine_commit") or "unknown"),
        "translation_model": str(timing.get("translation_model") or "unknown"),
        "font_fingerprint": str(timing.get("font_fingerprint") or "unknown"),
    }


def _translation_cache_matches(
    timing_path: Path,
    source: Path,
    translated: Path,
    model: str,
) -> bool:
    if not source.exists() or not translated.exists():
        return False
    provenance = _translation_provenance(
        timing_path,
        _sha256_file(source),
        _sha256_file(translated),
    )
    return (
        provenance["engine_fingerprint"] == _engine_fingerprint()
        and provenance["translation_model"] == model
        and provenance["font_fingerprint"] == _font_fingerprint()
    )


def _block_cache_path(translations_dir: Path, paper_id: str, model: str) -> Path:
    digest = hashlib.sha256()
    digest.update(model.encode("utf-8"))
    digest.update(b"\0")
    digest.update(_translation_fingerprint().encode("ascii"))
    namespace = digest.hexdigest()[:16]
    return translations_dir / f"{paper_id}.translation-cache.{namespace}.jsonl"


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
            _atomic_write_bytes(pdf_path, pdf_bytes)
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
            _atomic_write_text(
                meta_path,
                json.dumps(meta, ensure_ascii=False, indent=2) + "\n",
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

    if len(entries) > 1 or getattr(args, "isolate", False):
        # One subprocess per paper: the engine accumulates memory across
        # papers in-process (long 40+ paper runs segfault around paper ~34),
        # so bound each translation to a fresh interpreter.
        failures = 0
        for entry in entries:
            cmd = [
                sys.executable,
                str(Path(__file__).resolve()),
                "translate",
                "--only",
                entry.id,
                "--workdir",
                str(workdir),
            ]
            if args.force:
                cmd.append("--force")
            inherited_fd = os.environ.get(_LOCK_FD_ENV)
            pass_fds = (int(inherited_fd),) if inherited_fd else ()
            rc = subprocess.call(cmd, pass_fds=pass_fds)
            if rc != 0:
                failures += 1
                print(
                    f"translate {entry.id}: subprocess exited rc={rc}",
                    file=sys.stderr,
                )
        return 1 if failures else 0

    from pdf_zh_translator.pdf_layout import translate_pdf
    from pdf_zh_translator.translators import CachedTranslator, VendorTranslator

    paths = _paths(workdir)
    paths["translations"].mkdir(parents=True, exist_ok=True)
    model = _env("DEEPSEEK_MODEL", "deepseek-v4-pro")
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
            if _translation_cache_matches(timing_path, source, mono, model):
                print(f"translate {entry.id}: cached")
                continue
            print(f"translate {entry.id}: stale translation, retranslating")
        vendor = VendorTranslator(
            api_url=_env("DEEPSEEK_API_URL", "https://api.deepseek.com"),
            api_key=api_key,
            mode="deepseek",
            model=model,
            source_lang="en",
            target_lang="zh",
            progress=False,
        )
        translator = CachedTranslator(
            vendor,
            _block_cache_path(paths["translations"], entry.id, model),
        )
        engine_fingerprint = _current_engine_fingerprint()
        font_fingerprint = _current_font_fingerprint()
        handle, temporary_name = tempfile.mkstemp(
            dir=paths["translations"],
            prefix=f".{entry.id}-mono.",
            suffix=".pdf",
        )
        os.close(handle)
        temporary_mono = Path(temporary_name)
        temporary_mono.unlink()
        started = time.time()
        try:
            report = translate_pdf(
                input_pdf=source,
                output_pdf=temporary_mono,
                translator=translator,
                preserve_graphics_text=True,
            )
            final_engine_fingerprint = _current_engine_fingerprint()
            final_font_fingerprint = _current_font_fingerprint()
            if final_engine_fingerprint != engine_fingerprint:
                raise RuntimeError(
                    "translation engine changed during translation; "
                    "refusing mixed-version artifact"
                )
            if final_font_fingerprint != font_fingerprint:
                raise RuntimeError(
                    "font pack changed during translation; refusing mixed-layout artifact"
                )
            translated_sha256 = _sha256_file(temporary_mono)
            os.replace(temporary_mono, mono)
        except Exception as exc:
            failures += 1
            print(f"translate {entry.id}: FAILED {exc}", file=sys.stderr)
            continue
        finally:
            temporary_mono.unlink(missing_ok=True)
        elapsed = time.time() - started
        _atomic_write_text(
            timing_path,
            json.dumps(
                {
                    "schema_version": 2,
                    "id": entry.id,
                    "seconds": round(elapsed, 1),
                    "pages": report.page_count,
                    "translated_blocks": report.translated_blocks,
                    "finished_at": datetime.now(timezone.utc).isoformat(),
                    "source_sha256": _sha256_file(source),
                    "translated_sha256": translated_sha256,
                    "engine_fingerprint": engine_fingerprint,
                    "engine_commit": _git_commit(),
                    "translation_model": model,
                    "font_fingerprint": font_fingerprint,
                },
                ensure_ascii=False,
                indent=2,
            )
            + "\n",
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
            try:
                cached = json.loads(report_path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                cached = {}
            if _evaluation_cache_matches(
                cached,
                source,
                mono,
                paths["translations"] / f"{entry.id}.timing.json",
            ):
                print(f"evaluate {entry.id}: cached")
                continue
            print(f"evaluate {entry.id}: stale report, reevaluating")
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
        source_sha256 = _sha256_file(source)
        translated_sha256 = _sha256_file(mono)
        payload = {
            "schema_version": 2,
            "id": entry.id,
            "arxiv_id": entry.arxiv_id,
            "title": entry.title,
            "tags": list(entry.tags),
            "evaluated_at": datetime.now(timezone.utc).isoformat(),
            "source_sha256": source_sha256,
            "translated_sha256": translated_sha256,
            "qa_fingerprint": _qa_fingerprint(),
            "qa_commit": _git_commit(),
            **_translation_provenance(
                paths["translations"] / f"{entry.id}.timing.json",
                source_sha256,
                translated_sha256,
            ),
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
        _atomic_write_text(
            report_path,
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
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
    _atomic_write_text(report_md, "\n".join(lines) + "\n")

    for entry, report, meta in rows:
        source = paths["papers"] / f"{entry.id}.pdf"
        mono = paths["translations"] / f"{entry.id}-mono.pdf"
        previews = []
        if (
            not args.no_previews
            and bool(meta.get("showcase_ok"))
            and source.exists()
            and mono.exists()
        ):
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
                "qa_fingerprint": report.get("qa_fingerprint", "unknown"),
                "qa_commit": report.get("qa_commit", "unknown"),
                "engine_fingerprint": report.get("engine_fingerprint", "unknown"),
                "engine_commit": report.get("engine_commit", "unknown"),
                "translation_model": report.get("translation_model", "unknown"),
                "font_fingerprint": report.get("font_fingerprint", "unknown"),
                "previews": previews,
            }
        )
    payload = {
        "schema_version": 2,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "papers": showcase,
    }
    comparison = _baseline_comparison(workdir, rows)
    if comparison:
        payload["comparison"] = comparison
    _atomic_write_text(
        workdir / "showcase.json",
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
    )
    print(f"wrote {report_md} and showcase.json ({len(showcase)} papers)")
    return 0


def _baseline_comparison(workdir: Path, rows: list) -> dict | None:
    """Aggregate current vs pre-fix (reports_baseline) evaluation totals.

    Scoped to the papers present in BOTH evaluations, so growing the
    benchmark set never skews the improvement figures.
    """
    baseline_dir = workdir / "reports_baseline"
    if not baseline_dir.is_dir():
        return None
    baseline_reports = {
        path.stem: json.loads(path.read_text(encoding="utf-8"))
        for path in sorted(baseline_dir.glob("*.json"))
    }
    current_reports = {entry.id: report for entry, report, _meta in rows}
    shared = sorted(set(baseline_reports) & set(current_reports))
    if not shared:
        return None

    def totals(reports: list) -> dict:
        by_code: dict[str, int] = {}
        for report in reports:
            for code, count in report.get("issues_by_code", {}).items():
                by_code[code] = by_code.get(code, 0) + count
        return {
            "error_count": sum(report.get("error_count", 0) for report in reports),
            "strict_pass": sum(1 for report in reports if report.get("strict_pass")),
            "legacy_pass": sum(1 for report in reports if report.get("legacy_pass")),
            "issues_by_code": dict(sorted(by_code.items())),
        }

    return {
        "scope": f"{len(shared)} papers evaluated in both runs",
        "paper_ids": shared,
        "baseline": totals([baseline_reports[pid] for pid in shared]),
        "current": totals([current_reports[pid] for pid in shared]),
    }


# ---------------------------------------------------------------------------
# release gate
# ---------------------------------------------------------------------------


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def cmd_gate(entries: list[Entry], workdir: Path, args) -> int:
    """Fail unless benchmark evidence meets the open-source release policy."""
    _load_env_file()
    paths = _paths(workdir)
    min_evaluated = max(1, int(args.min_evaluated))
    min_strict = max(1, int(args.min_strict_passes))
    require_all_axes = bool(getattr(args, "require_all_axes", True))
    baseline_dir = getattr(args, "baseline_reports", None)
    baseline_dir = Path(baseline_dir) if baseline_dir else None

    reports: dict[str, dict] = {}
    missing_reports: list[str] = []
    provenance_errors: list[str] = []
    regressions: list[dict] = []
    covered_axes: set[str] = set()
    for entry in entries:
        report_path = paths["reports"] / f"{entry.id}.json"
        if not report_path.exists():
            missing_reports.append(entry.id)
            continue
        report = json.loads(report_path.read_text(encoding="utf-8"))
        reports[entry.id] = report
        covered_axes.update(entry.tags)

        source_path = paths["papers"] / f"{entry.id}.pdf"
        translated_path = paths["translations"] / f"{entry.id}-mono.pdf"
        meta_path = paths["meta"] / f"{entry.id}.json"
        if not source_path.exists() or not meta_path.exists():
            provenance_errors.append(f"{entry.id}: source or metadata missing")
        else:
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
            expected_hash = str(meta.get("sha256", ""))
            if not expected_hash or _sha256_file(source_path) != expected_hash:
                provenance_errors.append(f"{entry.id}: source SHA256 mismatch")
            license_url = str(meta.get("license", "unknown"))
            if license_url == "unknown":
                provenance_errors.append(f"{entry.id}: license unknown")
            expected_showcase = "creativecommons.org" in license_url
            if bool(meta.get("showcase_ok")) != expected_showcase:
                provenance_errors.append(f"{entry.id}: showcase license flag mismatch")
        if not _evaluation_cache_matches(
            report,
            source_path,
            translated_path,
            paths["translations"] / f"{entry.id}.timing.json",
        ):
            provenance_errors.append(f"{entry.id}: report provenance is stale")
        if (
            report.get("engine_fingerprint") in (None, "", "unknown")
            or report.get("engine_commit") in (None, "", "unknown")
        ):
            provenance_errors.append(f"{entry.id}: translation engine provenance missing")
        elif report.get("engine_fingerprint") != _engine_fingerprint():
            provenance_errors.append(f"{entry.id}: translation engine is stale")
        if report.get("font_fingerprint") != _font_fingerprint():
            provenance_errors.append(f"{entry.id}: translation font pack is stale")
        if report.get("translation_model") != _env(
            "DEEPSEEK_MODEL", "deepseek-v4-pro"
        ):
            provenance_errors.append(f"{entry.id}: translation model is stale")

        if baseline_dir:
            baseline_path = baseline_dir / f"{entry.id}.json"
            if baseline_path.exists():
                baseline = json.loads(baseline_path.read_text(encoding="utf-8"))
                previous = int(baseline.get("error_count", 0))
                current = int(report.get("error_count", 0))
                if current > previous:
                    regressions.append(
                        {
                            "id": entry.id,
                            "baseline_errors": previous,
                            "errors": current,
                        }
                    )

    evaluated = len(reports)
    strict_passes = sum(bool(report.get("strict_pass")) for report in reports.values())
    manifest_axes = set(
        json.loads(MANIFEST.read_text(encoding="utf-8"))["layout_axes"]
    )
    missing_axes = sorted(manifest_axes - covered_axes) if require_all_axes else []
    failures: list[str] = []
    if evaluated < min_evaluated:
        failures.append(f"evaluated {evaluated} papers; require at least {min_evaluated}")
    if strict_passes < min_strict:
        failures.append(f"strict passes {strict_passes}; require at least {min_strict}")
    if missing_axes:
        failures.append(f"layout axes not covered: {', '.join(missing_axes)}")
    if provenance_errors:
        failures.append(f"artifact provenance errors: {len(provenance_errors)}")
    if regressions:
        failures.append(f"papers regressed against baseline: {len(regressions)}")

    result = {
        "schema_version": 2,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "passed": not failures,
        "minimum_evaluated": min_evaluated,
        "minimum_strict_passes": min_strict,
        "evaluated": evaluated,
        "strict_passes": strict_passes,
        "covered_layout_axes": sorted(covered_axes),
        "missing_layout_axes": missing_axes,
        "missing_reports": missing_reports,
        "provenance_errors": provenance_errors,
        "regressions": regressions,
        "failures": failures,
    }
    output = workdir / "quality-gate.json"
    _atomic_write_text(
        output,
        json.dumps(result, ensure_ascii=False, indent=2) + "\n",
    )
    status = "PASS" if result["passed"] else "FAIL"
    print(
        f"quality gate {status}: evaluated={evaluated} strict={strict_passes} "
        f"failures={len(failures)} ({output})"
    )
    for failure in failures:
        print(f"- {failure}", file=sys.stderr)
    return 0 if result["passed"] else 1


# ---------------------------------------------------------------------------


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "command", choices=["fetch", "translate", "evaluate", "report", "gate"]
    )
    parser.add_argument("--only", help="comma-separated paper ids")
    parser.add_argument("--workdir", type=Path, default=DEFAULT_WORKDIR)
    parser.add_argument("--force", action="store_true", help="redo cached steps")
    parser.add_argument(
        "--isolate",
        action="store_true",
        help="isolate even a single-paper translation in a fresh subprocess",
    )
    parser.add_argument(
        "--no-previews", action="store_true", help="skip preview rendering in report"
    )
    parser.add_argument("--min-evaluated", type=int, default=20)
    parser.add_argument("--min-strict-passes", type=int, default=20)
    parser.add_argument("--baseline-reports", type=Path)
    parser.add_argument(
        "--skip-axis-coverage",
        action="store_false",
        dest="require_all_axes",
        help="do not require every manifest layout axis in evaluated papers",
    )
    args = parser.parse_args()

    entries = _load_entries(args.only)
    args.workdir.mkdir(parents=True, exist_ok=True)
    handler = {
        "fetch": cmd_fetch,
        "translate": cmd_translate,
        "evaluate": cmd_evaluate,
        "report": cmd_report,
        "gate": cmd_gate,
    }[args.command]
    with _benchmark_workdir_lock(args.workdir, args.command):
        return handler(entries, args.workdir, args)


if __name__ == "__main__":
    sys.exit(main())
