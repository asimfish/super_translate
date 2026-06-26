"""Tests for terminology corpus maintenance helpers."""

import json

from pdf_zh_translator.corpus import (
    extract_candidate_terms,
    record_candidate_terms,
    upsert_terms,
)


def test_upsert_terms_updates_field_and_metadata(tmp_path):
    corpus_path = tmp_path / "corpus.json"
    corpus_path.write_text(
        json.dumps({"_metadata": {"version": "test"}, "ai": {"LoRA": "LoRA"}}),
        encoding="utf-8",
    )

    changed = upsert_terms(
        "ai",
        {
            "Direct Preference Optimization": "直接偏好优化",
            "LoRA": "低秩适配",
        },
        source="unit-test",
        corpus_path=corpus_path,
    )

    data = json.loads(corpus_path.read_text(encoding="utf-8"))
    assert changed == 2
    assert data["ai"]["Direct Preference Optimization"] == "直接偏好优化"
    assert data["ai"]["LoRA"] == "低秩适配"
    assert data["_metadata"]["last_source"] == "unit-test"
    assert data["_metadata"]["total_terms"] == 2


def test_extract_candidate_terms_skips_known_terms():
    terms = extract_candidate_terms(
        [
            "We compare Retrieval-Augmented Generation, Adaptive Computation "
            "Graph, LoRA, and random baselines."
        ],
        max_terms=10,
    )

    lower_terms = {term.lower() for term in terms}
    assert "adaptive computation graph" in lower_terms
    assert "retrieval-augmented generation" not in lower_terms
    assert "lora" not in lower_terms


def test_record_candidate_terms_appends_new_jsonl_records(tmp_path):
    output_path = tmp_path / "terminology_candidates.jsonl"

    first = record_candidate_terms(
        ["Adaptive Computation Graph uses Latent Consistency Planning."],
        output_path,
        source="paper:abc",
    )
    second = record_candidate_terms(
        ["Adaptive Computation Graph uses Latent Consistency Planning."],
        output_path,
        source="paper:abc",
    )

    records = [
        json.loads(line)
        for line in output_path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    assert first >= 1
    assert second == 0
    assert {record["source"] for record in records} == {"paper:abc"}
    assert any(record["term"] == "Adaptive Computation Graph" for record in records)
