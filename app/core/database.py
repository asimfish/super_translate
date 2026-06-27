"""Database setup with async SQLAlchemy."""

from collections.abc import AsyncGenerator

from sqlalchemy import event, text
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


def apply_sqlite_pragmas(dbapi_connection) -> None:
    """Apply concurrency/safety PRAGMAs to a raw SQLite connection.

    - WAL lets readers (status polling) and a writer (progress updates) work
      concurrently instead of blocking each other, which is critical because the
      UI polls every 2s while translations write progress frequently.
    - busy_timeout makes a contended write wait instead of immediately raising
      "database is locked".
    - synchronous=NORMAL is the safe/fast pairing with WAL.
    - foreign_keys=ON enables the ON DELETE CASCADE declared on translation_jobs.
    """
    cursor = dbapi_connection.cursor()
    try:
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.execute("PRAGMA busy_timeout=5000")
        cursor.execute("PRAGMA synchronous=NORMAL")
        cursor.execute("PRAGMA foreign_keys=ON")
    finally:
        cursor.close()


@event.listens_for(engine.sync_engine, "connect")
def _configure_sqlite_connection(dbapi_connection, _connection_record) -> None:
    apply_sqlite_pragmas(dbapi_connection)


class Base(DeclarativeBase):
    """SQLAlchemy declarative base for all models."""

    pass


async def _ensure_column(conn, table: str, column: str, ddl_type: str) -> None:
    """Add a column to an existing SQLite table if it is missing.

    create_all() never alters existing tables, so new columns on already-created
    databases need an explicit, idempotent ADD COLUMN.
    """
    result = await conn.execute(text(f"PRAGMA table_info({table})"))
    existing = {row[1] for row in result.fetchall()}
    if column not in existing:
        await conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {column} {ddl_type}"))


async def init_db() -> None:
    """Initialize database tables and indexes."""
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        # Lightweight migrations for columns added after the first release.
        await _ensure_column(
            conn, "papers", "translation_stage", "VARCHAR(40) NOT NULL DEFAULT ''"
        )
        await _ensure_column(conn, "papers", "translation_eta_seconds", "INTEGER")
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
