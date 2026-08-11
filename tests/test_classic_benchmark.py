"""Tests for the classic-paper benchmark harness (no network, no API)."""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
MANIFEST = REPO / "benchmarks" / "classic20" / "manifest.json"


@pytest.fixture(scope="module")
def benchmark_module():
    spec = importlib.util.spec_from_file_location(
        "classic_benchmark", REPO / "scripts" / "classic_benchmark.py"
    )
    module = importlib.util.module_from_spec(spec)
    sys.modules["classic_benchmark"] = module
    spec.loader.exec_module(module)
    return module


class TestManifest:
    def test_has_at_least_20_papers_with_margin(self):
        data = json.loads(MANIFEST.read_text(encoding="utf-8"))
        assert len(data["papers"]) >= 25

    def test_entries_are_well_formed_and_unique(self):
        data = json.loads(MANIFEST.read_text(encoding="utf-8"))
        ids = [paper["id"] for paper in data["papers"]]
        arxiv_ids = [paper["arxiv_id"] for paper in data["papers"]]
        assert len(set(ids)) == len(ids)
        assert len(set(arxiv_ids)) == len(arxiv_ids)
        for paper in data["papers"]:
            assert paper["title"].strip()
            assert paper["tags"], paper["id"]
            assert all(tag in data["layout_axes"] for tag in paper["tags"])

    def test_layout_axes_are_all_covered(self):
        data = json.loads(MANIFEST.read_text(encoding="utf-8"))
        covered = {tag for paper in data["papers"] for tag in paper["tags"]}
        assert covered == set(data["layout_axes"])


class TestHarness:
    def test_load_entries_scopes_and_validates(self, benchmark_module):
        entries = benchmark_module._load_entries("word2vec,adam")
        assert {entry.id for entry in entries} == {"word2vec", "adam"}
        with pytest.raises(SystemExit):
            benchmark_module._load_entries("not-a-paper")

    def test_report_aggregates_without_previews(self, benchmark_module, tmp_path):
        workdir = tmp_path / "bench"
        reports = workdir / "reports"
        meta_dir = workdir / "meta"
        reports.mkdir(parents=True)
        meta_dir.mkdir(parents=True)
        entries = benchmark_module._load_entries("word2vec")
        (reports / "word2vec.json").write_text(
            json.dumps(
                {
                    "id": "word2vec",
                    "arxiv_id": "1301.3781",
                    "title": "word2vec",
                    "tags": ["single_column"],
                    "pages": 12,
                    "visual_score": 0.91,
                    "error_count": 3,
                    "legacy_error_count": 0,
                    "error_pages": [2],
                    "issues_by_code": {"font_size_drift": 3},
                    "strict_pass": False,
                    "legacy_pass": True,
                }
            ),
            encoding="utf-8",
        )
        (meta_dir / "word2vec.json").write_text(
            json.dumps({"license": "unknown", "showcase_ok": False}),
            encoding="utf-8",
        )

        class Args:
            no_previews = True

        code = benchmark_module.cmd_report(entries, workdir, Args())
        assert code == 0
        report_text = (workdir / "REPORT.md").read_text(encoding="utf-8")
        assert "word2vec" in report_text
        assert "font_size_drift:3" in report_text
        showcase = json.loads((workdir / "showcase.json").read_text(encoding="utf-8"))
        assert showcase["papers"][0]["legacy_pass"] is True
        assert showcase["papers"][0]["showcase_ok"] is False
