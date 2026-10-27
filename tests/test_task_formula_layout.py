import fitz

from pdf_zh_translator import pdf_layout as m


class CapturingTranslator:
    supports_source_style_segments = False

    def __init__(self):
        self.inputs = []

    def translate_batch(self, texts):
        self.inputs.extend(texts)
        return [
            {"Open Drawer": "打开抽屉", "Kitchen Toaster": "厨房烤面包机"}.get(
                t, "下面是各项任务的成功条件。"
            )
            for t in texts
        ]


def test_task_labels_stay_next_to_unchanged_source_conditions(tmp_path):
    source = tmp_path / "source.pdf"
    output = tmp_path / "translated.pdf"
    doc = fitz.open()
    page = doc.new_page(width=400, height=300)
    page.insert_font(fontname="bullet", fontbuffer=fitz.Font("china-s").buffer)
    page.insert_text((30, 40), "Below are the success conditions for each task:", fontsize=10)
    rows = [
        (80, "Open Drawer", "drawer_joint > 0.1 && condition(gripper_open)"),
        (140, "Kitchen Toaster", "toaster_joint > 0.65 && condition(gripper_open)"),
    ]
    for y, label, condition in rows:
        page.insert_text((40, y), "•", fontname="bullet", fontsize=10)
        page.insert_text((52, y), label, fontname="hebo", fontsize=10)
        end = 52 + fitz.get_text_length(label, fontname="hebo", fontsize=10)
        page.insert_text((end, y), ": success =", fontsize=10)
        page.insert_text((52, y + 12), condition, fontsize=10)
    doc.save(source)
    font_file = tmp_path / "cjk.ttf"
    font_file.write_bytes(fitz.Font("china-s").buffer)
    translator = CapturingTranslator()
    m.translate_pdf(source, output, translator, font_file=font_file, preserve_graphics_text=True)
    assert "Open Drawer" in translator.inputs
    assert "Kitchen Toaster" in translator.inputs
    assert all(
        "drawer_joint" not in text and "toaster_joint" not in text for text in translator.inputs
    )
    translated = fitz.open(output)
    for y, label, condition in rows:
        clip = fitz.Rect(51, y + 5, 395, y + 20)
        a = doc[0].get_pixmap(clip=clip, matrix=fitz.Matrix(2, 2))
        b = translated[0].get_pixmap(clip=clip, matrix=fitz.Matrix(2, 2))
        assert a.samples == b.samples, condition
        expected = {"Open Drawer": "打开抽屉", "Kitchen Toaster": "厨房烤面包机"}[label]
        boxes = translated[0].search_for(expected)
        assert len(boxes) == 1
        assert abs((boxes[0].y0 + boxes[0].y1) / 2 - (y - 3)) < 7
    translated.close()
    doc.close()
