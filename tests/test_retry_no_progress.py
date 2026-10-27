"""Do not spend the entire recovery budget replaying unchanged QA failures."""

from types import SimpleNamespace

import pytest

from app.services import quality_agent as qa


def issue(code, *, page=1, severity="error", message="same defect"):
    return SimpleNamespace(code=code, page=page, severity=severity, message=message)


def test_unsupported_formula_damage_does_not_request_identical_retranslation():
    assert qa.plan_quality_retry([issue("formula_changed")]).action is qa.QualityAction.STOP


def test_real_untranslated_prose_can_retry_once():
    assert (
        qa.plan_quality_retry([issue("untranslated_block")]).action is qa.QualityAction.RETRANSLATE
    )


def test_unchanged_blocking_errors_stop_even_when_warning_noise_changes():
    previous = [issue("untranslated_block"), issue("high_risk_layout", severity="warning")]
    current = [issue("untranslated_block"), issue("text_overlap", severity="warning", page=2)]
    assert qa.plan_quality_retry(current, previous).action is qa.QualityAction.STOP


def test_strictly_reduced_blocking_errors_allow_another_supported_retry():
    previous = [issue("untranslated_block"), issue("untranslated_block", page=2)]
    assert qa.plan_quality_retry([previous[0]], previous).action is qa.QualityAction.RETRANSLATE


@pytest.mark.parametrize(
    "new",
    [
        [issue("untranslated_block", page=2)],
        [issue("untranslated_block"), issue("formula_changed")],
    ],
)
def test_same_or_worse_error_count_does_not_masquerade_as_progress(new):
    assert qa.plan_quality_retry(new, [issue("untranslated_block")]).action is qa.QualityAction.STOP


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "reports,expected_calls,expected_status",
    [
        ([[issue("untranslated_block")]] * 3, 2, "repairing"),
        ([[issue("formula_changed")]] * 3, 1, "repairing"),
        ([[issue("untranslated_block")], []], 2, "completed"),
    ],
)
async def test_pipeline_counts_only_actual_attempts_and_preserves_draft(
    tmp_path,
    monkeypatch,
    reports,
    expected_calls,
    expected_status,
):
    from contextlib import asynccontextmanager
    from unittest.mock import AsyncMock, Mock

    import fitz

    import app.core.database as database
    from app.api import papers as api
    from app.services.translator import TranslationConfig, TranslationResult

    papers = tmp_path / "papers"
    papers.mkdir()
    source = papers / "paper.pdf"
    doc = fitz.open()
    doc.new_page()
    doc.save(source)
    doc.close()
    output = tmp_path / "translations" / "paper"
    output.mkdir(parents=True)
    mono = output / "paper-mono.pdf"
    paper = SimpleNamespace(
        id="paper",
        stored_filename="paper.pdf",
        title="Paper",
        page_count=1,
        file_size=source.stat().st_size,
        translated_filename=None,
        dual_filename=None,
        translation_log="",
        translation_status="translating",
        access_scope="local",
    )
    query = Mock()
    query.scalar_one_or_none.return_value = paper
    db = AsyncMock()
    db.execute.return_value = query
    db.get.return_value = SimpleNamespace(
        id="job", paper_id=paper.id, access_scope="local", cancel_requested=False,
        engine_revision="", attempt_count=0, max_attempts=3,
    )

    @asynccontextmanager
    async def sessions():
        yield db

    monkeypatch.setattr(database, "async_session", sessions)
    monkeypatch.setattr(
        api,
        "settings",
        SimpleNamespace(
            papers_path=papers,
            translations_path=tmp_path / "translations",
            translation_recovery_attempts=3,
            translation_recovery_backoff_seconds=0,
            feishu_webhook_url="",
        ),
    )
    for name in ("_append_log", "_set_translation_stage", "_clear_cancel_requested"):
        monkeypatch.setattr(api, name, Mock())
    monkeypatch.setattr(api, "current_engine_revision", lambda: "regression")
    monkeypatch.setattr(api, "_create_progress_handler", Mock(return_value=lambda _: None))
    monkeypatch.setattr(api, "_force_translation_job_terminal_state", AsyncMock())
    job_update = AsyncMock()
    monkeypatch.setattr(api, "_update_translation_job", job_update)

    async def translate(*args, **kwargs):
        mono.write_bytes(b"verified draft candidate")
        return TranslationResult(mono_path=mono)

    worker = AsyncMock(side_effect=translate)
    monkeypatch.setattr(api, "_run_translate_in_worker", worker)
    monkeypatch.setattr(api, "_run_post_translation_qa", Mock(side_effect=reports))
    await api._do_translate("paper", TranslationConfig(backend="google"), "fast", job_id="job")
    assert worker.await_count == expected_calls
    assert paper.translation_status == expected_status
    assert mono.read_bytes() == b"verified draft candidate"
    terminal = [
        c.kwargs
        for c in job_update.await_args_list
        if c.kwargs.get("status") in ("repair_pending", "completed")
    ][-1]
    assert terminal["attempt_count"] == expected_calls
