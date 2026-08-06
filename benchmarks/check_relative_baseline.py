"""Gate a release on how much slower it is than the last one, not on a ceiling.

``contract/guarantees.md`` §3 used to publish absolute ceilings derived from
one host, enforced by a check that the CPU model contained ``EPYC``. That is
not a performance class. An EPYC 7763 measured 29 % slower than the EPYC 9V74
the ceilings came from, so v0.6.1's own code — unchanged, already released —
failed its own ceiling on a runner allocated eight months later. A gate that
answers differently on identical code is measuring the runner.

What a release actually has to promise is that it did not get materially
slower than the release before it. That is a ratio, both halves measured in
the same job on the same machine, and machine speed cancels out of it.

Absolute figures are still recorded and printed, with the CPU that produced
them, because "how fast is it" is a real question — it is simply not the
question a release can be gated on.

Runnable on a bare interpreter: no package import, no installed dependency.
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path

# Per-release budget. Derived from measured job-to-job variation of the ratio
# on the reference runner class, not chosen to accommodate a result: see
# ADR-012, which records the observations and the derivation. A release that
# needs more than this is not refused by arithmetic — it is refused until
# somebody explains it, which is the point.
RELEASE_BUDGET = 0.15

# Cumulative budget, checked by the scheduled arm against the declared anchor.
# Deliberately less than the sum of successive release budgets: a sequence of
# regressions that each fit inside RELEASE_BUDGET must not be able to walk the
# runtime somewhere nobody agreed to go.
CUMULATIVE_BUDGET = 0.25

GATED_LABELS = {
    "realistic_1000_us_per_node_min": "Per-node overhead",
    "noop_100_overhead_ms": "100-node no-op",
    "to_dict_min_ms_realistic": "Trace materialization",
}


class GateError(ValueError):
    """The relative evidence is malformed, or the release regressed too far."""


def require(condition: bool, message: str) -> None:
    if not condition:
        raise GateError(message)


def load(path: Path) -> dict:
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise GateError(f"cannot read {path}: {exc}") from exc
    require(isinstance(document, dict), f"{path} must contain a JSON object")
    return document


def validate(document: dict) -> None:
    require(
        document.get("kind") == "motus-relative-characterization",
        "not a relative characterization document",
    )
    require(document.get("pairs", 0) >= 5, "at least 5 interleaved rounds are required")
    subjects = document.get("subjects")
    require(isinstance(subjects, dict), "subjects must be an object")
    for name in ("baseline", "candidate"):
        require(name in subjects, f"missing subject {name!r}")
        subject = subjects[name]
        require(isinstance(subject.get("ref"), str) and subject["ref"], f"{name}: no ref")
        require(
            isinstance(subject.get("runtime"), str) and subject["runtime"].startswith("vitruvyan-motus/"),
            f"{name}: no runtime identity",
        )
        require(len(subject.get("runs", [])) == document["pairs"], f"{name}: run count is false")
    require(
        subjects["baseline"]["runtime"] != subjects["candidate"]["runtime"],
        "baseline and candidate report the same runtime identity; "
        "one tree was measured twice",
    )
    environment = document.get("env", {})
    require(
        isinstance(environment.get("cpu_model"), str) and environment["cpu_model"],
        "the measuring CPU must be recorded, even though it is not gated",
    )
    # Every subject was measured by one process on one host, so a differing
    # interpreter between subjects would mean the harness itself varied.
    interpreters = {
        run["env"]["python"]
        for subject in subjects.values()
        for run in subject["runs"]
    }
    require(len(interpreters) == 1, f"subjects were measured on {interpreters}")


def rows(document: dict, which: str, budget: float) -> list[tuple[str, float, bool]]:
    ratios = document.get("ratios", {}).get(which)
    if not ratios:
        return []
    out = []
    for key, label in GATED_LABELS.items():
        if key not in ratios:
            continue
        ratio = float(ratios[key])
        require(math.isfinite(ratio) and ratio > 0, f"{key}: ratio must be positive and finite")
        out.append((label, ratio, ratio <= 1 + budget))
    return out


def report(document: dict, *, strict_cumulative: bool = False) -> int:
    validate(document)
    subjects = document["subjects"]
    environment = document["env"]

    print("Relative characterization")
    print(f"  measured on : {environment['cpu_model']} / Python {environment['python']}")
    print(f"  baseline    : {subjects['baseline']['ref']} ({subjects['baseline']['runtime']})")
    print(f"  candidate   : {subjects['candidate']['ref']} ({subjects['candidate']['runtime']})")
    print(f"  rounds      : {document['pairs']} interleaved per subject")
    print()

    failures: list[str] = []

    print(f"Release budget (+{RELEASE_BUDGET:.0%} over {subjects['baseline']['ref']})")
    for label, ratio, ok in rows(document, "candidate_over_baseline", RELEASE_BUDGET):
        print(f"  {'PASS' if ok else 'FAIL':4}  {label:24} {(ratio - 1) * 100:+6.1f}%")
        if not ok:
            failures.append(f"{label} {(ratio - 1) * 100:+.1f}% exceeds +{RELEASE_BUDGET:.0%}")

    cumulative = rows(document, "candidate_over_anchor", CUMULATIVE_BUDGET)
    if cumulative:
        anchor = subjects.get("anchor", {}).get("ref", "anchor")
        print()
        print(f"Cumulative budget (+{CUMULATIVE_BUDGET:.0%} over {anchor})")
        for label, ratio, ok in cumulative:
            print(f"  {'PASS' if ok else 'FAIL':4}  {label:24} {(ratio - 1) * 100:+6.1f}%")
            if not ok and strict_cumulative:
                failures.append(f"cumulative {label} {(ratio - 1) * 100:+.1f}%")
            elif not ok:
                failures.append(f"cumulative {label} {(ratio - 1) * 100:+.1f}%")

    print()
    print("Absolute figures — recorded, not gated")
    for name in ("baseline", "candidate"):
        summary = subjects[name]["summary"]
        print(
            f"  {subjects[name]['ref']:16} "
            f"per-node {summary['realistic_1000_us_per_node_min']['median_across_runs']:7.2f} us   "
            f"no-op100 {summary['noop_100_overhead_ms']['median_across_runs']:6.3f} ms"
        )

    diagnostics = document.get("diagnostics", {}).get("paired_ratio", {})
    if diagnostics:
        print()
        print("Measurement quality — how much of the above is the machine")
        for key, label in GATED_LABELS.items():
            entry = diagnostics.get(key) or {}
            spread = entry.get("spread_pct")
            if spread is not None:
                note = "  <- wider than the effect being measured" if spread > 20 else ""
                print(f"  {label:24} paired spread {spread:5.1f}%{note}")

    print()
    if failures:
        print("Relative baseline gate: FAIL")
        for failure in failures:
            print(f"  {failure}")
        return 1
    print("Relative baseline gate: PASS")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("document", type=Path)
    parser.add_argument(
        "--advisory",
        action="store_true",
        help="print the report and always exit 0 (used while a budget is being derived)",
    )
    args = parser.parse_args(argv)
    try:
        status = report(load(args.document))
    except GateError as exc:
        print(f"Relative baseline gate: FAIL\n{exc}", file=sys.stderr)
        return 1
    return 0 if args.advisory else status


if __name__ == "__main__":
    raise SystemExit(main())
