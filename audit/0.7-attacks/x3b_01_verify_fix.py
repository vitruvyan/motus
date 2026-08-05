"""x3b_01 — F1 end to end, and exactly what capability is still missing.

The fix makes `ReplayEngine.verify` refuse an awaitable return with
`ReplayError` instead of producing a false `ReplayMismatch`. This checks the
fix from every side, then measures what is still unreachable for a graph that
contains `async def` nodes:

  * verify()  — refuses loudly (fixed), but still cannot re-execute
  * resume()  — goes through Runtime._run_from -> _drive(machine, _invoke_sync)
  * _run_from — the segment driver itself
  * the run header's `replay` declaration — honest, or still an overclaim?

Coroutine hygiene is asserted under ``-W error::RuntimeWarning`` semantics:
every warning is captured and any "never awaited" is a failure.

Run: python .attack/x3b_01_verify_fix.py
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
    NodeFailed,
    Policy,
    ReplayEngine,
    ReplayError,
    ReplayMismatch,
    ReplayStatus,
    Runtime,
    State,
    TraceBundle,
)

NOW = datetime(2026, 8, 5, tzinfo=timezone.utc)
ROWS: list[tuple[str, str, str]] = []


def row(check: str, verdict: str, detail: str = "") -> None:
    ROWS.append((check, verdict, detail))


PURE_DOC = {
    "schema_version": "1.0.0", "name": "x3b-pure", "version": "1.0.0",
    "entry": "a",
    "nodes": [{"name": "a", "effect_class": "pure"},
              {"name": "b", "effect_class": "pure"}],
    "transitions": {"a": {"kind": "next", "to": "b"}, "b": {"kind": "terminal"}},
}
PURE = GraphSpec.from_dict(dict(PURE_DOC))

# Same shape, but the async node is NOT pure — verify never re-executes it.
IMPURE_DOC = {
    "schema_version": "1.0.0", "name": "x3b-impure", "version": "1.0.0",
    "entry": "a",
    "nodes": [{"name": "a", "effect_class": "recorded_effect"},
              {"name": "b", "effect_class": "pure"}],
    "transitions": {"a": {"kind": "next", "to": "b"}, "b": {"kind": "terminal"}},
}
IMPURE = GraphSpec.from_dict(dict(IMPURE_DOC))

THREE_DOC = {
    "schema_version": "1.0.0", "name": "x3b-three", "version": "1.0.0",
    "entry": "a",
    "nodes": [{"name": "a", "effect_class": "pure"},
              {"name": "b", "effect_class": "pure"},
              {"name": "c", "effect_class": "pure"}],
    "transitions": {"a": {"kind": "next", "to": "b"},
                    "b": {"kind": "next", "to": "c"},
                    "c": {"kind": "terminal"}},
}
THREE = GraphSpec.from_dict(dict(THREE_DOC))


def a_sync(state: State) -> State:
    return state.with_fact(Fact("a", "done", "x3b", NOW))


def b_sync(state: State) -> State:
    return state.with_fact(Fact("b", "done", "x3b", NOW))


def c_sync(state: State) -> State:
    return state.with_fact(Fact("c", "done", "x3b", NOW))


async def a_async(state: State) -> State:
    await asyncio.sleep(0)
    return state.with_fact(Fact("a", "done", "x3b", NOW))


async def b_async(state: State) -> State:
    await asyncio.sleep(0)
    return state.with_fact(Fact("b", "done", "x3b", NOW))


async def a_async_raises(state: State) -> State:
    await asyncio.sleep(0)
    raise ValueError("planned")


SYNC = {"a": a_sync, "b": b_sync}
ASYNC = {"a": a_async, "b": b_async}
MIXED = {"a": a_sync, "b": b_async}


def caught_verify(nodes, spec, trace):
    """Run verify, returning (exception-type-name, message, leaked-coroutines)."""
    engine = ReplayEngine(TraceBundle(spec=spec, trace=trace))
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        try:
            result = engine.verify(nodes)
            outcome = ("OK", f"verified {len(result.verified)} pure node(s)")
        except BaseException as exc:  # noqa: BLE001
            outcome = (type(exc).__name__, str(exc))
        # force finalisation of anything the call abandoned
        import gc
        gc.collect()
        leaked = [str(w.message) for w in caught if "never awaited" in str(w.message)]
    return outcome[0], outcome[1], leaked


def main() -> int:
    # ---------------------------------------------------------------- fixed --
    async_trace = asyncio.run(
        Runtime(PURE, ASYNC).arun(State.empty("v"),
                                  replay=ReplayStatus.declared("full"))
    ).trace
    kind, message, leaked = caught_verify(ASYNC, PURE, async_trace)
    row("verify(async pure) raises ReplayError",
        "PASS" if kind == "ReplayError" else "FAIL", f"{kind}: {message}")
    row("... and names the reason",
        "PASS" if "asynchronous" in message else "FAIL", message)
    row("... and leaks no coroutine",
        "PASS" if not leaked else "FAIL", str(leaked))
    row("... ReplayError is NOT a ReplayMismatch",
        "PASS" if kind != "ReplayMismatch" else "FAIL",
        f"issubclass(ReplayMismatch, ReplayError)="
        f"{issubclass(ReplayMismatch, ReplayError)} — a caller using "
        f"`except ReplayError` cannot tell them apart without type()")

    mixed_trace = asyncio.run(
        Runtime(PURE, MIXED).arun(State.empty("v"),
                                  replay=ReplayStatus.declared("full"))
    ).trace
    kind, message, leaked = caught_verify(MIXED, PURE, mixed_trace)
    row("verify(mixed sync+async) raises ReplayError",
        "PASS" if kind == "ReplayError" else "FAIL", f"{kind}: {message}")
    row("... and leaks no coroutine", "PASS" if not leaked else "FAIL", str(leaked))

    # an async node whose recorded outcome is `raised` still refuses loudly
    raised_trace = None
    try:
        asyncio.run(Runtime(PURE, {"a": a_async_raises, "b": b_sync}).arun(
            State.empty("v"), replay=ReplayStatus.declared("full")))
    except NodeFailed as exc:
        raised_trace = exc.trace
    kind, message, leaked = caught_verify(
        {"a": a_async_raises, "b": b_sync}, PURE, raised_trace)
    row("verify(async node with outcome=raised) refuses",
        "PASS" if kind == "ReplayError" else "FAIL", f"{kind}: {message}")
    row("... and leaks no coroutine", "PASS" if not leaked else "FAIL", str(leaked))

    # ------------------------------------------------------------ unchanged --
    sync_trace = Runtime(PURE, SYNC).run(
        State.empty("v"), replay=ReplayStatus.declared("full")).trace
    kind, message, leaked = caught_verify(SYNC, PURE, sync_trace)
    row("verify(sync) unchanged", "PASS" if kind == "OK" else "FAIL",
        f"{kind}: {message}")

    def a_changed(state: State) -> State:
        return state.with_fact(Fact("a", "SOMETHING ELSE", "x3b", NOW))

    kind, message, leaked = caught_verify({"a": a_changed, "b": b_sync},
                                          PURE, sync_trace)
    row("a real divergence still raises ReplayMismatch",
        "PASS" if kind == "ReplayMismatch" else "FAIL", f"{kind}: {message}")

    def a_raises(state: State) -> State:
        raise ValueError("now it raises")

    kind, message, _ = caught_verify({"a": a_raises, "b": b_sync}, PURE, sync_trace)
    row("a node that starts raising still raises ReplayMismatch",
        "PASS" if kind == "ReplayMismatch" else "FAIL", f"{kind}: {message}")

    # an async node that is NOT pure is never re-executed, so it verifies
    impure_trace = asyncio.run(
        Runtime(IMPURE, {"a": a_async, "b": b_sync}).arun(
            State.empty("v"), replay=ReplayStatus.declared("full"))
    ).trace
    kind, message, leaked = caught_verify({"a": a_async, "b": b_sync},
                                          IMPURE, impure_trace)
    row("async node declared recorded_effect: verify succeeds",
        "PASS" if kind == "OK" else "FAIL",
        f"{kind}: {message} — verify only re-executes `pure` nodes")

    # ------------------------------------------------ residual capability ----
    print("=== the verify() fix ===")
    width = max(len(c) for c, _, _ in ROWS)
    for check, verdict, detail in ROWS:
        print(f"  {verdict:<4}  {check:<{width}}  {detail}")

    print("\n=== what is still unreachable for a graph with async nodes ===")

    # resume / _run_from
    rt = Runtime(THREE, {"a": a_sync, "b": b_async, "c": c_sync})

    async def half():
        async for record in rt.astream(State.empty("half")):
            if record["kind"] == "transition":
                break
        await asyncio.sleep(0)
        await asyncio.sleep(0)

    asyncio.run(half())
    partial = rt._trace
    engine = ReplayEngine(TraceBundle(spec=THREE, trace=partial))
    fresh = Runtime(THREE, {"a": a_sync, "b": b_async, "c": c_sync})
    try:
        segment = engine.resume(fresh, run_id="x3b-resumed")
        print(f"  resume(): terminal={segment.trace.to_dict()['records'][-1]['kind']}")
    except BaseException as exc:  # noqa: BLE001
        print(f"  resume(): {type(exc).__name__}: {exc}")
        trace = getattr(exc, "trace", None)
        if trace is not None:
            for record in trace.to_dict()["records"]:
                if record["kind"] == "transition" and record.get("error"):
                    print(f"    recorded error: "
                          f"{json.dumps(record['error'], sort_keys=True)}")

    # _run_from directly
    fresh2 = Runtime(THREE, {"a": a_sync, "b": b_async, "c": c_sync})
    try:
        fresh2._run_from(State.empty("direct"), start_node="b",
                         resume_info=None, run_id="x3b-direct",
                         replay=ReplayStatus.declared("none", ("x",)))
        print("  _run_from(): completed")
    except BaseException as exc:  # noqa: BLE001
        print(f"  _run_from(): {type(exc).__name__}: {exc}")

    # is the header declaration honest?
    doc = async_trace.to_dict()
    print(f"\n  header replay declaration on the async run: "
          f"{json.dumps(doc['run']['replay'])}")
    print(f"  ...but verify() on it: ReplayError "
          f"(the engine cannot re-execute the pure nodes it names)")

    engine_api = sorted(n for n in dir(ReplayEngine) if not n.startswith("_"))
    print(f"  ReplayEngine public surface: {engine_api} "
          f"(no averify / aresume)")

    failures = sum(1 for _, v, _ in ROWS if v == "FAIL")
    print(f"\n{len(ROWS)} checks, {failures} FAIL")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
