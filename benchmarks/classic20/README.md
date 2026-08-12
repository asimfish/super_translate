# Classic-paper translation benchmark (classic20)

Strict, reproducible quality benchmark for the PDF translation engine over a
growing set of landmark and current ML/AI papers. Release policy: **at least 20
papers must pass the strict gate** before translation results are showcased
publicly.

## What it measures

Each paper is translated by the native engine and evaluated with the full
QA stack:

- `verify_translation_issues` - text-layer checks (untranslated prose,
  collisions, preserved regions, missing images/graphics/formulas) **plus
  the visual inspector** (`pdf_zh_translator/page_inspector.py`): cross-block
  font-size drift, sibling list size spread, inline formula sprite clipping,
  table grid mismatches, references overprint/bold bleed, untranslated
  paragraph blocks, display equation misalignment.
- `score_visual_layout` - render-based ink similarity.

Gates per paper:

- `strict_pass`: zero error-severity issues and visual score >= 0.55.
- `legacy_pass`: same, but ignoring inspector codes (tracks the pre-inspector
  contract while rendering fixes land class by class).

## Paper set

`manifest.json` currently curates 43 papers: 25 landmarks, six CC-licensed
showcase papers, and 12 recent stress papers. Together they cover the layout
axes the engine must handle (single/two column, math-dense, algorithm blocks,
table-heavy, figure-dense, long references, appendix-heavy, short/long). The
fetch step records per-paper license, source URL, SHA256 and byte size in
`data/benchmark/classic20/meta/`.

**License policy**: `showcase_ok` is true only for Creative Commons
licensed papers; only those may have full translated pages displayed in the
public showcase. Papers under the arXiv non-exclusive licence contribute
metrics (and internal review artifacts) only, unless permission is obtained.

The 25 classics all carry the arXiv non-exclusive licence, so the manifest
additionally curates a `"group": "showcase_cc"` set of CC BY 4.0 papers
(LLaMA, Mistral 7B, Mamba, DPO, Chain-of-Thought, vLLM - licenses verified
at fetch time) whose original-vs-translated page pairs power the public
comparison wall.

## Running

```sh
.venv/bin/python scripts/classic_benchmark.py fetch
.venv/bin/python scripts/classic_benchmark.py translate            # needs DEEPSEEK_API_KEY
.venv/bin/python scripts/classic_benchmark.py evaluate
.venv/bin/python scripts/classic_benchmark.py report
.venv/bin/python scripts/classic_benchmark.py gate
```

All steps are resumable (`--force` to redo, `--only id,id` to scope).
The `gate` command writes `quality-gate.json` and exits non-zero unless at
least 20 papers were evaluated, 20 strictly pass, every layout axis is covered,
source hashes and license metadata agree, and an optional `--baseline-reports`
directory has no per-paper error-count regressions.
Artifacts land in `data/benchmark/classic20/`:

- `papers/` originals, `translations/` mono PDFs + translation caches,
- `reports/{id}.json` per-paper issue lists and scores,
- `REPORT.md` aggregate table + layout coverage,
- `showcase.json` + `previews/` page images for the web showcase
  (first pages plus every error page, original vs translated).
