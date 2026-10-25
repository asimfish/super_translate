import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from pdf_zh_translator import pdf_layout as m


class Page:
    def get_text(self, mode):
        return {
            "blocks": [
                {
                    "type": 0,
                    "lines": [
                        {
                            "spans": [
                                {"text": "Results improve from 5/20 to"},
                                {"text": " 13/20 on real-robot tasks."},
                            ]
                        }
                    ],
                }
            ]
        }


class RetryRegressions(unittest.TestCase):
    def setUp(self):
        state = (dict(m._bibliography_seen), m._bibliography_ended, m._bibliography_heading_size)

        def restore():
            m._bibliography_seen.clear()
            m._bibliography_seen.update(state[0])
            m._bibliography_ended, m._bibliography_heading_size = state[1:]

        self.addCleanup(restore)

    def test_prompt_engine_update_changes_recovery_revision(self):
        from app.services import translation_recovery as recovery

        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            module = root / "app/services/translation_recovery.py"
            module.parent.mkdir(parents=True)
            prompt = root / "pdf_zh_translator/prompt_layout.py"
            prompt.parent.mkdir()
            prompt.write_text("before")
            try:
                with patch.object(recovery, "__file__", str(module)):
                    recovery.current_engine_revision.cache_clear()
                    before = recovery.current_engine_revision()
                    prompt.write_text("after")
                    recovery.current_engine_revision.cache_clear()
                    self.assertNotEqual(before, recovery.current_engine_revision())
            finally:
                recovery.current_engine_revision.cache_clear()

    def test_number_ranges_keep_both_values_without_english_connectors(self):
        fragments = m._extract_formula_fragments(Page())
        self.assertEqual(fragments, ["5/20", "13/20"])
        self.assertEqual(m._missing_formula_fragments(fragments, ["结果从5/20提升到13/20。"]), [])
        self.assertEqual(
            m._missing_formula_fragments(fragments, ["结果从5/20提升到12/20。"]), ["13/20"]
        )
        self.assertEqual(m._missing_formula_fragments(["5/20"], ["从15/20提升到13/20"]), ["5/20"])
        self.assertEqual(m._missing_formula_fragments(["5/20"], ["从5/200提升到13/20"]), ["5/20"])

    def test_multiple_affiliation_numbers_separate_names(self):
        text = "Marcel Torne1,4Anthony Simeonov1,4Zechu Li1,3,4April Chan1,4"
        self.assertFalse(m._looks_like_untranslated_english(text))
        self.assertTrue(
            m._looks_like_untranslated_english(
                "We evaluate the proposed method using the same network "
                "and improve the performance."
            )
        )

    def test_supplement_opener_after_references(self):
        def block(text, y, size=9.96):
            return m.TextBlock(15, (49, y, 300, y + 20), text, size, (0, 0, 0))

        opener = block("Next, we provide additional details of our work. More concretely:", 58)
        heading = block("VIII. TASK DETAILS", 323, 8.96)
        m._bibliography_seen.clear()
        m._bibliography_seen[11] = True
        m._bibliography_ended = False
        m._bibliography_heading_size = 8.96
        self.assertFalse(m._is_bibliography_context(opener, [opener, heading]))
        self.assertFalse(
            m._is_bibliography_context(
                block(
                    "In this section of the appendix we describe "
                    "additional details about each task.",
                    341,
                ),
                [heading],
            )
        )
        reference = block("[75] A. Smith. Additional details. 2024.", 100)
        m._bibliography_ended = False
        self.assertTrue(m._is_bibliography_context(opener, [opener, heading, reference]))

    def test_styled_list_label_keeps_shared_list_typography(self):
        block = m.TextBlock(
            0,
            (60, 100, 300, 122),
            "• Hardware Setup: details about the hardware.",
            9.96,
            (0, 0, 0),
            bold_prefix=True,
            translated_bold_prefix_chars=6,
        )
        self.assertTrue(m._is_sibling_list_item(block, "• 硬件配置：硬件的详细信息。"))
        block.text = "Hardware Setup: details about the hardware."
        self.assertFalse(m._is_sibling_list_item(block, "硬件配置：硬件的详细信息。"))

    def test_long_list_label_does_not_bold_the_following_sentence(self):
        block = m.TextBlock(
            0,
            (60, 100, 300, 140),
            "• GUI for Real-to-Sim Transfer of Scenes XII: We provide details.",
            10,
            (0, 0, 0),
            bold_prefix=True,
        )
        text = "用于场景真实到仿真迁移的图形用户界面 第十二节：本文进一步阐述所提出的界面细节。"
        tokens = m.tokenize_text(text)
        m.apply_inline_bold(tokens, block, text)
        position = 0
        for token in tokens:
            if token.kind != "space":
                self.assertEqual(token.bold, position <= text.index("："))
            position += len(token.text)


if __name__ == "__main__":
    unittest.main()


def test_formula_signatures_exclude_translatable_giving_connector():
    class FormulaPage:
        def get_text(self, _mode):
            return {
                "blocks": [
                    {
                        "type": 0,
                        "lines": [
                            {
                                "spans": [
                                    {"text": "ct+1 = ct(1-dt+1),"},
                                    {"text": " giving "},
                                    {"text": "J(theta) = E[P]"},
                                ]
                            }
                        ],
                    }
                ]
            }

    fragments = m._extract_formula_fragments(FormulaPage())
    assert all("giving" not in f for f in fragments)
    assert m._missing_formula_fragments(fragments, ["ct+1=ct(1-dt+1),得到J(theta)=E[P]"]) == []
    assert m._missing_formula_fragments(fragments, ["ct+1=ct(1-dt+2),得到J(theta)=E[P]"])


def test_long_sentence_case_appendix_heading_with_explicit_appendix_opener():
    heading = m.TextBlock(
        12,
        (108, 76, 504, 90),
        "A Instantiation: observation, action, reward, and sim context",
        10.96,
        (0, 0, 0),
        bold=True,
    )
    opener = m.TextBlock(
        12,
        (108, 96, 504, 119),
        "This appendix instantiates the shared spaces of the POMDP of section 3.1.",
        9.96,
        (0, 0, 0),
    )
    assert m._starts_post_reference_supplement(heading, [heading, opener], 11)
    # The title of a reference entry or its author list does not constitute an appendix.
    heading.bold = False
    assert not m._starts_post_reference_supplement(heading, [heading, opener], 11)


def test_shell_reproduction_commands_are_not_english_prose():
    command = (
        "python unpack.py --output workspace R=workspace/mof-select-physical-correction-v1 "
        "python -m pip install -r $T/requirements-tested.txt "
        'python $T/train.py --bundle "$B" --family lambdamart --mode physical --seed 123'
    )
    assert m._looks_like_code_or_symbolic_text(command)
    assert not m._looks_like_untranslated_english(command)
    assert not m._looks_like_code_or_symbolic_text(
        "Python provides the implementation, and we evaluate the model with the same settings."
    )


def test_rotated_source_labels_follow_native_preservation_policy(tmp_path):
    import fitz

    doc = fitz.open()
    page = doc.new_page(width=400, height=600)
    page.insert_text(
        (150, 460),
        "(c) All actions overlap (d) Actions remain distinguishable",
        fontsize=10,
        rotate=90,
    )
    source = tmp_path / "source.pdf"
    translated = tmp_path / "translated.pdf"
    doc.save(source)
    doc.save(translated)
    doc.close()
    issues = m.verify_translation_issues(source, translated)
    assert not [i for i in issues if i.code == "untranslated_english"]


def test_reproduction_commands_keep_native_code_ownership():
    block = m.TextBlock(
        22,
        (143, 80, 466, 199),
        "python unpack.py --output workspace python -m pip install -r requirements.txt",
        9.96,
        (0, 0, 0),
        source_lines=10,
    )
    m.classify_blocks([block], 22, 792, [])
    assert block.block_type == "code"
    assert not block.should_translate
