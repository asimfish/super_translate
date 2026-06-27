"""CLI tests for editable figure PPT provenance."""

import json
from pathlib import Path

from pdf_zh_translator.cli import main


def _write_completed_editppt_run(root: Path) -> Path:
    run_dir = root / "editppt-run"
    final_dir = run_dir / "final"
    final_dir.mkdir(parents=True)
    pptx = final_dir / "figure_edited.pptx"
    pptx.write_bytes(b"fake-pptx")
    (final_dir / "validation.json").write_text(json.dumps({"passed": True}), encoding="utf-8")
    (run_dir / "deck_manifest.json").write_text(
        json.dumps({"output": "final/figure_edited.pptx", "completed_at": "2026-06-27T00:00:00Z"}),
        encoding="utf-8",
    )
    (run_dir / "page_jobs.json").write_text(
        json.dumps(
            {
                "run_status": "complete",
                "pages": [{"page_id": "page_001", "status": "accepted", "accepted": True}],
            }
        ),
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
