"""Scoped, non-destructive document identity and import/version history."""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.models.document import PaperDocument, PaperImport
from app.models.paper import Paper, TranslationJob
from app.services.document_metadata import title_key, useful_metadata_title
from app.services.library import extract_title_from_pdf


def utc_now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def timestamp(value: datetime | None) -> str:
    return value.replace(tzinfo=timezone.utc).isoformat() if value else ""


async def find_duplicate(
    db: AsyncSession, scope: str, fingerprints: tuple[str, str]
) -> Paper | None:
    result = await db.execute(
        select(Paper)
        .join(PaperDocument, PaperDocument.paper_id == Paper.id)
        .where(
            Paper.access_scope == scope,
            PaperDocument.access_scope == scope,
            PaperDocument.canonical_paper_id.is_(None),
            or_(
                PaperDocument.file_sha256 == fingerprints[0],
                PaperDocument.content_sha256 == fingerprints[1],
            ),
        )
        .order_by(Paper.created_at, Paper.id)
        .limit(1)
    )
    return result.scalar_one_or_none()


async def record_import(
    db: AsyncSession,
    paper: Paper,
    filename: str,
    *,
    at: datetime | None = None,
    source_title: str = "",
) -> None:
    at = at or utc_now()
    record = await db.get(PaperDocument, paper.id)
    if record is not None:
        record.first_imported_at = min(record.first_imported_at, at)
        record.last_imported_at = max(record.last_imported_at, at)
    db.add(
        PaperImport(
            paper_id=paper.id,
            access_scope=paper.access_scope,
            filename=Path(filename.replace("\\", "/")).name[:500],
            source_title=source_title[:500],
            imported_at=at,
        )
    )


def new_document(
    paper: Paper,
    fingerprints: tuple[str, str],
    *,
    at: datetime | None = None,
    canonical: str | None = None,
) -> PaperDocument:
    at = at or paper.created_at or utc_now()
    return PaperDocument(
        paper_id=paper.id,
        access_scope=paper.access_scope,
        file_sha256=fingerprints[0],
        content_sha256=fingerprints[1],
        title_key=title_key(paper.title),
        canonical_paper_id=canonical,
        first_imported_at=at,
        last_imported_at=at,
    )


async def attach_document_info(db: AsyncSession, papers: list[Paper]) -> None:
    if not papers:
        return
    ids = [paper.id for paper in papers]
    rows = (
        (await db.execute(select(PaperDocument).where(PaperDocument.paper_id.in_(ids))))
        .scalars()
        .all()
    )
    records = {row.paper_id: row for row in rows}
    scopes = {paper.access_scope for paper in papers}
    keys = {row.title_key for row in rows}
    versions = (
        (
            await db.execute(
                select(PaperDocument)
                .where(
                    PaperDocument.access_scope.in_(scopes),
                    PaperDocument.title_key.in_(keys),
                    PaperDocument.canonical_paper_id.is_(None),
                )
                .order_by(PaperDocument.first_imported_at, PaperDocument.paper_id)
            )
        )
        .scalars()
        .all()
        if keys
        else []
    )
    groups = {}
    for row in versions:
        groups.setdefault((row.access_scope, row.title_key), []).append(row.paper_id)
    counts = dict(
        (
            await db.execute(
                select(PaperImport.paper_id, func.count())
                .where(PaperImport.paper_id.in_(ids))
                .group_by(PaperImport.paper_id)
            )
        ).all()
    )
    for paper in papers:
        record = records.get(paper.id)
        if not record:
            continue
        group = groups.get((record.access_scope, record.title_key), [paper.id])
        paper._document_info = {
            "last_imported_at": timestamp(record.last_imported_at),
            "import_count": counts.get(paper.id, 1),
            "version_count": len(group),
            "version_number": group.index(paper.id) + 1 if paper.id in group else 1,
            "canonical_paper_id": record.canonical_paper_id,
        }


async def organize_existing(db: AsyncSession, scope: str) -> dict:
    """Index old rows without unlinking files, translations, jobs or direct URLs."""
    indexed = duplicates = renamed = 0
    completions = (
        select(TranslationJob.paper_id, func.max(TranslationJob.finished_at).label("completed_at"))
        .where(TranslationJob.access_scope == scope, TranslationJob.status == "completed")
        .group_by(TranslationJob.paper_id)
        .subquery()
    )
    rows = (
        (
            await db.execute(
                select(Paper)
                .outerjoin(PaperDocument, PaperDocument.paper_id == Paper.id)
                .outerjoin(completions, completions.c.paper_id == Paper.id)
                .where(Paper.access_scope == scope)
                .order_by(
                    (Paper.translation_status == "completed").desc(),
                    (Paper.translation_status == "translating").desc(),
                    func.coalesce(
                        completions.c.completed_at, Paper.updated_at, Paper.created_at
                    ).desc(),
                    Paper.created_at,
                    Paper.id,
                )
            )
        )
        .scalars()
        .all()
    )
    from app.services.document_metadata import pdf_fingerprints

    for paper in rows:
        existing = await db.get(PaperDocument, paper.id)
        if existing is not None:
            # A storage UUID can differ from the indexed source title after a
            # legacy import. That exact generated filename is not a user title.
            stored_stem = Path(paper.stored_filename).stem.casefold()
            storage_uuid_title = (
                len(stored_stem) == 32
                and all(character in "0123456789abcdef" for character in stored_stem)
                and paper.title.casefold() == stored_stem
            )
            # Otherwise a title different from the source key is user-edited.
            # Already indexed automatic arXiv/UUID/template labels still need
            # correction after improvements to source-title recognition.
            if useful_metadata_title(paper.title) or (
                existing.title_key != title_key(paper.title) and not storage_uuid_title
            ):
                continue
            path = (settings.papers_path / paper.stored_filename).resolve()
            if not path.is_relative_to(settings.papers_path.resolve()) or not path.is_file():
                continue
            suggested = await asyncio.to_thread(
                extract_title_from_pdf, path, paper.original_filename
            )
            if useful_metadata_title(suggested) and suggested != paper.title:
                paper.title = suggested
                existing.title_key = title_key(suggested)
                renamed += 1
            continue
        path = (settings.papers_path / paper.stored_filename).resolve()
        if not path.is_relative_to(settings.papers_path.resolve()) or not path.is_file():
            continue
        fingerprints, suggested = await asyncio.gather(
            asyncio.to_thread(pdf_fingerprints, path),
            asyncio.to_thread(extract_title_from_pdf, path, paper.original_filename),
        )
        filename_title = Path(paper.original_filename).stem.replace("_", " ").replace("-", " ")
        old = paper.title
        if (
            not useful_metadata_title(old)
            or title_key(old) == title_key(filename_title)
            or (suggested.casefold().startswith(old.casefold()) and len(suggested) > len(old) + 10)
        ):
            paper.title = suggested
            renamed += int(old != suggested)
        canonical = await find_duplicate(db, scope, fingerprints)
        document = new_document(paper, fingerprints, canonical=canonical.id if canonical else None)
        document.title_key = title_key(suggested)
        db.add(document)
        await db.flush()
        target = canonical or paper
        await record_import(
            db, target, paper.original_filename, at=paper.created_at, source_title=old
        )
        indexed += 1
        duplicates += int(canonical is not None)
    await db.commit()
    return {"indexed": indexed, "duplicates": duplicates, "renamed": renamed}


async def library_history(db: AsyncSession, paper: Paper) -> dict:
    doc = await db.get(PaperDocument, paper.id)
    canonical_id = doc.canonical_paper_id if doc and doc.canonical_paper_id else paper.id
    imports = (
        (
            await db.execute(
                select(PaperImport)
                .where(
                    PaperImport.paper_id == canonical_id,
                    PaperImport.access_scope == paper.access_scope,
                )
                .order_by(PaperImport.imported_at.desc(), PaperImport.id.desc())
                .limit(200)
            )
        )
        .scalars()
        .all()
    )
    versions = (
        (
            await db.execute(
                select(Paper)
                .join(PaperDocument, PaperDocument.paper_id == Paper.id)
                .where(
                    Paper.access_scope == paper.access_scope,
                    PaperDocument.access_scope == paper.access_scope,
                    PaperDocument.title_key == (doc.title_key if doc else title_key(paper.title)),
                    PaperDocument.canonical_paper_id.is_(None),
                )
                .order_by(PaperDocument.first_imported_at.desc(), Paper.id.desc())
            )
        )
        .scalars()
        .all()
    )
    return {
        "paper_id": paper.id,
        "canonical_paper_id": canonical_id,
        "imports": [
            {
                "filename": row.filename,
                "title": row.source_title,
                "imported_at": timestamp(row.imported_at),
            }
            for row in imports
        ],
        "versions": [
            {
                "id": row.id,
                "title": row.title,
                "created_at": timestamp(row.created_at),
                "translation_status": row.translation_status,
            }
            for row in versions
        ],
    }
