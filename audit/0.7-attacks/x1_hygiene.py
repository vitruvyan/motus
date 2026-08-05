"""ATTACK 4b -- per-case attribution of awaitable hygiene.

x1_coroutine.py showed the aggregate child process emits "never awaited",
"never retrieved" and "Exception ignored" under -W error::RuntimeWarning.
This script isolates ONE node shape per subprocess so each warning can be
attributed to an exact code path, and adds a pure-stdlib reproduction of a
`close()` that raises inside `_invoke_sync`'s guard.
"""

from __future__ import annotations

import asyncio
import json
import os
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent

from x1_common import PINNED, Report  # noqa: E402

from vitruvyan_motus import GraphSpec, Runtime, State  # noqa: E402

LINEAR = {
    "schema_version": "1.0.0",
    "name": "hygiene",
    "version": "1.0.0",
    "entry": "a",
    "nodes": [{"name": "a", "effect_class": "pure"}, {"name": "b", "effect_class": "pure"}],
    "transitions": {"a": {"kind": "next", "to": "b"}, "b": {"kind": "terminal"}},
}


async def inner(state):
    await asyncio.sleep(0)
    return state


async def outer_returns_coroutine(state):
    """An async node whose awaited result is itself an un-awaited coroutine."""
    await asyncio.sleep(0)
    return inner(state)


async def stubborn(state):
    """A *started* coroutine that ignores GeneratorExit.

    Pure stdlib: `asyncio.sleep(0)` is a bare yield, so the coroutine can be
    advanced without a running loop, and awaiting again inside the
    GeneratorExit handler makes CPython raise
    RuntimeError('coroutine ignored GeneratorExit') from close().
    """
    try:
        await asyncio.sleep(0)
    except GeneratorExit:
        await asyncio.sleep(0)
    return state


def node_started_stubborn_coroutine(state):
    coro = stubborn(state)
    coro.send(None)          # advance to the first suspension point
    return coro


def node_failed_future(state):
    fut = asyncio.get_event_loop().create_future()
    fut.set_exception(ValueError("future blew up"))
    return fut


def node_returns_coroutine(state):
    return inner(state)


async def async_gen(state):
    yield state


PROBES = {
    # name: (node, driver)
    "sync/def-returns-coroutine": (node_returns_coroutine, "sync"),
    "sync/async-def": (inner, "sync"),
    "sync/async-generator-fn": (async_gen, "sync"),
    "async/async-generator-fn": (async_gen, "async"),
    "async/double-awaitable": (outer_returns_coroutine, "async"),
    "sync/failed-future": (node_failed_future, "sync"),
    "sync/started-coroutine-that-ignores-generatorexit": (
        node_started_stubborn_coroutine, "sync"),
    "async/started-coroutine-that-ignores-generatorexit": (
        node_started_stubborn_coroutine, "async"),
}


async def probe(name: str) -> None:
    node, driver = PROBES[name]
    rt = Runtime(
        GraphSpec.from_dict(dict(LINEAR)), {"a": node, "b": lambda s: s}, **PINNED
    )
    err = None
    try:
        if driver == "sync":
            rt.run(State.empty("h"), run_id="pinned")
        else:
            await asyncio.wait_for(rt.arun(State.empty("h"), run_id="pinned"), 3.0)
    except BaseException as exc:  # noqa: BLE001
        err = f"{type(exc).__name__}: {exc}"
    payload = {
        "err": err,
        "kinds": [r["kind"] for r in rt.trace.records],
        "error_type": next(
            (r["error"]["type"] for r in rt.trace.records
             if r["kind"] == "transition" and r["error"]), None),
        "running": rt._running,
    }
    sys.stdout.write(json.dumps(payload))
    sys.stdout.flush()


MARKERS = ("never awaited", "never retrieved", "was destroyed but it is pending",
           "Exception ignored")


def main() -> int:
    report = Report("x1_hygiene -- per-shape awaitable hygiene, one subprocess each")
    env = dict(os.environ, PYTHONPATH=str(HERE))
    for name in PROBES:
        proc = subprocess.run(
            [sys.executable, "-W", "error::RuntimeWarning", "-X", "dev",
             str(HERE / "x1_hygiene.py"), "probe", name],
            capture_output=True, text=True, env=env, cwd=str(HERE),
        )
        hits = [m for m in MARKERS if m in proc.stderr]
        payload = json.loads(proc.stdout) if proc.stdout.strip() else {}
        report.record(
            f"{name}: no interpreter-level awaitable warning",
            not hits,
            f"markers={hits} :: " + " ".join(
                line for line in proc.stderr.splitlines()
                if any(m in line for m in MARKERS))[:300],
        )
        print(f"    . {name}: {json.dumps(payload)}")
    return report.emit()


if __name__ == "__main__":
    if len(sys.argv) > 2 and sys.argv[1] == "probe":
        asyncio.run(probe(sys.argv[2]))
    else:
        sys.exit(1 if main() else 0)
