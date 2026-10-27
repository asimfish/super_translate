"""A repaired engine resumes canonical documents once within their owner scope."""

from datetime import datetime

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

import app.main as app_main
from app.core import database
from app.core.database import Base
from app.models.document import PaperDocument
from app.models.paper import Paper, TranslationJob

pytestmark = pytest.mark.skipif(
    not hasattr(app_main, "_recover_repair_pending_translations"),
    reason="Automatic repair recovery is currently provided by the production mirror",
)


@pytest.mark.asyncio
@pytest.mark.parametrize("canonical_state,canonical_scope,identity_scope,expected", [
    ("repairing", "alice", "alice", {"canonical123"}),
    ("completed", "alice", "alice", set()),
    ("completed", "bob", "alice", {"duplicate123"}),
    ("completed", "alice", "bob", {"duplicate123"}),
])
async def test_recovery_does_not_repeat_owned_canonical_content(
    tmp_path, monkeypatch, canonical_state, canonical_scope, identity_scope, expected,
):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'recovery.db'}")
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    monkeypatch.setattr(database, "async_session", sessions)
    monkeypatch.setattr(app_main, "current_engine_revision", lambda: "repaired-engine")
    now = datetime.now()
    async with sessions() as db:
        for paper_id, scope, state in [
            ("canonical123", canonical_scope, canonical_state),
            ("duplicate123", "alice", "repairing"),
        ]:
            db.add(Paper(
                id=paper_id, access_scope=scope, title="Source paper",
                original_filename="source.pdf", stored_filename=paper_id + ".pdf",
                translation_status=state, translation_error="Original quality error",
                translated_filename=paper_id + "/last-readable.pdf",
            ))
            db.add(TranslationJob(
                id=paper_id + "-job", paper_id=paper_id, access_scope=scope,
                status="repair_pending" if state == "repairing" else "completed",
                engine_revision="old-engine", attempt_count=3,
                error="Original quality error", backend="deepseek", quality="balanced",
            ))
        await db.flush()
        db.add(PaperDocument(
            paper_id="duplicate123", access_scope=identity_scope,
            canonical_paper_id="canonical123", file_sha256="1" * 64,
            content_sha256="2" * 64, title_key="source paper",
            first_imported_at=now, last_imported_at=now,
        ))
        await db.commit()
    try:
        payloads = await app_main._recover_repair_pending_translations()
        assert {row["paper_id"] for row in payloads} == expected
        async with sessions() as db:
            for paper_id in ("canonical123", "duplicate123"):
                paper = await db.get(Paper, paper_id)
                job = await db.get(TranslationJob, paper_id + "-job")
                assert paper.translated_filename == paper_id + "/last-readable.pdf"
                if paper_id in expected:
                    assert job.status == "queued" and job.attempt_count == 0
                    assert job.engine_revision == "repaired-engine"
                else:
                    assert job.attempt_count == 3
                    assert job.error == "Original quality error"
                    assert job.status != "queued"
    finally:
        await engine.dispose()
