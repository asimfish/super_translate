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
latest issue fingerprint. Worker failures and all error-severity QA findings are
retried within that budget. The best rendered output and QA report are retained
across attempts.

When the budget is exhausted, the paper enters `repairing` and its job enters
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
