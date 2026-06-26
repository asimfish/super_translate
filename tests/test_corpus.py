"""Tests for terminology corpus maintenance helpers."""

import json

from pdf_zh_translator.corpus import (
    corpus_stats,
    extract_candidate_terms,
    load_candidate_terms,
    promote_reviewed_terms,
    record_candidate_terms,
    release_corpus_version,
    upsert_terms,
    write_candidate_review,
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


def test_packaged_corpus_includes_top_conference_categories():
    stats = corpus_stats()

    assert stats["_total"] >= 550
    assert stats["neurips_icml_iclr"] >= 70
    assert stats["cvpr_computer_vision"] >= 45
    assert stats["acl_nlp"] >= 45
    assert stats["paper_layout_and_reporting"] >= 35


def test_candidate_review_promote_and_release_workflow(tmp_path):
    candidates_path = tmp_path / "candidates.jsonl"
    candidates_path.write_text(
        "\n".join(
            [
                json.dumps({"term": "Latent Consistency Planning", "source": "paper:a"}),
                json.dumps({"term": "Latent-Consistency Planning", "source": "paper:b"}),
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    review_path = tmp_path / "review.json"
    corpus_path = tmp_path / "corpus.json"

    candidates = load_candidate_terms(candidates_path)
    count = write_candidate_review(candidates_path, review_path)

    review = json.loads(review_path.read_text(encoding="utf-8"))
    review["candidates"][0]["approved"] = True
    review["candidates"][0]["translation"] = "潜在一致性规划"
    review_path.write_text(json.dumps(review, ensure_ascii=False), encoding="utf-8")

    promoted = promote_reviewed_terms(
        review_path,
        field="neurips_icml_iclr",
        corpus_path=corpus_path,
        source="unit-test-review",
    )
    metadata = release_corpus_version(version="2026.06-test", corpus_path=corpus_path)
    corpus = json.loads(corpus_path.read_text(encoding="utf-8"))

    assert count == 1
    assert candidates[0]["count"] == 2
    assert promoted == 1
    assert corpus["neurips_icml_iclr"]["Latent Consistency Planning"] == "潜在一致性规划"
    assert metadata["version"] == "2026.06-test"
    assert metadata["total_terms"] == 1
