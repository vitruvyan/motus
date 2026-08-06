"""Tests for the CI gates that protect the accepted Motus contract."""

from __future__ import annotations

import copy
import hashlib
import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from benchmarks.check_slo_baseline import (
    MOTUS_RUNTIME_IDENTITY,
    DEFAULT_BASELINE,
    DEFAULT_CANDIDATE,
    DEFAULT_GUARANTEES,
    GateError,
    MOTUS_CANDIDATE_TARGETS,
    MOTUS_CANDIDATE_TOLERANCE,
    SloRow,
    load_document,
    recomputed_stat,
    run_gate,
    validate_document,
)
from tools.check_frozen_paths import (
    APPROVED_KERNEL_SHA256,
    KERNEL_PATH,
    changed_paths,
    is_frozen_path,
)


REPO_ROOT = Path(__file__).resolve().parent.parent


def test_the_slo_gate_reads_its_runtime_identity_from_the_checkout(tmp_path):
    """ADR-006 couples the version string to committed performance evidence:
    ``bench_motus.py`` stamps ``f"vitruvyan-motus/{__version__}"`` and this gate
    refuses a candidate whose runtime identity does not match.  So the gate has
    to see the version the *checkout* declares.

    The trap is that CI runs this gate on a bare interpreter — checkout,
    setup-python, then straight to ``python benchmarks/check_slo_baseline.py``
    with no install and no ``PYTHONPATH``.  An ``import vitruvyan_motus`` there
    fails, and a fallback literal would freeze the identity at whatever version
    was current when it was written: the release that bumps the version would
    have its own fresh characterization rejected by its own gate.

    This copies the gate next to a checkout declaring an impossible version and
    runs it the way CI does.  If the identity is derived, it follows.
    """
    bench = tmp_path / "benchmarks"
    bench.mkdir()
    for name in ("check_slo_baseline.py", "collect_baseline.py"):
        shutil.copy2(REPO_ROOT / "benchmarks" / name, bench / name)
    package = tmp_path / "src" / "vitruvyan_motus"
    package.mkdir(parents=True)
    (package / "__init__.py").write_text('__version__ = "9.9.9"\n', encoding="utf-8")

    probe = subprocess.run(
        [sys.executable, "-c", "import check_slo_baseline as g; print(g.MOTUS_RUNTIME_IDENTITY)"],
        cwd=bench,
        capture_output=True,
        text=True,
        timeout=60,
    )

    assert probe.returncode == 0, probe.stderr
    assert probe.stdout.strip() == "vitruvyan-motus/9.9.9", (
        "the gate must read the version from the checkout it is run against, "
        f"not from an installed package or a literal; got {probe.stdout.strip()!r}"
    )


def test_the_slo_gate_carries_no_frozen_version_literal():
    """The sibling above proves the derivation works today.  This one keeps a
    fallback from being reintroduced: any ``vitruvyan-motus/<version>`` literal
    in the gate is the defect that test exists to catch, spelled out again."""
    source = (REPO_ROOT / "benchmarks" / "check_slo_baseline.py").read_text(encoding="utf-8")

    assert "vitruvyan-motus/0." not in source, (
        "the SLO gate must not hardcode a runtime identity; derive it from "
        "src/vitruvyan_motus/__init__.py so a version bump cannot invalidate "
        "the characterization that bump requires"
    )


def test_reference_slo_evidence_reproduces_the_published_contract():
    results = dict(run_gate(DEFAULT_BASELINE, DEFAULT_GUARANTEES))

    assert results == {
        "Per-node overhead": "PASS",
        "100-node no-op": "PASS",
        "Trace serialization": "KNOWN DEBT",
        "Superlinear accumulation": "KNOWN DEBT",
        "Trace completeness": "PASS",
    }


def test_a_stored_aggregate_cannot_disagree_with_its_raw_runs():
    document = load_document(DEFAULT_BASELINE)
    document = copy.deepcopy(document)
    document["summary"]["noop_100_overhead_ms"]["median_across_runs"] += 1

    with pytest.raises(GateError, match="recomputed"):
        validate_document(document, name="tampered")


def test_candidate_mode_enforces_target_plus_tolerance():
    candidate = load_document(DEFAULT_BASELINE)
    candidate = copy.deepcopy(candidate)
    attestation = {
        "runtime": MOTUS_RUNTIME_IDENTITY,
        "cpu_model": "AMD EPYC test fixture",
        "platform_system": "Linux",
        "machine": "x86_64",
    }
    candidate["env"].update(attestation)
    for run in candidate["runs"]:
        run["env"].update(attestation)
        for workload in ("noop_1000", "realistic_1000"):
            run["6_serialization"][workload]["events_len"] = 3002
            run["6_serialization"][workload]["violations_len"] = 0

    # ADR-012: the Motus candidate rows are RECORDED, not gated -- a ceiling
    # derived from one host refuses code it previously accepted on another.
    # A release is gated on the ratio to the previous release instead. The
    # target arithmetic itself still has to be right, because the recorded
    # report says whether a row met its target.
    inclusive = SloRow("x", "inclusive", 1.0, 1.25, 0.25)
    strict = SloRow("x", "strict", 10.0, 12.5, 0.25, strict=True)
    assert inclusive.passes
    assert not strict.passes


def test_committed_motus_candidate_passes_its_characterized_profile():
    results = dict(
        run_gate(DEFAULT_BASELINE, DEFAULT_GUARANTEES, DEFAULT_CANDIDATE)
    )

    # Recorded, with the value, rather than a verdict (ADR-012). What is still
    # a verdict is completeness: no sampling, ever -- an invariant, never a
    # measurement, so it cannot become a recorded number.
    for label in (
        "Candidate Per-node overhead",
        "Candidate 100-node no-op",
        "Candidate Trace serialization",
        "Candidate Superlinear accumulation",
    ):
        assert results[label].startswith(("RECORDED", "MEETS TARGET")), results[label]
    assert results["Candidate trace completeness"] == "PASS"


def test_candidate_evidence_cannot_disagree_with_its_own_raw_runs(tmp_path):
    """ADR-012 stopped gating the candidate on a ceiling, because a ceiling
    derived from one host refuses code it previously accepted on another. It
    did NOT stop the gate recomputing: a summary that disagrees with the runs
    beneath it is a document nobody should trust, whatever it says.

    The regression question moved to `check_relative_baseline.py`, where it is
    asked as a ratio measured on one machine in one job.
    """
    candidate = copy.deepcopy(load_document(DEFAULT_CANDIDATE))
    # A summary that flatters the runs beneath it.
    candidate["summary"]["realistic_1000_us_per_node_min"]["median_across_runs"] /= 2
    doctored = tmp_path / "doctored-candidate.json"
    doctored.write_text(json.dumps(candidate), encoding="utf-8")

    with pytest.raises(GateError, match="recomputed"):
        run_gate(DEFAULT_BASELINE, DEFAULT_GUARANTEES, doctored)


def test_a_slower_candidate_is_recorded_rather_than_refused(tmp_path):
    """The behaviour change ADR-012 makes, pinned so it is deliberate."""
    candidate = copy.deepcopy(load_document(DEFAULT_CANDIDATE))
    samples = []
    for run in candidate["runs"]:
        measurement = run["3_runner_realistic"]["1000"]
        measurement["us_per_node_min"] *= 2
        samples.append(measurement["us_per_node_min"])
    candidate["summary"]["realistic_1000_us_per_node_min"] = recomputed_stat(samples)
    slowed = tmp_path / "slowed-candidate.json"
    slowed.write_text(json.dumps(candidate), encoding="utf-8")

    results = dict(run_gate(DEFAULT_BASELINE, DEFAULT_GUARANTEES, slowed))

    assert results["Candidate Per-node overhead"].startswith("RECORDED")
    assert results["Candidate trace completeness"] == "PASS"


def test_candidate_gate_rejects_a_different_cpu_profile():
    candidate = copy.deepcopy(load_document(DEFAULT_CANDIDATE))
    candidate["env"]["cpu_model"] = "not the characterized runner"

    with pytest.raises(GateError, match="AMD EPYC"):
        validate_document(candidate, name="wrong runner", runtime_kind="motus")


@pytest.mark.parametrize("key", sorted(MOTUS_CANDIDATE_TARGETS))
def test_motus_candidate_profile_rejects_a_regression_beyond_its_ceiling(key):
    target = MOTUS_CANDIDATE_TARGETS[key]
    strict = key == "superlinear"
    row = SloRow(
        key,
        key,
        target,
        target * (1 + MOTUS_CANDIDATE_TOLERANCE) + 1e-9,
        MOTUS_CANDIDATE_TOLERANCE,
        strict=strict,
    )

    assert not row.passes


def test_non_finite_json_is_not_benchmark_evidence(tmp_path):
    malformed = tmp_path / "nan.json"
    malformed.write_text('{"kind": NaN}', encoding="utf-8")

    with pytest.raises(GateError, match="non-finite"):
        load_document(malformed)


@pytest.mark.parametrize(
    ("path", "frozen"),
    [
        ("tests/contract/test_inherited_conformance.py", True),
        ("tests/compat/terraveler/golden/production-ingestion-trace.json", True),
        ("tests/contract/kernel.py", True),
        ("tests/test_ci_foundation.py", False),
        (r"tests\compat\terraveler\test_frozen_surface.py", True),
    ],
)
def test_frozen_path_policy(path, frozen):
    assert is_frozen_path(path) is frozen


def test_one_time_kernel_switch_is_pinned_to_its_approved_digest():
    blob = subprocess.run(
        ["git", "show", f"HEAD:{KERNEL_PATH.as_posix()}"],
        check=True,
        capture_output=True,
    ).stdout
    assert hashlib.sha256(blob).hexdigest() == APPROVED_KERNEL_SHA256


def test_a_rename_cannot_move_frozen_evidence_outside_the_guard(tmp_path, monkeypatch):
    frozen = tmp_path / "tests" / "contract" / "proof.py"
    frozen.parent.mkdir(parents=True)
    frozen.write_text("evidence = True\n", encoding="utf-8")
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    subprocess.run(["git", "add", "."], cwd=tmp_path, check=True)
    subprocess.run(
        [
            "git",
            "-c",
            "user.name=Motus CI",
            "-c",
            "user.email=ci@invalid.example",
            "commit",
            "-qm",
            "frozen evidence",
        ],
        cwd=tmp_path,
        check=True,
    )
    base = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=tmp_path,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()

    destination = tmp_path / "archive" / "proof.py"
    destination.parent.mkdir()
    frozen.rename(destination)
    subprocess.run(["git", "add", "-A"], cwd=tmp_path, check=True)
    subprocess.run(
        [
            "git",
            "-c",
            "user.name=Motus CI",
            "-c",
            "user.email=ci@invalid.example",
            "commit",
            "-qm",
            "move evidence",
        ],
        cwd=tmp_path,
        check=True,
    )
    head = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=tmp_path,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()

    monkeypatch.chdir(tmp_path)
    paths = changed_paths(base, head)

    assert "tests/contract/proof.py" in paths
    assert any(is_frozen_path(path) for path in paths)
