"""Filesystem-backed, integrity-checked resumable upload storage."""

from __future__ import annotations

import hashlib
import os
import re
import shutil
import uuid
from collections.abc import AsyncIterable
from pathlib import Path
from typing import BinaryIO, Protocol

from app.core.config import settings

try:
    import fcntl
except ImportError:  # pragma: no cover - production targets Linux/macOS
    fcntl = None


_UPLOAD_ID_RE = re.compile(r"^[0-9a-f]{32}$")
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")


class UploadValidationError(ValueError):
    """Raised when an upload chunk or assembled PDF fails validation."""


class ChunkUploadStore(Protocol):
    """Storage boundary for resumable chunks."""

    async def write_chunk(
        self,
        upload_id: str,
        index: int,
        expected_size: int,
        expected_sha256: str,
        source: AsyncIterable[bytes],
    ) -> None: ...

    def uploaded_chunks(self, upload_id: str, chunk_count: int) -> list[int]: ...

    def assemble_pdf(
        self,
        upload_id: str,
        chunk_count: int,
        expected_size: int,
        destination: Path,
    ) -> str: ...


class FilesystemChunkUploadStore:
    """Store bounded chunks as atomic files and assemble them deterministically."""

    def __init__(self, root: Path | None = None) -> None:
        self.root = (root or settings.resumable_uploads_path).resolve()

    def _session_dir(self, upload_id: str) -> Path:
        if not _UPLOAD_ID_RE.fullmatch(upload_id):
            raise UploadValidationError("Invalid upload ID")
        return self.root / upload_id

    def _chunk_path(self, upload_id: str, index: int) -> Path:
        if index < 0:
            raise UploadValidationError("Invalid chunk index")
        return self._session_dir(upload_id) / f"{index}.chunk"

    async def write_chunk(
        self,
        upload_id: str,
        index: int,
        expected_size: int,
        expected_sha256: str,
        source: AsyncIterable[bytes],
    ) -> None:
        expected_sha256 = expected_sha256.strip().lower()
        if not _SHA256_RE.fullmatch(expected_sha256):
            raise UploadValidationError("Invalid chunk SHA256")
        if expected_size <= 0:
            raise UploadValidationError("Invalid chunk size")

        chunk_path = self._chunk_path(upload_id, index)
        chunk_path.parent.mkdir(parents=True, exist_ok=True)
        temporary = chunk_path.with_name(f".{chunk_path.name}.{uuid.uuid4().hex}.part")
        digest = hashlib.sha256()
        total = 0
        try:
            with temporary.open("xb") as output:
                async for data in source:
                    if not data:
                        continue
                    total += len(data)
                    if total > expected_size:
                        raise UploadValidationError(
                            f"Invalid chunk size: expected {expected_size} bytes"
                        )
                    digest.update(data)
                    output.write(data)
                output.flush()
                os.fsync(output.fileno())
            if total != expected_size:
                raise UploadValidationError(
                    f"Invalid chunk size: expected {expected_size} bytes, received {total}"
                )
            if digest.hexdigest() != expected_sha256:
                raise UploadValidationError("Chunk SHA256 mismatch")
            os.replace(temporary, chunk_path)
        finally:
            temporary.unlink(missing_ok=True)

    def uploaded_chunks(self, upload_id: str, chunk_count: int) -> list[int]:
        session_dir = self._session_dir(upload_id)
        if not session_dir.exists():
            return []
        uploaded: list[int] = []
        for path in session_dir.glob("*.chunk"):
            try:
                index = int(path.stem)
            except ValueError:
                continue
            if 0 <= index < chunk_count and path.is_file():
                uploaded.append(index)
        return sorted(set(uploaded))

    def assemble_pdf(
        self,
        upload_id: str,
        chunk_count: int,
        expected_size: int,
        destination: Path,
    ) -> str:
        uploaded = self.uploaded_chunks(upload_id, chunk_count)
        if uploaded != list(range(chunk_count)):
            missing = sorted(set(range(chunk_count)) - set(uploaded))
            raise UploadValidationError(f"Missing upload chunks: {missing[:10]}")

        destination.parent.mkdir(parents=True, exist_ok=True)
        digest = hashlib.sha256()
        total = 0
        first_bytes = b""
        tail = b""
        try:
            with destination.open("xb") as output:
                for index in range(chunk_count):
                    with self._chunk_path(upload_id, index).open("rb") as source:
                        while data := source.read(1024 * 1024):
                            if not first_bytes:
                                first_bytes = data[:8]
                            tail = (tail + data)[-1024:]
                            total += len(data)
                            digest.update(data)
                            output.write(data)
                output.flush()
                os.fsync(output.fileno())
            if total != expected_size:
                raise UploadValidationError(
                    f"Assembled file size mismatch: expected {expected_size}, received {total}"
                )
            if not first_bytes.startswith(b"%PDF"):
                raise UploadValidationError("Invalid PDF file (missing PDF header)")
            if b"%%EOF" not in tail:
                raise UploadValidationError("Invalid PDF file (missing %%EOF marker)")
            return digest.hexdigest()
        except Exception:
            destination.unlink(missing_ok=True)
            raise

    def acquire_lock(self, key: str) -> BinaryIO:
        if fcntl is None:
            raise RuntimeError("Cross-process upload locking is unavailable")
        lock_dir = self.root / "locks"
        lock_dir.mkdir(parents=True, exist_ok=True)
        lock_name = hashlib.sha256(key.encode("utf-8")).hexdigest()
        handle = (lock_dir / f"{lock_name}.lock").open("a+b")
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        return handle

    @staticmethod
    def release_lock(handle: BinaryIO) -> None:
        if fcntl is not None:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        handle.close()

    def cleanup_session(self, upload_id: str) -> None:
        session_dir = self._session_dir(upload_id)
        if session_dir.exists():
            shutil.rmtree(session_dir, ignore_errors=True)

