"""Real SQLite crash/restart boundaries; no native parser or supplier calls."""

from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import Column, MetaData, Table, select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

import app.main as app_main
from app.core import database
from app.models.paper import Paper, TranslationJob


def _stale():
    return datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(minutes=10)


def _paper(scope="alice", status="translating"):
    return Paper(
        id="restart211", access_scope=scope, title="Synthetic restart specimen",
        original_filename="source.pdf", stored_filename="source.pdf",
        translated_filename="restart211/retained.pdf", translation_status=status,
        translation_stage="等待恢复", translation_error="Preserved error",
    )


def _job(status="queued", scope="alice", cancelled=False):
    stale = _stale()
    return TranslationJob(
        id="restartjob211", paper_id="restart211", access_scope=scope,
        backend="deepseek", quality="balanced", status=status,
        cancel_requested=cancelled, created_at=stale, updated_at=stale,
        attempt_count=2, max_attempts=3, engine_revision="old-engine",
        last_issue_fingerprint="preserved-failure", error="Preserved error",
    )


@pytest.fixture
async def restart_db(tmp_path, monkeypatch):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'restart.db'}")
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    monkeypatch.setattr(database, "engine", engine)
    monkeypatch.setattr(database, "async_session", sessions)
    monkeypatch.setattr(app_main, "current_engine_revision", lambda: "new-engine")
    await database.init_db()
    try:
        yield sessions
    finally:
        await engine.dispose()


@pytest.mark.asyncio
@pytest.mark.parametrize("status", ["queued", "running"])
@pytest.mark.parametrize("cancelled", [False, True])
@pytest.mark.parametrize("enabled", [False, True])
async def test_restart_never_resurrects_cancelled_job(
    restart_db, status, cancelled, enabled,
):
    async with restart_db() as db:
        db.add(_paper())
        await db.flush()
        db.add(_job(status=status, cancelled=cancelled))
        await db.commit()
    payloads = await app_main._recover_stuck_translations(resume_queued=enabled)
    assert [item["job_id"] for item in payloads] == (
        ["restartjob211"] if enabled and not cancelled else []
    )
    async with restart_db() as db:
        job = await db.get(TranslationJob, "restartjob211")
        paper = await db.get(Paper, "restart211")
        assert job.cancel_requested is cancelled
        assert job.access_scope == "alice"
        assert job.attempt_count == 2 and job.max_attempts == 3
        assert job.engine_revision == "old-engine"
        assert job.last_issue_fingerprint == "preserved-failure"
        assert paper.translated_filename == "restart211/retained.pdf"
        assert paper.translation_status == (
            "translating" if enabled and not cancelled else "failed"
        )


@pytest.mark.asyncio
@pytest.mark.parametrize("cancelled", [False, True])
async def test_watchdog_respects_persisted_cancel_request(restart_db, cancelled):
    async with restart_db() as db:
        db.add(_paper())
        await db.flush()
        db.add(_job(cancelled=cancelled))
        await db.commit()
    payloads = await app_main._find_waiting_translation_jobs()
    assert [item["job_id"] for item in payloads] == (
        [] if cancelled else ["restartjob211"]
    )
    async with restart_db() as db:
        job = await db.get(TranslationJob, "restartjob211")
        assert job.cancel_requested is cancelled and job.attempt_count == 2


@pytest.mark.asyncio
async def test_cancel_committed_between_discovery_and_claim_is_not_resumed(
    restart_db, monkeypatch,
):
    async with restart_db() as db:
        db.add(_paper())
        await db.flush()
        db.add(_job())
        await db.commit()
    original_execute = AsyncSession.execute
    discovery_reads = 0

    async def cancel_after_discovery(session, statement, *args, **kwargs):
        nonlocal discovery_reads
        result = await original_execute(session, statement, *args, **kwargs)
        descriptions = getattr(statement, "column_descriptions", ())
        if descriptions and descriptions[0].get("entity") is TranslationJob:
            discovery_reads += 1
            if discovery_reads == 2:
                # A separate durable transaction, before recovery performs its
                # first write. No sleeps, same-session fake or supplier calls.
                async with restart_db() as cancelling:
                    await original_execute(
                        cancelling, update(TranslationJob)
                        .where(TranslationJob.id == "restartjob211")
                        .values(cancel_requested=True),
                    )
                    await cancelling.commit()
        return result

    monkeypatch.setattr(AsyncSession, "execute", cancel_after_discovery)
    payloads = await app_main._recover_stuck_translations(resume_queued=True)
    assert discovery_reads == 2
    assert payloads == []
    async with restart_db() as db:
        job = await db.get(TranslationJob, "restartjob211")
        assert job.cancel_requested is True
        assert job.attempt_count == 2 and job.engine_revision == "old-engine"


@pytest.mark.asyncio
@pytest.mark.parametrize("status,new_owner", [
    ("queued", "bob"), ("running", "bob"), ("queued", "alice"),
])
async def test_owner_or_paper_state_changed_before_claim_is_not_overwritten(
    restart_db, monkeypatch, status, new_owner,
):
    async with restart_db() as db:
        db.add(_paper())
        await db.flush()
        db.add(_job(status=status))
        await db.commit()
    original_execute = AsyncSession.execute
    discovery_reads = 0

    async def transfer_after_discovery(session, statement, *args, **kwargs):
        nonlocal discovery_reads
        result = await original_execute(session, statement, *args, **kwargs)
        descriptions = getattr(statement, "column_descriptions", ())
        if descriptions and descriptions[0].get("entity") is TranslationJob:
            discovery_reads += 1
            if discovery_reads == 2:
                async with restart_db() as transferring:
                    await original_execute(
                        transferring, update(Paper).where(Paper.id == "restart211")
                        .values(access_scope=new_owner, translation_status="pending",
                                translation_stage="New owner stage",
                                translation_error="New owner error",
                                translation_progress=0.25),
                    )
                    await transferring.commit()
        return result

    monkeypatch.setattr(AsyncSession, "execute", transfer_after_discovery)
    payloads = await app_main._recover_stuck_translations(resume_queued=True)
    assert discovery_reads == 2
    assert payloads == []
    async with restart_db() as db:
        paper = await db.get(Paper, "restart211")
        job = await db.get(TranslationJob, "restartjob211")
        assert paper.access_scope == new_owner
        assert paper.translation_status == "pending"
        assert paper.translation_stage == "New owner stage"
        assert paper.translation_error == "New owner error"
        assert paper.translation_progress == 0.25
        assert paper.translated_filename == "restart211/retained.pdf"
        assert job.access_scope == "alice" and job.status == status
        assert job.attempt_count == 2 and job.max_attempts == 3
        assert job.engine_revision == "old-engine"
        assert job.last_issue_fingerprint == "preserved-failure"
        assert job.error == "Preserved error"


@pytest.mark.asyncio
@pytest.mark.parametrize("original_scope", ["alice", "local", "bob"])
async def test_init_db_preserves_explicit_job_owner_across_restarts(
    restart_db, original_scope,
):
    async with restart_db() as db:
        db.add(_paper(scope="bob", status="repairing"))
        await db.flush()
        db.add(_job(status="repair_pending", scope=original_scope))
        await db.commit()
    await database.init_db()
    await database.init_db()
    async with restart_db() as db:
        job = await db.get(TranslationJob, "restartjob211")
        assert job.access_scope == original_scope
        assert job.error == "Preserved error" and job.attempt_count == 2
    payloads = await app_main._recover_repair_pending_translations()
    assert [item["job_id"] for item in payloads] == (
        ["restartjob211"] if original_scope == "bob" else []
    )
    async with restart_db() as db:
        job = await db.get(TranslationJob, "restartjob211")
        paper = await db.get(Paper, "restart211")
        assert job.access_scope == original_scope and paper.access_scope == "bob"
        assert paper.translated_filename == "restart211/retained.pdf"
        if original_scope != "bob":
            assert job.status == "repair_pending" and job.attempt_count == 2
            assert job.error == "Preserved error"
            assert paper.translation_status == "repairing"


@pytest.mark.asyncio
async def test_owner_backfill_only_for_genuinely_ownerless_legacy_schema(
    tmp_path, monkeypatch,
):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'ownerless.db'}")
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    monkeypatch.setattr(database, "engine", engine)
    metadata = MetaData()
    legacy = Table("translation_jobs", metadata, *[
        Column(column.name, column.type, primary_key=column.primary_key,
               nullable=column.nullable)
        for column in TranslationJob.__table__.columns if column.name != "access_scope"
    ])
    now = _stale()
    values = dict(
        id="restartjob211", paper_id="restart211", backend="deepseek", quality="balanced",
        preserve_graphics_text=False, skip_overflow=False, qa_mode="single", qa_max_passes=4,
        ocr_mode="off", ocr_language="eng", ocr_dpi=180, attempt_count=2, max_attempts=3,
        engine_revision="old-engine", last_issue_fingerprint="preserved-failure",
        status="repair_pending", progress=0.0, cancel_requested=False,
        error="Preserved error", created_at=now, updated_at=now,
    )
    try:
        async with engine.begin() as conn:
            await conn.run_sync(metadata.create_all)
            await conn.run_sync(database.Base.metadata.create_all)
            await conn.execute(legacy.insert().values(**values))
        async with sessions() as db:
            db.add(_paper(scope="alice", status="repairing"))
            await db.commit()
        await database.init_db()
        async with sessions() as db:
            job = await db.get(TranslationJob, "restartjob211")
            assert job.access_scope == "alice"
            paper = await db.get(Paper, "restart211")
            paper.access_scope = "bob"
            await db.commit()
        await database.init_db()
        async with sessions() as db:
            job = (await db.execute(select(TranslationJob))).scalar_one()
            assert job.access_scope == "alice"
            assert job.attempt_count == 2 and job.error == "Preserved error"
            assert job.last_issue_fingerprint == "preserved-failure"
    finally:
        await engine.dispose()


@pytest.mark.asyncio
@pytest.mark.parametrize("status", ["queued", "running"])
async def test_owner_only_transfer_before_claim_preserves_translating_new_owner(
    restart_db, monkeypatch, status,
):
    async with restart_db() as db:
        db.add(_paper())
        await db.flush()
        db.add(_job(status=status))
        await db.commit()
    original_execute = AsyncSession.execute
    discovery_reads = 0

    async def transfer_after_discovery(session, statement, *args, **kwargs):
        nonlocal discovery_reads
        result = await original_execute(session, statement, *args, **kwargs)
        descriptions = getattr(statement, "column_descriptions", ())
        if descriptions and descriptions[0].get("entity") is TranslationJob:
            discovery_reads += 1
            if discovery_reads == 2:
                async with restart_db() as transferring:
                    await original_execute(
                        transferring, update(Paper).where(Paper.id == "restart211")
                        .values(access_scope="bob", translation_status="translating",
                                translation_stage="New owner active stage",
                                translation_error="New owner active error",
                                translation_progress=0.25),
                    )
                    await transferring.commit()
        return result

    monkeypatch.setattr(AsyncSession, "execute", transfer_after_discovery)
    payloads = await app_main._recover_stuck_translations(resume_queued=True)
    assert discovery_reads == 2
    assert payloads == []
    async with restart_db() as db:
        paper = await db.get(Paper, "restart211")
        job = await db.get(TranslationJob, "restartjob211")
        assert paper.access_scope == "bob" and paper.translation_status == "translating"
        assert paper.translation_stage == "New owner active stage"
        assert paper.translation_error == "New owner active error"
        assert paper.translation_progress == 0.25
        assert paper.translated_filename == "restart211/retained.pdf"
        assert job.access_scope == "alice" and job.status == status
        assert job.attempt_count == 2 and job.max_attempts == 3
        assert job.engine_revision == "old-engine"
        assert job.last_issue_fingerprint == "preserved-failure"
        assert job.error == "Preserved error"
