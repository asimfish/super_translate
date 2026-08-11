# Golden PDF fixtures

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

```python
import fitz
src = fitz.open("tests/fixtures/otf_production_acceptance_full.pdf")
out = fitz.open()
out.insert_pdf(src, from_page=1, to_page=1)
out.save("tests/fixtures/otf_p02_contributions.pdf", garbage=3, deflate=True)
```
