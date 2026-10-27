"""Fill outlines cannot certify a stroked real CFF glyph's ink bounds."""

import copy
import hashlib
import json
from pathlib import Path

import fitz
import numpy as np
import pytest

from pdf_zh_translator import pdf_layout as layout

SOURCE = Path(__file__).parent / "fixtures/nature_quantum_51162_p9.pdf"


@pytest.mark.parametrize("paint_mode", [0, 1, 2, 3, 4, 5, 6])
def test_actual_cff_fill_and_stroke_requires_raw_geometry(paint_mode, tmp_path):
    # Use the original embedded CFF program and its real PDF encoding, not a
    # substitute font. Only this controlled derivative's paint mode changes.
    with fitz.open(SOURCE) as doc:
        page = doc[0]
        source_font = next(font for font in page.get_fonts() if font[3] == "NPFDMO+AdvP4C4E46")
        original_font_sha = hashlib.sha256(doc.extract_font(source_font[0])[3]).hexdigest()
        origin = (498.7841796875, 616.988525390625)
        source_pdf_y = page.rect.height - origin[1]
        content = (
            f"q BT /{source_font[4]} 8.2191 Tf {paint_mode} Tr 1 w "
            f"1 0 0 1 {origin[0]} {source_pdf_y} Tm <50> Tj ET Q"
        ).encode("ascii")
        xref = doc.get_new_xref()
        doc.update_object(xref, "<<>>")
        doc.update_stream(xref, content)
        page.set_contents(xref)
        assert hashlib.sha256(doc.extract_font(source_font[0])[3]).hexdigest() == original_font_sha

        raw = page.get_text("dict")
        before = copy.deepcopy(raw)
        layout._attach_source_glyph_ink_bboxes(page, raw, {})
        span = raw["blocks"][0]["lines"][0]["spans"][0]
        source_span = before["blocks"][0]["lines"][0]["spans"][0]
        assert span["text"] == source_span["text"] == "P"

        trace_types = [trace["type"] for trace in page.get_texttrace()]
        clip = fitz.Rect(495, 614, 510, 628)
        pix = page.get_pixmap(matrix=fitz.Matrix(32, 32), clip=clip, colorspace=fitz.csGRAY)
        pixels = np.frombuffer(pix.samples, dtype=np.uint8).reshape(pix.height, pix.width)
        painted_rows, painted_columns = np.where(pixels < 100)
        below = np.count_nonzero(
            (pixels < 100) & ((np.arange(pix.height)[:, None] + pix.y) / 32 > span["bbox"][3])
        )
        evidence = {
            "paint_mode": paint_mode,
            "source_font_sha256": original_font_sha,
            "source_bbox": source_span["bbox"],
            "candidate_bbox": span["bbox"],
            "trace_types": trace_types,
            "metadata_complete": span.get("_source_glyph_ink_complete", False),
            "black_pixels_below_candidate_bbox": int(below),
            "painted_bbox": (
                [
                    clip.x0 + painted_columns.min() / 32,
                    clip.y0 + painted_rows.min() / 32,
                    clip.x0 + (painted_columns.max() + 1) / 32,
                    clip.y0 + (painted_rows.max() + 1) / 32,
                ]
                if painted_rows.size
                else None
            ),
        }
        (tmp_path / f"paint-mode-{paint_mode}.json").write_text(json.dumps(evidence, indent=2))
        if paint_mode in (2, 6):
            # Both fill and stroke paint the original glyph. Outlines alone
            # cannot prove the stroke ink boundary, so the whole span falls back.
            assert trace_types == [0, 1]
            assert span == source_span, evidence
            assert not span.get("_source_glyph_ink_complete", False)
            assert below == 0, evidence
        elif paint_mode in (1, 3, 5):
            assert span == source_span, evidence
            assert not span.get("_source_glyph_ink_complete", False)
        else:
            assert span.get("_source_glyph_ink_complete", False), evidence
            assert 625.20 <= span["bbox"][3] < 625.6
            assert below == 0, evidence
