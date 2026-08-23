"""Regression coverage for durable translation recovery."""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, patch

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core.database import Base
from app.models.paper import Paper, TranslationJob


@asynccontextmanager
async def _recovery_database(tmp_path, monkeypatch):
    import app.main as app_main

    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'recovery.db'}")
    session_factory = async_sessionmaker(
        engine,
        class_=AsyncSession,
        expire_on_commit=False,
    )
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)

    translations_dir = tmp_path / "translations"
    translations_dir.mkdir()
    monkeypatch.setattr(app_main.settings, "base_dir", tmp_path)
    monkeypatch.setattr(app_main.settings, "translations_dir", translations_dir.name)
    with patch("app.core.database.async_session", session_factory):
        try:
            yield session_factory, translations_dir
        finally:
            await engine.dispose()


def _paper(**overrides) -> Paper:
    values = {
        "id": "paper123456",
        "access_scope": "alice",
        "title": "Recovered paper",
        "original_filename": "paper.pdf",
        "stored_filename": "paper.pdf",
        "translated_filename": None,
        "dual_filename": None,
        "file_size": 100,
        "page_count": 1,
        "translation_status": "translating",
        "translation_progress": 0.0,
        "translation_stage": "等待恢复",
    }
    values.update(overrides)
    return Paper(**values)


def _job(**overrides) -> TranslationJob:
    values = {
        "id": "job123",
        "paper_id": "paper123456",
        "access_scope": "alice",
        "backend": "google",
        "quality": "fast",
        "preserve_graphics_text": True,
        "skip_overflow": False,
        "qa_mode": "single",
        "qa_max_passes": 1,
        "ocr_mode": "off",
        "ocr_language": "eng",
        "ocr_dpi": 180,
        "status": "completed",
        "progress": 1.0,
        "cancel_requested": False,
        "finished_at": datetime.now(timezone.utc),
    }
    values.update(overrides)
    return TranslationJob(**values)


@pytest.mark.asyncio
async def test_reconciles_completed_job_with_existing_output(tmp_path, monkeypatch):
    from app.main import _repair_translation_state_drift

    async with _recovery_database(tmp_path, monkeypatch) as (sessions, translations_dir):
        output = translations_dir / "paper123456" / "translated.pdf"
        output.parent.mkdir()
        output.write_bytes(b"%PDF-1.4\n%%EOF\n")
        async with sessions() as db:
            db.add(
                _paper(
                    translated_filename="paper123456/translated.pdf",
                    translation_error="stale error",
                )
            )
            db.add(_job())
            await db.commit()

        assert await _repair_translation_state_drift() == 1

        async with sessions() as db:
            paper = await db.scalar(select(Paper).where(Paper.id == "paper123456"))
            assert paper is not None
            assert paper.translation_status == "completed"
            assert paper.translation_progress == 1.0
            assert paper.translation_stage == "翻译完成"
            assert paper.translation_error is None


@pytest.mark.asyncio
async def test_completed_job_without_output_becomes_visible_failure(tmp_path, monkeypatch):
    from app.main import _repair_translation_state_drift

    async with _recovery_database(tmp_path, monkeypatch) as (sessions, _translations_dir):
        async with sessions() as db:
            db.add(_paper(translated_filename="paper123456/missing.pdf"))
            db.add(_job())
            await db.commit()

        assert await _repair_translation_state_drift() == 1

        async with sessions() as db:
            paper = await db.scalar(select(Paper).where(Paper.id == "paper123456"))
            job = await db.scalar(select(TranslationJob).where(TranslationJob.id == "job123"))
            assert paper is not None
            assert job is not None
            assert paper.translation_status == "failed"
            assert paper.translation_stage == "恢复失败"
            assert "missing" in (paper.translation_error or "").lower()
            assert job.status == "failed"


@pytest.mark.asyncio
async def test_reconciliation_never_uses_cross_scope_job(tmp_path, monkeypatch):
    from app.main import _repair_translation_state_drift

    async with _recovery_database(tmp_path, monkeypatch) as (sessions, translations_dir):
        output = translations_dir / "paper123456" / "translated.pdf"
        output.parent.mkdir()
        output.write_bytes(b"%PDF-1.4\n%%EOF\n")
        async with sessions() as db:
            db.add(_paper(translated_filename="paper123456/translated.pdf"))
            db.add(_job(access_scope="bob"))
            await db.commit()

        assert await _repair_translation_state_drift() == 0

        async with sessions() as db:
            paper = await db.scalar(select(Paper).where(Paper.id == "paper123456"))
            assert paper is not None
            assert paper.translation_status == "translating"


@pytest.mark.asyncio
async def test_reconciliation_only_uses_latest_same_scope_job(tmp_path, monkeypatch):
    from app.main import _repair_translation_state_drift

    async with _recovery_database(tmp_path, monkeypatch) as (sessions, translations_dir):
        output = translations_dir / "paper123456" / "translated.pdf"
        output.parent.mkdir()
        output.write_bytes(b"%PDF-1.4\n%%EOF\n")
        now = datetime.now(timezone.utc)
        async with sessions() as db:
            db.add(_paper(translated_filename="paper123456/translated.pdf"))
            db.add(_job(id="older", created_at=now - timedelta(seconds=1)))
            db.add(
                _job(
                    id="newer",
                    status="queued",
                    progress=0.0,
                    finished_at=None,
                    created_at=now,
                )
            )
            await db.commit()

        assert await _repair_translation_state_drift() == 0


@pytest.mark.asyncio
async def test_startup_recovery_only_resumes_latest_same_scope_job(tmp_path, monkeypatch):
    import app.main as app_main
    from app.main import _recover_stuck_translations

    async with _recovery_database(tmp_path, monkeypatch) as (sessions, _translations_dir):
        monkeypatch.setattr(app_main, "_RECOVERY_STALE_SECONDS", 0)
        now = datetime.now(timezone.utc)
        async with sessions() as db:
            db.add(_paper())
            db.add(
                _job(
                    id="older",
                    status="queued",
                    progress=0.0,
                    finished_at=None,
                    created_at=now - timedelta(seconds=1),
                )
            )
            db.add(
                _job(
                    id="newer",
                    status="queued",
                    progress=0.0,
                    finished_at=None,
                    created_at=now,
                )
            )
            await db.commit()

        payloads = await _recover_stuck_translations(resume_queued=True)

    assert [payload["job_id"] for payload in payloads] == ["newer"]


@pytest.mark.asyncio
async def test_reconciliation_rejects_output_path_outside_translation_root(
    tmp_path,
    monkeypatch,
):
    from app.main import _repair_translation_state_drift

    outside = tmp_path / "outside.pdf"
    outside.write_bytes(b"%PDF-1.4\n%%EOF\n")
    async with _recovery_database(tmp_path, monkeypatch) as (sessions, _translations_dir):
        async with sessions() as db:
            db.add(_paper(translated_filename="../outside.pdf"))
            db.add(_job())
            await db.commit()

        assert await _repair_translation_state_drift() == 1

        async with sessions() as db:
            paper = await db.get(Paper, "paper123456")
            assert paper is not None
            assert paper.translation_status == "failed"


@pytest.mark.asyncio
async def test_waiting_queued_job_is_discovered_for_reschedule(tmp_path, monkeypatch):
    import app.main as app_main
    from app.main import _find_waiting_translation_jobs

    async with _recovery_database(tmp_path, monkeypatch) as (sessions, _translations_dir):
        monkeypatch.setattr(app_main, "_RECOVERY_STALE_SECONDS", 0)
        async with sessions() as db:
            db.add(_paper(translation_stage="等待恢复"))
            db.add(_job(status="queued", progress=0.0, finished_at=None))
            await db.commit()

        payloads = await _find_waiting_translation_jobs()

    assert [payload["job_id"] for payload in payloads] == ["job123"]


@pytest.mark.asyncio
async def test_recovery_scheduler_deduplicates_and_surfaces_task_crash():
    import app.main as app_main

    payload = {
        "paper_id": "paper123456",
        "access_scope": "alice",
        "backend": "google",
        "quality": "fast",
        "preserve_graphics_text": True,
        "skip_overflow": False,
        "qa_mode": "single",
        "qa_max_passes": 1,
        "ocr_mode": "off",
        "ocr_language": "eng",
        "ocr_dpi": 180,
        "job_id": "job123",
    }
    app_main._scheduled_recovery_job_ids.clear()
    app_main._startup_translation_tasks.clear()
    with (
        patch("app.api.papers._run_translation", side_effect=RuntimeError("worker crashed")),
        patch("app.api.papers._reset_paper_status") as reset_status,
    ):
        assert app_main._schedule_recovered_translation(payload, delay_seconds=0)
        assert not app_main._schedule_recovered_translation(payload, delay_seconds=0)
        await asyncio.gather(*tuple(app_main._startup_translation_tasks))
        await asyncio.sleep(0)

    reset_status.assert_called_once_with(
        "paper123456",
        "Recovered translation task crashed unexpectedly",
        "job123",
    )
    assert not app_main._scheduled_recovery_job_ids
    assert not app_main._startup_translation_tasks


@pytest.mark.asyncio
async def test_repair_recovery_ignores_old_job_after_newer_terminal_job(
    tmp_path,
    monkeypatch,
):
    from app.main import _recover_repair_pending_translations

    async with _recovery_database(tmp_path, monkeypatch) as (sessions, _translations_dir):
        now = datetime.now(timezone.utc)
        async with sessions() as db:
            db.add(
                _paper(
                    translation_status="repairing",
                    translation_stage="等待系统修复",
                )
            )
            db.add(
                _job(
                    id="old-repair",
                    status="repair_pending",
                    engine_revision="old-revision",
                    created_at=now - timedelta(seconds=1),
                )
            )
            db.add(_job(id="new-completed", created_at=now))
            await db.commit()

        with patch("app.main.current_engine_revision", return_value="new-revision"):
            assert await _recover_repair_pending_translations() == []

        async with sessions() as db:
            old_job = await db.get(TranslationJob, "old-repair")
            assert old_job is not None
            assert old_job.status == "repair_pending"


@pytest.mark.asyncio
async def test_repair_recovery_never_uses_cross_scope_job(tmp_path, monkeypatch):
    from app.main import _recover_repair_pending_translations

    async with _recovery_database(tmp_path, monkeypatch) as (sessions, _translations_dir):
        async with sessions() as db:
            db.add(
                _paper(
                    translation_status="repairing",
                    translation_stage="等待系统修复",
                )
            )
            db.add(
                _job(
                    status="repair_pending",
                    access_scope="bob",
                    engine_revision="old-revision",
                )
            )
            await db.commit()

        with patch("app.main.current_engine_revision", return_value="new-revision"):
            assert await _recover_repair_pending_translations() == []


@pytest.mark.asyncio
async def test_watchdog_repairs_terminal_drift_when_resume_is_disabled():
    import app.main as app_main

    with (
        patch(
            "app.main.asyncio.sleep",
            new=AsyncMock(side_effect=[None, asyncio.CancelledError]),
        ),
        patch(
            "app.main._repair_translation_state_drift",
            new=AsyncMock(return_value=1),
        ) as repair,
        patch(
            "app.main._find_waiting_translation_jobs",
            new=AsyncMock(return_value=[]),
        ) as find_jobs,
    ):
        with pytest.raises(asyncio.CancelledError):
            await app_main._translation_recovery_watchdog(resume_queued=False)

    repair.assert_awaited_once()
    find_jobs.assert_not_awaited()
