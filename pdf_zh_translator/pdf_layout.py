"""PDF layout extraction and in-place text replacement.

Designed for academic PDFs (including NeurIPS-style review drafts):

* Margin line numbers (tiny pure-digit spans) are stripped from text and
  erased from the page so they never pollute translations.
* Physical lines/blocks belonging to one paragraph are merged before
  translation, so the translator sees whole paragraphs and the inserted
  Chinese text flows naturally instead of one cramped box per line.
* Display-equation lines are detected, kept out of translation units and
  never redacted, so the original formula typesetting survives verbatim.
  Paragraph text before/after an equation becomes separate segments that
  flow around it.
* Inserted Chinese text is typeset by a dedicated CJK engine: justified
  lines, kinsoku (禁则) line-break rules, CJK/Latin spacing, centred
  headings/title detection, and bold heading rendering (黑体) with a
  Songti body font and glyph-level fallback.
"""

from __future__ import annotations

import re
import statistics
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

Color = Tuple[float, float, float]
BBox = Tuple[float, float, float, float]

# Prompt-injection lines hidden in source PDFs (e.g. instructions aimed at
# LLM reviewers). Matching lines are stripped from translation input and
# erased from the page like gutter line numbers.
INJECTION_PATTERNS = (
    re.compile(r"in your output[^.\n]{0,40}must", re.IGNORECASE),
    re.compile(r"you must include all of the following", re.IGNORECASE),
    re.compile(
        r"addresses the central challenge.{0,80}claims of the paper",
        re.IGNORECASE | re.DOTALL,
    ),
    re.compile(r"ignore (all|previous|the above) (instructions|prompts)", re.IGNORECASE),
    # Canary fragments alone: injection lines can wrap across PDF lines, so each
    # half must trigger independently (root cause of the v1 translation residue).
    re.compile(r"overall,?\s*i find this submission", re.IGNORECASE),
    re.compile(r"claims of the paper[\"\u201c\u201d']?\s*(and|和|”和“)", re.IGNORECASE),
    re.compile(r"[\"\u201c\u201d']\s*this work addresses the central challenge", re.IGNORECASE),
)

# Spans no larger than this are gutter line numbers when purely numeric.
LINE_NUMBER_MAX_SIZE = 6.5
# Pure-digit spans smaller than this fraction of the line's main size are dropped.
LINE_NUMBER_SIZE_RATIO = 0.72
# Horizontal air (PDF points) a digit span needs around it to count as a
# gutter line number; closer means it is a formula sub/superscript.
LINE_NUMBER_NEIGHBOR_GAP = 3.0
MARGIN_LINE_NUMBER_MAX_SIZE = 7.5
MARGIN_LINE_NUMBER_EDGE_RATIO = 0.08
# Vertical gap (in units of font size) below which two blocks are one paragraph.
PARAGRAPH_GAP_FACTOR = 0.65
# Font size difference above which blocks are never merged.
PARAGRAPH_SIZE_TOLERANCE = 1.5
# Minimum horizontal overlap ratio (of the narrower block) required to merge.
PARAGRAPH_MIN_X_OVERLAP = 0.5
# Graphic/table regions are kept untouched in conservative translation mode.
GRAPHIC_REGION_PADDING = 12.0
GRAPHIC_REGION_CLUSTER_GAP = 8.0
GRAPHIC_REGION_MIN_AREA = 250.0
GRAPHIC_REGION_MIN_SIDE = 8.0
GRAPHIC_BLOCK_OVERLAP_RATIO = 0.20
MATH_HEAVY_RATIO = 0.18
MATH_SHORT_BLOCK_RATIO = 0.05
EQUATION_TABLE_REDACT_GAP = 1.2

MATH_SYMBOLS = set(
    "=+\u2212\u00b1\u00d7\u00f7*/^_|\\<>~\u221e\u221a\u2202\u2207\u2211\u220f\u222b\u2208\u2209\u2282\u2286\u2283\u2287\u222a\u2229\u2227\u2228\u00ac\u2200\u2203\u2248\u2243\u2245\u2260\u2264\u2265\u226a\u226b\u221d\u2192\u2190\u2194\u21d2\u21d0\u21d4\u27e8\u27e9\u2032\u2033\u22a4\u22a5\u2225\u2295\u2297\u2299"
)  # noqa: E501

# --- Structure-aware classification ------------------------------------------
# Caption patterns: "Figure 1", "Fig. 2:", "Table III.", "图1", "表2".
# Plain prose references such as "Figure 5 summarizes ..." must stay body text.
_CAPTION_REFERENCE_VERBS = (
    r"(?:shows?|summari[sz]es|illustrates?|presents?|reports?|compares?|lists?|"
    r"contains?|provides?|depicts?|demonstrates?|highlights?|describes?|visuali[sz]es?)"
)
_CAPTION_RE = re.compile(
    rf"^(?:(?:Figure|Fig\.|Table)\s*(?:\d+|[IVXLCDM]+)"
    rf"(?:\s*[:.\-\u2013]\s*|\s+(?!{_CAPTION_REFERENCE_VERBS}\b))|(?:图|表)\s*\d)",
    re.IGNORECASE,
)
_ENGLISH_CAPTION_PREFIX_RE = re.compile(
    rf"^(?:Figure|Fig\.|Table)\s*(?:\d+|[IVXLCDM]+)"
    rf"(?:\s*[:.\-\u2013]\s*|\s+(?!{_CAPTION_REFERENCE_VERBS}\b))",
    re.IGNORECASE,
)
_FORMULA_EXPLANATION_RE = re.compile(
    r"^(?:where|with|here|for all|such that|subject to)\b",
    re.IGNORECASE,
)
_ACADEMIC_BOX_PROSE_RE = re.compile(
    r"^(?:Theorem|Lemma|Assumption|Definition|Proposition|Corollary|Remark|Proof)\b",
    re.IGNORECASE,
)
_ENUMERATED_ACADEMIC_BOX_RE = re.compile(r"^\(?[ivx]{1,4}\)\s+[A-Z]", re.IGNORECASE)
# Heading patterns: "1 Introduction", "2.1 Background", "A. Appendix"
_HEADING_RE = re.compile(
    r"^(?:\d+(?:\.\d+)*\.?\s|[A-Z]\.\s)",
)
_NUMBERED_HEADING_LINE_RE = re.compile(r"^\d+(?:\.\d+)*\.?\s+\S")
_APPENDIX_STYLE_HEADING_LINE_RE = re.compile(r"^[A-Z]\.\s+\S")
_STRUCTURE_HEADING_WORDS = {
    "abstract",
    "introduction",
    "background",
    "method",
    "methods",
    "methodology",
    "experiments",
    "experiment",
    "results",
    "discussion",
    "conclusion",
    "conclusions",
    "limitations",
    "references",
    "bibliography",
    "acknowledgments",
    "acknowledgements",
    "appendix",
    "impact statement",
    "keywords",
}
# Section number pattern for heading detection
_SECTION_NUM_RE = re.compile(r"^\d+(?:\.\d+)*\.?\s")
# Footer patterns: page numbers, headers
_FOOTER_MAX_Y_OFFSET = 50.0  # from bottom of page
_FOOTER_PAGE_NUM_RE = re.compile(r"^\d{1,3}$")
# Figure label max length (axis labels, short annotations)
_FIGURE_LABEL_MAX_LEN = 15
_DIAGRAM_HEAD_LABEL_WORDS = r"(?:Object|Skill|Depth)"
_DIAGRAM_MEMORY_LABEL_WORDS = (
    r"(?:Event-Boundary|Short-Term|Long-Term|Sliding Window|Full-KV|"
    r"Gist|Persistent|Hybrid)"
)
# Image zone extension to include nearby captions
_IMAGE_ZONE_CAPTION_GAP = 20.0  # points below image to look for captions
_CAPTION_EXTRA_HEIGHT = 36.0
_CAPTION_MIN_FONT_SIZE = 3.8
_CAPTION_TIGHT_LEADING = 1.02
_ABSOLUTE_MIN_FONT_SIZE = 2.8
# Column detection
_COLUMN_CLUSTER_GAP = 150.0  # minimum x0 separation for two-column layout

# --- Inline math protection -------------------------------------------------
# Spans set in math fonts (and superscripts / inline math tokens) are wrapped
# in sentinels at extraction time, converted to ⟦n⟧ placeholders before
# translation, and restored verbatim afterwards so the translator never
# touches formula content.
SENTINEL_OPEN = "\ue000"
SENTINEL_CLOSE = "\ue001"
MATH_FONT_RE = re.compile(
    r"(CMMI|CMSY|CMEX|MSAM|MSBM|rsfs|eufm|esint|wasy|stmary|Symbol|Math|STIX)",
    re.IGNORECASE,
)
PLACEHOLDER_RE = re.compile(r"\u27e6\s*(\d+)\s*\u27e7")
# One or more sentinel groups, optionally space-separated; must not consume
# the whitespace after the final group.
SENTINEL_RUN_RE = re.compile(
    "{o}[^{o}{c}]*{c}(?:\\s?{o}[^{o}{c}]*{c})*".format(o=SENTINEL_OPEN, c=SENTINEL_CLOSE)
)
CITATION_RE = re.compile(r"\[\d+(?:\s*[,\u2013-]\s*\d+)*\]")
URL_RE = re.compile(r"(?:https?://|www\.)\S+")
URL_TRAILING_PUNCT = ").,;:!?]\u3002\uff0c\uff1b"
# Characters that mark a whitespace-delimited token as inline math.
# U+27E6/27E7 (the placeholder brackets) are deliberately excluded.
MATH_TRIGGER = (
    "=^_\\\\|\u00b1\u00d7\u00f7\u221e\u221a\u2202\u2207\u2211\u220f\u222b"
    "\u2208\u2209\u2282\u2286\u2283\u2287\u222a\u2229\u2227\u2228\u00ac\u2200\u2203"
    "\u2248\u2243\u2245\u2260\u2264\u2265\u226a\u226b\u221d\u2212"
    "\u0370-\u03ff\u1f00-\u1fff"  # Greek
    "\u2070-\u209f"  # unicode super/subscripts
    "\u2190-\u22ff\u27c0-\u27e5\u27e8-\u27ef\u2a00-\u2aff"  # arrows + math operators
    "\U0001d400-\U0001d7ff"  # mathematical alphanumerics
)
MATH_TOKEN_RE = re.compile(r"\S*[%s]\S*" % MATH_TRIGGER)
SUPERSCRIPT_MAP = str.maketrans(
    "0123456789+-=()n",
    "\u2070\u00b9\u00b2\u00b3\u2074\u2075\u2076\u2077\u2078\u2079\u207a\u207b\u207c\u207d\u207e\u207f",
)  # noqa: E501

# Bold bit in PyMuPDF span flags.
FLAG_BOLD = 16

# Sentinel-content ratio above which a physical line is a display equation.
EQUATION_LINE_MATH_RATIO = 0.55
# Bold-character ratio above which a block renders in the heading font.
BLOCK_BOLD_RATIO = 0.6
EQUATION_NUMBER_RE = re.compile(r"\(?\d{1,3}(\.\d+)?\)")

# --- CJK typesetting rules ---------------------------------------------------
# 行首禁则: characters that must never start a line.
NO_LINE_START = set("。．，、；：？！）〕］｝〉》」』】’”…—‰％℃·~,.;:?!)]}%")
# 行尾禁则: characters that must never end a line.
NO_LINE_END = set("（〔［｛〈《「『【‘“([{")
# Default leading (line advance / font size) for inserted Chinese text.
# 1.5 gives comfortable reading for CJK body text.
DEFAULT_LEADING = 1.5
# Justification is skipped when per-gap stretch would exceed this many ems.
MAX_JUSTIFY_GAP_EM = 0.55

FONT_FILE_CANDIDATES = (
    "~/Library/Fonts/NotoSansCJKsc-Regular.otf",
    "~/Library/Fonts/NotoSansSC-Regular.otf",
    "~/Library/Fonts/NotoSansSC-Regular.ttf",
    "~/Library/Fonts/SourceHanSansSC-Regular.otf",
    "/Library/Fonts/Arial Unicode.ttf",
    "/System/Library/Fonts/Supplemental/Arial Unicode.ttf",
    "/usr/share/fonts/opentype/noto/NotoSansCJKsc-Regular.otf",
    "/usr/share/fonts/noto-cjk/NotoSansCJK-Regular.otf",
)

# Repo-local font faces extracted from the system TTCs (see ensure_font_pack).
FONTS_DIR = Path(__file__).resolve().parent.parent / "data" / "fonts"
BODY_FONT_FILE = FONTS_DIR / "SongtiSC-Regular.ttf"
BOLD_FONT_FILE = FONTS_DIR / "HiraginoSansGB-W6.ttf"
FALLBACK_FONT_CANDIDATES = (
    "/System/Library/Fonts/Supplemental/Arial Unicode.ttf",
    "/Library/Fonts/Arial Unicode.ttf",
)
TTC_FACE_SOURCES = (
    # (destination, ttc path, face name prefix)
    (BODY_FONT_FILE, "/System/Library/Fonts/Supplemental/Songti.ttc", "Songti SC Regular"),
    (BOLD_FONT_FILE, "/System/Library/Fonts/Hiragino Sans GB.ttc", "Hiragino Sans GB W6"),
)


@dataclass
class TextBlock:
    page_index: int
    bbox: BBox
    text: str
    font_size: float
    color: Color
    bold: bool = False
    starts_bold: bool = False
    source_lines: int = 1
    # Table cells: render on one line, anchored at the original x, never
    # merged into paragraphs and never centred.
    nowrap: bool = False
    no_merge: bool = False
    # Normally the insertion bbox is also the redaction bbox. Formula-tail
    # prose such as "where sigma is ..." can be inserted into the following
    # prose line while only redacting the English prose spans, leaving the
    # adjacent formula untouched.
    redact_bboxes: Optional[List[BBox]] = None
    # Structure-aware classification
    # title, heading, caption, equation, table, algorithm, bibliography, footer, figure_label
    block_type: str = "body"
    should_translate: bool = True
    preserve_position: bool = False  # Keep original bbox exactly (captions, figure labels)


@dataclass
class TranslationReport:
    input_pdf: Path
    output_pdf: Path
    page_count: int
    translated_blocks: int
    skipped_blocks: int
    warnings: List[str]


@dataclass(frozen=True)
class TranslationIssue:
    """Structured post-translation QA issue."""

    page: int
    code: str
    message: str
    severity: str = "warning"


@dataclass
class FontPack:
    """Fonts used by the CJK typesetting engine.

    ``regular``/``bold``/``fallback`` are fitz.Font objects for measurement;
    the *_file paths are registered on each page under fixed aliases.
    """

    regular: object
    regular_file: Path
    bold: object
    bold_file: Path
    fallback: Optional[object] = None
    fallback_file: Optional[Path] = None
    regular_alias: str = "zhbody"
    bold_alias: str = "zhbold"
    fallback_alias: str = "zhfall"

    def fonts_for(self, bold: bool) -> List[Tuple[object, str]]:
        """Measurement font + alias, in fallback order."""
        primary = (self.bold, self.bold_alias) if bold else (self.regular, self.regular_alias)
        chain = [primary]
        if self.fallback is not None:
            chain.append((self.fallback, self.fallback_alias))
        # Last resort: the other weight often covers extra glyphs.
        chain.append((self.regular, self.regular_alias) if bold else (self.bold, self.bold_alias))
        return chain


def translate_pdf(
    input_pdf: Path,
    output_pdf: Path,
    translator: object,
    font_name: str = "china-s",
    font_file: Optional[Path] = None,
    min_font_size: float = 5.0,
    font_scale: float = 0.92,
    margin: float = 0.8,
    preserve_graphics_text: bool = False,
    skip_overflow: bool = False,
) -> TranslationReport:
    try:
        import fitz
    except ImportError as exc:
        raise RuntimeError("PyMuPDF is required. Install with: pip install -e .") from exc

    warnings: List[str] = []
    font_pack = build_font_pack(font_file, warnings)

    document = fitz.open(str(input_pdf))
    try:
        from .layout_profiles import detect_layout_profile

        profile = detect_layout_profile(document)
        warnings.append(
            "Detected layout profile: %s (confidence %.2f, %d column%s)"
            % (
                profile.name,
                profile.confidence,
                profile.columns,
                "" if profile.columns == 1 else "s",
            )
        )
    except Exception:
        pass
    units, gutter_rects, skipped = prepare_translation_units(
        document,
        preserve_graphics_text=preserve_graphics_text,
    )
    warnings.extend(fragmented_prose_warnings_from_units(units))

    if not units:
        warnings.append("No extractable English text was found. Scanned PDFs need OCR.")
        page_count = document.page_count
        document.save(str(output_pdf), garbage=4, deflate=True)
        document.close()
        return TranslationReport(input_pdf, output_pdf, page_count, 0, skipped, warnings)

    protected_sources = [protected for _, protected, _ in units]
    block_types = [block.block_type for block, _, _ in units]

    # Set block types for context-aware translation prompts
    if hasattr(translator, "block_types"):
        translator.block_types = block_types
    translations = list(translator.translate_batch(protected_sources))
    if len(translations) != len(units):
        warnings.append(
            "Translator returned %d block(s) for %d source block(s)"
            % (len(translations), len(units))
        )
        if len(translations) < len(units):
            translations.extend(protected_sources[len(translations) :])
        else:
            translations = translations[: len(units)]

    cleaned_results = [
        _restore_unit_translation(translated_text, mapping)
        for (_, _, mapping), translated_text in zip(units, translations)
    ]
    retry_indexes = [
        index
        for index, ((block, _, _), (translated_text, _)) in enumerate(zip(units, cleaned_results))
        if _translated_block_still_english(block, translated_text)
    ]
    if retry_indexes:
        if hasattr(translator, "block_types"):
            translator.block_types = [units[index][0].block_type for index in retry_indexes]
        retry_outputs = list(
            translator.translate_batch([protected_sources[index] for index in retry_indexes])
        )
        for index, retry_text in zip(retry_indexes, retry_outputs):
            retry_cleaned, retry_missing = _restore_unit_translation(retry_text, units[index][2])
            if not _translated_block_still_english(units[index][0], retry_cleaned):
                cleaned_results[index] = (retry_cleaned, retry_missing)
        if hasattr(translator, "block_types"):
            translator.block_types = block_types

    by_page: Dict[int, List[Tuple[TextBlock, str]]] = {}
    for (block, _, _), (translated_text, missing) in zip(units, cleaned_results):
        if missing:
            warnings.append(
                "Page %d: translator dropped %d placeholder(s); fragments appended at block end"
                % (block.page_index + 1, len(missing))
            )
        if _translated_block_still_english(block, translated_text):
            warnings.append(
                "Page %d: translated %s block still looks like English after retry"
                % (block.page_index + 1, block.block_type)
            )
        by_page.setdefault(block.page_index, []).append((block, translated_text))

    for page_index in range(document.page_count):
        candidate_items = by_page.get(page_index, [])
        page_gutter = gutter_rects.get(page_index, [])
        if not candidate_items and not page_gutter:
            continue
        page = document[page_index]
        page_width = page.rect.width
        centered_flags = detect_centered_blocks(
            [block for block, _ in candidate_items], page_width
        )
        relax_caption_boxes(page, candidate_items)
        # Float obstacles (figures/tables/captions) that reflowed CJK must avoid.
        page_floats = list(_visual_regions_for_page(page))
        page_floats.extend(
            block.bbox
            for block, _ in candidate_items
            if block.preserve_position
            or block.nowrap
            or block.block_type in ("caption", "figure_label", "table", "equation", "algorithm")
        )
        page_items: List[Tuple[TextBlock, str]] = []
        item_centered_flags: List[bool] = []
        for (block, translated_text), centered in zip(candidate_items, centered_flags):
            # Preserve anchored labels, but keep visually centered captions centered.
            if block.block_type == "caption" and caption_should_center(block, page_width):
                block = center_caption_bbox(block, page_width)
                centered = True
            elif block.preserve_position:
                centered = False
            block = expand_heading_bbox(block)
            requested_size = requested_translation_font_size(block, min_font_size, font_scale)
            # Float-aware clip: keep reflowed CJK body text out of a right-column
            # figure/table instead of painting over it. Only applied when the
            # clipped box still fits the text, so it never makes layout worse.
            if not block.preserve_position and not block.nowrap:
                clipped = _clip_block_bbox_against_floats(block.bbox, page_floats, page_width)
                if clipped != block.bbox and translated_text_fits(
                    block=replace(block, bbox=clipped),
                    text=translated_text,
                    font_pack=font_pack,
                    font_size=requested_size,
                    min_font_size=min_font_size,
                    margin=margin,
                    centered=centered,
                ):
                    block = replace(block, bbox=clipped)
            if skip_overflow and not translated_text_fits(
                block=block,
                text=translated_text,
                font_pack=font_pack,
                font_size=requested_size,
                min_font_size=min_font_size,
                margin=margin,
                centered=centered,
            ):
                warnings.append(
                    "Page %d: kept original text to avoid overlap in bbox %s"
                    % (page_index + 1, compact_bbox(block.bbox))
                )
                continue
            page_items.append((block, translated_text))
            item_centered_flags.append(centered)

        redact_original_text(page, [block for block, _ in page_items], margin, page_gutter)
        # Register after redactions: apply_redactions rebuilds page resources
        # and would drop a font registered beforehand.
        register_font_pack(page, font_pack)
        for (block, translated_text), centered in zip(page_items, item_centered_flags):
            inserted = insert_translated_text(
                page=page,
                block=block,
                text=translated_text,
                font_pack=font_pack,
                font_size=requested_translation_font_size(block, min_font_size, font_scale),
                min_font_size=min_font_size,
                margin=margin,
                centered=centered,
            )
            if not inserted:
                warnings.append(
                    "Page %d: translated text did not fully fit in bbox %s"
                    % (page_index + 1, compact_bbox(block.bbox))
                )

    output_pdf.parent.mkdir(parents=True, exist_ok=True)
    document = subset_fonts_safely(document, font_pack, warnings)
    document.save(str(output_pdf), garbage=4, deflate=True)
    page_count = document.page_count
    document.close()
    return TranslationReport(input_pdf, output_pdf, page_count, len(units), skipped, warnings)


def create_dual_pdf(
    original_pdf: Path,
    translated_pdf: Path,
    output_pdf: Path,
) -> None:
    """Create a dual-language PDF by interleaving original and translated pages.

    For each page: original page, then translated page. This allows side-by-side
    viewing in PDF readers that support page pairs.
    """
    import fitz

    orig_doc = fitz.open(str(original_pdf))
    trans_doc = fitz.open(str(translated_pdf))
    dual_doc = fitz.open()

    for i in range(orig_doc.page_count):
        dual_doc.insert_pdf(orig_doc, from_page=i, to_page=i)
        if i < trans_doc.page_count:
            dual_doc.insert_pdf(trans_doc, from_page=i, to_page=i)

    output_pdf.parent.mkdir(parents=True, exist_ok=True)
    dual_doc.save(str(output_pdf), garbage=4, deflate=True)
    dual_doc.close()
    trans_doc.close()
    orig_doc.close()


_CJK_DETECT_RE = re.compile(r"[\u2e80-\u9fff\uf900-\ufaff]")
_ASCII_WORD_DETECT_RE = re.compile(r"[A-Za-z]{3,}")
_REFERENCE_ENTRY_RE = re.compile(
    r"^\s*(?:\[\d+\]|\d+\.|\w.+\(\d{4}[a-z]?\)|doi:|arxiv:)",
    re.IGNORECASE,
)
_REFERENCE_YEAR_RE = re.compile(r"\b(?:19|20)\d{2}[a-z]?\b", re.IGNORECASE)
_REFERENCE_FRAGMENT_CUE_RE = re.compile(
    r"(?:\bet al\.|\bpp\.|\bpages?\b|\bproceedings\b|\bconference\b|\bjournal\b"
    r"|\btransactions\b|\barxiv\b|\bpreprint\b|\. In\b|\bPMLR\b|\bIEEE\b"
    r"|\bICRA\b|\bICLR\b|\bCoRL\b|\bNeurIPS\b)",
    re.IGNORECASE,
)


def verify_translation(original_pdf: Path, translated_pdf: Path) -> List[str]:
    """Verify translated PDF quality. Returns user-readable issue messages."""
    return [issue.message for issue in verify_translation_issues(original_pdf, translated_pdf)]


def verify_translation_issues(original_pdf: Path, translated_pdf: Path) -> List[TranslationIssue]:
    """Run post-translation QA.

    Checks:
    1. Untranslated English prose outside references/formulas
    2. Text block collisions that can indicate broken layout
    3. Empty pages
    4. Missing raster images after redaction/rendering
    5. Missing formula fragments from math-heavy source blocks
    6. Missing vector graphics from figures/tables
    7. Render-based visual layout score
    8. High-risk table/algorithm/float regions
    """
    import fitz

    issues: List[TranslationIssue] = []
    try:
        orig_doc = fitz.open(str(original_pdf))
        trans_doc = fitz.open(str(translated_pdf))
    except Exception as e:
        return [
            TranslationIssue(
                page=0,
                code="qa_open_failed",
                message=f"Failed to open PDFs for verification: {e}",
                severity="error",
            )
        ]

    if orig_doc.page_count != trans_doc.page_count:
        issues.append(
            TranslationIssue(
                page=0,
                code="page_count_mismatch",
                message=(
                    "Translated PDF page count differs from original: "
                    f"{trans_doc.page_count} vs {orig_doc.page_count}"
                ),
                severity="error",
            )
        )

    try:
        source_units, _, _ = prepare_translation_units(orig_doc)
    except Exception:
        source_units = []
    try:
        preserved_regions_by_page = preserved_original_text_regions(orig_doc)
    except Exception:
        preserved_regions_by_page = {}
    for warning in fragmented_prose_warnings_from_units(source_units):
        match = re.search(r"Page (\d+):", warning)
        page = int(match.group(1)) if match else 0
        issues.append(
            TranslationIssue(
                page=page,
                code="fragmented_prose",
                message=warning,
                severity="warning",
            )
        )

    reference_section_active = False
    for page_idx in range(min(orig_doc.page_count, trans_doc.page_count)):
        orig_page = orig_doc[page_idx]
        trans_page = trans_doc[page_idx]

        # Check for untranslated English paragraphs
        trans_blocks = trans_page.get_text("dict").get("blocks", [])
        visual_regions = _visual_regions_for_page(orig_page)
        preserved_regions = preserved_regions_by_page.get(page_idx, [])
        reference_y = _reference_section_start_y(trans_page)
        if reference_y is None:
            reference_y = _reference_section_start_y(orig_page)
        if reference_y is not None:
            reference_section_active = True
        elif reference_section_active and (
            _page_looks_like_reference_continuation(trans_page)
            or _page_looks_like_reference_continuation(orig_page)
        ):
            reference_y = 0.0
        else:
            reference_section_active = False

        # Count English-only text blocks in translated PDF
        untranslated_examples: List[str] = []
        untranslated_caption_examples: List[str] = []
        untranslated_formula_examples: List[str] = []
        for block in trans_blocks:
            if block.get("type") != 0:
                continue
            if _block_in_reference_section(block, reference_y):
                continue
            text = _extract_text_from_block(block)
            if _looks_like_untranslated_caption(text):
                untranslated_caption_examples.append(" ".join(text.split())[:80])
            elif _looks_like_untranslated_formula_explanation(text):
                untranslated_formula_examples.append(" ".join(text.split())[:80])
            elif _looks_like_untranslated_english(text):
                if _block_inside_visual_region(block, visual_regions):
                    continue
                if _block_overlaps_preserved_regions(block, preserved_regions):
                    continue
                untranslated_examples.append(" ".join(text.split())[:80])

        if untranslated_examples:
            issues.append(
                TranslationIssue(
                    page=page_idx + 1,
                    code="untranslated_english",
                    message=(
                        f"Page {page_idx + 1}: {len(untranslated_examples)} block(s) "
                        "look like untranslated English outside references/formulas"
                    ),
                    severity="error",
                )
            )
        if untranslated_caption_examples:
            issues.append(
                TranslationIssue(
                    page=page_idx + 1,
                    code="untranslated_caption",
                    message=(
                        f"Page {page_idx + 1}: {len(untranslated_caption_examples)} caption(s) "
                        "still look like English and should be translated"
                    ),
                    severity="error",
                )
            )
        if untranslated_formula_examples:
            issues.append(
                TranslationIssue(
                    page=page_idx + 1,
                    code="untranslated_formula_explanation",
                    message=(
                        f"Page {page_idx + 1}: {len(untranslated_formula_examples)} formula "
                        "explanation block(s) still look like English"
                    ),
                    severity="error",
                )
            )

        # Check for overlapping text blocks in translated PDF
        text_bboxes = []
        for block in trans_blocks:
            if block.get("type") != 0:
                continue
            if _block_in_reference_section(block, reference_y):
                continue
            for bbox, text in _overlap_text_entries_from_block(block):
                if (
                    len(text.strip()) < 4
                    or _is_reference_or_formula_text(text)
                    or _looks_like_overlap_exempt_text(text)
                ):
                    continue
                text_bboxes.append((bbox, text))

        for i in range(len(text_bboxes)):
            for j in range(i + 1, len(text_bboxes)):
                (x0a, y0a, x1a, y1a), _ = text_bboxes[i]
                (x0b, y0b, x1b, y1b), _ = text_bboxes[j]
                h_overlap = max(0, min(x1a, x1b) - max(x0a, x0b))
                v_overlap = max(0, min(y1a, y1b) - max(y0a, y0b))
                if h_overlap > 10 and v_overlap > 8:
                    issues.append(
                        TranslationIssue(
                            page=page_idx + 1,
                            code="text_overlap",
                            message=(
                                f"Page {page_idx + 1}: text blocks overlap near "
                                f"y={max(y0a, y0b):.0f}"
                            ),
                        )
                    )
                    break

        issues.extend(_caption_graphic_overlap_issues(orig_page, trans_page, page_idx + 1))

        # Check for empty translated page
        trans_text = trans_page.get_text("text").strip()
        orig_text = orig_page.get_text("text").strip()
        if len(orig_text) > 100 and len(trans_text) < 20:
            issues.append(
                TranslationIssue(
                    page=page_idx + 1,
                    code="empty_page",
                    message=f"Page {page_idx + 1}: translated page appears empty",
                    severity="error",
                )
            )

        orig_image_count, orig_image_area = _visible_image_stats(orig_page)
        trans_image_count, trans_image_area = _visible_image_stats(trans_page)
        image_count_missing = orig_image_count and trans_image_count < max(1, orig_image_count // 2)
        image_area_missing = (
            orig_image_area > 1.0 and trans_image_area < orig_image_area * 0.5
        )
        if image_count_missing or image_area_missing:
            issues.append(
                TranslationIssue(
                    page=page_idx + 1,
                    code="missing_image",
                    message=(
                        f"Page {page_idx + 1}: translated page has fewer visible image blocks "
                        f"({trans_image_count}/{orig_image_count})"
                    ),
                    severity="error",
                )
            )

        orig_graphics = _count_vector_graphics(orig_page)
        trans_graphics = _count_vector_graphics(trans_page)
        if orig_graphics and trans_graphics < max(1, orig_graphics // 2):
            issues.append(
                TranslationIssue(
                    page=page_idx + 1,
                    code="missing_graphic",
                    message=(
                        f"Page {page_idx + 1}: translated page has far fewer vector graphics "
                        f"({trans_graphics}/{orig_graphics})"
                    ),
                    severity="error",
                )
            )

        formula_fragments = _extract_formula_fragments(orig_page)
        if formula_fragments:
            trans_compact = _normalize_formula_fragment_for_compare(trans_page.get_text("text"))
            missing_formulas = [
                fragment
                for fragment in formula_fragments
                if not _formula_fragment_present(fragment, trans_compact)
            ]
            if missing_formulas:
                issues.append(
                    TranslationIssue(
                        page=page_idx + 1,
                        code="formula_changed",
                        message=(
                            f"Page {page_idx + 1}: {len(missing_formulas)} formula "
                            "fragment(s) appear missing or altered"
                        ),
                        severity="error",
                    )
                )

        for feature in _detect_high_risk_layout_features(orig_page):
            issues.append(
                TranslationIssue(
                    page=page_idx + 1,
                    code="high_risk_layout",
                    message=f"Page {page_idx + 1}: high-risk layout feature detected: {feature}",
                    severity="warning",
                )
            )

    try:
        from .visual_qa import score_visual_layout

        visual = score_visual_layout(original_pdf, translated_pdf)
        if getattr(visual, "page_count_delta", 0) != 0:
            issues.append(
                TranslationIssue(
                    page=0,
                    code="page_count_mismatch",
                    message=(
                        "Translated PDF page count differs from original "
                        f"({visual.translated_pages}/{visual.original_pages})"
                    ),
                    severity="error",
                )
            )
        if getattr(visual, "min_page_size_similarity", 1.0) < 0.85:
            worst = (
                min(
                    visual.pages,
                    key=lambda page: getattr(page, "page_size_similarity", 1.0),
                )
                if visual.pages
                else None
            )
            page_num = worst.page if worst else 0
            issues.append(
                TranslationIssue(
                    page=page_num,
                    code="page_size_mismatch",
                    message=(
                        "Rendered page size differs from original "
                        f"(similarity={visual.min_page_size_similarity:.2f})"
                    ),
                    severity="error",
                )
            )
        if visual.overall_score < 0.35:
            worst = min(visual.pages, key=lambda page: page.score) if visual.pages else None
            page_num = worst.page if worst else 0
            issues.append(
                TranslationIssue(
                    page=page_num,
                    code="visual_layout_score_low",
                    message=(
                        "Rendered layout similarity is low "
                        f"(score={visual.overall_score:.2f}); inspect visual output"
                    ),
                    severity="error",
                )
            )
        elif (
            visual.min_zone_score < 0.25
            and visual.overall_score < 0.90
            and _visual_min_zone_intersects_graphics(orig_doc, visual)
        ):
            worst = (
                min(visual.pages, key=lambda page: page.min_zone_score)
                if visual.pages
                else None
            )
            page_num = worst.page if worst else 0
            issues.append(
                TranslationIssue(
                    page=page_num,
                    code="visual_layout_region_low",
                    message=(
                        "Rendered layout has a low-similarity page region "
                        f"(overall={visual.overall_score:.2f}, "
                        f"min_region={visual.min_zone_score:.2f}); inspect figures/tables"
                    ),
                    severity="error",
                )
            )
    except Exception:
        pass

    trans_doc.close()
    orig_doc.close()
    return issues


def _visual_min_zone_intersects_graphics(
    document: object,
    visual: object,
    *,
    grid_rows: int = 3,
    grid_cols: int = 2,
) -> bool:
    pages = getattr(visual, "pages", [])
    if not pages:
        return False
    worst = min(pages, key=lambda page: getattr(page, "min_zone_score", 1.0))
    zone_scores = getattr(worst, "zone_scores", ())
    if not zone_scores:
        return False
    min_zone_index = min(range(len(zone_scores)), key=zone_scores.__getitem__)
    page_number = int(getattr(worst, "page", 0))
    if page_number <= 0 or page_number > getattr(document, "page_count", 0):
        return False
    page = document[page_number - 1]
    regions = _visual_regions_for_page(page)
    if not regions:
        return False

    row = min_zone_index // grid_cols
    col = min_zone_index % grid_cols
    rect = page.rect
    zone = (
        float(rect.width) * col / grid_cols,
        float(rect.height) * row / grid_rows,
        float(rect.width) * (col + 1) / grid_cols,
        float(rect.height) * (row + 1) / grid_rows,
    )
    zone_area = max(1.0, bbox_area(zone))
    return any(bbox_intersection_area(zone, region) / zone_area >= 0.02 for region in regions)


def _extract_text_from_block(block: dict) -> str:
    text = ""
    for line in block.get("lines", []):
        for span in line.get("spans", []):
            text += span.get("text", "")
    return text.strip()


def _overlap_text_entries_from_block(block: dict) -> List[Tuple[BBox, str]]:
    """Return line-level text bboxes for overlap QA.

    PyMuPDF may group table/grid text from several rows and columns into one
    text block. Using the outer block bbox then creates false overlaps even
    when no glyphs touch, so overlap QA works at physical-line granularity.
    """
    entries: List[Tuple[BBox, str]] = []
    for line in block.get("lines", []):
        line_text = "".join(span.get("text", "") for span in line.get("spans", [])).strip()
        bbox = line.get("bbox")
        if line_text and bbox:
            entries.append((tuple(float(value) for value in bbox), line_text))
    if entries:
        return entries
    bbox = block.get("bbox")
    text = _extract_text_from_block(block)
    if text and bbox:
        return [(tuple(float(value) for value in bbox), text)]
    return []


def _is_reference_or_formula_text(text: str) -> bool:
    compact = " ".join(text.split())
    if not compact:
        return True
    if _REFERENCE_ENTRY_RE.search(compact):
        return True
    if _looks_like_reference_entry_text(compact):
        return True
    lower = compact.lower()
    if "doi" in lower or "arxiv" in lower or "http://" in lower or "https://" in lower:
        return True
    if _looks_like_code_or_symbolic_text(compact):
        return True
    if looks_like_math(compact):
        return True
    math_chars = sum(1 for char in compact if char in MATH_SYMBOLS)
    return math_chars >= 3 and math_chars / max(len(compact), 1) >= 0.08


def _looks_like_untranslated_english(text: str) -> bool:
    compact = " ".join(text.split())
    if len(compact) < 35 or _is_reference_or_formula_text(compact):
        return False
    if _looks_like_model_or_identifier_list_text(compact):
        return False
    if _looks_like_prompt_output_field_list(compact):
        return False
    if _looks_like_compact_control_table_row(compact):
        return False
    if _looks_like_author_or_affiliation_text(compact):
        return False
    words = _ASCII_WORD_DETECT_RE.findall(compact)
    if len(words) < 5:
        return False
    cjk_chars = len(_CJK_DETECT_RE.findall(compact))
    if cjk_chars >= max(4, len(words) // 2):
        return False
    ascii_letters = sum(1 for char in compact if char.isascii() and char.isalpha())
    return ascii_letters / max(len(compact), 1) >= 0.45


def _looks_like_model_or_identifier_list_text(text: str) -> bool:
    """Model-name stacks in tables are identifiers, not untranslated prose."""
    compact = " ".join(text.split())
    refs = re.findall(r"\[\d+\]", compact)
    if len(refs) < 4:
        return False
    without_refs = re.sub(r"\[\d+\]", " ", compact)
    if re.search(r"[。！？;；:：]", without_refs):
        return False
    words = _ASCII_WORD_DETECT_RE.findall(without_refs)
    if len(words) < max(4, len(refs) // 2):
        return False
    lowercase_words = [word for word in words if word.islower() and len(word) > 2]
    return len(lowercase_words) <= max(1, len(words) // 8)


def _looks_like_prompt_output_field_list(text: str) -> bool:
    """Prompt templates often preserve literal output field identifiers."""
    normalized = re.sub(r"[^a-z_]", "", text.lower())
    if not normalized:
        return False
    semantic_fields = (
        "robotness",
        "target_embodiment_match",
        "targetembodimentmatch",
        "interaction_preservation",
        "interactionpreservation",
        "scene_preservation",
        "scenepreservation",
    )
    perceptual_fields = (
        "naturalness",
        "artifact_absence",
        "artifactabsence",
        "local_coherence",
        "localcoherence",
    )
    semantic_hits = sum(1 for field in semantic_fields if field in normalized)
    perceptual_hits = sum(1 for field in perceptual_fields if field in normalized)
    return "reasoning" in normalized and (semantic_hits >= 3 or perceptual_hits >= 2)


def _looks_like_compact_control_table_row(text: str) -> bool:
    """Dense robotics/reward table rows are mostly identifiers and formulas."""
    compact = " ".join(text.split())
    lower = compact.lower()
    if len(compact) > 180:
        return False
    if not re.search(
        r"(?:locomotion|actuator|gravity|command|tracking|approach|ctrl|"
        r"court|ball\s*state|teammate\s*states|action\s*penalty|reward)",
        lower,
    ):
        return False
    if not (
        any(token in compact for token in ("=", "[", "]", "¬", "−", "+", "×"))
        or "exp(" in lower
        or re.search(r"(?:\d[A-Za-z]|[A-Za-z]\d)", compact)
    ):
        return False
    words = _ASCII_WORD_DETECT_RE.findall(compact)
    if len(words) > 24:
        return False
    body_cues = ("propose", "proposed", "show", "shows", "evaluate", "evaluates", "however")
    return not any(cue in lower for cue in body_cues)


def _looks_like_author_or_affiliation_text(text: str) -> bool:
    compact = " ".join(text.split())
    if "@" in compact or re.search(r"\{[^{}]{3,80}\}", compact):
        return True
    authorish = re.sub(r"(?<=[a-z])\d+(?=[A-Z])", " ", compact)
    names = re.findall(r"\b[A-Z][A-Za-z-]+(?:\s+[A-Z][A-Za-z-]+)+", authorish)
    lower = compact.lower()
    body_verbs = (
        "propose",
        "proposed",
        "show",
        "shows",
        "using",
        "improve",
        "improves",
        "evaluate",
        "evaluates",
    )
    title_tokens = re.findall(r"\b[A-Z][A-Za-z-]+\b", authorish)
    lowercase_words = re.findall(r"\b[a-z]{3,}\b", authorish)
    if re.search(r"[∗†‡*]", compact):
        return (
            len(names) >= 3
            or compact.count(",") >= 3
            or (len(title_tokens) >= 5 and len(lowercase_words) <= 1)
        )
    if compact.count(",") >= 4 and len(names) >= 3:
        return True
    if (
        len(compact) <= 220
        and compact.count(",") >= 3
        and re.search(r"\b[A-Z][A-Za-z´'’.-]+,\s*[A-Z]\.?", compact)
    ):
        return not any(verb in lower for verb in body_verbs)
    if (
        len(compact) <= 220
        and len(title_tokens) >= 5
        and len(lowercase_words) <= 1
        and not re.search(r"[.!?。！？]", compact)
    ):
        return not any(verb in lower for verb in body_verbs)
    if len(compact) <= 220 and len(names) >= 4:
        return not any(verb in lower for verb in body_verbs)
    return False


def _looks_like_code_or_symbolic_text(text: str) -> bool:
    compact = " ".join(text.split())
    lower = compact.lower()
    words = _ASCII_WORD_DETECT_RE.findall(compact)
    if re.search(r"^(?:#|>>>|\.\.\.)\s*", compact):
        return True
    if re.search(r"\b(?:def|class)\s+\w+\s*\(", compact):
        return True
    if re.search(r"^\s*(?:import|from)\s+[A-Za-z_][\w.]*", compact):
        return True
    if re.search(r"\b(?:np|numpy|mcdc|h5py)\s*\.", compact):
        return True
    if re.search(
        r"\b(?:MaterialMG|Surface|Cell|Universe|Source|Tally|MeshTally|array)\s*\(",
        compact,
    ):
        return True
    if re.search(r"\b[a-z][a-z0-9]*_[a-z0-9_]*\b", compact):
        if any(marker in compact for marker in (":", "=", "-", "(", ")", "[", "]")):
            return True
    if re.search(r"^\s*\d+\s*:\s*[A-Za-z]", compact):
        return True
    if (
        re.search(r"^\s*(?:Require|Ensure|Input|Output)\s*:", compact, re.IGNORECASE)
        and len(words) <= 14
        and not re.search(r"[.!?。！？]", compact)
    ):
        return True
    if (
        re.search(r"\b[a-z_][\w-]*\s*:\s*", lower)
        and compact.count(":") >= 2
        and not re.search(r"[.!?。！？]", compact)
    ):
        return True
    if any(token in compact for token in ("✓", "——", "———")):
        return True
    if any(token in compact for token in (":=", "setdefault(", "supp[", "ρ", "π", "ϕ", "χ", "ξ")):
        return True
    if re.search(r"\[[0-9]+\]", compact) and len(words) <= 16:
        if re.search(r"\b(?:VoteNet|ScanRefer|ViewRefer|Success|Rate|Drop|Stage)\b", compact):
            return True
    if (
        re.search(r"\[[0-9]+\]", compact)
        and len(words) <= 30
        and (
            re.search(r"\b(?:Objects?|Geometry|Geom|Prior)\b", compact)
            or (
                ("Regular" in compact or "Irregular" in compact)
                and ("AC" in compact or "GA" in compact)
            )
        )
    ):
        return True
    camel_tokens = re.findall(r"\b[A-Z][A-Za-z0-9]*(?:[A-Z][a-z0-9]+)+\b", compact)
    if len(camel_tokens) >= 2 and len(words) <= 16 and not re.search(r"[.!?。！？]", compact):
        return True
    greek_chars = sum(1 for char in compact if "\u0370" <= char <= "\u03ff")
    math_chars = sum(1 for char in compact if char in MATH_SYMBOLS)
    if math_chars >= 2 and len(words) <= 18 and re.search(r"\b(?:for|return|end|do)\b", lower):
        return True
    if greek_chars >= 2 and (math_chars >= 1 or "supp" in lower):
        return True
    if greek_chars >= 2 and len(words) <= 12 and re.search(
        r"\b(?:dataset|error|rate|lrt|gap|centroid|feat|diff)\b",
        lower,
    ):
        return True
    if greek_chars >= 2 and len(words) <= 12 and not re.search(r"[.!?。！？]", compact):
        return True
    return False


def _looks_like_overlap_exempt_text(text: str) -> bool:
    compact = " ".join(text.split())
    if not compact:
        return True
    control_chars = sum(1 for char in compact if ord(char) < 32)
    if control_chars >= 2:
        return True
    if _looks_like_code_or_symbolic_text(compact) or looks_like_math(compact):
        return True

    words = _PROSE_WORD_RE.findall(compact)
    math_chars = sum(1 for char in compact if char in MATH_SYMBOLS)
    greek_chars = sum(1 for char in compact if "\u0370" <= char <= "\u03ff")
    math_punct_chars = sum(1 for char in compact if char in "(){}[],⟨⟩")
    digit_chars = sum(1 for char in compact if char.isdigit())
    if (math_chars + greek_chars + math_punct_chars) >= 2 and len(words) <= 12:
        return True
    if greek_chars >= 1 and math_punct_chars >= 1 and len(words) <= 12:
        return True
    if re.search(r"\b(?:[CX][st]?\(|Xs|Xt|Orel|lsub)\b", compact) and len(words) <= 16:
        return True
    if (
        re.search(
            r"\b(?:arg|max|min|log|exp|sim|softmax|crossentropy|mlp|cos|supp)\b",
            compact,
            re.IGNORECASE,
        )
        and (math_chars or digit_chars)
        and len(words) <= 14
    ):
        return True

    no_space = re.sub(r"\s+", "", compact)
    if len(no_space) <= 180 and re.search(r"(?:[dp]\d+){3,}", no_space, re.IGNORECASE):
        return True
    if len(no_space) <= 140 and digit_chars / max(len(no_space), 1) >= 0.25:
        return True
    if len(compact) <= 120 and len(words) <= 8:
        has_sentence_mark = bool(re.search(r"[。！？.!?]", compact))
        title_like = sum(1 for word in words if word[0].isupper()) >= 2
        if not has_sentence_mark and title_like:
            return True
    cjk_chars = len(_CJK_DETECT_RE.findall(compact))
    if cjk_chars >= 30 and not re.search(r"[，。；：！？]", compact):
        return True
    return False


def _looks_like_untranslated_caption(text: str) -> bool:
    compact = " ".join(text.split())
    if not _ENGLISH_CAPTION_PREFIX_RE.match(compact):
        return False
    body = _ENGLISH_CAPTION_PREFIX_RE.sub("", compact, count=1).strip()
    if not body or _CJK_DETECT_RE.search(body):
        return False
    return bool(_ASCII_WORD_DETECT_RE.search(body))


def _looks_like_untranslated_formula_explanation(text: str) -> bool:
    compact = " ".join(text.split())
    if len(compact) < 12 or not _FORMULA_EXPLANATION_RE.match(compact):
        return False
    if _CJK_DETECT_RE.search(compact):
        return False
    words = _ASCII_WORD_DETECT_RE.findall(compact)
    if len(words) < 3:
        return False
    ascii_letters = sum(1 for char in compact if char.isascii() and char.isalpha())
    return ascii_letters / max(len(compact), 1) >= 0.35


def _reference_section_start_y(page: object) -> float | None:
    for block in page.get_text("dict").get("blocks", []):
        if block.get("type") != 0:
            continue
        compact = " ".join(_extract_text_from_block(block).split()).rstrip(":：")
        if _REFERENCES_HEADING_RE.match(compact):
            bbox = block.get("bbox")
            if bbox:
                return max(0.0, float(bbox[1]) - 1.0)
    return None


def _page_looks_like_reference_continuation(page: object) -> bool:
    text_blocks: List[str] = []
    for block in page.get_text("dict").get("blocks", []):
        if block.get("type") != 0:
            continue
        compact = " ".join(_extract_text_from_block(block).split())
        if len(compact) >= 20:
            text_blocks.append(compact)
    if not text_blocks:
        return False

    reference_like = sum(1 for text in text_blocks if _looks_like_reference_entry_text(text))
    if reference_like >= 2:
        return True
    return reference_like == 1 and len(text_blocks) <= 4


def _looks_like_reference_entry_text(text: str) -> bool:
    compact = " ".join(text.split())
    if not compact:
        return False
    if _REFERENCE_ENTRY_RE.search(compact):
        return True
    lower = compact.lower()
    if "doi" in lower or "arxiv" in lower or "http://" in lower or "https://" in lower:
        return True
    words = _ASCII_WORD_DETECT_RE.findall(compact)
    if len(words) < 6 or not _REFERENCE_YEAR_RE.search(compact):
        if not _REFERENCE_FRAGMENT_CUE_RE.search(compact):
            return False
        authorish = compact.count(",") >= 2 or bool(
            re.match(r"^[A-Z][A-Za-z-]+(?:\s+[A-Z]\.?)?(?:\s+[A-Z][A-Za-z-]+)?", compact)
        )
        if not authorish:
            return False
    cjk_chars = len(_CJK_DETECT_RE.findall(compact))
    return cjk_chars < max(4, len(words) // 3)


def _block_in_reference_section(block: dict, reference_y: float | None) -> bool:
    if reference_y is None:
        return False
    bbox = block.get("bbox")
    return bool(bbox and float(bbox[1]) >= reference_y)


def _block_inside_visual_region(
    block: dict,
    visual_regions: Sequence[BBox],
    *,
    min_overlap: float = 0.65,
) -> bool:
    bbox = block.get("bbox")
    if not bbox or not visual_regions:
        return False
    block_bbox = tuple(float(value) for value in bbox)
    area = max(1.0, bbox_area(block_bbox))
    return any(
        bbox_intersection_area(block_bbox, region) / area >= min_overlap
        for region in visual_regions
    )


def _block_overlaps_preserved_regions(
    block: dict,
    regions: Sequence[BBox],
    *,
    min_total_overlap: float = 0.20,
) -> bool:
    bbox = block.get("bbox")
    if not bbox or not regions:
        return False
    block_bbox = tuple(float(value) for value in bbox)
    area = max(1.0, bbox_area(block_bbox))
    total_overlap = sum(bbox_intersection_area(block_bbox, region) for region in regions)
    return total_overlap / area >= min_total_overlap


def preserved_original_text_regions(document: object) -> Dict[int, List[BBox]]:
    """Regions whose English text is intentionally preserved in graphics/table mode."""
    global _bibliography_ended, _bibliography_heading_size

    raw_blocks, _ = collect_text_blocks(document)
    graphic_regions = collect_graphic_regions(document)
    blocks = merge_paragraph_blocks(raw_blocks, graphic_regions_by_page=graphic_regions)

    _bibliography_seen.clear()
    _bibliography_ended = False
    _bibliography_heading_size = 0.0
    for page_index in range(document.page_count):
        page = document[page_index]
        page_blocks = [block for block in blocks if block.page_index == page_index]
        classify_blocks(page_blocks, page_index, page.rect.height, detect_image_zones(page))

    regions: Dict[int, List[BBox]] = {}
    for block in blocks:
        if not block.should_translate:
            continue
        if should_preserve_original_block(block, graphic_regions.get(block.page_index, [])):
            regions.setdefault(block.page_index, []).append(block.bbox)
    return regions


def _restore_unit_translation(
    translated_text: str,
    mapping: Dict[int, str],
) -> Tuple[str, List[int]]:
    restored, missing = restore_text(translated_text, mapping)
    return clean_translation(restored), missing


def _translated_block_still_english(block: TextBlock, translated_text: str) -> bool:
    if not block.should_translate:
        return False
    if block.block_type in ("algorithm", "bibliography", "equation", "figure_label", "footer"):
        return False
    return _looks_like_untranslated_english(translated_text)


def _visible_image_stats(page: object) -> Tuple[int, float]:
    """Count visible raster image blocks and their displayed page area."""
    try:
        image_blocks = [
            block
            for block in page.get_text("dict").get("blocks", [])
            if block.get("type") == 1 and block.get("bbox")
        ]
    except Exception:
        image_blocks = []
    if image_blocks:
        area = 0.0
        for block in image_blocks:
            x0, y0, x1, y1 = (float(value) for value in block["bbox"])
            area += max(0.0, x1 - x0) * max(0.0, y1 - y0)
        return len(image_blocks), area
    try:
        return len(page.get_images()), 0.0
    except Exception:
        return 0, 0.0


def _count_vector_graphics(page: object) -> int:
    try:
        drawings = page.get_drawings()
    except Exception:
        return 0
    count = 0
    for drawing in drawings:
        rect = drawing.get("rect")
        if rect is None:
            continue
        width = float(rect.x1 - rect.x0)
        height = float(rect.y1 - rect.y0)
        if width >= GRAPHIC_REGION_MIN_SIDE and height >= GRAPHIC_REGION_MIN_SIDE:
            count += 1
    return count


def _caption_graphic_overlap_issues(
    original_page: object,
    translated_page: object,
    page_number: int,
) -> List[TranslationIssue]:
    visual_regions = _visual_regions_for_page(original_page)
    if not visual_regions:
        return []
    source_captions = _source_caption_regions(original_page)

    issues: List[TranslationIssue] = []
    for block in translated_page.get_text("dict").get("blocks", []):
        if block.get("type") != 0:
            continue
        text = _extract_text_from_block(block)
        if not _CAPTION_RE.match(text.strip()):
            continue
        bbox = block.get("bbox")
        if not bbox:
            continue
        caption_bbox = tuple(float(value) for value in bbox)
        if _caption_near_source_caption(text, caption_bbox, source_captions):
            continue
        for region in visual_regions:
            if _bbox_overlap_ratio(caption_bbox, region) >= 0.08:
                issues.append(
                    TranslationIssue(
                        page=page_number,
                        code="caption_overlap",
                        message=(
                            f"Page {page_number}: caption overlaps a figure or table region"
                        ),
                    )
                )
                break
    return issues


def _source_caption_regions(page: object) -> List[Tuple[Tuple[str, str], BBox]]:
    captions: List[Tuple[Tuple[str, str], BBox]] = []
    for block in page.get_text("dict").get("blocks", []):
        if block.get("type") != 0 or not block.get("bbox"):
            continue
        text = _extract_text_from_block(block).strip()
        if not _CAPTION_RE.match(text):
            continue
        captions.append(
            (
                _caption_key(text),
                tuple(float(value) for value in block["bbox"]),
            )
        )
    return captions


def _caption_key(text: str) -> Tuple[str, str]:
    compact = " ".join(text.split())
    match = re.match(r"^(Figure|Fig\.|Table|图|表)\s*(\d+)", compact, re.IGNORECASE)
    if not match:
        return ("", "")
    kind = match.group(1).lower()
    if kind in {"figure", "fig.", "图"}:
        kind = "figure"
    elif kind in {"table", "表"}:
        kind = "table"
    return kind, match.group(2)


def _caption_near_source_caption(
    text: str,
    bbox: BBox,
    source_captions: Sequence[Tuple[Tuple[str, str], BBox]],
) -> bool:
    if not source_captions:
        return False
    key = _caption_key(text)
    candidates = [caption for caption in source_captions if key != ("", "") and caption[0] == key]
    if not candidates:
        candidates = source_captions
    for _candidate_key, source_bbox in candidates:
        height = max(1.0, source_bbox[3] - source_bbox[1])
        expanded = (
            source_bbox[0] - 12.0,
            source_bbox[1] - max(20.0, height * 0.5),
            source_bbox[2] + 12.0,
            source_bbox[3] + max(20.0, height * 0.8),
        )
        if _bbox_overlap_ratio(bbox, expanded) >= 0.75:
            return True
    return False


def _visual_regions_for_page(page: object) -> List[BBox]:
    regions: List[BBox] = []
    text_boxes = [
        tuple(float(value) for value in block.get("bbox", (0, 0, 0, 0)))
        for block in page.get_text("dict").get("blocks", [])
        if block.get("type") == 0 and block.get("bbox")
    ]
    for img in page.get_images():
        try:
            rect = page.get_image_bbox(img)
        except Exception:
            continue
        if rect and rect.is_empty is False and rect.is_valid:
            regions.append((rect.x0, rect.y0, rect.x1, rect.y1))
    try:
        drawings = page.get_drawings()
    except Exception:
        drawings = []
    for drawing in drawings:
        rect = drawing.get("rect")
        if rect is None:
            continue
        width = float(rect.x1 - rect.x0)
        height = float(rect.y1 - rect.y0)
        bbox = (float(rect.x0), float(rect.y0), float(rect.x1), float(rect.y1))
        if _looks_like_text_background(bbox, text_boxes):
            continue
        if width >= GRAPHIC_REGION_MIN_SIDE and height >= GRAPHIC_REGION_MIN_SIDE:
            regions.append(bbox)
    if not regions:
        return []
    return merge_nearby_bboxes(regions, 2.0)


def _clip_block_bbox_against_floats(
    bbox: BBox,
    floats: Sequence[BBox],
    page_width: float,
    *,
    min_keep_ratio: float = 0.30,
    side_gap: float = 3.0,
) -> BBox:
    """Shrink a wide (cross-column) block's right edge so reflowed CJK text stays
    out of a right-column figure/table/caption.

    Source PDFs often give a paragraph that visually wraps around a right-side
    float a full-width bounding box (lines above/below the float span the page).
    The CJK engine then fills that whole box left-to-right and paints over the
    float. We only clip when the block is clearly cross-column, a float occupies
    its right region, and the clipped box still keeps a usable width; otherwise
    the original bbox is returned so nothing gets worse.
    """
    x0, y0, x1, y1 = bbox
    width = x1 - x0
    if page_width <= 0 or width < page_width * 0.55:
        return bbox
    new_x1 = x1
    for fx0, fy0, fx1, fy1 in floats:
        if min(y1, fy1) - max(y0, fy0) <= 4.0:
            continue
        if fx0 <= x0 + width * 0.45:
            continue
        if fx1 >= x1 - width * 0.10 and fx0 < new_x1:
            new_x1 = min(new_x1, fx0 - side_gap)
    if new_x1 < x1 - 1.0 and (new_x1 - x0) >= page_width * min_keep_ratio:
        return (x0, y0, new_x1, y1)
    return bbox


def _looks_like_text_background(bbox: BBox, text_boxes: Sequence[BBox]) -> bool:
    area = max(1.0, bbox_area(bbox))
    return any(bbox_intersection_area(bbox, text_box) / area >= 0.10 for text_box in text_boxes)


def _bbox_overlap_ratio(a: BBox, b: BBox) -> float:
    ax0, ay0, ax1, ay1 = a
    bx0, by0, bx1, by1 = b
    overlap_w = max(0.0, min(ax1, bx1) - max(ax0, bx0))
    overlap_h = max(0.0, min(ay1, by1) - max(ay0, by0))
    if overlap_w <= 0 or overlap_h <= 0:
        return 0.0
    area = max(1.0, (ax1 - ax0) * (ay1 - ay0))
    return (overlap_w * overlap_h) / area


def _extract_formula_fragments(page: object) -> List[str]:
    fragments: List[str] = []
    seen: set[str] = set()
    for block in page.get_text("dict").get("blocks", []):
        if block.get("type") != 0:
            continue
        text = _extract_text_from_block(block)
        if not _looks_like_formula_fragment(text):
            continue
        compact = re.sub(r"\s+", "", text)
        if compact and compact not in seen:
            fragments.append(compact)
            seen.add(compact)
    return fragments[:20]


def _normalize_formula_fragment_for_compare(text: str) -> str:
    compact = re.sub(r"\s+", "", text)
    return (
        compact.replace("：", ":")
        .replace("，", ",")
        .replace("（", "(")
        .replace("）", ")")
        .replace("−", "-")
    )


def _strip_formula_compare_tail(text: str) -> str:
    previous = None
    current = text
    while current != previous:
        previous = current
        current = re.sub(r"[.,;:，。；：]+$", "", current)
        current = re.sub(r"\(\d{1,3}(?:\.\d+)?\)$", "", current)
    return current


def _formula_fragment_present(fragment: str, translated_compact: str) -> bool:
    normalized = _normalize_formula_fragment_for_compare(fragment)
    if normalized in translated_compact:
        return True
    stripped = _strip_formula_compare_tail(normalized)
    if len(stripped) >= 6 and stripped in translated_compact:
        return True
    return (
        len(stripped) >= 4
        and bool(re.search(r"[\d=+\-*/^_≤≥<>∥ρϵηαβγδ]", stripped))
        and stripped in translated_compact
    )


def _looks_like_formula_fragment(text: str) -> bool:
    compact = " ".join(text.split())
    if len(compact) < 3 or _REFERENCE_ENTRY_RE.search(compact):
        return False
    if any(ord(char) < 32 for char in text):
        return False
    if _looks_like_reference_entry_text(compact) or _looks_like_author_or_affiliation_text(compact):
        return False
    if re.search(
        r"\b(?:import|np\.|numpy\.|mcdc\.|h5py|MaterialMG|Surface|Cell|Universe)\b",
        compact,
    ):
        return False
    if re.search(r"\b[a-z][a-z0-9]*_[a-z0-9_]*\s*=", compact):
        return False
    if re.search(r"^[A-Za-z]\s*=\s*\[[^\]]+\]\s*,?\d*$", compact):
        return False
    if re.search(r'^"[^"]+"\s*:\s*<[^>]+>\s*,?\d*$', compact):
        return False
    if re.search(r"^[A-Za-z_][A-Za-z0-9_]*\s*=\s*[-+]?\d+(?:\.\d+)?\d*$", compact):
        return False
    if re.search(r"^[A-Za-z_][A-Za-z0-9_]*\s*=\s*[A-Za-z_][A-Za-z0-9_]*\d*$", compact):
        return False
    if re.search(r"^\s*\d+\s*:\s*[A-Za-z]", compact):
        return False
    if re.search(
        r"(?i)(?:^|[^a-z])(?:if|and|by|wehave|where|then|for|with|recall|hence|"
        r"similarly|moreover|therefore)(?:[^a-z]|$)",
        compact,
    ):
        return False
    if re.search(r"\bHandover\d+(?:/\d+){3,}", compact):
        return False
    compact_no_space = re.sub(r"\s+", "", compact)
    if compact_no_space[-1:] in {"=", "+", "−", "-", "≤", "≥", "<", ">", "∈", "∩", "×"}:
        return False
    if re.fullmatch(r"[A-Za-z][A-Za-z0-9]*=√", compact_no_space):
        return False
    digit_chars = sum(1 for char in compact_no_space if char.isdigit())
    slash_chars = compact_no_space.count("/")
    if re.search(r"\d{3}\d{3}", compact_no_space) and "=" not in compact_no_space:
        return False
    if slash_chars >= 3 and digit_chars / max(len(compact_no_space), 1) >= 0.35:
        return False
    words = _ASCII_WORD_DETECT_RE.findall(compact)
    math_chars = sum(1 for char in compact if char in MATH_SYMBOLS)
    if "=" in compact and not words:
        return True
    if looks_like_math(compact) and len(words) <= 1:
        return True
    return math_chars >= 2 and math_chars / max(len(compact), 1) >= 0.08 and len(words) <= 1


def _detect_high_risk_layout_features(page: object) -> List[str]:
    features: List[str] = []
    try:
        drawings = page.get_drawings()
    except Exception:
        drawings = []
    if len(drawings) >= 40:
        features.append("dense vector drawing or table grid")
    if _has_table_like_text_grid(page):
        features.append("table-like text grid")
    if _has_algorithm_like_text(page):
        features.append("algorithm or pseudocode block")
    if _has_cross_page_float_risk(page):
        features.append("float near page boundary")
    return features


def _has_table_like_text_grid(page: object) -> bool:
    rows: Dict[int, int] = {}
    for block in page.get_text("dict").get("blocks", []):
        if block.get("type") != 0:
            continue
        text = _extract_text_from_block(block)
        if len(text.strip()) > 35:
            continue
        bbox = block.get("bbox")
        if not bbox:
            continue
        row = int(float(bbox[1]) // 8)
        rows[row] = rows.get(row, 0) + 1
    return sum(1 for count in rows.values() if count >= 4) >= 3


def _has_algorithm_like_text(page: object) -> bool:
    text = page.get_text("text")
    markers = len(re.findall(r"^\s*\d{1,2}:\s+\S", text, flags=re.MULTILINE))
    keywords = re.search(r"\b(Require|Ensure|Input|Output|Algorithm)\b", text)
    return markers >= 2 or bool(markers and keywords)


def _has_cross_page_float_risk(page: object) -> bool:
    height = float(page.rect.height)
    risky = 0
    for zone in detect_image_zones(page):
        _x0, y0, _x1, y1 = zone
        if y0 < 40.0 or y1 > height - 40.0:
            risky += 1
    return risky >= 1


def _normalize_font_name(name: str) -> str:
    """Match display names ('Hiragino Sans GB W6') against PostScript base
    names ('HiraginoSansGB-W6')."""
    return re.sub(r"[\s\-_,]+", "", name.split("+")[-1]).lower()


def inserted_font_names(font_pack: FontPack) -> set:
    """Normalized names of the CJK faces this engine inserts."""
    names = set()
    for font in (font_pack.regular, font_pack.bold, font_pack.fallback):
        name = getattr(font, "name", None)
        if name:
            names.add(_normalize_font_name(name))
    return names


def subset_fonts_safely(document: object, font_pack: FontPack, warnings: List[str]) -> object:
    """Subset fonts, but roll back when our inserted CJK glyphs are lost.

    PyMuPDF's subset_fonts() occasionally drops glyphs from CJK collection
    faces (e.g. Hiragino bold), rendering headings as blanks while the text
    layer stays intact. Full CJK faces cost ~20 MB each, so subsetting is
    worth attempting -- guarded by a glyph-coverage check with rollback.

    Only the fonts this engine inserted are checked: original document fonts
    (LaTeX CM math faces etc.) use custom encodings that defeat the
    Unicode-based has_glyph probe and would always flag as lost."""
    import fitz

    try:
        snapshot = document.tobytes(garbage=0)
    except Exception as exc:
        warnings.append("Font subsetting skipped (snapshot failed: %s)" % exc)
        return document
    try:
        document.subset_fonts()
    except Exception as exc:
        warnings.append("Font subsetting failed: %s" % exc)
        return document
    if not subset_lost_glyphs(document, inserted_font_names(font_pack)):
        return document
    warnings.append("Font subsetting dropped glyphs; keeping full fonts")
    document.close()
    return fitz.open("pdf", snapshot)


def subset_lost_glyphs(document: object, font_names: Optional[set] = None) -> bool:
    """True when a character drawn in one of `font_names` (normalized names,
    see _normalize_font_name; None = all fonts) lacks a glyph in its embedded
    font."""
    import fitz

    usage: Dict[str, set] = {}
    for page_index in range(document.page_count):
        page_dict = document[page_index].get_text("dict")
        for block in page_dict.get("blocks", []):
            if block.get("type") != 0:
                continue
            for line in block.get("lines", []):
                for span in line.get("spans", []):
                    base = _normalize_font_name(span.get("font", ""))
                    if font_names is not None and base not in font_names:
                        continue
                    usage.setdefault(base, set()).update(span.get("text", ""))

    checked: set = set()
    for page_index in range(document.page_count):
        for font_info in document.get_page_fonts(page_index):
            xref, basefont = font_info[0], font_info[3]
            base = _normalize_font_name(basefont)
            if base in checked:
                continue
            checked.add(base)
            chars = usage.get(base)
            if not chars:
                continue
            try:
                buffer = document.extract_font(xref)[-1]
                if not buffer:
                    continue
                font = fitz.Font(fontbuffer=buffer)
            except Exception:
                continue
            for char in chars:
                if char.strip() and not font.has_glyph(ord(char)):
                    return True
    return False


# --- Fonts -------------------------------------------------------------------


def build_font_pack(font_file: Optional[Path], warnings: List[str]) -> FontPack:
    """Resolve the body/bold/fallback fonts used for inserted Chinese text."""
    import fitz

    fallback_file = find_fallback_font_file()
    fallback_font = fitz.Font(fontfile=str(fallback_file)) if fallback_file else None

    if font_file is not None:
        user_font = fitz.Font(fontfile=str(font_file))
        return FontPack(
            regular=user_font,
            regular_file=Path(font_file),
            bold=user_font,
            bold_file=Path(font_file),
            fallback=fallback_font,
            fallback_file=fallback_file,
            bold_alias="zhbody",
        )

    body_file, bold_file = ensure_font_pack_files(warnings)
    if body_file is None:
        discovered = find_default_font_file()
        if discovered is None:
            raise RuntimeError(
                "No CJK font available. Pass --font-file with a TTF/OTF that covers Chinese."
            )
        warnings.append("Using system CJK font: %s" % discovered)
        body_file = discovered
    if bold_file is None:
        bold_file = body_file

    regular = fitz.Font(fontfile=str(body_file))
    bold = fitz.Font(fontfile=str(bold_file))
    return FontPack(
        regular=regular,
        regular_file=body_file,
        bold=bold,
        bold_file=bold_file,
        fallback=fallback_font,
        fallback_file=fallback_file,
        bold_alias="zhbold" if bold_file != body_file else "zhbody",
    )


def ensure_font_pack_files(warnings: List[str]) -> Tuple[Optional[Path], Optional[Path]]:
    """Extract Songti SC Regular (body) and Hiragino W6 (headings) from the
    system TTC collections into the repo, once."""
    results: List[Optional[Path]] = []
    for destination, ttc_path, face_name in TTC_FACE_SOURCES:
        if destination.is_file():
            results.append(destination)
            continue
        extracted = extract_ttc_face(Path(ttc_path), face_name, destination)
        if extracted is None:
            warnings.append("Could not extract %s from %s" % (face_name, ttc_path))
        results.append(extracted)
    return results[0], results[1]


def extract_ttc_face(ttc_path: Path, face_name: str, destination: Path) -> Optional[Path]:
    if not ttc_path.is_file():
        return None
    try:
        from fontTools.ttLib import TTCollection
    except ImportError:
        return None
    try:
        collection = TTCollection(str(ttc_path), lazy=False)
        for face in collection.fonts:
            name = face["name"].getDebugName(4) or ""
            if name.startswith(face_name):
                destination.parent.mkdir(parents=True, exist_ok=True)
                face.save(str(destination))
                return destination
    except Exception:
        return None
    return None


def find_fallback_font_file() -> Optional[Path]:
    for candidate in FALLBACK_FONT_CANDIDATES:
        path = Path(candidate).expanduser()
        if path.is_file():
            return path
    return None


def find_default_font_file() -> Optional[Path]:
    for candidate in FONT_FILE_CANDIDATES:
        path = Path(candidate).expanduser()
        if path.is_file():
            return path
    return None


def register_font_pack(page: object, pack: FontPack) -> None:
    page.insert_font(fontname=pack.regular_alias, fontfile=str(pack.regular_file))
    if pack.bold_alias != pack.regular_alias:
        page.insert_font(fontname=pack.bold_alias, fontfile=str(pack.bold_file))
    if pack.fallback_file is not None:
        page.insert_font(fontname=pack.fallback_alias, fontfile=str(pack.fallback_file))


TranslationUnit = Tuple[TextBlock, str, Dict[int, str]]


def fragmented_prose_warnings_from_units(units: Sequence[TranslationUnit]) -> List[str]:
    """Flag pages where prose was split into many fixed-width single-line units."""
    page_counts: Dict[int, Tuple[int, int]] = {}
    for block, _, _ in units:
        if block.block_type != "body":
            continue
        body_count, fragmented_count = page_counts.get(block.page_index, (0, 0))
        body_count += 1
        plain = strip_sentinels(block.text)
        if (
            block.nowrap
            and block.source_lines <= 1
            and len(plain.strip()) >= 20
            and not _looks_like_small_fixed_width_table_fragment(block, plain)
        ):
            fragmented_count += 1
        page_counts[block.page_index] = (body_count, fragmented_count)

    warnings: List[str] = []
    for page_index, (body_count, fragmented_count) in sorted(page_counts.items()):
        threshold = max(6, int(body_count * 0.35))
        if fragmented_count >= threshold:
            warnings.append(
                "Page %d: %d body line(s) were treated as fixed-width fragments; "
                "prose may have been translated line-by-line"
                % (page_index + 1, fragmented_count)
            )
    return warnings


def _looks_like_small_fixed_width_table_fragment(block: TextBlock, text: str) -> bool:
    """Small fixed-width cells are usually tables/figure labels, not prose flow."""
    compact = " ".join(text.split())
    if not compact:
        return True
    width = block.bbox[2] - block.bbox[0]
    words = _PROSE_WORD_RE.findall(compact)
    lower = compact.lower()
    if block.font_size <= 8.5:
        return True
    if _looks_like_code_or_symbolic_text(compact) or looks_like_math(compact):
        return True
    if block.font_size <= 10.5 and re.search(
        r"\b(?:prompt template|your task is|please assign|use the following scale|"
        r"output a concise rationale|integer score from|brief explanation|"
        r"target-embodiment match|interaction preservation|scene preservation|"
        r"robotness|naturalness|artifact absence|local coherence)\b",
        lower,
    ):
        return True
    if re.search(r"\b(?:CR1|CR2|ICD|K-NN|BA:|Dataset|Success Rate)\b", compact):
        return True
    if any(marker in compact for marker in ("↔", "✓", "✗")):
        return True
    if width <= 240.0 and len(words) <= 8 and not re.search(r"[.!?。！？]$", compact):
        return True
    if compact.count(",") >= 2 and len(words) <= 24:
        return True
    if block.font_size <= 9.5 and width <= 380.0:
        if re.search(
            r"\b(?:beaker|flask|reagent|cylinder|pipette|centrifuge|thermometer|"
            r"cuvette|crucible|funnel|stirrer|mantle|hygrometer|balance|"
            r"yaml|schema|registry|verifier|constraint|constraints|reachability|"
            r"support table|stage-conditional|table\s+\d+)\b",
            lower,
        ):
            return True
    return False


def prepare_translation_units(
    document: object,
    preserve_graphics_text: bool = False,
) -> Tuple[List[TranslationUnit], Dict[int, List[BBox]], int]:
    """Shared extraction pipeline for both `translate` and `export`.

    Returns (units, gutter_rects, skipped) where each unit carries the block,
    its placeholder-protected text, and the restore mapping.

    Phase 1: Structure analysis — classify blocks by semantic type before translation.
    """
    global _bibliography_ended, _bibliography_heading_size

    raw_blocks, gutter_rects = collect_text_blocks(document)
    graphic_regions = collect_graphic_regions(document) if preserve_graphics_text else {}
    blocks = merge_paragraph_blocks(
        raw_blocks,
        graphic_regions_by_page=graphic_regions if preserve_graphics_text else None,
    )

    # --- Phase 1: Structure-aware classification ---
    _bibliography_seen.clear()
    _bibliography_ended = False
    _bibliography_heading_size = 0.0
    for page_index in range(document.page_count):
        page = document[page_index]
        page_height = page.rect.height
        image_zones = detect_image_zones(page)
        page_blocks = [b for b in blocks if b.page_index == page_index]
        classify_blocks(page_blocks, page_index, page_height, image_zones)

    units: List[TranslationUnit] = []
    skipped = 0
    for block in blocks:
        # Use structure-aware classification
        if not block.should_translate:
            skipped += 1
            continue
        if preserve_graphics_text and should_preserve_original_block(
            block,
            graphic_regions.get(block.page_index, []),
        ):
            skipped += 1
            continue
        plain = strip_sentinels(block.text)
        if not is_translatable(plain):
            skipped += 1
            continue
        protected, mapping = protect_text(block.text)
        bare = PLACEHOLDER_RE.sub("", protected)
        if not re.search(r"[A-Za-z]{2,}", bare):
            skipped += 1
            continue
        units.append((block, protected, mapping))
    return units, gutter_rects, skipped


_REFERENCES_HEADING_RE = re.compile(
    r"^(?:references|bibliography|参考文献)$",
    re.IGNORECASE,
)
_APPENDIX_HEADING_RE = re.compile(r"^(?:appendix\b|appendix[A-Z0-9])", re.IGNORECASE)
_APPENDIX_LETTER_HEADING_RE = re.compile(r"^[A-Z]\.?\s+(.+)$")


def _looks_like_appendix_heading(text: str, source_lines: int = 1) -> bool:
    compact = " ".join(text.split()).strip()
    if not compact:
        return False
    if _APPENDIX_HEADING_RE.match(compact):
        return True
    if source_lines > 2 or len(compact) > 80:
        return False
    if _REFERENCE_ENTRY_RE.search(compact) or _looks_like_reference_entry_text(compact):
        return False
    match = _APPENDIX_LETTER_HEADING_RE.match(compact)
    if not match:
        return False
    heading = match.group(1)
    words = _PROSE_WORD_RE.findall(heading)
    if not words or len(words) > 8:
        return False
    if heading.upper() == heading:
        return True
    significant = [
        word
        for word in words
        if word.lower() not in {"a", "an", "and", "for", "in", "of", "on", "the", "to", "with"}
    ]
    if not significant:
        return False
    title_case_words = sum(1 for word in significant if word[0].isupper())
    return title_case_words >= max(1, len(significant) - 1)


def mark_bibliography_blocks(
    blocks: Sequence[TextBlock], page_heights: Optional[Dict[int, float]] = None
) -> List[bool]:
    """Reference entries keep their original (English) typesetting: translated
    bibliographies are non-standard and reflowing them degrades the layout.

    The range starts after a "References" heading and ends at the next bold
    section heading (appendices). Page footers inside the range are exempt so
    they stay translated like on every other page."""
    page_heights = page_heights or {}
    flags: List[bool] = []
    in_references = False
    heading_size = 0.0
    for block in blocks:
        compact = " ".join(strip_sentinels(block.text).split())
        if _REFERENCES_HEADING_RE.match(compact):
            in_references = True
            heading_size = block.font_size
            flags.append(False)  # the heading itself is translated
            continue
        if in_references and _looks_like_appendix_heading(compact):
            in_references = False
        if in_references and block.bold and block.font_size >= max(heading_size * 0.92, 10.5):
            in_references = False  # next section (appendix) starts
        page_height = page_heights.get(block.page_index, 0.0)
        is_footer = page_height > 0 and block.bbox[1] >= page_height * 0.92
        flags.append(in_references and not is_footer)
    return flags


def collect_graphic_regions(document: object) -> Dict[int, List[BBox]]:
    """Find page regions that contain figures, vector diagrams, tables, or images."""
    regions: Dict[int, List[BBox]] = {}
    for page_index in range(document.page_count):
        page_regions = graphic_regions_for_page(document[page_index])
        if page_regions:
            regions[page_index] = page_regions
    return regions


def graphic_regions_for_page(page: object) -> List[BBox]:
    try:
        import fitz
    except ImportError as exc:
        raise RuntimeError("PyMuPDF is required. Install with: pip install -e .") from exc

    page_rect = page.rect
    candidates: List[BBox] = []
    for raw_block in page.get_text("dict").get("blocks", []):
        if raw_block.get("type") == 0 or "bbox" not in raw_block:
            continue
        bbox = tuple(float(value) for value in raw_block["bbox"])
        if bbox_is_graphic_candidate(bbox, page_rect):
            candidates.append(bbox)

    try:
        drawings = page.get_drawings()
    except Exception:
        drawings = []
    for drawing in drawings:
        rect = drawing.get("rect")
        if not isinstance(rect, fitz.Rect):
            continue
        bbox = (float(rect.x0), float(rect.y0), float(rect.x1), float(rect.y1))
        if _looks_like_page_background_rule(bbox, page_rect):
            continue
        if bbox_is_graphic_candidate(bbox, page_rect):
            candidates.append(bbox)

    merged = merge_nearby_bboxes(candidates, GRAPHIC_REGION_CLUSTER_GAP)
    output: List[BBox] = []
    for bbox in merged:
        if bbox_area(bbox) < GRAPHIC_REGION_MIN_AREA:
            continue
        output.append(expand_bbox_to_page(bbox, GRAPHIC_REGION_PADDING, page_rect))
    return output


def bbox_is_graphic_candidate(bbox: BBox, page_rect: object) -> bool:
    page_width = float(page_rect.width)
    page_height = float(page_rect.height)
    if bbox[2] <= 0 or bbox[0] >= page_width or bbox[3] <= 0 or bbox[1] >= page_height:
        return False
    width = bbox[2] - bbox[0]
    height = bbox[3] - bbox[1]
    if width < GRAPHIC_REGION_MIN_SIDE or height < GRAPHIC_REGION_MIN_SIDE:
        return False
    if bbox_area(bbox) < GRAPHIC_REGION_MIN_AREA:
        return False
    page_area = max(page_width * page_height, 1.0)
    return bbox_area(bbox) <= page_area * 0.85


def _looks_like_page_background_rule(bbox: BBox, page_rect: object) -> bool:
    page_width = max(float(page_rect.width), 1.0)
    page_height = max(float(page_rect.height), 1.0)
    width = bbox[2] - bbox[0]
    height = bbox[3] - bbox[1]
    return (
        width >= page_width * 0.90
        and height <= max(14.0, page_height * 0.025)
        and (bbox[0] <= page_width * 0.02 or bbox[2] >= page_width * 0.98)
    )


def merge_nearby_bboxes(bboxes: Sequence[BBox], gap: float) -> List[BBox]:
    merged = list(bboxes)
    changed = True
    while changed:
        changed = False
        next_round: List[BBox] = []
        while merged:
            current = merged.pop(0)
            index = 0
            while index < len(merged):
                if bboxes_intersect(expand_bbox(current, gap), merged[index]):
                    current = union_bbox([current, merged.pop(index)])
                    changed = True
                else:
                    index += 1
            next_round.append(current)
        merged = next_round
    return merged


def should_preserve_original_block(block: TextBlock, graphic_regions: Sequence[BBox]) -> bool:
    """Conservative-mode filter: keep figure/table internals and formulas untouched."""
    plain = " ".join(strip_sentinels(block.text).split())
    if not plain:
        return True
    if looks_like_vertical_margin_metadata(block, plain):
        return True
    if block.block_type == "caption" or _CAPTION_RE.match(plain):
        return False
    if block.block_type == "heading":
        return False
    if block.block_type == "table" and block.nowrap and block.no_merge:
        return True
    if looks_like_preserved_diagram_label(plain):
        return True
    if block_overlaps_graphic_region(block.bbox, graphic_regions):
        if looks_like_translatable_graphic_prose(block, plain):
            return False
        return True
    if math_heavy_block(block):
        return True
    if short_graphic_label_block(block, plain):
        return True
    return False


def looks_like_preserved_diagram_label(plain: str) -> bool:
    """Short labels inside architecture figures stay in their original language.

    Captions still translate; this only covers diagram-internal labels such as
    "(i) Object Head (ii) Skill Head (iii) Depth Head".
    """
    compact = " ".join(strip_sentinels(plain).split()).strip()
    if not compact or len(compact) > 120:
        return False
    if _CAPTION_RE.match(compact):
        return False
    if re.search(r"[.!?:。！？：]$", compact):
        return False
    without_markers = re.sub(r"\(?[ivx]{1,4}\)\s*", " ", compact, flags=re.IGNORECASE)
    without_markers = re.sub(
        rf"\b(Head)(?={_DIAGRAM_HEAD_LABEL_WORDS}\s+Head\b)",
        r"\1 ",
        without_markers,
        flags=re.IGNORECASE,
    )
    normalized = " ".join(without_markers.split())
    if re.fullmatch(
        rf"(?:(?:{_DIAGRAM_HEAD_LABEL_WORDS})\s+Head\s*){{1,4}}",
        normalized,
        re.IGNORECASE,
    ):
        return True
    if re.fullmatch(
        rf"(?:(?:{_DIAGRAM_MEMORY_LABEL_WORDS})\s+Memory\s*){{1,5}}",
        normalized,
        re.IGNORECASE,
    ):
        return True
    if re.fullmatch(
        r"(?:(?:Action|Video|World|Policy|Vision|Language)\s+Expert\s*){1,4}",
        normalized,
        re.IGNORECASE,
    ):
        return True
    return False


def looks_like_vertical_margin_metadata(block: TextBlock, plain: str) -> bool:
    width = block.bbox[2] - block.bbox[0]
    height = block.bbox[3] - block.bbox[1]
    if width > 45.0 or height < 80.0:
        return False
    compact = " ".join(plain.split())
    return bool(re.search(r"\barxiv\b|\[[a-z]{2}\.[a-z]{2}\]", compact, re.IGNORECASE))


def looks_like_translatable_graphic_prose(block: TextBlock, plain: str) -> bool:
    if block.nowrap:
        return False
    if _ACADEMIC_BOX_PROSE_RE.match(plain):
        return True
    if _ENUMERATED_ACADEMIC_BOX_RE.match(plain):
        return True
    if block.source_lines < 2:
        return False
    prose_words = substantial_prose_word_count(plain)
    if prose_words < 8:
        return False
    if prose_words >= 20:
        return True
    if _looks_like_code_or_symbolic_text(plain):
        return False
    return True


def block_overlaps_graphic_region(bbox: BBox, regions: Sequence[BBox]) -> bool:
    area = max(bbox_area(bbox), 1.0)
    center_x = (bbox[0] + bbox[2]) / 2.0
    center_y = (bbox[1] + bbox[3]) / 2.0
    for region in regions:
        if bbox_contains_point(region, center_x, center_y):
            return True
        if bbox_intersection_area(bbox, region) / area >= GRAPHIC_BLOCK_OVERLAP_RATIO:
            return True
    return False


def bbox_crosses_graphic_region(bbox: BBox, regions: Sequence[BBox]) -> bool:
    return any(bboxes_intersect(bbox, region) for region in regions)


def math_heavy_block(block: TextBlock) -> bool:
    bare = strip_sentinels(block.text)
    compact = "".join(bare.split())
    if not compact:
        return False
    if substantial_prose_word_count(bare) >= 5:
        return False
    if looks_like_math(bare):
        return True
    math_chars = sentinel_char_count(block.text)
    if not math_chars:
        return False
    ratio = math_chars / len(compact)
    if ratio >= MATH_HEAVY_RATIO:
        return True
    return block.source_lines <= 2 and ratio >= MATH_SHORT_BLOCK_RATIO


def short_graphic_label_block(block: TextBlock, plain: str) -> bool:
    words = _PROSE_WORD_RE.findall(plain)
    return (
        block.font_size <= 8.5
        and block.source_lines <= 2
        and len(plain) <= 80
        and len(words) <= 6
    )


def substantial_prose_word_count(text: str) -> int:
    return sum(
        1 for word in _PROSE_WORD_RE.findall(text) if word.lower() not in _MATH_WORDS
    )


def collect_text_blocks(document: object) -> Tuple[List[TextBlock], Dict[int, List[BBox]]]:
    """Extract text blocks and the bboxes of stripped gutter line numbers.

    Display-equation regions (clusters of raw blocks holding big operators,
    sub/superscript lines, equation numbers, ...) are detected geometrically
    and excluded entirely: their original typesetting is preserved.
    """
    blocks: List[TextBlock] = []
    gutter_rects: Dict[int, List[BBox]] = {}
    for page_index in range(document.page_count):
        page = document[page_index]
        page_width = float(page.rect.width)
        page_dict = page.get_text("dict")
        records: List[_RawBlockRec] = []
        for raw_block in page_dict.get("blocks", []):
            if raw_block.get("type") != 0:
                continue
            record, dropped = parse_block_lines(raw_block, page_width=page_width)
            if dropped:
                gutter_rects.setdefault(page_index, []).extend(dropped)
            if record is not None:
                records.append(record)
        equation_flags = mark_equation_blocks(records)
        formula_lines = [
            line
            for record, is_equation in zip(records, equation_flags)
            if is_equation
            for line in record.lines
            if not line_is_prose(line)
        ]
        for record, is_equation in zip(records, equation_flags):
            if record_is_algorithm(record):
                continue
            record_segments = segments_from_record(
                page_index, record, equation_record=is_equation
            )
            for block in record_segments:
                redacts = block.redact_bboxes or [block.bbox]
                trimmed_redacts = [
                    trim_redact_bbox_against_formula_lines(redact, formula_lines)
                    for redact in redacts
                ]
                if trimmed_redacts != redacts:
                    block.redact_bboxes = trimmed_redacts
            blocks.extend(record_segments)
    return blocks, gutter_rects


def detect_image_zones(page: object) -> List[BBox]:
    """Detect visual regions and extend them to include nearby captions.

    Returns a list of bboxes representing figure+caption zones.
    """
    zones: List[BBox] = []
    for img in page.get_images():
        try:
            rect = page.get_image_bbox(img)
            if rect and rect.is_empty is False and rect.is_valid:
                # Extend zone downward to include nearby captions
                pad = _IMAGE_ZONE_CAPTION_GAP
                zones.append((
                    rect.x0 - pad,
                    rect.y0,
                    rect.x1 + pad,
                    rect.y1 + pad * 3,
                ))
        except Exception:
            continue
    zones.extend(graphic_regions_for_page(page))
    if not zones:
        return []
    return merge_nearby_bboxes(zones, _IMAGE_ZONE_CAPTION_GAP)


def detect_columns(blocks: List[TextBlock]) -> List[Tuple[float, float]]:
    """Detect single/two-column layout from block x0 positions.

    Returns list of (left_margin, col_width) tuples, sorted by left_margin.
    """
    import statistics as _stats

    x0_weighted: Dict[float, float] = {}
    x0_to_widths: Dict[float, List[float]] = {}

    for block in blocks:
        if block.block_type in ("equation", "algorithm", "footer"):
            continue
        x0, y0, x1, y1 = block.bbox
        width = x1 - x0
        if width < 30:
            continue
        text_len = len(block.text.strip())
        x0_rounded = round(x0, 0)
        x0_weighted[x0_rounded] = x0_weighted.get(x0_rounded, 0) + text_len
        x0_to_widths.setdefault(x0_rounded, []).append(round(width, 0))

    if not x0_weighted:
        return []

    sorted_x0s = sorted(x0_weighted, key=x0_weighted.get, reverse=True)
    primary_x0 = sorted_x0s[0]

    # Look for a second column > _COLUMN_CLUSTER_GAP away
    for x0 in sorted_x0s[1:]:
        if abs(x0 - primary_x0) > _COLUMN_CLUSTER_GAP:
            columns = []
            for x in [primary_x0, x0]:
                widths = x0_to_widths[x]
                full_widths = [w for w in widths if w > 300]
                widths_to_use = full_widths or widths
                col_w = float(_stats.mode(widths_to_use))
                columns.append((float(x), col_w))
            columns.sort(key=lambda c: c[0])
            return columns

    # Single column
    all_widths = [w for ws in x0_to_widths.values() for w in ws]
    full_widths = [w for w in all_widths if w > 300]
    widths_to_use = full_widths or all_widths
    col_width = float(_stats.mode(widths_to_use))
    return [(float(primary_x0), col_width)]


def _block_in_zone(bbox: BBox, zone: BBox) -> bool:
    """Check if a block's bbox overlaps significantly with a zone."""
    bx0, by0, bx1, by1 = bbox
    zx0, zy0, zx1, zy1 = zone
    ox0 = max(bx0, zx0)
    oy0 = max(by0, zy0)
    ox1 = min(bx1, zx1)
    oy1 = min(by1, zy1)
    overlap_area = max(0, ox1 - ox0) * max(0, oy1 - oy0)
    block_area = max(1, (bx1 - bx0) * (by1 - by0))
    return overlap_area / block_area > 0.3


def classify_blocks(
    blocks: List[TextBlock],
    page_index: int,
    page_height: float,
    image_zones: List[BBox],
) -> None:
    """Classify blocks by semantic type. Modifies blocks in-place.

    Priority order:
    1. Equation zone → equation (should_translate=False)
    2. Algorithm → algorithm (should_translate=False)
    3. Inside image zone + short label → figure_label (should_translate=False)
    4. Caption pattern → caption (preserve_position=True)
    5. Bold + numbered + short → heading
    6. Bibliography → bibliography (should_translate=False)
    7. Footer → footer (should_translate=False)
    8. Otherwise → body
    """
    for block in blocks:
        text = block.text.strip()
        x0, y0, x1, y1 = block.bbox

        if block.block_type == "table" and block.nowrap and block.no_merge:
            block.should_translate = True
            continue

        # Already classified as equation by native engine
        if getattr(block, "_equation_zone", False):
            block.block_type = "equation"
            block.should_translate = False
            continue

        if looks_like_preserved_diagram_label(text):
            block.block_type = "figure_label"
            block.should_translate = False
            block.preserve_position = True
            continue

        # Inside image zone
        in_image = any(_block_in_zone(block.bbox, z) for z in image_zones)
        if in_image:
            if len(text) <= _FIGURE_LABEL_MAX_LEN and not _CAPTION_RE.match(text):
                block.block_type = "figure_label"
                block.should_translate = False
                block.preserve_position = True
                continue
            # Caption inside image zone — still translate but preserve position
            if _CAPTION_RE.match(text):
                block.block_type = "caption"
                block.preserve_position = True
                continue

        # Caption pattern (Figure N, Table N, etc.)
        if _CAPTION_RE.match(text):
            block.block_type = "caption"
            block.preserve_position = True
            continue

        # Bibliography: after "References" heading
        if _is_bibliography_context(block, blocks):
            block.block_type = "bibliography"
            block.should_translate = False
            continue

        if block.block_type == "heading":
            block.should_translate = True
            block.bold = True
            block.no_merge = True
            continue

        # Heading: bold + numbered + short
        if block.bold and len(text) < 100 and _HEADING_RE.match(text):
            block.block_type = "heading"
            continue

        # Footer: near bottom of page or page number
        if y0 > page_height - _FOOTER_MAX_Y_OFFSET:
            if _FOOTER_PAGE_NUM_RE.match(text) or len(text) < 30:
                block.block_type = "footer"
                block.should_translate = False
                continue

        # Default
        block.block_type = "body"
        block.should_translate = True


# Track whether we've seen a "References" heading per page
_bibliography_seen: Dict[int, bool] = {}
_bibliography_heading_size: float = 0.0
_bibliography_ended: bool = False


def _is_bibliography_context(block: TextBlock, all_blocks: List[TextBlock]) -> bool:
    """Check if block is in the bibliography section.

    Bibliography starts after a "References" heading and ends at the next
    appendix/supplement heading.
    """
    global _bibliography_heading_size, _bibliography_ended
    raw_text = block.text.strip()
    text = raw_text.lower()

    # Check if this block IS the references heading
    if re.match(r"^(references|bibliography|参考文献)\s*$", text, re.IGNORECASE):
        _bibliography_seen[block.page_index] = True
        _bibliography_heading_size = block.font_size
        _bibliography_ended = False
        return False  # The heading itself is translatable

    # Check if a new appendix/supplement heading ends the bibliography range.
    if _bibliography_seen and _looks_like_appendix_heading(raw_text, block.source_lines):
        _bibliography_ended = True

    # Fallback: bold section headings also end the bibliography range.
    bib_size_threshold = max(_bibliography_heading_size * 0.92, 10.5)
    if _bibliography_seen and block.bold and block.font_size >= bib_size_threshold:
        if _SECTION_NUM_RE.match(raw_text) or len(raw_text) < 60:
            _bibliography_ended = True

    if _bibliography_ended:
        return False

    # Check if we've seen references heading on a previous page or earlier on this page
    if _bibliography_seen.get(block.page_index, False):
        return True
    # Check previous pages
    for pi in range(block.page_index):
        if _bibliography_seen.get(pi, False):
            return True
    return False


@dataclass
class _LineRec:
    text: str  # sentinel-annotated line text
    bbox: BBox
    spans: List[dict]  # kept spans (gutter/empty spans removed)
    is_cell: bool = False  # piece of a physical line split at column gaps


@dataclass
class _RawBlockRec:
    lines: List[_LineRec]

    @property
    def bbox(self) -> BBox:
        return union_bbox([line.bbox for line in self.lines])

    def bare_text(self) -> str:
        return strip_sentinels(" ".join(line.text for line in self.lines))

    def compact_length(self) -> int:
        return len("".join(self.bare_text().split()))

    def sentinel_ratio(self) -> float:
        compact = self.compact_length()
        if compact == 0:
            return 1.0
        inside = sum(sentinel_char_count(line.text) for line in self.lines)
        return inside / compact


def _span_is_isolated(span: dict, siblings: Sequence[dict]) -> bool:
    """True when no other non-empty span sits horizontally adjacent to `span`.

    Formula sub/superscripts always touch their base glyphs; gutter line
    numbers are standalone text objects with clear air around them."""
    bbox = span.get("bbox")
    if not bbox:
        return True
    x0, y0, x1, y1 = (float(value) for value in bbox)
    for other in siblings:
        if other is span or "bbox" not in other:
            continue
        if not normalize_span_text(other.get("text", "")).strip():
            continue
        ox0, oy0, ox1, oy1 = (float(value) for value in other["bbox"])
        if min(y1, oy1) - max(y0, oy0) < -1.0:
            continue
        gap = max(ox0 - x1, x0 - ox1)
        if gap < LINE_NUMBER_NEIGHBOR_GAP:
            return False
    return True


def parse_block_lines(
    raw_block: dict, page_width: float | None = None
) -> Tuple[Optional[_RawBlockRec], List[BBox]]:
    """First pass: clean one raw PyMuPDF block into annotated physical lines.

    Gutter line numbers and prompt-injection lines are dropped here (their
    rects are returned for erasure)."""
    dropped_rects: List[BBox] = []
    lines: List[_LineRec] = []

    for raw_line in raw_block.get("lines", []):
        spans = raw_line.get("spans", [])
        line_max_size = 0.0
        non_empty_span_indexes: List[int] = []
        for span_index, span in enumerate(spans):
            if normalize_span_text(span.get("text", "")).strip():
                non_empty_span_indexes.append(span_index)
                line_max_size = max(line_max_size, float(span.get("size", 0.0)))
        last_non_empty_span = non_empty_span_indexes[-1] if non_empty_span_indexes else -1

        fragments: List[Tuple[str, dict]] = []
        for span_index, span in enumerate(spans):
            span_text = normalize_span_text(span.get("text", ""))
            if not span_text.strip():
                if fragments and span_index < last_non_empty_span:
                    fragments.append((span_text, span))
                continue
            span_size = float(span.get("size", line_max_size))
            isolated = _span_is_isolated(span, spans)
            if is_line_number_span(
                span_text, span_size, line_max_size, isolated=isolated
            ) or _is_margin_line_number_span(span_text, span, span_size, page_width, isolated):
                if "bbox" in span:
                    dropped_rects.append(tuple(float(x) for x in span["bbox"]))
                continue
            span_flags = int(span.get("flags", 0))
            if is_math_span(span.get("font", ""), span_flags, span_text, span_size, line_max_size):
                fragment = span_text.strip()
                if span_flags & 1 and span_size < line_max_size * 0.85:
                    fragment = fragment.translate(SUPERSCRIPT_MAP)
                fragments.append((SENTINEL_OPEN + fragment + SENTINEL_CLOSE, span))
            else:
                fragments.append((span_text, span))
        if not fragments:
            continue
        full_text = "".join(part for part, _ in fragments).strip()
        if not full_text:
            continue
        if is_injection_text(strip_sentinels(full_text)):
            for _, span in fragments:
                if "bbox" in span:
                    dropped_rects.append(tuple(float(x) for x in span["bbox"]))
            continue
        for group in split_line_cells(fragments, line_max_size):
            text = "".join(part for part, _ in group).strip()
            if not text:
                continue
            group_spans = [span for _, span in group]
            boxes = [
                tuple(float(x) for x in span["bbox"]) for span in group_spans if "bbox" in span
            ]
            if boxes:
                bbox = union_bbox(boxes)
            else:
                bbox = tuple(float(x) for x in raw_line.get("bbox", (0, 0, 0, 0)))
            lines.append(
                _LineRec(
                    text=text,
                    bbox=bbox,
                    spans=group_spans,
                    is_cell=len(group) < len(fragments),
                )
            )

    if not lines:
        return None, dropped_rects
    return _RawBlockRec(lines=lines), dropped_rects


# Horizontal span gap (in units of line font size) that separates table cells.
CELL_GAP_FACTOR = 1.6
CELL_GAP_MIN = 8.0


def split_line_cells(
    fragments: Sequence[Tuple[str, dict]], line_max_size: float
) -> List[List[Tuple[str, dict]]]:
    """Split one physical line into cell groups at large horizontal gaps."""
    threshold = max(CELL_GAP_FACTOR * max(line_max_size, 1.0), CELL_GAP_MIN)
    groups: List[List[Tuple[str, dict]]] = []
    current: List[Tuple[str, dict]] = []
    previous_end: Optional[float] = None
    for fragment in fragments:
        bbox = fragment[1].get("bbox")
        start = float(bbox[0]) if bbox else None
        gap = (
            current
            and previous_end is not None
            and start is not None
            and start - previous_end > threshold
        )
        if gap:
            groups.append(current)
            current = []
        current.append(fragment)
        if bbox:
            previous_end = float(bbox[2])
    if current:
        groups.append(current)
    return groups


# Big-operator / oddball glyphs that only occur inside display equations.
_BIG_OPERATOR_CHARS = set(
    "\u2211\u220f\u222b\u221a\u222c\u222d\u22c0\u22c1\u22c2\u22c3\u2a01\u2a02\u2a04\u2a06"
)  # noqa: E501
_ENGLISH_WORD_RE = re.compile(r"[a-z]{3,}")
# Limits for a small block to be absorbed into a neighbouring equation zone.
EQUATION_NEIGHBOR_MAX_CHARS = 60
EQUATION_NEIGHBOR_MAX_LINES = 5
EQUATION_NEIGHBOR_GAP = 9.0
EQUATION_NEIGHBOR_SIDE_GAP = 48.0


def block_is_strong_math(record: _RawBlockRec) -> bool:
    """Blocks that are unambiguously display-equation material."""
    for line in record.lines:
        compact = "".join(strip_sentinels(line.text).split())
        if compact and EQUATION_NUMBER_RE.fullmatch(compact):
            return True
        if any(ord(char) < 32 or char in _BIG_OPERATOR_CHARS for char in compact):
            return True
    if record.sentinel_ratio() >= 0.5:
        return True
    # Two physical lines overlapping vertically = 2D math layout (sub/sup).
    # Requires real math content: section headings ("3.4" + title) also arrive
    # as two side-by-side "lines" but carry no math-font spans.
    if record.sentinel_ratio() >= 0.05:
        for first, second in zip(record.lines, record.lines[1:]):
            overlap = min(first.bbox[3], second.bbox[3]) - max(first.bbox[1], second.bbox[1])
            if overlap >= 1.5:
                return True
    return bool(looks_like_math(record.bare_text()))


def block_is_equation_neighbor(record: _RawBlockRec) -> bool:
    """Small math-ish fragments (LHS, sub/superscript limits) that belong to a
    nearby equation zone but are not strong on their own."""
    if len(record.lines) > EQUATION_NEIGHBOR_MAX_LINES:
        return False
    if record.compact_length() > EQUATION_NEIGHBOR_MAX_CHARS:
        return False
    bare = record.bare_text()
    if record.sentinel_ratio() >= 0.2:
        return True
    words = _ENGLISH_WORD_RE.findall(bare)
    if not words:
        return True
    return any(char in MATH_SYMBOLS for char in bare)


def _zone_adjacent(candidate: BBox, zone: BBox) -> bool:
    horizontal_overlap = min(candidate[2], zone[2]) - max(candidate[0], zone[0])
    vertical_overlap = min(candidate[3], zone[3]) - max(candidate[1], zone[1])
    if horizontal_overlap > 0:
        gap = -vertical_overlap
        return gap <= EQUATION_NEIGHBOR_GAP
    if vertical_overlap > 0:
        # Same line band (e.g. "E(T) =" to the left of a tall sum sign).
        gap = -horizontal_overlap
        return gap <= EQUATION_NEIGHBOR_SIDE_GAP
    return False


def mark_equation_blocks(records: Sequence[_RawBlockRec]) -> List[bool]:
    """Flag raw blocks belonging to display-equation zones.

    Strong math blocks seed the zones; small math-ish neighbours touching a
    zone are absorbed iteratively. Long text paragraphs can never be absorbed.
    """
    flags = [block_is_strong_math(record) for record in records]
    candidates = [block_is_equation_neighbor(record) for record in records]
    changed = True
    while changed:
        changed = False
        for index, record in enumerate(records):
            if flags[index] or not candidates[index]:
                continue
            bbox = record.bbox
            for other_index, other in enumerate(records):
                if not flags[other_index] or other_index == index:
                    continue
                if _zone_adjacent(bbox, other.bbox):
                    flags[index] = True
                    changed = True
                    break
    return flags


_PSEUDOCODE_STEP_RE = re.compile(r"\b\d{1,2}:\s*\S")
_ALGORITHM_TITLE_RE = re.compile(r"^\s*Algorithm\s+\d+\b", re.IGNORECASE)
_ALGORITHM_IO_RE = re.compile(r"\b(?:Require|Ensure|Input|Output)\s*:", re.IGNORECASE)
_CODE_FONT_RE = re.compile(
    r"(?:Inconsolata|Courier|Mono|Typewriter|Consolas|Menlo|CMTT|CMRTypewriter)",
    re.IGNORECASE,
)
_CODE_LINE_RE = re.compile(
    r"^\s*(?:@\w+|def\s+\w+\s*\(|class\s+\w+\s*[:(]|return\b|yield\b|"
    r"if\b|elif\b|else\s*:|for\b|while\b|with\b|try\s*:|except\b|finally\s*:|"
    r"import\s+[A-Za-z_]|from\s+[A-Za-z_]|#|[A-Za-z_]\w*\s*(?:=|\+=|-=|:=))"
)


def line_has_code_font(line: _LineRec) -> bool:
    return any(_CODE_FONT_RE.search(str(span.get("font", ""))) for span in line.spans)


def line_is_code_like(line: _LineRec) -> bool:
    compact = " ".join(strip_sentinels(line.text).split()).strip()
    if not compact:
        return False
    if _PSEUDOCODE_STEP_RE.match(compact):
        return True
    if _CODE_LINE_RE.match(compact):
        return True
    if line_has_code_font(line):
        if re.search(r"(?:->|=>|==|!=|<=|>=|\+=|-=|:=|=|\(|\)|\[|\]|:)", compact):
            return True
    return False


def record_is_algorithm(record: _RawBlockRec) -> bool:
    """Algorithm floats (numbered pseudocode) keep their original typesetting:
    reflowing '1: x <- F(y)' statements as prose destroys them."""
    bare = record.bare_text()
    compact = " ".join(bare.split()).strip()
    if _ALGORITHM_TITLE_RE.match(compact):
        return True
    if _ALGORITHM_IO_RE.search(compact) and len(record.lines) <= 12:
        return True

    steps = len(_PSEUDOCODE_STEP_RE.findall(bare))
    if steps >= 1:
        has_arrow = "\u2190" in bare or "\u27f5" in bare or "\u21d0" in bare
        if has_arrow:
            return True
        if steps >= 3 and _ALGORITHM_IO_RE.search(bare):
            return True

    code_lines = [line for line in record.lines if line_is_code_like(line)]
    if len(code_lines) < 2:
        return False
    code_font_lines = sum(1 for line in code_lines if line_has_code_font(line))
    has_function_signature = any(
        re.match(r"^\s*(?:def|class)\s+\w+", strip_sentinels(line.text))
        for line in code_lines
    )
    return code_font_lines >= 2 or has_function_signature


def record_is_table(record: _RawBlockRec) -> bool:
    """Table blocks expose cells as separate physical lines sharing a y-band.

    Sequential paragraph lines never overlap vertically; two-line headings
    ("3.4" + title) are excluded by the minimum line count. Some PDFs also split
    a normal prose line into two same-baseline text objects; those have only a
    small horizontal gap and must stay mergeable as paragraph text."""
    lines = record.lines
    if len(lines) < 3:
        return False
    table_rows = 0
    for row in group_same_y_lines(lines):
        if row_has_table_gap(row):
            table_rows += 1
        if table_rows >= 2:
            return True
    return record_is_single_row_table(record)


def record_is_single_row_table(record: _RawBlockRec) -> bool:
    """Single physical table rows are common for headers and summary rows.

    They do not have the two same-y rows required by the stronger table
    detector, so we only accept short, multi-cell rows containing numeric
    values or table-header vocabulary.
    """
    rows = [row for row in group_same_y_lines(record.lines) if row_has_table_gap(row)]
    if len(rows) != 1:
        return False
    row = rows[0]
    if len(row) < 4:
        return False
    texts = [" ".join(strip_sentinels(line.text).split()) for line in row]
    if any(not text for text in texts):
        return False
    if any(len(text) > 42 for text in texts):
        return False
    joined = " ".join(texts)
    if re.search(r"(?<!\d)[.!?](?!\d)|[。！？]", joined):
        return False
    numeric_cells = sum(
        1
        for text in texts
        if re.search(r"(?:\d+(?:\.\d+)?\s*%|\d+\s*/\s*\d+|^\d+(?:\.\d+)?$)", text)
    )
    if numeric_cells >= max(2, len(row) // 2):
        return True
    header_terms = re.compile(
        r"\b(?:task|method|model|dataset|metric|average|avg|ours|baseline|"
        r"attention|frames?|tokens?|window|success|rate|score|accuracy|"
        r"precision|recall|f1|w/o|without)\b",
        re.IGNORECASE,
    )
    return bool(header_terms.search(joined))


def group_same_y_lines(lines: Sequence[_LineRec]) -> List[List[_LineRec]]:
    rows: List[List[_LineRec]] = []
    for line in sorted(lines, key=lambda item: ((item.bbox[1] + item.bbox[3]) / 2.0, item.bbox[0])):
        for row in rows:
            if any(lines_share_y_band(line, existing) for existing in row):
                row.append(line)
                break
        else:
            rows.append([line])
    return rows


def lines_share_y_band(first: _LineRec, second: _LineRec) -> bool:
    first_height = first.bbox[3] - first.bbox[1]
    second_height = second.bbox[3] - second.bbox[1]
    min_height = min(first_height, second_height)
    if min_height <= 0:
        return False
    overlap = min(first.bbox[3], second.bbox[3]) - max(first.bbox[1], second.bbox[1])
    return overlap >= 0.5 * min_height


def row_has_table_gap(row: Sequence[_LineRec]) -> bool:
    if len(row) < 2:
        return False
    if any(line.is_cell for line in row):
        return True
    ordered = sorted(row, key=lambda line: line.bbox[0])
    heights = [line.bbox[3] - line.bbox[1] for line in ordered]
    threshold = max(CELL_GAP_MIN, median_or_default(heights, 10.0) * CELL_GAP_FACTOR)
    for previous, current in zip(ordered, ordered[1:]):
        gap = current.bbox[0] - previous.bbox[2]
        if gap >= threshold:
            return True
    return False


# Function names and math keywords that appear as words inside formulas;
# they never make a line prose on their own.
_MATH_WORDS = frozenset(
    "sin cos tan exp log min max arg sup inf lim det diag clip tr kl "
    "softmax argmax argmin var cov std relu prox sgn span rank dim mod".split()
)
_PROSE_WORD_RE = re.compile(r"[A-Za-z]{3,}")
_SHORT_PROSE_WORDS = frozenset(
    "a an and are as at be by for if in is it of on or the to was were when where with".split()
)
_FORMULA_CONTEXT_WORDS = frozenset(
    "fig figure table panel top bottom left right middle row column".split()
)
_FORMULA_PROSE_FRAGMENT_CUE_RE = re.compile(
    r"(?i)(?:\bproof\b|\bwhere\w*|\bif\w*|\bthen\b|\bhence\b|\btherefore\b|"
    r"\bimplies?\b|\bestablish(?:es|ed)?\b|\bnote\b|\bbelow\b|\bfinally\b|"
    r"\brecall\b|\bdefine[sd]?\b|\bbetween\w*|\brestricted\b|\bpushforward\b|"
    r"\binequality\b|\bby\w*|\bwe\s*(?:have|use)\b|\bhave\w*|\bfor\s*all\w*|"
    r"\bsuch\s*that\b|\bto\s*be\b)"
)
_FORMULA_PREFIX_PROSE_TAIL_RE = re.compile(
    r"(?i)^[\s\.,;:)]+(?:as|hence|therefore|thus|consequently|moreover|then|so)\b"
)


def _text_outside_sentinels(text: str) -> str:
    outside: List[str] = []
    inside = False
    for char in text:
        if char == SENTINEL_OPEN:
            inside = True
        elif char == SENTINEL_CLOSE:
            inside = False
        elif not inside:
            outside.append(char)
    return "".join(outside)


def line_has_translatable_formula_tail(line: _LineRec) -> bool:
    """Math-heavy line that still carries prose outside the formula spans."""
    bare = strip_sentinels(line.text)
    compact = "".join(bare.split())
    if not compact:
        return False
    inside = sentinel_char_count(line.text)
    if inside < 2 or inside / len(compact) >= 0.55:
        return False
    outside = _text_outside_sentinels(line.text)
    words = [
        word
        for word in _PROSE_WORD_RE.findall(outside)
        if word.lower()
        not in _MATH_WORDS | _SHORT_PROSE_WORDS | _FORMULA_CONTEXT_WORDS
    ]
    return len(words) >= 2


def line_has_short_formula_prose_fragment(line: _LineRec) -> bool:
    """Short prose connectors split away from nearby formula spans.

    Math-heavy PDFs often split phrases like ``between T and T' to be`` or
    ``we have`` into their own physical line. They are too short for the normal
    prose detector but must still be translated and redacted with the sentence.
    """
    bare = strip_sentinels(line.text)
    compact = "".join(bare.split())
    if not compact or EQUATION_NUMBER_RE.fullmatch(compact):
        return False
    if any(ord(char) < 32 or char in _BIG_OPERATOR_CHARS for char in compact):
        return False
    if sentinel_char_count(line.text) / len(compact) >= 0.80:
        return False
    words = [word for word in _PROSE_WORD_RE.findall(bare) if word.lower() not in _MATH_WORDS]
    if not words:
        return False
    return bool(_FORMULA_PROSE_FRAGMENT_CUE_RE.search(bare))


def line_formula_prefix_prose_tail(line: _LineRec) -> Optional[_LineRec]:
    """Return the prose tail when a physical line starts with display math.

    Fraction layouts sometimes produce a line such as ``2D >= c. As`` whose
    formula prefix vertically overlaps a neighbouring numerator. Redacting the
    whole line erases the numerator; only the prose tail should be translated.
    """
    if len(line.spans) < 2:
        return None
    line_width = line.bbox[2] - line.bbox[0]
    if line.bbox[0] < 300.0 or line_width > 140.0:
        return None
    span_sizes = [float(span.get("size", 0.0)) for span in line.spans]
    line_max_size = max(span_sizes) if span_sizes else 0.0
    for index in range(1, len(line.spans)):
        tail_spans = list(line.spans[index:])
        tail_text = "".join(normalize_span_text(span.get("text", "")) for span in tail_spans)
        if not _FORMULA_PREFIX_PROSE_TAIL_RE.search(tail_text):
            continue
        prefix_spans = line.spans[:index]
        prefix_text = "".join(normalize_span_text(span.get("text", "")) for span in prefix_spans)
        prefix_has_math = any(
            is_math_span(
                span.get("font", ""),
                int(span.get("flags", 0)),
                normalize_span_text(span.get("text", "")),
                float(span.get("size", line_max_size)),
                line_max_size,
            )
            for span in prefix_spans
        ) or any(char in MATH_SYMBOLS for char in prefix_text)
        if not prefix_has_math:
            continue
        boxes = [
            tuple(float(value) for value in span["bbox"]) for span in tail_spans if "bbox" in span
        ]
        if not boxes:
            continue
        leading_match = re.match(r"^[\s\.,;:)]+", tail_text)
        cleaned = re.sub(r"^[\s\.,;:)]+", "", tail_text).strip()
        if not cleaned:
            continue
        bbox = union_bbox(boxes)
        if leading_match:
            consumed = len(leading_match.group(0))
            ratio = min(0.75, consumed / max(len(tail_text), 1))
            bbox = (bbox[0] + (bbox[2] - bbox[0]) * ratio, bbox[1], bbox[2], bbox[3])
        return _LineRec(text=cleaned, bbox=bbox, spans=tail_spans)
    return None


def line_is_prose(line: _LineRec) -> bool:
    """Inside an equation zone, full English sentences (e.g. a Remark line or
    a short connective like 'the forward equation is' that PyMuPDF glued onto
    the equation block) must still be translated."""
    bare = strip_sentinels(line.text)
    words = [word for word in _PROSE_WORD_RE.findall(bare) if word.lower() not in _MATH_WORDS]
    has_formula_tail = line_has_translatable_formula_tail(line)
    has_short_fragment = line_has_short_formula_prose_fragment(line)
    if len(words) < 3 and not has_formula_tail:
        return has_short_fragment
    compact = "".join(bare.split())
    if not compact:
        return False
    if sentinel_char_count(line.text) / len(compact) < 0.35:
        return True
    return has_formula_tail or has_short_fragment


def line_is_short_prose_prefix(line: _LineRec, next_line: _LineRec) -> bool:
    """A short prose heading glued to the first sentence inside an equation block."""
    bare = strip_sentinels(line.text).strip()
    if not bare or sentinel_char_count(line.text):
        return False
    if not sentence_final_text(bare):
        return False
    words = [word for word in _PROSE_WORD_RE.findall(bare) if word.lower() not in _MATH_WORDS]
    if not 1 <= len(words) <= 3:
        return False
    if looks_like_math(bare):
        return False
    if not line_is_prose(next_line):
        return False
    return lines_share_y_band(line, next_line)


def line_looks_like_section_heading(line: _LineRec) -> bool:
    """Detect structural heading lines before paragraph merging hides them."""
    compact = " ".join(strip_sentinels(line.text).split()).strip()
    if not compact:
        return False
    if sentinel_char_count(line.text):
        return False
    if len(compact) > 120:
        return False
    if _CAPTION_RE.match(compact):
        return False

    numbered_match = _NUMBERED_HEADING_LINE_RE.match(compact)
    appendix_match = _APPENDIX_STYLE_HEADING_LINE_RE.match(compact)
    reference_like = _looks_like_reference_entry_text(compact)
    if reference_like and (
        _REFERENCE_YEAR_RE.search(compact) or _REFERENCE_FRAGMENT_CUE_RE.search(compact)
    ):
        return False
    if reference_like and not (numbered_match or appendix_match):
        return False

    lower = compact.lower().rstrip(":")
    if lower in _STRUCTURE_HEADING_WORDS:
        return True

    if not (numbered_match or appendix_match):
        return False
    words = _PROSE_WORD_RE.findall(compact)
    if not 1 <= len(words) <= 14:
        return False
    if numbered_match:
        prefix = _SECTION_NUM_RE.match(compact)
        tail = compact[prefix.end() :].strip() if prefix else compact
    elif appendix_match:
        prefix = re.match(r"^[A-Z]\.\s+", compact)
        tail = compact[prefix.end() :].strip() if prefix else compact
    else:
        tail = compact
    tail_words = _PROSE_WORD_RE.findall(tail or compact)
    if not tail_words:
        return False
    if re.search(r"[!?。！？]$", compact):
        return False
    significant = [
        word
        for word in tail_words
        if word.lower() not in {"a", "an", "and", "for", "in", "of", "on", "the", "to", "with"}
    ]
    if not significant:
        return False
    return sum(1 for word in significant if word[0].isupper()) >= max(1, len(significant) - 1)


def heading_block_from_line(page_index: int, line: _LineRec) -> Optional[TextBlock]:
    accumulator = _SegmentAccumulator()
    _accumulate_line(accumulator, line)
    block = accumulator.flush(page_index)
    if block is None:
        return None
    block.block_type = "heading"
    block.should_translate = True
    block.bold = True
    block.no_merge = True
    block.preserve_position = False
    return block


def body_block_from_line(
    page_index: int,
    line: _LineRec,
    *,
    no_merge: bool = False,
) -> Optional[TextBlock]:
    accumulator = _SegmentAccumulator()
    _accumulate_line(accumulator, line)
    block = accumulator.flush(page_index)
    if block is not None:
        block.no_merge = no_merge
    return block


def line_looks_like_summary_leadin(line: _LineRec) -> bool:
    compact = " ".join(strip_sentinels(line.text).split()).strip()
    return bool(re.match(r"^In\s+summary\b", compact, re.IGNORECASE))


def accumulator_is_hyphenated_caption(accumulator: "_SegmentAccumulator") -> bool:
    if not accumulator.lines:
        return False
    first = " ".join(strip_sentinels(accumulator.lines[0]).split()).strip()
    last = strip_sentinels(accumulator.lines[-1]).rstrip()
    return bool(_CAPTION_RE.match(first) and last.endswith("-"))


def line_looks_like_list_item(line: _LineRec) -> bool:
    compact = " ".join(strip_sentinels(line.text).split()).strip()
    return bool(re.match(r"^(?:[•◦▪●]|\(?[a-zA-Z0-9]{1,3}\)|[-–])\s+", compact))


def line_continues_list_item(line: _LineRec, current: "_SegmentAccumulator") -> bool:
    if not current.line_bboxes:
        return False
    previous = current.line_bboxes[-1]
    previous_height = max(previous[3] - previous[1], 1.0)
    vertical_gap = line.bbox[1] - previous[3]
    if vertical_gap > max(14.0, previous_height * 1.45):
        return False
    list_left = current.line_bboxes[0][0]
    if not sentence_final_text(current.lines[-1]):
        return True
    if line.bbox[0] <= list_left + 8.0:
        return False
    return list_left - 2.0 <= line.bbox[0] <= list_left + 60.0


def split_bold_leadin_line(line: _LineRec) -> Optional[Tuple[_LineRec, _LineRec]]:
    prefix_spans: List[dict] = []
    tail_spans: List[dict] = []
    seen_text = False
    seen_bold_text = False
    collecting_prefix = True

    for span in line.spans:
        span_text = normalize_span_text(span.get("text", ""))
        if not span_text.strip():
            if seen_text and collecting_prefix:
                prefix_spans.append(span)
            elif seen_text:
                tail_spans.append(span)
            continue
        span_bold = bool(int(span.get("flags", 0)) & FLAG_BOLD)
        if not seen_text:
            if not span_bold and not span_is_leadin_marker(span_text):
                return None
            seen_text = True
        if collecting_prefix and span_bold:
            seen_bold_text = True
            prefix_spans.append(span)
            continue
        if collecting_prefix and not seen_bold_text and span_is_leadin_marker(span_text):
            prefix_spans.append(span)
            continue
        collecting_prefix = False
        tail_spans.append(span)

    if not seen_bold_text or not prefix_spans or not tail_spans:
        return None
    prefix_text = text_from_spans(prefix_spans)
    tail_text = text_from_spans(tail_spans)
    if not looks_like_bold_leadin_text(prefix_text, tail_text):
        return None
    prefix_line = line_from_spans(prefix_spans, fallback_bbox=line.bbox)
    tail_line = line_from_spans(tail_spans, fallback_bbox=line.bbox)
    if prefix_line is None or tail_line is None:
        return None
    if tail_line.text.startswith((": ", ":")) and not prefix_line.text.endswith(":"):
        prefix_line.text = prefix_line.text.rstrip() + ":"
        tail_line.text = tail_line.text[1:].lstrip()
    elif tail_line.text.startswith(("： ", "：")) and not prefix_line.text.endswith("："):
        prefix_line.text = prefix_line.text.rstrip() + "："
        tail_line.text = tail_line.text[1:].lstrip()
    return prefix_line, tail_line


def span_is_leadin_marker(text: str) -> bool:
    compact = " ".join(text.split()).strip()
    return bool(re.fullmatch(r"(?:\d+\)|\([a-zA-Z0-9]{1,3}\)|[•◦▪●])", compact))


def line_starts_with_leadin_marker(line: _LineRec) -> bool:
    compact = " ".join(strip_sentinels(line.text).split()).strip()
    return bool(re.match(r"^(?:\d+\)|\([a-zA-Z0-9]{1,3}\)|[•◦▪●])\s+", compact))


def line_contains_url(line: _LineRec) -> bool:
    return bool(URL_RE.search(strip_sentinels(line.text)))


def looks_like_bold_leadin_text(prefix_text: str, tail_text: str) -> bool:
    prefix = " ".join(strip_sentinels(prefix_text).split()).strip()
    tail = " ".join(strip_sentinels(tail_text).split()).strip()
    if not prefix or not tail:
        return False
    if sentinel_char_count(prefix_text):
        return False
    if _CAPTION_RE.match(prefix):
        return False
    if len(prefix) > 90:
        return False
    words = _PROSE_WORD_RE.findall(prefix)
    if not 1 <= len(words) <= 10:
        return False
    if substantial_prose_word_count(tail) < 2:
        return False
    return prefix.endswith((".", ":", "：")) or tail.startswith((": ", ":", "： ", "："))


def text_from_spans(spans: Sequence[dict]) -> str:
    return "".join(normalize_span_text(span.get("text", "")) for span in spans).strip()


def line_from_spans(spans: Sequence[dict], fallback_bbox: BBox) -> Optional[_LineRec]:
    text = text_from_spans(spans)
    if not text:
        return None
    boxes = [tuple(float(x) for x in span["bbox"]) for span in spans if "bbox" in span]
    bbox = union_bbox(boxes) if boxes else fallback_bbox
    return _LineRec(text=text, bbox=bbox, spans=list(spans))


def line_continues_inline_formula_tail(line: _LineRec, current: "_SegmentAccumulator") -> bool:
    """True for same-baseline formula fragments that finish a prose sentence."""
    if not current.bboxes:
        return False
    previous = current.bboxes[-1]
    overlap = min(previous[3], line.bbox[3]) - max(previous[1], line.bbox[1])
    min_height = min(previous[3] - previous[1], line.bbox[3] - line.bbox[1])
    if min_height <= 0 or overlap < min_height * 0.35:
        return False
    bare = strip_sentinels(line.text)
    if len(bare) > 120:
        return False
    compact = "".join(bare.split())
    if EQUATION_NUMBER_RE.fullmatch(compact):
        return False
    outside = _text_outside_sentinels(line.text)
    if re.search(r"[A-Za-z]|[),.;:]", outside):
        return True
    if current_expects_preserved_formula_tail(current):
        return False
    gap = line.bbox[0] - previous[2]
    return (
        sentinel_char_count(line.text) > 0
        and len(compact) <= 40
        and -2.0 <= gap <= max(12.0, min_height * 2.5)
    )


def current_expects_preserved_formula_tail(current: "_SegmentAccumulator") -> bool:
    if not current.lines:
        return False
    tail = re.sub(r"\s+", " ", strip_sentinels(current.lines[-1]).strip().lower())
    if not tail:
        return False
    if tail.endswith(("+", "=", ":=", ",")):
        return True
    return bool(
        re.search(
            r"(?:\bto be\b|\bwhere we use\b|\bwe have\b|\bfor all\w*\b|\band)$",
            tail,
        )
    )


def formula_prefix_tail_block(
    page_index: int, tail_line: _LineRec, next_line: _LineRec
) -> Optional[TextBlock]:
    accumulator = _SegmentAccumulator()
    _accumulate_line(accumulator, tail_line)
    _accumulate_line(accumulator, next_line)
    block = accumulator.flush(page_index)
    if block is None:
        return None
    block.bbox = next_line.bbox
    block.redact_bboxes = [tail_line.bbox, next_line.bbox]
    block.no_merge = True
    return block


def equation_table_prose_redact_bbox(line: _LineRec, lines: Sequence[_LineRec]) -> BBox:
    """Keep redaction for equation-table prose from expanding into formula cells."""
    formula_lines = [
        other
        for other in lines
        if other is not line and lines_share_y_band(line, other) and not line_is_prose(other)
    ]
    return trim_redact_bbox_against_formula_lines(line.bbox, formula_lines)


def trim_redact_bbox_against_formula_lines(
    redact_bbox: BBox, formula_lines: Sequence[_LineRec]
) -> BBox:
    x0, y0, x1, y1 = redact_bbox
    for formula_line in formula_lines:
        ox0, oy0, ox1, oy1 = formula_line.bbox
        if bbox_share_y_band(redact_bbox, formula_line.bbox):
            if ox0 >= x1 - 0.6 and ox0 - x1 <= 4.0:
                x1 = min(x1, ox0 - EQUATION_TABLE_REDACT_GAP)
            if ox1 <= x0 + 0.6 and x0 - ox1 <= 4.0:
                x0 = max(x0, ox1 + EQUATION_TABLE_REDACT_GAP)
            continue

        x_overlap = min(x1, ox1) - max(x0, ox0)
        if x_overlap <= 0:
            continue
        near_gap = EQUATION_TABLE_REDACT_GAP + 1.0
        if oy0 >= y0 and oy0 - y1 <= near_gap:
            y1 = min(y1, oy0 - EQUATION_TABLE_REDACT_GAP)
        elif oy1 <= y1 and y0 - oy1 <= near_gap:
            y0 = max(y0, oy1 + EQUATION_TABLE_REDACT_GAP)
    if x1 <= x0 or y1 <= y0:
        return redact_bbox
    return (x0, y0, x1, y1)


def bbox_share_y_band(first: BBox, second: BBox) -> bool:
    first_height = first[3] - first[1]
    second_height = second[3] - second[1]
    min_height = min(first_height, second_height)
    if min_height <= 0:
        return False
    overlap = min(first[3], second[3]) - max(first[1], second[1])
    return overlap >= 0.5 * min_height


def segments_from_formula_tail_prose(
    page_index: int, record: _RawBlockRec
) -> Optional[List[TextBlock]]:
    """Split prose that starts after an inline fraction without redacting it.

    Some PDFs typeset ``lambda = 1/(2 sigma^2) where sigma^2 is ...`` as
    overlapping physical lines. That can look like a table to the extractor,
    and the ``where`` clause may otherwise be preserved as formula material.
    We translate the clause in the following prose line's rectangle while only
    redacting the English clause spans, leaving the fraction itself intact.
    """
    lines = record.lines
    if len(lines) < 4:
        return None

    prose_lefts = [
        line.bbox[0]
        for line in lines
        if line_is_prose(line) and (line.bbox[2] - line.bbox[0]) >= 80.0
    ]
    if not prose_lefts:
        return None
    body_left = min(prose_lefts)

    for where_index, line in enumerate(lines[:-1]):
        line_text_lower = line.text.lower()
        cue_offset = line_text_lower.find("where")
        if cue_offset < 0:
            continue
        # Normal prose lines starting at the paragraph left should translate as
        # ordinary text. This path is only for right-side formula tails.
        if line.bbox[0] <= body_left + 80.0:
            continue

        prose_start = None
        for candidate in range(where_index + 1, len(lines)):
            candidate_line = lines[candidate]
            if line_is_prose(candidate_line) and candidate_line.bbox[0] <= body_left + 30.0:
                prose_start = candidate
                break
        if prose_start is None:
            continue

        segments: List[TextBlock] = []
        prefix = _SegmentAccumulator()
        for prefix_line in lines[:where_index]:
            if prefix_line.bbox[0] <= body_left + 60.0:
                _accumulate_line(prefix, prefix_line)
        prefix_block = prefix.flush(page_index)
        if prefix_block is not None:
            segments.append(prefix_block)

        clause_parts = [line.text[cue_offset:]]
        clause_parts.extend(tail_line.text for tail_line in lines[where_index + 1 :])
        clause_text = normalize_formula_tail_clause(join_lines(clause_parts))
        if not clause_text:
            return None

        insertion = _SegmentAccumulator()
        for prose_line in lines[prose_start:]:
            _accumulate_line(insertion, prose_line)
        clause_block = insertion.flush(page_index)
        if clause_block is None:
            return None

        redact_bboxes: List[BBox] = []
        cue_bbox = line_tail_bbox_from_cue(line, "where")
        if cue_bbox is not None:
            redact_bboxes.append(cue_bbox)
        redact_bboxes.extend(tail_line.bbox for tail_line in lines[where_index + 1 :])
        clause_block.text = clause_text
        clause_block.redact_bboxes = redact_bboxes
        clause_block.no_merge = True
        segments.append(clause_block)
        return segments

    return None


def normalize_formula_tail_clause(text: str) -> str:
    text = " ".join(text.split())
    return re.sub(r"\b(where|when|if)(?=\S)", r"\1 ", text, flags=re.IGNORECASE)


def line_tail_bbox_from_cue(line: _LineRec, cue: str) -> Optional[BBox]:
    bboxes: List[BBox] = []
    collecting = False
    for span in line.spans:
        span_text = normalize_span_text(span.get("text", ""))
        if not collecting and cue in span_text.lower():
            collecting = True
        if collecting and "bbox" in span:
            bboxes.append(tuple(float(value) for value in span["bbox"]))
    return union_bbox(bboxes) if bboxes else None


def segments_from_record(
    page_index: int, record: _RawBlockRec, equation_record: bool = False
) -> List[TextBlock]:
    """Second pass: build translatable segments from one raw block.

    For equation zones only full prose lines are extracted (the formula
    typesetting is preserved); for normal blocks a residual display-equation
    line still splits the segment and keeps its original rendering."""
    formula_tail_segments = segments_from_formula_tail_prose(page_index, record)
    if formula_tail_segments is not None:
        return formula_tail_segments

    segments: List[TextBlock] = []
    table_record = record_is_table(record)
    if equation_record and table_record:
        for line in record.lines:
            if not line_is_prose(line):
                continue
            cell = _SegmentAccumulator()
            _accumulate_line(cell, line)
            block = cell.flush(page_index)
            if block is not None:
                block.nowrap = True
                block.no_merge = True
                block.block_type = "table"
                block.redact_bboxes = [equation_table_prose_redact_bbox(line, record.lines)]
                segments.append(block)
        return segments

    if not equation_record and table_record:
        for line in record.lines:
            cell = _SegmentAccumulator()
            _accumulate_line(cell, line)
            block = cell.flush(page_index)
            if block is not None:
                block.nowrap = True
                block.no_merge = True
                block.block_type = "table"
                segments.append(block)
        return segments

    current = _SegmentAccumulator()
    current_has_inline_tail = False
    current_no_merge = False
    current_min_y0: Optional[float] = None
    current_inline_prefix_right: Optional[float] = None

    def flush_current() -> None:
        nonlocal current, current_has_inline_tail, current_no_merge, current_min_y0
        nonlocal current_inline_prefix_right
        block = current.flush(page_index)
        if block is not None:
            if current_has_inline_tail:
                # Keep the redaction tied to physical text spans. A single
                # union rectangle can erase nearby display-equation glyphs when
                # PyMuPDF vertically overlaps a prose tail and the next formula.
                block.redact_bboxes = list(current.line_bboxes)
            single_tail_after_prefix = (
                current_inline_prefix_right is not None
                and len(current.line_bboxes) == 1
                and current.line_bboxes[0][0] >= current_inline_prefix_right - 0.5
            )
            if (
                current_min_y0 is not None
                and block.bbox[1] < current_min_y0
                and not single_tail_after_prefix
            ):
                original_height = max(block.bbox[3] - block.bbox[1], block.font_size * 1.2)
                y0 = current_min_y0
                y1 = block.bbox[3]
                if y1 <= y0:
                    y1 = y0 + original_height
                block.bbox = (block.bbox[0], y0, block.bbox[2], y1)
                block.redact_bboxes = list(current.line_bboxes)
            if current_no_merge:
                block.no_merge = True
            segments.append(block)
        current = _SegmentAccumulator()
        current_has_inline_tail = False
        current_no_merge = False
        current_min_y0 = None
        current_inline_prefix_right = None

    skip_line_index: Optional[int] = None
    for line_index, line in enumerate(record.lines):
        if skip_line_index == line_index:
            continue
        if not equation_record and line_looks_like_section_heading(line):
            flush_current()
            heading = heading_block_from_line(page_index, line)
            if heading is not None:
                segments.append(heading)
                continue
        if not equation_record and line_looks_like_summary_leadin(line):
            flush_current()
            summary = heading_block_from_line(page_index, line)
            if summary is not None:
                segments.append(summary)
                continue
        if not equation_record and accumulator_is_hyphenated_caption(current):
            _accumulate_line(current, line)
            continue
        if not equation_record:
            split = split_bold_leadin_line(line)
            if split is not None:
                flush_current()
                leadin, tail = split
                if line_contains_url(tail):
                    url_block = body_block_from_line(page_index, line, no_merge=True)
                    if url_block is not None:
                        url_block.nowrap = True
                        segments.append(url_block)
                    continue
                heading = heading_block_from_line(page_index, leadin)
                if heading is not None:
                    segments.append(heading)
                    current_min_y0 = max(
                        current_min_y0 or float("-inf"),
                        heading.bbox[3] + max(1.0, heading.font_size * 0.12),
                    )
                    current_inline_prefix_right = heading.bbox[2]
                if line_starts_with_leadin_marker(leadin):
                    current_no_merge = True
                line = tail
        prose_tail = line_formula_prefix_prose_tail(line)
        if prose_tail is not None:
            next_line = (
                record.lines[line_index + 1] if line_index + 1 < len(record.lines) else None
            )
            if next_line is not None and line_is_prose(next_line):
                flush_current()
                block = formula_prefix_tail_block(page_index, prose_tail, next_line)
                if block is not None:
                    segments.append(block)
                    skip_line_index = line_index + 1
                    continue
            line = prose_tail
        if equation_record:
            if not line_is_prose(line):
                next_line = (
                    record.lines[line_index + 1]
                    if line_index + 1 < len(record.lines)
                    else None
                )
                if next_line is not None and line_is_short_prose_prefix(line, next_line):
                    _accumulate_line(current, line)
                    continue
                if line_continues_inline_formula_tail(line, current):
                    _accumulate_line(current, line)
                    current_has_inline_tail = True
                    continue
                flush_current()
                continue
        elif is_display_equation_line(line.text):
            flush_current()
            continue
        if line.is_cell:
            # Table cell: its own single-line block anchored at the cell bbox.
            flush_current()
            cell = _SegmentAccumulator()
            _accumulate_line(cell, line)
            block = cell.flush(page_index)
            if block is not None:
                block.nowrap = True
                segments.append(block)
            continue
        if not equation_record and line_looks_like_list_item(line):
            flush_current()
            current_no_merge = True
        elif current_no_merge and current.lines and not line_continues_list_item(line, current):
            flush_current()
        _accumulate_line(current, line)

    flush_current()
    return segments


def _accumulate_line(accumulator: "_SegmentAccumulator", line: _LineRec) -> None:
    accumulator.lines.append(line.text)
    accumulator.line_bboxes.append(line.bbox)
    for span in line.spans:
        if "bbox" in span:
            accumulator.bboxes.append(tuple(float(x) for x in span["bbox"]))
        if "size" in span:
            accumulator.font_sizes.append(float(span["size"]))
        if "color" in span:
            accumulator.colors.append(int_to_rgb(span["color"]))
        span_chars = len(normalize_span_text(span.get("text", "")).strip())
        span_bold = bool(int(span.get("flags", 0)) & FLAG_BOLD)
        if span_chars and not accumulator.seen_text:
            accumulator.starts_bold = span_bold
            accumulator.seen_text = True
        accumulator.total_chars += span_chars
        if span_bold:
            accumulator.bold_chars += span_chars


@dataclass
class _SegmentAccumulator:
    lines: List[str] = field(default_factory=list)
    line_bboxes: List[BBox] = field(default_factory=list)
    bboxes: List[BBox] = field(default_factory=list)
    font_sizes: List[float] = field(default_factory=list)
    colors: List[Color] = field(default_factory=list)
    bold_chars: int = 0
    total_chars: int = 0
    starts_bold: bool = False
    seen_text: bool = False

    def flush(self, page_index: int) -> Optional[TextBlock]:
        text = join_lines(self.lines)
        if not text or not self.bboxes:
            return None
        bold = self.total_chars > 0 and self.bold_chars / self.total_chars >= BLOCK_BOLD_RATIO
        return TextBlock(
            page_index=page_index,
            bbox=union_bbox(self.bboxes),
            text=text,
            font_size=median_or_default(self.font_sizes, 9.0),
            color=dominant_color(self.colors),
            bold=bold,
            starts_bold=self.starts_bold,
            source_lines=len(self.lines),
            redact_bboxes=list(self.line_bboxes) if len(self.line_bboxes) > 1 else None,
        )


def is_display_equation_line(line_text: str) -> bool:
    """A physical line whose content is dominated by math-font spans (or that
    is symbol-dense / a bare equation number) is a display-equation line."""
    bare = strip_sentinels(line_text).strip()
    compact = "".join(bare.split())
    if not compact:
        return bool(line_text.strip())
    if EQUATION_NUMBER_RE.fullmatch(compact):
        return True
    inside = sentinel_char_count(line_text)
    if inside and compact and inside / len(compact) >= EQUATION_LINE_MATH_RATIO:
        return True
    return looks_like_math(bare)


def sentinel_char_count(text: str) -> int:
    count = 0
    inside = False
    for char in text:
        if char == SENTINEL_OPEN:
            inside = True
        elif char == SENTINEL_CLOSE:
            inside = False
        elif inside and not char.isspace():
            count += 1
    return count


def is_injection_text(text: str) -> bool:
    """Detect prompt-injection lines targeting LLM readers/reviewers."""
    compact = " ".join(text.split())
    return any(pattern.search(compact) for pattern in INJECTION_PATTERNS)


def is_math_span(
    font: str, flags: int, text: str, size: float = 0.0, line_max_size: float = 0.0
) -> bool:
    """Spans the translator must never alter: math fonts, superscripts,
    single-letter italic variables.

    The superscript bit alone is only trusted for genuinely small spans:
    MuPDF marks whole continuation lines as superscript when they start with
    a subscript glyph, and those are normal prose."""
    if MATH_FONT_RE.search(font or ""):
        return True
    stripped = text.strip()
    if flags & 1:  # superscript bit
        if (
            stripped.lower() in _SHORT_PROSE_WORDS
            and size > 0
            and line_max_size > 0
            and size >= line_max_size * 0.85
        ):
            return False
        if size <= 0 or line_max_size <= 0 or size < line_max_size * 0.85:
            return True
        if len(stripped) <= 3:
            return True
        return False
    if (flags & 2) and len(stripped) <= 2 and stripped.isalpha():
        return True
    return False


def strip_sentinels(text: str) -> str:
    return text.replace(SENTINEL_OPEN, "").replace(SENTINEL_CLOSE, "")


def protect_text(text: str) -> Tuple[str, Dict[int, str]]:
    """Replace math/citation/URL fragments with ⟦n⟧ placeholders.

    Returns the protected text plus the mapping used to restore fragments
    after translation.
    """
    mapping: Dict[int, str] = {}

    def stash(fragment: str) -> str:
        index = len(mapping)
        mapping[index] = fragment
        return "\u27e6%d\u27e7" % index

    def stash_sentinel_run(match: re.Match) -> str:
        cleaned = strip_sentinels(match.group(0)).strip()
        if not cleaned:
            return " "
        return stash(cleaned)

    def stash_url(match: re.Match) -> str:
        fragment = match.group(0)
        trail = ""
        while fragment and fragment[-1] in URL_TRAILING_PUNCT:
            trail = fragment[-1] + trail
            fragment = fragment[:-1]
        if not fragment:
            return match.group(0)
        return stash(fragment) + trail

    def stash_math_token(match: re.Match) -> str:
        token = match.group(0)
        # Never re-stash text containing existing placeholders.
        if "\u27e6" in token or "\u27e7" in token:
            return token
        return stash(token)

    protected = SENTINEL_RUN_RE.sub(stash_sentinel_run, text)
    protected = URL_RE.sub(stash_url, protected)
    protected = CITATION_RE.sub(lambda m: stash(m.group(0)), protected)
    protected = MATH_TOKEN_RE.sub(stash_math_token, protected)
    return protected, mapping


def restore_text(translated: str, mapping: Dict[int, str]) -> Tuple[str, List[int]]:
    """Swap ⟦n⟧ placeholders back to the original fragments.

    Placeholders the translator dropped are appended at the end so no
    formula content is ever lost; their indices are reported for warnings.
    """
    seen: set = set()

    def swap(match: re.Match) -> str:
        index = int(match.group(1))
        if index in mapping:
            seen.add(index)
            return mapping[index]
        return ""

    restored = PLACEHOLDER_RE.sub(swap, translated)
    missing = [index for index in mapping if index not in seen]
    if missing:
        tail = " ".join(mapping[index] for index in missing)
        restored = restored.rstrip() + " " + tail
    return restored, missing


CJK_CHAR_RE = r"\u2e80-\u9fff\uf900-\ufaff\u3000-\u303f"
_SPACE_BEFORE_FULLWIDTH_RE = re.compile(  # noqa: E501
    r"\s+([\u3001\u3002\uff0c\uff1b\uff1a\uff1f\uff01\uff09\u3009\u300b\u300d\u3011\u2019\u201d])"
)
_SPACE_AFTER_FULLWIDTH_RE = re.compile(r"([\uff08\u3008\u300a\u300c\u3010\u2018\u201c])\s+")
_CJK_THEN_LATIN_RE = re.compile(r"([%s])([A-Za-z0-9$(\[\u2200-\u22ff\u0370-\u03ff])" % CJK_CHAR_RE)
_LATIN_THEN_CJK_RE = re.compile(r"([A-Za-z0-9%%)\]\u2200-\u22ff\u0370-\u03ff])([%s])" % CJK_CHAR_RE)
_FULLWIDTH_PUNCT = (  # noqa: E501
    "\u3001\u3002\uff0c\uff1b\uff1a\uff1f\uff01\uff08\uff09\u300a\u300b\u300c\u300d\u3010\u3011"
)


def clean_translation(text: str) -> str:
    """Normalise spacing of inserted Chinese text (盘古之白 + punctuation)."""
    cleaned = re.sub(r"[ \t]{2,}", " ", text)
    cleaned = cleaned.replace("\u27e8", "\u3008").replace("\u27e9", "\u3009")
    cleaned = cleaned.replace(").）", "）")
    cleaned = cleaned.replace(").，", "），")
    cleaned = _SPACE_BEFORE_FULLWIDTH_RE.sub(r"\1", cleaned)
    cleaned = _SPACE_AFTER_FULLWIDTH_RE.sub(r"\1", cleaned)
    # Thin breathing space between CJK and Latin/digits/math, both directions.
    cleaned = _CJK_THEN_LATIN_RE.sub(r"\1 \2", cleaned)
    cleaned = _LATIN_THEN_CJK_RE.sub(r"\1 \2", cleaned)
    cleaned = cleaned.replace("\u3008 ", "\u3008").replace(" \u3009", "\u3009")
    # No space between fullwidth punctuation and anything.
    cleaned = re.sub(r"([%s]) +" % _FULLWIDTH_PUNCT, r"\1", cleaned)
    cleaned = re.sub(r" +([%s])" % _FULLWIDTH_PUNCT, r"\1", cleaned)
    cleaned = re.sub(r"[ \t]{2,}", " ", cleaned)
    return cleaned.strip()


def is_line_number_span(
    text: str, size: float, line_max_size: float, isolated: bool = True
) -> bool:
    """Margin line numbers: short pure-digit spans much smaller than body text.

    Gutter numbers are isolated PDF text objects. A small digit span glued to
    neighbouring spans on the same line is a sub/superscript inside a formula
    (e.g. the 0 in X_0 set in CMR7) and must never be dropped."""
    stripped = text.strip()
    if not stripped.isdigit() or len(stripped) > 3:
        return False
    if len(stripped) == 1:
        return False
    if not isolated:
        return False
    if size <= LINE_NUMBER_MAX_SIZE:
        return True
    return bool(line_max_size) and size <= line_max_size * LINE_NUMBER_SIZE_RATIO


def _is_margin_line_number_span(
    text: str,
    span: dict,
    size: float,
    page_width: float | None,
    isolated: bool,
) -> bool:
    stripped = text.strip()
    if not isolated or not stripped.isdigit() or len(stripped) > 3:
        return False
    if page_width is None or page_width <= 0 or "bbox" not in span:
        return False
    if size > MARGIN_LINE_NUMBER_MAX_SIZE:
        return False
    x0, _y0, x1, _y1 = (float(value) for value in span["bbox"])
    center_x = (x0 + x1) / 2.0
    edge_band = max(24.0, page_width * MARGIN_LINE_NUMBER_EDGE_RATIO)
    return center_x <= edge_band or center_x >= page_width - edge_band


def join_lines(lines: Sequence[str]) -> str:
    """Join physical lines into flowing text, mending hyphenated words."""
    output = ""
    for line in lines:
        if not output:
            output = line
        elif output.endswith("-") and line[:1].islower():
            output = output[:-1] + line
        else:
            output += " " + line
    return output.strip()


def merge_paragraph_blocks(
    blocks: Sequence[TextBlock],
    graphic_regions_by_page: Optional[Dict[int, List[BBox]]] = None,
) -> List[TextBlock]:
    """Merge consecutive blocks that geometrically belong to one paragraph.

    Blocks arrive in document (reading) order. Two neighbours merge when they
    sit on the same page, have similar font sizes, overlap horizontally, and
    the vertical gap matches line spacing rather than paragraph spacing.
    """
    merged: List[TextBlock] = []
    for block in blocks:
        previous = merged[-1] if merged else None
        regions = (
            graphic_regions_by_page.get(block.page_index, []) if graphic_regions_by_page else []
        )
        if previous is not None and can_merge_blocks(previous, block, regions):
            merged[-1] = merge_two_blocks(previous, block)
        else:
            merged.append(block)
    return merged


def can_merge_blocks(
    prev: TextBlock,
    nxt: TextBlock,
    graphic_regions: Sequence[BBox] = (),
) -> bool:
    if prev.nowrap or nxt.nowrap:
        return can_merge_fixed_width_prose_blocks(prev, nxt, graphic_regions)
    if prev.no_merge or nxt.no_merge:
        return False
    if sentence_final_text(prev.text) and nxt.starts_bold:
        return False
    if prev.page_index != nxt.page_index:
        return False
    if abs(prev.font_size - nxt.font_size) > PARAGRAPH_SIZE_TOLERANCE:
        return False
    reference = max(prev.font_size, nxt.font_size, 1.0)
    gap = nxt.bbox[1] - prev.bbox[3]
    if gap > reference * PARAGRAPH_GAP_FACTOR:
        return False
    if gap < -reference and not _looks_like_overlapping_formula_tail_continuation(
        prev, nxt, gap, reference
    ):
        return False
    if _looks_like_float_wrap_boundary(prev, nxt):
        return False
    overlap = min(prev.bbox[2], nxt.bbox[2]) - max(prev.bbox[0], nxt.bbox[0])
    narrower = min(prev.bbox[2] - prev.bbox[0], nxt.bbox[2] - nxt.bbox[0])
    if narrower <= 0 or overlap / narrower < PARAGRAPH_MIN_X_OVERLAP:
        return False
    if graphic_regions and bbox_crosses_graphic_region(
        union_bbox([prev.bbox, nxt.bbox]),
        graphic_regions,
    ):
        return False
    return True


def _looks_like_overlapping_formula_tail_continuation(
    prev: TextBlock, nxt: TextBlock, gap: float, reference: float
) -> bool:
    """Merge prose split by a same-line formula tail with overlapping bboxes."""
    if gap < -reference * 1.35:
        return False
    if sentence_final_text(prev.text):
        return False
    if not nxt.text.lstrip().startswith(SENTINEL_OPEN):
        return False
    bare = strip_sentinels(nxt.text).lstrip()
    if not re.match(r"^(?:[<>=≤≥+\-−]\s*)?\d*(?:\.\d+)?\s*(?:be|is|are)\b", bare):
        return False
    words = _PROSE_WORD_RE.findall(bare)
    return len(words) >= 5


def _looks_like_float_wrap_boundary(prev: TextBlock, nxt: TextBlock) -> bool:
    """Stop merging when a paragraph leaves a side float and resumes full width."""
    if prev.source_lines < 4:
        return False
    if prev.block_type != "body" or nxt.block_type != "body":
        return False
    if sentence_final_text(prev.text):
        return False
    prev_width = prev.bbox[2] - prev.bbox[0]
    nxt_width = nxt.bbox[2] - nxt.bbox[0]
    if prev_width > 260.0 or nxt_width < 320.0:
        return False
    if abs(prev.bbox[0] - nxt.bbox[0]) > 12.0:
        return False
    right_growth = nxt.bbox[2] - prev.bbox[2]
    return right_growth >= max(120.0, prev_width * 0.55) and nxt_width >= prev_width * 1.45


def can_merge_fixed_width_prose_blocks(
    prev: TextBlock,
    nxt: TextBlock,
    graphic_regions: Sequence[BBox] = (),
) -> bool:
    if prev.no_merge or nxt.no_merge:
        return False
    if prev.page_index != nxt.page_index:
        return False
    if abs(prev.font_size - nxt.font_size) > PARAGRAPH_SIZE_TOLERANCE:
        return False
    if not _looks_like_fixed_width_prose_fragment(prev):
        return False
    if not _looks_like_fixed_width_prose_fragment(nxt):
        return False

    reference = max(prev.font_size, nxt.font_size, 1.0)
    gap = nxt.bbox[1] - prev.bbox[3]
    if gap > reference * 0.85 or gap < -reference * 0.2:
        return False

    prev_width = prev.bbox[2] - prev.bbox[0]
    nxt_width = nxt.bbox[2] - nxt.bbox[0]
    narrower = min(prev_width, nxt_width)
    if narrower < 150.0:
        return False
    overlap = min(prev.bbox[2], nxt.bbox[2]) - max(prev.bbox[0], nxt.bbox[0])
    if overlap <= 0 or overlap / narrower < 0.72:
        return False
    left_delta = abs(prev.bbox[0] - nxt.bbox[0])
    right_delta = abs(prev.bbox[2] - nxt.bbox[2])
    if left_delta > 28.0 and right_delta > 14.0:
        return False
    if graphic_regions and bbox_crosses_graphic_region(
        union_bbox([prev.bbox, nxt.bbox]),
        graphic_regions,
    ):
        return False
    return True


def _looks_like_fixed_width_prose_fragment(block: TextBlock) -> bool:
    if block.block_type != "body":
        return False
    if block.bold or block.starts_bold:
        return False
    if block.nowrap:
        if block.source_lines != 1:
            return False
    elif block.source_lines <= 1:
        return False

    plain = strip_sentinels(block.text).strip()
    if len(plain) < 35:
        return False
    if block.bbox[2] - block.bbox[0] < 150.0:
        return False
    words = _PROSE_WORD_RE.findall(plain)
    if len(words) < 5:
        return False
    if _looks_like_code_or_symbolic_text(plain) or looks_like_math(plain):
        return False
    return True


def sentence_final_text(text: str) -> bool:
    return strip_sentinels(text).rstrip().endswith((".", "?", "!", "。", "？", "！"))


def merge_two_blocks(prev: TextBlock, nxt: TextBlock) -> TextBlock:
    redact_bboxes: List[BBox] = []
    redact_bboxes.extend(prev.redact_bboxes or [prev.bbox])
    redact_bboxes.extend(nxt.redact_bboxes or [nxt.bbox])
    return TextBlock(
        page_index=prev.page_index,
        bbox=union_bbox([prev.bbox, nxt.bbox]),
        text=join_lines([prev.text, nxt.text]),
        font_size=prev.font_size,
        color=prev.color,
        bold=prev.bold and nxt.bold,
        starts_bold=prev.starts_bold,
        source_lines=prev.source_lines + nxt.source_lines,
        redact_bboxes=redact_bboxes,
    )


def redact_original_text(
    page: object,
    blocks: Sequence[TextBlock],
    margin: float,
    extra_rects: Sequence[BBox] = (),
) -> None:
    try:
        import fitz
    except ImportError as exc:
        raise RuntimeError("PyMuPDF is required. Install with: pip install -e .") from exc

    background = render_background(page)
    for block in blocks:
        for bbox in block.redact_bboxes or [block.bbox]:
            rect = expand_rect(fitz.Rect(bbox), margin)
            fill = sample_background_color(background, bbox, margin)
            page.add_redact_annot(rect, fill=fill)
    for bbox in extra_rects:
        rect = expand_rect(fitz.Rect(bbox), margin)
        fill = sample_background_color(background, bbox, margin)
        page.add_redact_annot(rect, fill=fill)

    kwargs = {}
    if hasattr(fitz, "PDF_REDACT_IMAGE_NONE"):
        kwargs["images"] = fitz.PDF_REDACT_IMAGE_NONE
    if hasattr(fitz, "PDF_REDACT_LINE_ART_NONE"):
        kwargs["graphics"] = fitz.PDF_REDACT_LINE_ART_NONE
    try:
        page.apply_redactions(**kwargs)
    except TypeError:
        page.apply_redactions()


# --- Centered-block detection -------------------------------------------------


def detect_centered_blocks(blocks: Sequence[TextBlock], page_width: float) -> List[bool]:
    """Title / author / heading blocks that were centred in the source stay
    centred. Body paragraphs (full column width or many lines) never match;
    flush-left blocks (bibliography entries, paragraph tails) are excluded by
    the column-left indent requirement."""
    if not blocks:
        return []
    widths = [block.bbox[2] - block.bbox[0] for block in blocks]
    reference = max(widths)
    if reference < page_width * 0.5:
        reference = page_width * 0.78
    # Column left edge: median x0 of body-like blocks (footers/page numbers
    # sit outside the column and would skew a plain minimum).
    body_lefts = [
        block.bbox[0] for block in blocks if block.source_lines >= 2 or len(block.text) > 60
    ]
    column_left = (
        statistics.median(body_lefts) if body_lefts else min(block.bbox[0] for block in blocks)
    )
    tolerance = max(6.0, page_width * 0.012)
    flags: List[bool] = []
    for block, width in zip(blocks, widths):
        if block.nowrap:
            flags.append(False)
            continue
        center = (block.bbox[0] + block.bbox[2]) / 2.0
        delta = abs(center - page_width / 2.0)
        narrow = width <= reference * 0.86
        short = block.source_lines <= 2 or len(block.text) <= 80
        indented = block.bbox[0] >= column_left + 18.0
        flags.append(delta <= tolerance and narrow and short and indented)
    return flags


def caption_should_center(block: TextBlock, page_width: float) -> bool:
    if block.block_type != "caption" or block.nowrap or block.source_lines > 2:
        return False
    x0, _, x1, _ = block.bbox
    width = x1 - x0
    if width < page_width * 0.25 or width > page_width * 0.90:
        return False
    center = (x0 + x1) / 2.0
    return abs(center - page_width / 2.0) <= max(12.0, page_width * 0.02)


def center_caption_bbox(block: TextBlock, page_width: float) -> TextBlock:
    if block.block_type != "caption":
        return block
    x0, y0, x1, y1 = block.bbox
    width = x1 - x0
    if width <= 0:
        return block
    new_x0 = max(0.0, min(page_width - width, page_width / 2.0 - width / 2.0))
    redacts = block.redact_bboxes or [block.bbox]
    return replace(block, bbox=(new_x0, y0, new_x0 + width, y1), redact_bboxes=redacts)


def requested_translation_font_size(
    block: TextBlock,
    min_font_size: float,
    font_scale: float,
) -> float:
    base_size = max(min_font_size, block.font_size * font_scale)
    if block.block_type == "heading":
        return max(base_size, block.font_size * 1.12)
    return base_size


def expand_heading_bbox(block: TextBlock) -> TextBlock:
    if block.block_type != "heading":
        return block
    x0, y0, x1, y1 = block.bbox
    pad_y = max(1.0, block.font_size * 0.18)
    pad_x = max(2.0, block.font_size * 0.35)
    return replace(block, bbox=(x0, y0 - pad_y, x1 + pad_x, y1 + pad_y))


def relax_caption_boxes(page: object, items: Sequence[Tuple[TextBlock, str]]) -> None:
    """Give translated captions a little extra vertical room when it is free."""
    if not items:
        return
    page_bottom = float(page.rect.height) - 18.0
    blocks = [block for block, _ in items]
    for block in blocks:
        if block.block_type != "caption":
            continue
        x0, y0, x1, y1 = block.bbox
        next_y = page_bottom
        for other in blocks:
            if other is block or other.page_index != block.page_index:
                continue
            ox0, oy0, ox1, _ = other.bbox
            if oy0 <= y1:
                continue
            overlap = min(x1, ox1) - max(x0, ox0)
            if overlap <= min(x1 - x0, ox1 - ox0) * 0.2:
                continue
            next_y = min(next_y, oy0 - 1.0)
        relaxed_y1 = min(y1 + _CAPTION_EXTRA_HEIGHT, next_y, page_bottom)
        if relaxed_y1 > y1 + 2.0:
            block.bbox = (x0, y0, x1, relaxed_y1)


# --- CJK typesetting engine ---------------------------------------------------


@dataclass
class _Token:
    kind: str  # "cjk" | "word" | "space"
    text: str
    width: float = 0.0


def is_cjk_char(char: str) -> bool:
    code = ord(char)
    if 0x2E80 <= code <= 0x9FFF or 0xF900 <= code <= 0xFAFF:
        return True
    if 0xFE30 <= code <= 0xFE4F or 0xFF00 <= code <= 0xFFEF:
        return True
    if 0x20000 <= code <= 0x2FA1F:
        return True
    return char in "…—–·‘’“”"


def tokenize_text(text: str) -> List[_Token]:
    tokens: List[_Token] = []
    word: List[str] = []

    def flush_word() -> None:
        if word:
            tokens.append(_Token("word", "".join(word)))
            word.clear()

    for char in text:
        if char.isspace():
            flush_word()
            if tokens and tokens[-1].kind == "space":
                continue
            tokens.append(_Token("space", " "))
        elif is_cjk_char(char):
            flush_word()
            tokens.append(_Token("cjk", char))
        else:
            word.append(char)
    flush_word()
    while tokens and tokens[0].kind == "space":
        tokens.pop(0)
    while tokens and tokens[-1].kind == "space":
        tokens.pop()
    return tokens


def char_width(char: str, fonts: Sequence[Tuple[object, str]], size: float) -> float:
    for font, _ in fonts:
        if font.has_glyph(ord(char)):
            return font.glyph_advance(ord(char)) * size
    return fonts[0][0].glyph_advance(ord(char)) * size


def token_width(token: _Token, fonts: Sequence[Tuple[object, str]], size: float) -> float:
    return sum(char_width(char, fonts, size) for char in token.text)


def split_long_word(
    token: _Token,
    fonts: Sequence[Tuple[object, str]],
    size: float,
    max_width: float,
) -> List[_Token]:
    """Hard-split a word wider than the line (URLs, hashes)."""
    pieces: List[_Token] = []
    chunk: List[str] = []
    width = 0.0
    for char in token.text:
        advance = char_width(char, fonts, size)
        if chunk and width + advance > max_width:
            pieces.append(_Token("word", "".join(chunk)))
            chunk = [char]
            width = advance
        else:
            chunk.append(char)
            width += advance
    if chunk:
        pieces.append(_Token("word", "".join(chunk)))
    return pieces


def break_lines(
    tokens: List[_Token],
    fonts: Sequence[Tuple[object, str]],
    size: float,
    max_width: float,
    prefer_space_break: bool = False,
) -> List[List[_Token]]:
    """Greedy line breaking with kinsoku adjustment."""
    for token in tokens:
        token.width = token_width(token, fonts, size)

    expanded: List[_Token] = []
    for token in tokens:
        if token.kind == "word" and token.width > max_width:
            expanded.extend(split_long_word(token, fonts, size, max_width))
        else:
            expanded.append(token)
    for token in expanded:
        token.width = token_width(token, fonts, size)

    lines: List[List[_Token]] = []
    current: List[_Token] = []
    current_width = 0.0

    def open_line(token: _Token) -> None:
        nonlocal current, current_width
        lines.append(current)
        current = [] if token.kind == "space" else [token]
        current_width = 0.0 if token.kind == "space" else token.width

    for token in expanded:
        if not current and token.kind == "space":
            continue
        if current_width + token.width <= max_width + 0.5 or not current:
            current.append(token)
            current_width += token.width
            continue
        # Token overflows: it starts the next line, with kinsoku fixes.
        if token.kind == "cjk" and token.text in NO_LINE_START and len(current) >= 2:
            pulled = current.pop()
            current_width -= pulled.width
            while current and current[-1].kind == "space":
                current_width -= current[-1].width
                current.pop()
            lines.append(current)
            current = [pulled, token]
            current_width = pulled.width + token.width
            continue
        while current and current[-1].kind == "space":
            current_width -= current[-1].width
            current.pop()
        if current and current[-1].kind == "cjk" and current[-1].text in NO_LINE_END:
            opener = current.pop()
            lines.append(current)
            current = [opener, token]
            current_width = opener.width + token.width
            continue
        if prefer_space_break:
            space_index = max(
                (i for i, item in enumerate(current) if item.kind == "space"),
                default=None,
            )
            if space_index is not None and space_index >= 1:
                width_before = sum(item.width for item in current[:space_index])
                if width_before >= 0.45 * max_width:
                    moved = current[space_index + 1 :]
                    lines.append(current[:space_index])
                    current = moved + [token]
                    current_width = sum(item.width for item in current)
                    continue
        open_line(token)
    if current:
        lines.append(current)
    return [line for line in lines if line]


def stretchable_gaps(line: Sequence[_Token]) -> List[int]:
    """Indices i such that the gap AFTER token i can stretch for justification."""
    gaps: List[int] = []
    for index in range(len(line) - 1):
        left, right = line[index], line[index + 1]
        if left.kind == "space" or right.kind == "space":
            gaps.append(index)
        elif left.kind == "cjk" or right.kind == "cjk":
            gaps.append(index)
    return gaps


def effective_min_font_size(block: TextBlock, min_font_size: float) -> float:
    if block.block_type == "caption":
        return min(min_font_size, _CAPTION_MIN_FONT_SIZE)
    return min_font_size


def leading_options(block: TextBlock) -> Tuple[float, ...]:
    if block.block_type == "caption":
        return (1.18, 1.08, _CAPTION_TIGHT_LEADING)
    return (DEFAULT_LEADING, 1.26, 1.15)


def choose_compressed_layout(
    tokens: List[_Token],
    fonts: Sequence[Tuple[object, str]],
    width: float,
    height: float,
    min_size: float,
    centered: bool,
) -> Tuple[float, float, List[List[_Token]]]:
    """Last-resort layout that still keeps text inside its rectangle."""
    size = min_size
    best: Tuple[float, float, List[List[_Token]]] | None = None
    while size >= _ABSOLUTE_MIN_FONT_SIZE - 1e-6:
        lines = break_lines(tokens, fonts, size, width, prefer_space_break=centered)
        for leading in (1.08, 1.0, 0.94):
            best = (size, leading, lines)
            if line_block_height(lines, size, leading) <= height + size * 0.25:
                return best
        size -= 0.2
    assert best is not None
    return best


def translated_text_fits(
    block: TextBlock,
    text: str,
    font_pack: FontPack,
    font_size: float,
    min_font_size: float,
    margin: float,
    centered: bool = False,
) -> bool:
    try:
        import fitz
    except ImportError as exc:
        raise RuntimeError("PyMuPDF is required. Install with: pip install -e .") from exc

    rect = shrink_rect(fitz.Rect(block.bbox), margin)
    if rect.width <= 1 or rect.height <= 1:
        return False
    fonts = font_pack.fonts_for(block.bold)
    tokens = tokenize_text(text)
    if not tokens:
        return True

    min_size = effective_min_font_size(block, min_font_size)
    if block.nowrap:
        width = sum(token_width(token, fonts, font_size) for token in tokens)
        if width <= rect.width or width <= 0:
            return True
        scaled_size = font_size * rect.width / width
        return scaled_size >= min_size * 0.8

    size = font_size
    while size >= min_size - 1e-6:
        lines = break_lines(tokens, fonts, size, rect.width, prefer_space_break=centered)
        for leading in leading_options(block):
            if line_block_height(lines, size, leading) <= rect.height + size * 0.4:
                return True
        size -= 0.25
    return False


def insert_translated_text(
    page: object,
    block: TextBlock,
    text: str,
    font_pack: FontPack,
    font_size: float,
    min_font_size: float,
    margin: float,
    centered: bool = False,
) -> bool:
    """Typeset `text` into the block's bbox with the CJK engine.

    Returns True when the text fits the bbox at >= min_font_size.
    """
    try:
        import fitz
    except ImportError as exc:
        raise RuntimeError("PyMuPDF is required. Install with: pip install -e .") from exc

    rect = shrink_rect(fitz.Rect(block.bbox), margin)
    if rect.width <= 1 or rect.height <= 1:
        return False
    fonts = font_pack.fonts_for(block.bold)
    tokens = tokenize_text(text)
    if not tokens:
        return True

    min_size = effective_min_font_size(block, min_font_size)
    if block.nowrap:
        # Table cell: one line at the original anchor, shrunk to the cell width.
        for token in tokens:
            token.width = token_width(token, fonts, font_size)
        render_single_line(
            page, tokens, rect, fonts, font_size, block.color, False, min_size
        )
        return True

    chosen: Optional[Tuple[float, float, List[List[_Token]]]] = None
    size = font_size
    while size >= min_size - 1e-6:
        lines = break_lines(tokens, fonts, size, rect.width, prefer_space_break=centered)
        for leading in leading_options(block):
            height = line_block_height(lines, size, leading)
            if height <= rect.height + size * 0.4:
                chosen = (size, leading, lines)
                break
        if chosen:
            break
        size -= 0.25

    fitted = chosen is not None
    if chosen is None:
        chosen = choose_compressed_layout(
            tokens,
            fonts,
            rect.width,
            rect.height,
            min_size,
            centered,
        )

    size, leading, lines = chosen

    if len(lines) == 1:
        render_single_line(
            page,
            lines[0],
            rect,
            fonts,
            size,
            block.color,
            centered,
            min_size,
            top_aligned=block.block_type == "caption",
        )
        return fitted

    ascent = fonts[0][0].ascender if fonts[0][0].ascender > 0 else 0.8
    baseline = rect.y0 + size * min(ascent, 0.92)
    advance = size * leading
    for index, line in enumerate(lines):
        is_last = index == len(lines) - 1
        justify = not centered and not is_last
        render_line(page, line, rect, fonts, size, block.color, baseline, centered, justify)
        baseline += advance
    return fitted


def line_block_height(lines: Sequence[Sequence[_Token]], size: float, leading: float) -> float:
    if not lines:
        return 0.0
    return size * leading * (len(lines) - 1) + size * 1.06


def render_single_line(
    page: object,
    line: Sequence[_Token],
    rect: object,
    fonts: Sequence[Tuple[object, str]],
    size: float,
    color: Color,
    centered: bool,
    min_font_size: float,
    top_aligned: bool = False,
) -> None:
    """Single-line blocks (headings, footers, captions cells): vertically
    centred; shrinks to the rect width when necessary."""
    width = sum(token.width for token in line)
    if width > rect.width and width > 0:
        scale = rect.width / width
        size = max(min_font_size * 0.8, size * scale)
        for token in line:
            token.width = token_width(token, fonts, size)
        width = sum(token.width for token in line)
    ascent = fonts[0][0].ascender if fonts[0][0].ascender > 0 else 0.8
    descent = abs(fonts[0][0].descender) if fonts[0][0].descender else 0.2
    if top_aligned:
        baseline = rect.y0 + size * 0.75
    else:
        baseline = rect.y0 + (rect.height + size * (ascent - descent)) / 2.0
        baseline = min(baseline, rect.y1 - size * descent * 0.5)
    x_start = rect.x0 + max(0.0, (rect.width - width) / 2.0) if centered else rect.x0
    emit_tokens(page, line, fonts, size, color, x_start, baseline, {})


def render_line(
    page: object,
    line: Sequence[_Token],
    rect: object,
    fonts: Sequence[Tuple[object, str]],
    size: float,
    color: Color,
    baseline: float,
    centered: bool,
    justify: bool,
) -> None:
    natural = sum(token.width for token in line)
    increments: Dict[int, float] = {}
    x_start = rect.x0
    if centered:
        x_start = rect.x0 + max(0.0, (rect.width - natural) / 2.0)
    elif justify and natural < rect.width:
        gaps = stretchable_gaps(line)
        if gaps:
            extra = rect.width - natural
            per_gap = extra / len(gaps)
            if per_gap <= size * MAX_JUSTIFY_GAP_EM:
                increments = {index: per_gap for index in gaps}
    emit_tokens(page, line, fonts, size, color, x_start, baseline, increments)


def emit_tokens(
    page: object,
    line: Sequence[_Token],
    fonts: Sequence[Tuple[object, str]],
    size: float,
    color: Color,
    x_start: float,
    baseline: float,
    increments: Dict[int, float],
) -> None:
    """Write one typeset line, batching runs that share font and need no
    positional adjustment."""
    x = x_start
    run_chars: List[str] = []
    run_font: Optional[str] = None
    run_x = x

    def flush_run() -> None:
        nonlocal run_chars, run_font
        if run_chars and run_font is not None:
            page.insert_text(
                (run_x, baseline),
                "".join(run_chars),
                fontname=run_font,
                fontsize=size,
                color=color,
            )
        run_chars = []
        run_font = None

    for index, token in enumerate(line):
        gap_after = increments.get(index, 0.0)
        if token.kind == "space":
            flush_run()
            x += token.width + gap_after
            run_x = x
            continue
        for char in token.text:
            alias = pick_font_alias(char, fonts)
            if run_font is None:
                run_font = alias
                run_x = x
            elif alias != run_font:
                flush_run()
                run_font = alias
                run_x = x
            run_chars.append(char)
            x += char_width(char, fonts, size)
        if gap_after:
            flush_run()
            x += gap_after
            run_x = x
    flush_run()


def pick_font_alias(char: str, fonts: Sequence[Tuple[object, str]]) -> str:
    for font, alias in fonts:
        if font.has_glyph(ord(char)):
            return alias
    return fonts[0][1]


def is_translatable(text: str) -> bool:
    stripped = text.strip()
    if len(stripped) < 2:
        return False
    if not any("A" <= char <= "Z" or "a" <= char <= "z" for char in stripped):
        return False
    if looks_like_math(stripped):
        return False
    return True


# Symbols that only appear in real mathematics. ASCII slash/star/etc. are
# excluded: bibliography lines ("3(2/3/4):265-291, doi:10.1504/...") are full
# of them and must never look like equations.
STRONG_MATH_SYMBOLS = MATH_SYMBOLS - set("/*^_|\\<>~+")


def looks_like_math(text: str) -> bool:
    """Heuristic for display equations: dense math symbols, few plain words."""
    compact = "".join(text.split())
    if len(compact) < 4:
        return False
    greek = sum(1 for char in compact if "\u0370" <= char <= "\u03ff")
    mathsym = sum(
        1
        for char in compact
        if char in STRONG_MATH_SYMBOLS
        or "\u2190" <= char <= "\u22ff"
        or "\u27c0" <= char <= "\u2bff"
        or "\U0001d400" <= char <= "\U0001d7ff"
    )
    score = greek + mathsym
    if score < 2:
        return False
    digits = sum(1 for char in compact if char.isdigit())
    brackets = sum(1 for char in compact if char in "()[]{}")
    ascii_letters = sum(1 for char in compact if char.isascii() and char.isalpha())
    symbolish = score + digits + brackets
    return symbolish / len(compact) >= 0.25 and ascii_letters / len(compact) <= 0.45


def normalize_span_text(text: str) -> str:
    return text.replace("\u00a0", " ")


def union_bbox(bboxes: Iterable[BBox]) -> BBox:
    boxes = list(bboxes)
    return (
        min(box[0] for box in boxes),
        min(box[1] for box in boxes),
        max(box[2] for box in boxes),
        max(box[3] for box in boxes),
    )


def bbox_area(bbox: BBox) -> float:
    return max(0.0, bbox[2] - bbox[0]) * max(0.0, bbox[3] - bbox[1])


def bbox_intersection_area(first: BBox, second: BBox) -> float:
    x0 = max(first[0], second[0])
    y0 = max(first[1], second[1])
    x1 = min(first[2], second[2])
    y1 = min(first[3], second[3])
    return max(0.0, x1 - x0) * max(0.0, y1 - y0)


def bboxes_intersect(first: BBox, second: BBox) -> bool:
    return (
        min(first[2], second[2]) > max(first[0], second[0])
        and min(first[3], second[3]) > max(first[1], second[1])
    )


def bbox_contains_point(bbox: BBox, x: float, y: float) -> bool:
    return bbox[0] <= x <= bbox[2] and bbox[1] <= y <= bbox[3]


def expand_bbox(bbox: BBox, amount: float) -> BBox:
    if amount <= 0:
        return bbox
    return (bbox[0] - amount, bbox[1] - amount, bbox[2] + amount, bbox[3] + amount)


def expand_bbox_to_page(bbox: BBox, amount: float, page_rect: object) -> BBox:
    expanded = expand_bbox(bbox, amount)
    return (
        max(float(page_rect.x0), expanded[0]),
        max(float(page_rect.y0), expanded[1]),
        min(float(page_rect.x1), expanded[2]),
        min(float(page_rect.y1), expanded[3]),
    )


def median_or_default(values: Sequence[float], default: float) -> float:
    if not values:
        return default
    return float(statistics.median(values))


def dominant_color(colors: Sequence[Color]) -> Color:
    if not colors:
        return (0, 0, 0)
    rounded = [(round(r, 2), round(g, 2), round(b, 2)) for r, g, b in colors]
    return max(set(rounded), key=rounded.count)


def int_to_rgb(color: int) -> Color:
    red = ((color >> 16) & 255) / 255.0
    green = ((color >> 8) & 255) / 255.0
    blue = (color & 255) / 255.0
    return (red, green, blue)


def expand_rect(rect: object, amount: float) -> object:
    if amount <= 0:
        return rect
    rect.x0 -= amount
    rect.y0 -= amount
    rect.x1 += amount
    rect.y1 += amount
    return rect


def shrink_rect(rect: object, amount: float) -> object:
    if amount <= 0:
        return rect
    if rect.width > amount * 2:
        rect.x0 += amount
        rect.x1 -= amount
    if rect.height > amount * 2:
        rect.y0 += amount
        rect.y1 -= amount
    return rect


def compact_bbox(bbox: BBox) -> str:
    return "(%.1f, %.1f, %.1f, %.1f)" % bbox


@dataclass
class RenderedBackground:
    samples: bytes
    width: int
    height: int
    channels: int
    scale: float


def render_background(page: object, scale: float = 1.5) -> RenderedBackground:
    try:
        import fitz
    except ImportError as exc:
        raise RuntimeError("PyMuPDF is required. Install with: pip install -e .") from exc

    pixmap = page.get_pixmap(matrix=fitz.Matrix(scale, scale), alpha=False)
    return RenderedBackground(
        samples=pixmap.samples,
        width=pixmap.width,
        height=pixmap.height,
        channels=pixmap.n,
        scale=scale,
    )


def sample_background_color(
    background: RenderedBackground,
    bbox: BBox,
    margin: float,
) -> Color:
    x0, y0, x1, y1 = bbox
    x0 -= margin
    y0 -= margin
    x1 += margin
    y1 += margin
    points = edge_sample_points(x0, y0, x1, y1)
    colors = [sample_pixel(background, x, y) for x, y in points]
    colors = [color for color in colors if color is not None]
    if not colors:
        return (1, 1, 1)
    return median_color(colors)


def edge_sample_points(x0: float, y0: float, x1: float, y1: float) -> List[Tuple[float, float]]:
    points: List[Tuple[float, float]] = []
    fractions = (0.08, 0.25, 0.5, 0.75, 0.92)
    for fraction in fractions:
        x = x0 + (x1 - x0) * fraction
        y = y0 + (y1 - y0) * fraction
        points.append((x, y0))
        points.append((x, y1))
        points.append((x0, y))
        points.append((x1, y))
    points.extend(
        [
            (x0, y0),
            (x1, y0),
            (x0, y1),
            (x1, y1),
        ]
    )
    return points


def sample_pixel(background: RenderedBackground, x: float, y: float) -> Optional[Color]:
    px = int(round(x * background.scale))
    py = int(round(y * background.scale))
    if px < 0 or py < 0 or px >= background.width or py >= background.height:
        return None
    offset = (py * background.width + px) * background.channels
    if offset + 2 >= len(background.samples):
        return None
    return (
        background.samples[offset] / 255.0,
        background.samples[offset + 1] / 255.0,
        background.samples[offset + 2] / 255.0,
    )


def median_color(colors: Sequence[Color]) -> Color:
    reds = sorted(color[0] for color in colors)
    greens = sorted(color[1] for color in colors)
    blues = sorted(color[2] for color in colors)
    middle = len(colors) // 2
    return (reds[middle], greens[middle], blues[middle])
