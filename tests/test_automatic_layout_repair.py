"""A rejected draft must try different safe cached renders without user input."""

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from app.services import quality_repair as repair


def issue(code="font_size_drift", page=1, severity="error"):
    return SimpleNamespace(code=code, page=page, severity=severity, message=code)


@pytest.fixture
def draft(tmp_path):
    source = tmp_path / "source.pdf"
    mono = tmp_path / "mono.pdf"
    dual = tmp_path / "dual.pdf"
    cache = tmp_path / "model-a.jsonl"
    for path in (source, mono, dual, cache):
        path.write_bytes(path.name.encode())
    result = SimpleNamespace(
        mono_path=mono, dual_path=dual, repair_cache_path=cache,
        repair_preserve_graphics_text=True,
    )
    return source, result


def run(draft, monkeypatch, reports, renderer=None, cancelled=lambda: False):
    source, result = draft

    def render(spec, *, cancelled, timeout):
        Path(spec["output_path"]).write_bytes(spec["profile"].encode())
        Path(spec["dual_path"]).write_bytes(spec["profile"].encode())

    renderer = Mock(side_effect=renderer or render)
    monkeypatch.setattr(repair, "_render_candidate", renderer)
    verify = Mock(side_effect=reports)
    outcome = repair.repair_cached_layout(
        source, result, [issue()], verify=verify, cancelled=cancelled,
        on_stage=Mock(), timeout=10,
    )
    return outcome, renderer, verify


def test_rejected_layout_automatically_tries_next_profile_and_completes(draft, monkeypatch):
    outcome, render, verify = run(draft, monkeypatch, [[issue()], []])
    assert not outcome.issues
    assert render.call_count == verify.call_count == 2
    assert draft[1].mono_path.read_bytes() == b"compact_spacing"
    assert draft[1].dual_path.read_bytes() == b"compact_spacing"
    assert [row["accepted"] for row in outcome.history] == [False, True]


def test_repair_that_introduces_formula_damage_is_rejected(draft, monkeypatch):
    outcome, _, _ = run(draft, monkeypatch, [[issue("formula_changed")], [issue()]])
    assert outcome.issues[0].code == "font_size_drift"
    assert draft[1].mono_path.read_bytes() == b"mono.pdf"
    assert draft[1].dual_path.read_bytes() == b"dual.pdf"
    assert all(not row["accepted"] for row in outcome.history)


def test_cached_repair_has_no_supplier_or_credentials_in_spec(draft, monkeypatch):
    _, render, _ = run(draft, monkeypatch, [[]])
    spec = render.call_args.args[0]
    assert set(spec) == {
        "input_path", "output_path", "dual_path", "cache_path", "profile",
        "preserve_graphics_text",
    }
    assert Path(spec["cache_path"]).name == "translation-cache.jsonl"


def test_no_cache_or_foreign_cache_cannot_trigger_render(draft, monkeypatch, tmp_path):
    source, result = draft
    foreign = tmp_path / "other-owner"
    foreign.mkdir()
    result.repair_cache_path = foreign / "cache.jsonl"
    result.repair_cache_path.write_text("another owner's cache")
    render = Mock()
    monkeypatch.setattr(repair, "_render_candidate", render)
    outcome = repair.repair_cached_layout(
        source, result, [issue()], verify=Mock(), cancelled=lambda: False, on_stage=Mock(),
    )
    assert outcome.history == []
    render.assert_not_called()


def test_partial_candidate_without_dual_retains_both_original_files(draft, monkeypatch):
    def partial(spec, **kwargs):
        Path(spec["output_path"]).write_bytes(b"candidate without dual")

    outcome, _, _ = run(draft, monkeypatch, [[], []], renderer=partial)
    assert outcome.issues[0].code == "font_size_drift"
    assert draft[1].mono_path.read_bytes() == b"mono.pdf"
    assert draft[1].dual_path.read_bytes() == b"dual.pdf"
    assert all(item["status"] == "candidate_failed" for item in outcome.history)


def test_candidate_crash_retains_draft_and_moves_to_another_profile(draft, monkeypatch):
    outcome, render, _ = run(draft, monkeypatch, [], renderer=RuntimeError("native crash"))
    assert render.call_count == 2
    assert draft[1].mono_path.read_bytes() == b"mono.pdf"
    assert len(outcome.history) == 2


def test_cancel_prevents_any_candidate_work(draft, monkeypatch):
    with pytest.raises(repair.RepairCancelled):
        run(draft, monkeypatch, [], cancelled=lambda: True)
    assert draft[1].mono_path.read_bytes() == b"mono.pdf"


def test_unknown_error_alone_does_not_schedule_irrelevant_layout_profiles(draft, monkeypatch):
    source, result = draft
    render = Mock()
    monkeypatch.setattr(repair, "_render_candidate", render)
    outcome = repair.repair_cached_layout(
        source, result, [issue("formula_changed")], verify=Mock(),
        cancelled=lambda: False, on_stage=Mock(),
    )
    assert not outcome.history
    render.assert_not_called()


def test_real_cached_render_repairs_crushed_fonts_without_a_supplier(tmp_path):
    import json

    import fitz

    from pdf_zh_translator.pdf_layout import prepare_translation_units, verify_translation_issues
    from pdf_zh_translator.translators import cache_key

    source = tmp_path / "source.pdf"
    mono = tmp_path / "mono.pdf"
    dual = tmp_path / "dual.pdf"
    cache = tmp_path / "cache.jsonl"
    text = "This paragraph explains the scientific method and reports the experimental results."
    chinese = "本段介绍科学方法，并报告实验结果，为研究结论提供支持。"
    with fitz.open() as original, fitz.open() as crushed:
        page = original.new_page()
        translated = crushed.new_page()
        for y in (120, 240, 360, 480):
            page.insert_textbox(fitz.Rect(72, y, 400, y + 60), text, fontsize=12)
            translated.insert_textbox(
                fitz.Rect(72, y, 400, y + 60), chinese, fontsize=4, fontname="china-s",
            )
        original.save(source)
        crushed.save(mono)
    with fitz.open(source) as document:
        units, _, _ = prepare_translation_units(document, preserve_graphics_text=True)
    cache.write_text("\n".join(json.dumps({
        "key": cache_key(text), "source": text, "translation": chinese,
    }, ensure_ascii=False) for _, text, _ in units) + "\n")
    repair._create_dual(source, mono, dual)
    before = verify_translation_issues(source, mono)
    assert any(item.code == "font_size_drift" and item.severity == "error" for item in before)
    result = SimpleNamespace(
        mono_path=mono, dual_path=dual, repair_cache_path=cache,
        repair_preserve_graphics_text=True,
    )
    outcome = repair.repair_cached_layout(
        source, result, before, verify=verify_translation_issues,
        cancelled=lambda: False, on_stage=Mock(),
    )
    assert not [item for item in outcome.issues if item.severity == "error"], outcome.history
    # Font metrics differ across macOS and Linux. Either registered profile
    # may be the successful one; the final document must pass the same QA.
    assert any(candidate["accepted"] for candidate in outcome.history)
    with fitz.open(mono) as repaired, fitz.open(dual) as paired:
        assert repaired.page_count == 1 and paired.page_count == 2
        assert paired[1].get_text() == repaired[0].get_text()


def test_repair_child_supports_legacy_cache_constructor(tmp_path, monkeypatch):
    import json
    import sys

    from pdf_zh_translator import pdf_layout, translators

    class LegacyCache:
        def __init__(self, cache_file):
            self.cache_file = cache_file

    spec = tmp_path / "candidate.json"
    spec.write_text(json.dumps({
        "input_path": "source.pdf", "output_path": "candidate.pdf",
        "cache_path": "cache.jsonl", "profile": "source_font", "preserve_graphics_text": True,
    }))
    render = Mock()
    monkeypatch.setattr(translators, "CacheOnlyTranslator", LegacyCache)
    monkeypatch.setattr(pdf_layout, "translate_pdf", render)
    monkeypatch.setattr(sys, "argv", ["repair", str(spec)])
    repair.main()
    assert render.call_args.args[2].supports_source_style_segments is True


@pytest.mark.parametrize("cancelled", [False, True])
def test_inflight_candidate_is_killed_on_timeout_or_cancellation(tmp_path, monkeypatch, cancelled):
    process = Mock()
    process.poll.return_value = None
    monkeypatch.setattr(repair.subprocess, "Popen", Mock(return_value=process))
    monkeypatch.setattr(repair.time, "monotonic", Mock(side_effect=[0, 2]))
    output = tmp_path / "candidate.pdf"
    expected = repair.RepairCancelled if cancelled else TimeoutError
    with pytest.raises(expected):
        repair._render_candidate(
            {"output_path": str(output)}, cancelled=lambda: cancelled, timeout=1,
        )
    process.kill.assert_called_once()
    process.wait.assert_called_once()
    assert not output.with_suffix(".json").exists()
