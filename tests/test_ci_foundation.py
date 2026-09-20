"""Tests for the CI gates that protect the accepted Motus contract."""

from __future__ import annotations

import copy
import hashlib
import json
import re
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
    main,
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


def test_nothing_tells_anyone_to_gate_a_frozen_candidate_file():
    """The sibling above guards the script; the same disease reappeared one
    level up, in the documents that tell somebody how to run it, where nothing
    guarded it at all.

    Two sites, found a day apart and neither reported by the other.
    ``.claude/agents/motus-verifier.md`` told the agent to run
    ``check_slo_baseline.py`` against
    ``benchmarks/candidate-v0.8.1-epyc-py310.json``, frozen at the release it
    was written for, so for six releases the agent gated evidence the runtime
    identity check was always going to refuse and reported a red that had
    nothing to do with the code under review.  ``benchmarks/README.md`` named
    ``candidate-v0.6.1-epyc-py310.json`` as "the current characterized Motus
    profile" from 0.6.1 until 0.14.0 -- nine candidate profiles later -- and
    printed the matching command underneath.

    So the property is: a file that tells anyone to run the gate against a
    named candidate must be one the release act retargets. Jenkins invokes the
    CLI without one, so the release act updates only ``DEFAULT_CANDIDATE``.

    Scoped to the invocation, not the file: prose recording this history is not
    the defect.  A mention of ``check_slo_baseline.py`` followed by a
    ``candidate-v<version>`` path is.

    **Where the class stays open.**  ``check_relative_baseline.py`` takes its
    evidence as a positional ``benchmarks/relative-<X.Y.Z>/*.json``, and
    ``README.md`` names one.  It has no ``DEFAULT_CANDIDATE`` to fall back on,
    so there is nothing for a caller to omit and this test cannot cover it by
    the same rule.  Closing it means giving that script a default too; until
    then, that line goes stale at each release like the ones above did.
    """
    exempt: set[str] = set()
    jenkinsfile = (REPO_ROOT / "Jenkinsfile").read_text(encoding="utf-8")
    slo_stage = (
        jenkinsfile.split("stage('slo-baseline')", 1)[1].split("stage(", 1)[0]
    )
    assert "benchmarks/check_slo_baseline.py" in slo_stage
    assert "--candidate" not in slo_stage
    assert "candidate-v" not in slo_stage
    # None of these is an instruction to run the gate today: this file is about
    # the rule, and `audit/` and `adr/` are dated records of commands that were
    # run in the past, where naming the candidate of the day is the point.
    excluded = {"tests/test_ci_foundation.py"}
    excluded_trees = {"audit", "adr"}
    scanned_suffixes = {".md", ".yml", ".yaml", ".py", ".txt", ".toml"}
    skipped_parents = {".git", "build", "worktrees", "node_modules", ".venv"}

    offenders = []
    seen = set()
    for path in sorted(REPO_ROOT.rglob("*")):
        if not path.is_file() or path.suffix not in scanned_suffixes:
            continue
        relative = path.relative_to(REPO_ROOT).as_posix()
        if skipped_parents.intersection(path.relative_to(REPO_ROOT).parts):
            continue
        if relative in excluded or path.relative_to(REPO_ROOT).parts[0] in excluded_trees:
            continue
        try:
            body = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue
        # Not line by line: A caller may write the invocation as a folded YAML
        # scalar, so the script and its `--candidate` land on separate lines --
        # and so could a real offender. Collapse whitespace and look at what
        # follows each mention of the script.
        for match in re.finditer(r"check_slo_baseline\.py", body):
            window = " ".join(body[match.end():match.end() + 200].split())
            # The flag, not the mention: prose that names the script and then,
            # a sentence later, names a candidate file is discussing the rule,
            # not instructing anyone. An invocation puts `--candidate` between
            # the two.
            if "--candidate" not in window or "candidate-v" not in window:
                continue
            seen.add(relative)
            if relative not in exempt:
                offenders.append(f"{relative}: check_slo_baseline.py{window[:90]}")

    assert not offenders, (
        "these pin the SLO gate to a frozen candidate file; pass no --candidate "
        "and let the CLI's DEFAULT_CANDIDATE (retargeted every release) supply "
        "it:\n  " + "\n  ".join(offenders)
    )

    dead = exempt - seen
    assert not dead, (
        f"exempt but no longer naming a candidate: {sorted(dead)} -- the "
        "exemption outlived the thing it excused, and would have hidden a real "
        "one if that file changed again"
    )

    # An empty sweep must not pass silently: the two directories the first
    # instance lived in have to have been read.
    for directory in (REPO_ROOT / ".claude" / "agents", REPO_ROOT / ".pi" / "agents"):
        assert directory.is_dir() and sorted(directory.glob("*.md")), (
            f"{directory} is missing or empty -- nothing to scan is not the "
            "same as nothing wrong"
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


def test_the_cli_defaults_to_the_complete_gate_with_no_arguments(capsys):
    """``--candidate`` carried no argparse default, so ``args.candidate`` was
    ``None`` whenever nobody passed the flag, and ``run_gate`` skips its whole
    candidate block behind ``if candidate_path is not None``.  An agent
    definition removed the frozen ``--candidate`` literal from its invocation
    on the belief that ``DEFAULT_CANDIDATE`` already drove the CLI -- it did
    not, so that fix traded a loud false red for a quiet PASS that never ran
    the candidate's runtime-identity check, its metric comparison, or its
    trace-completeness assertion.  The sibling above proves ``run_gate``
    itself is correct when called directly with ``DEFAULT_CANDIDATE``; this
    proves the *command line*, which is what an agent actually runs, reaches
    the same result with no arguments at all.
    """
    exit_code = main([])

    assert exit_code == 0
    output = capsys.readouterr().out
    for label in (
        "Candidate Per-node overhead",
        "Candidate 100-node no-op",
        "Candidate Trace serialization",
        "Candidate Superlinear accumulation",
        "Candidate trace completeness",
    ):
        assert label in output, f"CLI with no --candidate never reached the candidate block: {output!r}"


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


def test_pr_cannot_weaken_the_checker_that_judges_its_frozen_edits(tmp_path):
    """Jenkins must run the checker blob from base, never the PR replacement."""
    checker = tmp_path / "tools" / "check_frozen_paths.py"
    checker.parent.mkdir(parents=True)
    shutil.copy2(REPO_ROOT / "tools" / "check_frozen_paths.py", checker)
    frozen = tmp_path / "tests" / "contract" / "proof.py"
    frozen.parent.mkdir(parents=True)
    frozen.write_text("evidence = True\n", encoding="utf-8")
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    subprocess.run(["git", "add", "."], cwd=tmp_path, check=True)
    commit = [
        "git", "-c", "user.name=Motus CI",
        "-c", "user.email=ci@invalid.example", "commit", "-qm",
    ]
    subprocess.run([*commit, "trusted base"], cwd=tmp_path, check=True)
    base = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=tmp_path, check=True,
        capture_output=True, text=True,
    ).stdout.strip()

    frozen.write_text("evidence = False\n", encoding="utf-8")
    checker.write_text("raise SystemExit(0)\n", encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=tmp_path, check=True)
    subprocess.run([*commit, "malicious PR"], cwd=tmp_path, check=True)
    head = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=tmp_path, check=True,
        capture_output=True, text=True,
    ).stdout.strip()

    trusted = tmp_path / "trusted-checker.py"
    blob = subprocess.run(
        ["git", "show", f"{base}:tools/check_frozen_paths.py"],
        cwd=tmp_path, check=True, capture_output=True,
    ).stdout
    trusted.write_bytes(blob)
    judged = subprocess.run(
        [sys.executable, str(trusted), base, head],
        cwd=tmp_path, capture_output=True, text=True,
    )

    assert judged.returncode == 1
    assert "tests/contract/proof.py" in judged.stderr
