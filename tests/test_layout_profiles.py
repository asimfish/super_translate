"""Tests for academic layout profile detection."""

import fitz

from pdf_zh_translator.layout_profiles import detect_layout_profile, profile_policy


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
