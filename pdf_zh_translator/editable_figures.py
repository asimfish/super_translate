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
SOURCE_FIGURES_MANIFEST_FILENAME = "figure_sources_manifest.json"
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


def extract_pdf_figures(
    pdf_path: Path,
    output_root: Path,
    *,
    paper_id: str | None = None,
    min_width: float = 24.0,
    min_height: float = 24.0,
    min_area: float = 1000.0,
    max_figures: int | None = None,
    dpi: int = 200,
) -> dict[str, Any]:
    """Extract rendered PDF figure regions into per-figure source folders.

    The output images are only the source inputs for ``image-to-editable-ppt``.
    They are not accepted editable assets until an editppt run is finalized and
    registered with ``register_editable_figure``.
    """
    if not pdf_path.exists():
        raise FileNotFoundError(str(pdf_path))
    if dpi <= 0:
        raise ValueError("dpi must be positive")

    try:
        import fitz
    except ImportError as exc:
        raise RuntimeError("PyMuPDF is required for PDF figure extraction") from exc

    from .pdf_layout import bbox_area, graphic_regions_for_page

    safe_paper_id = _safe_id(paper_id or pdf_path.stem)
    paper_dir = output_root / safe_paper_id
    figures_root = paper_dir / "figures"
    figures_root.mkdir(parents=True, exist_ok=True)
    pdf_hash = sha256_file(pdf_path)

    extracted: list[dict[str, Any]] = []
    document = fitz.open(str(pdf_path))
    try:
        matrix = fitz.Matrix(dpi / 72.0, dpi / 72.0)
        for page_index in range(document.page_count):
            page = document[page_index]
            candidates = _filtered_figure_regions(
                graphic_regions_for_page(page),
                min_width=min_width,
                min_height=min_height,
                min_area=min_area,
            )
            for bbox in candidates:
                figure_number = len(extracted) + 1
                if max_figures is not None and figure_number > max_figures:
                    break
                figure_id = f"{safe_paper_id}_p{page_index + 1:03d}_fig{figure_number:03d}"
                figure_dir = figures_root / figure_id
                figure_dir.mkdir(parents=True, exist_ok=True)
                image_path = figure_dir / "source.png"
                pixmap = page.get_pixmap(matrix=matrix, clip=fitz.Rect(bbox), alpha=False)
                pixmap.save(str(image_path))
                extracted.append(
                    {
                        "figure_id": figure_id,
                        "paper_id": safe_paper_id,
                        "source_pdf": str(pdf_path),
                        "source_pdf_sha256": pdf_hash,
                        "page": page_index + 1,
                        "bbox": [round(float(value), 3) for value in bbox],
                        "kind": "graphic-region",
                        "image_path": str(image_path),
                        "image_sha256": sha256_file(image_path),
                        "width": pixmap.width,
                        "height": pixmap.height,
                        "area": round(bbox_area(bbox), 3),
                        "status": "source-extracted",
                    }
                )
            if max_figures is not None and len(extracted) >= max_figures:
                break
    finally:
        document.close()

    manifest = {
        "schema_version": 1,
        "paper_id": safe_paper_id,
        "source_pdf": str(pdf_path),
        "source_pdf_sha256": pdf_hash,
        "generated_at": _utc_now(),
        "skill": SKILL_NAME,
        "skill_source": SKILL_SOURCE_URL,
        "status": "source-extracted",
        "figure_count": len(extracted),
        "figures": extracted,
    }
    manifest_path = paper_dir / SOURCE_FIGURES_MANIFEST_FILENAME
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return manifest


def prepare_extracted_figures(
    source_manifest: Path,
    *,
    no_text_hints: bool = True,
    limit: int | None = None,
) -> dict[str, Any]:
    """Run ``editppt prepare`` for every extracted figure in a source manifest."""
    manifest = _read_json(source_manifest)
    figures = manifest.get("figures", [])
    if not isinstance(figures, list):
        raise ValueError("figure source manifest must contain a figures list")

    prepared = 0
    for figure in figures:
        if limit is not None and prepared >= limit:
            break
        if not isinstance(figure, dict):
            continue
        source_image = Path(str(figure.get("image_path", "")))
        figure_id = str(figure.get("figure_id", source_image.stem))
        output_root = source_image.parent.parent
        run_dir = prepare_editable_figure_run(
            source_image,
            output_root,
            figure_id=figure_id,
            no_text_hints=no_text_hints,
        )
        figure["editppt_run"] = str(run_dir)
        figure["prepared_at"] = _utc_now()
        figure["status"] = "prepared"
        prepared += 1

    manifest["prepared_count"] = sum(
        1 for figure in figures if isinstance(figure, dict) and figure.get("status") == "prepared"
    )
    manifest["status"] = "prepared" if manifest["prepared_count"] else manifest.get("status")
    manifest["updated_at"] = _utc_now()
    source_manifest.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return manifest


def register_finalized_figures(
    source_manifest: Path,
    *,
    limit: int | None = None,
) -> dict[str, Any]:
    """Register finalized editppt runs listed in a source manifest."""
    manifest = _read_json(source_manifest)
    figures = manifest.get("figures", [])
    if not isinstance(figures, list):
        raise ValueError("figure source manifest must contain a figures list")

    registered = 0
    issues: list[str] = []
    for figure in figures:
        if limit is not None and registered >= limit:
            break
        if not isinstance(figure, dict):
            issues.append("Invalid figure entry in source manifest")
            continue
        figure_id = str(figure.get("figure_id", "unknown"))
        try:
            source_image = Path(str(figure.get("image_path", "")))
            editppt_run_value = figure.get("editppt_run") or source_image.parent / "editppt-run"
            editppt_run = Path(str(editppt_run_value))
            registered_manifest = register_editable_figure(
                figure_id=figure_id,
                source_image=source_image,
                editppt_run=editppt_run,
                output_dir=source_image.parent,
            )
        except Exception as exc:
            issues.append(f"{figure_id}: {exc}")
            continue
        figure["status"] = "accepted"
        figure["registered_at"] = _utc_now()
        figure["editable_manifest"] = str(source_image.parent / MANIFEST_FILENAME)
        figure["editppt_output"] = str(registered_manifest["editppt_output"])
        figure["pptx_sha256"] = registered_manifest["pptx_sha256"]
        registered += 1

    manifest["registered_count"] = sum(
        1 for figure in figures if isinstance(figure, dict) and figure.get("status") == "accepted"
    )
    manifest["registration_issues"] = issues
    manifest["status"] = "accepted" if manifest["registered_count"] == len(figures) else "prepared"
    manifest["updated_at"] = _utc_now()
    source_manifest.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    manifest["_batch_registered"] = registered
    manifest["_batch_failed"] = len(issues)
    return manifest


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


def audit_figure_source_manifest(
    source_manifest: Path,
    *,
    require_prepared: bool = False,
    require_registered: bool = False,
) -> EditableFigureAuditResult:
    """Audit source-extracted figures and their editppt/register status."""
    try:
        manifest = _read_json(source_manifest)
    except Exception as exc:
        return EditableFigureAuditResult(checked=1, passed=0, failed=1, issues=[str(exc)])

    if manifest.get("skill") not in {None, SKILL_NAME}:
        return EditableFigureAuditResult(
            checked=1,
            passed=0,
            failed=1,
            issues=["source manifest skill is not image-to-editable-ppt"],
        )
    if manifest.get("skill_source") not in {None, SKILL_SOURCE_URL}:
        return EditableFigureAuditResult(
            checked=1,
            passed=0,
            failed=1,
            issues=["source manifest skill_source does not match required GitHub repository"],
        )
    figures = manifest.get("figures", [])
    if not isinstance(figures, list):
        return EditableFigureAuditResult(
            checked=1,
            passed=0,
            failed=1,
            issues=["source manifest must contain a figures list"],
        )

    source_pdf = Path(str(manifest.get("source_pdf", "")))
    source_pdf_hash = str(manifest.get("source_pdf_sha256", ""))
    shared_issue = ""
    if source_pdf_hash:
        if not source_pdf.exists():
            shared_issue = "source_pdf is missing"
        elif sha256_file(source_pdf) != source_pdf_hash:
            shared_issue = "source_pdf hash mismatch"

    checked = 0
    passed = 0
    issues: list[str] = []
    for figure in figures:
        checked += 1
        try:
            if shared_issue:
                raise ValueError(shared_issue)
            _audit_source_figure(
                figure,
                require_prepared=require_prepared,
                require_registered=require_registered,
            )
        except Exception as exc:
            figure_id = (
                str(figure.get("figure_id", "unknown"))
                if isinstance(figure, dict)
                else "unknown"
            )
            issues.append(f"{figure_id}: {exc}")
        else:
            passed += 1
    return EditableFigureAuditResult(
        checked=checked,
        passed=passed,
        failed=checked - passed,
        issues=issues,
    )


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _filtered_figure_regions(
    regions: list[tuple[float, float, float, float]],
    *,
    min_width: float,
    min_height: float,
    min_area: float,
) -> list[tuple[float, float, float, float]]:
    from .pdf_layout import bbox_area

    filtered: list[tuple[float, float, float, float]] = []
    for bbox in regions:
        width = bbox[2] - bbox[0]
        height = bbox[3] - bbox[1]
        if width < min_width or height < min_height:
            continue
        if bbox_area(bbox) < min_area:
            continue
        filtered.append(bbox)
    return filtered


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


def _audit_source_figure(
    figure: object,
    *,
    require_prepared: bool,
    require_registered: bool,
) -> None:
    if not isinstance(figure, dict):
        raise ValueError("figure entry must be a JSON object")
    source = Path(str(figure.get("image_path", "")))
    if not source.exists():
        raise ValueError("image_path is missing")
    expected_hash = str(figure.get("image_sha256", ""))
    if expected_hash and sha256_file(source) != expected_hash:
        raise ValueError("image_path hash mismatch")

    run_value = str(figure.get("editppt_run", ""))
    run_dir = Path(run_value) if run_value else source.parent / "editppt-run"
    if require_prepared and not run_dir.exists():
        raise ValueError("editppt_run is missing")
    if run_dir.exists():
        missing = [name for name in REQUIRED_RUN_FILES if not (run_dir / name).exists()]
        if missing:
            raise ValueError("editppt_run is missing required file(s): " + ", ".join(missing))

    manifest_value = str(figure.get("editable_manifest", ""))
    manifest_path = Path(manifest_value) if manifest_value else source.parent / MANIFEST_FILENAME
    if require_registered and not manifest_path.exists():
        raise ValueError("editable figure manifest is missing")
    if manifest_path.exists():
        _audit_manifest(manifest_path)


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
