"""x3b_02 — can a driver's cancellation still reach a run it does not own?

`Runtime._run_scoped_cancel` compares `self._trace_ref is trace_ref`: each
`_start` installs a fresh list, so list identity is run identity. This walks
every window in which a stale driver could fire — before the next run, during
the next run, across a resume segment, on both drivers — and checks that the
later run reaches its own terminal untouched.

`Runtime.cancel` already refused an idle cancellation once `_has_started` is
True (ADR-008 §1), so the new guard only has to close the "a *later* run is
live" window. Both are exercised, because a regression in either reopens the
same defect.

Run: python .attack/x3b_02_cancel_scope.py
"""

from __future__ import annotations

import asyncio
import gc
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from vitruvyan_motus import (  # noqa: E402
    Fact,
    GraphSpec,
    ReplayEngine,
    Runtime,
    State,
    TraceBundle,
)

NOW = datetime(2026, 8, 5, tzinfo=timezone.utc)

SPEC = GraphSpec.from_dict({
    "schema_version": "1.0.0", "name": "x3b-cancel", "version": "1.0.0",
    "entry": "a",
    "nodes": [{"name": "a", "effect_class": "pure"},
              {"name": "b", "effect_class": "pure"},
              {"name": "c", "effect_class": "pure"}],
    "transitions": {"a": {"kind": "next", "to": "b"},
                    "b": {"kind": "next", "to": "c"},
                    "c": {"kind": "terminal"}},
})


def s(name):
    return lambda st: st.with_fact(Fact(name, "d", "x3b", NOW))


def a(name):
    async def node(st):
        await asyncio.sleep(0)
        return st.with_fact(Fact(name, "d", "x3b", NOW))
    return node


SYNC = {k: s(k) for k in ("a", "b", "c")}
ASYNC = {k: a(k) for k in ("a", "b", "c")}

ROWS: list[tuple[str, str, str]] = []


def row(name, verdict, detail=""):
    ROWS.append((name, verdict, detail))


def terminal(trace) -> str:
    records = trace.to_dict()["records"]
    return records[-1]["kind"] if records else "(none)"


# ------------------------------------------------------------------ sync ----
def sync_no_reachable_stale_window() -> None:
    """Enumerate every way a synchronous run can end, and check whether the
    driver can survive it with ``_closed`` still False.

    `StreamDriver._closed` is set by `__next__` on ANY exception (exhaustion
    included) and by `close()`. The only other way the run can end is the
    generator chain being collected — which requires the driver itself to be
    unreachable. So a *live* sync driver whose run is already over should not
    exist, and `_run_scoped_cancel`'s synchronous half guards a state that
    cannot be reached from the public API.
    """
    reachable = []

    rt = Runtime(SPEC, SYNC)
    d = rt.stream(State.empty("exhaust"))
    for _ in d:
        pass
    if not d._closed and not rt._running:
        reachable.append("exhaustion")

    rt = Runtime(SPEC, {"a": s("a"), "b": lambda st: 1 / 0, "c": s("c")})
    d = rt.stream(State.empty("fail"))
    try:
        for _ in d:
            pass
    except BaseException:  # noqa: BLE001
        pass
    if not d._closed and not rt._running:
        reachable.append("node failure")

    rt = Runtime(SPEC, SYNC)
    d = rt.stream(State.empty("closed"))
    next(d)
    d.close("stopped")
    if not d._closed and not rt._running:
        reachable.append("close()")

    row("sync: no reachable window where a live driver outlives its run",
        "PASS" if not reachable else "FAIL",
        f"reachable stale states: {reachable or 'none'}")


def sync_stale_across_a_resume_segment() -> None:
    """A resume segment is a new run with a new _trace_ref: a driver from an
    earlier, properly-closed run must not touch it."""
    rt = Runtime(SPEC, SYNC)
    for record in rt.stream(State.empty("one")):
        if record["kind"] == "transition":
            break
    gc.collect()
    source = rt._trace

    fresh = Runtime(SPEC, SYNC)
    decoy = fresh.stream(State.empty("decoy"))
    next(decoy)
    decoy.close("decoy stopped")          # run 1 of `fresh` is cancelled

    engine = ReplayEngine(TraceBundle(spec=SPEC, trace=source))
    segment = engine.resume(fresh, run_id="x3b-seg")
    decoy.close("stale second close")      # no-op: already closed
    row("sync: a closed driver cannot touch a later resume segment",
        "PASS" if terminal(segment.trace) == "run_completed" else "FAIL",
        f"segment terminal={terminal(segment.trace)}")


def sync_own_run_still_cancellable() -> None:
    """The guard must not break the legitimate case."""
    rt = Runtime(SPEC, SYNC)
    d = rt.stream(State.empty("one"))
    next(d)
    next(d)
    d.close("consumer stopped")
    row("sync: a live driver can still cancel its OWN run",
        "PASS" if terminal(d.trace) == "run_cancelled" else "FAIL",
        f"terminal={terminal(d.trace)}")


def sync_pre_run_close_still_cancels() -> None:
    rt = Runtime(SPEC, SYNC)
    d = rt.stream(State.empty("one"))
    d.close("closed before advancing")
    row("sync: close() before advancing still cancels its own run",
        "PASS" if terminal(d.trace) == "run_cancelled" else "FAIL",
        f"terminal={terminal(d.trace)}")


# ----------------------------------------------------------------- async ----
async def async_stale_then_next_run() -> None:
    rt = Runtime(SPEC, ASYNC)
    d = rt.astream(State.empty("one"))
    await d.__anext__()
    await d._iterator.aclose()
    gc.collect()
    assert d._closed is False
    await d.aclose("stale context exit")
    result = await rt.arun(State.empty("two"))
    row("async: stale aclose() BEFORE the next run",
        "PASS" if terminal(result.trace) == "run_completed" else "FAIL",
        f"next run terminal={terminal(result.trace)}, "
        f"pending={rt._pending_cancel_reason!r}")


async def async_stale_during_next_run() -> None:
    rt = Runtime(SPEC, ASYNC)
    stale = rt.astream(State.empty("one"))
    await stale.__anext__()
    await stale._iterator.aclose()
    gc.collect()
    assert stale._closed is False

    live = rt.astream(State.empty("two"))
    await live.__anext__()
    await stale.aclose("stale context exit")
    kinds = [record["kind"] async for record in live]
    row("async: stale aclose() WHILE the next run is live",
        "PASS" if kinds[-1] == "run_completed" else "FAIL",
        f"live run terminal={kinds[-1]}")


async def async_stale_then_sync_run() -> None:
    """Cross-driver: a stale ASYNC driver against a later SYNC run."""
    # All nodes are synchronous so both drivers can run them; only the
    # stale driver is the asynchronous one.
    rt = Runtime(SPEC, SYNC)
    stale = rt.astream(State.empty("one"))
    await stale.__anext__()
    await stale._iterator.aclose()
    gc.collect()
    await asyncio.sleep(0)
    live = rt.stream(State.empty("two"))
    next(live)
    await stale.aclose("stale context exit")
    kinds = [record["kind"] for record in live]
    row("cross: stale ASYNC driver vs a live SYNC run",
        "PASS" if kinds[-1] == "run_completed" else "FAIL",
        f"live run terminal={kinds[-1]}")


async def async_stale_then_resume_segment() -> None:
    """The async stale driver against a later *resume segment* run."""
    rt = Runtime(SPEC, SYNC)
    for record in rt.stream(State.empty("source")):
        if record["kind"] == "transition":
            break
    gc.collect()
    source = rt._trace

    fresh = Runtime(SPEC, SYNC)
    stale = fresh.astream(State.empty("decoy"))
    await stale.__anext__()
    await stale._iterator.aclose()
    gc.collect()
    assert stale._closed is False

    engine = ReplayEngine(TraceBundle(spec=SPEC, trace=source))
    segment = engine.resume(fresh, run_id="x3b-seg-async")
    await stale.aclose("stale context exit")
    row("async: stale aclose() vs a later resume segment",
        "PASS" if terminal(segment.trace) == "run_completed" else "FAIL",
        f"segment terminal={terminal(segment.trace)}")


async def async_own_run_still_cancellable() -> None:
    rt = Runtime(SPEC, ASYNC)
    d = rt.astream(State.empty("one"))
    await d.__anext__()
    await d.__anext__()
    await d.aclose("consumer stopped")
    row("async: a live driver can still cancel its OWN run",
        "PASS" if terminal(d.trace) == "run_cancelled" else "FAIL",
        f"terminal={terminal(d.trace)}")


def main() -> int:
    sync_no_reachable_stale_window()
    sync_stale_across_a_resume_segment()
    sync_own_run_still_cancellable()
    sync_pre_run_close_still_cancels()
    asyncio.run(async_stale_then_next_run())
    asyncio.run(async_stale_during_next_run())
    asyncio.run(async_stale_then_sync_run())
    asyncio.run(async_stale_then_resume_segment())
    asyncio.run(async_own_run_still_cancellable())

    width = max(len(n) for n, _, _ in ROWS)
    for name, verdict, detail in ROWS:
        print(f"  {verdict:<4}  {name:<{width}}  {detail}")
    failures = sum(1 for _, v, _ in ROWS if v == "FAIL")
    print(f"\n{len(ROWS)} checks, {failures} FAIL")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
