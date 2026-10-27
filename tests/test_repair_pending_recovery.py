"""Durable recovery must be bounded, owner-scoped and fail closed on QA errors."""

from datetime import datetime

import fitz
import pytest
from sqlalchemy import Column, MetaData, Table, select, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

import app.main as app_main
from app.api import papers as api
from app.core import database
from app.core.config import settings
from app.models.paper import Paper, TranslationJob
from app.services.translator import TranslationConfig, TranslationResult

RECOVERY_COLUMNS = {"attempt_count", "max_attempts", "engine_revision", "last_issue_fingerprint"}


@pytest.mark.asyncio
async def test_unmanaged_failure_is_terminal_and_retains_prior_draft(recovery_state, monkeypatch):
    sessions, draft, cache = recovery_state
    original = draft.read_bytes(), cache.read_bytes()
    # Unregistered files must survive, but do not imply a durable recovery job.
    async with sessions() as db:
        paper = await db.get(Paper, "paper209")
        paper.translated_filename = None
        await db.commit()

    async def unavailable(*args, **kwargs):
        return TranslationResult(error="Supplier unavailable")

    monkeypatch.setattr(api, "_run_translate_in_worker", unavailable)
    await api._do_translate("paper209", TranslationConfig(backend="google"), "fast")
    async with sessions() as db:
        paper = await db.get(Paper, "paper209")
        assert paper.translation_status == "failed"
        assert paper.translation_stage == "翻译失败"
        assert paper.translated_filename is None
    assert (draft.read_bytes(), cache.read_bytes()) == original


@pytest.mark.asyncio
async def test_legacy_draft_retry_creates_owner_scoped_recovery_record(recovery_state, monkeypatch):
    sessions, draft, cache = recovery_state
    original = draft.read_bytes(), cache.read_bytes()

    async def unavailable(*args, **kwargs):
        return TranslationResult(error="Supplier unavailable")

    monkeypatch.setattr(api, "_run_translate_in_worker", unavailable)
    await api._do_translate("paper209", TranslationConfig(backend="google"), "fast")
    async with sessions() as db:
        job = (await db.execute(select(TranslationJob))).scalar_one()
        paper = await db.get(Paper, "paper209")
        assert job.paper_id == paper.id and job.access_scope == paper.access_scope == "alice"
        assert job.status == "repair_pending" and job.attempt_count == 3
        assert job.engine_revision == "engine-a"
        assert paper.translation_status == "repairing"
    assert (draft.read_bytes(), cache.read_bytes()) == original


@pytest.mark.parametrize("protected", [None, "readable", "cache", "symlink", "directory", "qa"])
def test_unmanaged_partial_outputs_are_recoverable(tmp_path, monkeypatch, protected):
    monkeypatch.setattr(settings, "base_dir", tmp_path)
    output = settings.translations_path / "legacy209"
    output.mkdir(parents=True)
    broken = output / "partial.pdf"
    broken.write_bytes(b"incomplete output")
    outside = tmp_path / "not-owned.pdf"
    outside.write_bytes(b"untouched")
    if protected == "readable":
        _pdf(output / "readable.pdf")
    elif protected == "cache":
        (output / "translation-cache.jsonl").write_bytes(b"preserved cache")
    elif protected == "symlink":
        (output / "linked.pdf").symlink_to(outside)
    elif protected == "directory":
        (output / "assets").mkdir()
    elif protected == "qa":
        (output / "partial.qa.json").write_bytes(b'{"verdict":"failed"}')

    moved = api._quarantine_unmanaged_partial_outputs(output)
    assert outside.read_bytes() == b"untouched"
    if protected:
        assert moved is None and broken.read_bytes() == b"incomplete output"
    else:
        assert moved is not None and moved.parent == settings.translations_path
        assert moved.name.startswith(".failed-legacy209-")
        assert not output.exists()
        assert (moved / "partial.pdf").read_bytes() == b"incomplete output"


def test_deleted_unmanaged_invalid_outputs_relocate_qa_without_changing_bytes(
    tmp_path, monkeypatch,
):
    monkeypatch.setattr(settings, "base_dir", tmp_path)
    output = settings.translations_path / "deleted209"
    output.mkdir(parents=True)
    (output / "partial.pdf").write_bytes(b"incomplete output")
    report = output / "partial.qa.json"
    report.write_bytes(b'{"verdict":"failed"}')
    original = report.read_bytes()
    moved = api._quarantine_unmanaged_partial_outputs(output, include_qa_reports=True)
    assert moved is not None and not output.exists()
    assert (moved / report.name).read_bytes() == original


@pytest.mark.asyncio
async def test_unmanaged_owner_transfer_never_quarantines_outputs(recovery_state, monkeypatch):
    sessions, draft, cache = recovery_state
    async with sessions() as db:
        paper = await db.get(Paper, "paper209")
        paper.translated_filename = None
        await db.commit()
    quarantines = []

    async def worker(*args, **kwargs):
        async with sessions() as db:
            paper = await db.get(Paper, "paper209")
            paper.access_scope = "bob"
            await db.commit()
        return TranslationResult(error="Supplier unavailable")

    monkeypatch.setattr(settings, "translation_recovery_attempts", 1)
    monkeypatch.setattr(api, "_run_translate_in_worker", worker)
    monkeypatch.setattr(api, "_quarantine_unmanaged_partial_outputs", quarantines.append)
    await api._do_translate("paper209", TranslationConfig(backend="google"), "fast")
    assert quarantines == [] and draft.is_file() and cache.is_file()
    async with sessions() as db:
        paper = await db.get(Paper, "paper209")
        assert paper.access_scope == "bob" and paper.translation_status == "translating"


def _pdf(path):
    path.parent.mkdir(parents=True, exist_ok=True)
    with fitz.open() as doc:
        doc.new_page().insert_text((72, 100), "Readable source or previous draft")
        doc.save(path)


@pytest.fixture
async def recovery_state(tmp_path, monkeypatch):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'recovery.db'}")
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    async with engine.begin() as connection:
        await connection.run_sync(database.Base.metadata.create_all)
    monkeypatch.setattr(database, "async_session", sessions)
    monkeypatch.setattr(settings, "base_dir", tmp_path)
    if "translation_recovery_attempts" in type(settings).model_fields:
        monkeypatch.setattr(settings, "translation_recovery_attempts", 3)
        monkeypatch.setattr(settings, "translation_recovery_backoff_seconds", 0)
    monkeypatch.setattr(api, "current_engine_revision", lambda: "engine-a", raising=False)
    monkeypatch.setattr(app_main, "current_engine_revision", lambda: "engine-b", raising=False)
    monkeypatch.setattr(api, "_append_log", lambda *args, **kwargs: None)
    monkeypatch.setattr(api, "_set_translation_stage", lambda *args, **kwargs: None)
    monkeypatch.setattr(api, "_run_post_translation_qa", lambda *args, **kwargs: [])
    source = settings.papers_path / "source.pdf"
    draft = settings.translations_path / "paper209" / "previous.pdf"
    cache = draft.parent / "source.translation-cache.jsonl"
    _pdf(source)
    _pdf(draft)
    cache.write_bytes(b'{"translation":"expensive previous translation"}\n')
    async with sessions() as db:
        db.add(Paper(
            id="paper209", access_scope="alice", title="Recovery specimen",
            original_filename="source.pdf", stored_filename="source.pdf",
            translation_status="translating", translated_filename="paper209/previous.pdf",
            page_count=1, file_size=source.stat().st_size,
        ))
        await db.commit()
    try:
        yield sessions, draft, cache
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_retry_backoff_uses_configured_base_and_attempt(recovery_state, monkeypatch):
    _, _, _ = recovery_state
    monkeypatch.setattr(settings, "translation_recovery_backoff_seconds", 12)
    delays = []

    def backoff(value, attempt):
        delays.append((value, attempt))
        return 0

    async def unavailable(*args, **kwargs):
        return TranslationResult(error="Supplier unavailable")

    monkeypatch.setattr(api, "recovery_backoff_seconds", backoff)
    monkeypatch.setattr(api, "_run_translate_in_worker", unavailable)
    await api._do_translate("paper209", TranslationConfig(backend="google"), "fast")
    assert delays == [(12, 2), (12, 3)]


def test_recovery_configuration_and_columns_are_real():
    assert RECOVERY_COLUMNS <= set(TranslationJob.__table__.columns.keys())
    assert {"translation_recovery_attempts", "translation_recovery_backoff_seconds"} <= (
        set(type(settings).model_fields)
    )


@pytest.mark.asyncio
async def test_transient_worker_failure_retries_then_completes(recovery_state, monkeypatch):
    sessions, draft, cache = recovery_state
    original = draft.read_bytes(), cache.read_bytes()
    async with sessions() as db:
        db.add(TranslationJob(id="job209", paper_id="paper209", access_scope="alice"))
        await db.commit()
    attempts = []

    async def worker(*args, **kwargs):
        attempts.append((draft.read_bytes(), cache.read_bytes()))
        if len(attempts) == 1:
            return TranslationResult(error="Supplier unavailable")
        candidate = draft.parent / "candidate.pdf"
        _pdf(candidate)
        return TranslationResult(mono_path=candidate)

    monkeypatch.setattr(api, "_run_translate_in_worker", worker)
    await api._do_translate("paper209", TranslationConfig(backend="google"), "fast",
                            job_id="job209")
    assert attempts == [original, original]
    async with sessions() as db:
        job = await db.get(TranslationJob, "job209")
        paper = await db.get(Paper, "paper209")
        assert job.status == "completed" and job.attempt_count == 2
        assert job.max_attempts == 3 and job.engine_revision == "engine-a"
        assert job.error is None and job.finished_at is not None
        assert paper.translation_status == "completed"


@pytest.mark.asyncio
async def test_permanent_failure_parks_after_exact_budget(recovery_state, monkeypatch):
    sessions, draft, cache = recovery_state
    original = draft.read_bytes(), cache.read_bytes()
    async with sessions() as db:
        db.add(TranslationJob(id="job209", paper_id="paper209", access_scope="alice"))
        await db.commit()
    calls = []

    async def worker(*args, **kwargs):
        calls.append(1)
        return TranslationResult(error="Supplier unavailable")

    monkeypatch.setattr(api, "_run_translate_in_worker", worker)
    await api._do_translate("paper209", TranslationConfig(backend="google"), "fast",
                            job_id="job209")
    assert len(calls) == 3
    assert (draft.read_bytes(), cache.read_bytes()) == original
    async with sessions() as db:
        job = await db.get(TranslationJob, "job209")
        paper = await db.get(Paper, "paper209")
        assert job.status == "repair_pending" and job.attempt_count == job.max_attempts == 3
        assert job.error == paper.translation_error == "Supplier unavailable"
        assert paper.translation_status == "repairing"
        assert paper.translated_filename == "paper209/previous.pdf"


@pytest.mark.asyncio
async def test_same_revision_exhausted_budget_never_calls_supplier(recovery_state, monkeypatch):
    sessions, _, _ = recovery_state
    async with sessions() as db:
        db.add(TranslationJob(
            id="job209", paper_id="paper209", access_scope="alice", status="queued",
            attempt_count=3, max_attempts=3, engine_revision="engine-a",
            error="Previous failure",
        ))
        await db.commit()

    async def forbidden_worker(*args, **kwargs):
        pytest.fail("An exhausted revision must not spend another provider call")

    monkeypatch.setattr(api, "_run_translate_in_worker", forbidden_worker)
    await api._do_translate("paper209", TranslationConfig(backend="google"), "fast",
                            job_id="job209")
    async with sessions() as db:
        job = await db.get(TranslationJob, "job209")
        assert job.status == "repair_pending" and job.attempt_count == 3
        assert job.error == "Previous failure"


@pytest.mark.asyncio
async def test_qa_exception_is_not_a_completed_translation(recovery_state, monkeypatch):
    sessions, draft, _ = recovery_state

    async def worker(*args, **kwargs):
        return TranslationResult(mono_path=draft)

    def unavailable(*args, **kwargs):
        raise RuntimeError("Renderer unavailable")

    monkeypatch.setattr(api, "_run_translate_in_worker", worker)
    monkeypatch.setattr(api, "_run_post_translation_qa", unavailable)
    await api._do_translate("paper209", TranslationConfig(backend="google"), "fast")
    async with sessions() as db:
        paper = await db.get(Paper, "paper209")
        assert paper.translation_status == "repairing"
        assert "Renderer unavailable" in paper.translation_error


@pytest.mark.asyncio
@pytest.mark.parametrize("revision,cancelled,scope,expected", [
    ("engine-a", False, "alice", True),
    ("engine-b", False, "alice", False),
    ("engine-a", True, "alice", False),
    ("engine-a", False, "bob", False),
])
async def test_resume_is_changed_revision_owned_uncancelled_and_once(
    recovery_state, revision, cancelled, scope, expected,
):
    sessions, draft, _ = recovery_state
    resume = getattr(app_main, "_recover_repair_pending_translations", None)
    assert callable(resume), "Production parked-job recovery is missing"
    async with sessions() as db:
        paper = await db.get(Paper, "paper209")
        paper.translation_status = "repairing"
        db.add(TranslationJob(
            id="job209", paper_id="paper209", access_scope=scope,
            status="repair_pending", attempt_count=3, max_attempts=3,
            engine_revision=revision, cancel_requested=cancelled, error="Keep evidence",
        ))
        await db.commit()
    payloads = await resume()
    assert [row["job_id"] for row in payloads] == (["job209"] if expected else [])
    assert await resume() == []
    assert draft.is_file()
    async with sessions() as db:
        job = await db.get(TranslationJob, "job209")
        paper = await db.get(Paper, "paper209")
        assert paper.translated_filename == "paper209/previous.pdf"
        assert job.attempt_count == (0 if expected else 3)
        assert job.status == ("queued" if expected else "repair_pending")
        assert job.error == (None if expected else "Keep evidence")


@pytest.mark.asyncio
async def test_migration_preserves_old_jobs_and_is_idempotent(tmp_path, monkeypatch):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'legacy.db'}")
    metadata = MetaData()
    legacy = Table("translation_jobs", metadata, *[
        Column(c.name, c.type, primary_key=c.primary_key, nullable=c.nullable)
        for c in TranslationJob.__table__.columns if c.name not in RECOVERY_COLUMNS
    ])
    now = datetime.now()
    values = dict(
        id="legacy209", paper_id="paper209", access_scope="alice", backend="google",
        quality="fast", preserve_graphics_text=False, skip_overflow=False, qa_mode="single",
        qa_max_passes=1, ocr_mode="off", ocr_language="eng", ocr_dpi=180, status="failed",
        progress=0.0, cancel_requested=False, error="Preserved failure", created_at=now,
        updated_at=now,
    )
    monkeypatch.setattr(database, "engine", engine)
    try:
        async with engine.begin() as conn:
            await conn.run_sync(metadata.create_all)
            await conn.execute(legacy.insert().values(**values))
        await database.init_db()
        await database.init_db()
        async with engine.connect() as conn:
            columns = {row[1] for row in (await conn.execute(text(
                "PRAGMA table_info(translation_jobs)"
            ))).all()}
            assert RECOVERY_COLUMNS <= columns
            row = (await conn.execute(select(TranslationJob.__table__))).mappings().one()
            assert row["id"] == "legacy209" and row["error"] == "Preserved failure"
            assert row["attempt_count"] == 0 and row["max_attempts"] == 3
            assert row["engine_revision"] == row["last_issue_fingerprint"] == ""
    finally:
        await engine.dispose()
