# Motus 0.5 performance status

## Status

The Motus benchmark implementation is present in:

- `benchmarks/bench_motus.py`;
- `benchmarks/collect_motus_baseline.py`.

It follows the normative method: two discarded warmups, seven measured
samples, `gc.collect()` before each, and a collector floor of five independent
runs. The Axis benchmark, collector and baseline are unchanged.

No candidate baseline is committed yet. The current machine is Windows with
Python 3.14.6, not the required reference profile (Python 3.10.12 on the EPYC
guest), so presenting its output as the release baseline would be false.

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
candidate, and requires the candidate to attest Motus 0.5, Python 3.10.12,
the operating system and an AMD EPYC CPU model. Local Windows evidence cannot
masquerade as release evidence.

## Release work still required

1. Run at least five independent collections on the reference Python 3.10.12
   EPYC profile and commit the raw candidate document.
2. Review the local timing debts against that evidence.
3. Add the candidate baseline invocation to CI in its separately authorized
   pull request.
