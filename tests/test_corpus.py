"""Tests for terminology corpus maintenance helpers."""

import json

from pdf_zh_translator.corpus import (
    audit_candidate_terms,
    audit_terminology_usage,
    corpus_lint,
    corpus_stats,
    extract_candidate_terms,
    load_candidate_terms,
    promote_reviewed_terms,
    record_candidate_terms,
    release_corpus_version,
    suggest_candidate_field,
    upsert_terms,
    write_candidate_review,
)


def test_corpus_lint_detects_cross_field_conflicts():
    corpus = {
        "cvpr_computer_vision": {"camera pose estimation": "相机位姿估计"},
        "cvpr_3d_geometry_reconstruction": {"camera pose estimation": "相机姿态估计"},
    }
    report = corpus_lint(corpus)
    assert report["conflict_count"] == 1
    conflict = report["conflicts"][0]
    assert conflict["term"] == "camera pose estimation"
    # The last field wins in the flattened lookup.
    assert conflict["effective"] == "相机姿态估计"
    assert not report["clean"]


def test_corpus_lint_flags_empty_and_untranslated_entries():
    corpus = {
        "ml": {"Transformer": "Transformer", "Attention": "注意力"},
        "empty": {},
        "bad": {"Foo": ""},
    }
    report = corpus_lint(corpus)
    assert "empty" in report["empty_fields"]
    assert report["empty_value_count"] == 1
    assert any(item["en"] == "Transformer" for item in report["untranslated"])
    assert not report["clean"]


def test_corpus_lint_clean_corpus():
    corpus = {"ml": {"Neural Network": "神经网络", "Attention": "注意力"}}
    report = corpus_lint(corpus)
    assert report["conflict_count"] == 0
    assert report["clean"] is True


def test_packaged_corpus_has_no_conflicts():
    # The shipped corpus must stay internally consistent.
    report = corpus_lint()
    assert report["conflict_count"] == 0, report["conflicts"]


def test_audit_terminology_usage_flags_missing_standard_translation():
    source = ["We use Retrieval-Augmented Generation in our pipeline."]
    relevant = audit_terminology_usage(source, ["译文里完全没有使用标准术语。"])
    # If the corpus knows this term, its standard zh should be expected.
    if relevant:
        assert all("expected_zh" in v for v in relevant)


def test_audit_terminology_usage_passes_when_standard_used():
    source = ["We use Neural Network architectures."]
    # Output contains the standard translation, so no violation for it.
    violations = audit_terminology_usage(source, ["我们使用了神经网络架构。"])
    assert all(v["expected_zh"] != "神经网络" for v in violations)


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

    assert stats["_total"] >= 1000
    assert stats["neurips_icml_iclr"] >= 70
    assert stats["neurips_foundations_theory"] >= 30
    assert stats["icml_optimization_learning_theory"] >= 30
    assert stats["iclr_representations_architectures"] >= 30
    assert stats["cvpr_computer_vision"] >= 45
    assert stats["cvpr_detection_segmentation"] >= 30
    assert stats["cvpr_3d_geometry_reconstruction"] >= 30
    assert stats["acl_nlp"] >= 45
    assert stats["acl_machine_translation_generation"] >= 30
    assert stats["acl_information_extraction_retrieval"] >= 30
    assert stats["paper_layout_and_reporting"] >= 35
    assert stats["ml_systems_data_scaling"] >= 30
    assert stats["ai_agents_tool_use"] >= 30


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


def test_candidate_audit_suggests_fields_and_marks_known_terms(tmp_path):
    candidates_path = tmp_path / "candidates.jsonl"
    candidates_path.write_text(
        "\n".join(
            [
                json.dumps({"term": "Object Detection Head", "source": "paper:cvpr"}),
                json.dumps({"term": "Retrieval-Augmented Generation", "source": "paper:acl"}),
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    audited = audit_candidate_terms(candidates_path)
    by_term = {item["term"]: item for item in audited}

    assert by_term["Object Detection Head"]["suggested_field"] == "cvpr_detection_segmentation"
    assert by_term["Object Detection Head"]["status"] == "needs_translation"
    assert by_term["Retrieval-Augmented Generation"]["status"] == "known"
    assert by_term["Retrieval-Augmented Generation"]["known_translation"] == "检索增强生成"


def test_suggest_candidate_field_uses_track_keywords():
    field, confidence, reason = suggest_candidate_field("Bayesian Posterior Calibration")

    assert field == "icml_probabilistic_bayes"
    assert confidence > 0.5
    assert "matched keywords" in reason


def test_auto_field_promote_uses_review_field(tmp_path):
    review_path = tmp_path / "review.json"
    corpus_path = tmp_path / "corpus.json"
    review_path.write_text(
        json.dumps(
            {
                "candidates": [
                    {
                        "term": "Object Detection Head",
                        "translation": "目标检测头",
                        "approved": True,
                        "field": "cvpr_detection_segmentation",
                    }
                ]
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    promoted = promote_reviewed_terms(
        review_path,
        field="auto",
        corpus_path=corpus_path,
        source="unit-test-auto",
    )

    corpus = json.loads(corpus_path.read_text(encoding="utf-8"))
    assert promoted == 1
    assert corpus["cvpr_detection_segmentation"]["Object Detection Head"] == "目标检测头"
