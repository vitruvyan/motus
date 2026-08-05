"""Every example runs, on every commit.

An example that no longer works is worse than no example: it is the first
thing a reader tries, and it teaches them the library is broken. These are
executed as real subprocesses, the way someone following the README would.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
EXAMPLES = sorted((ROOT / "examples").glob("*.py"))


def test_there_are_examples_to_run():
    assert EXAMPLES, "examples/ is empty; the README points readers at it"


@pytest.mark.parametrize("example", EXAMPLES, ids=lambda path: path.stem)
def test_the_example_runs_clean(example):
    result = subprocess.run(
        [sys.executable, str(example)],
        capture_output=True, text=True, cwd=str(ROOT), timeout=120,
    )

    assert result.returncode == 0, (
        f"{example.name} failed\nstdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    )
    assert result.stdout.strip(), f"{example.name} printed nothing"
    # A traceback on stderr means something was swallowed and printed rather
    # than raised -- an example must not teach that either.
    assert "Traceback" not in result.stderr, result.stderr


@pytest.mark.parametrize("example", EXAMPLES, ids=lambda path: path.stem)
def test_the_example_says_how_to_run_it(example):
    """The first thing a reader needs is the command."""
    head = example.read_text(encoding="utf-8")[:600]
    assert f"python examples/{example.name}" in head, (
        f"{example.name}'s docstring must show the command that runs it"
    )


def test_the_readme_names_every_public_symbol():
    """The public surface list drifted to 30 of 43 names before 0.7. A reader
    who cannot find a name in the README has no reason to believe it is
    supported."""
    import vitruvyan_motus

    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    missing = [name for name in vitruvyan_motus.__all__ if name not in readme]

    assert missing == [], f"public names absent from the README: {missing}"


def test_the_readme_shows_how_to_run_each_example():
    readme = (ROOT / "README.md").read_text(encoding="utf-8")

    for example in EXAMPLES:
        assert f"python examples/{example.name}" in readme, (
            f"{example.name} exists but the README never tells anyone to run it"
        )
