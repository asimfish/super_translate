"""Document identity across the web chunk-upload and desktop multipart paths."""

import asyncio
import hashlib
import subprocess
from pathlib import Path

import fitz
import httpx
import pytest
import pytest_asyncio
from fastapi import FastAPI, Header
from sqlalchemy import event, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

pytest.importorskip(
    "app.models.pdf_upload_session",
    reason="Run this web upload contract against the mirrored production package.",
)

from app.api import papers as papers_api
from app.core.access import get_request_access_scope
from app.core.config import settings
from app.core.database import Base, apply_sqlite_pragmas, get_session
from app.models.document import PaperDocument, PaperImport
from app.models.paper import Paper
from app.models.pdf_upload_session import PdfUploadSession
from app.services.document_library import new_document
from app.services.document_metadata import pdf_fingerprints
from app.services.library import extract_title_from_pdf


def identity_pdf(body="One", title="Molecular Systems", *, metadata=True):
    with fitz.open() as doc:
        page = doc.new_page()
        if title:
            page.insert_text((72, 110), title, fontsize=22)
        if body:
            page.insert_text((72, 170), body, fontsize=11)
        if metadata:
            doc.set_metadata({"title": title})
        return doc.tobytes()


@pytest_asyncio.fixture
async def identity_client(tmp_path, monkeypatch):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'identity.db'}")
    event.listen(engine.sync_engine, "connect", lambda conn, _: apply_sqlite_pragmas(conn))
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    monkeypatch.setattr(settings, "base_dir", tmp_path)
    monkeypatch.setattr(settings, "resumable_upload_chunk_size", 512)
    settings.papers_path.mkdir(parents=True)
    settings.translations_path.mkdir(parents=True)
    api = FastAPI()
    api.include_router(papers_api.router)

    async def session():
        async with sessions() as db:
            yield db

    async def scope(x_workspace: str = Header(default="owner-a")):
        return x_workspace

    api.dependency_overrides[get_session] = session
    api.dependency_overrides[get_request_access_scope] = scope
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=api), base_url="http://test"
    ) as client:
        yield client, sessions
    await engine.dispose()


async def multipart(client, data, filename="desktop.pdf", *, owner="owner-a"):
    response = await client.post(
        "/api/papers/upload",
        headers={"X-Workspace": owner},
        files={"file": (filename, data, "application/pdf")},
    )
    assert response.status_code == 200, response.text
    return response.json()


async def stage_chunks(client, data, filename="web.pdf", *, owner="owner-a"):
    response = await client.post(
        "/api/papers/uploads/init",
        headers={"X-Workspace": owner},
        json={"filename": filename, "file_size": len(data)},
    )
    assert response.status_code == 200, response.text
    session = response.json()
    size = session["chunk_size"]
    for index, start in enumerate(range(0, len(data), size)):
        chunk = data[start : start + size]
        response = await client.put(
            f"/api/papers/uploads/{session['upload_id']}/chunks/{index}",
            headers={"X-Workspace": owner, "X-Chunk-SHA256": hashlib.sha256(chunk).hexdigest()},
            content=chunk,
        )
        assert response.status_code == 200, response.text
    return session["upload_id"]


async def complete(client, upload_id, *, owner="owner-a"):
    response = await client.post(
        f"/api/papers/uploads/{upload_id}/complete",
        headers={"X-Workspace": owner},
    )
    assert response.status_code == 200, response.text
    return response.json()


@pytest.mark.asyncio
async def test_web_reuses_desktop_result_and_idempotent_completion(identity_client):
    client, sessions = identity_client
    data = identity_pdf()
    first = await multipart(client, data)
    async with sessions() as db:
        paper = await db.get(Paper, first["id"])
        paper.translation_status = "completed"
        paper.translated_filename = "existing-chinese.pdf"
        (settings.translations_path / paper.translated_filename).write_bytes(identity_pdf("译文"))
        await db.commit()
    upload_id = await stage_chunks(client, data, "download-again.pdf")
    again = await complete(client, upload_id)
    assert again["id"] == first["id"]
    assert again["duplicate"] and again["has_translated"]
    assert again["import_count"] == 2
    repeated = await complete(client, upload_id)
    assert repeated["id"] == first["id"] and repeated["has_translated"]
    assert repeated["duplicate"]
    assert repeated["import_count"] == 2
    assert (await client.get("/api/papers/", params={"search": "download-again.pdf"})).json()[
        "total"
    ] == 1
    async with sessions() as db:
        assert len((await db.scalars(select(Paper))).all()) == 1
        assert (await db.get(PdfUploadSession, upload_id)).paper_id == first["id"]
    assert len(list(settings.papers_path.glob("*.pdf"))) == 1


@pytest.mark.asyncio
async def test_repeating_new_web_completion_reuses_receipt_without_import(identity_client):
    client, sessions = identity_client
    upload_id = await stage_chunks(client, identity_pdf(), "brand-new.pdf")
    first = await complete(client, upload_id)
    assert first["duplicate"] is False
    assert first["import_count"] == 1
    repeated = await complete(client, upload_id)
    assert repeated["id"] == first["id"]
    assert repeated["duplicate"] is True
    assert repeated["import_count"] == 1
    assert repeated["translation_status"] == first["translation_status"]
    async with sessions() as db:
        assert len((await db.scalars(select(Paper))).all()) == 1
        assert len((await db.scalars(select(PaperImport))).all()) == 1
    assert len(list(settings.papers_path.glob("*.pdf"))) == 1


@pytest.mark.asyncio
async def test_desktop_reuses_web_and_metadata_only_changes(identity_client, tmp_path):
    client, sessions = identity_client
    data = identity_pdf()
    source = tmp_path / "source.pdf"
    source.write_bytes(data)
    with fitz.open(source) as document:
        document.set_metadata({"title": "Container metadata changed"})
        equivalent = document.tobytes(garbage=4, deflate=True)
    first = await complete(client, await stage_chunks(client, data))
    desktop = await multipart(client, equivalent)
    second = await complete(client, await stage_chunks(client, equivalent, "compressed.pdf"))
    assert first["id"] == desktop["id"] == second["id"]
    assert second["duplicate"] and second["import_count"] == 3
    async with sessions() as db:
        assert len((await db.scalars(select(PaperDocument))).all()) == 1


@pytest.mark.asyncio
async def test_parallel_web_and_desktop_share_one_canonical(identity_client):
    client, sessions = identity_client
    data = identity_pdf()
    one, two = await asyncio.gather(
        stage_chunks(client, data, "one.pdf"), stage_chunks(client, data, "two.pdf")
    )
    results = await asyncio.gather(
        complete(client, one), complete(client, two), multipart(client, data)
    )
    assert len({result["id"] for result in results}) == 1
    async with sessions() as db:
        assert len((await db.scalars(select(Paper))).all()) == 1
        assert len((await db.scalars(select(PaperImport))).all()) == 3
    assert not list(settings.papers_path.glob("*.part"))


@pytest.mark.asyncio
async def test_web_title_uses_original_filename_and_distinct_content_versions(identity_client):
    client, _ = identity_client
    blank = await complete(
        client,
        await stage_chunks(client, identity_pdf("", "", metadata=False), "chemical_notes.pdf"),
    )
    assert blank["title"] == "Chemical Notes"
    one = await complete(client, await stage_chunks(client, identity_pdf("One"), "same.pdf"))
    two = await complete(client, await stage_chunks(client, identity_pdf("Two"), "same.pdf"))
    assert one["id"] != two["id"]
    listed = (await client.get("/api/papers/", params={"search": "Molecular Systems"})).json()
    assert listed["total"] == 2
    assert [paper["version_number"] for paper in listed["papers"]] == [2, 1]
    assert all(paper["version_count"] == 2 for paper in listed["papers"])


@pytest.mark.asyncio
async def test_upload_scope_isolation_and_no_foreign_completion(identity_client):
    client, _ = identity_client
    data = identity_pdf()
    upload_id = await stage_chunks(client, data)
    response = await client.post(
        f"/api/papers/uploads/{upload_id}/complete", headers={"X-Workspace": "owner-b"}
    )
    assert response.status_code == 404
    a = await complete(client, upload_id)
    b = await complete(client, await stage_chunks(client, data, owner="owner-b"), owner="owner-b")
    assert a["id"] != b["id"]
    assert (
        await client.get(f"/api/papers/{a['id']}", headers={"X-Workspace": "owner-b"})
    ).status_code == 404


@pytest.mark.asyncio
async def test_atomic_import_failure_preserves_chunks_for_retry(identity_client, monkeypatch):
    client, sessions = identity_client
    data = identity_pdf()
    first = await multipart(client, data)
    upload_id = await stage_chunks(client, data, "retry.pdf")
    original = papers_api.record_import

    async def unavailable(*args, **kwargs):
        raise RuntimeError("controlled import transaction failure")

    monkeypatch.setattr(papers_api, "record_import", unavailable)
    response = await client.post(f"/api/papers/uploads/{upload_id}/complete")
    assert response.status_code == 500
    async with sessions() as db:
        assert (await db.get(PdfUploadSession, upload_id)).paper_id is None
        assert len((await db.scalars(select(Paper))).all()) == 1
        assert len((await db.scalars(select(PaperImport))).all()) == 1
    assert (await client.get(f"/api/papers/uploads/{upload_id}")).json()["uploaded_chunks"]
    monkeypatch.setattr(papers_api, "record_import", original)
    result = await complete(client, upload_id)
    assert result["id"] == first["id"] and result["import_count"] == 2
    assert not list(settings.papers_path.glob("*.part"))


@pytest.mark.asyncio
async def test_cancelled_registration_keeps_source_and_retryable_chunks(
    identity_client, monkeypatch
):
    client, sessions = identity_client
    data = identity_pdf()
    first = await multipart(client, data)
    upload_id = await stage_chunks(client, data, "cancelled.pdf")
    original = papers_api.record_import

    async def cancelled(*args, **kwargs):
        raise asyncio.CancelledError()

    monkeypatch.setattr(papers_api, "record_import", cancelled)
    with pytest.raises(asyncio.CancelledError):
        await client.post(f"/api/papers/uploads/{upload_id}/complete")
    async with sessions() as db:
        assert (await db.get(PdfUploadSession, upload_id)).paper_id is None
        assert len((await db.scalars(select(Paper))).all()) == 1
    assert len(list(settings.papers_path.glob("*.pdf"))) == 1
    assert not list(settings.papers_path.glob("*.part"))
    monkeypatch.setattr(papers_api, "record_import", original)
    result = await complete(client, upload_id)
    assert result["id"] == first["id"] and result["import_count"] == 2


@pytest.mark.asyncio
async def test_resumable_restores_missing_original_and_reuses_existing_result(identity_client):
    client, sessions = identity_client
    data = identity_pdf()
    first = await multipart(client, data)
    async with sessions() as db:
        paper = await db.get(Paper, first["id"])
        (settings.papers_path / paper.stored_filename).unlink()
        paper.translation_status = "completed"
        paper.translated_filename = "kept-result.pdf"
        (settings.translations_path / paper.translated_filename).write_bytes(
            identity_pdf("Existing result")
        )
        await db.commit()
    result = await complete(client, await stage_chunks(client, data, "restored.pdf"))
    assert result["id"] == first["id"] and result["has_original"] and result["has_translated"]
    assert (await client.get(f"/api/papers/{first['id']}/download/original")).content == data
    assert len(list(settings.papers_path.glob("*.pdf"))) == 1


@pytest.mark.asyncio
async def test_legacy_unindexed_resumable_session_is_indexed_in_place(identity_client):
    client, sessions = identity_client
    data = identity_pdf()
    upload_id = "2" * 32
    old_id = "2" * 12
    source = settings.papers_path / "old-web-source.pdf"
    source.write_bytes(data)
    async with sessions() as db:
        db.add(
            Paper(
                id=old_id,
                access_scope="owner-a",
                title="Molecular Systems",
                original_filename="old.pdf",
                stored_filename=source.name,
            )
        )
        await db.flush()
        db.add(
            PdfUploadSession(
                id=upload_id,
                access_scope="owner-a",
                original_filename="old.pdf",
                file_size=len(data),
                chunk_size=len(data),
                content_sha256=hashlib.sha256(data).hexdigest(),
                paper_id=old_id,
            )
        )
        await db.commit()
    result = await complete(client, await stage_chunks(client, data))
    assert result["id"] == old_id and result["duplicate"]
    desktop = await multipart(client, data)
    assert desktop["id"] == old_id
    async with sessions() as db:
        assert len((await db.scalars(select(Paper))).all()) == 1
        assert (await db.get(PaperDocument, old_id)).canonical_paper_id is None


@pytest.mark.asyncio
async def test_corrupt_chunk_and_missing_chunk_do_not_register_paper(identity_client):
    client, sessions = identity_client
    data = identity_pdf()
    init = await client.post(
        "/api/papers/uploads/init", json={"filename": "missing.pdf", "file_size": len(data)}
    )
    upload_id = init.json()["upload_id"]
    bad = await client.put(
        f"/api/papers/uploads/{upload_id}/chunks/0",
        headers={"X-Chunk-SHA256": "0" * 64},
        content=data[:512],
    )
    assert bad.status_code == 400
    missing = await client.post(f"/api/papers/uploads/{upload_id}/complete")
    assert missing.status_code == 400
    async with sessions() as db:
        assert not (await db.scalars(select(Paper))).all()
        assert (await db.get(PdfUploadSession, upload_id)).paper_id is None
    assert not list(settings.papers_path.glob("*.part"))


@pytest.mark.asyncio
async def test_previous_upload_session_returns_canonical_after_organization(identity_client):
    client, sessions = identity_client
    data = identity_pdf()
    canonical = await multipart(client, data)
    upload_id = "1" * 32
    old_id = "1" * 12
    stored = settings.papers_path / "legacy-copy.pdf"
    stored.write_bytes(data)
    async with sessions() as db:
        paper = Paper(
            id=old_id,
            access_scope="owner-a",
            title="Molecular Systems",
            original_filename="old.pdf",
            stored_filename=stored.name,
        )
        db.add(paper)
        await db.flush()
        db.add(new_document(paper, pdf_fingerprints(stored), canonical=canonical["id"]))
        db.add(
            PdfUploadSession(
                id=upload_id,
                access_scope="owner-a",
                original_filename="old.pdf",
                file_size=len(data),
                chunk_size=len(data),
                paper_id=old_id,
            )
        )
        await db.commit()
    result = await complete(client, upload_id)
    assert result["id"] == canonical["id"]
    old = (await client.get(f"/api/papers/{old_id}")).json()
    assert old["id"] == old_id and old["canonical_paper_id"] == canonical["id"]
    assert (await client.get(f"/api/papers/{old_id}/download/original")).content == data


def test_arxiv_sidebar_and_doi_do_not_outrank_paper_title(tmp_path):
    path = tmp_path / "safedreamer.pdf"
    with fitz.open() as doc:
        page = doc.new_page()
        page.insert_text((35, 650), "arXiv:2307.07176v3 [cs.LG] 7 Aug 2024", fontsize=24, rotate=90)
        page.insert_text((72, 100), "SafeDreamer: Safe Reinforcement Learning", fontsize=18)
        page.insert_text((72, 130), "with World Models", fontsize=18)
        doc.save(path)
    assert (
        extract_title_from_pdf(path) == "SafeDreamer: Safe Reinforcement Learning with World Models"
    )


@pytest.mark.asyncio
async def test_organize_corrects_indexed_rejected_automatic_title_but_keeps_manual(identity_client):
    client, sessions = identity_client
    first = await multipart(client, identity_pdf("One", "SafeDreamer: Safe Reinforcement Learning"))
    manual = await multipart(
        client, identity_pdf("Two", "SafeDreamer: Safe Reinforcement Learning")
    )
    bad = "arXiv:2307.07176v3 [cs.LG] 7 Aug 2024"
    async with sessions() as db:
        paper = await db.get(Paper, first["id"])
        record = await db.get(PaperDocument, paper.id)
        paper.title = bad
        from app.services.document_metadata import title_key

        record.title_key = title_key(bad)
        other = await db.get(Paper, manual["id"])
        other.title = "My chosen name"
        await db.commit()
    response = await client.post("/api/papers/organize")
    assert response.status_code == 200, response.text
    assert response.json()["renamed"] == 1 and response.json()["indexed"] == 0
    fixed = (await client.get(f"/api/papers/{first['id']}")).json()
    assert fixed["title"] == "SafeDreamer: Safe Reinforcement Learning"
    assert fixed["import_count"] == 1
    assert (await client.get(f"/api/papers/{manual['id']}")).json()["title"] == "My chosen name"
    assert (await client.post("/api/papers/organize")).json()["renamed"] == 0


def test_web_versions_are_distinguishable_without_changing_actions():
    source = Path(papers_api.__file__).resolve().parents[1] / "static/js/app.js"
    script = """
      const fs = require('fs');
      const vm = require('vm');
      const source = fs.readFileSync(process.argv[1], 'utf8');
      const start = source.indexOf('function renderPaperList()');
      const end = source.indexOf("document.addEventListener('keydown'", start);
      const render = source.slice(start, end);
      const container = {innerHTML: ''};
      const classList = {add() {}, remove() {}};
      const sameTitle = '<img src=x onerror=alert(1)> Study';
      const context = {
        papers: [1, 2].map(version => ({
          id: 'paper' + version, title: sameTitle, page_count: 1, file_size: 100,
          version_number: version, version_count: 2, created_at: '2026-10-11',
          translation_status: 'completed', has_translated: true,
        })),
        document: {getElementById(id) {
          return id === 'paper-list' ? container : id === 'empty-state'
            ? {classList} : {value: 'recent'};
        }},
        esc(value) {
          return String(value).replaceAll('&', '&amp;').replaceAll('<', '&lt;')
            .replaceAll('>', '&gt;').replaceAll('"', '&quot;').replaceAll("'", '&#39;');
        },
        libraryDay: value => value,
        formatDate: value => value,
        formatSize: value => value,
        sanitizeClass: value => value,
        statusLabel: value => value,
      };
      vm.runInNewContext(render + '\\nrenderPaperList();', context);
      process.stdout.write(container.innerHTML);
    """
    result = subprocess.run(
        ["node", "-e", script, str(source)], check=True, text=True, capture_output=True
    )
    html = result.stdout
    assert "内容版本 1 · &lt;img" in html and "内容版本 2 · &lt;img" in html
    assert "不同内容 · 1/2 · 首次导入" in html
    assert "<img src=x" not in html
    assert html.count('data-action="download-translated-by-id"') == 2
    assert html.count('data-action="confirm-delete"') == 2
    assert 'data-paper-id="paper1"' in html and 'data-paper-id="paper2"' in html
