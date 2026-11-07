# ADR-0002: Durable Translation Recovery

## Status

Accepted

## Context

Translation workers can time out or crash, and post-translation QA can reject a
rendered PDF. Treating the first recoverable error as a terminal failure leaves
the user with no recovery path. Retrying the same deterministic failure forever
would consume supplier quota, occupy the only worker slot, and still lose state
when the process restarts.

## Decision

Each job records a bounded attempt budget, the current engine revision, and the
latest issue fingerprint. Transient worker failures and supported untranslated-prose
QA findings are retried within that budget. Before parking a native non-OCR draft,
layout errors also trigger two isolated renders from that exact job's model cache:
source font scale and compact text-box padding. These require no supplier request.
Candidates are fully verified and must reduce blocking errors without increasing
any blocking page/code count; worse candidates and partial dual-PDF installs are
rolled back. Missing/foreign caches cannot be used. Each candidate has a deadline
(`translation_layout_repair_timeout_seconds`, default 300 seconds) and cancellation
polling. Verification subprocess failure gets one fresh-process retry and remains
an error if neither check completes. The QA report records each candidate outcome.
Unsupported deterministic findings
stop earlier; another full render of the same source/cache cannot repair them.
The best rendered output and QA report are retained across attempts.

When the budget is exhausted or no supported improving action remains, the paper
enters `repairing` and its job enters
`repair_pending`. This is a durable, non-terminal product state: the output,
error detail, parameters, and attempt history remain available. A startup under
a newer translation-engine revision resets the attempt budget and automatically
queues the job again. Restarting the same revision leaves it parked so an
unchanged deterministic defect cannot create an infinite supplier-cost loop.

Missing input files, invalid paths or credentials, explicit cancellation, and
deleted papers remain terminal because another render cannot repair them.

## Alternatives

### Unbounded in-process retry

Rejected because it is not durable, can monopolize the single worker, and can
repeat an identical deterministic defect indefinitely.

### Immediate terminal failure

Rejected because transient worker failures and newly fixed QA defects require
manual resubmission and lose the distinction between unrecoverable input and a
system defect awaiting repair.

### Repeating one layout render

Rejected as the recovery path for rejected layouts: the same geometry and cached
text cannot correct a deterministic layout fault. Two fixed, reviewable render
profiles add a real alternative while keeping formula protection, the QA gate,
the supplier budget, and source ownership unchanged. OCR replays remain excluded
until the exact OCR source coordinates have durable storage.

### External distributed queue

Deferred. A queue with leases and fencing is the correct next step for a
multi-node service, but the current single-node deployment can provide durable
recovery with the existing SQLite job table.

## Consequences

- The UI distinguishes active translation, waiting for system repair, and true
  failure.
- A deployment can resume repair-pending work without user action.
- Attempt metadata provides an audit trail for support and QA.
- The attempt budget limits duplicate API cost while preserving the obligation
  to repair and rerun failed papers after engine changes.
