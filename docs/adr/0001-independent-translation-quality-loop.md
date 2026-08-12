# ADR-0001: Independent Translation Quality Loop

## Status

Accepted

## Context

Translation output must preserve paper structure while detecting untranslated
prose, font drift, overlap, clipped formulas, damaged tables, and changed
preserved regions. Users can request one inspection pass or bounded iterative
inspection. The native renderer currently builds a PDF atomically in one
process; it does not expose durable page checkpoints.

The existing QA subprocess isolates PyMuPDF crashes and supports snapshots, but
its repair decision is a boolean for two overlap codes. Repeating the same
generic fixer can make no progress, and formula/font defects are not represented
as typed repair decisions.

## Driving Factors

- A failed inspector must not destroy a valid translated PDF.
- Identical input and engine output must produce deterministic QA decisions.
- Every accepted repair must strictly improve the error score.
- Single-pass and iterative modes must share the same detector contract.
- The design must work locally without a second model or external service.
- Visual/LLM review may propose issues, but may not mutate PDFs directly.

## Candidates

### Option A: Post-translation deterministic sidecar loop

Run the inspector in its isolated subprocess as soon as the atomic PDF is
written. A typed planner maps issue codes to repair strategies. Each iteration
snapshots outputs, applies one strategy, reruns all detectors, and accepts only
a strict score improvement; no progress, repeated fingerprints, or the pass
limit stops the loop and restores the best output.

- Pros: deterministic, testable, crash-isolated, compatible with current PDF
  engine, and safe to run on a single machine or queue worker.
- Cons: QA starts after the full PDF exists; formula/font repair generally
  requires a fresh render rather than a destructive PDF post-process.

### Option B: Concurrent per-page translation and QA workers

Persist every translated page as a checkpoint and dispatch it to a visual QA
worker while later pages translate. Merge only accepted pages into the final
document.

- Pros: overlaps translation and QA latency; naturally localizes repairs.
- Cons: requires a new page artifact protocol, durable queue/fencing, font and
  resource reconciliation at merge time, and resumable job ownership. Sharing
  live PyMuPDF objects is unsafe, while serializing full page resources is a
  substantial engine rewrite.

## Decision

Choose Option A. The canonical detector output is the existing structured
`TranslationIssue` list serialized in the QA JSON sidecar. Repair planning is
deterministic and code-based. An optional model reviewer may append proposed
issues to a separate review artifact, but those proposals must pass deterministic
validation before becoming blocking issues or repair actions.

Option B may be reconsidered only after the renderer emits independently
renderable page checkpoints with content hashes and the job system provides
durable ownership, retries, and fencing.

## Interfaces

- Detector boundary: original PDF + translated PDF -> `TranslationIssue[]`.
- Planner boundary: issues + pass history -> typed decision (`accept`,
  `repair_layout`, `retranslate`, or `stop`).
- Repair boundary: snapshot paths + decision -> candidate output paths.
- Acceptance boundary: candidate issue score must be strictly lower than the
  best accepted score; otherwise restore the snapshot.

## Impact

- `pdf_zh_translator/page_inspector.py` remains the independent visual detector.
- `app/api/papers.py` owns job progress, subprocess invocation, snapshots, and
  report persistence.
- `app/services/layout_fix.py` remains one conservative repair strategy, not a
  universal fixer.
- `scripts/classic_benchmark.py` is the release-scale acceptance harness.

