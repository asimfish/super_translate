"""CLI tests for editable figure PPT provenance."""

import json

from pdf_zh_translator.cli import main


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
