"""An engine revision must never attest incomplete or unavailable source bytes."""

from pathlib import Path

import pytest

from app.services import translation_recovery as recovery

SOURCE = b"def worker():\n    return 1\n"
EXPECTED_REVISION = "08cab1ad5067a407410d613dc4902c40"


@pytest.fixture
def revision_source(tmp_path, monkeypatch):
    source = tmp_path / "worker.py"
    source.write_bytes(SOURCE)
    monkeypatch.setattr(
        recovery, "__file__", str(tmp_path / "app/services/translation_recovery.py")
    )
    monkeypatch.setattr(recovery, "_REVISION_FILES", ("worker.py",))
    recovery.current_engine_revision.cache_clear()
    try:
        yield source
    finally:
        recovery.current_engine_revision.cache_clear()


def test_valid_source_preserves_revision(revision_source):
    assert recovery.current_engine_revision() == EXPECTED_REVISION


@pytest.mark.parametrize("returned", [b"", b"def worker():\n", b"x" * 80])
def test_incomplete_or_excess_read_is_rejected(revision_source, monkeypatch, returned):
    original_read = Path.read_bytes
    monkeypatch.setattr(
        Path, "read_bytes",
        lambda path: returned if path == revision_source else original_read(path),
    )
    assert revision_source.stat().st_size == len(SOURCE)
    with pytest.raises(RuntimeError, match="incomplete read"):
        recovery.current_engine_revision()


def test_empty_critical_source_is_rejected(revision_source):
    revision_source.write_bytes(b"")
    with pytest.raises(RuntimeError, match="empty source"):
        recovery.current_engine_revision()


@pytest.mark.parametrize("error_type", [FileNotFoundError, PermissionError])
def test_source_read_errors_cannot_become_a_revision(revision_source, monkeypatch, error_type):
    original_read = Path.read_bytes

    def unavailable(path):
        if path == revision_source:
            raise error_type("Synthetic source-read failure")
        return original_read(path)

    monkeypatch.setattr(Path, "read_bytes", unavailable)
    with pytest.raises(RuntimeError, match="source unavailable") as raised:
        recovery.current_engine_revision()
    assert isinstance(raised.value.__cause__, error_type)


def test_missing_critical_source_is_rejected(revision_source):
    revision_source.unlink()
    with pytest.raises(RuntimeError, match="source unavailable"):
        recovery.current_engine_revision()


def test_nonregular_source_is_rejected_before_reading(revision_source):
    revision_source.unlink()
    revision_source.mkdir()
    with pytest.raises(RuntimeError, match="not a regular file"):
        recovery.current_engine_revision()


def test_source_changed_during_read_is_rejected(revision_source, monkeypatch):
    original_read = Path.read_bytes

    def changed_during_read(path):
        value = original_read(path)
        if path == revision_source:
            path.write_bytes(value + b"# changed during reading\n")
        return value

    monkeypatch.setattr(Path, "read_bytes", changed_during_read)
    with pytest.raises(RuntimeError, match="changed during reading"):
        recovery.current_engine_revision()


def test_bad_read_is_not_cached_and_complete_read_can_recover(revision_source, monkeypatch):
    original_read = Path.read_bytes
    unavailable = True

    def read(path):
        if path == revision_source and unavailable:
            return b""
        return original_read(path)

    monkeypatch.setattr(Path, "read_bytes", read)
    with pytest.raises(RuntimeError, match="incomplete read"):
        recovery.current_engine_revision()
    assert recovery.current_engine_revision.cache_info().currsize == 0
    unavailable = False
    assert recovery.current_engine_revision() == EXPECTED_REVISION


def test_ui_only_api_changes_preserve_worker_revision(revision_source, monkeypatch):
    root = revision_source.parent
    api = root / "app/api/papers.py"
    api.parent.mkdir(parents=True)
    source = b"def _do_translate():\n    return 1\n\ndef library_route():\n    return 'old'\n"
    api.write_bytes(source)
    monkeypatch.setattr(recovery, "_REVISION_FILES", ("app/api/papers.py",))
    first = recovery.current_engine_revision()
    api.write_bytes(source.replace(b"'old'", b"'new UI route'"))
    recovery.current_engine_revision.cache_clear()
    assert recovery.current_engine_revision() == first
    api.write_bytes(source.replace(b"return 1", b"return 2"))
    recovery.current_engine_revision.cache_clear()
    assert recovery.current_engine_revision() != first


def test_worker_changes_update_revision(revision_source):
    first = recovery.current_engine_revision()
    revision_source.write_bytes(SOURCE.replace(b"return 1", b"return 2"))
    recovery.current_engine_revision.cache_clear()
    assert recovery.current_engine_revision() != first
