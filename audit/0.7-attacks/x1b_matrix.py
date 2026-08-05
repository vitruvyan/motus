"""ROUND 2, ITEM 5 -- re-run the equivalence matrix and the differential on
`a72cdf1`, three ways.

  bb886e6  pre-inversion            (no async at all)
  51e2439  post-inversion, pre-fix
  a72cdf1  HEAD

The synchronous path must be byte-identical across all three: the inversion
claimed behaviour neutrality, and the fix claimed to touch only paths that
were already broken. Any pair that differs is a regression introduced by the
fix on a path the fix was not supposed to reach.

The full sync/async equivalence + conformance sweep is re-run in-process by
delegating to x1_matrix.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from itertools import combinations
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
SCRATCH = ("/tmp/claude-1000/-home-vitruvyan-motus/"
           "2eec55a9-be04-4965-b2c4-951aee158e49/scratchpad")

# a72cdf1 is read from a read-only `git archive` extract, NOT from the working
# tree: another process modified src/ mid-session, so the working tree is not a
# defined baseline.
TREES = {
    "bb886e6 (pre-inversion)": f"{SCRATCH}/pre/src",
    "51e2439 (post-inversion, pre-fix)": f"{SCRATCH}/prefix/src",
    "a72cdf1 (commit, pristine)": os.environ.get(
        "MOTUS_HEAD_SRC", f"{SCRATCH}/head/src"),
    "working tree (a72cdf1 + uncommitted)": str(ROOT / "src"),
}


def dump(src: str):
    env = dict(os.environ, MOTUS_SRC=src, PYTHONPATH=str(HERE))
    proc = subprocess.run(
        [sys.executable, str(HERE / "x1_prediff.py"), "dump"],
        capture_output=True, text=True, env=env, cwd=str(HERE), timeout=900,
    )
    if proc.returncode != 0:
        raise SystemExit(f"dump failed for {src}:\n{proc.stderr[-4000:]}")
    return json.loads(proc.stdout)


def main() -> int:
    from x1_common import Report, diff

    report = Report("x1b_matrix -- three-way synchronous differential")
    missing = [label for label, src in TREES.items() if not Path(src).is_dir()]
    if missing:
        print(f"missing trees: {missing}")
        return 1

    sides = {label: dump(src) for label, src in TREES.items()}
    for left, right in combinations(sides, 2):
        failures = []
        for name in sides[right]:
            d = diff(sides[left][name], sides[right][name], f"${name}")
            if d:
                failures.append(f"{name}: " + "; ".join(d[:3]))
        report.record(
            f"{left}  ==  {right}   (27 cases, sync path)",
            not failures, " || ".join(failures[:3]),
        )

    print("\n--- delegating the full sync/async equivalence + conformance sweep "
          "to x1_matrix on HEAD ---")
    for label, src in (("a72cdf1 (pristine)", TREES["a72cdf1 (commit, pristine)"]),
                       ("working tree", str(ROOT / "src"))):
        proc = subprocess.run(
            [sys.executable, str(HERE / "x1_matrix.py")],
            capture_output=True, text=True,
            env=dict(os.environ, PYTHONPATH=str(HERE), MOTUS_SRC=src),
            cwd=str(HERE), timeout=900,
        )
        tail = [l for l in proc.stdout.splitlines() if "checks," in l]
        fails = [l for l in proc.stdout.splitlines() if l.startswith("[FAIL]")]
        report.record(
            f"x1_matrix full sweep on {label}: 0 FAIL",
            not fails and tail and ", 0 FAIL" in tail[-1],
            (tail[-1] if tail else "no summary") + " || " + " || ".join(fails[:3]),
        )
    return report.emit()


if __name__ == "__main__":
    sys.exit(1 if main() else 0)
