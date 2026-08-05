"""x3_04 — the replay surface has no asynchronous driver.

`Runtime.arun()` / `astream()` accept `async def` nodes and produce a trace
that the contract validator accepts. But `ReplayEngine.verify()` calls the
node synchronously and `ReplayEngine.resume()` goes through
`_execute_resume` -> `_drive(machine, _invoke_sync)`. Neither can await.

The failure mode is the dangerous one: `verify()` does not say "this engine
cannot drive an async node", it raises `ReplayMismatch(..., "outcome")` —
the contract's signal that *the node's behaviour changed*. It accuses
unchanged code of divergence, and leaks an un-awaited coroutine per node.

Run: python .attack/x3_04_async_replay_verify.py
"""

from __future__ import annotations

import asyncio
import json
import sys
import warnings
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from vitruvyan_motus import (  # noqa: E402
    Fact,
    GraphSpec,
    ReplayEngine,
    ReplayMismatch,
    ReplayStatus,
    Runtime,
    State,
    TraceBundle,
)

NOW = datetime(2026, 8, 5, tzinfo=timezone.utc)

SPEC = GraphSpec.from_dict(
    {
        "schema_version": "1.0.0",
        "name": "x3-async-replay",
        "version": "1.0.0",
        "entry": "a",
        "nodes": [
            {"name": "a", "effect_class": "pure"},
            {"name": "b", "effect_class": "pure"},
        ],
        "transitions": {"a": {"kind": "next", "to": "b"}, "b": {"kind": "terminal"}},
    }
)


def a_sync(state: State) -> State:
    return state.with_fact(Fact("a", "done", "x3", NOW))


def b_sync(state: State) -> State:
    return state.with_fact(Fact("b", "done", "x3", NOW))


async def a_async(state: State) -> State:
    await asyncio.sleep(0)
    return state.with_fact(Fact("a", "done", "x3", NOW))


async def b_async(state: State) -> State:
    await asyncio.sleep(0)
    return state.with_fact(Fact("b", "done", "x3", NOW))


SYNC = {"a": a_sync, "b": b_sync}
ASYNC = {"a": a_async, "b": b_async}
MIXED = {"a": a_sync, "b": b_async}


def verify_report(label: str, nodes, trace) -> None:
    bundle = TraceBundle(spec=SPEC, trace=trace)
    engine = ReplayEngine(bundle)
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        try:
            result = engine.verify(nodes)
            outcome = f"OK, verified {len(result.verified)} pure node(s)"
        except ReplayMismatch as exc:
            outcome = f"ReplayMismatch: {exc}"
        except BaseException as exc:  # noqa: BLE001
            outcome = f"{type(exc).__name__}: {exc}"
        leaked = [str(w.message) for w in caught if "never awaited" in str(w.message)]
    print(f"  {label:<44} {outcome}")
    if leaked:
        print(f"  {'':<44} leaked coroutines: {leaked}")


def main() -> int:
    print("Trace produced SYNCHRONOUSLY, replay declared full:")
    sync_trace = Runtime(SPEC, SYNC).run(
        State.empty("baseline"), replay=ReplayStatus.declared("full")
    ).trace
    verify_report("verify(sync nodes)", SYNC, sync_trace)

    print("\nTrace produced with arun(), all nodes async, replay declared full:")
    async_trace = asyncio.run(
        Runtime(SPEC, ASYNC).arun(
            State.empty("baseline"), replay=ReplayStatus.declared("full")
        )
    ).trace
    doc = async_trace.to_dict()
    print(f"  header replay declaration: {json.dumps(doc['run']['replay'])}")
    print(f"  terminal record: {doc['records'][-1]['kind']}, "
          f"{len(doc['records'])} records")
    verify_report("verify(the very nodes that produced it)", ASYNC, async_trace)

    print("\nTrace produced with arun(), one sync node and one async node:")
    mixed_trace = asyncio.run(
        Runtime(SPEC, MIXED).arun(
            State.empty("baseline"), replay=ReplayStatus.declared("full")
        )
    ).trace
    verify_report("verify(the same mixed registry)", MIXED, mixed_trace)

    print("\nresume() of an async trace (the segment driver is _invoke_sync):")
    # A non-terminal trace to resume from: cancel after the first transition.
    async def half():
        runtime = Runtime(SPEC, ASYNC)
        driver = runtime.astream(
            State.empty("half"), replay=ReplayStatus.declared("full")
        )
        seen = []
        async for record in driver:
            seen.append(record["kind"])
            if record["kind"] == "transition":
                break
        await asyncio.sleep(0)
        await asyncio.sleep(0)
        return runtime._trace, seen

    partial_trace, kinds = asyncio.run(half())
    print(f"  partial trace kinds: {kinds}")
    resume_runtime = Runtime(SPEC, ASYNC)
    engine = ReplayEngine(TraceBundle(spec=SPEC, trace=partial_trace))
    try:
        segment = engine.resume(resume_runtime, run_id="x3-resumed-segment")
        seg = segment.trace.to_dict()
        print(f"  resume() -> terminal={seg['records'][-1]['kind']}")
        failures = [r for r in seg["records"] if r.get("outcome") == "raised"]
        for f in failures:
            print(f"  raised: {json.dumps(f.get('error'), sort_keys=True)}")
    except BaseException as exc:  # noqa: BLE001
        print(f"  resume() RAISED {type(exc).__name__}: {exc}")

    print("\nSurface inventory:")
    engine_api = sorted(
        n for n in dir(ReplayEngine) if not n.startswith("_")
    )
    print(f"  ReplayEngine public: {engine_api}")
    runtime_api = sorted(
        n for n in dir(Runtime)
        if not n.startswith("_") and callable(getattr(Runtime, n, None))
    )
    print(f"  Runtime public callables: {runtime_api}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
