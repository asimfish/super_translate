"""Durable state for resumable PDF uploads."""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Index, Integer, String, func
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


def generate_upload_id() -> str:
    return uuid.uuid4().hex


class PdfUploadSession(Base):
    """Tenant-bound upload state shared by retries and server workers."""

    __tablename__ = "pdf_upload_sessions"
    __table_args__ = (
        Index("ix_pdf_upload_sessions_scope_updated", "access_scope", "updated_at"),
        Index(
            "ix_pdf_upload_sessions_scope_content",
            "access_scope",
            "content_sha256",
            "file_size",
        ),
    )

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=generate_upload_id)
    access_scope: Mapped[str] = mapped_column(String(80), nullable=False)
    original_filename: Mapped[str] = mapped_column(String(500), nullable=False)
    tags: Mapped[str] = mapped_column(String(1000), nullable=False, default="")
    file_size: Mapped[int] = mapped_column(Integer, nullable=False)
    chunk_size: Mapped[int] = mapped_column(Integer, nullable=False)
    content_sha256: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    paper_id: Mapped[str | None] = mapped_column(
        String(12),
        ForeignKey("papers.id", ondelete="SET NULL"),
        nullable=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime,
        nullable=False,
        server_default=func.now(),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime,
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )

    @property
    def chunk_count(self) -> int:
        return (self.file_size + self.chunk_size - 1) // self.chunk_size

