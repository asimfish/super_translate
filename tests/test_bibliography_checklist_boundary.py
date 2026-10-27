"""A translated post-bibliography checklist must not be a reference edit."""

from types import SimpleNamespace

import fitz
import pytest

from pdf_zh_translator.page_inspector import _reference_continuation_regions, _reference_issues
from pdf_zh_translator.pdf_layout import _looks_like_reference_entry_text


@pytest.mark.parametrize(
    "heading",
    ["NeurIPS Paper Checklist", "ICML Paper Checklist", "ICLR Paper Checklist", "Paper Checklist"],
)
def test_checklist_heading_is_a_section_not_reference_metadata(heading):
    assert not _looks_like_reference_entry_text(heading)


def checklist_reference_pair(*, reference_changed=False):
    original = fitz.open()
    source = original.new_page(width=612, height=792)
    translated = fitz.open()
    target = translated.new_page(width=612, height=792)
    for y in (100, 130, 160, 190):
        text = "A. Author and B. Writer. Preserved title. In: NeurIPS, 2024. pp. 1-5."
        source.insert_text((108, y), text, fontsize=10)
        target.insert_text((108, y), text, fontsize=10)
    source.insert_text((108, 520), "NeurIPS Paper Checklist", fontsize=12)
    target.insert_text(
        (108.8, 520), "神经信息处理系统大会论文检查清单", fontsize=11, fontname="china-ss"
    )
    source.insert_text((131, 540), "1. Claims", fontsize=10)
    target.insert_text((131.8, 540), "1. 声明", fontsize=10, fontname="china-ss")
    source.insert_text(
        (143, 560), "Question: Do the main claims accurately reflect the contribution?", fontsize=10
    )
    target.insert_text(
        (143.8, 560), "问题：论文的主要声明是否准确反映了贡献？", fontsize=10, fontname="china-ss"
    )
    if reference_changed:
        target.insert_text(
            (108, 220), "此处参考文献条目被擅自翻译添加中文内容", fontsize=10, fontname="china-ss"
        )
    roles = [
        SimpleNamespace(
            text="NeurIPS Paper Checklist",
            bbox=(108, 506, 248, 524),
            block_type="body",
            should_translate=True,
        ),
        SimpleNamespace(
            text="1. Claims", bbox=(131, 529, 179, 544), block_type="heading", should_translate=True
        ),
        SimpleNamespace(
            text="Question: Do the main claims accurately reflect the contribution?",
            bbox=(143, 547, 504, 569),
            block_type="body",
            should_translate=True,
        ),
    ]
    return original, translated, roles


def test_cited_venue_metadata_is_not_exempted_as_a_checklist_heading():
    assert _looks_like_reference_entry_text(
        "A. Author and B. Writer. NeurIPS Paper Checklist. "
        "In Advances in Neural Information Processing Systems, 2024."
    )
    assert _looks_like_reference_entry_text("NeurIPS Paper Checklist, 2024")


def test_checklist_translation_does_not_change_preceding_references():
    original, translated, roles = checklist_reference_pair()
    try:
        regions = _reference_continuation_regions(original[0], roles)
        issues = _reference_issues(
            original[0],
            translated[0],
            14,
            0.0,
            source_role_blocks=roles,
            reference_regions=regions,
        )
        assert not [issue for issue in issues if issue.code == "reference_content_changed"]
    finally:
        original.close()
        translated.close()


def test_real_reference_translation_still_reports_content_change():
    original, translated, roles = checklist_reference_pair(reference_changed=True)
    try:
        regions = _reference_continuation_regions(original[0], roles)
        issues = _reference_issues(
            original[0],
            translated[0],
            14,
            0.0,
            source_role_blocks=roles,
            reference_regions=regions,
        )
        assert any(issue.code == "reference_content_changed" for issue in issues)
    finally:
        original.close()
        translated.close()
