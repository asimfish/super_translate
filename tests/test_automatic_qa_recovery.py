"""Production QA entry points must repair without a manual retry request."""

import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from app.api import papers as api
from app.services.translator import TranslationResult
from pdf_zh_translator.pdf_layout import TranslationIssue


@pytest.mark.parametrize("first", [None, {}, {"crashed": True, "issues": []}, []])
def test_crashed_or_malformed_checker_recovers_in_a_fresh_process(tmp_path, monkeypatch, first):
    calls = []

    def verify(args, **kwargs):
        spec = json.loads(Path(args[-1]).read_text())
        calls.append(spec)
        payload = first if len(calls) == 1 else {"crashed": False, "issues": []}
        if payload is not None:
            Path(spec["result_path"]).write_text(json.dumps(payload))
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr("subprocess.run", verify)
    assert api._verify_translation_isolated(tmp_path / "source.pdf", tmp_path / "mono.pdf") == []
    assert len(calls) == 2
    assert calls[0]["result_path"] != calls[1]["result_path"]
    assert not list(tmp_path.glob(".verify-*"))


def test_repeated_checker_crash_is_an_error_and_cannot_pass(tmp_path, monkeypatch):
    worker = Mock(return_value=SimpleNamespace(returncode=-11))
    monkeypatch.setattr("subprocess.run", worker)
    issues = api._verify_translation_isolated(tmp_path / "source.pdf", tmp_path / "mono.pdf")
    assert worker.call_count == 2
    assert [(item.code, item.severity) for item in issues] == [("qa_failed", "error")]
    assert api._has_unresolved_error(issues)


def test_pipeline_automatically_renders_and_accepts_cached_layout(tmp_path, monkeypatch):
    from app.services import quality_repair

    source, mono, dual, cache = (tmp_path / name for name in (
        "source.pdf", "mono.pdf", "dual.pdf", "cache.jsonl",
    ))
    for path in (source, mono, dual, cache):
        path.write_bytes(path.name.encode())
    result = TranslationResult(mono, dual, repair_cache_path=cache)
    for name in ("_append_log", "_set_translation_stage", "_record_terminology_candidates",
                 "_audit_terminology_usage"):
        monkeypatch.setattr(api, name, Mock())
    monkeypatch.setattr(api, "_is_cancel_requested", lambda _: False)
    monkeypatch.setattr(api, "_fix_translated_outputs", lambda *a, **kw: False)
    reports = Mock(side_effect=[[
        TranslationIssue(
            page=1, code="font_size_drift", severity="error", message="font too small",
        ),
    ], []])
    monkeypatch.setattr(api, "_verify_translation_isolated", reports)

    def render(spec, **kwargs):
        Path(spec["output_path"]).write_bytes(b"automatically repaired PDF")
        Path(spec["dual_path"]).write_bytes(b"automatically repaired PDF")

    monkeypatch.setattr(quality_repair, "_render_candidate", render)
    unresolved = api._run_post_translation_qa("paper", None, source, result)
    assert unresolved == []
    assert mono.read_bytes() == dual.read_bytes() == b"automatically repaired PDF"
    report = json.loads(mono.with_suffix(".qa.json").read_text())
    assert report["status"] == "passed"
    assert report["repair_attempted"] is True
    assert report["pass_history"][-1]["cached_render"]["accepted"] is True
