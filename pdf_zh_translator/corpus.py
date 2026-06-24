"""Academic terminology corpus for translation consistency.

Loads terminology mappings from corpus.json and provides them to the
translation prompt builder. Terms are matched case-insensitively and
the most specific match wins (longer terms first).
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Dict, List, Optional, Tuple

_CORPUS_PATH = Path(__file__).parent / "corpus.json"
_corpus: Optional[Dict[str, Dict[str, str]]] = None
_flat_terms: Optional[List[Tuple[str, str]]] = None


def _load_corpus() -> Dict[str, Dict[str, str]]:
    """Load the terminology corpus from disk (cached)."""
    global _corpus
    if _corpus is None:
        with _CORPUS_PATH.open("r", encoding="utf-8") as f:
            data = json.load(f)
        # Remove metadata key
        _corpus = {k: v for k, v in data.items() if not k.startswith("_")}
    return _corpus


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


def reload_corpus() -> None:
    """Force reload the corpus from disk (for testing or hot-reload)."""
    global _corpus, _flat_terms
    _corpus = None
    _flat_terms = None
