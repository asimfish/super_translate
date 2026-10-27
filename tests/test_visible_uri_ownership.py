"""Displayed URL continuations must remain source-owned, like their link target."""

import fitz
import pytest

from pdf_zh_translator import pdf_layout as layout


def source_with_link(label, uri, *, annotate=True, unrelated=False):
    document = fitz.open()
    page = document.new_page(width=612, height=792)
    page.insert_text((60, 100), "This paragraph describes the experimental method.", fontsize=11)
    page.insert_text((60, 720), label, fontsize=9, fontname="cour")
    if annotate:
        bbox = page.search_for(label)[0]
        if unrelated:
            bbox = fitz.Rect(60, 80, 300, 105)
        page.insert_link({"kind": fitz.LINK_URI, "from": bbox, "uri": uri})
    document = fitz.open(stream=document.tobytes(), filetype="pdf")
    return document


@pytest.mark.parametrize("label,uri", [
    ("mappings/mappingbased-objects", "https://databus.dbpedia.org/dbpedia/mappings/mappingbased-objects"),
    ("non-commercial-datasets/", "https://developer.imdb.com/non-commercial-datasets/"),
    ("https://example.org/data/", "https://example.org/data/"),
])
def test_actual_uri_text_is_excluded_from_translatable_blocks(label, uri):
    with source_with_link(label, uri) as document:
        blocks, _ = layout.collect_text_blocks(document)
        assert not any(label in block.text for block in blocks)
        assert any("experimental method" in block.text for block in blocks)
        assert document[0].get_links()[0]["uri"] == uri
        assert label in document[0].get_text()


@pytest.mark.parametrize("label,uri,kwargs", [
    ("Download the supplementary methods", "https://example.org/methods", {}),
    ("methods", "https://example.org/methods", {}),
    (
        "The mappings/objects ratio describes the method.",
        "https://example.org/mappings/objects", {},
    ),
    ("mappings/mappingbased-objects", "https://example.org/other", {}),
    (
        "mappings/mappingbased-objects", "https://example.org/mappings/mappingbased-objects",
        {"unrelated": True},
    ),
    (
        "mappings/mappingbased-objects", "https://example.org/mappings/mappingbased-objects",
        {"annotate": False},
    ),
])
def test_labels_prose_unmatched_and_unannotated_paths_remain_translatable(label, uri, kwargs):
    with source_with_link(label, uri, **kwargs) as document:
        blocks, _ = layout.collect_text_blocks(document)
        assert any(label in block.text for block in blocks)


def test_mixed_prose_and_uri_continuation_are_kept_as_one_reflow_owner():
    # Removing only the second line would shrink its paragraph and leave the
    # first half of a reflowed URL disconnected from its source-owned suffix.
    uri = "https://example.org/mapping/data"
    class Page:
        def get_links(self):
            return [{"uri": uri, "from": fitz.Rect(60, 100, 300, 130)}]

    lines = [
        {"bbox": (60, 100, 300, 112), "spans": [{"text": "Available at https://example.org/"}]},
        {"bbox": (60, 116, 180, 128), "spans": [{"text": "mapping/data"}]},
    ]
    raw = {"blocks": [{"type": 0, "bbox": (60, 100, 300, 128), "lines": lines.copy()}]}
    layout._preserve_annotated_uri_lines(Page(), raw)
    assert raw["blocks"][0]["lines"] == lines
