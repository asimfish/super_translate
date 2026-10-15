# ADR-0009: Layered visual repair loop

## Status

Accepted

## Context

The translation pipeline can produce a readable draft while a later layout pass
still contains a visible defect, such as translated body text entering a figure.
The previous QA path checked captions and text-to-text collisions, but it did not
model newly painted text over visual regions. Repairing every text block that
intersects a figure would damage labels that are native to the figure.

## Decision

Keep the first translated PDF as a reviewable draft, then run independent visual
QA. A new `text_visual_overlap` error compares source and translated visual
regions and only reports newly overlapping translated CJK spans. Source text
already occupying the same visual region is exempted.

When this issue is reported, the deterministic quality agent sends only the
reported page numbers to the layout repairer. The repairer reflows long CJK body
blocks to the safe side of the figure, writes a new PDF, and runs the complete QA
suite again. If the result does not improve, the snapshot is restored and the
existing repair-pending recovery state is retained.

## Alternatives

### QA-only blocking

This would catch the defect but leave every affected draft waiting for manual
intervention.

### Global figure-overlap rewriting

This could move figure-internal labels and damage preserved artwork because it
lacks source ownership information.

## Consequences

- Users can inspect the draft while repair runs.
- Repairs are scoped to pages and blocks implicated by the detector.
- Existing caption, formula, and preserved-region checks remain unchanged.
- A failed or non-improving repair is rolled back and remains visible in the QA
  report instead of being silently accepted.
