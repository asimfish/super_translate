"""Bounded, supplier-free alternate renders for drafts rejected by layout QA."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from app.services.quality_agent import issue_fingerprint


@dataclass(frozen=True)
class RenderProfile:
    name: str
    label: str
    font_scale: float
    margin: float


RENDER_PROFILES = (
    RenderProfile("source_font", "恢复原文字号", 1.0, 0.8),
    RenderProfile("compact_spacing", "收紧文字框内边距", 0.92, 0.2),
)
_PROFILES = {profile.name: profile for profile in RENDER_PROFILES}
_LAYOUT_ERRORS = frozenset({
    "caption_overlap", "text_overlap", "text_visual_overlap", "raster_ink_overlap",
    "raster_heading_body_overlap", "font_size_drift", "font_role_size_mismatch",
})


class RepairCancelled(Exception):  # noqa: N818 - cancellation signal, not a failed repair
    """The owner cancelled the job during an isolated repair."""


@dataclass
class RepairOutcome:
    issues: list
    history: list[dict]


def _blocking_counts(issues: list) -> Counter:
    return Counter(
        (getattr(issue, "code", "unknown"), getattr(issue, "page", 0))
        for issue in issues if getattr(issue, "severity", "warning") == "error"
    )


def _create_dual(source: Path, mono: Path, dual: Path) -> None:
    from pdf_zh_translator.pdf_layout import create_dual_pdf

    create_dual_pdf(source, mono, dual)


def repair_cached_layout(
    input_path: Path,
    result: object,
    issues: list,
    *,
    verify: Callable,
    cancelled: Callable[[], bool],
    on_stage: Callable[[str], None],
    timeout: float = 300,
) -> RepairOutcome:
    """Try each distinct profile once; retain only verified, safer candidates.

    The cache identity comes from the actual translation worker, never a glob
    over other models or owners. Candidates stay separate from the draft until
    verified. A lower error total cannot compensate for a newly damaged formula,
    lost graphic, untranslated paragraph, or any new blocking issue on a page.
    """
    outcome = RepairOutcome(list(issues), [])
    if not any(
        getattr(issue, "severity", "warning") == "error"
        and getattr(issue, "code", "") in _LAYOUT_ERRORS for issue in issues
    ):
        return outcome
    cache_path = getattr(result, "repair_cache_path", None)
    mono_path = getattr(result, "mono_path", None)
    if cache_path is None or mono_path is None:
        return outcome
    if Path(mono_path).is_symlink():
        return outcome
    mono_path = Path(mono_path).resolve()
    cache_path = Path(cache_path)
    dual_path = getattr(result, "dual_path", None)
    if dual_path and (
        Path(dual_path).is_symlink() or Path(dual_path).resolve().parent != mono_path.parent
    ):
        return outcome
    if (cache_path.is_symlink() or not cache_path.is_file()
            or cache_path.resolve().parent != mono_path.parent):
        return outcome
    before = _blocking_counts(outcome.issues)
    with tempfile.TemporaryDirectory(prefix=".quality-repair-", dir=mono_path.parent) as work:
        work_dir = Path(work)
        # Copy the exact cache into the isolated staging directory. Failed
        # replay diagnostics must not modify an immutable model cache.
        staged_cache = work_dir / "translation-cache.jsonl"
        shutil.copyfile(cache_path, staged_cache)
        for index, profile in enumerate(RENDER_PROFILES, 1):
            if cancelled():
                raise RepairCancelled()
            on_stage(f"自动版面修复 {index}/{len(RENDER_PROFILES)}：{profile.label}")
            candidate = work_dir / f"{profile.name}.pdf"
            candidate_dual = work_dir / f"{profile.name}-dual.pdf"
            history = {"profile": profile.name, "accepted": False, "verified": False}
            try:
                _render_candidate({
                    "input_path": str(input_path.resolve()),
                    "output_path": str(candidate),
                    "dual_path": str(candidate_dual) if dual_path else None,
                    "cache_path": str(staged_cache),
                    "profile": profile.name,
                    "preserve_graphics_text": bool(
                        getattr(result, "repair_preserve_graphics_text", True)
                    ),
                }, cancelled=cancelled, timeout=timeout)
                if cancelled():
                    raise RepairCancelled()
                candidate_issues = list(verify(input_path, candidate))
                after = _blocking_counts(candidate_issues)
                history.update(
                    error_count=sum(after.values()), status="rejected", verified=True,
                    warning_count=len(candidate_issues) - sum(after.values()),
                    issue_count=len(candidate_issues),
                    issue_codes=sorted({getattr(item, "code", "unknown")
                                        for item in candidate_issues}),
                    issue_fingerprint=issue_fingerprint(candidate_issues),
                )
                # Counter subtraction exposes every new/increased page/code.
                if sum(after.values()) < sum(before.values()) and not (after - before):
                    if dual_path and not candidate_dual.is_file():
                        raise RuntimeError("Cached layout repair did not generate its dual PDF")
                    if cancelled():
                        raise RepairCancelled()
                    # Roll back either file if the pair cannot be installed.
                    original_mono = work_dir / "original-mono.pdf"
                    shutil.copyfile(mono_path, original_mono)
                    original_dual = work_dir / "original-dual.pdf"
                    if dual_path:
                        shutil.copyfile(dual_path, original_dual)
                    try:
                        candidate.replace(mono_path)
                        if dual_path:
                            candidate_dual.replace(dual_path)
                    except Exception:
                        original_mono.replace(mono_path)
                        if dual_path:
                            original_dual.replace(dual_path)
                        raise
                    outcome.issues = candidate_issues
                    before = after
                    history.update(accepted=True, status="improved")
            except RepairCancelled:
                raise
            except Exception:
                # A failed render/cache miss cannot discard the usable draft.
                history["status"] = "candidate_failed"
            outcome.history.append(history)
            if not before:
                break
    return outcome


def _render_candidate(spec: dict, *, cancelled: Callable[[], bool], timeout: float) -> None:
    """Run native code with a hard deadline and owner cancellation polling."""
    spec_path = Path(spec["output_path"]).with_suffix(".json")
    spec_path.write_text(json.dumps(spec), encoding="utf-8")
    deadline = time.monotonic() + max(1.0, min(1800.0, float(timeout)))
    proc = subprocess.Popen(
        [sys.executable, "-m", "app.services.quality_repair", str(spec_path)],
        cwd=str(Path(__file__).resolve().parents[2]),
        # Keep native crashes and cache misses in the existing server log.
        stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
        env=dict(os.environ, PYTHONFAULTHANDLER="1"),
    )
    try:
        while proc.poll() is None:
            if cancelled():
                raise RepairCancelled()
            if time.monotonic() >= deadline:
                raise TimeoutError("Cached layout repair exceeded its deadline")
            time.sleep(0.25)
        if proc.returncode != 0 or not Path(spec["output_path"]).is_file():
            raise RuntimeError("Cached layout repair failed")
    finally:
        if proc.poll() is None:
            proc.kill()
        proc.wait()
        spec_path.unlink(missing_ok=True)


def main() -> None:
    """Strict cache replay: no vendor is constructed or called in this process."""
    from pdf_zh_translator.pdf_layout import translate_pdf
    from pdf_zh_translator.translators import CacheOnlyTranslator

    spec = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
    profile = _PROFILES[spec["profile"]]
    translator = CacheOnlyTranslator(Path(spec["cache_path"]))
    # Older installed engines accept only the cache path in the constructor;
    # their strict lookup is still usable for the live segmented model cache.
    translator.supports_source_style_segments = True
    translate_pdf(
        Path(spec["input_path"]), Path(spec["output_path"]),
        translator,
        font_scale=profile.font_scale, margin=profile.margin,
        preserve_graphics_text=spec["preserve_graphics_text"], skip_overflow=False,
    )
    if spec.get("dual_path"):
        _create_dual(
            Path(spec["input_path"]), Path(spec["output_path"]), Path(spec["dual_path"]),
        )


if __name__ == "__main__":
    main()
