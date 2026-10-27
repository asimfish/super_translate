"""A repaired prompt, adapter or journal rule must unlock parked recovery."""

import importlib.util
from pathlib import Path

import pytest

from app.services import translation_recovery


@pytest.mark.parametrize("relative", [
    "app/services/translator.py",
    "pdf_zh_translator/translators.py",
    "pdf_zh_translator/journal_layout.py",
    "pdf_zh_translator/layout_profiles.py",
    "pdf_zh_translator/layout_families.py",
    "pdf_zh_translator/layout_families.json",
])
def test_engine_revision_tracks_rendering_dependencies(tmp_path, relative):
    module_path = tmp_path / "app/services/translation_recovery.py"
    module_path.parent.mkdir(parents=True)
    module_path.write_bytes(Path(translation_recovery.__file__).read_bytes())
    dependency = tmp_path / relative
    dependency.parent.mkdir(parents=True, exist_ok=True)
    dependency.write_text("before repair")
    spec = importlib.util.spec_from_file_location("isolated_recovery", module_path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    # Exercise a healthy, complete source tree. Missing production inputs must
    # fail verification rather than become part of an apparently valid revision.
    for relative_path in module._REVISION_FILES:
        source = tmp_path / relative_path
        if not source.exists():
            source.parent.mkdir(parents=True, exist_ok=True)
            source.write_bytes(b"# Complete dependency fixture.\n")
    before = module.current_engine_revision()
    dependency.write_text("after repair")
    module.current_engine_revision.cache_clear()
    assert module.current_engine_revision() != before, (
        f"Recovery ignored a result-changing repair: {relative}"
    )
