"""Durable translation recovery policy helpers."""

from __future__ import annotations

import hashlib
from functools import lru_cache
from pathlib import Path

_REVISION_FILES = (
    "pdf_zh_translator/pdf_layout.py",
    "pdf_zh_translator/page_inspector.py",
    "app/api/papers.py",
    "app/services/layout_fix.py",
    "app/services/quality_agent.py",
    "app/services/translation_recovery.py",
)


@lru_cache(maxsize=1)
def current_engine_revision() -> str:
    """Return a deterministic revision for code that can change PDF results."""
    root = Path(__file__).resolve().parents[2]
    digest = hashlib.sha256()
    for relative_path in _REVISION_FILES:
        path = root / relative_path
        digest.update(relative_path.encode("utf-8"))
        try:
            digest.update(path.read_bytes())
        except OSError:
            digest.update(b"<missing>")
    return digest.hexdigest()[:32]


def recovery_attempt_limit(value: object, *, default: int = 3) -> int:
    """Normalize a configured attempt budget to a conservative range."""
    attempts = value if type(value) is int else default
    return max(1, min(8, attempts))


def recovery_backoff_seconds(value: object, attempt: int) -> float:
    """Return bounded linear backoff before a follow-up attempt."""
    base = float(value) if type(value) in {int, float} else 2.0
    return max(0.0, min(30.0, base * max(0, attempt - 1)))
