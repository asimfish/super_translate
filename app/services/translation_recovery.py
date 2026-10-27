"""Durable translation recovery policy helpers."""

from __future__ import annotations

import ast
import hashlib
import stat
from functools import lru_cache
from pathlib import Path

_REVISION_FILES = (
    'pdf_zh_translator/pdf_layout.py',
    'pdf_zh_translator/page_inspector.py',
    'app/api/papers.py',
    'app/services/layout_fix.py',
    'app/services/quality_agent.py',
    'app/services/translation_recovery.py',
    'app/services/translator.py',
    'pdf_zh_translator/translators.py',
    'pdf_zh_translator/journal_layout.py',
    'pdf_zh_translator/layout_profiles.py',
    'pdf_zh_translator/layout_families.py',
    'pdf_zh_translator/layout_families.json',
    'pdf_zh_translator/prompt_layout.py',
    'pdf_zh_translator/task_formula_layout.py',
)


def translation_api_fingerprint(source: bytes) -> bytes:
    """Hash worker code and its local dependencies, excluding library/UI routes.

    The subprocess entry point is named in a script string, so it is an explicit
    root as well as the parent worker and post-translation QA entry point.
    """
    try:
        tree = ast.parse(source)
    except (SyntaxError, ValueError):
        return source
    bindings = {}
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            bindings[node.name] = node
        elif isinstance(node, (ast.Assign, ast.AnnAssign)):
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            for target in targets:
                for name in ast.walk(target):
                    if isinstance(name, ast.Name):
                        bindings[name.id] = node
    roots = {"_run_translation", "_do_translate", "_run_post_translation_qa"}
    pending = roots & bindings.keys()
    if not pending:
        return source
    selected = {}
    referenced = set()
    while pending:
        name = pending.pop()
        if name in selected:
            continue
        node = bindings[name]
        selected[name] = ast.dump(node, include_attributes=False)
        names = {
            n.id for n in ast.walk(node) if isinstance(n, ast.Name) and isinstance(n.ctx, ast.Load)
        }
        referenced.update(names)
        pending.update((names & bindings.keys()) - selected.keys())
    imports = []
    for node in tree.body:
        if not isinstance(node, (ast.Import, ast.ImportFrom)):
            continue
        aliases = [a for a in node.names if (a.asname or a.name.split(".")[0]) in referenced]
        if not aliases:
            continue
        aliases = sorted(aliases, key=lambda a: (a.name, a.asname or ""))
        relevant = (
            ast.Import(names=aliases)
            if isinstance(node, ast.Import)
            else ast.ImportFrom(module=node.module, names=aliases, level=node.level)
        )
        imports.append(ast.dump(relevant, include_attributes=False))
    return (
        "\n".join(selected[name] for name in sorted(selected)) + "\n" + "\n".join(sorted(imports))
    ).encode("utf-8")


@lru_cache(maxsize=1)
def current_engine_revision() -> str:
    """Return a deterministic revision only for complete, stable source reads."""
    root = Path(__file__).resolve().parents[2]
    digest = hashlib.sha256()
    for relative_path in _REVISION_FILES:
        path = root / relative_path
        digest.update(relative_path.encode("utf-8"))
        try:
            before = path.stat()
            if not stat.S_ISREG(before.st_mode):
                raise RuntimeError(
                    f"Cannot verify engine source '{relative_path}': not a regular file"
                )
            source = path.read_bytes()
            after = path.stat()
        except OSError as error:
            raise RuntimeError(
                f"Cannot verify engine source '{relative_path}': source unavailable"
            ) from error
        before_identity = (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns)
        after_identity = (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns)
        if before_identity != after_identity:
            raise RuntimeError(
                f"Cannot verify engine source '{relative_path}': changed during reading"
            )
        if len(source) != before.st_size:
            raise RuntimeError(
                f"Cannot verify engine source '{relative_path}': incomplete read "
                f"({len(source)} of {before.st_size} bytes)"
            )
        if not source:
            raise RuntimeError(f"Cannot verify engine source '{relative_path}': empty source")
        digest.update(
            translation_api_fingerprint(source)
            if relative_path == "app/api/papers.py"
            else source
        )
    return digest.hexdigest()[:32]


def recovery_attempt_limit(value: object, *, default: int = 3) -> int:
    """Normalize a configured attempt budget to a conservative range."""
    attempts = value if type(value) is int else default
    return max(1, min(8, attempts))


def recovery_backoff_seconds(value: object, attempt: int) -> float:
    """Return bounded linear backoff before a follow-up attempt."""
    base = float(value) if type(value) in {int, float} else 2.0
    return max(0.0, min(30.0, base * max(0, attempt - 1)))
