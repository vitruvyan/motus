"""ATTACK 2b -- differential test of the *synchronous* path against the
pre-inversion engine (commit bb886e6, the parent of 51e2439).

The claim says the inversion "changed nothing about what the machine
decides". The strongest available oracle for that is the machine itself,
before the change. This harness runs the identical case matrix on the
pre-inversion package (extracted read-only with `git archive`) and on the
current one, in two subprocesses, and diffs the resulting traces field by
field. Every nondeterminism source is pinned, so a single differing byte is
a regression.

Usage
  python x1_prediff.py                 # orchestrate + diff
  python x1_prediff.py dump            # dump one side (MOTUS_SRC selects it)
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
PRE_SRC = os.environ.get(
    "MOTUS_PRE_SRC",
    "/tmp/claude-1000/-home-vitruvyan-motus/2eec55a9-be04-4965-b2c4-951aee158e49"
    "/scratchpad/pre/src",
)


def dump() -> None:
    import x1_matrix as M
    from x1_common import BEHAVIOURS, doc
    from vitruvyan_motus import State

    out = {}
    for case_ in M.CASES:
        name = case_["name"]
        state_factory = case_["state"] or (lambda: State.empty(name))
        names = case_["names"]

        BEHAVIOURS.clear()
        BEHAVIOURS.update(case_["behaviours"]())
        rt = M.build(case_, M.sync_registry(names))
        result, err = M.capture_sync(lambda: rt.run(state_factory(), run_id="pinned"))

        BEHAVIOURS.clear()
        BEHAVIOURS.update(case_["behaviours"]())
        rt2 = M.build(case_, M.sync_registry(names))
        records, serr = [], None
        try:
            with rt2.stream(state_factory(), run_id="pinned") as driver:
                for rec in driver:
                    records.append(rec)
        except BaseException as exc:  # noqa: BLE001
            serr = f"{type(exc).__name__}: {exc}"

        out[name] = {
            "trace": doc(rt.trace),
            "err": err,
            "state": result.state.snapshot() if result is not None else None,
            "stream": json.loads(json.dumps(records)),
            "stream_err": serr,
            "running_after": rt._running,
            "cancel_after": rt.cancel("late"),
        }
    sys.stdout.write(json.dumps(out))


def orchestrate() -> int:
    from x1_common import diff

    if not Path(PRE_SRC).is_dir():
        print(f"pre-inversion source not found at {PRE_SRC}")
        return 1
    sides = {}
    for label, src in (("post", str(ROOT / "src")), ("pre", PRE_SRC)):
        env = dict(os.environ, MOTUS_SRC=src, PYTHONPATH=str(HERE))
        proc = subprocess.run(
            [sys.executable, str(HERE / "x1_prediff.py"), "dump"],
            capture_output=True, text=True, env=env, cwd=str(HERE),
        )
        if proc.returncode != 0:
            print(f"{label} dump failed:\n{proc.stderr[-4000:]}")
            return 1
        sides[label] = json.loads(proc.stdout)

    failures = 0
    for name in sides["post"]:
        d = diff(sides["pre"][name], sides["post"][name], f"${name}")
        verdict = "PASS" if not d else "FAIL"
        if d:
            failures += 1
        print(f"[{verdict}] {name}: pre-inversion == post-inversion" + (
            "  --  " + "; ".join(d[:6]) if d else ""))
    print(f"----- {len(sides['post'])} cases, {failures} FAIL -----")
    return failures


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "dump":
        dump()
    else:
        sys.exit(1 if orchestrate() else 0)
