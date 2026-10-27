"""Retained drafts are readable, never implicitly accepted, and remain scoped."""

from io import BytesIO

import fitz
import httpx
import pytest
import pytest_asyncio
from fastapi import FastAPI
from PIL import Image
from pydantic import SecretStr
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.api import papers as api
from app.core import users
from app.core.config import settings
from app.core.database import Base, get_session
from app.models.paper import Paper

PAPER_ID = "a21000000001"


@pytest_asyncio.fixture
async def draft_preview_client(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "base_dir", tmp_path)
    monkeypatch.setattr(settings, "workspace_tokens", "alice:fixture-alice,bob:fixture-bob")
    monkeypatch.setattr(settings, "api_token", SecretStr(""))

    async def no_extra_user_tokens():
        return {}

    monkeypatch.setattr(users, "refresh_token_scopes", no_extra_user_tokens)
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'preview.db'}")
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    pdf = settings.translations_path / PAPER_ID / "draft.pdf"
    pdf.parent.mkdir(parents=True)
    with fitz.open() as document:
        page = document.new_page()
        page.insert_text((72, 100), "Previously readable draft; requires QA review")
        document.save(pdf)
    async with sessions() as db:
        db.add(Paper(
            id=PAPER_ID, access_scope="alice", title="Draft preview specimen",
            original_filename="source.pdf", stored_filename="source.pdf",
            translated_filename=f"{PAPER_ID}/draft.pdf", page_count=1,
            translation_status="repairing", translation_error="QA unresolved",
        ))
        await db.commit()

    async def session_dependency():
        async with sessions() as db:
            yield db

    app = FastAPI()
    app.include_router(api.router)
    app.dependency_overrides[get_session] = session_dependency
    try:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://127.0.0.1",
            headers={"Authorization": "Bearer fixture-alice"},
        ) as client:
            yield client, sessions, pdf
    finally:
        await engine.dispose()


@pytest.mark.asyncio
@pytest.mark.parametrize("state", ["failed", "repairing", "translating", "pending", "completed"])
async def test_retained_preview_matches_view_and_download_without_accepting(
    draft_preview_client, state,
):
    client, sessions, pdf = draft_preview_client
    original = pdf.read_bytes()
    async with sessions() as db:
        paper = await db.get(Paper, PAPER_ID)
        paper.translation_status = state
        await db.commit()
    for endpoint in ("view/translated", "download/translated"):
        response = await client.get(f"/api/papers/{PAPER_ID}/{endpoint}")
        assert response.status_code == 200 and response.content == original
    preview = await client.get(f"/api/papers/{PAPER_ID}/preview/translated/1")
    assert preview.status_code == 200 and preview.headers["content-type"] == "image/webp"
    with Image.open(BytesIO(preview.content)) as image:
        assert image.format == "WEBP" and image.width > 0 and image.height > 0
    assert pdf.read_bytes() == original
    async with sessions() as db:
        paper = await db.get(Paper, PAPER_ID)
        assert paper.translation_status == state and paper.translation_error == "QA unresolved"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("filename", "expected"), [(None, 409), ("missing.pdf", 404), ("../outside.pdf", 403)],
)
async def test_preview_requires_existing_in_scope_artifact(
    draft_preview_client, filename, expected,
):
    client, sessions, pdf = draft_preview_client
    async with sessions() as db:
        paper = await db.get(Paper, PAPER_ID)
        paper.translated_filename = filename
        await db.commit()
    response = await client.get(f"/api/papers/{PAPER_ID}/preview/translated/1")
    assert response.status_code == expected
    assert not (pdf.parent / "preview").exists()


@pytest.mark.asyncio
@pytest.mark.parametrize(("token", "expected"), [("fixture-bob", 404), ("invalid", 401)])
async def test_preview_does_not_leak_other_owner_or_skip_auth(
    draft_preview_client, token, expected,
):
    client, _, pdf = draft_preview_client
    response = await client.get(
        f"/api/papers/{PAPER_ID}/preview/translated/1",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == expected
    assert not (pdf.parent / "preview").exists()


@pytest.mark.asyncio
async def test_retained_preview_still_rejects_out_of_range_page(draft_preview_client):
    client, _, pdf = draft_preview_client
    response = await client.get(f"/api/papers/{PAPER_ID}/preview/translated/2")
    assert response.status_code == 404
    assert not (pdf.parent / "preview").exists()
