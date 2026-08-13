#!/usr/bin/env python3
"""Neuter a fix, run the tests, and confirm they notice.

    python tools/mutation_probe.py PROBES.json

A mutation probe answers one question: **would the suite have caught this if
the fix were absent?** A test that passes either way is a test that asserts the
right sentence about the wrong thing, and this project has shipped several.

## Why this is a tool and not three lines of shell

The obvious harness edits the file and restores it with `git checkout --`.
That discards *every* uncommitted change to that file, not only the mutation —
and in this project it silently destroyed a morning's work **four separate
times**, each time after the rule "commit before probing" had been written down
and not kept.

So restoration here never touches git. The original bytes are held in memory
and written back, which is correct on a dirty tree, correct when the anchor is
not found, and correct when pytest dies. The clean-tree check below is the
second layer, for the case this process is killed between mutate and restore:
then, and only then, `git checkout --` is a safe recovery for the operator.

**A rule that has to be remembered is a rule that will be broken. Put it in the
tool.**

## The probes file

    [
      {"name": "what is being neutered",
       "file": "src/vitruvyan_motus/commitlog.py",
       "from": "exact text to replace (first occurrence)",
       "to":   "the neutered text",
       "tests": "tests/test_commitlog.py tests/test_commit_lifecycle.py"}
    ]

Exit code is 0 when every probe was KILLED, 1 when any survived or could not be
applied. A SURVIVED probe is not automatically a defect — an *equivalent*
mutant is a real category, and the honest response is to record why in the
source rather than to invent a contrived test. But it is never something to
leave unexplained.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
GUARDED = ("src", "contract", "tests", "benchmarks")


def working_tree_is_clean() -> tuple[bool, str]:
    done = subprocess.run(
        ["git", "status", "--porcelain", "--", *GUARDED],
        cwd=ROOT, capture_output=True, text=True, check=False,
    )
    return not done.stdout.strip(), done.stdout.strip()


def run_probe(probe: dict, python: str) -> str:
    path = ROOT / probe["file"]
    original = path.read_text(encoding="utf-8")
    if probe["from"] not in original:
        return "NOT-APPLIED"
    path.write_text(original.replace(probe["from"], probe["to"], 1),
                    encoding="utf-8")
    try:
        done = subprocess.run(
            [python, "-m", "pytest", "-q", *probe["tests"].split()],
            cwd=ROOT, capture_output=True, text=True, check=False, timeout=900,
        )
    except subprocess.TimeoutExpired:
        # A mutant that hangs the suite is caught, and loudly: it is what the
        # daemon-thread probe did, and the timeout IS the signal.
        return "KILLED"
    finally:
        # Never `git checkout`. The bytes we replaced are the bytes we restore.
        path.write_text(original, encoding="utf-8")
    return "KILLED" if done.returncode != 0 else "SURVIVED"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("probes", help="JSON file describing the probes")
    parser.add_argument("--python", default=str(ROOT / ".venv" / "bin" / "python"))
    parser.add_argument(
        "--allow-dirty", action="store_true",
        help=("run with uncommitted changes. Restoration does not use git, so "
              "this is safe for the probe itself; the check exists for the "
              "case this process is killed mid-probe, where a clean tree is "
              "what makes `git checkout --` a safe recovery"),
    )
    args = parser.parse_args(argv)

    clean, dirt = working_tree_is_clean()
    if not clean and not args.allow_dirty:
        print("REFUSED: uncommitted changes under " + ", ".join(GUARDED) + ":")
        print(dirt)
        print("\nCommit first. If this process is killed between mutating and "
              "restoring, a clean tree is what makes recovery a one-liner.\n"
              "Pass --allow-dirty if you accept that.")
        return 1

    probes = json.loads(Path(args.probes).read_text(encoding="utf-8"))
    survived = 0
    for probe in probes:
        outcome = run_probe(probe, args.python)
        print(f"{outcome:<11} {probe['name']}")
        if outcome != "KILLED":
            survived += 1
    print(f"\n{len(probes) - survived}/{len(probes)} killed")
    return 1 if survived else 0


if __name__ == "__main__":
    sys.exit(main())
