"""x3_01 — lifecycle asymmetries between StreamDriver and AsyncStreamDriver.

Hypotheses under test:

H1  Abandoning a *sync* StreamDriver mid-run releases the Runtime (the
    generator chain is finalised by refcount), so a later run() succeeds.
H2  Abandoning an *async* AsyncStreamDriver mid-run does NOT release the
    Runtime, because an async generator's finalisation is deferred to the
    event loop's asyncgen hooks. If so the Runtime is wedged: `_running`
    stays True, `_hub.close()` never runs, and a later arun()/run() raises
    "a Runtime instance cannot execute overlapping runs".
H3  An exception raised in the body of `async with runtime.astream()` still
    closes the run (aexit path).
H4  A sync StreamDriver abandoned inside a *loop-free* context behaves the
    same as before the inversion (regression check).
"""

from __future__ import annotations

import asyncio
import gc
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from vitruvyan_motus import Decision, Fact, GraphSpec, Runtime, State  # noqa: E402

NOW = datetime(2026, 8, 5, tzinfo=timezone.utc)

LINEAR = GraphSpec.from_dict(
    {
        "schema_version": "1.0.0",
        "name": "x3-linear",
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
)


def n(name):
    def node(state: State) -> State:
        return state.with_fact(Fact(name, "done", "x3", NOW))

    return node


def an(name):
    async def node(state: State) -> State:
        await asyncio.sleep(0)
        return state.with_fact(Fact(name, "done", "x3", NOW))

    return node


SYNC_NODES = {"a": n("a"), "b": n("b"), "c": n("c")}
ASYNC_NODES = {"a": an("a"), "b": an("b"), "c": an("c")}

results: list[tuple[str, str, str]] = []


def record(hid: str, verdict: str, detail: str) -> None:
    results.append((hid, verdict, detail))


# ---------------------------------------------------------------- H1 (sync) --
def h1_sync_abandon():
    runtime = Runtime(LINEAR, SYNC_NODES)
    driver = runtime.stream(State.empty("first"))
    next(driver)  # run_started
    next(driver)  # attempt_started for a
    del driver
    gc.collect()
    try:
        result = runtime.run(State.empty("second"))
        record("H1", "RELEASED", f"second run status={result.trace.to_dict()['records'][-1]['kind']}")
    except RuntimeError as exc:
        record("H1", "WEDGED", f"{type(exc).__name__}: {exc}")


# --------------------------------------------------------------- H2 (async) --
async def h2_async_abandon():
    runtime = Runtime(LINEAR, ASYNC_NODES)
    driver = runtime.astream(State.empty("first"))
    await driver.__anext__()  # run_started
    await driver.__anext__()  # attempt_started for a
    del driver
    gc.collect()
    await asyncio.sleep(0)
    gc.collect()
    await asyncio.sleep(0)
    try:
        result = await runtime.arun(State.empty("second"))
        record("H2", "RELEASED", f"second run status={result.trace.to_dict()['records'][-1]['kind']}")
    except RuntimeError as exc:
        record("H2", "WEDGED", f"{type(exc).__name__}: {exc}")


# ---------------------------------------------------------- H2b (sync after) --
async def h2b_async_abandon_then_sync_run():
    runtime = Runtime(LINEAR, ASYNC_NODES)
    driver = runtime.astream(State.empty("first"))
    await driver.__anext__()
    await driver.__anext__()
    del driver
    gc.collect()
    await asyncio.sleep(0)
    try:
        runtime.run(State.empty("second"))
        record("H2b", "RELEASED", "sync run after abandoned astream succeeded")
    except RuntimeError as exc:
        record("H2b", "WEDGED", f"{type(exc).__name__}: {exc}")


# --------------------------------------------------------------------- H3 ----
async def h3_exception_in_async_with():
    runtime = Runtime(LINEAR, ASYNC_NODES)
    try:
        async with runtime.astream(State.empty("first")) as driver:
            await driver.__anext__()
            await driver.__anext__()
            raise ZeroDivisionError("consumer blew up")
    except ZeroDivisionError:
        pass
    try:
        await runtime.arun(State.empty("second"))
        record("H3", "RELEASED", "aexit drained and released the runtime")
    except RuntimeError as exc:
        record("H3", "WEDGED", f"{type(exc).__name__}: {exc}")


# --------------------------------------------------------------------- H4 ----
def h4_sync_break_out_of_for():
    runtime = Runtime(LINEAR, SYNC_NODES)
    for _ in runtime.stream(State.empty("first")):
        break
    gc.collect()
    try:
        runtime.run(State.empty("second"))
        record("H4", "RELEASED", "break-out-of-for released the runtime")
    except RuntimeError as exc:
        record("H4", "WEDGED", f"{type(exc).__name__}: {exc}")


# --------------------------------------------------------------------- H5 ----
async def h5_async_break_out_of_async_for():
    """`async for` + break: does the abandoned asyncgen release the runtime?"""
    runtime = Runtime(LINEAR, ASYNC_NODES)
    async for _ in runtime.astream(State.empty("first")):
        break
    gc.collect()
    await asyncio.sleep(0)
    try:
        await runtime.arun(State.empty("second"))
        record("H5", "RELEASED", "async-for break released the runtime")
    except RuntimeError as exc:
        record("H5", "WEDGED", f"{type(exc).__name__}: {exc}")


# --------------------------------------------------------------------- H6 ----
async def h6_task_cancellation_midrun():
    """Cancel the asyncio Task running arun(): does the runtime release?"""
    started = asyncio.Event()
    hold = asyncio.Event()

    async def slow(state: State) -> State:
        started.set()
        await hold.wait()
        return state.with_fact(Fact("a", "done", "x3", NOW))

    runtime = Runtime(LINEAR, {"a": slow, "b": an("b"), "c": an("c")})
    task = asyncio.create_task(runtime.arun(State.empty("first")))
    await started.wait()
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass
    gc.collect()
    await asyncio.sleep(0)
    try:
        await runtime.arun(State.empty("second"))
        record("H6", "RELEASED", "task cancellation released the runtime")
    except RuntimeError as exc:
        record("H6", "WEDGED", f"{type(exc).__name__}: {exc}")


def main() -> int:
    h1_sync_abandon()
    h4_sync_break_out_of_for()
    asyncio.run(h2_async_abandon())
    asyncio.run(h2b_async_abandon_then_sync_run())
    asyncio.run(h3_exception_in_async_with())
    asyncio.run(h5_async_break_out_of_async_for())
    asyncio.run(h6_task_cancellation_midrun())
    width = max(len(h) for h, _, _ in results)
    for hid, verdict, detail in results:
        print(f"{hid:<{width}}  {verdict:<9}  {detail}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
