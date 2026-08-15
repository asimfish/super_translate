# Golden PDF fixtures

## Visual inspector regression pairs (`otf_p*_*.pdf` / `*_translated.pdf`)

Page-level pairs extracted from the OFT paper below (CC BY 4.0) and its
2026-08-11 production translation, one pair per defect class found in the
production review. Each `X.pdf` is the original page; `X_translated.pdf` is
the same page from the translated PDF exhibiting the defect. Used by
`tests/test_page_inspector.py`.

- `otf_p2_font_drift`: contribution bullets shrunk to 6.4-7.4pt vs 9.2pt body
  (`font_size_drift`, `list_font_inconsistent`)
- `otf_p4_formula_clip`: inline formula sprites with clipped
  ascenders/descenders (`formula_clipped`)
- `otf_p8_table_grid`: Table 2 header rules rebuilt at wrong offsets
  (`table_structure_mismatch`)
- `otf_p11_12_refs`: references pages 11-12; bold author names overprint the
  translated entry (`reference_overlap`, `reference_bold_style`)
- `otf_p14_display_align`: source appendix page used to synthesize a 36pt
  display-equation shift at test runtime (`display_formula_misaligned`). The
  archived translated page is not the oracle: its formulas are aligned, and
  the former warning came from misclassifying the translated `C.1` heading.
- `otf_p1_clean`: title page with no known defects (negative control)

Regenerate with `tmp/inspect_prod/make_fixtures.py` from the production
paper pair.

## `otf_production_acceptance_full.pdf`

- Title: *Optimal Flow Transport and its Entropic Regularization: a GPU-friendly Matrix Iterative Algorithm for Flow Balance Satisfaction*
- Authors: Liangliang Shi, Yufeng Li, Kaipeng Zeng, Yihui Tu, Junchi Yan
- Venue: ICLR 2025
- Source: https://proceedings.iclr.cc/paper_files/paper/2025/file/4dac4a4cf3eea44eb9b192e88d1c754a-Paper-Conference.pdf
- OpenReview record: https://openreview.net/forum?id=NtSlKEJ2DS
- Pages: 19
- SHA256: `77ca901150f8c0ec0a59700f7f2ec550f98e64e4a47083dc7cee1dcdaa77e4f4`
- License: CC BY 4.0, as declared by the OpenReview record.
- Test purpose: deterministic, non-production golden regression for text-layer ownership, untranslated prose, source-style typography, formula placement, table-caption clearance, and appendix reading order. The complete paper is retained because page-local extraction cannot exercise repeated headers or whole-document text-layer growth.

Reproduce the fixture:

```sh
curl -L --fail \
  https://proceedings.iclr.cc/paper_files/paper/2025/file/4dac4a4cf3eea44eb9b192e88d1c754a-Paper-Conference.pdf \
  -o tests/fixtures/otf_production_acceptance_full.pdf
shasum -a 256 tests/fixtures/otf_production_acceptance_full.pdf
```

## `otf_p02_contributions.pdf`

- Page 2 extracted from `otf_production_acceptance_full.pdf` (same source,
  license CC BY 4.0 as above).
- Test purpose: sibling contribution bullets must share one harmonized font
  size instead of shrinking independently (production defect: 7.4/6.4/9.2pt).

## `otf_p09_table_captions.pdf`

- Page 9 extracted from `otf_production_acceptance_full.pdf` (same source,
  license CC BY 4.0 as above).
- Test purpose: single-line table captions must stay near caption scale
  (production defect: 5.7pt) and the caption redaction must not paint over
  the table top rules (stub-rule artifact beside the caption).

## `memorywam_p3_inline_window.pdf`

- Page 3 extracted from the MemoryWAM production paper (arXiv preprint,
  uploaded by the paper owner for internal QA).
- Test purpose: the two-line body paragraph before Eq. (1) must keep body
  scale; raw Noto CJK font-file metrics previously forced it to 6.9pt
  (production defect).

## `fact_p5_piecewise_prose.pdf`

- Title: *FACT: Failure-Aware Causal Training for World-Action Models*
- Venue: CoRL 2026 submission
- Source: production upload; the PDF identifies https://fact-wam.github.io
- Source PDF SHA256: `49bbd09c992531c9b07a777cfc2a80d0f7c32a0cff3a9855f8a51c4d981c8553`
- Extracted page: PDF page 5
- Fixture SHA256: `344333dc741488632393dd49142238ecac246ee1c89dbfd08411f71ffa7f7da3`
- License: not declared in the supplied PDF. This single-page excerpt is
  retained solely as a regression fixture for internal layout QA and should
  not be redistributed independently of this test purpose.
- Test purpose: display equation (6) ends with a detached `if fail` branch.
  The branch must remain protected math without absorbing the following
  `where ... In our experiments ...` prose paragraph and causing a leak.

Reproduce the fixture from the production upload:

```python
import fitz

source = fitz.open("FACT.pdf")
fixture = fitz.open()
fixture.insert_pdf(source, from_page=4, to_page=4)
fixture.save(
    "tests/fixtures/fact_p5_piecewise_prose.pdf",
    garbage=3,
    deflate=True,
)
```

## `gears_p5_production_font_drift.pdf`

- Title: *GEARS: Seeing Geometry, Diffusing Actions for Zero-Shot Sim-to-Real Dexterous Manipulation*
- Venue: ECCV 2026 submission
- Source: production upload
- Source PDF SHA256: `423d7ecc266d0dcc802655df9966666230454e14874897b00e68502a97b2acbe`
- Extracted page: PDF page 5
- Fixture SHA256: `e5e7bf04f87420780a69ffc43965fc72e8b4a669b99bfa708607c6818bf3c238`
- License: not declared in the supplied PDF. This single-page excerpt is
  retained solely as a regression fixture for internal layout QA and should
  not be redistributed independently of this test purpose.
- Test purpose: the page-bottom `Architecture.` run-in paragraph contains two
  inline formulas and a detached formula suffix. The production translation
  must borrow whitespace left by the translated paragraph above instead of
  shrinking from 9.17pt to 7.67pt.

Reproduce the fixture from the production upload while retaining the original
page content streams and text grouping:

```python
import fitz

source = fitz.open("GEARS.pdf")
source.select([4])
source.save(
    "tests/fixtures/gears_p5_production_font_drift.pdf",
    garbage=4,
    deflate=True,
    clean=False,
)
```

## `gears_p13_p14_cross_page_duplicate.pdf`

- Title: *GEARS: Seeing Geometry, Diffusing Actions for Zero-Shot Sim-to-Real Dexterous Manipulation*
- Venue: ECCV 2026 submission
- Source: production upload
- Source PDF SHA256: `423d7ecc266d0dcc802655df9966666230454e14874897b00e68502a97b2acbe`
- Extracted pages: PDF pages 13-14
- Fixture SHA256: `37e0170ddeefded7e38ece9e7a30b305f0a3330c2d822b0ddbc42252c3cb1653`
- License: not declared in the supplied PDF. This two-page excerpt is
  retained solely as a regression fixture for internal layout QA and should
  not be redistributed independently of this test purpose.
- Test purpose: the failure-analysis paragraph starts on the final source line
  of page 13 and resumes below a float on page 14. A provider returned the
  complete paragraph for both fragments, duplicating the translation and
  shrinking the first copy from 9.17pt to 4.20pt.

Reproduce the fixture while retaining the original page content streams and
cross-page paragraph boundary:

```python
import fitz

source = fitz.open("GEARS.pdf")
source.select([12, 13])
source.save(
    "tests/fixtures/gears_p13_p14_cross_page_duplicate.pdf",
    garbage=4,
    deflate=True,
    clean=False,
)
```

```python
import fitz
src = fitz.open("tests/fixtures/otf_production_acceptance_full.pdf")
out = fitz.open()
out.insert_pdf(src, from_page=1, to_page=1)
out.save("tests/fixtures/otf_p02_contributions.pdf", garbage=3, deflate=True)
```
