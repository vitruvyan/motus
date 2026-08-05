"""ROUND 2, ITEM 4 -- attack `_run_scoped_cancel` and the `aclose` guard.

runtime.py:438-455:

    def cancel(reason):
        if self._trace_ref is not trace_ref:
            return False
        return self.cancel(reason)

observers.py:336-353:

    self._cancel(reason)
    if getattr(self._iterator, "ag_running", False):
        return                      # <-- returns WITHOUT setting _closed

Attacks:
  * the identity predicate -- can `_trace_ref` ever be the same list twice,
    and is there a path where a driver's cancel SHOULD bind but no longer does?
  * the must-still-work direction: `close()`/`aclose()` on a live run must
    still land `run_cancelled` (ADR-008 §1 keeps "closing an active driver
    retains cooperative, trace-visible cancellation").
  * the early return in `aclose`: it leaves `_closed` False and the run in
    flight, which is not what the class docstring (observers.py:301-302,
    unchanged by the fix) promises: "aclose on a live run drains only the
    cancellation terminal."
"""

from __future__ import annotations

import asyncio
import sys

from x1_common import PINNED, Report, load_validator

from vitruvyan_motus import GraphSpec, Runtime, State

validate = load_validator()

SPEC_DOC = {
    "schema_version": "1.0.0",
    "name": "cancelscope",
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
SPEC = GraphSpec.from_dict(dict(SPEC_DOC))

GATE: asyncio.Event | None = None


def build(nodes=None):
    return Runtime(
        SPEC, nodes or {"a": lambda s: s, "b": lambda s: s, "c": lambda s: s}, **PINNED
    )


async def gated(state):
    assert GATE is not None
    await GATE.wait()
    return state


async def tick(state):
    await asyncio.sleep(0)
    return state


async def main() -> int:
    global GATE
    report = Report("x1b_cancel_scope -- run-scoped cancel and the aclose guard")

    # ================= A. the identity predicate ==========================
    # NB: comparing id() of DEAD lists proves nothing -- CPython reuses
    # addresses. The predicate is only ever evaluated by a closure that holds a
    # strong reference to its own trace_ref, so the correct test keeps every
    # driver (and therefore every trace_ref) alive.
    rt = build()
    kept, refs = [], []
    for i in range(50):
        d = rt.stream(State.empty(f"r{i}"), run_id=f"r{i}")
        kept.append(d)                       # keep the closure, keep the list
        refs.append(rt._trace_ref)
        for _ in d:
            pass
    report.record(
        "each live run holds a distinct _trace_ref object "
        "(identity is a sound run identity while any driver survives)",
        len({id(r) for r in refs}) == len(refs),
        f"{len({id(r) for r in refs})} distinct of {len(refs)} (all kept alive)",
    )
    report.record(
        "every stale driver among those 50 refuses to cancel",
        all(d._cancel("stale") is False for d in kept[:-1]),
        "",
    )

    # a stale SYNC driver must not cancel a later run
    rt = build()
    stale = rt.stream(State.empty("first"), run_id="first")
    next(stale)                                  # leave run 1 in flight
    stale_ref = rt._trace_ref
    for _ in stale:                              # drain to the terminal
        pass
    second = rt.stream(State.empty("second"), run_id="second")
    next(second)
    bound = stale._cancel("stale cancel")
    report.record(
        "a stale StreamDriver's cancel refuses (returns False) during a later run",
        bound is False and rt._cancel_reason is None,
        f"bound={bound} reason={rt._cancel_reason!r}",
    )
    for _ in second:
        pass
    report.record(
        "…and that later run still completes normally",
        second.trace.records[-1]["kind"] == "run_completed",
        second.trace.records[-1]["kind"],
    )

    # a stale ASYNC driver must not cancel a later run (the RA-001 recurrence)
    rt = build({"a": tick, "b": tick, "c": tick})
    astale = rt.astream(State.empty("first"), run_id="first")
    await astale.__anext__()
    async for _ in astale:
        pass
    asecond = rt.astream(State.empty("second"), run_id="second")
    await asecond.__anext__()
    abound = astale._cancel("stale cancel")
    report.record(
        "a stale AsyncStreamDriver's cancel refuses during a later run",
        abound is False and rt._cancel_reason is None,
        f"bound={abound} reason={rt._cancel_reason!r}",
    )
    kinds = []
    async for rec in asecond:
        kinds.append(rec["kind"])
    report.record(
        "…and that later async run still completes normally",
        kinds and kinds[-1] == "run_completed", str(kinds[-1:]),
    )

    # a failed _start must not make an older driver's cancel bind to nothing odd
    rt = build()
    d1 = rt.stream(State.empty("live"), run_id="live")
    for _ in d1:
        pass
    ref_before = rt._trace_ref
    failed = None
    try:
        # run_id validation (runtime.py:481) happens BEFORE _trace_ref is
        # reassigned (runtime.py:519), so this leaves _trace_ref pointing at
        # the previous run's list.
        rt.stream(State.empty("bad"), run_id="x" * 201)
    except BaseException as exc:  # noqa: BLE001
        failed = type(exc).__name__
    stale_bound = d1._cancel("late")
    report.record(
        "a _start that raises leaves _trace_ref stale, and the old driver's "
        "cancel is still refused by the second gate (run already started)",
        failed == "ValueError" and rt._trace_ref is ref_before
        and stale_bound is False and rt._running is False,
        f"failed={failed} stale_ref={rt._trace_ref is ref_before} "
        f"cancel={stale_bound} running={rt._running}",
    )
    probe = build()
    probe.stream(State.empty("empty-id"), run_id="")
    print("    ~ observation: run_id='' is silently replaced by a generated uuid "
          f"(runtime.py:480 `run_id or kernel_uuid()`) -> "
          f"{probe.trace.run['run_id']!r}  [PRE-EXISTING, unrelated to this patch]")

    # ================= B. cancel MUST still bind on a live run =============
    rt = build()
    d = rt.stream(State.empty("livecancel"), run_id="lc")
    next(d)
    next(d)
    d.close("consumer stopped")
    report.record(
        "StreamDriver.close() on a live run still cancels it (ADR-008 §1)",
        d.trace.records[-1]["kind"] == "run_cancelled"
        and d.trace.records[-1]["reason"] == "consumer stopped",
        str([r["kind"] for r in d.trace.records]),
    )
    report.record(
        "…and that cancellation trace is contract-valid in both encodings",
        validate.validate_trace(d.trace.to_dict(), spec=SPEC_DOC) == []
        and validate.validate_jsonl(d.trace.to_jsonl(), spec=SPEC_DOC)[0] == [],
        str(validate.validate_trace(d.trace.to_dict(), spec=SPEC_DOC)[:1]),
    )

    rt = build({"a": tick, "b": tick, "c": tick})
    ad = rt.astream(State.empty("livecancel"), run_id="lc")
    await ad.__anext__()
    await ad.__anext__()
    await ad.aclose("consumer stopped")
    report.record(
        "AsyncStreamDriver.aclose() on a live, unread run still cancels it",
        ad.trace.records[-1]["kind"] == "run_cancelled"
        and ad._closed is True and rt._running is False,
        f"{[r['kind'] for r in ad.trace.records]} closed={ad._closed}",
    )

    # context-manager early exit must cancel
    rt = build()
    with rt.stream(State.empty("brk"), run_id="brk") as d:
        for _ in d:
            break
    report.record(
        "`with stream(): break` cancels on exit",
        d.trace.records[-1]["kind"] == "run_cancelled",
        str([r["kind"] for r in d.trace.records]),
    )
    rt = build({"a": tick, "b": tick, "c": tick})
    async with rt.astream(State.empty("brk"), run_id="brk") as ad:
        async for _ in ad:
            break
    report.record(
        "`async with astream(): break` cancels on exit",
        ad.trace.records[-1]["kind"] == "run_cancelled",
        str([r["kind"] for r in ad.trace.records]),
    )

    # ================= C. ADR-008 §1 non-regression ========================
    rt = build()
    with rt.stream(State.empty("exh"), run_id="e1") as d:
        for _ in d:
            pass
    report.record(
        "an exhausted stream()'s context exit cannot cancel a later run",
        rt.run(State.empty("after"), run_id="a1").status == "completed", "",
    )
    rt = build({"a": tick, "b": tick, "c": tick})
    async with rt.astream(State.empty("exh"), run_id="e2") as ad:
        async for _ in ad:
            pass
    report.record(
        "an exhausted astream()'s context exit cannot cancel a later run",
        (await rt.arun(State.empty("after"), run_id="a2")).status == "completed", "",
    )
    rt = build()
    report.record(
        "Runtime.cancel() public semantics unchanged: queue before first run, "
        "refuse when idle after one",
        rt.cancel("queued") is True
        and rt.run(State.empty("q"), run_id="q").status == "cancelled"
        and rt.cancel("idle") is False,
        "",
    )

    # ================= D. the aclose ag_running early return ===============
    GATE = asyncio.Event()
    rt = build({"a": gated, "b": tick, "c": tick})
    driver = rt.astream(State.empty("graceful"), run_id="g1")
    seen: list[str] = []

    async def consumer():
        async for rec in driver:
            seen.append(rec["kind"])

    task = asyncio.create_task(consumer())
    await asyncio.sleep(0.05)                     # consumer parked in __anext__
    running_flag = getattr(driver._iterator, "ag_running", None)
    await driver.aclose("supervisor stopped")
    closed_right_after = driver._closed
    terminal_right_after = [r["kind"] for r in driver.trace.records][-1]
    GATE.set()
    await task
    report.record(
        "aclose() during an in-flight __anext__ took the early-return path",
        running_flag is True, f"ag_running={running_flag}",
    )
    report.record(
        "aclose() on a live run returns only after the run reached its terminal "
        "(observers.py:301-302: 'aclose on a live run drains only the "
        "cancellation terminal')",
        terminal_right_after == "run_cancelled" and closed_right_after is True,
        f"immediately after aclose(): terminal={terminal_right_after!r} "
        f"_closed={closed_right_after}; after the consumer drained: "
        f"terminal={[r['kind'] for r in driver.trace.records][-1]!r} "
        f"_closed={driver._closed}",
    )
    report.record(
        "the reading consumer does drive it to run_cancelled and release the Runtime",
        [r["kind"] for r in driver.trace.records][-1] == "run_cancelled"
        and driver._closed is True and rt._running is False,
        f"{seen} closed={driver._closed} running={rt._running}",
    )
    report.record(
        "…and that trace is contract-valid",
        validate.validate_trace(driver.trace.to_dict(), spec=SPEC_DOC) == [],
        str(validate.validate_trace(driver.trace.to_dict(), spec=SPEC_DOC)[:1]),
    )

    # D2: supervisor closes, then the reading consumer is cancelled -> orphan?
    GATE = asyncio.Event()
    rt = build({"a": gated, "b": tick, "c": tick})
    driver2 = rt.astream(State.empty("orphan"), run_id="g2")

    async def consumer2():
        async for _ in driver2:
            pass

    task2 = asyncio.create_task(consumer2())
    await asyncio.sleep(0.05)
    await driver2.aclose("supervisor stopped")
    task2.cancel()
    try:
        await task2
    except BaseException:  # noqa: BLE001
        pass
    GATE.set()
    for _ in range(30):
        await asyncio.sleep(0)
    doc2 = driver2.trace.to_dict()
    report.record(
        "aclose() + consumer cancelled: the Runtime is still released "
        "(no wedge)",
        rt._running is False and driver2._closed is True,
        f"running={rt._running} closed={driver2._closed}",
    )
    report.record(
        "aclose() + consumer cancelled: the supervisor's cancellation REASON "
        "survives in the trace (a run_cancelled record)",
        driver2.trace.records[-1]["kind"] == "run_cancelled",
        f"kinds={[r['kind'] for r in driver2.trace.records]} -- the bound "
        "cancellation was never driven to a terminal, so 'supervisor stopped' "
        "is nowhere in the evidence",
    )
    report.record(
        "…the truncated trace is still valid EVIDENCE (expect_complete=False)",
        validate.validate_trace(doc2, spec=SPEC_DOC, expect_complete=False) == [],
        str(validate.validate_trace(doc2, spec=SPEC_DOC, expect_complete=False)[:1]),
    )

    # D3: two concurrent aclose() calls
    GATE = asyncio.Event()
    rt = build({"a": gated, "b": tick, "c": tick})
    driver3 = rt.astream(State.empty("double"), run_id="g3")
    await driver3.__anext__()
    GATE.set()
    results = await asyncio.gather(
        driver3.aclose("one"), driver3.aclose("two"), return_exceptions=True
    )
    report.record(
        "two concurrent aclose() calls neither raise nor wedge the run",
        all(not isinstance(r, BaseException) for r in results)
        and driver3._closed is True and rt._running is False,
        f"{results} closed={driver3._closed} running={rt._running}",
    )

    # D4: aclose() from inside the consumer's own `async for` body
    GATE = asyncio.Event()
    GATE.set()
    rt = build({"a": tick, "b": tick, "c": tick})
    driver4 = rt.astream(State.empty("selfclose"), run_id="g4")
    inner_err = None
    try:
        async for _ in driver4:
            await driver4.aclose("closed from inside the loop")
            break
    except BaseException as exc:  # noqa: BLE001
        inner_err = f"{type(exc).__name__}: {exc}"
    report.record(
        "aclose() from inside the consumer's own loop body is safe",
        inner_err is None and driver4._closed is True and rt._running is False,
        f"err={inner_err} closed={driver4._closed} running={rt._running} "
        f"kinds={[r['kind'] for r in driver4.trace.records]}",
    )

    return report.emit()


if __name__ == "__main__":
    sys.exit(1 if asyncio.run(main()) else 0)
