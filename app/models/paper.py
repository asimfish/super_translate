"""Paper database model."""

from __future__ import annotations

import uuid
from datetime import datetime
from enum import Enum as PyEnum

from sqlalchemy import Boolean, DateTime, ForeignKey, Index, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class TranslationStatus(str, PyEnum):
    """Translation status enum."""

    PENDING = "pending"
    TRANSLATING = "translating"
    COMPLETED = "completed"
    FAILED = "failed"


class TranslationJobStatus(str, PyEnum):
    """Durable translation job status."""

    QUEUED = "queued"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


def generate_id() -> str:
    return uuid.uuid4().hex[:12]


def generate_job_id() -> str:
    return uuid.uuid4().hex


class Paper(Base):
    """Paper database model."""

    __tablename__ = "papers"
    __table_args__ = (
        Index("ix_papers_status", "translation_status"),
        Index("ix_papers_created", "created_at"),
    )

    id: Mapped[str] = mapped_column(String(12), primary_key=True, default=generate_id)
    title: Mapped[str] = mapped_column(String(500), nullable=False)
    original_filename: Mapped[str] = mapped_column(String(500), nullable=False)
    stored_filename: Mapped[str] = mapped_column(String(500), nullable=False)
    translated_filename: Mapped[str | None] = mapped_column(String(500), nullable=True)
    dual_filename: Mapped[str | None] = mapped_column(String(500), nullable=True)
    file_size: Mapped[int] = mapped_column(nullable=False, default=0)
    page_count: Mapped[int] = mapped_column(nullable=False, default=0)
    translation_status: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        default=TranslationStatus.PENDING.value,
    )
    translation_progress: Mapped[float] = mapped_column(nullable=False, default=0.0)
    translation_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    translation_log: Mapped[str] = mapped_column(Text, nullable=False, default="")
    tags: Mapped[str] = mapped_column(String(1000), nullable=False, default="")
    notes: Mapped[str] = mapped_column(Text, nullable=False, default="")
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


class TranslationJob(Base):
    """Durable translation job record.

    The web app still executes jobs through FastAPI background tasks for local
    simplicity, but this table makes job parameters, cancellation, progress,
    heartbeat, and terminal state visible across restarts and worker processes.
    """

    __tablename__ = "translation_jobs"
    __table_args__ = (
        Index("ix_translation_jobs_paper_created", "paper_id", "created_at"),
        Index("ix_translation_jobs_status", "status"),
        Index("ix_translation_jobs_cancel_requested", "cancel_requested"),
    )

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=generate_job_id)
    paper_id: Mapped[str] = mapped_column(
        String(12),
        ForeignKey("papers.id", ondelete="CASCADE"),
        nullable=False,
    )
    backend: Mapped[str] = mapped_column(String(50), nullable=False, default="")
    quality: Mapped[str] = mapped_column(String(20), nullable=False, default="balanced")
    preserve_graphics_text: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    skip_overflow: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    qa_mode: Mapped[str] = mapped_column(String(20), nullable=False, default="single")
    qa_max_passes: Mapped[int] = mapped_column(Integer, nullable=False, default=4)
    ocr_mode: Mapped[str] = mapped_column(String(20), nullable=False, default="off")
    ocr_language: Mapped[str] = mapped_column(String(50), nullable=False, default="eng")
    ocr_dpi: Mapped[int] = mapped_column(Integer, nullable=False, default=180)
    status: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        default=TranslationJobStatus.QUEUED.value,
    )
    progress: Mapped[float] = mapped_column(nullable=False, default=0.0)
    cancel_requested: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    heartbeat_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    started_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
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
