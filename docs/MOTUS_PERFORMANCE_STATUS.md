# Motus performance status

## Status

The Motus benchmark implementation is present in:

- `benchmarks/bench_motus.py`;
- `benchmarks/collect_motus_baseline.py`.

It follows the normative method: two discarded warmups, seven measured
samples, `gc.collect()` before each, and a collector floor of five independent
runs. The Axis benchmark, collector and baseline are unchanged.

The release candidate baseline is committed as
`benchmarks/candidate-v0.6.0-epyc-py310.json`. It was collected by GitHub
Actions run 30935054533 on Python 3.10.12 and AMD EPYC 9V74: five independent
runs, two discarded warmups and seven measured samples per measurement. The
raw file and every stored aggregate are checked in CI.

## Local engineering snapshot

Five independent non-normative runs on 2026-08-04 measured the following
min-of-seven statistic, aggregated as the median across runs:

| Obligation | Local result | Status on this profile |
|---|---:|---|
| No-op overhead, 100 nodes | 2.60 ms total; 20.6% spread | above the 1 ms target |
| Realistic full-trace cost, 1,000 nodes | 46.72 us/node; 33.1% spread | above the 15 us target |
| Serialization preparation / `json.dumps` | 1.02x; component spreads 30.5% / 16.9% | within the 1.5x target |
| Superlinear term at 1,000 nodes | 11.29%; ratio spread 90.7% | above the <10% target, highly noisy |
| Trace completeness | 100%, 3,002 native records, zero declaration violations | complete |

These numbers are diagnostic only: the interpreter and hardware profile are
not normative, and the high scaling spread makes the marginal miss especially
unsafe to over-interpret. A five-run reference measurement is still required
before deciding whether each debt is platform noise, an implementation debt,
or an SLO amendment candidate.

### 0.6 compiled-plan spot check

A non-normative single collection after making the immutable `CompiledPlan`
the runtime's default topology source measured 43.42 us/node at 1,000
realistic nodes and a 1,000-vs-100 scaling ratio of 1.05 on the same Windows
profile. Trace completeness remained 3,002 records. This is encouraging for
the superlinear term, but it is not a five-run reference result and therefore
does not alter an SLO or claim a release-grade speedup.

## Candidate-gate correction implemented

The inherited candidate checker defined completeness using the Axis event
count: 2,002 events for a 1,000-node run. A valid Motus v1 trace contains
3,002 records for the same linear graph:

- one `run_started`;
- 1,000 `attempt_started` records;
- 1,000 `transition` records;
- 1,000 `routing` records;
- one terminal record.

The checker is now profile-aware: it preserves 2,002 for the frozen Axis
reference, requires 3,002 and zero declaration violations for a Motus
candidate, and requires the candidate to attest Motus 0.6, Python 3.10.12,
the operating system and an AMD EPYC CPU model. Local Windows evidence cannot
masquerade as release evidence.

## Release gate

ADR-006 establishes a Motus-native regression profile because Motus records
3,002 records for the 1,000-node workload while the immutable Axis reference
records 2,002. The old Axis numbers remain unchanged and are not presented as
a Motus pass.

| Motus obligation | EPYC result | Gate target | Status |
|---|---:|---:|---|
| Realistic full-trace cost | 43.5001 us/node; 1.91% spread | <= 45 us/node | pass |
| No-op overhead, 100 nodes | 3.08655 ms; 5.03% spread | <= 3.25 ms | pass |
| Trace preparation / `json.dumps` | 0.788x | <= 1.5x | pass |
| Positive superlinear term | 0% (ratio 0.9861) | < 10% | pass |
| Trace completeness | 100%, 3,002 records, zero violations | 100% | pass |

The standard 25% noise tolerance is a failure ceiling, not the published
target. CI now runs the candidate checker on every change and tests that a
regression beyond each ceiling is rejected. The performance gate required for
Motus 0.6 is therefore executable rather than documentary.
