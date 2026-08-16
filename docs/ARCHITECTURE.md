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

## Durable recovery

Worker failures and QA errors receive bounded automatic retries. If the current
engine cannot produce a clean result, the job is persisted as `repair_pending`
and the paper remains visible as `repairing`, with its best PDF and QA evidence
retained. A newer engine revision automatically requeues those jobs at startup;
the same revision does not spin on a deterministic failure. See
[ADR-0002](adr/0002-durable-translation-recovery.md).

## Quality evidence

Focused PDF fixtures lock individual defect classes. Golden-page suites cover
font/platform variants. `benchmarks/classic20/manifest.json` defines the
release benchmark across more than 20 papers and records license policy; only
Creative Commons artifacts may be exposed on the public showcase.

## Provider credentials

Authenticated users configure DeepSeek, Kimi, OpenAI, Anthropic, or GLM under
their own access scope. Keys are AES-GCM encrypted at rest, are never returned
to the browser, and are passed to the isolated translation worker through stdin
instead of the durable worker specification. Translation jobs persist their
credential owner scope so restart recovery preserves tenant ownership. Provider
base URLs are server-controlled. See [ADR-0002](adr/0002-user-scoped-provider-credentials.md).

## Resumable PDF uploads

PDFs of 8 MiB or more use a tenant-bound resumable protocol. The browser sends
4 MiB chunks with SHA256 digests, persists the upload ID locally, and asks the
server which chunks are already durable after a retry. Completion verifies the
whole PDF, serializes concurrent completion across processes, and deduplicates
by tenant plus content hash. A lost proxy response can therefore be retried
without creating another paper. Incomplete sessions expire after 24 hours. See
[ADR-0003](adr/0003-resumable-pdf-upload.md).
