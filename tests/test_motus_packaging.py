"""Packaging boundary for the vitruvyan-motus distribution (milestone A).

Two claims are load-bearing and both are proven here against a REAL built
artifact, not against the pyproject.toml configuration that is supposed to
produce it:

1. the wheel contains `vitruvyan_motus` (with `py.typed`) and nothing
   under `axis/` — checked structurally (the wheel's own file list) AND
   operationally (installed alone, in a venv that is not this checkout,
   `vitruvyan_motus` imports and `axis` does not);
2. the predecessor runtime is absent from the working tree entirely
   (ADR-009), so claim 1's `axis`-is-not-importable guarantee now holds for
   the stronger reason that there is nothing on disk to import — verified
   directly against the current process, no assumption.

Building a wheel here uses the same mechanism `pip install -e ".[test]"`
already relies on in CI (build isolation fetching `setuptools>=68` per
`[build-system]`), so this adds no new dependency on the environment, only
a deterministic check of what that mechanism already requires to succeed.
"""

from __future__ import annotations

import importlib.util
import json
import shutil
import subprocess
import sys
import venv
import zipfile
from pathlib import Path

import pytest

from vitruvyan_motus import __version__ as MOTUS_VERSION
from vitruvyan_motus.mcp.sources import CITABLE

REPO_ROOT = Path(__file__).resolve().parent.parent


# What must NOT travel into the build sandbox below. Two entries are here
# because they were caught leaking, not because they looked untidy:
#
#   `build/`    — setuptools packages whatever sits in `build/lib`, so a wheel
#                 built over a previous build contains files the CURRENT
#                 pyproject.toml no longer declares.
#   `*.egg-info` — the editable install's `SOURCES.txt` lists every data file
#                 it once shipped, and `include-package-data` (on by default
#                 for pyproject metadata) honours it. The schemas kept
#                 appearing in the wheel with their `package-data` entry
#                 deleted.
#
# Both were found the same way: neutralise a line of packaging configuration,
# re-run these tests, and watch them stay green. A test that builds over the
# working tree's leftovers is not testing the configuration — it is testing
# the leftovers.
_NOT_SOURCE = {
    ".git",
    ".venv",
    ".attack",
    "build",
    "dist",
    "*.egg-info",
    "__pycache__",
    ".pytest_cache",
    ".mypy_cache",
    ".ruff_cache",
    "node_modules",
}


@pytest.fixture(scope="session")
def built_wheel(tmp_path_factory) -> Path:
    """Build the vitruvyan-motus wheel once, from a pristine copy of the tree.

    The copy is what makes this a test of the configuration rather than of
    whatever happens to be lying around: no `build/` to inherit, no editable
    install to shadow it.
    """
    out_dir = tmp_path_factory.mktemp("motus-wheel")
    source = tmp_path_factory.mktemp("motus-source") / "repo"
    shutil.copytree(
        REPO_ROOT,
        source,
        ignore=shutil.ignore_patterns(*_NOT_SOURCE),
        symlinks=True,
    )
    assert not (source / "build").exists()
    assert list(source.glob("*.egg-info")) == []

    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "pip",
            "wheel",
            str(source),
            "--no-deps",
            "--no-cache-dir",
            "-w",
            str(out_dir),
        ],
        capture_output=True,
        text=True,
        timeout=300,
    )
    assert result.returncode == 0, (
        f"building the vitruvyan-motus wheel failed\n"
        f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    )
    wheels = list(out_dir.glob("vitruvyan_motus-*.whl"))
    assert len(wheels) == 1, f"expected exactly one wheel, found {wheels}"
    return wheels[0]


def test_wheel_is_named_for_the_declared_version(built_wheel):
    assert built_wheel.name.startswith(f"vitruvyan_motus-{MOTUS_VERSION}-")


def test_wheel_contains_the_package_and_py_typed_but_not_axis(built_wheel):
    with zipfile.ZipFile(built_wheel) as archive:
        names = archive.namelist()

    assert "vitruvyan_motus/__init__.py" in names
    assert "vitruvyan_motus/py.typed" in names, (
        "py.typed must ship as package data — it is not a .py file and "
        "setuptools does not include it without an explicit declaration"
    )

    axis_entries = [name for name in names if name.startswith("axis/")]
    assert axis_entries == [], (
        f"the Motus wheel must not contain axis/, found: {axis_entries}"
    )
    # Defense in depth against the same defect under a different name.
    assert not any("axis" in name.split("/")[0] for name in names if "/" in name)
    assert any(name.endswith(".dist-info/licenses/LICENSE") for name in names)


def test_wheel_declares_jsonschema_and_nothing_else(built_wheel):
    """The distribution installs exactly one thing, and it is the one the
    shipped validator needs.

    This assertion replaced a stricter one — "zero unconditional
    Requires-Dist" — when the founder decided the validator must ship (#48).
    That is a real reduction and it is recorded here rather than quietly
    dropped: a trace nobody can check is a log, and the tool that checks it
    was staying on GitHub. The property worth keeping is narrower and still
    held: the KERNEL imports nothing outside the standard library, which
    `test_importing_motus_pulls_no_third_party_module` proves against a
    running interpreter, not against this metadata.

    A second entry appearing here means someone widened the distribution's
    footprint. That is a decision, not a detail — take it deliberately.
    """
    with zipfile.ZipFile(built_wheel) as archive:
        metadata_name = next(
            n for n in archive.namelist() if n.endswith(".dist-info/METADATA")
        )
        metadata = archive.read(metadata_name).decode("utf-8")

    unconditional = [
        line
        for line in metadata.splitlines()
        if line.startswith("Requires-Dist:") and "extra ==" not in line
    ]
    assert len(unconditional) == 1, (
        f"vitruvyan-motus declares exactly one unconditional runtime "
        f"dependency — jsonschema, for the shipped validator — found: "
        f"{unconditional}"
    )
    assert unconditional[0].startswith("Requires-Dist: jsonschema"), unconditional


def test_wheel_metadata_is_accurate(built_wheel):
    with zipfile.ZipFile(built_wheel) as archive:
        metadata_name = next(
            n for n in archive.namelist() if n.endswith(".dist-info/METADATA")
        )
        metadata = archive.read(metadata_name).decode("utf-8")

    assert "Name: vitruvyan-motus" in metadata
    assert f"Version: {MOTUS_VERSION}" in metadata
    assert "License-Expression: Apache-2.0" in metadata
    assert "License-File: LICENSE" in metadata
    assert "Vitruvyan Motus" in metadata
    assert "Vitruvyan Axis" not in metadata
    assert "Synaptic Bus" not in metadata


def test_installed_alone_motus_imports_and_axis_does_not(built_wheel, tmp_path):
    """The sharpest form of "axis is not in the wheel": install the wheel
    BY ITSELF, in a venv rooted outside this checkout, and try both
    imports there. There is no `sys.path` accident left to hide behind.
    """
    isolated_venv = tmp_path / "isolated-venv"
    venv.EnvBuilder(with_pip=True, clear=True).create(isolated_venv)
    python = isolated_venv / (
        "Scripts/python.exe" if sys.platform == "win32" else "bin/python"
    )

    install = subprocess.run(
        [
            str(python),
            "-m",
            "pip",
            "install",
            "--no-deps",
            "--no-index",
            str(built_wheel),
        ],
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert install.returncode == 0, (
        f"installing the built wheel in isolation failed\n"
        f"stdout:\n{install.stdout}\nstderr:\n{install.stderr}"
    )

    probe = subprocess.run(
        [
            str(python),
            "-c",
            "import vitruvyan_motus; print('motus-ok', vitruvyan_motus.__version__)",
        ],
        capture_output=True,
        text=True,
        cwd=str(tmp_path),
    )
    assert probe.returncode == 0, probe.stderr
    assert f"motus-ok {MOTUS_VERSION}" in probe.stdout

    axis_probe = subprocess.run(
        [str(python), "-c", "import axis"],
        capture_output=True,
        text=True,
        cwd=str(tmp_path),
    )
    assert axis_probe.returncode != 0, (
        "axis must NOT be importable from a venv where only the Motus "
        "wheel is installed and the cwd is not this checkout"
    )
    assert "No module named 'axis'" in axis_probe.stderr


def test_the_predecessor_runtime_is_absent_from_the_working_tree():
    """ADR-009 removed `axis/`. Its sibling above still asserts the packaging
    guarantee that matters — axis is not importable from a venv holding only
    the Motus wheel — which is now true for the stronger reason that the
    predecessor is not on disk at all. The byte-preserved artifact is the
    `v0.6.1` tag; the pinnable distribution is `vitruvyan-axis` 0.4.0.
    """
    assert not (REPO_ROOT / "axis").exists()
    assert importlib.util.find_spec("axis") is None


def test_wheel_ships_the_validator_and_its_schemas_and_nothing_else_from_contract(
    built_wheel,
):
    """Installing Motus installs the means to check what it produced.

    The first external integration documented its rows as "the same bytes
    `contract/validate.py` accepts" and never ran the validator, because
    `pip install` did not deliver it. The fix is this file list.

    The exclusions are as deliberate as the inclusions: the prose is the
    contract's authority and the fixtures are 660 KB of conformance corpus.
    Whoever needs either is already reading the repository; cloning is not
    their obstacle. The validator is different in kind — it is needed by
    everyone, without having to know it exists.
    """
    with zipfile.ZipFile(built_wheel) as archive:
        names = archive.namelist()

    assert "vitruvyan_motus/contract/validate.py" in names
    assert "vitruvyan_motus/contract/trace.v1.schema.json" in names
    assert "vitruvyan_motus/contract/graphspec.v1.schema.json" in names

    shipped_from_contract = sorted(
        n for n in names if n.startswith("vitruvyan_motus/contract/")
    )
    assert shipped_from_contract == [
        # ADR-022 decision 1: the MCP's reader is an agent inside a virtualenv,
        # and a citation it cannot resolve after `pip install` is not a
        # citation. `README.md` and `node-protocol.md` travel for that reason
        # and no other — the exact set is asserted against `CITABLE` below, so
        # neither this list nor that one can grow alone.
        "vitruvyan_motus/contract/README.md",
        "vitruvyan_motus/contract/__init__.py",
        # The commitment side travels for the same reason the trace side does:
        # a receipt is the artefact a THIRD PARTY holds, and a verifier they
        # had to clone a repository to obtain is a verifier most of them will
        # not run.
        "vitruvyan_motus/contract/checkpoint.v1.schema.json",
        "vitruvyan_motus/contract/commitment.v1.schema.json",
        "vitruvyan_motus/contract/graphspec.v1.schema.json",
        # The invariants. `durability_profile` is documented here and an
        # integrator arrives holding that word from their own trace header
        # (#108), so `motus_find` has to be able to reach it.
        "vitruvyan_motus/contract/guarantees.md",
        "vitruvyan_motus/contract/node-protocol.md",
        "vitruvyan_motus/contract/receipt.v1.schema.json",
        "vitruvyan_motus/contract/trace.v1.schema.json",
        "vitruvyan_motus/contract/validate.py",
    ], shipped_from_contract

    assert not any("fixtures" in n for n in names), (
        "the frozen conformance corpus stays in the repository"
    )
    prose = sorted(n for n in names
                   if n.endswith(".md") and n.startswith("vitruvyan_motus/"))
    assert prose == sorted(
        "vitruvyan_motus/" + relative for relative in CITABLE
        if relative.endswith(".md")), (
        "prose in the wheel is exactly what the MCP cites, and ADR-022 records "
        "it as a cost paid deliberately: " + repr(prose))


def test_the_wheel_ships_every_source_the_mcp_would_cite(built_wheel):
    """ADR-022 decision 1, checked against the artefact rather than the tree.

    The server never fetches a source it did not install — not from GitHub, not
    from a newer release — so a source missing from the wheel is not a
    degraded answer, it is a tool that raises. In a checkout every citable path
    resolves from the repository and this can never fail; the wheel is the only
    place the packaging can be wrong, which is why the check lives here.

    It runs in both directions on purpose. A citable source nobody packaged is
    a broken tool; a document packaged that nothing cites is weight in the
    distribution that no decision put there.
    """
    with zipfile.ZipFile(built_wheel) as archive:
        names = set(archive.namelist())

    missing = sorted(relative for relative in CITABLE
                     if "vitruvyan_motus/" + relative not in names)
    assert not missing, (
        f"mcp/sources.py cites these and the wheel does not carry them: {missing}"
    )

    packaged_adrs = sorted(
        n[len("vitruvyan_motus/"):] for n in names
        if n.startswith("vitruvyan_motus/adr/") and not n.endswith("__init__.py")
    )
    assert packaged_adrs == sorted(
        relative for relative in CITABLE if relative.startswith("adr/")), (
        f"an ADR travels when a tool cites it and not otherwise: {packaged_adrs}")

    # The examples are the exception, and it is a choice rather than an
    # oversight: they are one document. `motus_start_here` cites two of them,
    # and shipping only those two would leave the installed `examples/`
    # directory a half of itself, where `02` refers to `03` and neither is
    # there. So all of them travel, and the check is that none is MISSING.
    packaged_examples = sorted(
        n[len("vitruvyan_motus/examples/"):] for n in names
        if n.startswith("vitruvyan_motus/examples/")
        and not n.endswith("__init__.py")
    )
    assert packaged_examples == sorted(
        path.name for path in (REPO_ROOT / "examples").glob("*.py")
        if path.name != "__init__.py"), packaged_examples


def test_wheel_exposes_the_validator_as_a_command(built_wheel):
    """`motus-validate` — so checking a trace does not require knowing the
    module path. The entry point is the difference between a tool a consumer
    finds and one they have to be told about."""
    with zipfile.ZipFile(built_wheel) as archive:
        entry_points = archive.read(
            next(n for n in archive.namelist() if n.endswith(".dist-info/entry_points.txt"))
        ).decode("utf-8")

    assert "motus-validate" in entry_points, entry_points
    assert "vitruvyan_motus.contract.validate:main" in entry_points, entry_points


def test_shipped_schemas_are_the_repository_schemas_byte_for_byte(built_wheel):
    """The wheel maps `contract/` in; it does not hold a copy that can drift.

    If these ever differ, an installed consumer is validating against a
    different contract than the one this repository publishes — the exact
    failure the whole authority order exists to prevent.
    """
    with zipfile.ZipFile(built_wheel) as archive:
        for schema in ("trace.v1.schema.json", "graphspec.v1.schema.json"):
            shipped = archive.read(f"vitruvyan_motus/contract/{schema}")
            on_disk = (REPO_ROOT / "contract" / schema).read_bytes()
            assert shipped == on_disk, f"{schema} drifted between tree and wheel"


def test_the_shipped_validator_runs_from_its_installed_import_path(tmp_path):
    """`python -m vitruvyan_motus.contract.validate` — the invocation a
    consumer has, with no checkout — accepts a valid trace and rejects a
    tampered one.

    Finding the schemas matters as much as importing: `validate.py` resolves
    them next to itself, so this also proves the two JSON files travel with
    the module rather than being left behind.
    """
    envelope = json.loads(
        (REPO_ROOT / "contract" / "fixtures" / "04-trace-happy-path.json").read_text()
    )
    assert envelope["expect"] == "valid"

    good = tmp_path / "trace.json"
    good.write_text(json.dumps(envelope["instance"]))

    accepted = subprocess.run(
        [sys.executable, "-m", "vitruvyan_motus.contract.validate", "trace", str(good)],
        capture_output=True,
        text=True,
        cwd=str(tmp_path),
        timeout=60,
    )
    assert accepted.returncode == 0, (
        f"the shipped validator rejected a fixture the contract calls valid\n"
        f"stdout:\n{accepted.stdout}\nstderr:\n{accepted.stderr}"
    )

    tampered_doc = json.loads(json.dumps(envelope["instance"]))
    tampered_doc["records"] = tampered_doc["records"][:-1]
    tampered = tmp_path / "tampered.json"
    tampered.write_text(json.dumps(tampered_doc))

    rejected = subprocess.run(
        [
            sys.executable,
            "-m",
            "vitruvyan_motus.contract.validate",
            "trace",
            str(tampered),
        ],
        capture_output=True,
        text=True,
        cwd=str(tmp_path),
        timeout=60,
    )
    assert rejected.returncode != 0, (
        "a truncated trace must not validate — a validator that only ever "
        "says yes proves nothing"
    )


def test_importing_motus_pulls_no_third_party_module():
    """Zero new runtime dependencies means zero — not "zero declared"."""
    probe = subprocess.run(
        [
            sys.executable,
            "-c",
            "import sys; before = set(sys.modules); import vitruvyan_motus; "
            "after = set(sys.modules) - before; "
            "stdlib_or_local = {m for m in after "
            "if m.split('.')[0] in sys.stdlib_module_names "
            "or m.split('.')[0] == 'vitruvyan_motus'}; "
            "print(sorted(after - stdlib_or_local))",
        ],
        capture_output=True,
        text=True,
        cwd=str(REPO_ROOT),
        timeout=30,
    )
    assert probe.returncode == 0, probe.stderr
    assert probe.stdout.strip() == "[]", (
        f"importing vitruvyan_motus pulled non-stdlib modules: {probe.stdout}"
    )


def test_the_mcp_answers_from_an_installed_wheel_and_not_from_the_checkout(
    built_wheel, tmp_path
):
    """ADR-022 decision 1's distribution clause, in the only place it can fail.

    In a checkout every citable path resolves from the repository, so the
    server appears to work no matter how the packaging is written. Here the
    wheel is installed by itself, in a venv rooted outside this tree, and the
    tools are asked a question whose answer is a quotation: if the sources did
    not travel, `Quoted` raises rather than answering, which is decision 1
    applied to the server's own distribution.

    The alternative — the server fetching what it lacks — is what makes this
    worth a venv and two minutes. An answer derived from `main` while the
    caller runs an older release is wrong in the most convincing way
    available: correct prose about code they do not have.
    """
    isolated_venv = tmp_path / "mcp-venv"
    venv.EnvBuilder(with_pip=True, clear=True).create(isolated_venv)
    python = isolated_venv / (
        "Scripts/python.exe" if sys.platform == "win32" else "bin/python"
    )

    install = subprocess.run(
        [str(python), "-m", "pip", "install", "--no-index", "--no-deps",
         str(built_wheel)],
        capture_output=True, text=True, timeout=120,
    )
    assert install.returncode == 0, install.stderr

    # `--no-deps`, so the SDK is absent here too. That is deliberate: the
    # derivation is the valuable half of ADR-022 and it must not need a
    # transport to be correct.
    probe = subprocess.run(
        [str(python), "-c",
         "from vitruvyan_motus.mcp import sources, tools\n"
         "for relative in sources.CITABLE:\n"
         "    sources.read(relative)\n"
         "answer = tools.classify('the node runs an INSERT')\n"
         "print(answer.spans[0].text)\n"
         "print(any(getattr(s, 'source', None) for s in answer.spans))\n"],
        capture_output=True, text=True, cwd=str(tmp_path), timeout=120,
    )
    assert probe.returncode == 0, (
        f"the installed MCP could not answer from its own sources\n{probe.stderr}"
    )
    assert probe.stdout.splitlines() == [
        "\u00a74.4 terms present in your text: INSERT", "True"], probe.stdout
