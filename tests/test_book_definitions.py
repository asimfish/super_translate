"""Textbook input/output definitions are prose; executable examples are code."""
import fitz

from pdf_zh_translator.pdf_layout import prepare_translation_units


def test_book_definitions_translate_while_monospaced_code_stays_original():
    with fitz.open() as doc:
        cover = doc.new_page()
        cover.insert_text((54, 60), "Contents", fontsize=20)
        cover.insert_text((54, 100), "ISBN 978-1-23456-789-0", fontsize=10)
        chapter = doc.new_page()
        chapter.insert_text((54, 80), "Chapter 1", fontsize=24)
        for y, label, value in (
            (300, "input:", "Get data from the keyboard, a file, or another device."),
            (325, "output:", "Display data on the screen, save it in a file, or send it."),
        ):
            chapter.insert_text((54, y), label, fontname="hebo", fontsize=10)
            chapter.insert_text((94, y), value, fontsize=10)
        chapter.insert_text((54, 400), "input: dataset = load('training.csv')",
                            fontname="cour", fontsize=10)
        doc.new_page().insert_text((54, 80), "Chapter 2", fontsize=24)
        units, _, _ = prepare_translation_units(doc, preserve_graphics_text=True)
        assert any("keyboard" in b.text for b, _, _ in units)
        assert any("Display data" in b.text for b, _, _ in units)
        assert not any("training.csv" in b.text for b, _, _ in units)
