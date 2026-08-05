"""ROUND 3, ITEM 6 -- `_drive` now ends with `finally: machine.close()`.

runtime.py:215-240. Two questions:
  * does it change ANY record stream, terminal or exception propagation
    versus a72cdf1 / 51e2439 / bb886e6?
  * `run()` consumes `_drive` to exhaustion -- is `machine.close()` on an
    already-exhausted generator truly a no-op there, or does it perturb the
    lifecycle flags the run leaves behind?

Every probe runs in a subprocess against each tree so the answers are a
differential, not an assertion.
"""

from __future__ import annotations

import gc
import json
import os
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
SCRATCH = ("/tmp/claude-1000/-home-vitruvyan-motus/"
           "2eec55a9-be04-4965-b2c4-951aee158e49/scratchpad")
TREES = {
    "bb886e6": f"{SCRATCH}/pre/src",
    "51e2439": f"{SCRATCH}/prefix/src",
    "a72cdf1": f"{SCRATCH}/h2/src",
    "fb30b8d": f"{SCRATCH}/h3/src",
}

from x1_common import FIXED_TS, PINNED, Report, diff  # noqa: E402

from vitruvyan_motus import Fact, GraphSpec, Policy, Runtime, State  # noqa: E402

SPEC_DOC = {
    "schema_version": "1.0.0",
    "name": "driveclose",
    "version": "1.0.0",
    "entry": "a",
    "nodes": [
        {"name": "a", "effect_class": "pure"},
        {"name": "b", "effect_class": "pure"},
        {"name": "c", "effect_class": "pure"},
    ],
    "transitions": {
        "a": {"kind": "next", "to": "b"},
        "b": {"kind": "next", "to": "c"},
        "c": {"kind": "terminal"},
    },
}


def ok(state):
    return state.with_fact(Fact("k", 1, "s", FIXED_TS))


def boom(state):
    raise ValueError("down")


def build(nodes=None, **kw):
    return Runtime(
        GraphSpec.from_dict(dict(SPEC_DOC)),
        nodes or {"a": ok, "b": ok, "c": ok}, **kw, **PINNED,
    )


def snap(rt, err=None, extra=None):
    out = {
        "err": err,
        "kinds": [r["kind"] for r in rt.trace.records] if rt.trace else None,
        "running": rt._running,
        "hub_closed": rt._hub._closed if rt._hub else None,
        "cancel_reason": rt._cancel_reason,
        "trace": json.loads(json.dumps(rt.trace.to_dict())) if rt.trace else None,
    }
    if extra:
        out.update(extra)
    return out


PROBES = {}


def probe(name):
    def register(fn):
        PROBES[name] = fn
        return fn

    return register


@probe("run-to-exhaustion")
def _p1():
    rt = build()
    result = rt.run(State.empty("x"), run_id="p")
    return snap(rt, None, {"status": result.status,
                           "state": result.state.snapshot()})


@probe("run-node-failure")
def _p2():
    rt = build({"a": ok, "b": boom, "c": ok})
    err = None
    try:
        rt.run(State.empty("x"), run_id="p")
    except BaseException as exc:  # noqa: BLE001
        err = f"{type(exc).__name__}: {exc}"
    return snap(rt, err)


@probe("run-basexception-teardown")
def _p3():
    def kb(state):
        raise KeyboardInterrupt("hard")

    rt = build({"a": kb, "b": ok, "c": ok})
    err = None
    try:
        rt.run(State.empty("x"), run_id="p")
    except BaseException as exc:  # noqa: BLE001
        err = f"{type(exc).__name__}: {exc}"
    return snap(rt, err)


@probe("stream-exhausted")
def _p4():
    rt = build()
    kinds = []
    with rt.stream(State.empty("x"), run_id="p") as d:
        for rec in d:
            kinds.append(rec["kind"])
    return snap(rt, None, {"yielded": kinds, "closed": d._closed})


@probe("stream-explicit-close-midrun")
def _p5():
    rt = build()
    d = rt.stream(State.empty("x"), run_id="p")
    next(d)
    next(d)
    d.close("stopped")
    return snap(rt, None, {"closed": d._closed})


@probe("stream-abandoned-midrun")
def _p6():
    rt = build()
    d = rt.stream(State.empty("x"), run_id="p")
    next(d)
    trace = rt.trace
    del d
    gc.collect()
    later = None
    try:
        later = rt.run(State.empty("later"), run_id="l").status
    except BaseException as exc:  # noqa: BLE001
        later = f"{type(exc).__name__}"
    return {"abandoned_kinds": [r["kind"] for r in trace.records],
            "later_run": later, "running_before_later": None}


@probe("stream-never-iterated")
def _p7():
    rt = build()
    d = rt.stream(State.empty("x"), run_id="p")
    del d
    gc.collect()
    later = None
    try:
        later = rt.run(State.empty("later"), run_id="l").status
    except BaseException as exc:  # noqa: BLE001
        later = f"{type(exc).__name__}"
    return {"later_run": later, "running": rt._running,
            "hub_closed": rt._hub._closed if rt._hub else None}


@probe("stream-close-then-close-again")
def _p8():
    rt = build()
    d = rt.stream(State.empty("x"), run_id="p")
    next(d)
    d.close("first")
    err = None
    try:
        d.close("second")
    except BaseException as exc:  # noqa: BLE001
        err = f"{type(exc).__name__}"
    return snap(rt, err, {"closed": d._closed})


@probe("stream-broken-early")
def _p9():
    rt = build()
    kinds = []
    with rt.stream(State.empty("x"), run_id="p") as d:
        for rec in d:
            kinds.append(rec["kind"])
            break
    return snap(rt, None, {"yielded": kinds, "closed": d._closed})


@probe("exploration-continue")
def _p10():
    rt = build({"a": boom, "b": ok, "c": ok}, policy=Policy.EXPLORATION)
    result = rt.run(State.empty("x"), run_id="p")
    return snap(rt, None, {"status": result.status})


def dump() -> None:
    out = {}
    for name, fn in PROBES.items():
        try:
            out[name] = fn()
        except BaseException as exc:  # noqa: BLE001
            out[name] = {"probe_crashed": f"{type(exc).__name__}: {exc}"}
    sys.stdout.write(json.dumps(out, default=str))


def run_side(src):
    proc = subprocess.run(
        [sys.executable, str(HERE / "x1c_drive_close.py"), "dump"],
        capture_output=True, text=True,
        env=dict(os.environ, PYTHONPATH=str(HERE), MOTUS_SRC=src),
        cwd=str(HERE), timeout=300,
    )
    if not proc.stdout.strip():
        return None, proc.stderr
    return json.loads(proc.stdout), proc.stderr


def main() -> int:
    report = Report("x1c_drive_close -- `finally: machine.close()` on the equivalence axis")
    sides = {}
    for label, src in TREES.items():
        if not Path(src).is_dir():
            print(f"missing {label} at {src}")
            return 1
        sides[label], err = run_side(src)
        if sides[label] is None:
            print(f"{label} failed:\n{err[-2500:]}")
            return 1

    baseline = "a72cdf1"
    for name in PROBES:
        for tree in ("bb886e6", "51e2439", "a72cdf1"):
            d = diff(sides[tree][name], sides["fb30b8d"][name])
            report.record(
                f"{name}: fb30b8d == {tree}",
                not d, "; ".join(d[:4]),
            )
        print(f"    . {name}: {json.dumps(sides['fb30b8d'][name])[:170]}")

    # The one thing that SHOULD have changed.
    report.record(
        "…except the lifecycle release the fix targeted "
        "(recorded explicitly, not as a silent pass)",
        True,
        f"stream-abandoned-midrun later_run: "
        f"bb886e6={sides['bb886e6']['stream-abandoned-midrun']['later_run']} "
        f"51e2439={sides['51e2439']['stream-abandoned-midrun']['later_run']} "
        f"a72cdf1={sides['a72cdf1']['stream-abandoned-midrun']['later_run']} "
        f"fb30b8d={sides['fb30b8d']['stream-abandoned-midrun']['later_run']}; "
        f"stream-never-iterated later_run: "
        f"a72cdf1={sides['a72cdf1']['stream-never-iterated']['later_run']} "
        f"fb30b8d={sides['fb30b8d']['stream-never-iterated']['later_run']}",
    )
    return report.emit()


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "dump":
        dump()
    else:
        sys.exit(1 if main() else 0)
