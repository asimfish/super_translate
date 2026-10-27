from pathlib import Path

import fitz
import pytest

from pdf_zh_translator import pdf_layout as layout

FIXTURE = Path(__file__).parent / "fixtures/nature_quantum_51162_p9.pdf"


@pytest.mark.parametrize(
    "text,baseline,rule_width,color,opacity,expected",
    [
        ("https://doi.org/10.1038/example", 25, 520, (0, 0, 0), 1, True),
        ("https://doi.org/10.1038/example", 770, 520, (0, 0, 0), 1, True),
        ("https://doi.org/10.1038/example", 300, 520, (0, 0, 0), 1, False),
        ("Read the article https://doi.org/10.1038/example", 25, 520, (0, 0, 0), 1, False),
        ("Read the article", 25, 520, (0, 0, 0), 1, False),
        ("https://doi.org/10.1038/example", 25, 0, (0, 0, 0), 1, False),
        ("https://doi.org/10.1038/example", 25, 120, (0, 0, 0), 1, False),
        ("https://doi.org/10.1038/example", 25, 520, (1, 1, 1), 1, False),
        ("https://doi.org/10.1038/example", 25, 520, (0, 0, 0), 0, False),
    ],
)
def test_source_margin_uri_requires_visible_rule_and_margin(
    text, baseline, rule_width, color, opacity, expected
):
    with fitz.open() as document:
        page = document.new_page(width=600, height=800)
        page.insert_text((100, baseline), text, fontsize=8)
        rule_y = baseline - 14 if baseline > 720 else baseline + 7
        if rule_width:
            page.draw_line(
                (40, rule_y),
                (40 + rule_width, rule_y),
                color=color,
                stroke_opacity=opacity,
                width=0.3,
            )
        obstacles = layout._source_margin_uri_obstacles(page)
        assert bool(obstacles) == expected
        if expected:
            assert obstacles[0][0] == 40 and obstacles[0][2] == 560
            assert obstacles[0][1] <= rule_y <= obstacles[0][3]


def test_actual_source_nature_header_obstacle():
    with fitz.open(FIXTURE) as document:
        page = document[0]
        assert not any(link.get("uri") for link in page.get_links())
        obstacles = layout._source_margin_uri_obstacles(page)
        assert len(obstacles) == 1
        rect = fitz.Rect(obstacles[0])
        assert rect.x0 == pytest.approx(39.6849976)
        assert rect.x1 == pytest.approx(561.2606201)
        assert rect.y0 == pytest.approx(25.0934372)
        assert rect.y1 == pytest.approx(38.0410156)
