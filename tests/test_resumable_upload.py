"""Regression coverage for proxy-safe, resumable PDF uploads."""

from __future__ import annotations

import asyncio
import hashlib
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.core.access import get_request_access_scope
from app.core.config import settings
from app.core.database import Base, get_session
from app.main import app
from app.models.paper import Paper


@pytest.fixture
def resumable_client(tmp_path):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'uploads.db'}")
    sessions = async_sessionmaker(engine, expire_on_commit=False)

    async def prepare_database() -> None:
        async with engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)

    asyncio.run(prepare_database())

    async def override_session():
        async with sessions() as session:
            yield session

    scope = {"value": "alice"}
    app.dependency_overrides[get_session] = override_session
    app.dependency_overrides[get_request_access_scope] = lambda: scope["value"]
    with (
        patch.object(settings, "base_dir", tmp_path),
        patch.object(settings, "papers_dir", Path("papers")),
        patch.object(settings, "translations_dir", Path("translations")),
        patch.object(settings, "max_upload_size", 10 * 1024 * 1024),
        patch("app.api.papers.get_pdf_info", return_value=(1, 0)) as pdf_info,
        patch("app.api.papers.extract_title_from_pdf", return_value="Chunked Paper"),
        patch("app.api.papers.safe_pdf_for_use", side_effect=lambda path: path.resolve()),
        patch("app.main.init_db", new=AsyncMock()),
        patch("app.main.refresh_token_scopes", new=AsyncMock(return_value={})),
        patch("app.main._recover_stuck_translations", new=AsyncMock(return_value=[])),
        patch(
            "app.main._recover_repair_pending_translations",
            new=AsyncMock(return_value=[]),
        ),
        TestClient(app) as client,
    ):
        pdf_info.side_effect = lambda path: (1, path.stat().st_size)
        yield client, sessions, scope, tmp_path

    app.dependency_overrides.clear()
    asyncio.run(engine.dispose())


def _new_upload(client: TestClient, content: bytes) -> dict:
    response = client.post(
        "/api/papers/uploads/init",
        json={
            "filename": "large-paper.pdf",
            "file_size": len(content),
            "tags": "large,proxy",
        },
    )
    assert response.status_code == 200, response.text
    return response.json()


def _put_chunk(client: TestClient, upload_id: str, index: int, chunk: bytes):
    return client.put(
        f"/api/papers/uploads/{upload_id}/chunks/{index}",
        content=chunk,
        headers={
            "Content-Type": "application/octet-stream",
            "X-Chunk-SHA256": hashlib.sha256(chunk).hexdigest(),
        },
    )


def test_chunked_upload_is_tenant_bound_integrity_checked_and_idempotent(resumable_client):
    client, sessions, scope, tmp_path = resumable_client
    content = b"%PDF-1.7\n" + b"x" * (8 * 1024 * 1024) + b"\n%%EOF"
    upload = _new_upload(client, content)
    upload_id = upload["upload_id"]
    assert upload["chunk_size"] == 4 * 1024 * 1024
    assert upload["chunk_count"] == 3

    chunk_size = upload["chunk_size"]
    chunks = [
        content[offset : offset + chunk_size]
        for offset in range(0, len(content), chunk_size)
    ]
    bad_hash = client.put(
        f"/api/papers/uploads/{upload_id}/chunks/0",
        content=chunks[0],
        headers={"X-Chunk-SHA256": "0" * 64},
    )
    assert bad_hash.status_code == 400
    assert "SHA256" in bad_hash.json()["detail"]

    scope["value"] = "bob"
    denied = _put_chunk(client, upload_id, 0, chunks[0])
    assert denied.status_code == 404
    assert client.get(f"/api/papers/uploads/{upload_id}").status_code == 404

    scope["value"] = "alice"
    for index in (2, 0, 1):
        response = _put_chunk(client, upload_id, index, chunks[index])
        assert response.status_code == 200, response.text

    status = client.get(f"/api/papers/uploads/{upload_id}")
    assert status.status_code == 200
    assert status.json()["uploaded_chunks"] == [0, 1, 2]

    completed = client.post(f"/api/papers/uploads/{upload_id}/complete")
    assert completed.status_code == 200, completed.text
    paper_id = completed.json()["id"]
    repeated = client.post(f"/api/papers/uploads/{upload_id}/complete")
    assert repeated.status_code == 200
    assert repeated.json()["id"] == paper_id

    async def paper_state() -> tuple[int, Paper]:
        async with sessions() as session:
            count = await session.scalar(select(func.count()).select_from(Paper))
            paper = await session.scalar(select(Paper).where(Paper.id == paper_id))
            assert paper is not None
            return int(count or 0), paper

    count, paper = asyncio.run(paper_state())
    assert count == 1
    assert (tmp_path / "papers" / paper.stored_filename).read_bytes() == content

    duplicate = _new_upload(client, content)
    for index, chunk in enumerate(chunks):
        assert _put_chunk(client, duplicate["upload_id"], index, chunk).status_code == 200
    deduplicated = client.post(
        f"/api/papers/uploads/{duplicate['upload_id']}/complete"
    )
    assert deduplicated.status_code == 200
    assert deduplicated.json()["id"] == paper_id
    count_after, _paper = asyncio.run(paper_state())
    assert count_after == 1


def test_chunked_upload_rejects_wrong_chunk_length(resumable_client):
    client, _sessions, _scope, _tmp_path = resumable_client
    content = b"%PDF-1.7\n" + b"x" * (5 * 1024 * 1024) + b"\n%%EOF"
    upload = _new_upload(client, content)
    response = _put_chunk(client, upload["upload_id"], 0, b"too short")
    assert response.status_code == 400
    assert "chunk size" in response.json()["detail"].lower()


def test_upload_initialization_is_idempotent_after_a_lost_response(resumable_client):
    client, _sessions, scope, _tmp_path = resumable_client
    upload_id = "a" * 32
    payload = {
        "filename": "retry.pdf",
        "file_size": 9 * 1024 * 1024,
        "tags": "proxy",
        "client_upload_id": upload_id,
    }
    first = client.post("/api/papers/uploads/init", json=payload)
    retried = client.post("/api/papers/uploads/init", json=payload)
    assert first.status_code == 200
    assert retried.status_code == 200
    assert first.json()["upload_id"] == upload_id
    assert retried.json() == first.json()

    scope["value"] = "bob"
    collision = client.post("/api/papers/uploads/init", json=payload)
    assert collision.status_code == 409
