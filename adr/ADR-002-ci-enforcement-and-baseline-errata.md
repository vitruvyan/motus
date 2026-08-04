# ADR-002 — Activate CI enforcement and correct baseline semantics

| | |
|---|---|
| Status | **ACCEPTED** |
| Date | 2026-08-03 |
| Deciders | Davide Baldoni, founder · Codex, CI implementation and verification |
| Approved by | Davide Baldoni, founder — 2026-08-03 |
| Amends | ADR-001 and `contract/guarantees.md` §3; no runtime semantics |

## Context

ADR-001 accepted the Motus foundation. Installing its third pre-implementation
gate exposed three documentary inconsistencies and one aggregation error:

1. the accepted contract still labelled itself `DRAFT` and MF-17 as pending;
2. guarantees.md called the last three SLO rows debts even though trace
   completeness is already 100% and is a non-negotiable invariant;
3. the row labelled “100-node no-op run overhead vs bare loop” aggregated the
   runner's total `min_ms`, without subtracting the bare-loop measurement;
4. the protected-path requirement existed only in prose.

The five raw runs are not replaced. They already contain both runner timing
and bare-loop timing, so the true overhead is deterministically recoverable.

## Decision

1. Mark the already accepted contract surfaces active and remove stale
   “pending ratification” wording. This records ADR-001's effect; it does not
   reopen its decisions.
2. State that the two failed performance rows — serialization and
   superlinear accumulation — are the measured debts. Trace completeness
   remains an invariant, never a sampling control.
3. Derive 100-node no-op overhead from the existing
   `overhead_us_per_node_*` fields and convert 100 nodes from microseconds per
   node to total milliseconds. The corrected min-of-samples median across the
   same five runs is **0.64 ms**, spread **11%**. The target remains ≤ 1 ms and
   its PASS verdict is unchanged. ADR-001's rounded 0.65 ms sentence is
   superseded only on this evidentiary point.
4. Install the third gate as `.github/workflows/ci.yml` job `slo-baseline`.
   The checker recomputes aggregates from raw runs, verifies the published SLO
   table, rejects non-finite JSON, names known debts, and supports strict
   target-plus-tolerance checking for a separately characterized candidate.
5. Freeze `tests/contract/` and `tests/compat/` mechanically with a trusted-base
   `pull_request_target` check. `tests/contract/kernel.py` remains the sole
   one-time import exception authorized by ADR-001.

## Consequences

- No SLO target, runtime behavior, package boundary, or Motus 0.5 scope changes.
- The committed baseline summary now describes what its labels claim, while
  every raw timing sample stays byte-for-byte unchanged.
- A pull request cannot weaken its own frozen-path judge because the judge is
  executed from the base revision, never from pull-request code.
- Timings from GitHub-hosted runners remain non-comparable until that runner
  class has its own five-run profile under guarantees.md §3.

## Approval

Approved by the founder on 2026-08-03 together with explicit authorization to
commit and push the CI foundation. The resulting branch SHA is the auditable
implementation record of this decision.
