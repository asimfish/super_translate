"""Tests for academic layout profile detection."""

import fitz

from pdf_zh_translator.layout_profiles import (
    detect_layout_profile,
    learn_layout_template,
    profile_policy,
    write_learned_layout_template,
)


def test_detects_ieee_two_column_profile():
    document = fitz.open()
    page = document.new_page(width=612, height=792)
    page.insert_text((54, 50), "IEEE Conference Paper")
    for i in range(8):
        page.insert_text((54, 90 + i * 22), "Left column text " * 5, fontsize=9)
        page.insert_text((330, 90 + i * 22), "Right column text " * 5, fontsize=9)

    profile = detect_layout_profile(document)
    policy = profile_policy(profile)
    document.close()

    assert profile.name == "ieee_two_column"
    assert profile.columns >= 2
    assert policy["warn_complex_floats"] is True


def test_detects_springer_like_single_column_profile():
    document = fitz.open()
    page = document.new_page(width=420, height=650)
    for i in range(6):
        page.insert_text((55, 80 + i * 28), "Single column LNCS style text " * 4, fontsize=10)

    profile = detect_layout_profile(document)
    document.close()

    assert profile.name == "springer_lncs"
    assert profile.columns == 1


def test_learns_template_profile_from_representative_pdf(tmp_path):
    pdf_path = tmp_path / "ieee.pdf"
    document = fitz.open()
    page = document.new_page(width=612, height=792)
    page.insert_text((54, 50), "IEEE Conference Paper")
    for i in range(8):
        page.insert_text((54, 90 + i * 22), "Left column text " * 5, fontsize=9)
        page.insert_text((330, 90 + i * 22), "Right column text " * 5, fontsize=9)
    document.save(pdf_path)
    document.close()

    learned = learn_layout_template([pdf_path], template_name="ieee_custom")

    assert learned["template_name"] == "ieee_custom"
    assert learned["_metadata"]["source_count"] == 1
    assert learned["layout"]["columns"] >= 2
    assert learned["layout"]["left_margin_median"] > 0
    assert learned["typography"]["font_size_median"] > 0
    assert learned["policy"]["learned"] is True


def test_writes_learned_template_profile(tmp_path):
    pdf_path = tmp_path / "single.pdf"
    output_path = tmp_path / "profiles" / "single.json"
    document = fitz.open()
    page = document.new_page(width=420, height=650)
    for i in range(6):
        page.insert_text((55, 80 + i * 28), "Single column LNCS style text " * 4, fontsize=10)
    document.save(pdf_path)
    document.close()

    learned = write_learned_layout_template([pdf_path], output_path, template_name="springer")

    assert output_path.exists()
    assert learned["template_name"] == "springer"
    assert learned["layout"]["columns"] == 1
