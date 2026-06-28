"""CLI tests for editable figure PPT provenance."""

import hashlib
import json
from pathlib import Path

from pdf_zh_translator.cli import main


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _write_completed_editppt_run(root: Path) -> Path:
    run_dir = root / "editppt-run"
    final_dir = run_dir / "final"
    page_dir = run_dir / "pages" / "page_001"
    final_dir.mkdir(parents=True)
    page_dir.mkdir(parents=True)
    pptx = final_dir / "figure_edited.pptx"
    pptx.write_bytes(b"fake-pptx")
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
    (final_dir / "validation.json").write_text(json.dumps({"passed": True}), encoding="utf-8")
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
    return run_dir


def test_figure_ppt_audit_fails_without_manifests(tmp_path, capsys):
    code = main(["figure-ppt-audit", str(tmp_path)])

    captured = capsys.readouterr()
    assert code == 1
    assert "0 checked" in captured.out
    assert "not proven to use image-to-editable-ppt" in captured.err


def test_figure_ppt_audit_allows_empty_when_requested(tmp_path, capsys):
    code = main(["figure-ppt-audit", str(tmp_path), "--allow-empty"])

    captured = capsys.readouterr()
    assert code == 0
    assert "0 checked" in captured.out


def test_figure_ppt_register_rejects_unfinalized_run(tmp_path, capsys):
    source = tmp_path / "figure.png"
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    source.write_bytes(b"source")
    (run_dir / "deck_manifest.json").write_text(
        json.dumps({"output": "final/figure_edited.pptx"}),
        encoding="utf-8",
    )
    (run_dir / "page_jobs.json").write_text(
        json.dumps({"run_status": "pending", "pages": [{"page_id": "page_001"}]}),
        encoding="utf-8",
    )

    code = main(
        [
            "figure-ppt-register",
            "fig-1",
            str(source),
            str(run_dir),
            str(tmp_path / "out"),
        ]
    )

    captured = capsys.readouterr()
    assert code == 1
    assert "Figure PPT register failed" in captured.err


def test_figure_ppt_extract_writes_source_manifest(tmp_path, capsys):
    import fitz

    pdf_path = tmp_path / "paper.pdf"
    document = fitz.open()
    page = document.new_page(width=300, height=240)
    page.draw_rect(fitz.Rect(60, 50, 210, 150), color=(0, 0, 0), fill=(0.9, 0.9, 0.9))
    document.save(pdf_path)
    document.close()

    code = main(
        [
            "figure-ppt-extract",
            str(pdf_path),
            str(tmp_path / "editable"),
            "--paper-id",
            "paper",
        ]
    )

    captured = capsys.readouterr()
    manifest = tmp_path / "editable" / "paper" / "figure_sources_manifest.json"
    assert code == 0
    assert "Extracted 1 figure source" in captured.out
    assert manifest.exists()
    data = json.loads(manifest.read_text(encoding="utf-8"))
    assert Path(data["figures"][0]["image_path"]).exists()


def test_figure_ppt_source_audit_requires_registered(tmp_path, capsys):
    source = tmp_path / "editable" / "paper" / "figures" / "paper_p001_fig001" / "source.png"
    source.parent.mkdir(parents=True)
    source.write_bytes(b"source")
    manifest = tmp_path / "editable" / "paper" / "figure_sources_manifest.json"
    manifest.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "paper_id": "paper",
                "figure_count": 1,
                "figures": [{"figure_id": "paper_p001_fig001", "image_path": str(source)}],
            }
        ),
        encoding="utf-8",
    )

    code = main(["figure-ppt-source-audit", str(manifest), "--require-registered"])

    captured = capsys.readouterr()
    assert code == 1
    assert "1 checked" in captured.out
    assert "editable figure manifest is missing" in captured.err


def test_figure_ppt_batch_register_finalized_run(tmp_path, capsys):
    source = tmp_path / "editable" / "paper" / "figures" / "paper_p001_fig001" / "source.png"
    source.parent.mkdir(parents=True)
    source.write_bytes(b"source")
    run_dir = _write_completed_editppt_run(source.parent)
    manifest = tmp_path / "editable" / "paper" / "figure_sources_manifest.json"
    manifest.write_text(
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
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    code = main(["figure-ppt-batch-register", str(manifest)])

    captured = capsys.readouterr()
    assert code == 0
    assert "Registered 1 finalized figure" in captured.out
    assert (source.parent / "editable_figure_manifest.json").exists()
