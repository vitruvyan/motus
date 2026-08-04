"""Tests for the CI gates that protect the accepted Motus contract."""

from __future__ import annotations

import copy
import hashlib
import json
import subprocess
from pathlib import Path

import pytest

from benchmarks.check_slo_baseline import (
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
        "runtime": "vitruvyan-motus/0.6.0",
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

    with pytest.raises(GateError, match="candidate misses target-plus-tolerance"):
        from tempfile import NamedTemporaryFile

        with NamedTemporaryFile("w", suffix=".json", delete=False) as handle:
            import json

            json.dump(candidate, handle)
            candidate_path = handle.name
        try:
            run_gate(DEFAULT_BASELINE, DEFAULT_GUARANTEES, Path(candidate_path))
        finally:
            Path(candidate_path).unlink()

    inclusive = SloRow("x", "inclusive", 1.0, 1.25, 0.25)
    strict = SloRow("x", "strict", 10.0, 12.5, 0.25, strict=True)
    assert inclusive.passes
    assert not strict.passes


def test_committed_motus_candidate_passes_its_characterized_profile():
    results = dict(
        run_gate(DEFAULT_BASELINE, DEFAULT_GUARANTEES, DEFAULT_CANDIDATE)
    )

    assert results["Candidate Per-node overhead"] == "PASS"
    assert results["Candidate 100-node no-op"] == "PASS"
    assert results["Candidate Trace serialization"] == "PASS"
    assert results["Candidate Superlinear accumulation"] == "PASS"
    assert results["Candidate trace completeness"] == "PASS"


def test_candidate_gate_recomputes_and_rejects_a_real_timing_regression(tmp_path):
    candidate = copy.deepcopy(load_document(DEFAULT_CANDIDATE))
    samples = []
    for run in candidate["runs"]:
        measurement = run["3_runner_realistic"]["1000"]
        measurement["us_per_node_min"] *= 2
        samples.append(measurement["us_per_node_min"])
    candidate["summary"]["realistic_1000_us_per_node_min"] = recomputed_stat(
        samples
    )
    slowed = tmp_path / "slowed-candidate.json"
    slowed.write_text(json.dumps(candidate), encoding="utf-8")

    with pytest.raises(GateError, match="candidate misses target-plus-tolerance"):
        run_gate(DEFAULT_BASELINE, DEFAULT_GUARANTEES, slowed)


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
