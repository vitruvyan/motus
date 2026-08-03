"""Collect the versioned benchmark baseline: N independent runs of bench_kernel.py.

One run is not a baseline.  On the reference VPS profile (shared vCPU guest)
the SAME measurement varies run to run by more than the SLO tolerance, with the
guest's own load average flat — the contention is at the hypervisor, invisible
from inside.  A gate built on a single run would fail on noise and pass on
regressions, so the baseline is the MEDIAN ACROSS RUNS and it publishes the
observed spread beside it.  guarantees.md §3 is written against these fields.

Usage:  PYTHONPATH=<repo> python benchmarks/collect_baseline.py [runs] > baseline.json
"""
import json
import statistics
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
BENCH = HERE / "bench_kernel.py"

# (label, path into a run document) — the metrics guarantees.md §3 asserts on.
TRACKED = [
    ("noop_100_overhead_ms", ("2_runner_noop", "100", "min_ms")),
    ("noop_100_overhead_median_ms", ("2_runner_noop", "100", "median_ms")),
    ("realistic_1000_us_per_node_min", ("3_runner_realistic", "1000", "us_per_node_min")),
    ("realistic_1000_us_per_node_median", ("3_runner_realistic", "1000", "us_per_node_median")),
    ("to_dict_min_ms_realistic", ("6_serialization", "realistic_1000", "to_dict_min_ms")),
    ("json_dumps_min_ms_realistic", ("6_serialization", "realistic_1000", "json_dumps_min_ms")),
    ("superlinearity_t1000_over_10x_t100", ("4_scaling", "t1000_vs_10x_t100_realistic")),
    ("window_growth_ratio_last_over_first", ("4_scaling", "growth_ratio_w10_over_w1")),
]


def dig(doc, path):
    for key in path:
        if not isinstance(doc, dict) or key not in doc:
            return None
        doc = doc[key]
    return doc


def main() -> int:
    runs_wanted = int(sys.argv[1]) if len(sys.argv) > 1 else 5
    runs = []
    for _ in range(runs_wanted):
        proc = subprocess.run(
            [sys.executable, str(BENCH)], capture_output=True, text=True,
            cwd=str(HERE.parent),
        )
        if proc.returncode != 0:
            sys.stderr.write(proc.stderr)
            return proc.returncode
        runs.append(json.loads(proc.stdout))

    summary = {}
    for label, path in TRACKED:
        values = [v for v in (dig(r, path) for r in runs) if isinstance(v, (int, float))]
        if not values:
            continue
        lo, hi = min(values), max(values)
        summary[label] = {
            "median_across_runs": statistics.median(values),
            "min_across_runs": lo,
            "max_across_runs": hi,
            "spread_pct": (hi - lo) / lo * 100 if lo else None,
            "samples": values,
        }

    out = {
        "kind": "motus-benchmark-baseline",
        "runs_collected": len(runs),
        "method": {
            "warmups_per_measurement": 2,
            "measured_samples_per_measurement": 7,
            "gc_collect_before_each_sample": True,
            "aggregation": "median across independent runs; spread published",
            "note": "single-run numbers are not the baseline — see this file's docstring",
        },
        "env": runs[0].get("env"),
        "summary": summary,
        "runs": runs,
    }
    json.dump(out, sys.stdout, indent=1)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
