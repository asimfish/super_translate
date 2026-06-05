"""Paper database model."""

import uuid
from datetime import datetime
from enum import Enum as PyEnum

from sqlalchemy import DateTime, Enum, Index, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class TranslationStatus(str, PyEnum):
    PENDING = "pending"
    TRANSLATING = "translating"
    COMPLETED = "completed"
    FAILED = "failed"


def generate_id() -> str:
    return uuid.uuid4().hex[:12]


class Paper(Base):
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
        String(20), nullable=False, default=TranslationStatus.PENDING.value
    )
    translation_progress: Mapped[float] = mapped_column(nullable=False, default=0.0)
    translation_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    tags: Mapped[str] = mapped_column(String(1000), nullable=False, default="")
    notes: Mapped[str] = mapped_column(Text, nullable=False, default="")
    created_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, server_default=func.now(), onupdate=func.now()
    )
