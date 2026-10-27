"""A retry must keep the last readable PDF and the expensive source cache."""

import asyncio
import time
from pathlib import Path

import fitz
import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.api import papers as api
from app.core import database
from app.core.config import settings
from app.core.database import Base
from app.models.paper import Paper
from app.services.translator import TranslationConfig, TranslationResult


def readable_pdf(path: Path):
    path.parent.mkdir(parents=True, exist_ok=True)
    with fitz.open() as doc:
        doc.new_page().insert_text((72, 100), "Previously readable translation")
        doc.save(path)


def test_failed_result_preserves_existing_draft_and_cache(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "base_dir", tmp_path)
    output = settings.translations_path / "paper"
    draft = output / "old-mono.pdf"
    readable_pdf(draft)
    cache = output / "source.translation-cache.jsonl"
    cache.write_text('{"key":"source","translation":"translated"}\n')
    original = draft.read_bytes()
    paper = Paper(id="paper", title="Prior draft", original_filename="source.pdf",
                  stored_filename="source.pdf", translated_filename="paper/old-mono.pdf")
    api._update_paper_result(paper, TranslationResult(error="Supplier unavailable"), output)
    assert draft.is_file(), "A failed retry deleted the existing readable draft"
    assert draft.read_bytes() == original
    assert cache.is_file()
    assert paper.translation_status == "failed"
    assert paper.translation_error == "Supplier unavailable"


@pytest.mark.asyncio
async def test_retry_keeps_previous_outputs_while_worker_runs(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "base_dir", tmp_path)
    monkeypatch.setattr(settings, "translation_recovery_attempts", 1)
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'drafts.db'}")
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    monkeypatch.setattr(database, "async_session", sessions)
    monkeypatch.setattr(api, "_append_log", lambda *a, **kw: None)
    monkeypatch.setattr(api, "_set_translation_stage", lambda *a, **kw: None)
    source = settings.papers_path / "source.pdf"
    readable_pdf(source)
    output = settings.translations_path / "paper"
    draft = output / "old-mono.pdf"
    readable_pdf(draft)
    cache = output / "source.translation-cache.jsonl"
    cache.write_text('{"key":"source","translation":"translated"}\n')
    async with sessions() as db:
        db.add(Paper(id="paper", title="Prior draft", original_filename="source.pdf",
                     stored_filename="source.pdf", translated_filename="paper/old-mono.pdf",
                     translation_status="translating", page_count=1,
                     file_size=source.stat().st_size))
        await db.commit()

    during = []

    async def failed_worker(*args, **kwargs):
        during.append((draft.is_file(), cache.is_file()))
        return TranslationResult(error="Supplier unavailable")

    monkeypatch.setattr(api, "_run_translate_in_worker", failed_worker)
    try:
        await api._do_translate("paper", TranslationConfig(backend="google"), "fast")
        await asyncio.sleep(0)
        assert during == [(True, True)], "Retry erased files before its worker even started"
        assert draft.is_file() and cache.is_file()
        async with sessions() as db:
            paper = await db.get(Paper, "paper")
            assert paper.translation_status == "repairing"
            assert paper.translation_error == "Supplier unavailable"
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_cancel_keeps_existing_draft(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "base_dir", tmp_path)
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'cancel.db'}")
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    monkeypatch.setattr(database, "async_session", sessions)
    monkeypatch.setattr(api, "_append_log", lambda *a, **kw: None)
    output = settings.translations_path / "paper"
    draft = output / "old-mono.pdf"
    readable_pdf(draft)
    async with sessions() as db:
        db.add(Paper(id="paper", title="Prior draft", original_filename="source.pdf",
                     stored_filename="source.pdf", translated_filename="paper/old-mono.pdf"))
        await db.commit()
    try:
        await api._finalize_cancelled_translation(
            "paper", asyncio.get_running_loop(), output, time.monotonic(), None)
        assert draft.is_file(), "Cancelling a retry deleted its previous readable draft"
        async with sessions() as db:
            paper = await db.get(Paper, "paper")
            assert paper.translation_status == "pending"
    finally:
        await engine.dispose()
