"""User account model for multi-user login."""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import DateTime, String, func
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


def generate_user_id() -> str:
    return uuid.uuid4().hex[:12]


class User(Base):
    """A login account.

    ``token`` is the long-lived bearer token the web UI stores after a
    successful password login; it maps to the user's ``access_scope``, which
    isolates their papers exactly like a workspace token does.
    """

    __tablename__ = "users"

    id: Mapped[str] = mapped_column(String(12), primary_key=True, default=generate_user_id)
    username: Mapped[str] = mapped_column(String(80), unique=True, nullable=False)
    password_hash: Mapped[str] = mapped_column(String(200), nullable=False)
    token: Mapped[str] = mapped_column(String(120), unique=True, nullable=False)
    access_scope: Mapped[str] = mapped_column(String(80), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime,
        nullable=False,
        server_default=func.now(),
    )
