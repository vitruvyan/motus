"""Measure two source trees with one harness, interleaved, on one machine.

The absolute gate this replaces compared a candidate against a ceiling derived
from a specific machine, and checked only that the CPU model contained
``EPYC``. That is not a performance class: an EPYC 7763 measured 29 % slower
than the EPYC 9V74 the ceiling came from, so the same code passed or failed
depending on which host a shared runner happened to allocate. A gate that
answers differently on identical code is not measuring the code.

This measures the previous release and the candidate **in the same job, on the
same host, alternating**, and reports the ratio. Machine speed cancels; drift
during the job is spread across both subjects rather than concentrated in one.

One harness, two subjects. ``bench_motus.py`` comes from the candidate
checkout in both cases, and only ``PYTHONPATH`` changes — so a difference in
the numbers cannot come from a difference in how they were taken.

Usage::

    python benchmarks/collect_relative_baseline.py \\
        --baseline-src /path/to/previous/src --baseline-ref v0.6.1 \\
        --candidate-src src --candidate-ref release/0.7.0 \\
        --pairs 5
"""

from __future__ import annotations

import argparse
import json
import platform
import statistics
import subprocess
import sys
from pathlib import Path

MIN_PAIRS = 5
HERE = Path(__file__).resolve().parent
BENCH = HERE / "bench_motus.py"

try:  # package import under pytest; direct import when executed as a script
    from .collect_baseline import TRACKED
except ImportError:  # pragma: no cover - exercised by the CI command itself
    from collect_baseline import TRACKED

# The metrics the ratio is gated on. The rest are collected and published, but
# a regression budget only means something for a metric whose value is a cost.
GATED = (
    "realistic_1000_us_per_node_min",
    "noop_100_overhead_ms",
    "to_dict_min_ms_realistic",
)


def dig(document, path):
    for key in path:
        document = document[key]
    return document


def one_run(source: Path) -> dict:
    """One `bench_motus.py` run against `source`, using the candidate's harness."""
    completed = subprocess.run(
        [sys.executable, str(BENCH)],
        cwd=HERE.parent,
        capture_output=True,
        text=True,
        env={**_environ(), "PYTHONPATH": str(source)},
    )
    if completed.returncode:
        sys.stderr.write(completed.stderr)
        raise SystemExit(completed.returncode)
    return json.loads(completed.stdout)


def _environ() -> dict:
    import os

    return dict(os.environ)


def summarise(runs: list[dict]) -> dict:
    summary = {}
    for key, path, _tolerance in TRACKED:
        samples = [float(dig(run, path)) for run in runs]
        low, high = min(samples), max(samples)
        summary[key] = {
            "median_across_runs": statistics.median(samples),
            "min_across_runs": low,
            "max_across_runs": high,
            "spread_pct": (high - low) / low * 100 if low else None,
            "samples": samples,
        }
    return summary


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--baseline-src", type=Path, required=True)
    parser.add_argument("--baseline-ref", required=True)
    parser.add_argument("--candidate-src", type=Path, required=True)
    parser.add_argument("--candidate-ref", required=True)
    parser.add_argument("--pairs", type=int, default=MIN_PAIRS)
    parser.add_argument(
        "--anchor-ref",
        default=None,
        help="an older release also measured here, for cumulative drift",
    )
    parser.add_argument("--anchor-src", type=Path, default=None)
    args = parser.parse_args(argv)

    if args.pairs < MIN_PAIRS:
        print(
            f"refusing {args.pairs} pairs: at least {MIN_PAIRS} are normative",
            file=sys.stderr,
        )
        return 2

    subjects = {
        "baseline": (args.baseline_ref, args.baseline_src.resolve()),
        "candidate": (args.candidate_ref, args.candidate_src.resolve()),
    }
    if args.anchor_ref:
        if args.anchor_src is None:
            print("--anchor-ref requires --anchor-src", file=sys.stderr)
            return 2
        subjects["anchor"] = (args.anchor_ref, args.anchor_src.resolve())

    # Interleaved, and in a rotating order, so no subject is systematically
    # measured on a colder or hotter machine than another.
    collected: dict[str, list[dict]] = {name: [] for name in subjects}
    order = list(subjects)
    for index in range(args.pairs):
        rotated = order[index % len(order):] + order[: index % len(order)]
        for name in rotated:
            collected[name].append(one_run(subjects[name][1]))

    document = {
        "kind": "motus-relative-characterization",
        "schema": 1,
        "pairs": args.pairs,
        "env": {
            "python": platform.python_version(),
            "cpu_model": _cpu_model(),
            "platform_system": platform.system(),
            "machine": platform.machine(),
        },
        "subjects": {},
        "ratios": {},
    }
    for name, (ref, source) in subjects.items():
        runs = collected[name]
        document["subjects"][name] = {
            "ref": ref,
            "runtime": runs[0]["env"]["runtime"],
            "runs": runs,
            "summary": summarise(runs),
        }

    base = document["subjects"]["baseline"]["summary"]
    for target in ("candidate",) + (("anchor",) if "anchor" in subjects else ()):
        against = document["subjects"][target]["summary"]
        # candidate/baseline, and anchor/baseline. The cumulative figure is
        # reported as candidate-over-anchor below, computed from the same job.
        document["ratios"][f"{target}_over_baseline"] = {
            key: against[key]["median_across_runs"] / base[key]["median_across_runs"]
            for key in GATED
            if base[key]["median_across_runs"]
        }
    if "anchor" in subjects:
        anchor = document["subjects"]["anchor"]["summary"]
        candidate = document["subjects"]["candidate"]["summary"]
        document["ratios"]["candidate_over_anchor"] = {
            key: candidate[key]["median_across_runs"] / anchor[key]["median_across_runs"]
            for key in GATED
            if anchor[key]["median_across_runs"]
        }

    # Diagnostic, never gated: the per-pair ratio and its spread say how much
    # of the gated figure is measurement rather than code. Interleaving is
    # meant to correlate adjacent runs; where it does not — a host with other
    # load — the paired spread is wider than either subject's own, and that is
    # the signal that the number should not be trusted to three decimals.
    candidate_summary = document["subjects"]["candidate"]["summary"]
    document["diagnostics"] = {
        "paired_ratio": {
            key: {
                "samples": (pairs := [
                    c / b
                    for b, c in zip(base[key]["samples"], candidate_summary[key]["samples"])
                    if b
                ]),
                "median": statistics.median(pairs) if pairs else None,
                "spread_pct": (max(pairs) / min(pairs) - 1) * 100 if pairs else None,
            }
            for key in GATED
        },
        "within_subject_spread_pct": {
            name: {key: document["subjects"][name]["summary"][key]["spread_pct"] for key in GATED}
            for name in subjects
        },
    }

    json.dump(document, sys.stdout, indent=2, sort_keys=True)
    sys.stdout.write("\n")
    return 0


def _cpu_model() -> str:
    try:
        for line in Path("/proc/cpuinfo").read_text(encoding="utf-8").splitlines():
            if line.startswith("model name"):
                return line.split(":", 1)[1].strip()
    except OSError:
        pass
    return platform.processor() or "unknown"


if __name__ == "__main__":
    raise SystemExit(main())
