import fitz
import pytest

from pdf_zh_translator import pdf_layout as layout

URI = "https://platform.openai.com/docs/api-reference"


def restore(page, source_page):
    return getattr(layout, "_restore_source_uri_annotation_metadata", lambda *_: 0)(
        page, source_page
    )


@pytest.mark.parametrize(
    "lines,target,annotated",
    [
        (["api-reference"], URI, True),
        (["Read the API reference", "api-reference"], URI, True),
        (["https://platform.openai.com/docs/", "api-reference"], URI + "/different", True),
        (["https://platform.openai.com/docs/", "api-reference"], URI, False),
        (["Available at https://platform.openai.com/docs/", "api-reference"], URI, True),
    ],
)
def test_natural_mixed_unmatched_and_unannotated_uri_owners_are_translatable(
    lines, target, annotated
):
    class Page:
        def get_links(self):
            return [{"uri": target, "from": fitz.Rect(10, 10, 300, 60)}] if annotated else []

    raw_lines = [
        {"bbox": (10, 10 + i * 15, 300, 22 + i * 15), "spans": [{"text": text}]}
        for i, text in enumerate(lines)
    ]
    raw = {"blocks": [{"type": 0, "lines": raw_lines.copy()}]}
    layout._preserve_annotated_uri_lines(Page(), raw)
    assert raw["blocks"][0]["lines"] == raw_lines


@pytest.mark.parametrize(
    "parts",
    [
        ["https://platform.openai.com/docs/", "api-", "reference"],
        ["https://platform.openai.com/docs/", "api", "-reference"],
        ["https://platform.openai.com/docs/api-referenc", "e"],
    ],
)
def test_complete_annotated_literal_allows_short_wrapped_uri_fragments(parts):
    class Page:
        def get_links(self):
            return [{"uri": URI, "from": fitz.Rect(10, 10, 300, 70)}]

    raw_lines = [
        {"bbox": (10, 10 + i * 15, 300, 22 + i * 15), "spans": [{"text": text}]}
        for i, text in enumerate(parts)
    ]
    raw = {"blocks": [{"type": 0, "lines": raw_lines.copy()}]}
    layout._preserve_annotated_uri_lines(Page(), raw)
    assert raw["blocks"][0]["lines"] == []


def pair():
    source = fitz.open()
    page = source.new_page()
    page.insert_text((60, 100), "Read the API reference", fontsize=10)
    rect = page.search_for("Read the API reference")[0]
    page.insert_link({"kind": fitz.LINK_URI, "from": rect, "uri": URI})
    source = fitz.open(stream=source.tobytes(), filetype="pdf")
    xref = source[0].get_links()[0]["xref"]
    source.update_object(
        xref,
        "<< /Type /Annot /Subtype /Link /A << /Type /Action /S /URI /URI "
        + fitz.get_pdf_str(URI)
        + " >> /Border [0 0 0] /C [0 1 1] /H /I /Rect "
        + source.xref_get_key(xref, "Rect")[1]
        + " >>",
    )
    output = fitz.open(stream=source.tobytes(), filetype="pdf")
    xref = output[0].get_links()[0]["xref"]
    output.update_object(
        xref,
        "<< /Subtype /Link /A << /S /URI /URI "
        + fitz.get_pdf_str(URI)
        + " >> /BS << /W 0 >> /NM (fitz-L0) /Rect "
        + output.xref_get_key(xref, "Rect")[1]
        + " >>",
    )
    return source, output


def test_natural_click_label_metadata_restores_without_changing_label_or_rect():
    source, output = pair()
    with source, output:
        text = output[0].get_text()
        rect = tuple(output[0].get_links()[0]["from"])
        assert restore(output[0], source[0]) == 1
        assert output[0].get_text() == text
        assert tuple(output.reload_page(output[0]).get_links()[0]["from"]) == rect
        assert layout._realign_exact_uri_links(output[0], source[0]) == 0


@pytest.mark.parametrize(
    "case",
    [
        "source_indirect_action",
        "source_indirect_border",
        "source_unknown_action",
        "source_additional_action",
        "source_nonhttp",
        "current_custom",
        "different_rect",
        "duplicate_source_identity",
        "duplicate_current_identity",
        "rotated_page",
    ],
)
def test_unproven_or_opaque_metadata_never_crosses_documents(case):
    source, output = pair()
    with source, output:
        sx = source[0].get_links()[0]["xref"]
        ox = output[0].get_links()[0]["xref"]
        if case == "source_indirect_action":
            other = source.get_new_xref()
            source.update_object(other, source.xref_get_key(sx, "A")[1])
            source.xref_set_key(sx, "A", f"{other} 0 R")
        elif case == "source_indirect_border":
            other = source.get_new_xref()
            source.update_object(other, "[1 2 3]")
            source.xref_set_key(sx, "Border", f"{other} 0 R")
        elif case == "source_unknown_action":
            source.xref_set_key(sx, "A/Next", "<< /S /JavaScript /JS (opaque) >>")
        elif case == "source_additional_action":
            source.xref_set_key(sx, "AA", "<< /U << /S /JavaScript /JS (opaque) >> >>")
        elif case == "source_nonhttp":
            source.xref_set_key(sx, "A/URI", "(mailto:author@example.org)")
        elif case == "current_custom":
            output.xref_set_key(ox, "Contents", "(Keep current custom metadata)")
        elif case == "different_rect":
            output.xref_set_key(ox, "Rect", "[50 50 100 100]")
        elif case.startswith("duplicate_"):
            doc = source if case == "duplicate_source_identity" else output
            page = doc[0]
            link = page.get_links()[0]
            page.insert_link({"kind": fitz.LINK_URI, "from": link["from"], "uri": URI})
            del page
        elif case == "rotated_page":
            source[0].set_rotation(90)
        source_page = source.reload_page(source[0])
        output_page = output.reload_page(output[0])
        before = [output.xref_object(xref) for xref in range(1, output.xref_length())]
        assert restore(output_page, source_page) == 0
        assert [output.xref_object(xref) for xref in range(1, output.xref_length())] == before
