"""Authorized JSONL upload -> durable offline job -> real renderer, without a supplier."""
import asyncio
import json
from pathlib import Path

import fitz
import httpx
import pytest
from fastapi import FastAPI
from pydantic import SecretStr
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.api import papers as api
from app.core import database, users
from app.core.config import settings
from app.core.database import Base, get_session
from app.models.paper import Paper, TranslationJob
from app.services.offline_cache import MAX_CACHE_BYTES, validate_cache
from pdf_zh_translator import translators
from pdf_zh_translator.pdf_layout import translation_unit_source_texts

PAPER_ID = "cace00000001"


def record(
    source="This method improves the accuracy of scientific translation.",
    translation="该方法提高科学翻译的准确率。",
):
    return json.dumps({"key": translators.cache_key(source), "source": source,
                       "translation": translation}, ensure_ascii=False).encode() + b"\n"


@pytest.fixture
async def offline_client(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "base_dir", tmp_path)
    monkeypatch.setattr(settings, "workspace_tokens", "alice:cache-alice,bob:cache-bob")
    monkeypatch.setattr(settings, "api_token", SecretStr(""))
    monkeypatch.setenv("PYTHONPATH", str(Path(__file__).resolve().parents[1]))

    async def no_user_tokens():
        return {}
    monkeypatch.setattr(users, "refresh_token_scopes", no_user_tokens)
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'cache.db'}")
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    monkeypatch.setattr(database, "async_session", sessions)
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    source = settings.papers_path / "source.pdf"
    source.parent.mkdir(parents=True)
    with fitz.open() as document:
        for number in range(2):
            page = document.new_page()
            page.insert_text(
                (72, 100), "This method improves the accuracy of scientific translation.",
            )
            page.insert_text(
                (72, 130), "We present experimental evidence for the proposed approach.",
            )
        document.save(source)
    payload = b"".join(record(text) for text in translation_unit_source_texts(source))
    async with sessions() as db:
        db.add(Paper(
            id=PAPER_ID, access_scope="alice", title="Offline specimen", page_count=2,
            original_filename="source.pdf", stored_filename="source.pdf",
        ))
        await db.commit()
    async def session_dependency():
        async with sessions() as db:
            yield db
    scheduled = []
    monkeypatch.setattr(api, "_schedule_background_task", lambda *args: scheduled.append(args))
    def no_supplier(*args, **kwargs):
        pytest.fail("Offline rendering must never construct a supplier")
    monkeypatch.setattr(translators, "VendorTranslator", no_supplier)
    app = FastAPI()
    app.include_router(api.router)
    app.dependency_overrides[get_session] = session_dependency
    try:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://127.0.0.1",
            headers={"Authorization": "Bearer cache-alice"},
        ) as client:
            yield client, sessions, payload, scheduled
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_cache_upload_survives_worker_restart_and_real_pdf_is_readable(offline_client):
    client, sessions, payload, scheduled = offline_client
    response = await client.post(
        f"/api/papers/{PAPER_ID}/translate?cache_only=true&quality=fast",
        files={"cache_file": ("../../untrusted.jsonl", payload)},
    )
    assert response.status_code == 200, response.text
    job_id = response.json()["job_id"]
    async with sessions() as db:
        job = await db.get(TranslationJob, job_id)
        assert job.backend == "cache-only" and job.status == "queued"
    # Execute the persisted queue payload through the real worker/subprocess,
    # renderer and post-translation QA. No API key exists in this fixture.
    function, *args = scheduled.pop()
    await asyncio.to_thread(function, *args)
    async with sessions() as db:
        job = await db.get(TranslationJob, job_id)
        paper = await db.get(Paper, PAPER_ID)
        assert job.status == "completed", (job.error, paper.translation_log)
        assert paper.translation_status == "completed"
    pdf = await client.get(f"/api/papers/{PAPER_ID}/download/translated")
    assert pdf.status_code == 200
    with fitz.open(stream=pdf.content, filetype="pdf") as document:
        assert document.page_count == 2
        assert all("科学翻译" in page.get_text() for page in document)
    exported = await client.get(f"/api/papers/{PAPER_ID}/translation-cache")
    assert exported.status_code == 200
    assert validate_cache(exported.content) == validate_cache(payload)
    # Reuse after a new request: no filename supplied, same persisted cache.
    response = await client.post(f"/api/papers/{PAPER_ID}/translate?cache_only=true")
    assert response.status_code == 200


@pytest.mark.asyncio
@pytest.mark.parametrize("case", ["other_owner", "invalid_json", "bad_key", "oversize", "missing"])
async def test_cache_rejection_does_not_schedule_or_change_paper(offline_client, case):
    client, sessions, payload, scheduled = offline_client
    headers = {"Authorization": "Bearer cache-bob"} if case == "other_owner" else {}
    data = {"invalid_json": b"invalid", "bad_key": b'{"key":"bad","source":"s","translation":"t"}',
            "oversize": b"x" * (MAX_CACHE_BYTES + 1)}.get(case, payload)
    files = {"cache_file": ("cache.jsonl", data)} if case != "missing" else None
    response = await client.post(
        f"/api/papers/{PAPER_ID}/translate?cache_only=true", files=files, headers=headers,
    )
    assert response.status_code == (404 if case == "other_owner" else 400)
    assert not scheduled
    async with sessions() as db:
        paper = await db.get(Paper, PAPER_ID)
        assert paper.translation_status == "pending"
        assert not (await db.execute(select(TranslationJob))).scalars().all()
    if case == "other_owner":
        response = await client.get(f"/api/papers/{PAPER_ID}/translation-cache", headers=headers)
        assert response.status_code == 404


@pytest.mark.parametrize("payload", [b"", b"[]", b"\xff", record("x ⟦0⟧", "缺少占位符")])
def test_invalid_cache_fails_closed(payload):
    with pytest.raises(ValueError):
        validate_cache(payload)


@pytest.mark.parametrize("segments", [False, True])
def test_progress_wrapper_replays_whole_or_style_segment_cache(tmp_path, segments):
    from app.services.translator import _ProgressTranslator
    from pdf_zh_translator.pdf_layout import (
        TextBlock,
        _source_style_translation_plan,
        _translate_units_with_source_style_runs,
    )
    source = "Task: Slide the block to the square target."
    block = TextBlock(
        0, (0, 0, 240, 24), source, 10, (0, 0, 0), starts_bold=True,
        bold_terms=("Task",), bold_prefix=True,
    )
    sources, _, _ = _source_style_translation_plan(object(), [(block, source, {})])
    assert len(sources) == 2
    path = tmp_path / "cache.jsonl"
    path.write_bytes(
        b"".join(record(text, "任务：" if index == 0 else "将滑块移动到方形目标。")
                 for index, text in enumerate(sources))
        if segments else record(source, "任务：将滑块移动到方形目标。")
    )
    progress = []
    wrapped = _ProgressTranslator(
        translators.CacheOnlyTranslator(path, segment_source_styles=True), progress.append,
    )
    result, requested, received = _translate_units_with_source_style_runs(
        wrapped, [(block, source, {})],
    )
    assert result[0].replace(" ", "") == "任务：将滑块移动到方形目标。"
    assert requested == received == (2 if segments else 1)
    assert progress and progress[-1] == 1.0


def test_canonical_cache_size_limit_applies_to_persisted_snapshot(monkeypatch):
    from app.services import offline_cache

    payload = record("source", "translation").replace(b": ", b":").replace(b", ", b",")
    monkeypatch.setattr(offline_cache, "MAX_CACHE_BYTES", len(payload))
    with pytest.raises(ValueError, match="规范化"):
        offline_cache.validate_cache(payload)


@pytest.mark.asyncio
async def test_sanitized_online_cache_can_be_exported_and_reused(offline_client):
    client, sessions, payload, scheduled = offline_client
    folder = settings.translations_path / PAPER_ID
    folder.mkdir(parents=True)
    existing = folder / ".source.safe.translation-cache.config-fixture.jsonl"
    existing.write_bytes(payload)
    response = await client.get(f"/api/papers/{PAPER_ID}/translation-cache")
    assert response.status_code == 200
    assert response.content == payload
    response = await client.post(f"/api/papers/{PAPER_ID}/translate?cache_only=true")
    assert response.status_code == 200, response.text
    async with sessions() as db:
        job = await db.get(TranslationJob, response.json()["job_id"])
        assert job.backend == "cache-only" and job.status == "queued"
        snapshot = folder / f".cache-only-{job.id}.jsonl"
        assert validate_cache(snapshot.read_bytes()) == validate_cache(payload)
    assert len(scheduled) == 1
