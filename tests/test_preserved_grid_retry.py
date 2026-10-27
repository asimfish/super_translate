from types import SimpleNamespace

from pdf_zh_translator import page_inspector as inspector
from pdf_zh_translator import pdf_layout as m


def test_preserved_three_row_numeric_grid_with_section_heading_is_not_prose():
    spans = []
    for y in (100, 110, 120):
        for text, x0, x1 in [
            ("Class", 10, 35),
            ("Membership", 100, 150),
            ("Compound", 200, 240),
            ("57", 300, 315),
            ("10", 335, 350),
        ]:
            spans.append(SimpleNamespace(text=text, bbox=(x0, y, x1, y + 8), font="Times"))
    for y in (130, 140):
        spans.append(
            SimpleNamespace(
                text="Elements in this order and their definitions",
                bbox=(10, y, 350, y + 8),
                font="Times",
            )
        )
    block = SimpleNamespace(spans=spans, text=" ".join(s.text for s in spans))
    assert inspector._looks_like_aligned_preserved_rows(block)


def test_three_wrapped_prose_rows_do_not_become_a_numeric_table():
    spans = [
        SimpleNamespace(
            text="We train the model for 10 epochs and evaluate 20 samples",
            bbox=(10, y, 350, y + 8),
            font="Times",
        )
        for y in (100, 110, 120)
    ]
    assert not inspector._looks_like_aligned_preserved_rows(
        SimpleNamespace(spans=spans, text=" ".join(s.text for s in spans))
    )


def test_compact_named_math_assignments_are_preserved_diagram_labels():
    assert not m._looks_like_untranslated_english(
        "quality = meani q(Ri)quantity = 1-e-n/τ , n = executable routes"
    )
    assert m._looks_like_untranslated_english(
        "We use quality = 1 and quantity = 2 to evaluate the model in these experiments."
    )


def test_supplement_reference_list_after_appendix_preserves_entries_only():
    references = (
        "S3. A. Smith, A crystal structure. Nature 351, 549–552 (1991). "
        "doi: 10.1000/example3. S4. B. Jones, A measured phase. "
        "Physical Review B 56, 2831–2835 (1997). doi: 10.1000/example4."
    )
    state = (dict(m._bibliography_seen), m._bibliography_ended)
    try:
        m._bibliography_seen[10] = True
        m._bibliography_ended = True
        block = m.TextBlock(15, (125, 84, 506, 431), references, 9.96, (0, 0, 0))
        assert m._is_bibliography_context(block, [block])
        prose = m.TextBlock(
            15,
            (108, 460, 504, 500),
            "In Tables S3 and S4 we compare measurements from 1991 and 1997. "
            "The results show that the model improves the prediction.",
            9.96,
            (0, 0, 0),
        )
        assert not m._is_bibliography_context(prose, [prose])
        assert m._bibliography_ended
    finally:
        m._bibliography_seen.clear()
        m._bibliography_seen.update(state[0])
        m._bibliography_ended = state[1]


def test_caption_anchored_table_covers_tall_wrapped_rows_and_header():
    import fitz

    doc = fitz.open()
    try:
        page = doc.new_page()
        for y in (120, 147, 251, 355, 437):
            page.draw_line((110, y), (500, y))
        caption = m.TextBlock(
            0,
            (108, 82, 504, 114),
            "Table 26: Construction methods.",
            10,
            (0, 0, 0),
            block_type="caption",
        )
        cells = [
            m.TextBlock(0, (x, y, x + 60, y + 12), "wrapped cell", 10, (0, 0, 0))
            for y in (130, 170, 270, 380)
            for x in (115, 270, 410)
        ]
        regions = m._vector_table_region_bboxes(page, [caption, *cells])
        assert any(
            left <= 110 and top <= 120 and right >= 500 and bottom >= 437
            for left, top, right, bottom in regions
        )
        assert not m._vector_table_region_bboxes(page, cells)
    finally:
        doc.close()


def test_supplement_reference_pdf_object_can_end_before_last_doi():
    text = (
        "and K. Smith. A structure. Nature 351, 632–634 (1991). "
        "doi:10.1000/one.S5. G. Jones. A static phase. Review B 73 (2006). "
        "doi:10.1000/two.S6. J. Brown. Influence of substitution. Review B 64 (2001). Tables I and"
    )
    assert m._looks_like_reference_entry_text(text)


def test_tall_table_rules_do_not_cross_following_section():
    import fitz

    doc = fitz.open()
    try:
        page = doc.new_page()
        for y in (120, 147, 251, 400):
            page.draw_line((110, y), (500, y))
        caption = m.TextBlock(
            0, (108, 82, 504, 114), "Table 3: Measured values.", 10, (0, 0, 0), block_type="caption"
        )
        cells = [
            m.TextBlock(0, (x, y, x + 60, y + 12), "wrapped cell", 10, (0, 0, 0))
            for y in (130, 170)
            for x in (115, 270, 410)
        ]
        heading = m.TextBlock(
            0,
            (108, 280, 400, 292),
            "C.4 ONLINE EXTENSION AND CACHING",
            10,
            (0, 0, 0),
            block_type="heading",
        )
        prose = m.TextBlock(
            0,
            (108, 310, 500, 390),
            "A precursor absent from the initial cache can still be evaluated.",
            10,
            (0, 0, 0),
        )
        regions = m._vector_table_region_bboxes(page, [caption, *cells, heading, prose])
        assert regions
        assert all(bottom <= 251 for _, _, _, bottom in regions)
    finally:
        doc.close()
