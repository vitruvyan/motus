# ADR-006 — Motus 0.6 performance profile

- **Status:** ACCEPTED
- **Date:** 2026-08-04
- **Authority:** founder directive to close the final Motus 0.6 gate
- **Depends on:** ADR-001, ADR-002, ADR-005

## Context

The performance table accepted with ADR-001 is evidence from Axis v0.4.0. Its
full trace contains 2,002 events for a 1,000-node linear graph. Motus 0.6
produces 3,002 native records for the same topology: `run_started`, an
`attempt_started`, `transition`, and `routing` record per node, and the
terminal record. It also validates declaration violations. Applying the Axis
timing target to this larger evidence surface would compare different work.

The contract required a second runner class to be characterized with five
independent runs before it could gate a release. GitHub Actions run
`30935054533`, at source SHA
`9c09d8e52ef0ebcc2ec1d240c192f6b7e36a2e0e`, collected that evidence on
Python 3.10.12 and an AMD EPYC 9V74 runner. Each run used two discarded
warmups and seven measured samples with garbage collection before every
sample.

## Evidence

The raw document is committed as
`benchmarks/candidate-v0.6.0-epyc-py310.json` (SHA-256
`73da38cb5b0616bc0f5597593d5d436446628feaf658882e685a5c1bf0002b3c`). The
uploaded artifact archive had SHA-256
`3a7c35314a240aaab909e87f3172f2f474f22a405a4905731aabf082534da029`.

The asserted min-of-samples statistic, aggregated as the median across the
five runs, measured:

- 43.5001 microseconds per node for the realistic 1,000-node run, with 1.91%
  run-to-run spread;
- 3.08655 milliseconds total overhead for the 100-node no-op run, with 5.03%
  spread;
- 0.788x trace preparation versus `json.dumps`;
- 0% positive superlinear accumulation (the measured scaling ratio was
  0.9861);
- 100% trace completeness: 3,002 records and zero declaration violations.

## Decision

Axis v0.4.0 remains the immutable historical reference and its targets and
known debts are not rewritten. Motus 0.6 receives a separate regression
profile for its native trace semantics:

| Motus 0.6 SLO | Target | 25% gate ceiling |
|---|---:|---:|
| Realistic full-trace cost, n <= 1,000 | <= 45 microseconds/node | 56.25 microseconds/node |
| 100-node no-op overhead | <= 3.25 ms | 4.0625 ms |
| Trace preparation / `json.dumps` | <= 1.5x | 1.875x |
| Positive superlinear accumulation at n = 1,000 | < 10% | < 12.5% |
| Trace completeness | 100%, no sampling | 100%, no sampling |

These are regression limits, not a claim that the implementation is fully
optimized. They preserve speed as a product property—tens of microseconds
per traced node—without buying it by dropping evidence. A tighter target
requires a later ADR backed by a new five-run profile.

## Enforcement

`benchmarks/check_slo_baseline.py --candidate
benchmarks/candidate-v0.6.0-epyc-py310.json` recomputes all aggregates from
the raw runs, checks the environment identity and trace completeness, and
enforces these ceilings. CI runs this command on every pull request and push
to `main`. Tests pin both the passing evidence and rejection beyond every
ceiling.

## Consequences

Motus 0.6 may proceed only while this gate is green. The profile cannot be
silently relaxed, substituted with a local machine, or compared as if it
were the Axis workload. No PyPI publication is authorized by this ADR.
**Amended by ADR-032 (accepted 2026-09-07), decision 1: `vitruvyan-motus`
is published on PyPI from the tag of each release, by Trusted Publishing
under the PyPI organisation `vitruvyan`. The gate described above is
untouched and becomes a precondition of publication — a publication
cannot pass a gate that failed.**
