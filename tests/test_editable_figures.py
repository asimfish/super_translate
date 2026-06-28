"""Tests for editable figure PPT provenance."""

import hashlib
import json
from pathlib import Path
from unittest.mock import patch

import pytest

from pdf_zh_translator.editable_figures import (
    SKILL_NAME,
    SKILL_SOURCE_URL,
    SOURCE_FIGURES_MANIFEST_FILENAME,
    audit_editable_figure_manifests,
    audit_figure_source_manifest,
    extract_pdf_figures,
    prepare_editable_figure_run,
    prepare_extracted_figures,
    register_editable_figure,
    register_finalized_figures,
    validate_editppt_run,
)


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _write_completed_editppt_run(root: Path) -> tuple[Path, Path]:
    run_dir = root / "editppt-run"
    final_dir = run_dir / "final"
    page_dir = run_dir / "pages" / "page_001"
    final_dir.mkdir(parents=True)
    page_dir.mkdir(parents=True)
    pptx = final_dir / "figure_edited.pptx"
    pptx.write_bytes(b"fake-pptx-for-provenance-tests")
    page_outputs = {
        "page_manifest": page_dir / "manifest.json",
        "imagegen_jobs": page_dir / "imagegen-jobs.json",
        "page_pptx": page_dir / "page.pptx",
        "preview": page_dir / "preview.png",
        "contact_sheet": page_dir / "split_assets_contact.png",
        "validation": page_dir / "validation.json",
        "page_result": page_dir / "page_result.json",
    }
    (page_dir / "source.png").write_bytes(b"source-page")
    (page_dir / "page_request.json").write_text(
        json.dumps(
            {
                "page_id": "page_001",
                "page_dir": "pages/page_001",
                "source": "pages/page_001/source.png",
                "slide": {"width": 10, "height": 7.5},
                "content_box": [0, 0, 100, 75],
            }
        ),
        encoding="utf-8",
    )
    page_outputs["page_manifest"].write_text(
        json.dumps(
            {
                "slide": {"width": 10, "height": 7.5},
                "content_box": [0, 0, 100, 75],
                "source": {"width_px": 100, "height_px": 75},
                "text_inventory": [],
                "visual_inventory": [],
                "background_strategy": {"mode": "native-or-script"},
                "quality_checks": {
                    "font_size_calibrated": True,
                    "visual_inventory_matched": True,
                    "background_strategy_checked": True,
                    "shape_corner_geometry_checked": True,
                },
                "text_boxes": [],
                "shapes": [],
                "images": [],
                "asset_provenance": [],
            }
        ),
        encoding="utf-8",
    )
    page_outputs["imagegen_jobs"].write_text(json.dumps({"jobs": []}), encoding="utf-8")
    page_outputs["page_pptx"].write_bytes(b"page-pptx")
    page_outputs["preview"].write_bytes(b"preview")
    page_outputs["contact_sheet"].write_bytes(b"contact-sheet")
    page_outputs["validation"].write_text(json.dumps({"passed": True}), encoding="utf-8")
    page_outputs["page_result"].write_text(
        json.dumps(
            {
                "page_manifest": "manifest.json",
                "imagegen_jobs": "imagegen-jobs.json",
                "page_pptx": "page.pptx",
                "preview": "preview.png",
                "contact_sheet": "split_assets_contact.png",
                "validation": "validation.json",
                "page_result": "page_result.json",
            }
        ),
        encoding="utf-8",
    )
    (final_dir / "validation.json").write_text(
        json.dumps({"passed": True}),
        encoding="utf-8",
    )
    (final_dir / "run_summary.json").write_text(
        json.dumps(
            {
                "schema_version": 1,
                "run_id": "editppt-run",
                "status": "complete",
                "page_count": 1,
                "output": str(pptx),
                "validation": str(final_dir / "validation.json"),
                "completed_at": "2026-06-27T00:00:00Z",
            }
        ),
        encoding="utf-8",
    )
    (run_dir / "deck_manifest.json").write_text(
        json.dumps(
            {
                "schema_version": 1,
                "run_id": "editppt-run",
                "input_type": "image",
                "page_count": 1,
                "output": "final/figure_edited.pptx",
                "page_jobs": "page_jobs.json",
                "run_state": "run_state.json",
                "pages": [
                    {
                        "page_id": "page_001",
                        "page_dir": "pages/page_001",
                        "page_request": "pages/page_001/page_request.json",
                        "source": "pages/page_001/source.png",
                    }
                ],
                "completed_at": "2026-06-27T00:00:00Z",
            }
        ),
        encoding="utf-8",
    )
    (run_dir / "page_jobs.json").write_text(
        json.dumps(
            {
                "schema_version": 1,
                "run_id": "editppt-run",
                "run_status": "complete",
                "pages": [
                    {
                        "page_id": "page_001",
                        "status": "accepted",
                        "accepted": True,
                        "page_dir": "pages/page_001",
                        "page_request": "pages/page_001/page_request.json",
                        "source": "pages/page_001/source.png",
                        "result": {
                            "agent_id": "main",
                            "record_mode": "local-main-agent",
                            "outputs": {
                                key: str(path.relative_to(run_dir))
                                for key, path in page_outputs.items()
                            },
                            "hashes": {key: _sha256(path) for key, path in page_outputs.items()},
                            "validation_passed": True,
                        },
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    (run_dir / "run_state.json").write_text(
        json.dumps({"status": "complete", "history": []}),
        encoding="utf-8",
    )
    return run_dir, pptx


def test_prepare_editable_figure_run_calls_editppt_prepare(tmp_path):
    source = tmp_path / "figure.png"
    source.write_bytes(b"png")

    with (
        patch("pdf_zh_translator.editable_figures.shutil.which", return_value="/bin/editppt"),
        patch("pdf_zh_translator.editable_figures.subprocess.run") as run,
    ):
        run_dir = prepare_editable_figure_run(source, tmp_path / "out", figure_id="fig 1")

    assert run_dir == tmp_path / "out" / "fig-1" / "editppt-run"
    run.assert_called_once()
    command = run.call_args.args[0]
    assert command[:2] == ["editppt", "prepare"]
    assert "--job-dir" in command
    assert "--no-text-hints" in command


def test_register_and_audit_editppt_finalized_figure(tmp_path):
    source = tmp_path / "figure.png"
    source.write_bytes(b"source-image")
    run_dir, _pptx = _write_completed_editppt_run(tmp_path)
    output_dir = tmp_path / "registered" / "fig-1"

    manifest = register_editable_figure(
        figure_id="fig-1",
        source_image=source,
        editppt_run=run_dir,
        output_dir=output_dir,
    )
    audit = audit_editable_figure_manifests(tmp_path / "registered")

    assert manifest["skill"] == SKILL_NAME
    assert manifest["skill_source"] == SKILL_SOURCE_URL
    assert manifest["runtime"] == "editppt"
    assert audit.ok is True
    assert audit.checked == 1


def test_audit_fails_when_source_hash_changes(tmp_path):
    source = tmp_path / "figure.png"
    source.write_bytes(b"source-image")
    run_dir, _pptx = _write_completed_editppt_run(tmp_path)
    output_dir = tmp_path / "registered" / "fig-1"

    register_editable_figure(
        figure_id="fig-1",
        source_image=source,
        editppt_run=run_dir,
        output_dir=output_dir,
    )
    source.write_bytes(b"changed")

    audit = audit_editable_figure_manifests(tmp_path / "registered")

    assert audit.ok is False
    assert audit.failed == 1
    assert "hash mismatch" in audit.issues[0]


def test_validate_rejects_unfinalized_editppt_run(tmp_path):
    run_dir, _pptx = _write_completed_editppt_run(tmp_path)
    jobs = json.loads((run_dir / "page_jobs.json").read_text(encoding="utf-8"))
    jobs["pages"][0]["status"] = "recorded"
    jobs["pages"][0]["accepted"] = False
    (run_dir / "page_jobs.json").write_text(json.dumps(jobs), encoding="utf-8")

    with pytest.raises(ValueError, match="not finalized"):
        validate_editppt_run(run_dir)


def test_validate_rejects_run_without_skill_final_summary(tmp_path):
    run_dir, _pptx = _write_completed_editppt_run(tmp_path)
    (run_dir / "final" / "run_summary.json").unlink()

    with pytest.raises(ValueError, match="run_summary"):
        validate_editppt_run(run_dir)


def test_validate_rejects_page_artifact_hash_mismatch(tmp_path):
    run_dir, _pptx = _write_completed_editppt_run(tmp_path)
    manifest_path = run_dir / "pages" / "page_001" / "manifest.json"
    manifest_path.write_text(
        json.dumps(
            {
                "slide": {"width": 10, "height": 7.5},
                "content_box": [0, 0, 100, 75],
                "source": {"width_px": 100, "height_px": 75},
                "text_inventory": [],
                "visual_inventory": [],
                "background_strategy": {"mode": "changed"},
                "quality_checks": {"font_size_calibrated": True},
                "text_boxes": [],
                "shapes": [],
                "images": [],
                "asset_provenance": [],
            }
        ),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="hash mismatch"):
        validate_editppt_run(run_dir)


def test_validate_rejects_page_manifest_without_required_contract(tmp_path):
    run_dir, _pptx = _write_completed_editppt_run(tmp_path)
    manifest_path = run_dir / "pages" / "page_001" / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest.pop("asset_provenance")
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    jobs_path = run_dir / "page_jobs.json"
    jobs = json.loads(jobs_path.read_text(encoding="utf-8"))
    jobs["pages"][0]["result"]["hashes"]["page_manifest"] = _sha256(manifest_path)
    jobs_path.write_text(json.dumps(jobs), encoding="utf-8")

    with pytest.raises(ValueError, match="manifest.json is missing required field"):
        validate_editppt_run(run_dir)


def test_extract_pdf_figures_writes_source_manifest(tmp_path):
    import fitz

    pdf_path = tmp_path / "paper.pdf"
    document = fitz.open()
    page = document.new_page(width=300, height=240)
    page.draw_rect(fitz.Rect(60, 50, 210, 150), color=(0, 0, 0), fill=(0.85, 0.85, 0.85))
    page.insert_text((60, 175), "Figure 1: Architecture overview.", fontsize=10)
    document.save(pdf_path)
    document.close()

    manifest = extract_pdf_figures(pdf_path, tmp_path / "editable", paper_id="Test Paper")
    manifest_path = tmp_path / "editable" / "Test-Paper" / SOURCE_FIGURES_MANIFEST_FILENAME
    figure = manifest["figures"][0]

    assert manifest["figure_count"] == 1
    assert manifest_path.exists()
    assert figure["status"] == "source-extracted"
    assert Path(figure["image_path"]).exists()
    assert figure["image_sha256"]
    assert figure["source_pdf_sha256"] == manifest["source_pdf_sha256"]


def test_prepare_extracted_figures_updates_manifest(tmp_path):
    source = tmp_path / "editable" / "paper" / "figures" / "paper_p001_fig001" / "source.png"
    source.parent.mkdir(parents=True)
    source.write_bytes(b"png")
    manifest_path = tmp_path / "editable" / "paper" / SOURCE_FIGURES_MANIFEST_FILENAME
    manifest_path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "paper_id": "paper",
                "figure_count": 1,
                "figures": [
                    {
                        "figure_id": "paper_p001_fig001",
                        "image_path": str(source),
                        "status": "source-extracted",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    with (
        patch("pdf_zh_translator.editable_figures.shutil.which", return_value="/bin/editppt"),
        patch("pdf_zh_translator.editable_figures.subprocess.run") as run,
    ):
        manifest = prepare_extracted_figures(manifest_path)

    figure = manifest["figures"][0]
    assert manifest["prepared_count"] == 1
    assert figure["status"] == "prepared"
    assert figure["editppt_run"].endswith("paper_p001_fig001/editppt-run")
    run.assert_called_once()


def test_source_audit_requires_prepared_run(tmp_path):
    source = tmp_path / "editable" / "paper" / "figures" / "paper_p001_fig001" / "source.png"
    source.parent.mkdir(parents=True)
    source.write_bytes(b"png")
    manifest_path = tmp_path / "editable" / "paper" / SOURCE_FIGURES_MANIFEST_FILENAME
    manifest_path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "paper_id": "paper",
                "figure_count": 1,
                "figures": [
                    {
                        "figure_id": "paper_p001_fig001",
                        "image_path": str(source),
                        "image_sha256": "bad-hash",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    audit = audit_figure_source_manifest(manifest_path, require_prepared=True)

    assert audit.ok is False
    assert audit.failed == 1
    assert "hash mismatch" in audit.issues[0]


def test_register_finalized_figures_updates_source_manifest(tmp_path):
    source = tmp_path / "editable" / "paper" / "figures" / "paper_p001_fig001" / "source.png"
    source.parent.mkdir(parents=True)
    source.write_bytes(b"source-image")
    run_dir, pptx = _write_completed_editppt_run(source.parent)
    manifest_path = tmp_path / "editable" / "paper" / SOURCE_FIGURES_MANIFEST_FILENAME
    manifest_path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "paper_id": "paper",
                "figure_count": 1,
                "figures": [
                    {
                        "figure_id": "paper_p001_fig001",
                        "image_path": str(source),
                        "editppt_run": str(run_dir),
                        "status": "prepared",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    manifest = register_finalized_figures(manifest_path)
    audit = audit_figure_source_manifest(manifest_path, require_registered=True)
    figure = manifest["figures"][0]

    assert manifest["_batch_registered"] == 1
    assert manifest["_batch_failed"] == 0
    assert figure["status"] == "accepted"
    assert Path(figure["editable_manifest"]).exists()
    assert figure["editppt_output"] == str(pptx)
    assert audit.ok is True
