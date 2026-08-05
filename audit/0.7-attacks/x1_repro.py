"""Minimal reproductions of the two confirmed findings.

  python .attack/x1_repro.py a    # X1-A  guard cleanup outside the failure boundary
  python .attack/x1_repro.py b    # X1-B  _invoke_async drops a second-level awaitable

Run B under `-W error::RuntimeWarning -X dev` to see the leak escalate.
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from vitruvyan_motus import GraphSpec, Runtime, State

SPEC = GraphSpec.from_dict({
    "schema_version": "1.0.0", "name": "repro", "version": "1.0.0", "entry": "a",
    "nodes": [{"name": "a", "effect_class": "pure"}],
    "transitions": {"a": {"kind": "terminal"}},
})


# --------------------------------------------------------------------------- #
# X1-A  runtime.py:166-168 -- returned.close() sits OUTSIDE the try/except      #
#       Exception at runtime.py:162-165, so an ordinary Exception raised by     #
#       the guard's own cleanup escapes the node-failure boundary.             #
# --------------------------------------------------------------------------- #

async def stubborn(state):
    """A started coroutine that ignores GeneratorExit -> close() raises."""
    try:
        await asyncio.sleep(0)          # a bare yield; no running loop needed
    except GeneratorExit:
        await asyncio.sleep(0)          # ignoring it makes close() raise
    return state


def node_a(state):
    coro = stubborn(state)
    coro.send(None)                     # advance to the suspension point
    return coro


def repro_a() -> None:
    runtime = Runtime(SPEC, {"a": node_a})
    try:
        runtime.run(State.empty("repro"))
        print("run() returned normally -- unexpected")
    except BaseException as exc:
        print(f"run() raised   : {type(exc).__name__}: {exc}")
    print(f"trace kinds     : {[r['kind'] for r in runtime.trace.records]}")
    print("expected        : NodeFailed, "
          "['run_started', 'attempt_started', 'transition', 'run_failed']")

    async def under_arun():
        rt = Runtime(SPEC, {"a": node_a})
        try:
            await rt.arun(State.empty("repro"))
        except BaseException as exc:
            print(f"arun() raised  : {type(exc).__name__}: {exc}")
        print(f"trace kinds     : {[r['kind'] for r in rt.trace.records]}")
        print(f"error type      : "
              f"{[r['error'] for r in rt.trace.records if r['kind'] == 'transition']}")

    asyncio.run(under_arun())


# --------------------------------------------------------------------------- #
# X1-B  runtime.py:214-215 -- `if inspect.isawaitable(returned): returned =     #
#       await returned` awaits exactly one level. A second-level awaitable is   #
#       dropped un-awaited and un-closed, where _invoke_sync closes it.        #
# --------------------------------------------------------------------------- #

async def inner(state):
    await asyncio.sleep(0)
    return state


async def outer(state):
    await asyncio.sleep(0)
    return inner(state)                 # the result is itself a coroutine


def repro_b() -> None:
    async def go():
        rt = Runtime(SPEC, {"a": outer})
        try:
            await rt.arun(State.empty("repro"))
        except BaseException as exc:
            print(f"arun() raised  : {type(exc).__name__}: {exc}")
        print(f"trace kinds     : {[r['kind'] for r in rt.trace.records]}")

    asyncio.run(go())
    import gc

    gc.collect()
    print("expected        : no RuntimeWarning; actual: "
          "'coroutine inner was never awaited' on stderr")


if __name__ == "__main__":
    which = sys.argv[1] if len(sys.argv) > 1 else "a"
    (repro_a if which == "a" else repro_b)()
