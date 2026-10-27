import unittest
from pathlib import Path

from pdf_zh_translator import pdf_layout as m
from pdf_zh_translator.prompt_layout import (
    join_prompt_list_continuations,
    paired_markdown_openings,
    reflow_dense_prompt_items,
    split_fenced_code_blocks,
)


def raw(*texts):
    return {
        "type": 0,
        "bbox": (10, 20, 200, 100),
        "lines": [
            {
                "bbox": (10, 20 + i * 10, 200, 28 + i * 10),
                "spans": [{"text": text, "font": "NimbusMonL-Regu", "size": 6}],
            }
            for i, text in enumerate(texts)
        ],
    }


class PromptOwnership(unittest.TestCase):
    def test_unmatched_fence_cannot_preserve_following_prose(self):
        class Page:
            def get_text(self, mode):
                return {"blocks": [raw("```python", "A paragraph that still needs translation.")]}

        class Document:
            page_count = 1

            def __getitem__(self, index):
                return Page()

        valid = paired_markdown_openings(Document())
        self.assertEqual(valid, {})
        prose, boxes, fence = split_fenced_code_blocks(
            Page().get_text("dict")["blocks"],
            valid_openings=valid.get(0, set()),
        )
        self.assertFalse(boxes)
        self.assertIsNone(fence)
        self.assertEqual(len(prose[0]["lines"]), 2)

    def test_code_fence_splits_mixed_block_and_continues_across_pages(self):
        prose, boxes, fence = split_fenced_code_blocks(
            [raw("Explain the output:", "```python", "def cost(x):", "    return x + 1")]
        )
        self.assertEqual(len(prose), 1)
        self.assertEqual(len(prose[0]["lines"]), 1)
        self.assertEqual(boxes, [(10, 30, 200, 58)])
        self.assertEqual(fence, "```")
        prose, boxes, fence = split_fenced_code_blocks(
            [raw("    return cost", "```", "Next instruction")], fence
        )
        self.assertIsNone(fence)
        self.assertEqual(boxes, [(10, 20, 200, 38)])
        self.assertEqual(prose[0]["lines"][0]["spans"][0]["text"], "Next instruction")

    def test_inline_backticks_and_unfenced_prose_stay_translatable(self):
        prose, boxes, fence = split_fenced_code_blocks(
            [raw("Use `x` as input.", "- Explain how the function works.")]
        )
        self.assertFalse(boxes)
        self.assertIsNone(fence)
        self.assertEqual(len(prose[0]["lines"]), 2)

    def test_dense_nested_list_uses_readable_shared_size_and_fits_every_item(self):
        import fitz

        doc = fitz.open()
        page = doc.new_page(width=600, height=800)
        items = []
        for index in range(14):
            x = 70 + 7 * (index % 3)
            y = 100 + index * 7
            source = "- Explain the constraint and the current robot stage."
            page.insert_text((x, y), source, fontname="cour", fontsize=6)
        for block in page.get_text("dict")["blocks"]:
            for line in block.get("lines", []):
                bbox = tuple(line["bbox"])
                source = "".join(s["text"] for s in line["spans"])
                b = m.TextBlock(
                    0, bbox, source, 6, (0, 0, 0), no_merge=True, source_line_bboxes=(bbox,)
                )
                items.append((b, "- 说明约束条件与机器人当前所处的任务阶段。"))
        font = fitz.Font("china-s")
        pack = m.FontPack(font, Path("unused"), font, Path("unused"))
        result = reflow_dense_prompt_items(items, page, pack)
        sizes = {block.fixed_translation_font_size for block, _ in result}
        self.assertEqual(len(sizes), 1)
        self.assertGreaterEqual(next(iter(sizes)), 5.28)
        for index, (block, text) in enumerate(result):
            self.assertTrue(
                m.translated_text_fits(
                    block, text, pack, block.fixed_translation_font_size, 5, 0.8, False
                )
            )
            if index:
                self.assertGreaterEqual(block.bbox[1], result[index - 1][0].bbox[3])
        doc.close()

    def test_wrapped_prompt_instruction_gets_one_translation_owner(self):
        class Page:
            def get_text(self, mode):
                return {"blocks": [raw(*["- A prompt list item."] * 5)]}

        lead = m.TextBlock(
            0,
            (100, 100, 400, 106),
            "- Align the end-effector with the",
            6,
            (0, 0, 0),
            no_merge=True,
        )
        tail = m.TextBlock(0, (124, 107, 250, 113), "target object handle.", 6, (0, 0, 0))
        result = join_prompt_list_continuations([lead, tail], Page())
        self.assertEqual(len(result), 1)
        self.assertIn("the target object handle", result[0].text)


if __name__ == "__main__":
    unittest.main()
