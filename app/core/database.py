"""Database setup with async SQLAlchemy."""

from collections.abc import AsyncGenerator

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase
from sqlalchemy.pool import StaticPool

from .config import settings

# SQLite-specific connection pool settings
# StaticPool maintains a single connection per thread, which is optimal for SQLite
engine = create_async_engine(
    settings.db_url,
    echo=settings.debug,
    poolclass=StaticPool,
    connect_args={"check_same_thread": False},
)
async_session = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)


class Base(DeclarativeBase):
    """SQLAlchemy declarative base for all models."""

    pass


async def init_db() -> None:
    """Initialize database tables and indexes."""
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        # create_all skips indexes on existing tables — ensure they exist
        await conn.execute(
            text("CREATE INDEX IF NOT EXISTS ix_papers_status ON papers (translation_status)"),
        )
        await conn.execute(
            text("CREATE INDEX IF NOT EXISTS ix_papers_created ON papers (created_at)"),
        )
        await conn.execute(
            text(
                "CREATE INDEX IF NOT EXISTS ix_translation_jobs_paper_created "
                "ON translation_jobs (paper_id, created_at)"
            ),
        )
        await conn.execute(
            text(
                "CREATE INDEX IF NOT EXISTS ix_translation_jobs_status "
                "ON translation_jobs (status)"
            ),
        )
        await conn.execute(
            text(
                "CREATE INDEX IF NOT EXISTS ix_translation_jobs_cancel_requested "
                "ON translation_jobs (cancel_requested)"
            ),
        )


async def get_session() -> AsyncGenerator[AsyncSession, None]:
    """Get an async database session."""
    async with async_session() as session:
        try:
            yield session
        except Exception:
            await session.rollback()
            raise
