"""Document management changes must not restart parked translations."""

from app.services.translation_recovery import translation_api_fingerprint


def test_library_changes_and_unused_imports_do_not_change_engine_fingerprint():
    source = b"""
from renderer import render, unused
def _run_translation(path):
    return _do_translate(path)
def _do_translate(path):
    return render(path)
def list_papers():
    return ['old title']
"""
    changed = source.replace(b"unused", b"unused, document_info")
    changed = changed.replace(b"['old title']", b"['organized title']")
    changed += b"\ndef organize_library():\n    return 'new UI'\n"
    assert translation_api_fingerprint(source) == translation_api_fingerprint(changed)


def test_worker_dependency_constant_and_import_changes_are_detected():
    source = b"""
from renderer import render
QUALITY = 2
def _run_translation(path):
    return helper(path)
def helper(path):
    return render(path, QUALITY)
"""
    original = translation_api_fingerprint(source)
    for changed in [
        source.replace(b"QUALITY = 2", b"QUALITY = 3"),
        source.replace(b"renderer", b"new_renderer"),
        source.replace(b"return render(path, QUALITY)", b"return render(path, 5)"),
    ]:
        assert original != translation_api_fingerprint(changed)


def test_subprocess_and_qa_entry_points_are_included_even_when_called_by_string():
    source = b"""
def _run_translation():
    return 'subprocess imports _do_translate'
def _do_translate():
    return 'original translation'
def _run_post_translation_qa():
    return True
"""
    original = translation_api_fingerprint(source)
    assert original != translation_api_fingerprint(
        source.replace(b"original translation", b"fixed translation")
    )
    assert original != translation_api_fingerprint(source.replace(b"return True", b"return False"))


def test_invalid_or_unrecognized_api_falls_back_to_exact_source():
    for source in [b"def broken(", b"def unknown():\n    return 1\n"]:
        assert translation_api_fingerprint(source) == source
