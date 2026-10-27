"""Copy/search ownership follows source rows; visible formulas remain a single sprite."""
from pathlib import Path

import fitz
import pytest

from pdf_zh_translator import pdf_layout as m
from pdf_zh_translator.formula_accessibility import formula_copy_rows, source_page_dict

FIXTURES = Path(__file__).parent / "fixtures"


def test_six_source_rows_have_six_distinct_hidden_owners_and_batched_runs():
    with fitz.open() as source, fitz.open() as target:
        page = source.new_page()
        expected = [f"x{i} = y{i} + z{i}" for i in range(6)]
        for index, text in enumerate(expected):
            page.insert_text((72, 100 + 30 * index), text, fontsize=12)
        atoms = tuple(span["bbox"] for block in page.get_text("dict")["blocks"]
                      for line in block.get("lines", []) for span in line["spans"])
        core = tuple(f([box[index] for box in atoms]) for index, f in
                     enumerate((min, min, max, max)))
        token = m._Token(
            kind="formula", text=" ".join(expected), width=140, source_bbox=core,
            source_atom_bboxes=atoms, source_size=12,
        )
        out = target.new_page()
        font = fitz.Font("helv")
        m.emit_tokens(out, [token], [(font, "helv")], 12, (0, 0, 0), 72, 300, {},
                      source_document=source)
        hidden = [span for span in out.get_texttrace() if span["type"] == 3]
        assert ["".join(chr(char[0]) for char in span["chars"]) for span in hidden] == expected
        assert len({round(span["chars"][0][2][1], 2) for span in hidden}) == 6
        assert len(out.get_images()) == 1
        # One same-font run per row, rather than one PDF operator per character.
        assert len(hidden) == 6


def test_batchnorm_six_gradient_rows_exclude_neighbour_prose_and_cross_row_cmex():
    with fitz.open(FIXTURES / "classic20_batchnorm_p2_p4_p8.pdf") as source:
        spans = [span for block in source_page_dict(source, 2)["blocks"]
                 for line in block.get("lines", []) for span in line["spans"]
                 if span["bbox"][0] < 305 and 367 < span["bbox"][1] < 489]
        atoms = tuple(span["bbox"] for span in spans)
        rows = formula_copy_rows(source, 2, atoms, (74, 367, 299, 491), 10)
        assert len(rows) == 6
        assert [row.baseline for row in rows] == pytest.approx(
            [378.72, 396.36, 420.84, 444.36, 465.60, 483.12], abs=0.02,
        )
        assert [row.text.count("∑") for row in rows] == [0, 1, 2, 0, 1, 1]
        assert rows[2].text.count("(") == rows[2].text.count(")") == 2
        assert "γ" in rows[0].text and "γ" in rows[4].text
        assert "β" in rows[5].text and all("β" not in row.text for row in rows[:5])
        assert "b" in rows[4].text and "b" not in rows[5].text
        assert all("Thus" not in row.text and "N" not in row.text for row in rows)
        assert all(left.bbox[3] <= right.bbox[1] for left, right in zip(rows, rows[1:]))


def test_source_decode_is_once_per_page_and_never_shared_between_documents(monkeypatch):
    original = fitz.Page.get_text
    calls = []
    def counted(page, *args, **kwargs):
        calls.append((id(page.parent), page.number))
        return original(page, *args, **kwargs)
    with fitz.open() as first, fitz.open() as second:
        first.new_page().insert_text((72, 100), "first")
        second.new_page().insert_text((72, 100), "second")
        monkeypatch.setattr(fitz.Page, "get_text", counted)
        assert source_page_dict(first, 0) is source_page_dict(first, 0)
        m._trim_formula_clip_against_foreign_ink(first, 0, (70, 90, 110, 110))
        other = source_page_dict(second, 0)
        assert other["blocks"][0]["lines"][0]["spans"][0]["text"] == "second"
        assert calls == [(id(first), 0), (id(second), 0)]


def test_inline_sum_and_radical_remain_one_semantic_row():
    with fitz.open(FIXTURES / "classic20_adam_p4_p14.pdf") as source:
        raw = source_page_dict(source, 0)
        spans = [span for block in raw["blocks"] for line in block.get("lines", [])
                 for span in line["spans"]]
        atoms = tuple(span["bbox"] for span in spans)
        for bbox in (
            (128.64, 252.47, 247.79, 275.14),
            (329.12, 251.50, 361.59, 269.65),
            (401.57, 370.37, 435.97, 389.23),
        ):
            assert formula_copy_rows(source, 0, atoms, bbox, 9) == []
