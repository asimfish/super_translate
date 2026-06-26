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
