"""Keep native figure titles without retaining the prose that crosses them."""

from pathlib import Path

import fitz
import pytest

from pdf_zh_translator import pdf_layout as layout


@pytest.mark.parametrize(
    "fixture,font_name,title",
    [
        ("native_type3_plot_title.pdf", "Inter-Bold", "AB"),
        ("native_type0_plot_title.pdf", "Inter-ExtraBold", "Ha"),
    ],
)
def test_overlapping_prose_is_removed_and_title_keeps_original_native_glyphs(
    fixture, font_name, title
):
    with fitz.open(Path(__file__).parent / "fixtures" / fixture) as source:
        with fitz.open(stream=source.tobytes(), filetype="pdf") as target:
            originals = [t for t in source[0].get_texttrace() if t["font"] == font_name]
            original = originals[0]
            region = layout.union_bbox([t["bbox"] for t in originals])
            original_chars = [c for t in originals for c in t["chars"]]
            body = layout.TextBlock(
                page_index=0,
                bbox=(100, 89, 290, 104),
                text="English prose",
                font_size=10,
                color=(0, 0, 0),
            )
            layout.redact_original_text(target[0], [body], 0.8, protected_regions=[region])
            assert title not in target[0].get_text()
            assert (
                layout._restore_redaction_damaged_preserved_regions(
                    target[0], source, 0, [region], [body], 0.8
                )
                == 1
            )
            titles = [t for t in target[0].get_texttrace() if t["font"] == font_name]
            restored_title = titles[0]
            restored_chars = [c for t in titles for c in t["chars"]]
            assert [(c[0], c[1]) for c in restored_chars] == [(c[0], c[1]) for c in original_chars]
            for restored, prior in zip(restored_chars, original_chars):
                assert restored[2] == pytest.approx(prior[2], abs=0.0001)
            assert restored_title["color"] == pytest.approx(original["color"], abs=0.0001)
            assert not target[0].get_images()
            assert "English" not in target[0].get_text()
            assert title in target[0].get_text().replace("\n", "")


def test_unsupported_font_does_not_remove_or_replace_existing_text():
    with fitz.open() as source:
        page = source.new_page()
        page.insert_text((72, 90), "Unsupported standard font")
        with fitz.open(stream=source.tobytes(), filetype="pdf") as target:
            region = source[0].get_texttrace()[0]["bbox"]
            count = target.xref_length()
            before = [(target.xref_object(x), target.xref_stream(x)) for x in range(1, count)]
            assert not layout._restore_overlapping_preserved_native_text(
                target[0], source[0], region
            )
            assert target.xref_length() == count
            assert [
                (target.xref_object(x), target.xref_stream(x)) for x in range(1, count)
            ] == before
