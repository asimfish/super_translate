"""Tests for editable figure PPT provenance."""

import json
from pathlib import Path
from unittest.mock import patch

import pytest

from pdf_zh_translator.editable_figures import (
    SKILL_NAME,
    SKILL_SOURCE_URL,
    audit_editable_figure_manifests,
    prepare_editable_figure_run,
    register_editable_figure,
    validate_editppt_run,
)


def _write_completed_editppt_run(root: Path) -> tuple[Path, Path]:
    run_dir = root / "editppt-run"
    final_dir = run_dir / "final"
    final_dir.mkdir(parents=True)
    pptx = final_dir / "figure_edited.pptx"
    pptx.write_bytes(b"fake-pptx-for-provenance-tests")
    (final_dir / "validation.json").write_text(
        json.dumps({"passed": True}),
        encoding="utf-8",
    )
    (run_dir / "deck_manifest.json").write_text(
        json.dumps(
            {
                "output": "final/figure_edited.pptx",
                "completed_at": "2026-06-27T00:00:00Z",
            }
        ),
        encoding="utf-8",
    )
    (run_dir / "page_jobs.json").write_text(
        json.dumps(
            {
                "run_status": "complete",
                "pages": [
                    {
                        "page_id": "page_001",
                        "status": "accepted",
                        "accepted": True,
                    }
                ],
            }
        ),
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
