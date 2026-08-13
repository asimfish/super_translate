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

`manifest.json` currently curates 50 papers: 25 core landmarks, seven classic
expansion papers, six CC-licensed showcase papers, and 12 recent stress papers.
Together they cover the layout axes the engine must handle (single/two column,
math-dense, algorithm blocks, table-heavy, figure-dense, long references,
appendix-heavy, short/long). The fetch step records per-paper license, source
URL, SHA256 and byte size in `data/benchmark/classic20/meta/`.

**License policy**: `showcase_ok` is true only for Creative Commons
licensed papers; only those may have full translated pages displayed in the
public showcase. Papers under the arXiv non-exclusive licence contribute
metrics (and internal review artifacts) only, unless permission is obtained.

The core classics generally carry the arXiv non-exclusive licence, so the
manifest additionally curates a `"group": "showcase_cc"` set of papers whose
licences are verified at fetch time. The report generator exposes full
original-vs-translated page previews only when the recorded licence sets
`showcase_ok=true`; restricted papers contribute aggregate metrics only.

## Running

```sh
.venv/bin/python scripts/classic_benchmark.py fetch
.venv/bin/python scripts/classic_benchmark.py translate --isolate  # needs DEEPSEEK_API_KEY
.venv/bin/python scripts/classic_benchmark.py evaluate
.venv/bin/python scripts/classic_benchmark.py report
.venv/bin/python scripts/classic_benchmark.py gate
```

All steps are resumable (`--force` to redo, `--only id,id` to scope). Multi-paper
translation automatically gives every paper a fresh interpreter so native PDF
engine memory remains bounded. `--isolate` applies the same protection to an
explicit single-paper run.
The harness takes an OS-level exclusive lock on the canonical work directory;
a second fetch/translate/evaluate/report/gate process fails fast, even when it
runs from another Git worktree. Isolated translation children inherit that
lock. PDFs and JSON evidence are published with atomic replacement, and a
translation is rejected if its engine files or selected font pack change while
the paper is running.
Evaluation cache entries are content-addressed: source PDF SHA-256, translated
PDF SHA-256, and a QA-code fingerprint must all match. Reports also record the
QA and translation-engine commits/fingerprints. The release gate rejects stale
reports and translations whose timing metadata cannot prove which engine built
the exact output PDF. A cached translation is reused only when its source hash
and engine fingerprint still match; terminology corpus, layout, and prompt
changes therefore force a real rebuild instead of silently recycling old output.
Block-level API response caches are separately namespaced by model, prompts,
and terminology content, so a semantic translation change cannot reuse text
produced under an older policy.
The selected regular, bold, fallback, and math font files are fingerprinted as
well, because identical code with different CJK metrics is not the same layout
engine for reproducibility purposes.
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
