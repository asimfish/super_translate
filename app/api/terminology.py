"""Authenticated terminology management and import/export endpoints."""

from __future__ import annotations

import json
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.access import get_request_access_scope
from app.core.database import get_session
from app.models.paper import Paper
from app.models.terminology import (
    TerminologyCategory,
    TerminologyEntry,
    TerminologyLibrary,
    TerminologyProfile,
)
from app.services.terminology import (
    BUILTIN_ID,
    MAX_CATEGORIES,
    MAX_ENTRIES,
    MAX_LIBRARIES,
    MAX_LIBRARY_ENTRIES,
    STARTER_TEMPLATES,
    TermInput,
    builtin_terms,
    normalized_source,
    parse_term_import,
    profile_record,
    resolve_terminology,
    scoped_record,
    single_line,
)

router = APIRouter(prefix="/api/terminology", tags=["terminology"])
Scope = Annotated[str, Depends(get_request_access_scope)]
Db = Annotated[AsyncSession, Depends(get_session)]


class CategoryInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(min_length=1, max_length=120)
    parent_id: str | None = Field(default=None, max_length=32)

    @field_validator("name")
    @classmethod
    def validate_name(cls, value: str) -> str:
        return single_line(value)


class LibraryInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(min_length=1, max_length=120)
    description: str = Field(default="", max_length=1000)
    category_id: str | None = Field(default=None, max_length=32)
    paper_id: str | None = Field(default=None, max_length=12)
    enabled: bool = True

    @field_validator("name")
    @classmethod
    def validate_name(cls, value: str) -> str:
        return single_line(value)

    @model_validator(mode="after")
    def validate_scope(self):
        if self.paper_id and self.category_id:
            raise ValueError("论文专用库与分类库不能同时设置")
        return self


class ProfileInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    category_id: str | None = Field(default=None, max_length=32)
    library_ids: list[str] = Field(default_factory=list, max_length=20)
    use_builtin: bool = True

    @field_validator("library_ids")
    @classmethod
    def validate_ids(cls, values: list[str]) -> list[str]:
        if any(len(value) > 32 for value in values):
            raise ValueError("无效术语库 ID")
        return list(dict.fromkeys(values))


class ImportInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    content: str = Field(max_length=2 * 1024 * 1024)
    format: Literal["json", "csv"] = "csv"
    conflict: Literal["keep", "overwrite"] = "keep"


async def commit(db: AsyncSession) -> None:
    try:
        await db.commit()
    except IntegrityError:
        await db.rollback()
        raise HTTPException(409, "记录已存在或正在被其他操作引用，请刷新后重试") from None


def category_data(row: TerminologyCategory) -> dict:
    return {"id": row.id, "name": row.name, "parent_id": row.parent_id}


def library_data(row: TerminologyLibrary, count: int = 0) -> dict:
    return {
        "id": row.id,
        "name": row.name,
        "description": row.description,
        "category_id": row.category_id,
        "paper_id": row.paper_id,
        "enabled": row.enabled,
        "count": count,
        "readonly": False,
    }


def entry_data(row: TerminologyEntry) -> dict:
    return {
        "id": row.id,
        "source": row.source,
        "translation": row.translation,
        "aliases": json.loads(row.aliases),
        "notes": row.notes,
        "reference": row.reference,
    }


async def category_parent(db: AsyncSession, scope: str, parent: str | None) -> None:
    seen = set()
    while parent:
        if parent in seen or len(seen) >= 7:
            raise HTTPException(422, "分类最多 8 层，且不能循环")
        seen.add(parent)
        row = await scoped_record(db, TerminologyCategory, parent, scope)
        parent = row.parent_id


async def entry_capacity(db: AsyncSession, scope: str, library_id: str, addition: int) -> None:
    count = await db.scalar(
        select(func.count())
        .select_from(TerminologyEntry)
        .where(
            TerminologyEntry.library_id == library_id,
        )
    )
    total = await db.scalar(
        select(func.count())
        .select_from(TerminologyEntry)
        .join(
            TerminologyLibrary,
            TerminologyEntry.library_id == TerminologyLibrary.id,
        )
        .where(TerminologyLibrary.access_scope == scope)
    )
    if count + addition > MAX_LIBRARY_ENTRIES or total + addition > MAX_ENTRIES:
        raise HTTPException(422, "每库最多 5000 个词条，工作区最多 20000 个")


async def validate_library_scope(db: AsyncSession, scope: str, data: LibraryInput) -> None:
    if data.category_id:
        await scoped_record(db, TerminologyCategory, data.category_id, scope)
    if data.paper_id:
        await scoped_record(db, Paper, data.paper_id, scope)


@router.get("/categories")
async def list_categories(db: Db, scope: Scope):
    rows = (
        await db.scalars(
            select(TerminologyCategory)
            .where(
                TerminologyCategory.access_scope == scope,
            )
            .order_by(TerminologyCategory.name, TerminologyCategory.id)
        )
    ).all()
    return [category_data(row) for row in rows]


@router.post("/categories", status_code=201)
async def create_category(data: CategoryInput, db: Db, scope: Scope):
    await category_parent(db, scope, data.parent_id)
    count = await db.scalar(
        select(func.count())
        .select_from(TerminologyCategory)
        .where(
            TerminologyCategory.access_scope == scope,
        )
    )
    if count >= MAX_CATEGORIES:
        raise HTTPException(422, "工作区最多 500 个分类")
    existing = await db.scalar(
        select(TerminologyCategory.id).where(
            TerminologyCategory.access_scope == scope,
            TerminologyCategory.name == data.name,
            TerminologyCategory.parent_id == data.parent_id,
        )
    )
    if existing:
        raise HTTPException(409, "该位置已有同名分类")
    row = TerminologyCategory(access_scope=scope, **data.model_dump())
    db.add(row)
    await commit(db)
    return category_data(row)


@router.put("/categories/{id}")
async def rename_category(id: str, data: CategoryInput, db: Db, scope: Scope):
    row = await scoped_record(db, TerminologyCategory, id, scope)
    # Reparenting an existing subtree can invalidate descendant depth and scope.
    # Rename is supported; move libraries independently without rewriting trees.
    if row.parent_id != data.parent_id:
        raise HTTPException(422, "分类改名时请保留父分类；可在库设置中移动术语库")
    existing = await db.scalar(
        select(TerminologyCategory.id).where(
            TerminologyCategory.access_scope == scope,
            TerminologyCategory.parent_id == row.parent_id,
            TerminologyCategory.name == data.name,
            TerminologyCategory.id != id,
        )
    )
    if existing:
        raise HTTPException(409, "该位置已有同名分类")
    row.name = data.name
    await commit(db)
    return category_data(row)


@router.delete("/categories/{id}")
async def delete_category(id: str, db: Db, scope: Scope):
    row = await scoped_record(db, TerminologyCategory, id, scope)
    for model, field in [
        (TerminologyCategory, TerminologyCategory.parent_id),
        (TerminologyLibrary, TerminologyLibrary.category_id),
        (TerminologyProfile, TerminologyProfile.category_id),
    ]:
        if await db.scalar(
            select(model.id).where(model.access_scope == scope, field == id).limit(1)
        ):
            raise HTTPException(409, "分类仍含子分类、术语库或绑定，请先移动或解除关联")
    await db.delete(row)
    await commit(db)
    return {"deleted": True}


@router.get("/libraries")
async def list_libraries(db: Db, scope: Scope):
    rows = (
        await db.scalars(
            select(TerminologyLibrary)
            .where(
                TerminologyLibrary.access_scope == scope,
            )
            .order_by(TerminologyLibrary.created_at, TerminologyLibrary.id)
        )
    ).all()
    counts = dict(
        (
            await db.execute(
                select(TerminologyEntry.library_id, func.count())
                .join(
                    TerminologyLibrary,
                    TerminologyEntry.library_id == TerminologyLibrary.id,
                )
                .where(TerminologyLibrary.access_scope == scope)
                .group_by(TerminologyEntry.library_id)
            )
        ).all()
    )
    return [
        {
            "id": BUILTIN_ID,
            "name": "内置 AI / 计算机术语",
            "description": "发布语料，只读；可复制到自己的库继续维护。",
            "category_id": None,
            "paper_id": None,
            "enabled": True,
            "readonly": True,
            "count": len(builtin_terms()),
        },
        *[library_data(row, counts.get(row.id, 0)) for row in rows],
    ]


@router.post("/libraries", status_code=201)
async def create_library(data: LibraryInput, db: Db, scope: Scope):
    await validate_library_scope(db, scope, data)
    count = await db.scalar(
        select(func.count())
        .select_from(TerminologyLibrary)
        .where(
            TerminologyLibrary.access_scope == scope,
        )
    )
    if count >= MAX_LIBRARIES:
        raise HTTPException(422, "工作区最多 200 个术语库")
    row = TerminologyLibrary(access_scope=scope, **data.model_dump())
    db.add(row)
    await commit(db)
    return library_data(row)


@router.put("/libraries/{id}")
async def update_library(id: str, data: LibraryInput, db: Db, scope: Scope):
    row = await scoped_record(db, TerminologyLibrary, id, scope)
    await validate_library_scope(db, scope, data)
    # A selected general library must not silently turn into another paper's override.
    if row.paper_id != data.paper_id:
        raise HTTPException(422, "库的论文归属不可修改；请导出后导入新的库")
    for key, value in data.model_dump().items():
        setattr(row, key, value)
    await commit(db)
    return library_data(row)


@router.delete("/libraries/{id}")
async def delete_library(id: str, db: Db, scope: Scope):
    row = await scoped_record(db, TerminologyLibrary, id, scope)
    profiles = (
        await db.scalars(
            select(TerminologyProfile).where(
                TerminologyProfile.access_scope == scope,
            )
        )
    ).all()
    for profile in profiles:
        profile.library_ids = json.dumps(
            [value for value in json.loads(profile.library_ids) if value != id]
        )
    await db.delete(row)
    await commit(db)
    return {"deleted": True}


@router.get("/libraries/{id}/entries")
async def list_entries(
    id: str,
    db: Db,
    scope: Scope,
    q: str = Query("", max_length=200),
    offset: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=200),
):
    if id == BUILTIN_ID:
        query = normalized_source(q)
        entries = [
            {
                "id": source,
                "source": source,
                "translation": translation,
                "aliases": [],
                "notes": "",
                "reference": "内置发布语料",
            }
            for source, translation in builtin_terms().items()
            if not query or query in source or query in translation
        ]
        entries.sort(key=lambda row: row["source"])
        return {"total": len(entries), "entries": entries[offset : offset + limit]}
    await scoped_record(db, TerminologyLibrary, id, scope)
    filters = [TerminologyEntry.library_id == id]
    if q:
        filters.append(
            TerminologyEntry.normalized_source.contains(normalized_source(q), autoescape=True)
            | TerminologyEntry.translation.contains(q.strip(), autoescape=True)
            | TerminologyEntry.aliases.contains(q.strip(), autoescape=True)
        )
    count = await db.scalar(select(func.count()).select_from(TerminologyEntry).where(*filters))
    rows = (
        await db.scalars(
            select(TerminologyEntry)
            .where(*filters)
            .order_by(
                TerminologyEntry.normalized_source,
            )
            .offset(offset)
            .limit(limit)
        )
    ).all()
    return {"total": count, "entries": [entry_data(row) for row in rows]}


def apply_term(row: TerminologyEntry, data: TermInput) -> None:
    for key, value in data.model_dump().items():
        setattr(row, key, json.dumps(value, ensure_ascii=False) if key == "aliases" else value)
    row.normalized_source = normalized_source(data.source)


@router.post("/libraries/{id}/entries", status_code=201)
async def create_entry(id: str, data: TermInput, db: Db, scope: Scope):
    await scoped_record(db, TerminologyLibrary, id, scope)
    await entry_capacity(db, scope, id, 1)
    row = TerminologyEntry(library_id=id)
    apply_term(row, data)
    db.add(row)
    await commit(db)
    return entry_data(row)


@router.put("/libraries/{id}/entries/{entry_id}")
async def update_entry(id: str, entry_id: str, data: TermInput, db: Db, scope: Scope):
    await scoped_record(db, TerminologyLibrary, id, scope)
    row = await db.scalar(
        select(TerminologyEntry).where(
            TerminologyEntry.id == entry_id,
            TerminologyEntry.library_id == id,
        )
    )
    if row is None:
        raise HTTPException(404, "词条不存在")
    apply_term(row, data)
    await commit(db)
    return entry_data(row)


@router.delete("/libraries/{id}/entries/{entry_id}")
async def delete_entry(id: str, entry_id: str, db: Db, scope: Scope):
    await scoped_record(db, TerminologyLibrary, id, scope)
    row = await db.scalar(
        select(TerminologyEntry).where(
            TerminologyEntry.id == entry_id,
            TerminologyEntry.library_id == id,
        )
    )
    if row is None:
        raise HTTPException(404, "词条不存在")
    await db.delete(row)
    await commit(db)
    return {"deleted": True}


@router.post("/libraries/{id}/import")
async def import_entries(id: str, data: ImportInput, db: Db, scope: Scope):
    await scoped_record(db, TerminologyLibrary, id, scope)
    inputs = parse_term_import(data.content, data.format)
    existing = {
        row.normalized_source: row
        for row in (
            await db.scalars(
                select(TerminologyEntry).where(
                    TerminologyEntry.library_id == id,
                )
            )
        ).all()
    }
    addition = sum(normalized_source(row.source) not in existing for row in inputs)
    await entry_capacity(db, scope, id, addition)
    updated = skipped = 0
    for input in inputs:
        row = existing.get(normalized_source(input.source))
        if row is not None and data.conflict == "keep":
            skipped += 1
            continue
        if row is None:
            row = TerminologyEntry(library_id=id)
            db.add(row)
        else:
            updated += 1
        apply_term(row, input)
    await commit(db)
    return {"added": addition, "updated": updated, "skipped": skipped}


@router.get("/libraries/{id}/export")
async def export_library(
    id: str,
    db: Db,
    scope: Scope,
    offset: int = Query(0, ge=0),
    limit: int = Query(1000, ge=1, le=1000),
):
    # Every exported part can be imported atomically under the same 1000-row limit.
    if id == BUILTIN_ID:
        all_entries = [
            {
                "source": source,
                "translation": translation,
                "aliases": [],
                "notes": "",
                "reference": "内置发布语料",
            }
            for source, translation in sorted(builtin_terms().items())
        ]
        entries = all_entries[offset : offset + limit]
        total = len(all_entries)
        library = {"name": "内置 AI / 计算机术语", "description": "只读发布语料"}
    else:
        row = await scoped_record(db, TerminologyLibrary, id, scope)
        total = await db.scalar(
            select(func.count())
            .select_from(TerminologyEntry)
            .where(
                TerminologyEntry.library_id == id,
            )
        )
        rows = (
            await db.scalars(
                select(TerminologyEntry)
                .where(TerminologyEntry.library_id == id)
                .order_by(TerminologyEntry.normalized_source)
                .offset(offset)
                .limit(limit)
            )
        ).all()
        entries = [
            {key: value for key, value in entry_data(entry).items() if key != "id"}
            for entry in rows
        ]
        library = {"name": row.name, "description": row.description}
    # Names and aliases can make 1000 rows >2 MiB; shrink parts so round trips
    # always fit the import boundary. Browser follows next_offset until complete.
    while len(json.dumps(entries, ensure_ascii=False).encode("utf-8")) > 1800000:
        entries = entries[: len(entries) // 2]
    return {
        "schema_version": 1,
        "library": library,
        "entries": entries,
        "total": total,
        "next_offset": offset + len(entries) if offset + len(entries) < total else None,
    }


@router.get("/papers")
async def terminology_papers(db: Db, scope: Scope, q: str = Query("", max_length=200)):
    query = select(Paper).where(Paper.access_scope == scope)
    if q:
        query = query.where(Paper.title.contains(q, autoescape=True))
    rows = (await db.scalars(query.order_by(Paper.created_at.desc()).limit(100))).all()
    return [{"id": row.id, "title": row.title} for row in rows]


async def get_profile(db: AsyncSession, scope: str, paper_id: str | None = None) -> dict:
    if paper_id:
        await scoped_record(db, Paper, paper_id, scope)
    row = await profile_record(db, scope, paper_id)
    inherited = row is None and paper_id is not None
    if row is None and paper_id:
        row = await profile_record(db, scope)
    return {
        "category_id": row.category_id if row else None,
        "library_ids": json.loads(row.library_ids) if row else [],
        "use_builtin": row.use_builtin if row else True,
        "inherited": inherited,
    }


async def save_profile(
    data: ProfileInput, db: AsyncSession, scope: str, paper_id: str | None = None
) -> dict:
    if paper_id:
        await scoped_record(db, Paper, paper_id, scope)
    if data.category_id:
        await scoped_record(db, TerminologyCategory, data.category_id, scope)
    for id in data.library_ids:
        library = await scoped_record(db, TerminologyLibrary, id, scope)
        if library.paper_id is not None:
            raise HTTPException(422, "论文专用库自动生效，无需额外勾选")
    row = await profile_record(db, scope, paper_id)
    if row is None:
        row = TerminologyProfile(
            access_scope=scope, target=paper_id or "default", paper_id=paper_id
        )
        db.add(row)
    row.category_id = data.category_id
    row.library_ids = json.dumps(data.library_ids)
    row.use_builtin = data.use_builtin
    await commit(db)
    return await get_profile(db, scope, paper_id)


@router.get("/profile")
async def workspace_profile(db: Db, scope: Scope):
    return await get_profile(db, scope)


@router.put("/profile")
async def update_workspace_profile(data: ProfileInput, db: Db, scope: Scope):
    return await save_profile(data, db, scope)


@router.get("/papers/{paper_id}/profile")
async def paper_profile(paper_id: str, db: Db, scope: Scope):
    return await get_profile(db, scope, paper_id)


@router.put("/papers/{paper_id}/profile")
async def update_paper_profile(paper_id: str, data: ProfileInput, db: Db, scope: Scope):
    return await save_profile(data, db, scope, paper_id)


@router.delete("/papers/{paper_id}/profile")
async def reset_paper_profile(paper_id: str, db: Db, scope: Scope):
    await scoped_record(db, Paper, paper_id, scope)
    row = await profile_record(db, scope, paper_id)
    if row:
        await db.delete(row)
        await commit(db)
    return await get_profile(db, scope, paper_id)


@router.get("/resolve")
async def preview_terminology(
    db: Db, scope: Scope, paper_id: str | None = Query(None, max_length=12)
):
    result = await resolve_terminology(db, scope, paper_id)
    result.pop("snapshot")
    result["sample"] = [
        {"source": source, "translation": translation}
        for source, translation in list(sorted(result.pop("terms").items()))[:50]
    ]
    return result


@router.post("/templates/{name}", status_code=201)
async def install_template(name: str, db: Db, scope: Scope):
    if name not in STARTER_TEMPLATES:
        raise HTTPException(404, "模板不存在")
    discipline, domains, terms = STARTER_TEMPLATES[name]
    root = await db.scalar(
        select(TerminologyCategory).where(
            TerminologyCategory.access_scope == scope,
            TerminologyCategory.name == discipline,
            TerminologyCategory.parent_id.is_(None),
        )
    )
    if root is None:
        total = await db.scalar(
            select(func.count())
            .select_from(TerminologyCategory)
            .where(TerminologyCategory.access_scope == scope)
        )
        if total + 1 + len(domains) > MAX_CATEGORIES:
            raise HTTPException(422, "分类数量达到上限")
        root = TerminologyCategory(access_scope=scope, name=discipline)
        db.add(root)
        await db.flush()
    children = set(
        (
            await db.scalars(
                select(TerminologyCategory.name).where(
                    TerminologyCategory.access_scope == scope,
                    TerminologyCategory.parent_id == root.id,
                )
            )
        ).all()
    )
    total_categories = await db.scalar(
        select(func.count())
        .select_from(TerminologyCategory)
        .where(
            TerminologyCategory.access_scope == scope,
        )
    )
    if total_categories + len(set(domains) - children) > MAX_CATEGORIES:
        raise HTTPException(422, "分类数量达到上限")
    for domain in domains:
        if not await db.scalar(
            select(TerminologyCategory.id).where(
                TerminologyCategory.access_scope == scope,
                TerminologyCategory.parent_id == root.id,
                TerminologyCategory.name == domain,
            )
        ):
            db.add(TerminologyCategory(access_scope=scope, name=domain, parent_id=root.id))
    library = await db.scalar(
        select(TerminologyLibrary).where(
            TerminologyLibrary.access_scope == scope,
            TerminologyLibrary.category_id == root.id,
            TerminologyLibrary.name == f"{discipline}入门术语",
        )
    )
    if library is None:
        total = await db.scalar(
            select(func.count())
            .select_from(TerminologyLibrary)
            .where(TerminologyLibrary.access_scope == scope)
        )
        if total >= MAX_LIBRARIES:
            raise HTTPException(422, "术语库数量达到上限")
        library = TerminologyLibrary(
            access_scope=scope,
            name=f"{discipline}入门术语",
            category_id=root.id,
            description="基础示例，可编辑扩展；请按论文和学科惯例审核新增译法。",
        )
        db.add(library)
        await db.flush()
    existing = set(
        (
            await db.scalars(
                select(TerminologyEntry.normalized_source).where(
                    TerminologyEntry.library_id == library.id
                )
            )
        ).all()
    )
    await entry_capacity(
        db, scope, library.id, sum(normalized_source(key) not in existing for key in terms)
    )
    for source, translation in terms.items():
        if normalized_source(source) not in existing:
            row = TerminologyEntry(library_id=library.id)
            apply_term(row, TermInput(source=source, translation=translation))
            db.add(row)
    await commit(db)
    return library_data(library, len(existing | {normalized_source(key) for key in terms}))
