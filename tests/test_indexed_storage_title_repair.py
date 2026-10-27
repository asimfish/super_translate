"""Indexed source-title keys must not protect automatic storage UUID labels."""

from pathlib import Path

import pytest
from sqlalchemy import select
from test_document_organization import library_client as library_client
from test_document_organization import pdf_bytes, upload

from app.core.config import settings
from app.models.document import PaperDocument, PaperImport
from app.models.paper import Paper
from app.services.document_metadata import title_key


@pytest.mark.asyncio
async def test_indexed_storage_uuid_repaired_without_reimport_or_file_changes(library_client):
    client, sessions = library_client
    title = "Optimal Flow Transport and Its Entropic Regularization"
    original = pdf_bytes(title=title)
    receipt = await upload(client, original, "OTF-first-page.pdf")
    async with sessions() as db:
        paper = await db.get(Paper, receipt["id"])
        record = await db.get(PaperDocument, paper.id)
        assert record.title_key == title_key(title)
        paper.title = Path(paper.stored_filename).stem.upper()
        paper.translation_status = "completed"
        paper.translated_filename = "kept-mono.pdf"
        kept = settings.translations_path / paper.translated_filename
        kept.write_bytes(b"existing translation must remain untouched")
        stored = settings.papers_path / paper.stored_filename
        await db.commit()
    organized = await client.post("/api/papers/organize")
    assert organized.status_code == 200, organized.text
    assert organized.json()["renamed"] == 1
    assert organized.json()["indexed"] == 0
    fixed = (await client.get(f"/api/papers/{receipt['id']}")).json()
    assert fixed["id"] == receipt["id"] and fixed["title"] == title
    assert fixed["translation_status"] == "completed" and fixed["has_translated"]
    assert fixed["import_count"] == 1
    assert stored.read_bytes() == original
    assert kept.read_bytes() == b"existing translation must remain untouched"
    async with sessions() as db:
        assert len((await db.scalars(select(PaperImport))).all()) == 1
    assert (await client.post("/api/papers/organize")).json()["renamed"] == 0


@pytest.mark.parametrize("chosen", ["My chosen OTF title", "ABCDEF0123456789ABCDEF0123456789"])
@pytest.mark.asyncio
async def test_indexed_user_title_is_preserved_when_not_the_storage_uuid(library_client, chosen):
    client, sessions = library_client
    receipt = await upload(client, pdf_bytes(), "paper.pdf")
    async with sessions() as db:
        paper = await db.get(Paper, receipt["id"])
        assert Path(paper.stored_filename).stem.casefold() != chosen.casefold()
        paper.title = chosen
        await db.commit()
    response = await client.post("/api/papers/organize")
    assert response.status_code == 200 and response.json()["renamed"] == 0
    assert (await client.get(f"/api/papers/{receipt['id']}")).json()["title"] == chosen


@pytest.mark.asyncio
async def test_storage_uuid_repair_stays_in_requesting_owner_scope(library_client):
    client, sessions = library_client
    receipt = await upload(client, pdf_bytes(), "paper.pdf", scope="user-b")
    async with sessions() as db:
        paper = await db.get(Paper, receipt["id"])
        paper.title = Path(paper.stored_filename).stem.upper()
        old = paper.title
        await db.commit()
    response = await client.post("/api/papers/organize", headers={"X-Workspace": "user-a"})
    assert response.status_code == 200 and response.json()["renamed"] == 0
    async with sessions() as db:
        assert (await db.get(Paper, receipt["id"])).title == old
