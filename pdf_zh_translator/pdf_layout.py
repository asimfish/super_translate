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
from dataclasses import dataclass, field
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
# Vertical gap (in units of font size) below which two blocks are one paragraph.
PARAGRAPH_GAP_FACTOR = 0.65
# Font size difference above which blocks are never merged.
PARAGRAPH_SIZE_TOLERANCE = 1.5
# Minimum horizontal overlap ratio (of the narrower block) required to merge.
PARAGRAPH_MIN_X_OVERLAP = 0.5

MATH_SYMBOLS = set("=+\u2212\u00b1\u00d7\u00f7*/^_|\\<>~\u221e\u221a\u2202\u2207\u2211\u220f\u222b\u2208\u2209\u2282\u2286\u2283\u2287\u222a\u2229\u2227\u2228\u00ac\u2200\u2203\u2248\u2243\u2245\u2260\u2264\u2265\u226a\u226b\u221d\u2192\u2190\u2194\u21d2\u21d0\u21d4\u27e8\u27e9\u2032\u2033\u22a4\u22a5\u2225\u2295\u2297\u2299")

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
    "\u2070-\u209f"               # unicode super/subscripts
    "\u2190-\u22ff\u27c0-\u27e5\u27e8-\u27ef\u2a00-\u2aff"  # arrows + math operators
    "\U0001d400-\U0001d7ff"       # mathematical alphanumerics
)
MATH_TOKEN_RE = re.compile(r"\S*[%s]\S*" % MATH_TRIGGER)
SUPERSCRIPT_MAP = str.maketrans("0123456789+-=()n", "\u2070\u00b9\u00b2\u00b3\u2074\u2075\u2076\u2077\u2078\u2079\u207a\u207b\u207c\u207d\u207e\u207f")

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
    source_lines: int = 1
    # Table cells: render on one line, anchored at the original x, never
    # merged into paragraphs and never centred.
    nowrap: bool = False


@dataclass
class TranslationReport:
    input_pdf: Path
    output_pdf: Path
    page_count: int
    translated_blocks: int
    skipped_blocks: int
    warnings: List[str]


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
) -> TranslationReport:
    try:
        import fitz
    except ImportError as exc:
        raise RuntimeError("PyMuPDF is required. Install with: pip install -e .") from exc

    warnings: List[str] = []
    font_pack = build_font_pack(font_file, warnings)

    document = fitz.open(str(input_pdf))
    units, gutter_rects, skipped = prepare_translation_units(document)

    if not units:
        warnings.append("No extractable English text was found. Scanned PDFs need OCR before translation.")
        page_count = document.page_count
        document.save(str(output_pdf), garbage=4, deflate=True)
        document.close()
        return TranslationReport(input_pdf, output_pdf, page_count, 0, skipped, warnings)

    translations = translator.translate_batch([protected for _, protected, _ in units])
    by_page: Dict[int, List[Tuple[TextBlock, str]]] = {}
    for (block, _, mapping), translated_text in zip(units, translations):
        restored, missing = restore_text(translated_text, mapping)
        if missing:
            warnings.append(
                "Page %d: translator dropped %d placeholder(s); fragments appended at block end"
                % (block.page_index + 1, len(missing))
            )
        by_page.setdefault(block.page_index, []).append((block, clean_translation(restored)))

    for page_index in range(document.page_count):
        page_items = by_page.get(page_index, [])
        page_gutter = gutter_rects.get(page_index, [])
        if not page_items and not page_gutter:
            continue
        page = document[page_index]
        redact_original_text(page, [block for block, _ in page_items], margin, page_gutter)
        # Register after redactions: apply_redactions rebuilds page resources
        # and would drop a font registered beforehand.
        register_font_pack(page, font_pack)
        centered_flags = detect_centered_blocks(
            [block for block, _ in page_items], page.rect.width
        )
        for (block, translated_text), centered in zip(page_items, centered_flags):
            inserted = insert_translated_text(
                page=page,
                block=block,
                text=translated_text,
                font_pack=font_pack,
                font_size=max(min_font_size, block.font_size * font_scale),
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


def prepare_translation_units(
    document: object,
) -> Tuple[List[TranslationUnit], Dict[int, List[BBox]], int]:
    """Shared extraction pipeline for both `translate` and `export`.

    Returns (units, gutter_rects, skipped) where each unit carries the block,
    its placeholder-protected text, and the restore mapping.
    """
    raw_blocks, gutter_rects = collect_text_blocks(document)
    blocks = merge_paragraph_blocks(raw_blocks)
    page_heights = {
        index: document[index].rect.height for index in range(document.page_count)
    }
    bibliography = mark_bibliography_blocks(blocks, page_heights)
    units: List[TranslationUnit] = []
    skipped = 0
    for block, in_bibliography in zip(blocks, bibliography):
        if in_bibliography:
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


_REFERENCES_HEADING_RE = re.compile(r"^(references|bibliography)$", re.IGNORECASE)


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
        if (
            in_references
            and block.bold
            and block.font_size >= max(heading_size * 0.92, 10.5)
        ):
            in_references = False  # next section (appendix) starts
        page_height = page_heights.get(block.page_index, 0.0)
        is_footer = page_height > 0 and block.bbox[1] >= page_height * 0.92
        flags.append(in_references and not is_footer)
    return flags


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
        page_dict = page.get_text("dict")
        records: List[_RawBlockRec] = []
        for raw_block in page_dict.get("blocks", []):
            if raw_block.get("type") != 0:
                continue
            record, dropped = parse_block_lines(raw_block)
            if dropped:
                gutter_rects.setdefault(page_index, []).extend(dropped)
            if record is not None:
                records.append(record)
        equation_flags = mark_equation_blocks(records)
        for record, is_equation in zip(records, equation_flags):
            if record_is_algorithm(record):
                continue
            blocks.extend(
                segments_from_record(page_index, record, equation_record=is_equation)
            )
    return blocks, gutter_rects


@dataclass
class _LineRec:
    text: str          # sentinel-annotated line text
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


def parse_block_lines(raw_block: dict) -> Tuple[Optional[_RawBlockRec], List[BBox]]:
    """First pass: clean one raw PyMuPDF block into annotated physical lines.

    Gutter line numbers and prompt-injection lines are dropped here (their
    rects are returned for erasure)."""
    dropped_rects: List[BBox] = []
    lines: List[_LineRec] = []

    for raw_line in raw_block.get("lines", []):
        spans = raw_line.get("spans", [])
        line_max_size = 0.0
        for span in spans:
            if normalize_span_text(span.get("text", "")).strip():
                line_max_size = max(line_max_size, float(span.get("size", 0.0)))

        fragments: List[Tuple[str, dict]] = []
        for span in spans:
            span_text = normalize_span_text(span.get("text", ""))
            if not span_text.strip():
                continue
            span_size = float(span.get("size", line_max_size))
            if is_line_number_span(
                span_text, span_size, line_max_size, isolated=_span_is_isolated(span, spans)
            ):
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
            boxes = [tuple(float(x) for x in span["bbox"]) for span in group_spans if "bbox" in span]
            bbox = union_bbox(boxes) if boxes else tuple(float(x) for x in raw_line.get("bbox", (0, 0, 0, 0)))
            lines.append(
                _LineRec(text=text, bbox=bbox, spans=group_spans, is_cell=len(group) < len(fragments))
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
        if current and previous_end is not None and start is not None and start - previous_end > threshold:
            groups.append(current)
            current = []
        current.append(fragment)
        if bbox:
            previous_end = float(bbox[2])
    if current:
        groups.append(current)
    return groups


# Big-operator / oddball glyphs that only occur inside display equations.
_BIG_OPERATOR_CHARS = set("\u2211\u220f\u222b\u221a\u222c\u222d\u22c0\u22c1\u22c2\u22c3\u2a01\u2a02\u2a04\u2a06")
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


def record_is_algorithm(record: _RawBlockRec) -> bool:
    """Algorithm floats (numbered pseudocode) keep their original typesetting:
    reflowing '1: x <- F(y)' statements as prose destroys them."""
    bare = record.bare_text()
    steps = len(_PSEUDOCODE_STEP_RE.findall(bare))
    if steps < 1:
        return False
    has_arrow = "\u2190" in bare or "\u27f5" in bare or "\u21d0" in bare
    if has_arrow:
        return True
    return steps >= 3 and bool(re.search(r"\b(Require|Ensure|Input|Output)\s*:", bare))


def record_is_table(record: _RawBlockRec) -> bool:
    """Table blocks expose cells as separate physical lines sharing a y-band.

    Sequential paragraph lines never overlap vertically; two-line headings
    ("3.4" + title) are excluded by the minimum line count."""
    lines = record.lines
    if len(lines) < 3:
        return False
    overlapping_rows = 0
    for index in range(1, len(lines)):
        current_bbox = lines[index].bbox
        for other in range(max(0, index - 8), index):
            other_bbox = lines[other].bbox
            overlap = min(current_bbox[3], other_bbox[3]) - max(current_bbox[1], other_bbox[1])
            min_height = min(
                current_bbox[3] - current_bbox[1], other_bbox[3] - other_bbox[1]
            )
            if min_height > 0 and overlap >= 0.5 * min_height:
                overlapping_rows += 1
                break
        if overlapping_rows >= 2:
            return True
    return False


# Function names and math keywords that appear as words inside formulas;
# they never make a line prose on their own.
_MATH_WORDS = frozenset(
    "sin cos tan exp log min max arg sup inf lim det diag clip tr kl "
    "softmax argmax argmin var cov std relu prox sgn span rank dim mod".split()
)
_PROSE_WORD_RE = re.compile(r"[A-Za-z]{3,}")


def line_is_prose(line: _LineRec) -> bool:
    """Inside an equation zone, full English sentences (e.g. a Remark line or
    a short connective like 'the forward equation is' that PyMuPDF glued onto
    the equation block) must still be translated."""
    bare = strip_sentinels(line.text)
    words = [
        word for word in _PROSE_WORD_RE.findall(bare) if word.lower() not in _MATH_WORDS
    ]
    if len(words) < 3:
        return False
    compact = "".join(bare.split())
    if not compact:
        return False
    return sentinel_char_count(line.text) / len(compact) < 0.35


def segments_from_record(
    page_index: int, record: _RawBlockRec, equation_record: bool = False
) -> List[TextBlock]:
    """Second pass: build translatable segments from one raw block.

    For equation zones only full prose lines are extracted (the formula
    typesetting is preserved); for normal blocks a residual display-equation
    line still splits the segment and keeps its original rendering."""
    segments: List[TextBlock] = []
    if not equation_record and record_is_table(record):
        for line in record.lines:
            cell = _SegmentAccumulator()
            _accumulate_line(cell, line)
            block = cell.flush(page_index)
            if block is not None:
                block.nowrap = True
                segments.append(block)
        return segments

    current = _SegmentAccumulator()

    def flush_current() -> None:
        nonlocal current
        block = current.flush(page_index)
        if block is not None:
            segments.append(block)
        current = _SegmentAccumulator()

    for line in record.lines:
        if equation_record:
            if not line_is_prose(line):
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
        _accumulate_line(current, line)

    flush_current()
    return segments


def _accumulate_line(accumulator: "_SegmentAccumulator", line: _LineRec) -> None:
    accumulator.lines.append(line.text)
    for span in line.spans:
        if "bbox" in span:
            accumulator.bboxes.append(tuple(float(x) for x in span["bbox"]))
        if "size" in span:
            accumulator.font_sizes.append(float(span["size"]))
        if "color" in span:
            accumulator.colors.append(int_to_rgb(span["color"]))
        span_chars = len(normalize_span_text(span.get("text", "")).strip())
        accumulator.total_chars += span_chars
        if int(span.get("flags", 0)) & FLAG_BOLD:
            accumulator.bold_chars += span_chars


@dataclass
class _SegmentAccumulator:
    lines: List[str] = field(default_factory=list)
    bboxes: List[BBox] = field(default_factory=list)
    font_sizes: List[float] = field(default_factory=list)
    colors: List[Color] = field(default_factory=list)
    bold_chars: int = 0
    total_chars: int = 0

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
            source_lines=len(self.lines),
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
    if flags & 1:  # superscript bit
        if size <= 0 or line_max_size <= 0 or size < line_max_size * 0.85:
            return True
        stripped = text.strip()
        if len(stripped) <= 3:
            return True
        return False
    stripped = text.strip()
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
_SPACE_BEFORE_FULLWIDTH_RE = re.compile(r"\s+([\u3001\u3002\uff0c\uff1b\uff1a\uff1f\uff01\uff09\u300b\u300d\u3011\u2019\u201d])")
_SPACE_AFTER_FULLWIDTH_RE = re.compile(r"([\uff08\u300a\u300c\u3010\u2018\u201c])\s+")
_CJK_THEN_LATIN_RE = re.compile(r"([%s])([A-Za-z0-9$(\[\u2200-\u22ff\u0370-\u03ff])" % CJK_CHAR_RE)
_LATIN_THEN_CJK_RE = re.compile(r"([A-Za-z0-9%%)\]\u2200-\u22ff\u0370-\u03ff])([%s])" % CJK_CHAR_RE)
_FULLWIDTH_PUNCT = "\u3001\u3002\uff0c\uff1b\uff1a\uff1f\uff01\uff08\uff09\u300a\u300b\u300c\u300d\u3010\u3011"


def clean_translation(text: str) -> str:
    """Normalise spacing of inserted Chinese text (盘古之白 + punctuation)."""
    cleaned = re.sub(r"[ \t]{2,}", " ", text)
    cleaned = _SPACE_BEFORE_FULLWIDTH_RE.sub(r"\1", cleaned)
    cleaned = _SPACE_AFTER_FULLWIDTH_RE.sub(r"\1", cleaned)
    # Thin breathing space between CJK and Latin/digits/math, both directions.
    cleaned = _CJK_THEN_LATIN_RE.sub(r"\1 \2", cleaned)
    cleaned = _LATIN_THEN_CJK_RE.sub(r"\1 \2", cleaned)
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
    if not isolated:
        return False
    if size <= LINE_NUMBER_MAX_SIZE:
        return True
    return bool(line_max_size) and size <= line_max_size * LINE_NUMBER_SIZE_RATIO


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


def merge_paragraph_blocks(blocks: Sequence[TextBlock]) -> List[TextBlock]:
    """Merge consecutive blocks that geometrically belong to one paragraph.

    Blocks arrive in document (reading) order. Two neighbours merge when they
    sit on the same page, have similar font sizes, overlap horizontally, and
    the vertical gap matches line spacing rather than paragraph spacing.
    """
    merged: List[TextBlock] = []
    for block in blocks:
        previous = merged[-1] if merged else None
        if previous is not None and can_merge_blocks(previous, block):
            merged[-1] = merge_two_blocks(previous, block)
        else:
            merged.append(block)
    return merged


def can_merge_blocks(prev: TextBlock, nxt: TextBlock) -> bool:
    if prev.nowrap or nxt.nowrap:
        return False
    if prev.page_index != nxt.page_index:
        return False
    if abs(prev.font_size - nxt.font_size) > PARAGRAPH_SIZE_TOLERANCE:
        return False
    reference = max(prev.font_size, nxt.font_size, 1.0)
    gap = nxt.bbox[1] - prev.bbox[3]
    if gap > reference * PARAGRAPH_GAP_FACTOR or gap < -reference:
        return False
    overlap = min(prev.bbox[2], nxt.bbox[2]) - max(prev.bbox[0], nxt.bbox[0])
    narrower = min(prev.bbox[2] - prev.bbox[0], nxt.bbox[2] - nxt.bbox[0])
    if narrower <= 0 or overlap / narrower < PARAGRAPH_MIN_X_OVERLAP:
        return False
    return True


def merge_two_blocks(prev: TextBlock, nxt: TextBlock) -> TextBlock:
    return TextBlock(
        page_index=prev.page_index,
        bbox=union_bbox([prev.bbox, nxt.bbox]),
        text=join_lines([prev.text, nxt.text]),
        font_size=prev.font_size,
        color=prev.color,
        bold=prev.bold and nxt.bold,
        source_lines=prev.source_lines + nxt.source_lines,
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
        rect = expand_rect(fitz.Rect(block.bbox), margin)
        fill = sample_background_color(background, block.bbox, margin)
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
        block.bbox[0]
        for block in blocks
        if block.source_lines >= 2 or len(block.text) > 60
    ]
    column_left = statistics.median(body_lefts) if body_lefts else min(
        block.bbox[0] for block in blocks
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


def split_long_word(token: _Token, fonts: Sequence[Tuple[object, str]], size: float, max_width: float) -> List[_Token]:
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

    if block.nowrap:
        # Table cell: one line at the original anchor, shrunk to the cell width.
        for token in tokens:
            token.width = token_width(token, fonts, font_size)
        render_single_line(page, tokens, rect, fonts, font_size, block.color, False, min_font_size)
        return True

    chosen: Optional[Tuple[float, float, List[List[_Token]]]] = None
    size = font_size
    while size >= min_font_size - 1e-6:
        lines = break_lines(tokens, fonts, size, rect.width, prefer_space_break=centered)
        for leading in (DEFAULT_LEADING, 1.26, 1.15):
            height = line_block_height(lines, size, leading)
            if height <= rect.height + size * 0.4:
                chosen = (size, leading, lines)
                break
        if chosen:
            break
        size -= 0.25

    fitted = chosen is not None
    if chosen is None:
        # Last resort: smallest size, tightest leading; render anyway (the
        # engine never spills outside the rect width; extra lines may slightly
        # exceed the rect bottom).
        size = min_font_size
        lines = break_lines(tokens, fonts, size, rect.width)
        chosen = (size, 1.08, lines)

    size, leading, lines = chosen

    if len(lines) == 1:
        render_single_line(page, lines[0], rect, fonts, size, block.color, centered, min_font_size)
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
