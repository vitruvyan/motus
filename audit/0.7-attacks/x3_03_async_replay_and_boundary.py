"""x3_03 — what the inversion moved, and what async execution cannot reach.

A. Replay/resume have no asynchronous driver. `ReplayEngine.verify()` and
   `.resume()` call `_drive(machine, _invoke_sync)`, so a trace produced by
   `arun()` over `async def` nodes cannot be verified or resumed with the very
   nodes that produced it. Test whether that is true and what it reports.

B. The inversion moved three expressions out of the node-failure try block:
       node      = self._nodes[current_node]
       uses_ctx  = self._uses_context[current_node]
       ctx       = self._control.node_context
   In v0.6.1 all three were inside `try: ... except Exception as exc: error = exc`,
   so a raise there became a recorded `node_failed`. Now it escapes the
   generator with `attempt_started` already emitted and unclosed. Probe
   whether any of them is reachable.

C. A synchronous node that returns an awaitable is now a captured TypeError
   rather than an AttributeError. Check the recorded cause and that the
   coroutine is closed (no "never awaited" RuntimeWarning).

D. An abandoned stream's persisted trace: does it carry a terminal record, and
   does the sync path differ from the async path?
"""

from __future__ import annotations

import asyncio
import io
import json
import sys
import warnings
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from vitruvyan_motus import (  # noqa: E402
    Decision,
    Fact,
    GraphSpec,
    InMemoryTraceSink,
    Policy,
    ReplayEngine,
    Runtime,
    State,
    TraceBundle,
)

NOW = datetime(2026, 8, 5, tzinfo=timezone.utc)

LINEAR = GraphSpec.from_dict(
    {
        "schema_version": "1.0.0",
        "name": "x3-replay",
        "version": "1.0.0",
        "entry": "a",
        "nodes": [
            {"name": "a", "effect_class": "pure"},
            {"name": "b", "effect_class": "pure"},
        ],
        "transitions": {"a": {"kind": "next", "to": "b"}, "b": {"kind": "terminal"}},
    }
)


async def a_async(state: State) -> State:
    await asyncio.sleep(0)
    return state.with_fact(Fact("a", "done", "x3", NOW))


async def b_async(state: State) -> State:
    await asyncio.sleep(0)
    return state.with_fact(Fact("b", "done", "x3", NOW))


ASYNC_NODES = {"a": a_async, "b": b_async}


def section(title: str) -> None:
    print(f"\n=== {title} ===")


# ------------------------------------------------------------------- A ------
def probe_replay_of_an_async_trace() -> None:
    section("A. replay / resume of a trace produced by arun() over async nodes")
    runtime = Runtime(LINEAR, ASYNC_NODES)
    result = asyncio.run(runtime.arun(State.empty("async-origin")))
    doc = result.trace.to_dict()
    print(f"  arun produced {len(doc['records'])} records, "
          f"terminal={doc['records'][-1]['kind']}")

    bundle = TraceBundle(spec=LINEAR, trace=result.trace)
    engine = ReplayEngine(bundle)

    print(f"  ReplayEngine has: verify={hasattr(engine,'verify')} "
          f"averify={hasattr(engine,'averify')} resume={hasattr(engine,'resume')} "
          f"aresume={hasattr(engine,'aresume')}")

    try:
        verdict = engine.verify(ASYNC_NODES)
        print(f"  verify(async nodes) -> status={verdict.status} "
              f"mismatch={getattr(verdict,'mismatch',None)}")
    except BaseException as exc:  # noqa: BLE001
        print(f"  verify(async nodes) RAISED {type(exc).__name__}: {exc}")

    try:
        playback = engine.playback()
        print(f"  playback() -> status={playback.status}")
    except BaseException as exc:  # noqa: BLE001
        print(f"  playback() RAISED {type(exc).__name__}: {exc}")

    try:
        resumed = engine.resume(ASYNC_NODES, from_node="b", run_id="x3-resume")
        print(f"  resume(async nodes) -> {resumed}")
    except BaseException as exc:  # noqa: BLE001
        print(f"  resume(async nodes) RAISED {type(exc).__name__}: {exc}")


# ------------------------------------------------------------------- B ------
def probe_boundary_moves() -> None:
    section("B. expressions moved out of the node-failure try block")

    class HostileRegistry(dict):
        """A Mapping whose __getitem__ raises for a declared node."""

        def __getitem__(self, key):
            if key == "b":
                raise KeyError("registry blew up on lookup")
            return super().__getitem__(key)

    ok = {"a": lambda s: s.with_fact(Fact("a", "d", "x3", NOW)),
          "b": lambda s: s.with_fact(Fact("b", "d", "x3", NOW))}

    # Does Runtime.__init__ copy the registry (making a hostile mapping moot)?
    try:
        runtime = Runtime(LINEAR, HostileRegistry(ok))
        print(f"  Runtime accepted a hostile mapping; internal type="
              f"{type(runtime._nodes).__name__}")
        try:
            res = runtime.run(State.empty("hostile"))
            kinds = [r["kind"] for r in res.trace.to_dict()["records"]]
            print(f"  run() -> {kinds[-1]} (records={len(kinds)})")
        except BaseException as exc:  # noqa: BLE001
            print(f"  run() RAISED {type(exc).__name__}: {exc}")
    except BaseException as exc:  # noqa: BLE001
        print(f"  Runtime(...) rejected the hostile mapping: "
              f"{type(exc).__name__}: {exc}")

    # Is `node_context` a property that can raise mid-run?
    import inspect as _inspect
    from vitruvyan_motus.context import _RunController

    attr = getattr(type(_RunController(replay=None)), "node_context", None)
    print(f"  _RunController.node_context is a "
          f"{type(attr).__name__ if attr is not None else 'plain attribute'}")
    if isinstance(attr, property):
        src = _inspect.getsource(attr.fget)
        print("  node_context body:")
        for line in src.splitlines():
            print(f"    {line}")


# ------------------------------------------------------------------- C ------
def probe_async_node_under_sync_driver() -> None:
    section("C. an async node driven by run(): cause + coroutine hygiene")
    runtime = Runtime(LINEAR, {"a": a_async, "b": b_async})
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        result = runtime.run(State.empty("wrong-driver"))
        doc = result.trace.to_dict()
    failed = [r for r in doc["records"] if r["kind"] == "node_failed"]
    print(f"  terminal={doc['records'][-1]['kind']}  node_failed={len(failed)}")
    if failed:
        print(f"  cause={json.dumps(failed[0].get('cause'), sort_keys=True)}")
    never_awaited = [w for w in caught if "never awaited" in str(w.message)]
    print(f"  'coroutine was never awaited' warnings: {len(never_awaited)}")

    # And the reverse: a plain sync node under arun().
    runtime2 = Runtime(LINEAR, {
        "a": lambda s: s.with_fact(Fact("a", "d", "x3", NOW)),
        "b": lambda s: s.with_fact(Fact("b", "d", "x3", NOW)),
    })
    doc2 = asyncio.run(runtime2.arun(State.empty("sync-under-async"))).trace.to_dict()
    print(f"  sync nodes under arun -> {doc2['records'][-1]['kind']} "
          f"({len(doc2['records'])} records)")


# ------------------------------------------------------------------- D ------
def probe_abandoned_stream_traces() -> None:
    section("D. what an abandoned stream leaves in the sink")

    sync_sink = InMemoryTraceSink()
    rt = Runtime(LINEAR, {
        "a": lambda s: s.with_fact(Fact("a", "d", "x3", NOW)),
        "b": lambda s: s.with_fact(Fact("b", "d", "x3", NOW)),
    }, sink=sync_sink)
    for _ in rt.stream(State.empty("abandoned-sync")):
        break
    written = sync_sink.records if hasattr(sync_sink, "records") else None
    print(f"  sync  abandoned: sink holds {len(written) if written is not None else '?'} "
          f"records, kinds={[r['kind'] for r in (written or [])]}")

    async def go():
        async_sink = InMemoryTraceSink()
        rt2 = Runtime(LINEAR, ASYNC_NODES, sink=async_sink)
        async for _ in rt2.astream(State.empty("abandoned-async")):
            break
        await asyncio.sleep(0)
        await asyncio.sleep(0)
        w = async_sink.records if hasattr(async_sink, "records") else None
        print(f"  async abandoned: sink holds {len(w) if w is not None else '?'} "
              f"records, kinds={[r['kind'] for r in (w or [])]}")

    asyncio.run(go())


# ------------------------------------------------------------------- E ------
def probe_minimal_wedge() -> None:
    section("E. minimal reproduction of the abandonment asymmetry")

    def sync_case():
        rt = Runtime(LINEAR, {
            "a": lambda s: s.with_fact(Fact("a", "d", "x3", NOW)),
            "b": lambda s: s.with_fact(Fact("b", "d", "x3", NOW)),
        })
        for _ in rt.stream(State.empty("one")):
            break
        try:
            rt.run(State.empty("two"))
            return "OK  — sync: the next run started"
        except RuntimeError as exc:
            return f"FAIL — sync: {exc}"

    async def async_case():
        rt = Runtime(LINEAR, ASYNC_NODES)
        async for _ in rt.astream(State.empty("one")):
            break
        try:
            await rt.arun(State.empty("two"))
            return "OK  — async: the next run started"
        except RuntimeError as exc:
            return f"FAIL — async: {exc}"

    print("  " + sync_case())
    print("  " + asyncio.run(async_case()))


if __name__ == "__main__":
    probe_replay_of_an_async_trace()
    probe_boundary_moves()
    probe_async_node_under_sync_driver()
    probe_abandoned_stream_traces()
    probe_minimal_wedge()
