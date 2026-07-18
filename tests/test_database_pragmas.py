"""Regression tests for SQLite concurrency/safety PRAGMAs."""

import sqlite3

from sqlalchemy.pool import StaticPool

from app.core.database import apply_sqlite_pragmas, engine


def test_file_sqlite_uses_async_queue_pool_for_concurrent_sessions():
    assert not isinstance(engine.sync_engine.pool, StaticPool)
    assert engine.sync_engine.pool.__class__.__name__ == "AsyncAdaptedQueuePool"


def test_apply_sqlite_pragmas_sets_wal_busy_timeout_and_fk(tmp_path):
    db_path = tmp_path / "pragma_test.db"
    conn = sqlite3.connect(str(db_path))
    try:
        apply_sqlite_pragmas(conn)
        cur = conn.cursor()
        # WAL allows the 2s status poll (reader) and progress writes (writer)
        # to proceed concurrently instead of blocking each other.
        assert cur.execute("PRAGMA journal_mode").fetchone()[0].lower() == "wal"
        # Contended writers wait instead of immediately failing with locked DB.
        assert cur.execute("PRAGMA busy_timeout").fetchone()[0] == 5000
        # ON DELETE CASCADE on translation_jobs requires foreign keys enabled.
        assert cur.execute("PRAGMA foreign_keys").fetchone()[0] == 1
    finally:
        conn.close()
