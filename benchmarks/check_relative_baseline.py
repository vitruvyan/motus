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
import statistics
import sys
from pathlib import Path

# Per-release budget. Five times the +/-2 point job-to-job variation measured
# on the reference runner class (ADR-012 records the three observations and the
# derivation). Not chosen by trying it against a release: 0.7.0 does not fit it
# on one metric, and is recorded as a named exception rather than accommodated
# by a wider number. A release that needs more than this is not refused by
# arithmetic -- it is refused until somebody explains it, which is the point.
RELEASE_BUDGET = 0.10

# Cumulative budget, checked by the scheduled arm against the declared anchor.
# Deliberately less than the sum of successive release budgets: a sequence of
# regressions that each fit inside RELEASE_BUDGET must not be able to walk the
# runtime somewhere nobody agreed to go.
CUMULATIVE_BUDGET = 0.20

GATED_LABELS = {
    "realistic_1000_us_per_node_min": "Per-node overhead",
    "noop_100_overhead_ms": "100-node no-op",
    "to_dict_min_ms_realistic": "Trace materialization",
}

# Sampling and the decision rule, in one place because a gate whose statistic
# is implicit can be argued with after the fact (ADR-012 section 2):
#
#   within a job   each subject's figure is the MEDIAN across >= MIN_PAIRS
#                  interleaved rounds; each round is itself min-of-samples
#                  under the ADR-006 method (2 warmups, >= 7 samples, gc.collect)
#   job ratio      candidate median / baseline median, per metric
#   release figure the CANONICAL value is the MEDIAN of the job ratios across
#                  >= MIN_JOBS independent dispatches
#   decision       the canonical value is compared to the budget. Individual
#                  jobs are observations, never verdicts: the runner varies 55%
#                  between them and a single job is a coin toss.
MIN_PAIRS = 5
MIN_JOBS = 3

# Machine-enforced, scoped, and expiring by construction. Keyed on the baseline
# ref, the candidate's own declared version and the metric, so an exception
# granted to one release cannot silently cover another: 0.8.0 does not match
# this key and is measured under the full budget.
#
#   (baseline_ref, candidate_version, metric) -> (ceiling, authority, why)
DECLARED_EXCEPTIONS = {
    ("v0.6.1", "0.7.0", "noop_100_overhead_ms"): (
        0.13,
        "ADR-012",
        "the executor's node-invocation inversion adds a generator round trip "
        "and one allocation per node; on nodes that do no work that cost is "
        "the entire measurement. Deferred with a hypothesis to falsify, not "
        "accommodated by a wider budget.",
    ),
    ("v0.7.0", "0.8.0", "noop_100_overhead_ms"): (
        1.05,
        "ADR-017",
        "the integrity chain. Every record is canonicalised and hashed, and on "
        "nodes that do no work that cost is most of the measurement. Declared "
        "rather than accommodated: this is the price of a deliberate "
        "capability, not an accidental regression, and the number is the best "
        "achieved after optimisation, not the first one measured. The "
        "hypothesis to falsify is that no cheaper canonical form exists — four "
        "were tried and recorded in ADR-017, including one that measured "
        "SLOWER.",
    ),
    ("v0.7.0", "0.8.0", "realistic_1000_us_per_node_min"): (
        0.78,
        "ADR-017",
        "the integrity chain, on the realistic graph: +65 microseconds per "
        "node. Invisible against a node that calls a model, and a doubling of "
        "the engine on pure computation. Stated here because the gate measures "
        "the second and deployments live in the first, and pretending "
        "otherwise is how a budget stops meaning anything.",
    ),
    ("v0.7.0", "0.8.0", "to_dict_min_ms_realistic"): (
        0.35,
        "ADR-017",
        "the integrity chain, at trace materialisation. The smallest of the "
        "three because this metric already serialises.",
    ),
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


def canonical(documents: list[dict], which: str) -> dict[str, float]:
    """The release's figure for each metric: the median across jobs.

    A single job is an observation. The absolute measurement moves 55 % between
    identical jobs on this runner class, so a verdict taken from one of them is
    a verdict about which host GitHub allocated.
    """
    out: dict[str, float] = {}
    for key in GATED_LABELS:
        ratios = [
            float(document.get("ratios", {}).get(which, {})[key])
            for document in documents
            if key in document.get("ratios", {}).get(which, {})
        ]
        if not ratios:
            continue
        for ratio in ratios:
            require(
                math.isfinite(ratio) and ratio > 0,
                f"{key}: every job ratio must be positive and finite",
            )
        out[key] = statistics.median(ratios)
    return out


def _exception_for(documents: list[dict], key: str):
    baseline = documents[0]["subjects"]["baseline"]["ref"]
    version = documents[0]["subjects"]["candidate"]["runtime"].split("/", 1)[-1]
    return DECLARED_EXCEPTIONS.get((baseline, version, key))


def report(documents: list[dict]) -> int:
    for document in documents:
        validate(document)
    require(
        len({d["subjects"]["baseline"]["ref"] for d in documents}) == 1
        and len({d["subjects"]["candidate"]["runtime"] for d in documents}) == 1,
        "every job must compare the same pair of subjects",
    )

    first = documents[0]["subjects"]
    print("Relative characterization")
    print(f"  baseline    : {first['baseline']['ref']} ({first['baseline']['runtime']})")
    print(f"  candidate   : {first['candidate']['ref']} ({first['candidate']['runtime']})")
    print(f"  jobs        : {len(documents)} independent dispatches")
    for index, document in enumerate(documents, 1):
        environment = document["env"]
        print(
            f"    job {index}: {environment['cpu_model']} / "
            f"Python {environment['python']} / {document['pairs']} rounds per subject"
        )
    print()

    if len(documents) < MIN_JOBS:
        print(
            f"Relative baseline gate: FAIL\n  {len(documents)} job(s); "
            f"at least {MIN_JOBS} independent dispatches are required before a "
            "release figure is canonical",
            file=sys.stderr,
        )
        return 1

    failures: list[str] = []

    def section(which: str, budget: float, title: str) -> None:
        values = canonical(documents, which)
        if not values:
            return
        print(title)
        for key, label in GATED_LABELS.items():
            if key not in values:
                continue
            ratio = values[key]
            per_job = [
                document["ratios"][which][key] for document in documents
                if key in document.get("ratios", {}).get(which, {})
            ]
            ceiling, note = budget, ""
            granted = _exception_for(documents, key) if which == "candidate_over_baseline" else None
            if granted and granted[0] > budget:
                ceiling = granted[0]
                note = f"  <- {granted[1]} exception, ceiling +{ceiling:.0%}"
            ok = ratio <= 1 + ceiling
            spread = f"[{min(per_job) - 1:+.1%} .. {max(per_job) - 1:+.1%}]"
            print(
                f"  {'PASS' if ok else 'FAIL':4}  {label:24} "
                f"{(ratio - 1) * 100:+6.1f}%  across jobs {spread}{note}"
            )
            if not ok:
                failures.append(
                    f"{label} {(ratio - 1) * 100:+.1f}% exceeds +{ceiling:.0%}"
                )
        print()

    section(
        "candidate_over_baseline",
        RELEASE_BUDGET,
        f"Release budget (+{RELEASE_BUDGET:.0%} over {first['baseline']['ref']}) "
        "— canonical value is the median across jobs",
    )
    anchor = first.get("anchor", {}).get("ref")
    if anchor:
        section(
            "candidate_over_anchor",
            CUMULATIVE_BUDGET,
            f"Cumulative budget (+{CUMULATIVE_BUDGET:.0%} over anchor {anchor})",
        )

    print("Absolute figures — recorded with their host, never gated")
    for index, document in enumerate(documents, 1):
        subjects = document["subjects"]
        print(
            f"  job {index} on {document['env']['cpu_model'][:34]:36} "
            f"{subjects['baseline']['ref']} "
            f"{subjects['baseline']['summary']['realistic_1000_us_per_node_min']['median_across_runs']:6.2f} us"
            f"  ->  candidate "
            f"{subjects['candidate']['summary']['realistic_1000_us_per_node_min']['median_across_runs']:6.2f} us"
        )

    print()
    print("Measurement quality — how much of the above is the machine")
    for key, label in GATED_LABELS.items():
        spreads = [
            (document.get("diagnostics", {}).get("paired_ratio", {}).get(key) or {}).get("spread_pct")
            for document in documents
        ]
        spreads = [value for value in spreads if value is not None]
        if not spreads:
            continue
        worst = max(spreads)
        flag = "  <- wider than the effect being measured" if worst > 20 else ""
        print(f"  {label:24} worst paired spread {worst:5.1f}%{flag}")

    granted_any = [
        (key, _exception_for(documents, key)) for key in GATED_LABELS
        if _exception_for(documents, key)
    ]
    if granted_any:
        print()
        print("Declared exceptions applied to this release")
        for key, (ceiling, authority, why) in granted_any:
            print(f"  {GATED_LABELS[key]} — ceiling +{ceiling:.0%}, {authority}")
            print(f"    {why}")

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
    parser.add_argument(
        "documents", type=Path, nargs="+",
        help=f"one relative-characterization document per job; {MIN_JOBS}+ required",
    )
    parser.add_argument(
        "--advisory", action="store_true",
        help="print the report and always exit 0 (a single job cannot be a verdict)",
    )
    args = parser.parse_args(argv)
    try:
        status = report([load(path) for path in args.documents])
    except GateError as exc:
        print(f"Relative baseline gate: FAIL\n{exc}", file=sys.stderr)
        return 1
    return 0 if args.advisory else status


if __name__ == "__main__":
    raise SystemExit(main())
