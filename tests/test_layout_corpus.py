"""The acceptance tool must fail on wrong inputs and missing translated artifacts."""

import json
import subprocess
import sys
from pathlib import Path

import fitz
import pytest
from scripts.check_layout_families import audit_case, digest, prepare_case


def case_source(tmp_path):
    source = tmp_path / "source"
    source.mkdir()
    inputs = tmp_path / "inputs"
    inputs.mkdir()
    path = source / "sample.pdf"
    with fitz.open() as doc:
        p = doc.new_page()
        for y in (100, 130, 160):
            p.insert_text((60, y), "A scientific document used to verify source integrity.")
        doc.save(path)
    case = {
        "id": "sample", "source_file": path.name, "source_sha256": digest(path), "pages": [0],
        "expected_profile": {"name": "single_column", "columns": 1},
    }
    return case, source, inputs


def test_wrong_original_source_is_rejected(tmp_path):
    case, source, inputs = case_source(tmp_path)
    case["source_sha256"] = "0" * 64
    with pytest.raises(ValueError, match="checksum"):
        prepare_case(case, source, inputs)


def test_forged_input_receipt_does_not_override_the_original_pdf(tmp_path):
    case, source, inputs = case_source(tmp_path)
    prepare_case(case, source, inputs)
    altered = inputs / "sample.pdf"
    with fitz.open() as doc:
        doc.new_page().insert_text((60, 100), "Another unrelated document.")
        doc.save(altered)
    receipt = altered.with_suffix(".source.json")
    data = json.loads(receipt.read_text())
    data["input_sha256"] = digest(altered)
    receipt.write_text(json.dumps(data))
    with pytest.raises(ValueError, match="appearance differs"):
        audit_case(case, source, inputs, None)


def test_required_render_cannot_pass_with_only_detection(tmp_path):
    case, source, inputs = case_source(tmp_path)
    prepare_case(case, source, inputs)
    manifest = tmp_path / "manifest.json"
    manifest.write_text(json.dumps({"cases": [case]}))
    report = tmp_path / "report.json"
    script = Path(__file__).resolve().parents[1] / "scripts/check_layout_families.py"
    process = subprocess.run([
        sys.executable, str(script), "check", "--manifest", str(manifest),
        "--source-root", str(source), "--input-root", str(inputs), "--report", str(report),
        "--require-render",
    ], capture_output=True, text=True)
    assert process.returncode == 1
    assert json.loads(report.read_text())["failures"][0]["error"] == "render acceptance missing"
