# Benchmarks — the measured baseline behind guarantees.md §3

`bench_kernel.py` is the microbenchmark that produced the Axis v0.4.0
numbers cited in the contract (per-node overhead, O(n²) accumulation,
serialization tax). `collect_baseline.py` runs it N times and writes
`baseline-v0.4.0-epyc-py310.json`: 5 independent runs, their aggregate, and
the observed run-to-run spread, on the reference profile (Python 3.10.12,
AMD EPYC vCPU guest — the production VPS class), 2026-08-03.

**Why 5 runs and not one.** The same measurement varies run to run on this
profile — the in-run median by 27%, the min-of-samples by ≤ 11% — while the
guest's own load average stays flat, so the contention is at the hypervisor
and invisible from inside. A single run is an anecdote; a gate built on one
would fail on noise and pass on regressions. guarantees.md §3 therefore
asserts on the min-of-samples aggregated as the median across runs, publishes
the spread, and sets the tolerance above it.

Method rules are normative in guarantees.md §3: 2 warmup calls discarded,
then ≥7 measured samples with `gc.collect()` before each; ≥5 independent runs
per baseline; ±25% tolerance; raw JSON committed; cProfile never quoted as
wall time.

The CI job that asserts the §3 SLO table against this baseline is one of
the three gates listed in contract/README.md ("Gates required before
implementation") — it lands with the CI foundation, before Motus 0.5
implementation begins.
