"""Workspace-owned terminology taxonomy, libraries, entries and profiles."""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, Index, String, Text, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


def terminology_id() -> str:
    return uuid.uuid4().hex


class TerminologyCategory(Base):
    __tablename__ = "terminology_categories"
    __table_args__ = (Index("ix_term_category_scope", "access_scope"),)
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=terminology_id)
    access_scope: Mapped[str] = mapped_column(String(80), nullable=False)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    parent_id: Mapped[str | None] = mapped_column(
        ForeignKey("terminology_categories.id", ondelete="RESTRICT"),
        nullable=True,
    )


class TerminologyLibrary(Base):
    __tablename__ = "terminology_libraries"
    __table_args__ = (Index("ix_term_library_scope", "access_scope"),)
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=terminology_id)
    access_scope: Mapped[str] = mapped_column(String(80), nullable=False)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False, default="")
    category_id: Mapped[str | None] = mapped_column(
        ForeignKey("terminology_categories.id", ondelete="RESTRICT"),
        nullable=True,
    )
    paper_id: Mapped[str | None] = mapped_column(
        ForeignKey("papers.id", ondelete="CASCADE"),
        nullable=True,
    )
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())


class TerminologyEntry(Base):
    __tablename__ = "terminology_entries"
    __table_args__ = (
        UniqueConstraint("library_id", "normalized_source", name="uq_term_entry_source"),
    )
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=terminology_id)
    library_id: Mapped[str] = mapped_column(
        ForeignKey("terminology_libraries.id", ondelete="CASCADE"),
        nullable=False,
    )
    source: Mapped[str] = mapped_column(String(240), nullable=False)
    normalized_source: Mapped[str] = mapped_column(String(480), nullable=False)
    translation: Mapped[str] = mapped_column(String(240), nullable=False)
    aliases: Mapped[str] = mapped_column(Text, nullable=False, default="[]")
    notes: Mapped[str] = mapped_column(Text, nullable=False, default="")
    reference: Mapped[str] = mapped_column(String(500), nullable=False, default="")


class TerminologyProfile(Base):
    __tablename__ = "terminology_profiles"
    __table_args__ = (UniqueConstraint("access_scope", "target", name="uq_term_profile_target"),)
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=terminology_id)
    access_scope: Mapped[str] = mapped_column(String(80), nullable=False)
    target: Mapped[str] = mapped_column(String(32), nullable=False, default="default")
    paper_id: Mapped[str | None] = mapped_column(
        ForeignKey("papers.id", ondelete="CASCADE"),
        nullable=True,
    )
    category_id: Mapped[str | None] = mapped_column(
        ForeignKey("terminology_categories.id", ondelete="RESTRICT"),
        nullable=True,
    )
    library_ids: Mapped[str] = mapped_column(Text, nullable=False, default="[]")
    use_builtin: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
