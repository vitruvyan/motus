"""Collect at least five independent ``bench_motus.py`` runs."""

from __future__ import annotations

import json
import statistics
import subprocess
import sys
from pathlib import Path

MIN_RUNS = 5
HERE = Path(__file__).resolve().parent
BENCH = HERE / "bench_motus.py"
TRACKED = [
    ("noop_100_overhead_ms", ("2_runner_noop", "100", "overhead_us_per_node_min"), 0.1),
    ("noop_100_overhead_median_ms", ("2_runner_noop", "100", "overhead_us_per_node_median"), 0.1),
    ("realistic_1000_us_per_node_min", ("3_runner_realistic", "1000", "us_per_node_min"), 1.0),
    ("realistic_1000_us_per_node_median", ("3_runner_realistic", "1000", "us_per_node_median"), 1.0),
    ("to_dict_min_ms_realistic", ("6_serialization", "realistic_1000", "to_dict_min_ms"), 1.0),
    ("json_dumps_min_ms_realistic", ("6_serialization", "realistic_1000", "json_dumps_min_ms"), 1.0),
    ("superlinearity_t1000_over_10x_t100", ("4_scaling", "t1000_vs_10x_t100_realistic"), 1.0),
    ("window_growth_ratio_last_over_first", ("4_scaling", "growth_ratio_w10_over_w1"), 1.0),
]


def dig(document, path):
    for key in path:
        document = document[key]
    return document


def main() -> int:
    count = int(sys.argv[1]) if len(sys.argv) > 1 else MIN_RUNS
    if count < MIN_RUNS:
        print(f"refusing {count} runs: at least {MIN_RUNS} are normative", file=sys.stderr)
        return 2
    runs = []
    for _ in range(count):
        completed = subprocess.run(
            [sys.executable, str(BENCH)], cwd=HERE.parent,
            capture_output=True, text=True,
        )
        if completed.returncode:
            sys.stderr.write(completed.stderr)
            return completed.returncode
        runs.append(json.loads(completed.stdout))
    summary = {}
    for label, path, scale in TRACKED:
        values = [dig(run, path) * scale for run in runs]
        low, high = min(values), max(values)
        summary[label] = {
            "median_across_runs": statistics.median(values),
            "min_across_runs": low,
            "max_across_runs": high,
            "spread_pct": (high - low) / low * 100 if low else None,
            "samples": values,
        }
    output = {
        "kind": "motus-benchmark-baseline",
        "runs_collected": count,
        "method": {
            "warmups_per_measurement": 2,
            "measured_samples_per_measurement": 7,
            "gc_collect_before_each_sample": True,
            "aggregation": "median across independent runs; spread published",
        },
        "env": runs[0]["env"],
        "summary": summary,
        "runs": runs,
    }
    json.dump(output, sys.stdout, indent=1)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
