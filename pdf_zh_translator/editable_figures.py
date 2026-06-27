"""Editable-PPT provenance for paper figures.

The project treats ``image-to-editable-ppt`` as the only accepted conversion
path for figure-to-PPT assets. This module does not reconstruct slides itself;
it calls or verifies ``editppt`` runtime artifacts and records provenance that
can be audited later.
"""

from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

SKILL_NAME = "image-to-editable-ppt"
SKILL_SOURCE_URL = "https://github.com/ningzimu/image-to-editable-ppt-skill"
MANIFEST_FILENAME = "editable_figure_manifest.json"
REQUIRED_RUN_FILES = ("deck_manifest.json", "page_jobs.json")


@dataclass(frozen=True)
class EditableFigureAuditResult:
    checked: int
    passed: int
    failed: int
    issues: list[str]

    @property
    def ok(self) -> bool:
        return self.failed == 0 and self.checked > 0


def editppt_available() -> bool:
    """Return whether the required editppt runtime is available."""
    return shutil.which("editppt") is not None


def prepare_editable_figure_run(
    source_image: Path,
    output_root: Path,
    *,
    figure_id: str | None = None,
    no_text_hints: bool = True,
) -> Path:
    """Create an editppt run directory for a figure image.

    This is the only prepare path used by the project for editable figure PPTs.
    The actual object reconstruction still must follow the skill workflow:
    dispatch/reconstruct, record, and finalize through ``editppt``.
    """
    if not editppt_available():
        raise RuntimeError("editppt CLI is required for image-to-editable-ppt conversion")
    if not source_image.exists():
        raise FileNotFoundError(str(source_image))

    run_dir = output_root / _safe_id(figure_id or source_image.stem) / "editppt-run"
    command = [
        "editppt",
        "prepare",
        str(source_image),
        "--job-dir",
        str(run_dir),
    ]
    if no_text_hints:
        command.append("--no-text-hints")
    subprocess.run(command, check=True)
    return run_dir


def register_editable_figure(
    *,
    figure_id: str,
    source_image: Path,
    editppt_run: Path,
    output_dir: Path,
    pptx_path: Path | None = None,
) -> dict[str, Any]:
    """Register an editppt-finalized figure PPTX and write provenance.

    Registration fails unless the run has the core editppt files, the final
    PPTX exists, and all pages have been accepted by ``editppt run finalize``.
    """
    if not source_image.exists():
        raise FileNotFoundError(str(source_image))
    run_info = validate_editppt_run(editppt_run, pptx_path=pptx_path)
    output_dir.mkdir(parents=True, exist_ok=True)

    final_pptx = run_info["pptx_path"]
    manifest = {
        "schema_version": 1,
        "figure_id": figure_id,
        "generated_at": _utc_now(),
        "skill": SKILL_NAME,
        "skill_source": SKILL_SOURCE_URL,
        "runtime": "editppt",
        "source_image": str(source_image),
        "source_sha256": sha256_file(source_image),
        "editppt_run": str(editppt_run),
        "editppt_output": str(final_pptx),
        "pptx_sha256": sha256_file(final_pptx),
        "deck_manifest": str(run_info["deck_manifest_path"]),
        "page_jobs": str(run_info["page_jobs_path"]),
        "validation": str(run_info["validation_path"]) if run_info.get("validation_path") else "",
        "page_count": run_info["page_count"],
        "status": "accepted",
    }
    manifest_path = output_dir / MANIFEST_FILENAME
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return manifest


def validate_editppt_run(editppt_run: Path, *, pptx_path: Path | None = None) -> dict[str, Any]:
    """Validate that a run is a completed editppt finalization."""
    run_dir = editppt_run.resolve()
    deck_path = run_dir / "deck_manifest.json"
    jobs_path = run_dir / "page_jobs.json"
    missing = [name for name in REQUIRED_RUN_FILES if not (run_dir / name).exists()]
    if missing:
        raise ValueError("editppt run is missing required file(s): " + ", ".join(missing))

    deck = _read_json(deck_path)
    jobs = _read_json(jobs_path)
    pages = jobs.get("pages", [])
    if not pages:
        raise ValueError("editppt page_jobs.json does not list pages")
    not_accepted = [
        str(page.get("page_id", "unknown"))
        for page in pages
        if page.get("status") != "accepted" or page.get("accepted") is not True
    ]
    if not_accepted:
        raise ValueError(
            "editppt run is not finalized; unaccepted page(s): " + ", ".join(not_accepted)
        )
    if jobs.get("run_status") != "complete":
        raise ValueError("editppt run_status must be complete")
    if not deck.get("completed_at"):
        raise ValueError("editppt deck_manifest.json is missing completed_at")

    final_pptx = pptx_path or _resolve_run_path(run_dir, str(deck.get("output", "")))
    if final_pptx is None or not final_pptx.exists() or final_pptx.suffix.lower() != ".pptx":
        raise ValueError("editppt final PPTX is missing")
    validation_path = final_pptx.parent / "validation.json"
    if validation_path.exists():
        validation = _read_json(validation_path)
        if validation.get("passed") is not True:
            raise ValueError("editppt final validation did not pass")

    return {
        "deck_manifest_path": deck_path,
        "page_jobs_path": jobs_path,
        "pptx_path": final_pptx,
        "validation_path": validation_path if validation_path.exists() else None,
        "page_count": len(pages),
    }


def audit_editable_figure_manifests(root: Path) -> EditableFigureAuditResult:
    """Audit all editable figure manifests below ``root``."""
    manifests = sorted(root.rglob(MANIFEST_FILENAME)) if root.exists() else []
    issues: list[str] = []
    passed = 0
    for manifest_path in manifests:
        try:
            _audit_manifest(manifest_path)
        except Exception as exc:
            issues.append(f"{manifest_path}: {exc}")
        else:
            passed += 1
    return EditableFigureAuditResult(
        checked=len(manifests),
        passed=passed,
        failed=len(manifests) - passed,
        issues=issues,
    )


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _audit_manifest(manifest_path: Path) -> None:
    manifest = _read_json(manifest_path)
    if manifest.get("skill") != SKILL_NAME:
        raise ValueError("manifest skill is not image-to-editable-ppt")
    if manifest.get("skill_source") != SKILL_SOURCE_URL:
        raise ValueError("manifest skill_source does not match required GitHub repository")
    source = Path(str(manifest.get("source_image", "")))
    pptx = Path(str(manifest.get("editppt_output", "")))
    run_dir = Path(str(manifest.get("editppt_run", "")))
    if not source.exists():
        raise ValueError("source_image is missing")
    if not pptx.exists():
        raise ValueError("editppt_output is missing")
    if manifest.get("source_sha256") != sha256_file(source):
        raise ValueError("source_image hash mismatch")
    if manifest.get("pptx_sha256") != sha256_file(pptx):
        raise ValueError("PPTX hash mismatch")
    validate_editppt_run(run_dir, pptx_path=pptx)


def _read_json(path: Path) -> dict[str, Any]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError(f"{path} must contain a JSON object")
    return data


def _resolve_run_path(run_dir: Path, value: str) -> Path | None:
    if not value:
        return None
    path = Path(value)
    if not path.is_absolute():
        path = run_dir / path
    return path


def _safe_id(value: str) -> str:
    cleaned = "".join(char if char.isalnum() or char in {"-", "_"} else "-" for char in value)
    return cleaned.strip("-_") or "figure"


def _utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
