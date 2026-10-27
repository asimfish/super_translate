"""Use real PDFs, file storage and SQLite to verify library organization."""

import asyncio
from datetime import datetime

import fitz
import httpx
import pytest
import pytest_asyncio
from fastapi import FastAPI, Header
from sqlalchemy import event, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.api.papers import router
from app.core.access import get_request_access_scope
from app.core.config import settings
from app.core.database import Base, apply_sqlite_pragmas, get_session
from app.models.document import PaperDocument, PaperImport
from app.models.paper import Paper, TranslationJob
from app.services.document_metadata import pdf_fingerprints
from app.services.library import extract_title_from_pdf


def pdf_bytes(body="Version one", title="A Study of Molecular Systems", figure=False):
    with fitz.open() as doc:
        page = doc.new_page()
        page.insert_text((72, 100), title, fontsize=22)
        page.insert_text((72, 165), body, fontsize=11)
        if figure:
            page.draw_rect(fitz.Rect(72, 220, 90, 240), fill=(1, 0, 0))
        doc.set_metadata({"title": title})
        return doc.tobytes()


@pytest_asyncio.fixture
async def library_client(tmp_path, monkeypatch):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'library.db'}")
    event.listen(engine.sync_engine, "connect", lambda conn, _: apply_sqlite_pragmas(conn))
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    monkeypatch.setattr(settings, "base_dir", tmp_path)
    settings.papers_path.mkdir(parents=True)
    settings.translations_path.mkdir(parents=True)
    api = FastAPI()
    api.include_router(router)

    async def session():
        async with sessions() as db:
            yield db

    async def scope(x_workspace: str = Header(default="user-a")):
        return x_workspace

    api.dependency_overrides[get_session] = session
    api.dependency_overrides[get_request_access_scope] = scope
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=api), base_url="http://test"
    ) as client:
        yield client, sessions
    await engine.dispose()


async def upload(client, data, name="download.pdf", scope="user-a"):
    response = await client.post(
        "/api/papers/upload",
        headers={"X-Workspace": scope},
        files={"file": (name, data, "application/pdf")},
    )
    assert response.status_code == 200, response.text
    return response.json()


def test_multiline_title_not_journal_or_template_metadata(tmp_path):
    path = tmp_path / "random.pdf"
    with fitz.open() as doc:
        page = doc.new_page()
        page.insert_text((72, 25), "NATURE COMPUTATIONAL SCIENCE", fontsize=28)
        page.insert_text((72, 110), "Learning chemical representations", fontsize=22)
        page.insert_text((72, 138), "from molecular structures", fontsize=22)
        doc.set_metadata({"title": "Microsoft Word - document1.docx"})
        doc.save(path)
    assert (
        extract_title_from_pdf(path)
        == "Learning chemical representations from molecular structures"
    )


@pytest.mark.asyncio
async def test_same_content_different_names_reuses_translation_and_aliases(library_client):
    client, sessions = library_client
    first = await upload(client, pdf_bytes(), "first.pdf")
    async with sessions() as db:
        paper = await db.get(Paper, first["id"])
        paper.translation_status = "completed"
        paper.translated_filename = "done.pdf"
        (settings.translations_path / "done.pdf").write_bytes(pdf_bytes("Chinese result"))
        await db.commit()
    again = await upload(client, pdf_bytes(), "copy.pdf")
    assert again["id"] == first["id"]
    assert again["duplicate"] is True and again["has_translated"] is True
    assert again["import_count"] == 2
    response = await client.get("/api/papers/", params={"search": "copy.pdf"})
    assert response.json()["total"] == 1
    assert len(list(settings.papers_path.glob("*.pdf"))) == 1
    async with sessions() as db:
        assert len((await db.scalars(select(Paper))).all()) == 1
        assert len((await db.scalars(select(PaperImport))).all()) == 2


@pytest.mark.asyncio
async def test_same_title_changed_text_or_figure_are_versions(library_client):
    client, _ = library_client
    records = [
        await upload(client, pdf_bytes(body=body, figure=figure), "same.pdf")
        for body, figure in [("One", False), ("Two", False), ("Two", True)]
    ]
    assert len({r["id"] for r in records}) == 3
    papers = (await client.get("/api/papers/")).json()["papers"]
    assert [p["id"] for p in papers] == [r["id"] for r in reversed(records)]
    assert all(p["version_count"] == 3 for p in papers)
    assert [p["version_number"] for p in papers] == [3, 2, 1]
    history = (await client.get(f"/api/papers/{records[0]['id']}/library-history")).json()
    assert len(history["versions"]) == 3


def test_metadata_compression_duplicate_but_changed_figures_do_not_merge(tmp_path):
    a, b, c = [tmp_path / n for n in ["a.pdf", "b.pdf", "c.pdf"]]
    a.write_bytes(pdf_bytes())
    with fitz.open(a) as doc:
        doc.set_metadata({"title": "Some different container metadata"})
        doc.save(b, garbage=4, deflate=True)
    c.write_bytes(pdf_bytes(figure=True))
    assert pdf_fingerprints(a)[0] != pdf_fingerprints(b)[0]
    assert pdf_fingerprints(a)[1] == pdf_fingerprints(b)[1]
    assert pdf_fingerprints(a)[1] != pdf_fingerprints(c)[1]


@pytest.mark.asyncio
async def test_parallel_duplicate_and_scope_isolation(library_client):
    client, _ = library_client
    data = pdf_bytes()
    a, b = await asyncio.gather(upload(client, data, "a.pdf"), upload(client, data, "b.pdf"))
    assert a["id"] == b["id"]
    other = await upload(client, data, scope="user-b")
    assert other["id"] != a["id"] and other["duplicate"] is False
    forbidden = await client.get(
        f"/api/papers/{a['id']}/library-history", headers={"X-Workspace": "user-b"}
    )
    assert forbidden.status_code == 404


@pytest.mark.asyncio
async def test_legacy_organize_keeps_ids_files_and_manual_names(library_client):
    client, sessions = library_client
    ids = ["000000000001", "000000000002", "000000000003"]
    async with sessions() as db:
        for i, paper_id in enumerate(ids):
            filename = f"old-{i}.pdf"
            (settings.papers_path / filename).write_bytes(
                pdf_bytes(body="same" if i < 2 else "different")
            )
            db.add(
                Paper(
                    id=paper_id,
                    access_scope="user-a",
                    title="Old Download" if i < 2 else "My chosen name",
                    original_filename="old_download.pdf",
                    stored_filename=filename,
                    created_at=datetime(2025, 1, i + 1),
                )
            )
        await db.commit()
    response = await client.post("/api/papers/organize")
    assert response.status_code == 200, response.text
    assert response.json()["indexed"] == 3 and response.json()["duplicates"] == 1
    assert (await client.get("/api/papers/")).json()["total"] == 2
    assert (await client.get(f"/api/papers/{ids[1]}")).status_code == 200
    assert len(list(settings.papers_path.glob("*.pdf"))) == 3
    async with sessions() as db:
        assert (await db.get(Paper, ids[2])).title == "My chosen name"
        assert len((await db.scalars(select(PaperDocument))).all()) == 3
    again = await client.post("/api/papers/organize")
    assert again.json()["indexed"] == 0


@pytest.mark.asyncio
async def test_legacy_duplicates_prefer_latest_success_and_keep_earliest_import(library_client):
    client, sessions = library_client
    data = pdf_bytes()
    ids = ["000000000011", "000000000012"]
    results = [pdf_bytes(body="Older translation"), pdf_bytes(body="Corrected translation")]
    async with sessions() as db:
        for i, paper_id in enumerate(ids):
            source = f"source-{i}.pdf"
            output = f"translated-{i}.pdf"
            (settings.papers_path / source).write_bytes(data)
            (settings.translations_path / output).write_bytes(results[i])
            db.add(
                Paper(
                    id=paper_id,
                    access_scope="user-a",
                    title="A Study of Molecular Systems",
                    original_filename=f"old-{i}.pdf",
                    stored_filename=source,
                    translated_filename=output,
                    translation_status="completed",
                    created_at=datetime(2025, i + 1, 1),
                    updated_at=datetime(2030 if i == 0 else 2025, 3, 1),
                )
            )
        await db.flush()
        for i, paper_id in enumerate(ids):
            db.add(
                TranslationJob(
                    id=str(i + 1).zfill(32),
                    paper_id=paper_id,
                    access_scope="user-a",
                    status="completed",
                    finished_at=datetime(2025, i + 1, 2),
                )
            )
        await db.commit()
    response = await client.post("/api/papers/organize")
    assert response.status_code == 200, response.text
    listed = (await client.get("/api/papers/")).json()
    assert listed["total"] == 1 and listed["papers"][0]["id"] == ids[1]
    assert (await client.get(f"/api/papers/{ids[1]}/download/translated")).content == results[1]
    assert (await client.get(f"/api/papers/{ids[0]}/download/translated")).content == results[0]
    async with sessions() as db:
        assert (await db.get(PaperDocument, ids[1])).first_imported_at == datetime(2025, 1, 1)
        assert (await db.get(PaperDocument, ids[0])).canonical_paper_id == ids[1]
    repeated = await upload(client, data, "same-content-new-name.pdf")
    assert repeated["id"] == ids[1] and repeated["import_count"] == 3


@pytest.mark.asyncio
async def test_filename_fallback_uses_uploaded_name_and_download_is_safe(library_client):
    client, _ = library_client
    with fitz.open() as doc:
        doc.new_page()
        data = doc.tobytes()
    record = await upload(client, data, "my_research.pdf")
    assert record["title"] == "My Research"
    await client.patch(
        f"/api/papers/{record['id']}", json={"title": "../化学: Biological / Systems"}
    )
    response = await client.get(f"/api/papers/{record['id']}/download/original")
    assert response.status_code == 200
    assert "filename*=utf-8''" in response.headers["content-disposition"]
    assert record["id"] in response.headers["content-disposition"]


@pytest.mark.asyncio
async def test_manual_abbreviation_keeps_versions_and_original_title_search(library_client):
    client, _ = library_client
    first = await upload(client, pdf_bytes(body="one"))
    await upload(client, pdf_bytes(body="two"))
    response = await client.patch(f"/api/papers/{first['id']}", json={"title": "Molecular Study"})
    assert response.json()["version_count"] == 2
    original_title = (
        await client.get("/api/papers/", params={"search": "A Study of Molecular Systems"})
    ).json()
    assert original_title["total"] == 2
    assert any(p["title"] == "Molecular Study" for p in original_title["papers"])


@pytest.mark.asyncio
async def test_duplicate_restores_missing_original_without_new_paper(library_client):
    client, sessions = library_client
    data = pdf_bytes()
    first = await upload(client, data)
    async with sessions() as db:
        paper = await db.get(Paper, first["id"])
        (settings.papers_path / paper.stored_filename).unlink()
    again = await upload(client, data, "restored.pdf")
    assert again["id"] == first["id"] and again["has_original"] is True
    assert (await client.get(f"/api/papers/{first['id']}/download/original")).status_code == 200


@pytest.mark.asyncio
async def test_deleting_canonical_does_not_destroy_legacy_duplicate(library_client):
    client, sessions = library_client
    original = await upload(client, pdf_bytes())
    duplicate_id = "000000000009"
    path = settings.papers_path / "legacy.pdf"
    path.write_bytes(pdf_bytes())
    async with sessions() as db:
        db.add(
            Paper(
                id=duplicate_id,
                access_scope="user-a",
                title="Legacy",
                original_filename="legacy.pdf",
                stored_filename=path.name,
            )
        )
        await db.commit()
    assert (await client.post("/api/papers/organize")).status_code == 200
    assert (await client.delete(f"/api/papers/{original['id']}")).status_code == 200
    assert (await client.get(f"/api/papers/{duplicate_id}")).status_code == 200
    assert path.exists()
    assert (await client.get("/api/papers/")).json()["total"] == 1
