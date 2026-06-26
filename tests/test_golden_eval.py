"""Tests for golden regression-set manifests and evaluation."""

import json

import fitz

from pdf_zh_translator.golden_eval import evaluate_golden_set, write_manifest_template


def _save_blank_pdf(path) -> None:
    document = fitz.open()
    document.new_page(width=240, height=240)
    document.save(path)
    document.close()


def test_golden_manifest_template_defaults_to_100_cases(tmp_path):
    manifest = tmp_path / "golden.json"

    write_manifest_template(manifest)

    data = json.loads(manifest.read_text(encoding="utf-8"))
    assert data["target_cases"] == 100
    assert data["cases"] == []


def test_golden_evaluation_requires_target_case_count(tmp_path):
    original = tmp_path / "original.pdf"
    translated = tmp_path / "translated.pdf"
    _save_blank_pdf(original)
    _save_blank_pdf(translated)
    manifest = tmp_path / "golden.json"
    manifest.write_text(
        json.dumps(
            {
                "target_cases": 2,
                "cases": [
                    {
                        "id": "blank",
                        "original_pdf": original.name,
                        "translated_pdf": translated.name,
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    result = evaluate_golden_set(manifest)

    assert result.evaluated_cases == 1
    assert result.passed_cases == 1
    assert result.ready_for_release is False
