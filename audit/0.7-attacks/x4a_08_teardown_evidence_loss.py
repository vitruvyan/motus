"""x4a-08 — buffered evidence is DISCARDED, not flushed, at run teardown.

``_ObservationHub.close()`` latches ``_closed`` and cancels the pending Timer.
It never flushes.  Every *terminal* record force-flushes, so the ordinary
paths are safe.  But three ordinary, non-crash teardowns reach
``_managed_execute``'s ``finally`` with no terminal at all:

  * a node raising a ``BaseException`` — ``KeyboardInterrupt`` is the shipped
    example (``_invoke_sync`` captures only ``Exception``);
  * ``asyncio.CancelledError`` into ``arun()`` — ``_invoke_async``'s docstring
    says so explicitly ("tears the run down rather than becoming a raised
    attempt");
  * a ``stream()`` / ``astream()`` driver abandoned mid-run.

In all three the process is alive, the caller still holds a ``Trace`` naming
every record, and ``_hub._buffer`` still holds them — and they are never
handed to the sink.  ``close()`` had them in memory and dropped them.

Controls that must NOT lose anything: the synchronous profile, an explicit
``driver.close()``, a listener-driven cancel, a node raising ``Exception``
(whose ``raised`` transition force-flushes per guarantees.md invariant II).

Run: .venv/bin/python .attack/x4a_08_teardown_evidence_loss.py
"""

from __future__ import annotations

import asyncio
import gc
import sys

sys.path.insert(0, "/home/vitruvyan/motus/.attack")
sys.path.insert(0, "/home/vitruvyan/motus/contract")

import validate as contract_validate  # noqa: E402
from vitruvyan_motus import (  # noqa: E402
    DurabilityProfile, Fact, GraphSpec, Runtime, State,
)
from x4a_common import Report, RecordingSink, chain, chain_doc  # noqa: E402

# The shipped Runtime defaults.
CHUNK = 64
INTERVAL = 1000
N = 21


def nodes(kind: str, at: str = "n15"):
    def make(i: int):
        name = f"n{i}"

        async def anode(state: State) -> State:
            if name == at and kind == "async_cancel":
                await asyncio.sleep(5)
            if kind == "async_plain":
                await asyncio.sleep(0)
            return state.with_fact(
                Fact(key=f"k{i}", value=i, source=name, ts="2026-08-05T00:00:00Z"))

        def snode(state: State) -> State:
            if name == at and kind == "base_exc":
                raise KeyboardInterrupt("operator pressed ctrl-c")
            if name == at and kind == "exc":
                raise ValueError("ordinary node failure")
            return state.with_fact(
                Fact(key=f"k{i}", value=i, source=name, ts="2026-08-05T00:00:00Z"))

        return anode if kind.startswith("async") else snode

    return {f"n{i}": make(i) for i in range(N)}


def make_rt(kind: str, profile=DurabilityProfile.BUFFERED, **kw) -> tuple[Runtime, RecordingSink]:
    sink = RecordingSink()
    rt = Runtime(
        chain(N), nodes(kind), durability_profile=profile, sink=sink,
        chunk_records=CHUNK, flush_interval_ms=INTERVAL, **kw,
    )
    return rt, sink


def gap(rt, sink: RecordingSink) -> dict[str, object]:
    trace = rt.trace
    tseqs = [r["seq"] for r in (trace.records if trace is not None else ())]
    persisted = [r for _t, b in sink.batches for r in b]
    pseqs = [r["seq"] for r in persisted]
    return {
        "in_trace": len(tseqs),
        "in_sink": len(pseqs),
        "dropped": len([s for s in tseqs if s not in set(pseqs)]),
        "stuck_in_buffer": len(rt._hub._buffer) if rt._hub is not None else None,
        "hub_closed": rt._hub._closed if rt._hub is not None else None,
        "sink_opened_run": len(sink.sessions),
        "trace_tail": trace.records[-1]["kind"] if tseqs else None,
    }


def report(rep: Report, label: str, rt, sink, must_lose_nothing: bool = True) -> None:
    info = gap(rt, sink)
    ok = info["dropped"] == 0
    rep.record(label, ok if must_lose_nothing else not ok, str(info))


def main() -> int:
    rep = Report("x4a-08 buffered evidence discarded at teardown")

    # --- 1. sync: KeyboardInterrupt out of a node -------------------------
    rt, sink = make_rt("base_exc")
    try:
        rt.run()
    except KeyboardInterrupt:
        pass
    report(rep, "1/sync-BaseException", rt, sink)

    # --- 2. async: task cancellation into arun() --------------------------
    async def cancel_arun():
        rt, sink = make_rt("async_cancel")
        task = asyncio.ensure_future(rt.arun())
        await asyncio.sleep(0.15)
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
        return rt, sink

    rt, sink = asyncio.run(cancel_arun())
    report(rep, "2/async-task-cancelled", rt, sink)

    # --- 3. sync: abandoned stream() driver -------------------------------
    rt, sink = make_rt("plain")
    driver = rt.stream()
    for i, _rec in enumerate(driver):
        if i >= 30:
            break
    del driver
    gc.collect()
    report(rep, "3/abandoned-stream-driver", rt, sink)

    # --- 4. async: abandoned astream() driver -----------------------------
    async def abandon_astream():
        rt, sink = make_rt("async_plain")
        driver = rt.astream()
        i = 0
        async for _rec in driver:
            i += 1
            if i >= 30:
                break
        del driver
        gc.collect()
        await asyncio.sleep(0.05)
        return rt, sink

    rt, sink = asyncio.run(abandon_astream())
    report(rep, "4/abandoned-astream-driver", rt, sink)

    # --- controls ---------------------------------------------------------
    rt, sink = make_rt("base_exc", profile=DurabilityProfile.SYNCHRONOUS)
    try:
        rt.run()
    except KeyboardInterrupt:
        pass
    report(rep, "C1/synchronous-profile-loses-nothing", rt, sink)

    rt, sink = make_rt("plain")
    driver = rt.stream()
    for i, _rec in enumerate(driver):
        if i >= 30:
            break
    driver.close("explicit")
    report(rep, "C2/explicit-driver-close-loses-nothing", rt, sink)

    rt, sink = make_rt("exc")
    try:
        rt.run()
    except BaseException:  # NodeFailed
        pass
    report(rep, "C3/ordinary-node-failure-loses-nothing", rt, sink)

    # --- what the operator can see afterwards ------------------------------
    rt, sink = make_rt("base_exc")
    try:
        rt.run()
    except KeyboardInterrupt:
        pass
    persisted = [r for _t, b in sink.batches for r in b]
    header = sink.sessions[-1].header if sink.sessions else None
    declared = (header or {}).get("sink")
    doc = {"schema_version": rt.trace.to_dict()["schema_version"],
           "run": header, "records": persisted}
    if persisted:
        v = contract_validate.validate_trace(doc, chain_doc(N), expect_complete=False)
    else:
        v = ["<no records at all: the persisted account is a bare header>"]
    rep.record(
        "5/persisted-account-is-recoverable",
        bool(persisted) and not v,
        f"declared loss window={declared}; sink received {len(persisted)} of "
        f"{len(rt.trace.records)} records; validator says "
        f"{[getattr(x, 'rule', x) for x in v][:4]}",
    )
    return rep.dump()


if __name__ == "__main__":
    raise SystemExit(0 if main() == 0 else 1)
