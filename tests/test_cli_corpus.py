"""CLI tests for terminology corpus maintenance."""

import json

from pdf_zh_translator.cli import build_parser, main


def test_corpus_add_parser_accepts_term_pairs(tmp_path):
    corpus_path = tmp_path / "corpus.json"
    parser = build_parser()

    args = parser.parse_args(
        [
            "corpus-add",
            "ai",
            "Direct Preference Optimization=直接偏好优化",
            "--source",
            "unit-test",
            "--corpus-file",
            str(corpus_path),
        ]
    )

    assert args.command == "corpus-add"
    assert args.field == "ai"
    assert args.source == "unit-test"
    assert args.corpus_file == corpus_path


def test_corpus_add_main_updates_custom_corpus_file(tmp_path, capsys):
    corpus_path = tmp_path / "corpus.json"

    code = main(
        [
            "corpus-add",
            "ai",
            "Direct Preference Optimization=直接偏好优化",
            "--source",
            "unit-test",
            "--corpus-file",
            str(corpus_path),
        ]
    )

    data = json.loads(corpus_path.read_text(encoding="utf-8"))
    output = capsys.readouterr().out
    assert code == 0
    assert data["ai"]["Direct Preference Optimization"] == "直接偏好优化"
    assert "Updated 1 terminology entry" in output


def test_corpus_add_rejects_invalid_pair(tmp_path, capsys):
    corpus_path = tmp_path / "corpus.json"

    code = main(["corpus-add", "ai", "missing-separator", "--corpus-file", str(corpus_path)])

    err = capsys.readouterr().err
    assert code == 1
    assert "Invalid term pair" in err
    assert not corpus_path.exists()


def test_corpus_stats_command_prints_total(capsys):
    code = main(["corpus-stats"])

    output = capsys.readouterr().out
    assert code == 0
    assert "_total:" in output
    assert "neurips_icml_iclr:" in output


def test_corpus_health_reports_candidates(tmp_path, capsys):
    candidates = tmp_path / "terminology_candidates.jsonl"
    candidates.write_text(
        json.dumps({"term": "Latent Planning", "source": "paper:a"}) + "\n",
        encoding="utf-8",
    )

    code = main(["corpus-health", "--candidates-jsonl", str(candidates)])

    output = capsys.readouterr().out
    assert code == 0
    assert "Top-conference terms:" in output
    assert "Candidate terms pending review: 1" in output
    assert "Top-conference fields: complete" in output


def test_corpus_promote_and_release_commands(tmp_path, capsys):
    review_path = tmp_path / "review.json"
    corpus_path = tmp_path / "corpus.json"
    review_path.write_text(
        json.dumps(
            {
                "candidates": [
                    {
                        "term": "Token Merging",
                        "translation": "词元合并",
                        "approved": True,
                    }
                ]
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    promote_code = main(
        [
            "corpus-promote",
            str(review_path),
            "cvpr_computer_vision",
            "--corpus-file",
            str(corpus_path),
        ]
    )
    release_code = main(
        [
            "corpus-release",
            "2026.06-test",
            "--corpus-file",
            str(corpus_path),
        ]
    )

    data = json.loads(corpus_path.read_text(encoding="utf-8"))
    output = capsys.readouterr().out
    assert promote_code == 0
    assert release_code == 0
    assert data["cvpr_computer_vision"]["Token Merging"] == "词元合并"
    assert data["_metadata"]["version"] == "2026.06-test"
    assert "Promoted 1 reviewed term" in output
    assert "Released corpus 2026.06-test" in output


def test_golden_init_command_writes_100_case_template(tmp_path, capsys):
    manifest = tmp_path / "golden.json"

    code = main(["golden-init", str(manifest)])

    data = json.loads(manifest.read_text(encoding="utf-8"))
    output = capsys.readouterr().out
    assert code == 0
    assert data["target_cases"] == 100
    assert "Wrote golden manifest template" in output


def test_golden_discover_command_writes_manifest(tmp_path, capsys):
    import fitz

    pairs_dir = tmp_path / "pairs"
    pairs_dir.mkdir()
    for name in ["paper-a-original.pdf", "paper-a-translated.pdf"]:
        document = fitz.open()
        document.new_page(width=120, height=120)
        document.save(pairs_dir / name)
        document.close()
    manifest = tmp_path / "golden.json"

    code = main(
        [
            "golden-discover",
            str(pairs_dir),
            str(manifest),
            "--target-cases",
            "1",
        ]
    )

    data = json.loads(manifest.read_text(encoding="utf-8"))
    output = capsys.readouterr().out
    assert code == 0
    assert len(data["cases"]) == 1
    assert "Discovered 1 golden PDF pair" in output
