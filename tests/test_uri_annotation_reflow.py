import fitz
import pytest

from pdf_zh_translator import pdf_layout as layout

URI = "https://github.com/facebookresearch/head-to-tail"


def realign(output, source):
    function = getattr(layout, "_realign_exact_uri_links", lambda output, source: 0)
    return function(output, source)


def refresh(document, page_index=0):
    return document.reload_page(document[page_index])


def uri_links(page, uri=URI):
    return [link for link in page.get_links() if link.get("uri") == uri]


def assert_clicks_cover_uri(page, parts, uri=URI):
    boxes = [fitz.Rect(link["from"]) for link in uri_links(page, uri)]
    for part in parts:
        match = page.search_for(part)
        assert len(match) == 1
        rect = match[0]
        assert max((rect & box).get_area() / rect.get_area() for box in boxes) >= 0.95


def pair(
    *,
    source_label=URI,
    output_label=URI,
    source_duplicate=False,
    output_duplicate=False,
    other_page=False,
    source_target=URI,
):
    source = fitz.open()
    page = source.new_page(width=612, height=792)
    page.insert_text((60, 100), source_label, fontsize=9, fontname="cour")
    rect = page.search_for(source_label)[0]
    page.insert_link({"kind": fitz.LINK_URI, "from": rect, "uri": source_target})
    if source_duplicate:
        page.insert_text((60, 160), URI, fontsize=9, fontname="cour")
    if source_label != URI:
        page.insert_text((60, 160), URI, fontsize=9, fontname="cour")
    source = fitz.open(stream=source.tobytes(), filetype="pdf")
    output = fitz.open()
    page = output.new_page(width=612, height=792)
    page.insert_text((60, 300), "Normal translated surrounding text", fontsize=10)
    page.insert_link({"kind": fitz.LINK_URI, "from": rect, "uri": source_target})
    if other_page:
        page = output.new_page(width=612, height=792)
    page.insert_text((60, 400), output_label, fontsize=9, fontname="cour")
    if output_duplicate:
        page.insert_text((60, 460), URI, fontsize=9, fontname="cour")
    output = fitz.open(stream=output.tobytes(), filetype="pdf")
    return source, output


def test_unique_complete_same_page_uri_reanchors_without_touching_text():
    source, output = pair()
    with source, output:
        before = output[0].get_text()
        assert realign(output[0], source[0]) == 1
        page = refresh(output)
        assert_clicks_cover_uri(page, [URI])
        assert page.get_text() == before


@pytest.mark.parametrize(
    "kwargs",
    [
        {"source_label": "Download the supplementary methods"},
        {"source_target": "https://example.org/different"},
        {"output_label": "https://example.org/different"},
        {"output_label": URI + "/different-path"},
        {"output_label": URI + "?different=query"},
        {"source_duplicate": True},
        {"output_duplicate": True},
        {"other_page": True},
    ],
)
def test_ambiguous_different_or_natural_labels_never_move(kwargs):
    source, output = pair(**kwargs)
    with source, output:
        before = [layout._link_identity(link) for link in output[0].get_links()]
        assert realign(output[0], source[0]) == 0
        assert [layout._link_identity(link) for link in refresh(output).get_links()] == before


def test_source_annotation_must_cover_full_displayed_uri():
    source, output = pair()
    with source, output:
        link = source[0].get_links()[0]
        link["from"] = source[0].search_for("https://github.com/")[0]
        source[0].update_link(link)
        source_page = refresh(source)
        before = [layout._link_identity(link) for link in output[0].get_links()]
        assert realign(output[0], source_page) == 0
        assert [layout._link_identity(link) for link in refresh(output).get_links()] == before


def test_already_covered_output_uri_keeps_source_annotation_padding():
    source, output = pair()
    with source, output:
        page = output[0]
        link = page.get_links()[0]
        link["from"] = page.search_for(URI)[0] + (-1, -1, 1, 1)
        page.update_link(link)
        page = output.reload_page(page)
        before = [layout._link_identity(link) for link in page.get_links()]
        assert realign(page, source[0]) == 0
        assert [
            layout._link_identity(link) for link in output.reload_page(page).get_links()
        ] == before


def test_wrapped_source_uri_reflow_to_one_line_keeps_all_annotations():
    source = fitz.open()
    page = source.new_page(width=612, height=792)
    prefix, suffix = "https://github.com/", "facebookresearch/head-to-tail"
    for y, part in [(100, prefix), (112, suffix)]:
        page.insert_text((60, y), part, fontsize=9, fontname="cour")
        page.insert_link({"kind": fitz.LINK_URI, "from": page.search_for(part)[0], "uri": URI})
    source = fitz.open(stream=source.tobytes(), filetype="pdf")
    output = fitz.open()
    page = output.new_page(width=612, height=792)
    page.insert_text((60, 400), URI, fontsize=9, fontname="cour")
    for link in source[0].get_links():
        page.insert_link({"kind": fitz.LINK_URI, "from": link["from"], "uri": URI})
    output = fitz.open(stream=output.tobytes(), filetype="pdf")
    with source, output:
        before = output[0].get_text()
        before_objects = {
            link["xref"]: output.xref_object(link["xref"]) for link in output[0].get_links()
        }
        assert realign(output[0], source[0]) == 0
        page = refresh(output)
        assert len(uri_links(page)) == 2
        assert {
            link["xref"]: output.xref_object(link["xref"]) for link in page.get_links()
        } == before_objects
        assert page.get_text() == before


def test_one_source_annotation_reflow_to_two_lines_keeps_all_metadata():
    source, output = pair()
    with source, output:
        page = output[0]
        page.add_redact_annot(page.search_for(URI)[0])
        page.apply_redactions()
        page.insert_text((60, 400), "https://github.com/", fontsize=9, fontname="cour")
        page.insert_text((60, 412), "facebookresearch/head-to-tail", fontsize=9, fontname="cour")
        page = output.reload_page(page)
        link = page.get_links()[0]
        output.xref_set_key(link["xref"], "Contents", "(Preserve even when URI gains a line)")
        before_object = output.xref_object(link["xref"])
        before_text = page.get_text()
        assert realign(page, source[0]) == 0
        page = output.reload_page(page)
        assert len(uri_links(page)) == 1
        assert output.xref_object(link["xref"]) == before_object
        assert page.get_text() == before_text


@pytest.mark.parametrize("source_rotation,output_rotation", [(90, 0), (0, 90), (180, 180)])
def test_rotated_source_or_output_page_is_explicitly_unsupported(source_rotation, output_rotation):
    source, output = pair()
    with source, output:
        source[0].set_rotation(source_rotation)
        output[0].set_rotation(output_rotation)
        before = {link["xref"]: output.xref_object(link["xref"]) for link in output[0].get_links()}
        assert realign(output[0], source[0]) == 0
        assert {
            link["xref"]: output.xref_object(link["xref"]) for link in output[0].get_links()
        } == before


def test_reanchoring_existing_annotation_preserves_opaque_metadata():
    source, output = pair()
    with source, output:
        xref = output[0].get_links()[0]["xref"]
        for key, value in {
            "Contents": "(Original link description)",
            "C": "[1 0 0]",
            "F": "4",
        }.items():
            output.xref_set_key(xref, key, value)
        before = {
            key: output.xref_get_key(xref, key)
            for key in output.xref_get_keys(xref)
            if key != "Rect"
        }
        assert realign(output[0], source[0]) == 1
        assert {
            key: output.xref_get_key(xref, key)
            for key in output.xref_get_keys(xref)
            if key != "Rect"
        } == before
        assert_clicks_cover_uri(refresh(output), [URI])


def test_translate_pdf_calls_realign_after_output_uri_is_rendered(tmp_path, monkeypatch):
    source = fitz.open()
    page = source.new_page(width=612, height=792)
    body = (
        (
            "Our experiments compare the two training procedures "
            "under identical evaluation conditions. "
            "We report the average performance across independent runs "
            "and retain the same data split. "
            "The implementation includes the complete training configuration "
            "and the evaluation scripts. "
        )
        * 3
        + "The project is available at "
        + URI
    )
    assert page.insert_textbox((60, 250, 550, 550), body, fontsize=11, fontname="tiro") >= 0
    page.insert_link({"kind": fitz.LINK_URI, "from": page.search_for(URI)[0], "uri": URI})
    input_pdf = tmp_path / "source.pdf"
    source.save(input_pdf)
    source.close()
    original = layout._realign_exact_uri_links
    observed = []

    def inspect_rendered_uri(output, original_page):
        observed.append(len(layout._exact_page_uri_matches(output, URI)))
        return original(output, original_page)

    class ControlledTranslation:
        def translate_batch(self, texts):
            return [
                "该项目的代码和评估工具将在以下公开地址提供，以便其他研究人员能够复现实验设置：⟦0⟧。"
                for text in texts
            ]

    monkeypatch.setattr(layout, "_realign_exact_uri_links", inspect_rendered_uri)
    layout.translate_pdf(
        input_pdf,
        tmp_path / "output.pdf",
        ControlledTranslation(),
        preserve_graphics_text=True,
    )
    assert observed == [1]
    with fitz.open(tmp_path / "output.pdf") as output:
        assert "该项目" in output[0].get_text()
        assert uri_links(output[0])
