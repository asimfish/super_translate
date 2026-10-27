"""Conference authors remain source-owned even below the first metadata row."""
import fitz

from pdf_zh_translator.pdf_layout import prepare_translation_units


def test_email_byline_above_abstract_is_preserved_at_every_row():
    with fitz.open() as doc:
        p = doc.new_page(width=612, height=792)
        p.insert_text((54, 150), "An Academic Paper Title", fontname="hebo", fontsize=18)
        for y in (220, 280, 340):
            p.insert_text((54, y), "Research Author", fontname="hebo", fontsize=10)
            p.insert_text((54, y + 12), "University Research Group", fontsize=10)
            p.insert_text((54, y + 24), "researcher@university.org", fontname="cour", fontsize=10)
        p.insert_text((54, 390), "Abstract", fontname="hebo", fontsize=12)
        p.insert_text(
            (54, 410), "This is the scientific abstract that must be translated.", fontsize=10,
        )
        p.insert_text((54, 760), "Conference on Neural Information Processing Systems", fontsize=8)
        units, _, _ = prepare_translation_units(doc, preserve_graphics_text=True)
        assert not any(
            "Research Author" in b.text or "university.org" in b.text for b, _, _ in units
        )
        assert any("scientific abstract" in b.text for b, _, _ in units)
