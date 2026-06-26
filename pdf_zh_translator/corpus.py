"""Academic terminology corpus for translation consistency.

Loads terminology mappings from corpus.json and provides them to the
translation prompt builder. Terms are matched case-insensitively and
the most specific match wins (longer terms first).
"""

from __future__ import annotations

import contextlib
import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Mapping, Optional, Tuple

_CORPUS_PATH = Path(__file__).parent / "corpus.json"
_CORPORA_DIR = Path(__file__).parent / "corpora"
_corpus: Optional[Dict[str, Dict[str, str]]] = None
_flat_terms: Optional[List[Tuple[str, str]]] = None
_TERM_CANDIDATE_RE = re.compile(
    r"\b(?:"
    r"[A-Z][A-Za-z]+(?:[- ][A-Z][A-Za-z]+){1,4}|"
    r"[A-Z]{2,}(?:[- ][A-Za-z0-9]+){0,4}|"
    r"[A-Za-z]+(?:-[A-Za-z]+){1,4}"
    r")\b"
)
_COMMON_CANDIDATE_WORDS = frozenset(
    "abstract introduction related work experiment experiments conclusion appendix "
    "figure table algorithm theorem proof this paper our method".split()
)


def _load_corpus() -> Dict[str, Dict[str, str]]:
    """Load the terminology corpus from disk (cached)."""
    global _corpus
    if _corpus is None:
        with _CORPUS_PATH.open("r", encoding="utf-8") as f:
            data = json.load(f)
        # Remove metadata key
        _corpus = {k: v for k, v in data.items() if not k.startswith("_")}
        for extra_path in sorted(_CORPORA_DIR.glob("*.json")):
            extra = _load_raw_corpus(extra_path)
            for field, terms in extra.items():
                if field.startswith("_") or not isinstance(terms, dict):
                    continue
                field_data = _corpus.setdefault(field, {})
                field_data.update({str(en): str(zh) for en, zh in terms.items()})
    return _corpus


def _load_raw_corpus(corpus_path: Path) -> dict:
    if not corpus_path.exists():
        return {"_metadata": {"version": "1.0.0"}}
    with corpus_path.open("r", encoding="utf-8") as f:
        data = json.load(f)
    if not isinstance(data, dict):
        raise ValueError("Corpus JSON must contain an object at the top level")
    return data


def _write_raw_corpus(corpus_path: Path, data: dict) -> None:
    corpus_path.parent.mkdir(parents=True, exist_ok=True)
    with corpus_path.open("w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
        f.write("\n")


def _get_flat_terms() -> List[Tuple[str, str]]:
    """Get all terms as a flat list sorted by length (longest first)."""
    global _flat_terms
    if _flat_terms is None:
        corpus = _load_corpus()
        terms = {}
        for field_data in corpus.values():
            for en, zh in field_data.items():
                terms[en.lower()] = zh
        # Sort by length descending so longer terms match first
        _flat_terms = sorted(terms.items(), key=lambda x: len(x[0]), reverse=True)
    return _flat_terms


def get_relevant_terms(texts: List[str], max_terms: int = 50) -> Dict[str, str]:
    """Find terminology terms that appear in the given texts.

    Returns a dict of english_term → chinese_translation for terms
    that are found in any of the input texts. Limited to max_terms
    to keep the prompt size manageable.
    """
    terms = _get_flat_terms()
    combined = " ".join(texts).lower()
    found: Dict[str, str] = {}

    for en, zh in terms:
        if len(found) >= max_terms:
            break
        # Use word boundary check for short terms to avoid false matches
        if len(en) <= 3:
            if re.search(r'\b' + re.escape(en) + r'\b', combined):
                found[en] = zh
        else:
            if en in combined:
                found[en] = zh

    return found


def build_terminology_prompt(terms: Dict[str, str]) -> str:
    """Build a terminology instruction block for the translation prompt."""
    if not terms:
        return ""

    lines = ["【术语表】以下术语必须使用标准译法："]
    for en, zh in sorted(terms.items()):
        lines.append(f"  {en} → {zh}")
    return "\n".join(lines)


def corpus_stats() -> Dict[str, int]:
    """Return term counts by field for diagnostics."""
    corpus = _load_corpus()
    stats = {field: len(terms) for field, terms in corpus.items()}
    stats["_total"] = sum(stats.values())
    return stats


def upsert_terms(
    field: str,
    terms: Mapping[str, str],
    *,
    source: str = "manual",
    corpus_path: Optional[Path] = None,
) -> int:
    """Add or update approved terminology in the corpus.

    Returns the number of entries that changed. The in-memory cache is
    invalidated when the project corpus is updated.
    """
    field_name = field.strip()
    if not field_name:
        raise ValueError("field must not be empty")

    cleaned_terms = {
        en.strip(): zh.strip()
        for en, zh in terms.items()
        if en and zh and en.strip() and zh.strip()
    }
    if not cleaned_terms:
        return 0

    target_path = corpus_path or _CORPUS_PATH
    data = _load_raw_corpus(target_path)
    field_data = data.setdefault(field_name, {})
    if not isinstance(field_data, dict):
        raise ValueError(f"Corpus field '{field_name}' must contain an object")

    changed = 0
    for english, chinese in sorted(cleaned_terms.items(), key=lambda item: item[0].lower()):
        if field_data.get(english) != chinese:
            field_data[english] = chinese
            changed += 1

    if changed:
        metadata = data.setdefault("_metadata", {})
        metadata["updated_at"] = _utc_now()
        metadata["last_source"] = source
        metadata["total_terms"] = _count_terms(data)
        _write_raw_corpus(target_path, data)
        if target_path.resolve() == _CORPUS_PATH.resolve():
            reload_corpus()
    return changed


def extract_candidate_terms(texts: List[str], max_terms: int = 100) -> List[str]:
    """Extract review candidates for later terminology curation.

    This deliberately records candidates instead of auto-adding translations;
    final Chinese terms still need a reviewed entry through ``upsert_terms``.
    """
    known = {_candidate_key(term) for term, _ in _get_flat_terms()}
    candidates: Dict[str, str] = {}
    for text in texts:
        for match in _TERM_CANDIDATE_RE.finditer(text):
            raw = match.group(0).strip()
            key = _candidate_key(raw)
            if key in candidates or key in known:
                continue
            if key in _COMMON_CANDIDATE_WORDS:
                continue
            if len(key) < 4 or key.isdigit():
                continue
            if not _looks_like_academic_candidate(raw):
                continue
            candidates[key] = raw
            if len(candidates) >= max_terms:
                return sorted(candidates.values(), key=str.lower)
    return sorted(candidates.values(), key=str.lower)


def record_candidate_terms(
    texts: List[str],
    output_path: Path,
    *,
    source: str,
    max_terms: int = 100,
) -> int:
    """Append new candidate terminology observations to a JSONL file."""
    terms = extract_candidate_terms(texts, max_terms=max_terms)
    if not terms:
        return 0

    existing: set[str] = set()
    if output_path.exists():
        with output_path.open("r", encoding="utf-8") as f:
            for line in f:
                with contextlib.suppress(json.JSONDecodeError):
                    record = json.loads(line)
                    term = ""
                    if isinstance(record, dict):
                        term = str(record.get("term", "")).strip().lower()
                    if term:
                        existing.add(_candidate_key(term))

    output_path.parent.mkdir(parents=True, exist_ok=True)
    observed_at = _utc_now()
    added = 0
    with output_path.open("a", encoding="utf-8") as f:
        for term in terms:
            key = _candidate_key(term)
            if key in existing:
                continue
            f.write(
                json.dumps(
                    {"term": term, "source": source, "observed_at": observed_at},
                    ensure_ascii=False,
                )
                + "\n"
            )
            existing.add(key)
            added += 1
    return added


def load_candidate_terms(candidate_path: Path) -> List[dict]:
    """Load and deduplicate terminology candidate observations."""
    grouped: Dict[str, dict] = {}
    if not candidate_path.exists():
        return []
    with candidate_path.open("r", encoding="utf-8") as f:
        for line in f:
            with contextlib.suppress(json.JSONDecodeError):
                record = json.loads(line)
                if not isinstance(record, dict):
                    continue
                term = str(record.get("term", "")).strip()
                if not term:
                    continue
                key = _candidate_key(term)
                item = grouped.setdefault(
                    key,
                    {
                        "term": term,
                        "sources": [],
                        "count": 0,
                        "approved": False,
                        "translation": "",
                    },
                )
                item["count"] += 1
                source = str(record.get("source", "")).strip()
                if source and source not in item["sources"]:
                    item["sources"].append(source)
    return sorted(grouped.values(), key=lambda item: (-item["count"], item["term"].lower()))


def write_candidate_review(candidate_path: Path, review_path: Path) -> int:
    """Write a deduplicated review JSON for human/agent approval."""
    candidates = load_candidate_terms(candidate_path)
    review_path.parent.mkdir(parents=True, exist_ok=True)
    review_path.write_text(
        json.dumps(
            {
                "_meta": {
                    "source": str(candidate_path),
                    "generated_at": _utc_now(),
                    "instructions": "Set approved=true and fill translation to promote terms.",
                },
                "candidates": candidates,
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    return len(candidates)


def promote_reviewed_terms(
    review_path: Path,
    *,
    field: str,
    corpus_path: Optional[Path] = None,
    source: str = "candidate-review",
) -> int:
    """Promote approved reviewed candidates to the official corpus."""
    data = json.loads(review_path.read_text(encoding="utf-8"))
    approved = {}
    for item in data.get("candidates", []):
        if not isinstance(item, dict) or not item.get("approved"):
            continue
        term = str(item.get("term", "")).strip()
        translation = str(item.get("translation", "")).strip()
        if term and translation:
            approved[term] = translation
    return upsert_terms(field, approved, source=source, corpus_path=corpus_path)


def release_corpus_version(
    *,
    version: str,
    corpus_path: Optional[Path] = None,
) -> dict:
    """Stamp corpus metadata for a reviewed release."""
    target_path = corpus_path or _CORPUS_PATH
    data = _load_raw_corpus(target_path)
    metadata = data.get("_meta")
    if not isinstance(metadata, dict):
        metadata = data.setdefault("_metadata", {})
    metadata["version"] = version
    metadata["released_at"] = _utc_now()
    metadata["total_terms"] = _count_terms(data)
    _write_raw_corpus(target_path, data)
    if target_path.resolve() == _CORPUS_PATH.resolve():
        reload_corpus()
    return metadata


def _looks_like_academic_candidate(term: str) -> bool:
    if any(char.isupper() for char in term):
        return True
    lower = term.lower()
    academic_markers = (
        "learning",
        "optimization",
        "alignment",
        "transformer",
        "retrieval",
        "diffusion",
        "reasoning",
        "representation",
        "preference",
        "context",
        "multimodal",
        "vision",
        "language",
    )
    return "-" in term and any(marker in lower for marker in academic_markers)


def _candidate_key(term: str) -> str:
    return " ".join(term.replace("-", " ").split()).lower()


def _count_terms(data: dict) -> int:
    return sum(
        len(value)
        for key, value in data.items()
        if not key.startswith("_") and isinstance(value, dict)
    )


def _utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def reload_corpus() -> None:
    """Force reload the corpus from disk (for testing or hot-reload)."""
    global _corpus, _flat_terms
    _corpus = None
    _flat_terms = None
