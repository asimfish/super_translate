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
  of page 13 and resumes below a float on page 14. Providers returned either
  the complete paragraph for both fragments or the complete paragraph for the
  first fragment plus the correct suffix for the continuation. Both responses
  duplicated page-14 content and shrank page 13 from 9.17pt to 4.20pt.

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

## `roboguardian_p5_booktabs_table.pdf`

- Title: *RoboGuardian: Fault-Aware Execution and Memory Maintenance for
  Embodied Agent Harness Frameworks*
- Venue: DAI 2026 submission
- Source: production upload; no public source URL is declared in the supplied
  PDF.
- Source PDF SHA256: `59a97dff659346f7705065e7d0960943efe62b1a0443758feea1a81f1e6c1324`
- Extracted page: PDF page 5
- Fixture SHA256: `c059663f5ea9083b82c6b4488230ffc8eb9169daab5ebd7e9a299c37ef845368`
- License: not declared in the supplied PDF. This single-page excerpt is
  retained solely as a regression fixture for internal layout QA and should
  not be redistributed independently of this test purpose.
- Test purpose: Table 2 uses three booktabs horizontal rules: the top and
  header rules are only 13.6pt apart, while the definition rows extend 143.4pt
  to the bottom rule. The table content must remain verbatim, its caption must
  translate, and the fused `3.6 Implementation Details` heading plus following
  prose must remain translation units.

Reproduce the fixture while retaining the original page content streams:

```python
import fitz

source = fitz.open("RoboGuardian.pdf")
source.select([4])
source.save(
    "tests/fixtures/roboguardian_p5_booktabs_table.pdf",
    garbage=4,
    deflate=True,
    clean=False,
)
```

## Robustness page-6 table-note fixtures

- Title: *Robustness Begins Before Policy Execution: State and Instruction
  Canonicalization for Compact Vision-Language-Action Policies*
- Venue: anonymous submission
- Source: production upload; no public source URL is declared in the supplied
  PDF.
- Source PDF SHA256:
  `85b677b6fc4a98c1b3b261ecefab11972bf4d3a643a5bf4bbf4afecebe372eda`
- Failed translated PDF SHA256:
  `095966b0fe419a21f60092420146342df7d0a011a53a7f0904ca103ef54d8307`
- Extracted page: PDF page 6
- Source fixture SHA256:
  `22bc8baa8691c7eb30bc0b15aac13e1dcb5079b5d8ea7ae4eb4717cf6d1bef5e`
- Paired translated fixture SHA256:
  `4ccf12809417e517df9b656707e7b313cc06253b0ddd14067f680d525a059a37`
- License: not declared in the supplied PDF. These single-page excerpts are
  retained solely as regression fixtures for internal layout QA and should
  not be redistributed independently of this test purpose.
- Test purpose: Table 1 stores its last two result rows and the prose footnote
  in one source text object that crosses the final booktabs rule. The payload
  is intentionally preserved, while QA must not misclassify it as an
  untranslated body paragraph.

Reproduce the fixtures while retaining the page content streams:

```python
import fitz

for source_path, output_path in (
    ("Robustness.pdf", "tests/fixtures/robustness_p6_table_note.pdf"),
    (
        "Robustness-translated-failed.pdf",
        "tests/fixtures/robustness_p6_table_note_translated.pdf",
    ),
):
    source = fitz.open(source_path)
    fixture = fitz.open()
    fixture.insert_pdf(source, from_page=5, to_page=5)
    fixture.save(output_path, garbage=4, deflate=True, clean=False)
    fixture.close()
    source.close()
```

## Price layout fixtures

- Title: *The Price of Algorithmic Monoculture in Congestion-Sensitive Routing*
- Venue: anonymous submission
- Source: production upload; no public source URL is declared in the supplied
  PDF.
- Source PDF SHA256: `c659ddda106981b5ec29e0d16c7e06114fe329e208367c77547d460726051d38`
- Extracted pages: PDF pages 2, 3, and 5
- Fixture SHA256 (page 2):
  `11e011cf453e8bb750326063936ee35426c21312d6181247b327f20885064252`
- Fixture SHA256 (page 5):
  `522ff8eeb481f1340a19b3c846a2f6a4534531ba1a948073112f020d9cb04fb5`
- Fixture SHA256 (page 3):
  `00c962e002c093c80ac5fb02178e1385342c6e2c9737b919c9d63b483b5e238f`
- Paired failed page-2 translation fixture:
  `price_p2_heading_body_translated.pdf`
- Failed translation PDF SHA256:
  `c8c17e4e1557e5702453e4818516d911011fc5edc8b1f9f784a2e05dd83714ca`
- Paired fixture SHA256:
  `2a1dca6fadc6663bebdb52c138f522bd82faf77577106fa32ae5f4f8ada409d0`
- Paired failed page-3 translation fixture:
  `price_p3_formula_connectors_translated.pdf`
- Failed full replay SHA256:
  `c0ce5529ff3b173963a19331ef23e8f3df9c41d71045a08db5289039729794b1`
- Paired page-3 fixture SHA256:
  `9b67d414dc5bb8576fb8054e5951e98e247fae40dc3cb3c88fdfb0e941fb330e`
- License: not declared in the supplied PDF. These single-page excerpts are
  retained solely as regression fixtures for internal layout QA and should
  not be redistributed independently of this test purpose.
- Test purpose: table bodies were preserved while their parallel header cells
  (`Symbol or metric`, `Policy family`, and `Component`) remained translation
  units. That produced half-translated tables and false untranslated-prose QA
  errors. The failed page-2 translation also merges a correctly sized 10.91pt
  heading and 8.33pt body line into one extracted text block; font QA must
  compare spans within each source role rather than assign the body's dominant
  size to the heading. Captions and body sections around tables still translate.
  Page 3 captures prose fragmented around display and inline formulas: `is`,
  `Writing`, and `to` must remain owned by continuous translatable prose instead
  of surviving as isolated English text in the output.

Reproduce the fixtures:

```python
import fitz

source = fitz.open("Price-of-Algorithmic-Monoculture.pdf")
for page, name in (
    (1, "price_p2_parallel_table_headers.pdf"),
    (2, "price_p3_formula_connectors.pdf"),
    (4, "price_p5_parallel_table_headers.pdf"),
):
    fixture = fitz.open()
    fixture.insert_pdf(source, from_page=page, to_page=page)
    fixture.save(f"tests/fixtures/{name}", garbage=4, deflate=True)
    fixture.close()
```

Reproduce the paired failed page-2 translation fixture:

```python
import fitz

source = fitz.open("Price-of-Algorithmic-Monoculture-translated-failed.pdf")
fixture = fitz.open()
fixture.insert_pdf(source, from_page=1, to_page=1)
fixture.save(
    "tests/fixtures/price_p2_heading_body_translated.pdf",
    garbage=4,
    deflate=True,
    clean=False,
)
```

Reproduce the paired failed page-3 translation fixture from the current replay:

```python
import fitz

source = fitz.open("Price-of-Algorithmic-Monoculture-translated-failed.pdf")
fixture = fitz.open()
fixture.insert_pdf(source, from_page=2, to_page=2)
fixture.save(
    "tests/fixtures/price_p3_formula_connectors_translated.pdf",
    garbage=4,
    deflate=True,
    clean=False,
)
```

## `evicoord_p8_cross_column_references.pdf`

- Title: *EviCoord: A Domain-Adaptable Evidence-State Coordination Protocol
  for Multi-Agent AI-for-Science*
- Venue: DAI 2026 submission
- Source: production upload; no public source URL is declared in the supplied
  PDF.
- Source PDF SHA256: `54c07d5fba5325c06e05829e632506237212fc9c3f997554d17a1760b0767f5e`
- Extracted page: PDF page 8
- Fixture SHA256:
  `6b309c9efed12c169f1ad82095b4938c20fd8308dde99a130ed9dd1d7c21feb9`
- Paired failed translation fixture:
  `evicoord_p8_cross_column_references_translated.pdf`
- Failed translation PDF SHA256:
  `c47279bf8e68d62626a6c7a3d4fd7a1bb97624c673516f6a6ad0aff81194d953`
- Paired fixture SHA256:
  `e8fe8f97d6b5601df0effd07cd005c88ed6a80da573284dc2a96f57f60a549de`
- License: not declared in the supplied PDF. This single-page excerpt is
  retained solely as a regression fixture for internal layout QA and should
  not be redistributed independently of this test purpose.
- Test purpose: the References heading starts near the bottom of the left
  column, while entries continue at the top of the right column. The QA
  bibliography range must follow two-column reading order instead of treating
  the heading y-coordinate as a page-wide lower bound.

Reproduce the fixture:

```bash
pdfseparate -f 8 -l 8 EviCoord.pdf evicoord-page-%d.pdf
mv evicoord-page-8.pdf tests/fixtures/evicoord_p8_cross_column_references.pdf
```

Reproduce the paired failed translation fixture while preserving the page's
text fragmentation:

```python
import fitz

source = fitz.open("EviCoord-translated-failed.pdf")
fixture = fitz.open()
fixture.insert_pdf(source, from_page=7, to_page=7)
fixture.save(
    "tests/fixtures/evicoord_p8_cross_column_references_translated.pdf",
    garbage=4,
    deflate=True,
    clean=False,
)
```

## `evicoord_p3_algorithm_reference_prose.pdf`

- Title: *EviCoord: A Domain-Adaptable Evidence-State Coordination Protocol
  for Multi-Agent AI-for-Science*
- Venue: DAI 2026 submission
- Source: production upload; no public source URL is declared in the supplied
  PDF.
- Source PDF SHA256: `54c07d5fba5325c06e05829e632506237212fc9c3f997554d17a1760b0767f5e`
- Extracted page: PDF page 3
- Fixture SHA256:
  `382946042241b9503cef8c2a99a5125e8f960658faf6162ae1491f5f5faeedfd`
- License: not declared in the supplied PDF. This single-page excerpt is
  retained solely as a regression fixture for internal layout QA and should
  not be redistributed independently of this test purpose.
- Test purpose: section 3.3 begins with the prose sentence `Algorithm 1
  implements the protocol used in our evaluation.` The sentence references a
  float on the next page and must translate; it is not itself an algorithm
  title or pseudocode region.

Reproduce the fixture:

```python
import fitz

source = fitz.open("EviCoord.pdf")
fixture = fitz.open()
fixture.insert_pdf(source, from_page=2, to_page=2)
fixture.save(
    "tests/fixtures/evicoord_p3_algorithm_reference_prose.pdf",
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
