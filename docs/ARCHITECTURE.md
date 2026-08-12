# Architecture

## Translation quality pipeline

1. The translation worker creates mono and dual PDFs atomically.
2. Post-translation QA runs in an isolated subprocess and emits structured
   `TranslationIssue` records.
3. A deterministic quality loop selects a typed action, snapshots outputs,
   applies a bounded repair, and reruns every detector.
4. A candidate replaces the prior output only when its error score strictly
   improves. Otherwise the snapshot is restored.
5. The QA JSON sidecar records every pass and powers the UI and benchmark.

Single-pass mode performs one inspection and at most one conservative repair.
Iterative mode repeats the same contract until clean, no progress, or its pass
limit. See [ADR-0001](adr/0001-independent-translation-quality-loop.md).

## Quality evidence

Focused PDF fixtures lock individual defect classes. Golden-page suites cover
font/platform variants. `benchmarks/classic20/manifest.json` defines the
release benchmark across more than 20 papers and records license policy; only
Creative Commons artifacts may be exposed on the public showcase.
