# Benchmarks — the measured baseline behind guarantees.md §3

`bench_kernel.py` is the microbenchmark that produced the Axis v0.4.0
numbers cited in the contract (per-node overhead, O(n²) accumulation,
serialization tax). `baseline-v0.4.0-epyc-py310.json` is its raw output on
the reference profile: Python 3.10.12, AMD EPYC vCPU guest (the production
VPS class), min-of-7 with GC enabled, 2026-08-03.

Method rules are normative in guarantees.md §3: ≥7 samples after ≥2 warmup
runs, `gc.collect()` before each sample, SLOs asserted on the median with
min published alongside, ±20% tolerance, raw JSON committed per run,
cProfile never quoted as wall time.

The CI job that asserts the §3 SLO table against this baseline is one of
the three gates listed in contract/README.md ("Gates required before
implementation") — it lands with the CI foundation, before Motus 0.5
implementation begins.
