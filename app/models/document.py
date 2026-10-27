"""Additive organization records; existing paper IDs and files stay valid."""

from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Index, Integer, String, text
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class PaperDocument(Base):
    __tablename__ = "paper_documents"
    __table_args__ = (
        Index("ix_documents_scope_bytes", "access_scope", "file_sha256"),
        Index("ix_documents_scope_title", "access_scope", "title_key"),
        Index("ix_documents_scope_imported", "access_scope", "last_imported_at"),
        Index(
            "uq_documents_scope_content",
            "access_scope",
            "content_sha256",
            unique=True,
            sqlite_where=text("canonical_paper_id IS NULL"),
        ),
    )

    paper_id: Mapped[str] = mapped_column(
        String(12),
        ForeignKey("papers.id", ondelete="CASCADE"),
        primary_key=True,
    )
    access_scope: Mapped[str] = mapped_column(String(80), nullable=False)
    file_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    content_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    title_key: Mapped[str] = mapped_column(String(500), nullable=False)
    canonical_paper_id: Mapped[str | None] = mapped_column(
        String(12),
        ForeignKey("papers.id", ondelete="CASCADE"),
        nullable=True,
    )
    first_imported_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    last_imported_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)


class PaperImport(Base):
    __tablename__ = "paper_imports"
    __table_args__ = (Index("ix_imports_paper_time", "paper_id", "imported_at"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    paper_id: Mapped[str] = mapped_column(
        String(12),
        ForeignKey("papers.id", ondelete="CASCADE"),
        nullable=False,
    )
    access_scope: Mapped[str] = mapped_column(String(80), nullable=False)
    filename: Mapped[str] = mapped_column(String(500), nullable=False)
    source_title: Mapped[str] = mapped_column(String(500), nullable=False, default="")
    imported_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
