"""Tests for the classic-paper benchmark harness (no network, no API)."""

from __future__ import annotations

import hashlib
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

    def test_evaluation_cache_is_bound_to_pdf_and_qa_fingerprints(
        self, benchmark_module, tmp_path
    ):
        source = tmp_path / "source.pdf"
        translated = tmp_path / "translated.pdf"
        source.write_bytes(b"source-v1")
        translated.write_bytes(b"translated-v1")
        report = {
            "schema_version": 2,
            "source_sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
            "translated_sha256": hashlib.sha256(translated.read_bytes()).hexdigest(),
            "qa_fingerprint": benchmark_module._qa_fingerprint(),
        }

        assert benchmark_module._evaluation_cache_matches(
            report,
            source,
            translated,
        )
        translated.write_bytes(b"translated-v2")
        assert not benchmark_module._evaluation_cache_matches(
            report,
            source,
            translated,
        )

    def test_evaluation_cache_is_bound_to_translation_provenance(
        self, benchmark_module, tmp_path
    ):
        source = tmp_path / "source.pdf"
        translated = tmp_path / "translated.pdf"
        timing = tmp_path / "paper.timing.json"
        source.write_bytes(b"source")
        translated.write_bytes(b"translated")
        provenance = {
            "engine_fingerprint": "engine-v1",
            "engine_commit": "commit-v1",
            "translation_model": "model-v1",
            "font_fingerprint": "font-v1",
        }
        report = {
            "schema_version": 2,
            "source_sha256": hashlib.sha256(b"source").hexdigest(),
            "translated_sha256": hashlib.sha256(b"translated").hexdigest(),
            "qa_fingerprint": benchmark_module._qa_fingerprint(),
            **provenance,
        }
        timing.write_text(
            json.dumps(
                {
                    "schema_version": 2,
                    "source_sha256": report["source_sha256"],
                    "translated_sha256": report["translated_sha256"],
                    **provenance,
                }
            ),
            encoding="utf-8",
        )

        assert benchmark_module._evaluation_cache_matches(
            report, source, translated, timing
        )
        timing_payload = json.loads(timing.read_text(encoding="utf-8"))
        timing_payload["font_fingerprint"] = "font-v2"
        timing.write_text(json.dumps(timing_payload), encoding="utf-8")
        assert not benchmark_module._evaluation_cache_matches(
            report, source, translated, timing
        )

    def test_translation_provenance_requires_matching_output_hash(
        self, benchmark_module, tmp_path
    ):
        timing = tmp_path / "paper.timing.json"
        timing.write_text(
            json.dumps(
                {
                    "schema_version": 2,
                    "source_sha256": "source-hash",
                    "translated_sha256": "translated-hash",
                    "engine_fingerprint": "engine-fingerprint",
                    "engine_commit": "engine-commit",
                    "translation_model": "test-model",
                    "font_fingerprint": "test-fonts",
                }
            ),
            encoding="utf-8",
        )

        assert benchmark_module._translation_provenance(
            timing, "source-hash", "translated-hash"
        ) == {
            "engine_fingerprint": "engine-fingerprint",
            "engine_commit": "engine-commit",
            "translation_model": "test-model",
            "font_fingerprint": "test-fonts",
        }
        assert benchmark_module._translation_provenance(
            timing, "changed-source", "translated-hash"
        ) == {
            "engine_fingerprint": "unknown",
            "engine_commit": "unknown",
            "translation_model": "unknown",
            "font_fingerprint": "unknown",
        }
        assert benchmark_module._translation_provenance(
            timing, "source-hash", "changed-hash"
        ) == {
            "engine_fingerprint": "unknown",
            "engine_commit": "unknown",
            "translation_model": "unknown",
            "font_fingerprint": "unknown",
        }

    def test_translation_cache_requires_current_engine(
        self, benchmark_module, tmp_path, monkeypatch
    ):
        source = tmp_path / "source.pdf"
        translated = tmp_path / "translated.pdf"
        timing = tmp_path / "paper.timing.json"
        source.write_bytes(b"source")
        translated.write_bytes(b"translated")
        timing.write_text(
            json.dumps(
                {
                    "schema_version": 2,
                    "source_sha256": hashlib.sha256(b"source").hexdigest(),
                    "translated_sha256": hashlib.sha256(b"translated").hexdigest(),
                    "engine_fingerprint": "current-engine",
                    "engine_commit": "test-commit",
                    "translation_model": "test-model",
                    "font_fingerprint": "current-fonts",
                }
            ),
            encoding="utf-8",
        )
        monkeypatch.setattr(
            benchmark_module, "_engine_fingerprint", lambda: "current-engine"
        )
        monkeypatch.setattr(
            benchmark_module, "_font_fingerprint", lambda: "current-fonts"
        )

        assert benchmark_module._translation_cache_matches(
            timing, source, translated, "test-model"
        )
        monkeypatch.setattr(
            benchmark_module, "_engine_fingerprint", lambda: "new-engine"
        )
        assert not benchmark_module._translation_cache_matches(
            timing, source, translated, "test-model"
        )
        monkeypatch.setattr(
            benchmark_module, "_engine_fingerprint", lambda: "current-engine"
        )
        assert not benchmark_module._translation_cache_matches(
            timing, source, translated, "changed-model"
        )
        monkeypatch.setattr(
            benchmark_module, "_font_fingerprint", lambda: "changed-fonts"
        )
        assert not benchmark_module._translation_cache_matches(
            timing, source, translated, "test-model"
        )

    def test_block_cache_namespace_changes_with_model_and_prompt(
        self, benchmark_module, tmp_path, monkeypatch
    ):
        monkeypatch.setattr(
            benchmark_module,
            "_translation_fingerprint",
            lambda: "prompt-and-terminology-v1",
        )
        first = benchmark_module._block_cache_path(tmp_path, "paper", "model-a")
        assert first == benchmark_module._block_cache_path(
            tmp_path, "paper", "model-a"
        )
        assert first != benchmark_module._block_cache_path(
            tmp_path, "paper", "model-b"
        )
        monkeypatch.setattr(
            benchmark_module,
            "_translation_fingerprint",
            lambda: "prompt-and-terminology-v2",
        )
        assert first != benchmark_module._block_cache_path(
            tmp_path, "paper", "model-a"
        )

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
        assert showcase["schema_version"] == 2
        assert showcase["papers"][0]["qa_commit"] == "unknown"

    def test_report_never_renders_previews_for_restricted_paper(
        self, benchmark_module, tmp_path, monkeypatch
    ):
        workdir = tmp_path / "bench"
        for name in ("reports", "meta", "papers", "translations"):
            (workdir / name).mkdir(parents=True)
        entries = benchmark_module._load_entries("word2vec")
        (workdir / "reports" / "word2vec.json").write_text(
            json.dumps(
                {
                    "pages": 1,
                    "visual_score": 0.9,
                    "error_count": 0,
                    "legacy_error_count": 0,
                    "error_pages": [],
                    "issues_by_code": {},
                    "strict_pass": True,
                    "legacy_pass": True,
                }
            ),
            encoding="utf-8",
        )
        (workdir / "meta" / "word2vec.json").write_text(
            json.dumps(
                {
                    "license": "http://arxiv.org/licenses/nonexclusive-distrib/1.0/",
                    "showcase_ok": False,
                }
            ),
            encoding="utf-8",
        )
        (workdir / "papers" / "word2vec.pdf").write_bytes(b"%PDF source")
        (workdir / "translations" / "word2vec-mono.pdf").write_bytes(
            b"%PDF translated"
        )
        render_calls = []
        monkeypatch.setattr(
            benchmark_module,
            "_render_previews",
            lambda *args, **kwargs: render_calls.append((args, kwargs)),
        )

        class Args:
            no_previews = False

        assert benchmark_module.cmd_report(entries, workdir, Args()) == 0
        showcase = json.loads((workdir / "showcase.json").read_text(encoding="utf-8"))
        assert render_calls == []
        assert showcase["papers"][0]["previews"] == []

    def test_quality_gate_enforces_strict_passes_and_artifact_provenance(
        self, benchmark_module, tmp_path
    ):
        workdir = tmp_path / "bench"
        reports = workdir / "reports"
        meta_dir = workdir / "meta"
        papers = workdir / "papers"
        reports.mkdir(parents=True)
        meta_dir.mkdir(parents=True)
        papers.mkdir(parents=True)
        entries = benchmark_module._load_entries("word2vec")
        source = papers / "word2vec.pdf"
        source.write_bytes(b"%PDF-1.4 benchmark fixture")
        (reports / "word2vec.json").write_text(
            json.dumps(
                {
                    "id": "word2vec",
                    "error_count": 0,
                    "strict_pass": True,
                    "source_sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
                    "translated_sha256": hashlib.sha256(b"translated").hexdigest(),
                    "qa_fingerprint": benchmark_module._qa_fingerprint(),
                    "schema_version": 2,
                    "engine_fingerprint": benchmark_module._engine_fingerprint(),
                    "engine_commit": "test-engine-commit",
                    "translation_model": "deepseek-v4-pro",
                    "font_fingerprint": benchmark_module._font_fingerprint(),
                }
            ),
            encoding="utf-8",
        )
        translations = workdir / "translations"
        translations.mkdir()
        (translations / "word2vec-mono.pdf").write_bytes(b"translated")
        report = json.loads(
            (reports / "word2vec.json").read_text(encoding="utf-8")
        )
        (translations / "word2vec.timing.json").write_text(
            json.dumps(
                {
                    "schema_version": 2,
                    "source_sha256": report["source_sha256"],
                    "translated_sha256": report["translated_sha256"],
                    "engine_fingerprint": report["engine_fingerprint"],
                    "engine_commit": report["engine_commit"],
                    "translation_model": report["translation_model"],
                    "font_fingerprint": report["font_fingerprint"],
                }
            ),
            encoding="utf-8",
        )
        (meta_dir / "word2vec.json").write_text(
            json.dumps(
                {
                    "id": "word2vec",
                    "license": "http://arxiv.org/licenses/nonexclusive-distrib/1.0/",
                    "showcase_ok": False,
                    "sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
                }
            ),
            encoding="utf-8",
        )

        class Args:
            min_evaluated = 1
            min_strict_passes = 1
            baseline_reports = None
            require_all_axes = False

        assert benchmark_module.cmd_gate(entries, workdir, Args()) == 0
        result = json.loads(
            (workdir / "quality-gate.json").read_text(encoding="utf-8")
        )
        assert result["passed"] is True
        assert result["schema_version"] == 2
        assert result["evaluated"] == 1
        assert result["strict_passes"] == 1

        report = json.loads((reports / "word2vec.json").read_text(encoding="utf-8"))
        del report["qa_fingerprint"]
        (reports / "word2vec.json").write_text(json.dumps(report), encoding="utf-8")
        assert benchmark_module.cmd_gate(entries, workdir, Args()) == 1
        stale = json.loads(
            (workdir / "quality-gate.json").read_text(encoding="utf-8")
        )
        assert any(
            "report provenance" in error for error in stale["provenance_errors"]
        )

        report["qa_fingerprint"] = benchmark_module._qa_fingerprint()
        (reports / "word2vec.json").write_text(json.dumps(report), encoding="utf-8")

        report["strict_pass"] = False
        report["error_count"] = 1
        (reports / "word2vec.json").write_text(json.dumps(report), encoding="utf-8")
        assert benchmark_module.cmd_gate(entries, workdir, Args()) == 1
        failed = json.loads(
            (workdir / "quality-gate.json").read_text(encoding="utf-8")
        )
        assert failed["passed"] is False
        assert any("strict passes" in failure for failure in failed["failures"])
