"""Tests for the classic-paper benchmark harness (no network, no API)."""

from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import subprocess
import sys
import textwrap
from pathlib import Path
from types import SimpleNamespace

import pytest

REPO = Path(__file__).resolve().parents[1]
MANIFEST = REPO / "benchmarks" / "classic20" / "manifest.json"
HELDOUT_MANIFEST = REPO / "benchmarks" / "classic20" / "heldout-final.json"


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
    def test_heldout_manifest_is_exactly_the_frozen_classic20(self):
        data = json.loads(HELDOUT_MANIFEST.read_text(encoding="utf-8"))
        assert [paper["id"] for paper in data["papers"]] == [
            "attention",
            "resnet",
            "bert",
            "gan",
            "adam",
            "vit",
            "clip",
            "ddpm",
            "unet",
            "dqn",
            "ppo",
            "word2vec",
            "bahdanau",
            "faster_rcnn",
            "mask_rcnn",
            "lora",
            "batchnorm",
            "latent_diffusion",
            "gpt3",
            "instructgpt",
        ]
        canonical = json.loads(MANIFEST.read_text(encoding="utf-8"))
        assert data["papers"] == canonical["papers"][:20]
        assert set(data["layout_axes"]) == {
            tag for paper in data["papers"] for tag in paper["tags"]
        }

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
    def test_actionable_warning_fails_strict_evaluation(self, benchmark_module):
        warning = SimpleNamespace(
            page=4,
            code="formula_changed",
            severity="warning",
            message="formula ink differs",
        )
        informational = SimpleNamespace(
            page=4,
            code="high_risk_layout",
            severity="warning",
            message="visually reviewed",
        )

        assert benchmark_module._strict_evaluation([informational], 0.91) is True
        assert benchmark_module._strict_evaluation([warning], 0.91) is False
        assert benchmark_module._actionable_warning_count([warning, informational]) == 1

    def test_isolated_process_retries_timeout_and_emits_heartbeats(
        self, benchmark_module, monkeypatch
    ):
        processes = []

        class FakeProcess:
            def __init__(self, return_code):
                self.return_code = return_code
                self.returncode = None
                self.terminated = False
                self.killed = False
                self.polls = 0

            def poll(self):
                self.polls += 1
                if self.return_code == "timeout":
                    return None
                self.returncode = self.return_code
                return self.returncode

            def terminate(self):
                self.terminated = True

            def wait(self, timeout=None):
                if self.return_code == "timeout":
                    raise subprocess.TimeoutExpired("fixture", timeout)
                return self.return_code

            def kill(self):
                self.killed = True
                self.returncode = -9

        outcomes = iter(["timeout", 0])

        def fake_popen(*_args, **_kwargs):
            process = FakeProcess(next(outcomes))
            processes.append(process)
            return process

        monotonic_values = iter([0.0, 0.0, 2.0, 2.0, 2.0])
        monkeypatch.setattr(benchmark_module.subprocess, "Popen", fake_popen)
        monkeypatch.setattr(
            benchmark_module.time, "monotonic", lambda: next(monotonic_values)
        )
        monkeypatch.setattr(benchmark_module.time, "sleep", lambda _seconds: None)
        events = []

        result = benchmark_module._run_isolated_translation_process(
            ["translate", "paper"],
            pass_fds=(),
            max_attempts=2,
            timeout_seconds=1,
            heartbeat_seconds=0.1,
            batch_deadline=None,
            on_event=events.append,
        )

        assert result["status"] == "completed"
        assert result["attempt"] == 2
        assert processes[0].terminated is True
        assert processes[0].killed is True
        assert any(event["status"] == "running" for event in events)
        assert any(event["status"] == "retrying" for event in events)

    def test_round_gate_rejects_mixed_fingerprints_and_stale_report(
        self, benchmark_module, tmp_path
    ):
        generated = "2026-08-16T10:00:00+00:00"
        round_metadata = {
            "schema_version": 1,
            "created_at": "2026-08-16T09:00:00+00:00",
            "paper_ids": ["first", "second"],
            "engine_commit": "engine-1",
            "engine_fingerprint": "engine-fp",
            "qa_commit": "qa-1",
            "qa_fingerprint": "qa-fp",
            "font_fingerprint": "font-fp",
            "translation_model": "model",
            "translation_config": {"quality": "quality", "qa_max_passes": 4},
        }
        reports = {
            "first": {
                **round_metadata,
                "id": "first",
                "evaluated_at": generated,
                "engine_commit": "engine-1",
                "strict_pass": True,
                "actionable_warning_count": 0,
            },
            "second": {
                **round_metadata,
                "id": "second",
                "evaluated_at": generated,
                "engine_commit": "engine-2",
                "strict_pass": True,
                "actionable_warning_count": 0,
            },
        }
        (tmp_path / "REPORT.md").write_text(
            "Generated: 2026-08-16T09:30:00+00:00\n", encoding="utf-8"
        )

        errors = benchmark_module._round_evidence_errors(
            round_metadata, reports, tmp_path / "REPORT.md"
        )

        assert any("second: engine_commit" in error for error in errors)
        assert any("REPORT.md predates" in error for error in errors)
    def test_pdf_open_retry_recovers_from_transient_failure(
        self, benchmark_module, monkeypatch
    ):
        monkeypatch.setattr(benchmark_module.time, "sleep", lambda _delay: None)
        calls = 0

        def operation(_source: Path, _translated: Path) -> str:
            nonlocal calls
            calls += 1
            if calls < 3:
                raise RuntimeError("Failed to open file '/tmp/result.pdf'.")
            return "ok"

        assert benchmark_module._run_with_pdf_open_retry(
            operation, Path("a.pdf"), Path("b.pdf")
        ) == "ok"
        assert calls == 3

    def test_pdf_open_retry_does_not_hide_other_qa_failures(
        self, benchmark_module, monkeypatch
    ):
        monkeypatch.setattr(benchmark_module.time, "sleep", lambda _delay: None)
        calls = 0

        def operation(_source: Path, _translated: Path) -> None:
            nonlocal calls
            calls += 1
            raise RuntimeError("page analysis failed")

        with pytest.raises(RuntimeError, match="page analysis failed"):
            benchmark_module._run_with_pdf_open_retry(
                operation, Path("a.pdf"), Path("b.pdf")
            )
        assert calls == 1

    def test_pdf_open_retry_stops_after_bounded_attempts(
        self, benchmark_module, monkeypatch
    ):
        monkeypatch.setattr(benchmark_module.time, "sleep", lambda _delay: None)
        calls = 0

        def operation(_source: Path, _translated: Path) -> None:
            nonlocal calls
            calls += 1
            raise OSError("cannot open document")

        with pytest.raises(OSError, match="cannot open document"):
            benchmark_module._run_with_pdf_open_retry(
                operation, Path("a.pdf"), Path("b.pdf")
            )
        assert calls == 3

    def test_load_entries_scopes_and_validates(self, benchmark_module):
        entries = benchmark_module._load_entries("word2vec,adam")
        assert {entry.id for entry in entries} == {"word2vec", "adam"}
        with pytest.raises(SystemExit):
            benchmark_module._load_entries("not-a-paper")

    def test_load_entries_accepts_an_explicit_manifest(
        self, benchmark_module, tmp_path
    ):
        manifest = tmp_path / "manifest.json"
        manifest.write_text(
            json.dumps(
                {
                    "layout_axes": ["two_column"],
                    "papers": [
                        {
                            "id": "held_out",
                            "arxiv_id": "",
                            "title": "Held-out paper",
                            "tags": ["two_column"],
                        }
                    ],
                }
            ),
            encoding="utf-8",
        )

        entries = benchmark_module._load_entries("held_out", manifest)

        assert [entry.id for entry in entries] == ["held_out"]

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

    def test_workdir_lock_rejects_a_second_process(
        self, benchmark_module, tmp_path
    ):
        script = textwrap.dedent(
            f"""
            import importlib.util
            import sys
            import time
            from pathlib import Path

            spec = importlib.util.spec_from_file_location(
                "lock_holder", {str(REPO / 'scripts' / 'classic_benchmark.py')!r}
            )
            module = importlib.util.module_from_spec(spec)
            sys.modules[spec.name] = module
            spec.loader.exec_module(module)
            with module._benchmark_workdir_lock(Path(sys.argv[1]), "holder"):
                print("locked", flush=True)
                time.sleep(30)
            """
        )
        holder = subprocess.Popen(
            [sys.executable, "-c", script, str(tmp_path)],
            stdout=subprocess.PIPE,
            text=True,
        )
        try:
            assert holder.stdout is not None
            assert holder.stdout.readline().strip() == "locked"
            with pytest.raises(SystemExit, match="already in use"):
                with benchmark_module._benchmark_workdir_lock(tmp_path, "contender"):
                    pass
        finally:
            holder.terminate()
            holder.wait(timeout=10)

    def test_inherited_lock_fd_remains_valid_after_parent_exits(
        self, benchmark_module, tmp_path
    ):
        lock_path = tmp_path / ".benchmark.lock"
        script = textwrap.dedent(
            """
            import fcntl
            import os
            import subprocess
            import sys

            path = sys.argv[1]
            handle = open(path, "a+")
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            child = subprocess.Popen(
                [sys.executable, "-c", "import time; time.sleep(30)"],
                pass_fds=(handle.fileno(),),
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            print(child.pid, flush=True)
            """
        )
        parent = subprocess.run(
            [sys.executable, "-c", script, str(lock_path)],
            check=True,
            capture_output=True,
            text=True,
        )
        child_pid = int(parent.stdout.strip())
        try:
            with pytest.raises(SystemExit, match="already in use"):
                with benchmark_module._benchmark_workdir_lock(tmp_path, "contender"):
                    pass
        finally:
            os.kill(child_pid, 15)

    def test_multi_paper_translation_is_always_isolated(
        self, benchmark_module, tmp_path, monkeypatch
    ):
        entries = benchmark_module._load_entries("word2vec,adam")
        calls = []
        monkeypatch.setattr(benchmark_module, "_load_env_file", lambda: None)
        monkeypatch.setattr(
            benchmark_module, "_env", lambda name, default="": "test-api-key"
        )
        def run_process(command, **kwargs):
            calls.append((command, kwargs))
            kwargs["on_event"]({"status": "completed", "attempt": 1})
            return {"status": "completed", "attempt": 1, "return_code": 0}

        monkeypatch.setattr(
            benchmark_module, "_run_isolated_translation_process", run_process
        )

        class Args:
            isolate = False
            force = False
            manifest = tmp_path / "held-out-manifest.json"

        assert benchmark_module.cmd_translate(entries, tmp_path, Args()) == 0
        assert len(calls) == 2
        assert {call[0][call[0].index("--only") + 1] for call in calls} == {
            "word2vec",
            "adam",
        }
        assert all("--isolate" not in call[0] for call in calls)
        assert all(
            call[0][call[0].index("--manifest") + 1] == str(Args.manifest)
            for call in calls
        )
        state = json.loads((tmp_path / "batch-state.json").read_text(encoding="utf-8"))
        assert state["status"] == "completed"
        assert all(paper["status"] == "completed" for paper in state["papers"].values())

    def test_atomic_write_preserves_old_artifact_until_replace(
        self, benchmark_module, tmp_path, monkeypatch
    ):
        destination = tmp_path / "artifact.json"
        destination.write_text("old", encoding="utf-8")
        real_replace = benchmark_module.os.replace

        def fail_replace(source, target):
            assert Path(target) == destination
            raise OSError("injected publish failure")

        monkeypatch.setattr(benchmark_module.os, "replace", fail_replace)
        with pytest.raises(OSError, match="injected"):
            benchmark_module._atomic_write_text(destination, "new")
        assert destination.read_text(encoding="utf-8") == "old"
        assert list(tmp_path.glob(".*.tmp")) == []
        monkeypatch.setattr(benchmark_module.os, "replace", real_replace)
        benchmark_module._atomic_write_text(destination, "new")
        assert destination.read_text(encoding="utf-8") == "new"

    def test_translation_records_schema3_config_times_and_iterative_qa(
        self, benchmark_module, tmp_path, monkeypatch
    ):
        workdir = tmp_path / "bench"
        (workdir / "papers").mkdir(parents=True)
        (workdir / "papers" / "word2vec.pdf").write_bytes(b"%PDF source")
        entries = benchmark_module._load_entries("word2vec")
        monkeypatch.setattr(benchmark_module, "_load_env_file", lambda: None)
        monkeypatch.setattr(
            benchmark_module,
            "_env",
            lambda name, default="": (
                "test-api-key" if name == "DEEPSEEK_API_KEY" else default
            ),
        )
        monkeypatch.setattr(
            benchmark_module, "_current_engine_fingerprint", lambda: "engine-fp"
        )
        monkeypatch.setattr(
            benchmark_module, "_current_font_fingerprint", lambda: "font-fp"
        )
        monkeypatch.setattr(benchmark_module, "_git_commit", lambda: "commit")
        qa_record = {
            "mode": "iterative",
            "max_passes": 4,
            "passes_run": 1,
            "status": "passed",
            "history": [],
        }
        monkeypatch.setattr(
            benchmark_module,
            "_run_iterative_quality_qa",
            lambda *_args, **_kwargs: qa_record,
        )

        import pdf_zh_translator.pdf_layout as layout_module
        import pdf_zh_translator.translators as translator_module

        def fake_translate_pdf(*, output_pdf, **_kwargs):
            output_pdf.write_bytes(b"%PDF translated")
            return SimpleNamespace(page_count=1, translated_blocks=2)

        monkeypatch.setattr(layout_module, "translate_pdf", fake_translate_pdf)
        monkeypatch.setattr(
            translator_module, "VendorTranslator", lambda **_kwargs: object()
        )
        monkeypatch.setattr(
            translator_module,
            "CachedTranslator",
            lambda _vendor, _path: object(),
        )

        class Args:
            isolate = False
            force = True
            quality = "quality"
            qa_mode = "iterative"
            qa_max_passes = 4

        assert benchmark_module.cmd_translate(entries, workdir, Args()) == 0
        timing = json.loads(
            (workdir / "translations" / "word2vec.timing.json").read_text(
                encoding="utf-8"
            )
        )
        assert timing["schema_version"] == 3
        assert timing["started_at"] <= timing["finished_at"]
        assert timing["translation_config"]["qa_mode"] == "iterative"
        assert timing["translation_config"]["qa_max_passes"] == 4
        assert timing["qa_preflight"] == qa_record

    def test_evaluate_persists_actionable_warning_as_strict_failure(
        self, benchmark_module, tmp_path, monkeypatch
    ):
        workdir = tmp_path / "bench"
        for name in ("papers", "translations"):
            (workdir / name).mkdir(parents=True)
        source = workdir / "papers" / "word2vec.pdf"
        translated = workdir / "translations" / "word2vec-mono.pdf"
        source.write_bytes(b"%PDF source")
        translated.write_bytes(b"%PDF translated")
        timing = {
            "schema_version": 3,
            "source_sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
            "translated_sha256": hashlib.sha256(translated.read_bytes()).hexdigest(),
            "engine_fingerprint": "engine-fp",
            "engine_commit": "commit",
            "translation_model": "model",
            "font_fingerprint": "font-fp",
            "translation_config": {"qa_mode": "iterative", "qa_max_passes": 4},
        }
        (workdir / "translations" / "word2vec.timing.json").write_text(
            json.dumps(timing), encoding="utf-8"
        )
        issue = SimpleNamespace(
            page=2,
            code="formula_changed",
            severity="warning",
            message="formula differs",
        )

        import pdf_zh_translator.pdf_layout as layout_module
        import pdf_zh_translator.visual_qa as visual_module

        monkeypatch.setattr(
            layout_module, "verify_translation_issues", lambda *_args: [issue]
        )
        monkeypatch.setattr(
            visual_module,
            "score_visual_layout",
            lambda *_args: SimpleNamespace(
                overall_score=0.9, risk_level="low", original_pages=2
            ),
        )

        class Args:
            force = True

        entries = benchmark_module._load_entries("word2vec")
        assert benchmark_module.cmd_evaluate(entries, workdir, Args()) == 0
        report = json.loads(
            (workdir / "reports" / "word2vec.json").read_text(encoding="utf-8")
        )
        assert report["strict_pass"] is False
        assert report["actionable_warning_count"] == 1
        assert report["actionable_warning_codes"] == ["formula_changed"]

    @pytest.mark.parametrize("drift_kind", ["engine", "font"])
    def test_runtime_fingerprint_drift_never_replaces_previous_pdf(
        self, benchmark_module, tmp_path, monkeypatch, drift_kind
    ):
        workdir = tmp_path / "bench"
        papers = workdir / "papers"
        translations = workdir / "translations"
        papers.mkdir(parents=True)
        translations.mkdir()
        (papers / "word2vec.pdf").write_bytes(b"%PDF source")
        output = translations / "word2vec-mono.pdf"
        output.write_bytes(b"previous-valid-pdf")
        entries = benchmark_module._load_entries("word2vec")

        monkeypatch.setattr(benchmark_module, "_load_env_file", lambda: None)
        monkeypatch.setattr(
            benchmark_module,
            "_env",
            lambda name, default="": (
                "test-api-key" if name == "DEEPSEEK_API_KEY" else default
            ),
        )
        engine_values = iter(["engine-v1", "engine-v2"])
        font_values = iter(["font-v1", "font-v2"])
        monkeypatch.setattr(
            benchmark_module,
            "_current_engine_fingerprint",
            (lambda: next(engine_values)) if drift_kind == "engine" else lambda: "engine-v1",
        )
        monkeypatch.setattr(
            benchmark_module,
            "_current_font_fingerprint",
            (lambda: next(font_values)) if drift_kind == "font" else lambda: "font-v1",
        )

        import pdf_zh_translator.pdf_layout as layout_module
        import pdf_zh_translator.translators as translator_module

        def fake_translate_pdf(*, output_pdf, **_kwargs):
            output_pdf.write_bytes(b"mixed-runtime-pdf")
            return SimpleNamespace(page_count=1, translated_blocks=1)

        monkeypatch.setattr(layout_module, "translate_pdf", fake_translate_pdf)
        monkeypatch.setattr(translator_module, "VendorTranslator", lambda **_kwargs: object())
        monkeypatch.setattr(
            translator_module,
            "CachedTranslator",
            lambda _vendor, _path: object(),
        )

        class Args:
            isolate = False
            force = True

        assert benchmark_module.cmd_translate(entries, workdir, Args()) == 1
        assert output.read_bytes() == b"previous-valid-pdf"
        assert not (translations / "word2vec.timing.json").exists()
        assert list(translations.glob(".word2vec-mono.*.pdf")) == []

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

    def test_report_uses_explicit_frozen_baseline(
        self, benchmark_module, tmp_path
    ):
        workdir = tmp_path / "work"
        reports = workdir / "reports"
        meta = workdir / "meta"
        frozen = tmp_path / "reports_r5v4"
        reports.mkdir(parents=True)
        meta.mkdir(parents=True)
        frozen.mkdir()
        entry = benchmark_module.Entry(
            id="word2vec",
            arxiv_id="1301.3781",
            title="word2vec",
            tags=("single_column",),
        )
        current = {
            "pages": 1,
            "visual_score": 0.9,
            "error_count": 1,
            "issues_by_code": {"font_size_drift": 1},
            "strict_pass": False,
            "legacy_pass": True,
        }
        baseline = {
            "error_count": 4,
            "issues_by_code": {"font_size_drift": 4},
            "strict_pass": False,
            "legacy_pass": False,
        }
        (reports / "word2vec.json").write_text(json.dumps(current), encoding="utf-8")
        (meta / "word2vec.json").write_text(
            json.dumps({"showcase_ok": False}), encoding="utf-8"
        )
        (frozen / "word2vec.json").write_text(json.dumps(baseline), encoding="utf-8")

        class Args:
            no_previews = True
            baseline_reports = frozen

        assert benchmark_module.cmd_report([entry], workdir, Args()) == 0
        showcase = json.loads((workdir / "showcase.json").read_text(encoding="utf-8"))
        assert showcase["comparison"]["baseline"]["error_count"] == 4
        assert showcase["comparison"]["current"]["error_count"] == 1
        assert showcase["comparison"]["baseline_reports"] == str(frozen)

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
