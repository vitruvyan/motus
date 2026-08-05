"""ATTACK 5 -- pathological async node shapes, plus interleaving.

Beyond the shapes the brief names, this adds the attack the async driver
actually invites: several Runtimes interleaved on one event loop. Each run is
single-lane, but the loop is not, so if any per-run state had leaked into
module scope the interleaved traces would differ from the sequential ones.
"""

from __future__ import annotations

import asyncio
import sys

from x1_common import FIXED_TS, PINNED, Report, diff, doc, load_validator

from vitruvyan_motus import Fact, GraphSpec, Runtime, State

validate = load_validator()

LINEAR = {
    "schema_version": "1.0.0",
    "name": "shapes",
    "version": "1.0.0",
    "entry": "a",
    "nodes": [{"name": "a", "effect_class": "pure"}, {"name": "b", "effect_class": "pure"}],
    "transitions": {"a": {"kind": "next", "to": "b"}, "b": {"kind": "terminal"}},
}

ORPHANS: list[asyncio.Task] = []


def build(nodes, **kw):
    return Runtime(GraphSpec.from_dict(dict(LINEAR)), nodes, **kw, **PINNED)


async def tick(state):
    await asyncio.sleep(0)
    return state


async def slow(state):
    await asyncio.sleep(30)
    return state


async def spawns_orphan(state):
    async def background():
        await asyncio.sleep(0.05)

    ORPHANS.append(asyncio.get_running_loop().create_task(background()))
    await asyncio.sleep(0)
    return state.with_fact(Fact("spawned", True, "s", FIXED_TS))


async def await_then_raise(state):
    await asyncio.sleep(0)
    raise ValueError("after the await")


async def resolved_future(state):
    fut = asyncio.get_running_loop().create_future()
    fut.set_result(state.with_fact(Fact("via", "future", "s", FIXED_TS)))
    return await fut


def returns_resolved_future(state):
    fut = asyncio.get_running_loop().create_future()
    fut.set_result(state.with_fact(Fact("via", "future", "s", FIXED_TS)))
    return fut


async def reenters_run(state):
    await asyncio.sleep(0)
    RE_ENTRY["result"] = "no error"
    try:
        RE_ENTRY["runtime"].run(State.empty("nested"))
    except BaseException as exc:  # noqa: BLE001
        RE_ENTRY["result"] = f"{type(exc).__name__}: {exc}"
    return state


RE_ENTRY: dict = {}


async def cancels_itself(state):
    await asyncio.sleep(0)
    SELF_CANCEL["runtime"].cancel("cancelled from inside a node")
    return state.with_fact(Fact("k", 1, "s", FIXED_TS))


SELF_CANCEL: dict = {}


async def main() -> int:
    report = Report("x1_asyncshapes -- pathological async shapes and interleaving")

    # --- a node that awaits and then raises --------------------------------
    rt = build({"a": await_then_raise, "b": tick})
    err = None
    try:
        await rt.arun(State.empty("raise"), run_id="pinned")
    except BaseException as exc:  # noqa: BLE001
        err = type(exc).__name__
    tr = [r["kind"] for r in rt.trace.records]
    report.record(
        "await-then-raise is an ordinary raised attempt",
        err == "NodeFailed" and tr == ["run_started", "attempt_started", "transition", "run_failed"],
        f"{err} {tr}",
    )
    report.record(
        "…and its trace is contract-valid",
        validate.validate_trace(rt.trace.to_dict(), spec=LINEAR) == [], "",
    )

    # --- an already-resolved Future, awaited and returned bare -------------
    for label, node in (("awaited", resolved_future), ("returned-bare", returns_resolved_future)):
        rt = build({"a": node, "b": tick})
        res = await rt.arun(State.empty("fut"), run_id="pinned")
        report.record(
            f"resolved Future ({label}) commits normally under arun",
            res.status == "completed" and res.state.fact("via") == "future",
            f"status={res.status}",
        )
        report.record(
            f"resolved Future ({label}) trace is contract-valid",
            validate.validate_trace(rt.trace.to_dict(), spec=LINEAR) == [], "",
        )

    # --- a node that spawns a task outliving the call ----------------------
    ORPHANS.clear()
    rt = build({"a": spawns_orphan, "b": tick})
    res = await rt.arun(State.empty("orphan"), run_id="pinned")
    report.record(
        "a node may spawn a task that outlives it; the run still terminates",
        res.status == "completed" and ORPHANS and not ORPHANS[0].done(),
        f"status={res.status} orphan_done={ORPHANS[0].done() if ORPHANS else None}",
    )
    await asyncio.gather(*ORPHANS)

    # --- external task.cancel() while a node awaits ------------------------
    rt = build({"a": slow, "b": tick})
    task = asyncio.create_task(rt.arun(State.empty("cancelled"), run_id="pinned"))
    await asyncio.sleep(0.05)
    task.cancel()
    outcome = None
    try:
        await task
    except BaseException as exc:  # noqa: BLE001
        outcome = type(exc).__name__
    kinds = [r["kind"] for r in rt.trace.records]
    report.record(
        "external task.cancel() during a node await tears the run down (7.3)",
        outcome == "CancelledError" and kinds == ["run_started", "attempt_started"],
        f"{outcome} {kinds}",
    )
    report.record(
        "…and the lifecycle is released (_running False, hub closed)",
        rt._running is False and rt._hub._closed is True
        and rt._cancel_reason is None and rt._active_attempt is None,
        f"running={rt._running} closed={rt._hub._closed}",
    )
    report.record(
        "…and the torn-down Runtime accepts a fresh run",
        (await build({"a": tick, "b": tick}).arun(State.empty("after"))).status == "completed",
        "",
    )

    # --- external cancel of an astream consumer ----------------------------
    rt = build({"a": slow, "b": tick})
    driver = rt.astream(State.empty("astream-cancel"), run_id="pinned")

    async def consume():
        out = []
        async for rec in driver:
            out.append(rec["kind"])
        return out

    ctask = asyncio.create_task(consume())
    await asyncio.sleep(0.05)
    ctask.cancel()
    coutcome = None
    try:
        await ctask
    except BaseException as exc:  # noqa: BLE001
        coutcome = type(exc).__name__
    report.record(
        "external cancel of an astream consumer tears the run down and closes the driver",
        coutcome == "CancelledError" and driver._closed is True and rt._running is False,
        f"{coutcome} closed={driver._closed} running={rt._running}",
    )

    # --- re-entrancy: a node starting a run on its own Runtime -------------
    rt = build({"a": reenters_run, "b": tick})
    RE_ENTRY["runtime"] = rt
    res = await rt.arun(State.empty("reentrant"), run_id="pinned")
    report.record(
        "a node cannot start an overlapping run on its own Runtime",
        "RuntimeError" in RE_ENTRY["result"] and "overlapping" in RE_ENTRY["result"],
        RE_ENTRY["result"],
    )

    # --- a node cancelling its own run -------------------------------------
    rt = build({"a": cancels_itself, "b": tick})
    SELF_CANCEL["runtime"] = rt
    res = await rt.arun(State.empty("selfcancel"), run_id="pinned")
    kinds = [r["kind"] for r in rt.trace.records]
    report.record(
        "self-cancellation lands a recorded run_cancelled after the transition",
        res.status == "cancelled" and kinds[-1] == "run_cancelled",
        f"{kinds}",
    )
    report.record(
        "…and that cancellation trace is contract-valid",
        validate.validate_trace(rt.trace.to_dict(), spec=LINEAR) == [],
        str(validate.validate_trace(rt.trace.to_dict(), spec=LINEAR)[:1]),
    )

    # --- interleaving: N independent Runtimes on one loop ------------------
    async def one(i):
        rt = build({"a": tick, "b": tick})
        return doc((await rt.arun(State.empty(f"i{i}"), run_id=f"run-{i}")).trace)

    sequential = [await one(i) for i in range(8)]
    concurrent = await asyncio.gather(*(one(i) for i in range(8)))
    d = diff(sequential, list(concurrent))
    report.record(
        "8 Runtimes interleaved on one loop == the same 8 run sequentially",
        not d, "; ".join(d[:4]),
    )

    # --- interleaving with contention inside the node ----------------------
    gate = asyncio.Event()

    async def gated(state):
        await gate.wait()
        return state.with_fact(Fact("gated", True, "s", FIXED_TS))

    rts = [build({"a": gated, "b": tick}) for _ in range(4)]
    tasks = [
        asyncio.create_task(rt.arun(State.empty(f"g{i}"), run_id=f"g-{i}"))
        for i, rt in enumerate(rts)
    ]
    await asyncio.sleep(0.05)
    gate.set()
    results = await asyncio.gather(*tasks)
    seqs = [[r["seq"] for r in res.trace.records] for res in results]
    report.record(
        "each interleaved run keeps its own contiguous seq numbering",
        all(s == list(range(1, len(s) + 1)) for s in seqs),
        f"{seqs[:1]}",
    )
    report.record(
        "every interleaved trace is contract-valid",
        all(validate.validate_trace(res.trace.to_dict(), spec=LINEAR) == [] for res in results),
        "",
    )

    return report.emit()


if __name__ == "__main__":
    sys.exit(1 if asyncio.run(main()) else 0)
