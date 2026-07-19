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
_CJK_RE = re.compile(r"[\u4e00-\u9fff]")


def _has_cjk(text: str) -> bool:
    return bool(_CJK_RE.search(text))
TOP_CONFERENCE_FIELDS = (
    "neurips_icml_iclr",
    "neurips_foundations_theory",
    "neurips_rl_decision_making",
    "neurips_generative_multimodal",
    "icml_optimization_learning_theory",
    "icml_probabilistic_bayes",
    "iclr_representations_architectures",
    "cvpr_computer_vision",
    "cvpr_detection_segmentation",
    "cvpr_3d_geometry_reconstruction",
    "cvpr_video_embodied_vision",
    "acl_nlp",
    "acl_machine_translation_generation",
    "acl_information_extraction_retrieval",
    "acl_dialogue_safety_evaluation",
    "agents_alignment_safety",
    "paper_layout_and_reporting",
    "ml_systems_data_scaling",
    "ai_agents_tool_use",
)
FIELD_KEYWORDS: tuple[tuple[str, tuple[str, ...]], ...] = (
    (
        "cvpr_detection_segmentation",
        (
            "detection",
            "segmentation",
            "object",
            "instance",
            "mask",
            "bounding box",
            "recognition",
        ),
    ),
    (
        "cvpr_3d_geometry_reconstruction",
        (
            "3d",
            "geometry",
            "reconstruction",
            "nerf",
            "point cloud",
            "pose",
            "depth",
            "surface",
        ),
    ),
    (
        "cvpr_video_embodied_vision",
        (
            "video",
            "tracking",
            "embodied",
            "action",
            "temporal",
            "robot",
            "scene",
        ),
    ),
    (
        "acl_machine_translation_generation",
        (
            "translation",
            "generation",
            "summarization",
            "decoding",
            "preference",
            "faithfulness",
        ),
    ),
    (
        "acl_information_extraction_retrieval",
        (
            "retrieval",
            "reranking",
            "extraction",
            "entity",
            "question answering",
            "knowledge graph",
        ),
    ),
    (
        "acl_dialogue_safety_evaluation",
        (
            "dialogue",
            "alignment",
            "safety",
            "toxicity",
            "evaluation",
            "instruction",
        ),
    ),
    (
        "neurips_rl_decision_making",
        (
            "reinforcement",
            "policy",
            "reward",
            "offline rl",
            "bandit",
            "decision",
        ),
    ),
    (
        "neurips_generative_multimodal",
        (
            "diffusion",
            "multimodal",
            "vision-language",
            "generative",
            "latent",
        ),
    ),
    (
        "icml_optimization_learning_theory",
        (
            "optimization",
            "generalization",
            "gradient",
            "convex",
            "learning theory",
            "convergence",
        ),
    ),
    (
        "icml_probabilistic_bayes",
        (
            "bayes",
            "probabilistic",
            "variational",
            "causal",
            "uncertainty",
            "posterior",
        ),
    ),
    (
        "iclr_representations_architectures",
        (
            "representation",
            "transformer",
            "attention",
            "architecture",
            "normalization",
            "adapter",
        ),
    ),
    (
        "ml_systems_data_scaling",
        (
            "serving",
            "throughput",
            "latency",
            "scaling",
            "distributed",
            "checkpoint",
            "pipeline",
        ),
    ),
    (
        "ai_agents_tool_use",
        (
            "agent",
            "tool",
            "planning",
            "workflow",
            "function calling",
            "environment",
        ),
    ),
    (
        "paper_layout_and_reporting",
        (
            "ablation",
            "appendix",
            "benchmark",
            "reproducibility",
            "statistical",
            "confidence interval",
        ),
    ),
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


_term_patterns: Dict[str, "re.Pattern[str]"] = {}


def _term_search_pattern(en: str) -> "re.Pattern[str]":
    """Whole-word pattern for a corpus term, tolerating a simple plural.

    Substring matching produced false hits such as "ranking"→"rank",
    "normalized"→"norm", and "interactive learning"→"active learning",
    which polluted both translation prompts and terminology advisories.
    """
    pattern = _term_patterns.get(en)
    if pattern is None:
        pattern = re.compile(r"\b" + re.escape(en) + r"(?:e?s)?\b")
        _term_patterns[en] = pattern
    return pattern


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
        if _term_search_pattern(en).search(combined):
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


def corpus_lint(corpus: Optional[Dict[str, Dict[str, str]]] = None) -> dict:
    """Lint the merged corpus for quality problems.

    The flattened lookup silently lets the last field win when the same English
    term maps to different Chinese in multiple fields, which is how inconsistent
    terminology sneaks in. This surfaces those conflicts plus empty fields,
    blank values, and entries left untranslated (Chinese identical to English).

    Accepts an explicit ``corpus`` mapping for testing; defaults to the live one.
    """
    if corpus is None:
        corpus = _load_corpus()

    by_key: Dict[str, List[Tuple[str, str, str]]] = {}
    empty_fields: List[str] = []
    empty_values: List[dict] = []
    untranslated: List[dict] = []
    total = 0

    for field, terms in corpus.items():
        if not terms:
            empty_fields.append(field)
            continue
        for en, zh in terms.items():
            total += 1
            en_str = str(en).strip()
            zh_str = str(zh).strip()
            if not en_str or not zh_str:
                empty_values.append({"field": field, "en": en, "zh": zh})
                continue
            # Iteration order matches _load_corpus, so the last entry is the one
            # that wins in the flattened lookup.
            by_key.setdefault(en_str.lower(), []).append((field, en_str, zh_str))
            if zh_str.lower() == en_str.lower():
                untranslated.append({"field": field, "en": en_str, "zh": zh_str})

    conflicts: List[dict] = []
    for key, entries in by_key.items():
        if len({zh for _, _, zh in entries}) > 1:
            conflicts.append(
                {
                    "term": key,
                    "effective": entries[-1][2],
                    "translations": [{"field": f, "en": e, "zh": z} for f, e, z in entries],
                }
            )

    conflicts.sort(key=lambda c: c["term"])
    return {
        "total_terms": total,
        "conflict_count": len(conflicts),
        "conflicts": conflicts,
        "empty_field_count": len(empty_fields),
        "empty_fields": sorted(empty_fields),
        "empty_value_count": len(empty_values),
        "empty_values": empty_values,
        "untranslated_count": len(untranslated),
        "untranslated": untranslated,
        # "untranslated" (Chinese identical to English) is advisory: model names
        # like "Toolformer" are legitimately kept as-is, so it doesn't fail clean.
        "clean": not (conflicts or empty_fields or empty_values),
    }


def audit_terminology_usage(
    source_texts: List[str],
    translated_texts: List[str],
    *,
    max_violations: int = 50,
) -> List[dict]:
    """Check that corpus terms in the source were rendered with the standard zh.

    Terminology is a soft prompt constraint, so the LLM can drift to a synonym.
    For each corpus term present in the source whose standard Chinese is absent
    from the translation, report a (best-effort, advisory) violation.
    """
    relevant = get_relevant_terms(source_texts, max_terms=200)
    if not relevant:
        return []
    translated_blob = "\n".join(translated_texts)
    violations: List[dict] = []
    # Multi-word/hyphenated entries are real terminology; single generic
    # words are closer to style suggestions, so surface them last.
    ordered = sorted(
        relevant.items(),
        key=lambda item: (" " not in item[0] and "-" not in item[0], item[0]),
    )
    for en, zh in ordered:
        if len(violations) >= max_violations:
            break
        zh_str = str(zh).strip()
        if zh_str and _has_cjk(zh_str) and zh_str not in translated_blob:
            violations.append({"en": en, "expected_zh": zh_str})
    return violations


def corpus_health(candidate_path: Optional[Path] = None) -> dict:
    """Return corpus coverage and maintenance signals for AI conference terms."""
    stats = corpus_stats()
    root_data = _load_raw_corpus(_CORPUS_PATH)
    metadata = root_data.get("_metadata") or root_data.get("_meta") or {}
    candidates = load_candidate_terms(candidate_path) if candidate_path else []
    top_counts = {field: stats.get(field, 0) for field in TOP_CONFERENCE_FIELDS}
    return {
        "total_terms": stats.get("_total", 0),
        "top_conference_terms": sum(top_counts.values()),
        "top_conference_fields": top_counts,
        "missing_top_conference_fields": [
            field for field, count in top_counts.items() if count <= 0
        ],
        "candidate_terms": len(candidates),
        "extra_corpora": sorted(path.name for path in _CORPORA_DIR.glob("*.json")),
        "metadata": metadata,
    }


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


def audit_candidate_terms(candidate_path: Path) -> List[dict]:
    """Return deduplicated terminology candidates with review hints."""
    known = _known_term_lookup()
    audited: List[dict] = []
    for item in load_candidate_terms(candidate_path):
        term = str(item.get("term", "")).strip()
        key = _candidate_key(term)
        known_entry = known.get(key)
        suggested_field, confidence, reason = suggest_candidate_field(term)
        enriched = dict(item)
        enriched.setdefault("approved", False)
        enriched.setdefault("translation", "")
        enriched["suggested_field"] = suggested_field
        enriched["suggested_confidence"] = confidence
        enriched["classification_reason"] = reason
        enriched["field"] = enriched.get("field") or suggested_field
        if known_entry:
            field, canonical_term, translation = known_entry
            enriched["status"] = "known"
            enriched["known_field"] = field
            enriched["known_term"] = canonical_term
            enriched["known_translation"] = translation
            enriched["translation"] = enriched.get("translation") or translation
            enriched["review_notes"] = (
                "Already covered by corpus; do not promote unless changing译法."
            )
        else:
            enriched["status"] = "needs_translation"
            enriched["review_notes"] = (
                "Set approved=true after confirming field and Chinese translation."
            )
        audited.append(enriched)
    return audited


def suggest_candidate_field(term: str) -> tuple[str, float, str]:
    """Suggest the most likely top-conference field for a candidate term."""
    key = _candidate_key(term)
    best_field = "neurips_icml_iclr"
    best_hits: list[str] = []
    for field, keywords in FIELD_KEYWORDS:
        hits = [keyword for keyword in keywords if keyword in key]
        if len(hits) > len(best_hits):
            best_field = field
            best_hits = hits
    if best_hits:
        confidence = min(0.95, 0.52 + len(best_hits) * 0.12)
        return best_field, confidence, "matched keywords: " + ", ".join(best_hits[:4])
    return best_field, 0.35, "fallback to broad NeurIPS/ICML/ICLR terminology"


def write_candidate_review(candidate_path: Path, review_path: Path) -> int:
    """Write a deduplicated review JSON for human/agent approval."""
    candidates = audit_candidate_terms(candidate_path)
    review_path.parent.mkdir(parents=True, exist_ok=True)
    review_path.write_text(
        json.dumps(
            {
                "_meta": {
                    "source": str(candidate_path),
                    "generated_at": _utc_now(),
                    "instructions": (
                        "Review suggested_field/field, fill translation, then set "
                        "approved=true. Use corpus-promote FIELD or FIELD=auto."
                    ),
                    "candidate_count": len(candidates),
                    "known_count": sum(1 for item in candidates if item["status"] == "known"),
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
    approved_by_field: Dict[str, Dict[str, str]] = {}
    for item in data.get("candidates", []):
        if not isinstance(item, dict) or not item.get("approved"):
            continue
        term = str(item.get("term", "")).strip()
        translation = str(item.get("translation", "")).strip()
        if term and translation:
            target_field = field
            if field == "auto":
                target_field = str(item.get("field") or item.get("suggested_field") or "").strip()
            if not target_field:
                raise ValueError("review item is missing field/suggested_field for auto promote")
            approved_by_field.setdefault(target_field, {})[term] = translation

    changed = 0
    for target_field, approved in sorted(approved_by_field.items()):
        changed += upsert_terms(
            target_field,
            approved,
            source=source,
            corpus_path=corpus_path,
        )
    return changed


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


def _known_term_lookup() -> Dict[str, Tuple[str, str, str]]:
    known: Dict[str, Tuple[str, str, str]] = {}
    for field, terms in _load_corpus().items():
        for english, chinese in terms.items():
            known[_candidate_key(english)] = (field, english, chinese)
    return known


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
