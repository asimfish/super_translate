"""CLI tests for layout template learning."""

import json

import fitz

from pdf_zh_translator.cli import main


def test_layout_learn_command_writes_profile(tmp_path, capsys):
    pdf_path = tmp_path / "paper.pdf"
    output_path = tmp_path / "layout-profile.json"
    document = fitz.open()
    page = document.new_page(width=612, height=792)
    page.insert_text((54, 50), "IEEE Conference Paper")
    for i in range(6):
        page.insert_text((54, 90 + i * 22), "Left column text " * 5, fontsize=9)
        page.insert_text((330, 90 + i * 22), "Right column text " * 5, fontsize=9)
    document.save(pdf_path)
    document.close()

    code = main(["layout-learn", "ieee_custom", str(output_path), str(pdf_path)])

    data = json.loads(output_path.read_text(encoding="utf-8"))
    output = capsys.readouterr().out
    assert code == 0
    assert data["template_name"] == "ieee_custom"
    assert data["layout"]["columns"] >= 2
    assert "Learned layout template ieee_custom" in output


def test_layout_learn_rejects_missing_pdf(tmp_path, capsys):
    output_path = tmp_path / "layout-profile.json"

    code = main(["layout-learn", "missing", str(output_path), str(tmp_path / "missing.pdf")])

    err = capsys.readouterr().err
    assert code == 1
    assert "Input PDF does not exist" in err
    assert not output_path.exists()
