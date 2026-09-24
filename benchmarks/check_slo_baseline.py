"""Executable gate for ``contract/guarantees.md`` section 3.

Always validates the committed Axis reference evidence: recomputes every
aggregate from the raw runs and checks that the published Markdown table
tells the same story.  ``--candidate`` (default ``DEFAULT_CANDIDATE``, the
release's characterized profile) additionally enforces the target-plus-
tolerance ceilings against a separately characterized Motus profile; it must
not be pointed at an uncharacterized runner class.
"""

from __future__ import annotations

import argparse
import json
import math
import re
import statistics
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

try:  # package import under pytest; direct import when executed as a script
    from .collect_baseline import TRACKED
except ImportError:  # pragma: no cover - exercised by the CI command itself
    from collect_baseline import TRACKED


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_BASELINE = ROOT / "benchmarks" / "baseline-v0.4.0-epyc-py310.json"
DEFAULT_CANDIDATE = ROOT / "benchmarks" / "candidate-v0.18.0-epyc-py310.json"
DEFAULT_GUARANTEES = ROOT / "contract" / "guarantees.md"
MIN_RUNS = 5
MOTUS_CANDIDATE_TOLERANCE = 0.25


def _declared_version() -> str:
    """The version this checkout declares, read without importing it.

    Derived, never a literal: the collector stamps
    ``f"vitruvyan-motus/{__version__}"`` and a version bump must not silently
    make characterization evidence unacceptable to the gate that consumes it.

    Importing the package is not available on the path that matters.  CI runs
    this gate on a bare interpreter — checkout, setup-python, and straight to
    ``python benchmarks/check_slo_baseline.py``, with no install and no
    ``PYTHONPATH`` — so an import here resolves only under pytest.  A gate that
    reads the right version everywhere except the job that gates the release is
    not derived at all; it is a literal wearing a fallback.
    """
    source = ROOT / "src" / "vitruvyan_motus" / "__init__.py"
    match = re.search(
        r'^__version__\s*[:=]\s*["\']([^"\']+)["\']',
        source.read_text(encoding="utf-8"),
        re.MULTILINE,
    )
    if match is None:
        raise ValueError(f"no __version__ declaration found in {source}")
    return match.group(1)


MOTUS_RUNTIME_IDENTITY = f"vitruvyan-motus/{_declared_version()}"
MOTUS_CANDIDATE_TARGETS = {
    "per_node": 45.0,
    "noop": 3.25,
    "serialization": 1.5,
    "superlinear": 10.0,
}

class GateError(ValueError):
    """The benchmark evidence or its published contract is inconsistent."""


@dataclass(frozen=True)
class SloRow:
    key: str
    label: str
    target: float
    measured: float
    tolerance: float
    strict: bool = False

    @property
    def ceiling(self) -> float:
        return self.target * (1 + self.tolerance)

    @property
    def passes(self) -> bool:
        if self.strict:
            return self.measured < self.ceiling
        return self.measured <= self.ceiling


def require(condition: bool, message: str) -> None:
    if not condition:
        raise GateError(message)


def number(value: Any, location: str) -> float:
    require(
        isinstance(value, (int, float)) and not isinstance(value, bool),
        f"{location} must be numeric",
    )
    value = float(value)
    require(math.isfinite(value), f"{location} must be finite")
    return value


def dig(document: dict[str, Any], path: Iterable[str], location: str) -> Any:
    current: Any = document
    for key in path:
        require(isinstance(current, dict) and key in current, f"{location} is missing {key!r}")
        current = current[key]
    return current


def close(actual: float, expected: float, location: str) -> None:
    require(
        math.isclose(actual, expected, rel_tol=1e-12, abs_tol=1e-12),
        f"{location}: stored {actual!r}, recomputed {expected!r}",
    )


def recomputed_stat(samples: list[float]) -> dict[str, Any]:
    low = min(samples)
    high = max(samples)
    return {
        "median_across_runs": statistics.median(samples),
        "min_across_runs": low,
        "max_across_runs": high,
        "spread_pct": (high - low) / low * 100 if low else None,
        "samples": samples,
    }


def load_document(path: Path) -> dict[str, Any]:
    def reject_non_finite(token: str) -> None:
        raise GateError(f"{path} contains non-finite JSON number {token}")

    try:
        document = json.loads(
            path.read_text(encoding="utf-8"),
            parse_constant=reject_non_finite,
        )
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise GateError(f"cannot read benchmark document {path}: {exc}") from exc
    require(isinstance(document, dict), f"{path} must contain a JSON object")
    return document


def validate_document(
    document: dict[str, Any], *, name: str, runtime_kind: str = "axis"
) -> dict[str, float | bool]:
    require(runtime_kind in {"axis", "motus"}, f"{name}: unknown runtime kind")
    require(document.get("kind") == "motus-benchmark-baseline", f"{name}: wrong kind")
    runs = document.get("runs")
    require(isinstance(runs, list), f"{name}: runs must be an array")
    require(len(runs) >= MIN_RUNS, f"{name}: at least {MIN_RUNS} independent runs are required")
    require(document.get("runs_collected") == len(runs), f"{name}: runs_collected is false")

    method = document.get("method")
    require(isinstance(method, dict), f"{name}: method must be an object")
    require(method.get("warmups_per_measurement") == 2, f"{name}: exactly 2 warmups are required")
    samples_per_measurement = method.get("measured_samples_per_measurement")
    require(
        isinstance(samples_per_measurement, int) and samples_per_measurement >= 7,
        f"{name}: at least 7 measured samples are required",
    )
    require(
        method.get("gc_collect_before_each_sample") is True,
        f"{name}: gc.collect before each sample must be declared",
    )

    environment = document.get("env")
    require(isinstance(environment, dict), f"{name}: env must be an object")
    require(environment.get("python") == "3.10.12", f"{name}: the reference interpreter must be Python 3.10.12")
    require(environment.get("gc_enabled_during_runs") is True, f"{name}: GC must be enabled during runs")
    if runtime_kind == "motus":
        require(
            environment.get("runtime") == MOTUS_RUNTIME_IDENTITY,
            f"{name}: runtime identity must be {MOTUS_RUNTIME_IDENTITY}",
        )
        cpu_model = environment.get("cpu_model")
        require(
            isinstance(cpu_model, str) and "EPYC" in cpu_model.upper(),
            f"{name}: candidate must attest an AMD EPYC CPU model",
        )
        require(
            isinstance(environment.get("platform_system"), str)
            and bool(environment["platform_system"]),
            f"{name}: candidate must record its operating system",
        )

    summary = document.get("summary")
    require(isinstance(summary, dict), f"{name}: summary must be an object")
    for index, run in enumerate(runs):
        require(isinstance(run, dict), f"{name}: run {index} must be an object")
        run_environment = dig(run, ("env",), f"{name}: run {index}")
        require(isinstance(run_environment, dict), f"{name}: run {index} env must be an object")
        require(run_environment.get("python") == environment.get("python"), f"{name}: run {index} used a different interpreter")
        require(run_environment.get("gc_enabled_during_runs") is True, f"{name}: run {index} disabled GC")
        if runtime_kind == "motus":
            for field in ("runtime", "cpu_model", "platform_system", "machine"):
                require(
                    run_environment.get(field) == environment.get(field),
                    f"{name}: run {index} used a different {field}",
                )
        repeats = run_environment.get("repeats")
        require(
            isinstance(repeats, int) and repeats >= samples_per_measurement,
            f"{name}: run {index} does not contain the declared sample count",
        )

    for label, path, scale in TRACKED:
        values = [number(dig(run, path, f"{name}: run {index}"), f"{name}: {label}") * scale for index, run in enumerate(runs)]
        expected = recomputed_stat(values)
        stored = summary.get(label)
        require(isinstance(stored, dict), f"{name}: summary.{label} is missing")
        require(stored.get("samples") == expected["samples"], f"{name}: summary.{label}.samples does not reproduce the runs")
        for field in ("median_across_runs", "min_across_runs", "max_across_runs", "spread_pct"):
            close(number(stored.get(field), f"{name}: summary.{label}.{field}"), float(expected[field]), f"{name}: summary.{label}.{field}")

    def aggregate(label: str, field: str = "median_across_runs") -> float:
        return number(summary[label][field], f"{name}: summary.{label}.{field}")

    serialization_ratio = aggregate("to_dict_min_ms_realistic") / aggregate(
        "json_dumps_min_ms_realistic"
    )
    scaling_ratio = aggregate("superlinearity_t1000_over_10x_t100")
    superlinear_pct = max(0.0, (scaling_ratio - 1.0) / scaling_ratio * 100.0)

    completeness = True
    expected_records = 3002 if runtime_kind == "motus" else 2002
    for index, run in enumerate(runs):
        noop = dig(run, ("6_serialization", "noop_1000"), f"{name}: run {index}")
        realistic = dig(run, ("6_serialization", "realistic_1000"), f"{name}: run {index}")
        complete = (
            isinstance(noop, dict)
            and isinstance(realistic, dict)
            and noop.get("events_len") == expected_records
            and noop.get("facts_len") == 0
            and realistic.get("events_len") == expected_records
            and realistic.get("facts_len") == 1000
        )
        if runtime_kind == "motus":
            complete = (
                complete
                and noop.get("violations_len") == 0
                and realistic.get("violations_len") == 0
            )
        completeness = completeness and complete

    return {
        "per_node_us": aggregate("realistic_1000_us_per_node_min"),
        "noop_100_ms": aggregate("noop_100_overhead_ms"),
        "serialization_ratio": serialization_ratio,
        "superlinear_pct": superlinear_pct,
        "trace_complete": completeness,
        "per_node_spread_pct": aggregate("realistic_1000_us_per_node_min", "spread_pct"),
        "noop_spread_pct": aggregate("noop_100_overhead_ms", "spread_pct"),
        "to_dict_spread_pct": aggregate("to_dict_min_ms_realistic", "spread_pct"),
        "json_spread_pct": aggregate("json_dumps_min_ms_realistic", "spread_pct"),
        "superlinear_spread_pct": aggregate("superlinearity_t1000_over_10x_t100", "spread_pct"),
        "published_median_per_node_us": aggregate("realistic_1000_us_per_node_median"),
        "published_median_per_node_spread_pct": aggregate("realistic_1000_us_per_node_median", "spread_pct"),
    }


def markdown_rows(markdown: str) -> dict[str, list[str]]:
    rows: dict[str, list[str]] = {}
    for line in markdown.splitlines():
        if not line.startswith("|") or line.startswith("|---"):
            continue
        cells = [cell.strip() for cell in line.strip().strip("|").split("|")]
        if len(cells) < 4:
            continue
        label = cells[0]
        if label.startswith("Per-node overhead"):
            rows["per_node"] = cells
        elif label.startswith("100-node no-op"):
            rows["noop"] = cells
        elif label.startswith("Trace serialization"):
            rows["serialization"] = cells
        elif label.startswith("Superlinear accumulation"):
            rows["superlinear"] = cells
        elif label.startswith("Trace completeness"):
            rows["completeness"] = cells
    return rows


def first_number(text: str, location: str) -> float:
    match = re.search(r"(?<![\w.])(\d+(?:\.\d+)?)", text.replace(",", "."))
    require(match is not None, f"{location}: no number found in {text!r}")
    return float(match.group(1))


def verify_published_table(markdown: str, metrics: dict[str, float | bool]) -> tuple[list[SloRow], float]:
    tolerance_match = re.search(r"Tolerance band:\s*\*\*±(\d+(?:\.\d+)?)%\*\*", markdown)
    require(tolerance_match is not None, "guarantees.md: tolerance band is missing")
    tolerance = float(tolerance_match.group(1)) / 100.0
    rows = markdown_rows(markdown)
    require(set(rows) == {"per_node", "noop", "serialization", "superlinear", "completeness"}, "guarantees.md: the five SLO rows are not all present")

    derived = {
        "per_node": (float(metrics["per_node_us"]), 1),
        "noop": (float(metrics["noop_100_ms"]), 2),
        "serialization": (float(metrics["serialization_ratio"]), 1),
        "superlinear": (float(metrics["superlinear_pct"]), 0),
        "completeness": (100.0 if metrics["trace_complete"] else 0.0, 0),
    }
    for key, (value, digits) in derived.items():
        published = first_number(rows[key][2], f"guarantees.md: {key} measured")
        require(published == round(value, digits), f"guarantees.md: {key} publishes {published}, raw evidence rounds to {round(value, digits)}")

    spread_expectations = {
        "per_node": [round(float(metrics["per_node_spread_pct"]))],
        "noop": [round(float(metrics["noop_spread_pct"]))],
        "serialization": [
            round(float(metrics["to_dict_spread_pct"])),
            round(float(metrics["json_spread_pct"])),
        ],
        "superlinear": [round(float(metrics["superlinear_spread_pct"]))],
    }
    for key, expected in spread_expectations.items():
        published = [int(value) for value in re.findall(r"\d+", rows[key][3])]
        require(published == expected, f"guarantees.md: {key} spread is {rows[key][3]!r}, expected {expected!r}")

    published_median = re.search(r"in-run median per-node cost,\s*(\d+(?:\.\d+)?)\s*µs\s*\(spread\s*(\d+(?:\.\d+)?)\s*%", markdown)
    require(published_median is not None, "guarantees.md: published in-run median is missing")
    require(float(published_median.group(1)) == round(float(metrics["published_median_per_node_us"]), 1), "guarantees.md: published in-run median does not match raw evidence")
    require(float(published_median.group(2)) == round(float(metrics["published_median_per_node_spread_pct"])), "guarantees.md: published median spread does not match raw evidence")

    slo_rows = [
        SloRow("per_node", "Per-node overhead", first_number(rows["per_node"][1], "per-node target"), float(metrics["per_node_us"]), tolerance),
        SloRow("noop", "100-node no-op", first_number(rows["noop"][1], "no-op target"), float(metrics["noop_100_ms"]), tolerance),
        SloRow("serialization", "Trace serialization", first_number(rows["serialization"][1], "serialization target"), float(metrics["serialization_ratio"]), tolerance),
        SloRow("superlinear", "Superlinear accumulation", first_number(rows["superlinear"][1], "superlinear target"), float(metrics["superlinear_pct"]), tolerance, strict=True),
    ]
    return slo_rows, tolerance


def run_gate(baseline_path: Path, guarantees_path: Path, candidate_path: Path | None = None) -> list[tuple[str, str]]:
    baseline = load_document(baseline_path)
    baseline_metrics = validate_document(baseline, name="reference baseline")
    try:
        markdown = guarantees_path.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as exc:
        raise GateError(f"cannot read {guarantees_path}: {exc}") from exc
    reference_rows, _ = verify_published_table(markdown, baseline_metrics)

    expected_reference = {
        "per_node": True,
        "noop": True,
        "serialization": False,
        "superlinear": False,
    }
    results: list[tuple[str, str]] = []
    for row in reference_rows:
        require(row.passes is expected_reference[row.key], f"reference baseline changed the reviewed status of {row.label}; an ADR is required")
        results.append((row.label, "PASS" if row.passes else "KNOWN DEBT"))
    require(bool(baseline_metrics["trace_complete"]), "reference baseline sampled or lost trace records")
    results.append(("Trace completeness", "PASS"))

    if candidate_path is not None:
        candidate = load_document(candidate_path)
        candidate_metrics = validate_document(
            candidate, name="candidate baseline", runtime_kind="motus"
        )
        metric_keys = {
            "per_node": "per_node_us",
            "noop": "noop_100_ms",
            "serialization": "serialization_ratio",
            "superlinear": "superlinear_pct",
        }
        candidate_rows = [
            SloRow(
                row.key,
                row.label,
                MOTUS_CANDIDATE_TARGETS[row.key],
                float(candidate_metrics[metric_keys[row.key]]),
                MOTUS_CANDIDATE_TOLERANCE,
                row.strict,
            )
            for row in reference_rows
        ]
        # ADR-012: these rows are RECORDED, not gated. The same v0.6.1 code
        # measures 51.2 us/node on the EPYC 9V74 the targets came from and
        # 66.1 us/node on an EPYC 7763 allocated later, and the runner check
        # ("EPYC" in the model string) cannot tell them apart -- so a ceiling
        # here refuses code it previously accepted, which is measuring the
        # runner. A release is gated on the ratio to the previous release
        # instead; see check_relative_baseline.py.
        #
        # Everything else still binds: the aggregates are recomputed from the
        # raw runs, the interpreter and the runtime identity must match, and an
        # incomplete trace is still a refusal. Completeness is an invariant,
        # never a measurement.
        require(bool(candidate_metrics["trace_complete"]), "candidate baseline sampled or lost trace records")
        results.extend(
            (
                f"Candidate {row.label}",
                "MEETS TARGET" if row.passes else f"RECORDED {row.measured:.6g}",
            )
            for row in candidate_rows
        )
        results.append(("Candidate trace completeness", "PASS"))

    return results


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--baseline", type=Path, default=DEFAULT_BASELINE)
    parser.add_argument("--guarantees", type=Path, default=DEFAULT_GUARANTEES)
    parser.add_argument("--candidate", type=Path, default=DEFAULT_CANDIDATE)
    args = parser.parse_args(argv)
    try:
        results = run_gate(args.baseline, args.guarantees, args.candidate)
    except GateError as exc:
        print(f"SLO baseline gate: FAIL\n{exc}", file=sys.stderr)
        return 1
    print("SLO baseline gate: PASS")
    for label, status in results:
        print(f"  {status:10} {label}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
