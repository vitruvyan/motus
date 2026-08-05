"""Packaging boundary for the vitruvyan-motus distribution (milestone A).

Two claims are load-bearing and both are proven here against a REAL built
artifact, not against the pyproject.toml configuration that is supposed to
produce it:

1. the wheel contains `vitruvyan_motus` (with `py.typed`) and nothing
   under `axis/` — checked structurally (the wheel's own file list) AND
   operationally (installed alone, in a venv that is not this checkout,
   `vitruvyan_motus` imports and `axis` does not);
2. `axis` stays importable from the checkout regardless of what the wheel
   contains — because that importability was never a packaging fact, it
   is `python -m pytest` putting the repository root on `sys.path`
   (verified directly against the current process, no assumption).

Building a wheel here uses the same mechanism `pip install -e ".[test]"`
already relies on in CI (build isolation fetching `setuptools>=68` per
`[build-system]`), so this adds no new dependency on the environment, only
a deterministic check of what that mechanism already requires to succeed.
"""

from __future__ import annotations

import subprocess
import sys
import venv
import zipfile
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture(scope="session")
def built_wheel(tmp_path_factory) -> Path:
    """Build the vitruvyan-motus wheel once; every test below inspects it."""
    out_dir = tmp_path_factory.mktemp("motus-wheel")
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "pip",
            "wheel",
            str(REPO_ROOT),
            "--no-deps",
            "-w",
            str(out_dir),
        ],
        capture_output=True,
        text=True,
        timeout=180,
    )
    assert result.returncode == 0, (
        f"building the vitruvyan-motus wheel failed\n"
        f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    )
    wheels = list(out_dir.glob("vitruvyan_motus-*.whl"))
    assert len(wheels) == 1, f"expected exactly one wheel, found {wheels}"
    return wheels[0]


def test_wheel_is_named_for_the_declared_version(built_wheel):
    assert built_wheel.name.startswith("vitruvyan_motus-0.6.1-")


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


def test_wheel_metadata_is_accurate_and_declares_zero_runtime_dependencies(
    built_wheel,
):
    """The wheel must identify Motus truthfully and declare no unconditional
    Requires-Dist. The `test` extra's dependencies are expected and remain
    correctly gated behind `extra == "test"`."""
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
    assert unconditional == [], (
        f"vitruvyan-motus must declare zero unconditional runtime "
        f"dependencies, found: {unconditional}"
    )
    assert "Name: vitruvyan-motus" in metadata
    assert "Version: 0.6.1" in metadata
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
    assert "motus-ok 0.6.1" in probe.stdout

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


def test_axis_stays_importable_from_the_checkout():
    """Not a packaging fact — a `python -m pytest` fact. This test running
    at all, in this process, from this repository root, is the proof:
    `axis` resolves from the checkout regardless of anything declared in
    pyproject.toml, because the mechanism that finds it is CWD-on-sys.path,
    never a pip install.
    """
    import axis

    assert Path(axis.__file__).resolve() == (REPO_ROOT / "axis" / "__init__.py")


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
