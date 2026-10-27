"""Term parsing and deterministic, workspace-scoped translation snapshots."""

from __future__ import annotations

import csv
import hashlib
import io
import json
import unicodedata

from fastapi import HTTPException
from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.paper import Paper
from app.models.terminology import (
    TerminologyCategory,
    TerminologyEntry,
    TerminologyLibrary,
    TerminologyProfile,
)

MAX_CATEGORIES = 500
MAX_LIBRARIES = 200
MAX_ENTRIES = 20_000
MAX_LIBRARY_ENTRIES = 5_000
MAX_IMPORT_ROWS = 1_000
MAX_IMPORT_BYTES = 2 * 1024 * 1024
BUILTIN_ID = "builtin-ai"


def normalized_source(value: str) -> str:
    return unicodedata.normalize("NFKC", value).strip().casefold()


def single_line(value: str) -> str:
    value = value.strip()
    if not value or any(unicodedata.category(char).startswith("C") for char in value):
        raise ValueError("请输入非空、无换行或控制字符的文本")
    return value


class TermInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    source: str = Field(min_length=1, max_length=240)
    translation: str = Field(min_length=1, max_length=240)
    aliases: list[str] = Field(default_factory=list, max_length=10)
    notes: str = Field(default="", max_length=1000)
    reference: str = Field(default="", max_length=500)

    @field_validator("source", "translation")
    @classmethod
    def validate_text(cls, value: str) -> str:
        return single_line(value)

    @field_validator("aliases")
    @classmethod
    def validate_aliases(cls, values: list[str]) -> list[str]:
        result = []
        for value in values:
            value = single_line(value)
            if len(value) > 240:
                raise ValueError("别名不能超过 240 字符")
            if value not in result:
                result.append(value)
        return result


def parse_term_import(content: str, format: str) -> list[TermInput]:
    if len(content.encode("utf-8")) > MAX_IMPORT_BYTES:
        raise HTTPException(413, "导入内容不能超过 2 MiB")
    try:
        if format == "json":
            data = json.loads(content.lstrip("\ufeff"))
            if isinstance(data, dict):
                if data.get("schema_version", 1) != 1:
                    raise ValueError("不支持的术语文件版本")
                data = data.get("entries")
            if not isinstance(data, list):
                raise ValueError("JSON 需为词条数组或含 entries 的导出对象")
        else:
            reader = csv.DictReader(io.StringIO(content.lstrip("\ufeff")))
            if not reader.fieldnames or not {"source", "translation"} <= set(reader.fieldnames):
                raise ValueError("CSV 表头需包含 source,translation；可加 aliases,notes,reference")
            if set(reader.fieldnames) - {"source", "translation", "aliases", "notes", "reference"}:
                raise ValueError("CSV 含有未知列")
            data = []
            for row in reader:
                if len(data) >= MAX_IMPORT_ROWS:
                    raise ValueError("每次最多导入 1000 行")
                if None in row or any(value is None for value in row.values()):
                    raise ValueError("CSV 行列数不一致")
                aliases = row.pop("aliases", "")
                row["aliases"] = [v.strip() for v in aliases.split("|") if v.strip()]
                data.append(row)
        if not data or len(data) > MAX_IMPORT_ROWS:
            raise ValueError("每次导入 1–1000 个词条")
        result = [TermInput.model_validate(row) for row in data]
        sources = [normalized_source(row.source) for row in result]
        if len(set(sources)) != len(sources):
            raise ValueError("导入内容含重复源词，请先合并")
        return result
    except (ValueError, TypeError, csv.Error, RecursionError):
        # Validation exceptions may contain full user inputs. Return a stable,
        # bounded error, and keep the entire transaction untouched.
        raise HTTPException(
            422, "导入格式错误：检查版本、表头、重复词、长度及行数（最多 1000）"
        ) from None


def builtin_terms() -> dict[str, str]:
    from pdf_zh_translator.corpus import _get_flat_terms

    return dict(_get_flat_terms())


def terminology_fingerprint(terms: dict[str, str]) -> str:
    payload = json.dumps(terms, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]


async def scoped_record(db: AsyncSession, model, id: str, scope: str):
    record = await db.scalar(select(model).where(model.id == id, model.access_scope == scope))
    if record is None:
        raise HTTPException(404, "记录不存在")
    return record


async def profile_record(db: AsyncSession, scope: str, paper_id: str | None = None):
    return await db.scalar(
        select(TerminologyProfile).where(
            TerminologyProfile.access_scope == scope,
            TerminologyProfile.target == (paper_id or "default"),
        )
    )


async def resolve_terminology(
    db: AsyncSession,
    scope: str,
    paper_id: str | None = None,
) -> dict:
    """Resolve once per job; never share mutable glossary state across jobs."""
    if paper_id is not None:
        await scoped_record(db, Paper, paper_id, scope)
    profile = await profile_record(db, scope, paper_id)
    inherited = profile is None and paper_id is not None
    if profile is None and paper_id is not None:
        profile = await profile_record(db, scope)
    use_builtin = profile.use_builtin if profile else True
    category_id = profile.category_id if profile else None
    selected_ids = json.loads(profile.library_ids) if profile else []
    categories = {
        row.id: row
        for row in (
            await db.scalars(
                select(TerminologyCategory).where(
                    TerminologyCategory.access_scope == scope,
                )
            )
        ).all()
    }
    ancestors = []
    current = category_id
    while current in categories and current not in ancestors and len(ancestors) < 8:
        ancestors.append(current)
        current = categories[current].parent_id
    ancestors.reverse()
    libraries = (
        await db.scalars(
            select(TerminologyLibrary)
            .where(
                TerminologyLibrary.access_scope == scope,
                TerminologyLibrary.enabled.is_(True),
            )
            .order_by(TerminologyLibrary.created_at, TerminologyLibrary.id)
        )
    ).all()
    ranks = {}
    for library in libraries:
        if library.paper_id:
            if library.paper_id == paper_id:
                ranks[library.id] = 100
            continue
        if library.id in selected_ids:
            ranks[library.id] = 20 + selected_ids.index(library.id)
        elif library.category_id in ancestors:
            ranks[library.id] = 2 + ancestors.index(library.category_id)
        elif library.category_id is None:
            ranks[library.id] = 1
    ordered = sorted((row for row in libraries if row.id in ranks), key=lambda row: ranks[row.id])
    resolved = builtin_terms() if use_builtin else {}
    origins = {key: "内置 AI 术语" for key in resolved}
    conflicts = []
    entries_by_library: dict[str, list[TerminologyEntry]] = {}
    if ranks:
        entries = (
            await db.scalars(
                select(TerminologyEntry)
                .where(
                    TerminologyEntry.library_id.in_(ranks),
                )
                .order_by(TerminologyEntry.normalized_source)
            )
        ).all()
        for entry in entries:
            entries_by_library.setdefault(entry.library_id, []).append(entry)
    canonical_aliases: dict[str, set[str]] = {}
    alias_owners: dict[str, str] = {}
    for library in ordered:
        for entry in entries_by_library.get(library.id, []):
            canonical = normalized_source(entry.source)
            inherited_aliases = sorted(
                alias
                for alias in canonical_aliases.get(canonical, set())
                if alias_owners.get(alias) == canonical
            )
            explicit_aliases = json.loads(entry.aliases)
            canonical_aliases.setdefault(canonical, set()).update(
                normalized_source(alias) for alias in explicit_aliases
            )
            for source in [entry.source, *inherited_aliases, *explicit_aliases]:
                key = normalized_source(source)
                previous = resolved.get(key)
                if previous is not None and previous != entry.translation and len(conflicts) < 100:
                    conflicts.append(
                        {
                            "source": source,
                            "previous": previous,
                            "previous_library": origins[key],
                            "translation": entry.translation,
                            "library": library.name,
                        }
                    )
                resolved[key] = entry.translation
                origins[key] = library.name
                alias_owners[key] = canonical
    # None preserves the legacy path and cache filename when no custom scope
    # has ever been set. An explicit empty dict intentionally disables AI terms.
    snapshot = resolved if profile is not None or ordered else None
    return {
        "terms": resolved,
        "snapshot": snapshot,
        "inherited": inherited,
        "count": len(resolved),
        "libraries": [{"id": row.id, "name": row.name} for row in ordered],
        "use_builtin": use_builtin,
        "category_id": category_id,
        "conflicts": conflicts,
        "fingerprint": terminology_fingerprint(resolved),
    }


STARTER_TEMPLATES = {
    "chemistry": (
        "化学",
        ["有机化学", "分析化学", "物理化学"],
        {
            "catalyst": "催化剂",
            "activation energy": "活化能",
            "reaction rate": "反应速率",
            "chemical equilibrium": "化学平衡",
            "molar concentration": "摩尔浓度",
            "oxidation state": "氧化态",
            "covalent bond": "共价键",
            "functional group": "官能团",
        },
    ),
    "biology": (
        "生物学",
        ["分子生物学", "细胞生物学", "生物信息学"],
        {
            "gene expression": "基因表达",
            "cell membrane": "细胞膜",
            "transcription": "转录",
            "translation": "翻译",
            "protein folding": "蛋白质折叠",
            "enzyme kinetics": "酶动力学",
            "genome": "基因组",
            "amino acid": "氨基酸",
        },
    ),
}
