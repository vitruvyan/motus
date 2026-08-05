"""x3c_01 — can the new `node:<name>:async` constraint break T10?

T10 (contract/validate.py) says two things about every terminal record:

  1. the final capability may not rank ABOVE the declared one, and
  2. no constraint present in the header's declaration may be absent from the
     terminal's.

`_refresh_identity` now appends ``("partial", f"node:{name}:async")`` for every
coroutine-function node, and `_start` feeds those to
`_RunController.downgrade_many`. This drives every shape where that could go
wrong — declared full / partial / none, caller constraints that collide with
the generated one, every terminal kind, resume segments that already declare
`partial` + `resume:<id>`, runs torn down before any terminal, and graphs
where the async node is never reached — and validates each produced trace with
`contract/validate.py` in BOTH encodings.

It also reports, for each case, the header declaration beside the terminal
status, so the "is the header honest?" question is answered from evidence.

Run: python .attack/x3c_01_t10_async_degradation.py
"""

from __future__ import annotations

import asyncio
import functools
import importlib.util
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from vitruvyan_motus import (  # noqa: E402
    Decision,
    DurabilityProfile,
    Fact,
    GraphSpec,
    InMemoryTraceSink,
    NodeFailed,
    Policy,
    ReplayEngine,
    ReplayStatus,
    Runtime,
    State,
    TraceBundle,
)


def _validator():
    spec = importlib.util.spec_from_file_location(
        "x3c_contract_validate", ROOT / "contract" / "validate.py"
    )
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


V = _validator()
NOW = datetime(2026, 8, 5, tzinfo=timezone.utc)
_RANK = {"none": 0, "partial": 1, "full": 2}


# --------------------------------------------------------------------------- #
# Graphs                                                                       #
# --------------------------------------------------------------------------- #

def spec_doc(name, nodes, transitions, **extra):
    doc = {
        "schema_version": "1.0.0", "name": name, "version": "1.0.0",
        "entry": nodes[0]["name"], "nodes": nodes, "transitions": transitions,
    }
    doc.update(extra)
    return doc


LINEAR_DOC = spec_doc(
    "x3c-linear",
    [{"name": "a", "effect_class": "pure"}, {"name": "b", "effect_class": "pure"}],
    {"a": {"kind": "next", "to": "b"}, "b": {"kind": "terminal"}})
THREE_DOC = spec_doc(
    "x3c-three",
    [{"name": "a", "effect_class": "pure"},
     {"name": "b", "effect_class": "pure"},
     {"name": "c", "effect_class": "pure"}],
    {"a": {"kind": "next", "to": "b"}, "b": {"kind": "next", "to": "c"},
     "c": {"kind": "terminal"}})
# The async node is a route the run never takes.
UNREACHED_DOC = spec_doc(
    "x3c-unreached",
    [{"name": "classify", "effect_class": "pure"},
     {"name": "taken", "effect_class": "pure"},
     {"name": "never", "effect_class": "pure"}],
    {"classify": {"kind": "route", "on": "verdict",
                  "map": {"go": "taken"}, "default": "never"},
     "taken": {"kind": "terminal"}, "never": {"kind": "terminal"}})
CTX_DOC = spec_doc(
    "x3c-ctx", [{"name": "draw", "effect_class": "pure"}],
    {"draw": {"kind": "terminal"}})

LINEAR = GraphSpec.from_dict(dict(LINEAR_DOC))
THREE = GraphSpec.from_dict(dict(THREE_DOC))
UNREACHED = GraphSpec.from_dict(dict(UNREACHED_DOC))
CTX = GraphSpec.from_dict(dict(CTX_DOC))


# --------------------------------------------------------------------------- #
# Nodes                                                                        #
# --------------------------------------------------------------------------- #

def sync_node(key):
    def node(state: State) -> State:
        return state.with_fact(Fact(key, "done", "x3c", NOW))
    return node


def async_node(key):
    async def node(state: State) -> State:
        await asyncio.sleep(0)
        return state.with_fact(Fact(key, "done", "x3c", NOW))
    return node


async def _async_body(state: State, key: str) -> State:
    await asyncio.sleep(0)
    return state.with_fact(Fact(key, "done", "x3c", NOW))


class AsyncCallable:
    """A callable object whose __call__ is a coroutine function."""

    def __init__(self, key: str) -> None:
        self.key = key

    async def __call__(self, state: State) -> State:
        await asyncio.sleep(0)
        return state.with_fact(Fact(self.key, "done", "x3c", NOW))


def plain_def_returning_a_coroutine(key):
    """NOT a coroutine function. `iscoroutinefunction` is False for it, but it
    hands `arun` an awaitable and `verify` something it cannot drive."""
    def node(state: State):
        return _async_body(state, key)
    return node


def async_generator_node(key):
    async def node(state: State):
        yield state.with_fact(Fact(key, "done", "x3c", NOW))
    return node


# --------------------------------------------------------------------------- #
# Harness                                                                      #
# --------------------------------------------------------------------------- #

CASES: list[dict] = []


def case(name, spec_doc_, trace, *, complete=True, note=""):
    CASES.append({"name": name, "spec": spec_doc_, "trace": trace,
                  "complete": complete, "note": note})


def capture(fn):
    try:
        return fn().trace
    except NodeFailed as exc:
        return exc.trace


async def acapture(coro):
    try:
        return (await coro).trace
    except NodeFailed as exc:
        return exc.trace


def build_sync() -> None:
    A, B = async_node("a"), sync_node("b")
    S3 = {"a": sync_node("a"), "b": sync_node("b"), "c": sync_node("c")}
    A3 = {"a": async_node("a"), "b": sync_node("b"), "c": sync_node("c")}

    # 1 declared full, async node present, driven synchronously (the async node
    #   fails under run(), which is fine — the constraint is a property of the
    #   REGISTRY, not of what executed).
    case("01 declared full, async node, sync driver", LINEAR_DOC,
         capture(lambda: Runtime(LINEAR, {"a": A, "b": B}).run(
             State.empty("x"), replay=ReplayStatus.declared("full"))),
         note="the async node never ran; the constraint still applies")

    # 2 declared partial with the caller's own constraint
    case("02 declared partial + caller constraint", LINEAR_DOC,
         Runtime(LINEAR, {"a": sync_node("a"), "b": B}).run(
             State.empty("x"),
             replay=ReplayStatus.declared("partial", ("zz-caller-reason",))).trace,
         note="no async node: baseline for constraint preservation")

    case("03 declared partial + caller constraint + async node", LINEAR_DOC,
         capture(lambda: Runtime(LINEAR, {"a": A, "b": B}).run(
             State.empty("x"),
             replay=ReplayStatus.declared("partial", ("zz-caller-reason",)))))

    # 4 declared none with the caller's constraint: must NOT be raised to partial
    case("04 declared none + async node (must stay none)", LINEAR_DOC,
         capture(lambda: Runtime(LINEAR, {"a": A, "b": B}).run(
             State.empty("x"),
             replay=ReplayStatus.declared("none", ("caller-said-none",)))))

    # 5 the caller declares exactly the constraint the runtime generates
    case("05 constraint collision (caller declares node:a:async)", LINEAR_DOC,
         capture(lambda: Runtime(LINEAR, {"a": A, "b": B}).run(
             State.empty("x"),
             replay=ReplayStatus.declared("partial", ("node:a:async",)))),
         note="set-union must not duplicate, and must not lose the declared one")

    # 6 two async nodes -> two constraints
    case("06 two async nodes", LINEAR_DOC,
         capture(lambda: Runtime(
             LINEAR, {"a": async_node("a"), "b": async_node("b")}).run(
             State.empty("x"), replay=ReplayStatus.declared("full"))))

    # 7 async node that is never reached by the route
    case("07 async node on an untaken route", UNREACHED_DOC,
         Runtime(UNREACHED, {
             "classify": lambda s: s.with_decision(Decision("verdict", "go", NOW)),
             "taken": sync_node("taken"),
             "never": async_node("never"),
         }).run(State.empty("x"), replay=ReplayStatus.declared("full")).trace,
         note="degrades even though the async node never executed")

    # 8 functools.partial wrapping an async function
    case("08 functools.partial(async fn)", LINEAR_DOC,
         capture(lambda: Runtime(LINEAR, {
             "a": functools.partial(_async_body, key="a"), "b": B}).run(
             State.empty("x"), replay=ReplayStatus.declared("full"))))

    # 9 a callable object with `async def __call__`
    case("09 callable object with async __call__", LINEAR_DOC,
         capture(lambda: Runtime(LINEAR, {"a": AsyncCallable("a"), "b": B}).run(
             State.empty("x"), replay=ReplayStatus.declared("full"))))

    # 10 a plain def that RETURNS a coroutine — the detector gap
    case("10 plain def returning a coroutine", LINEAR_DOC,
         capture(lambda: Runtime(
             LINEAR, {"a": plain_def_returning_a_coroutine("a"), "b": B}).run(
             State.empty("x"), replay=ReplayStatus.declared("full"))),
         note="iscoroutinefunction is False for it")

    # 11 an async GENERATOR node
    case("11 async generator node", LINEAR_DOC,
         capture(lambda: Runtime(
             LINEAR, {"a": async_generator_node("a"), "b": B}).run(
             State.empty("x"), replay=ReplayStatus.declared("full"))))

    # 12 cancellation mid-stream with an async node in the registry
    rt = Runtime(THREE, A3)
    driver = rt.stream(State.empty("x"), replay=ReplayStatus.declared("full"))
    next(driver)
    driver.close("x3c consumer stopped")
    case("12 run_cancelled with an async node", THREE_DOC, driver.trace)

    # 13 pre-run cancellation
    rt = Runtime(THREE, A3)
    rt.cancel("x3c pre-cancel")
    case("13 pre-run cancellation with an async node", THREE_DOC,
         rt.run(State.empty("x"), replay=ReplayStatus.declared("full")).trace)

    # 14 run_failed with an async node in the registry
    case("14 run_failed with an async node", LINEAR_DOC,
         capture(lambda: Runtime(LINEAR, {
             "a": (lambda s: (_ for _ in ()).throw(ValueError("boom"))),
             "b": async_node("b")}).run(
             State.empty("x"), replay=ReplayStatus.declared("full"))))

    # 15 torn down before any terminal: the header is the ONLY replay statement
    rt = Runtime(THREE, A3)
    for record in rt.stream(State.empty("x"),
                            replay=ReplayStatus.declared("full")):
        if record["kind"] == "attempt_started":
            break
    case("15 no terminal (abandoned stream)", THREE_DOC, rt.trace,
         complete=False,
         note="nothing degrades the header when there is no terminal")

    # 16 resume segment (declares partial + resume:<id>) over an async graph
    rt = Runtime(THREE, S3)
    for record in rt.stream(State.empty("x")):
        if record["kind"] == "transition":
            break
    source = rt.trace
    engine = ReplayEngine(TraceBundle(spec=THREE, trace=source))
    fresh = Runtime(THREE, {"a": sync_node("a"), "b": sync_node("b"),
                            "c": async_node("c")})
    case("16 resume segment over an async graph", THREE_DOC,
         capture(lambda: engine.resume(fresh, run_id="x3c-resume")),
         note="declared partial + resume:<id>; the async constraint joins it")

    # 17 async node PLUS an ambient-draw degradation source
    def drawing(state: State, context) -> State:
        return state.with_fact(Fact("draw", context.uuid(), "x3c", context.now()))

    case("17 context draws + async, declared full", CTX_DOC,
         Runtime(CTX, {"draw": drawing}).run(
             State.empty("x"), replay=ReplayStatus.declared("full")).trace,
         note="baseline: draws alone")

    # 18 durability profiles carry the same status
    sink = InMemoryTraceSink()
    case("18 buffered profile with an async node", LINEAR_DOC,
         capture(lambda: Runtime(
             LINEAR, {"a": A, "b": B}, sink=sink,
             durability_profile=DurabilityProfile.BUFFERED,
             chunk_records=2, flush_interval_ms=10).run(
             State.empty("x"), replay=ReplayStatus.declared("full"))))

    # 19 exploration policy: a route miss completes, still degraded
    case("19 exploration completion with an async node", LINEAR_DOC,
         Runtime(LINEAR, {"a": sync_node("a"), "b": async_node("b")},
                 policy=Policy.EXPLORATION).run(
             State.empty("x"), replay=ReplayStatus.declared("full")).trace,
         note="the async node raises under run(); exploration continues")


async def build_async() -> None:
    A3 = {"a": async_node("a"), "b": async_node("b"), "c": async_node("c")}

    case("20 arun, declared full, all async", LINEAR_DOC,
         (await Runtime(LINEAR, {"a": async_node("a"), "b": async_node("b")})
          .arun(State.empty("x"), replay=ReplayStatus.declared("full"))).trace)

    case("21 arun, declared partial + caller constraint", LINEAR_DOC,
         (await Runtime(LINEAR, {"a": async_node("a"), "b": async_node("b")})
          .arun(State.empty("x"),
                replay=ReplayStatus.declared("partial", ("zz-caller",)))).trace)

    case("22 arun, declared none + caller constraint", LINEAR_DOC,
         (await Runtime(LINEAR, {"a": async_node("a"), "b": async_node("b")})
          .arun(State.empty("x"),
                replay=ReplayStatus.declared("none", ("caller-none",)))).trace)

    case("23 arun, mixed sync + async", LINEAR_DOC,
         (await Runtime(LINEAR, {"a": sync_node("a"), "b": async_node("b")})
          .arun(State.empty("x"), replay=ReplayStatus.declared("full"))).trace)

    case("24 arun, plain def returning a coroutine", LINEAR_DOC,
         (await Runtime(LINEAR, {"a": plain_def_returning_a_coroutine("a"),
                                 "b": async_node("b")})
          .arun(State.empty("x"), replay=ReplayStatus.declared("full"))).trace,
         note="executes fine; is it degraded?")

    case("25 arun, functools.partial(async fn)", LINEAR_DOC,
         (await Runtime(LINEAR, {"a": functools.partial(_async_body, key="a"),
                                 "b": async_node("b")})
          .arun(State.empty("x"), replay=ReplayStatus.declared("full"))).trace)

    case("26 arun, callable object with async __call__", LINEAR_DOC,
         (await Runtime(LINEAR, {"a": AsyncCallable("a"), "b": async_node("b")})
          .arun(State.empty("x"), replay=ReplayStatus.declared("full"))).trace)

    case("27 arun raised to run_failed", LINEAR_DOC,
         await acapture(Runtime(LINEAR, {
             "a": _raising_async(), "b": async_node("b")}).arun(
             State.empty("x"), replay=ReplayStatus.declared("full"))))

    # astream cancellation
    rt = Runtime(THREE, A3)
    driver = rt.astream(State.empty("x"), replay=ReplayStatus.declared("full"))
    await driver.__anext__()
    await driver.aclose("x3c consumer stopped")
    case("28 astream run_cancelled", THREE_DOC, driver.trace)

    # astream abandoned: no terminal at all
    rt = Runtime(THREE, A3)
    async for record in rt.astream(State.empty("x"),
                                   replay=ReplayStatus.declared("full")):
        if record["kind"] == "attempt_started":
            break
    await asyncio.sleep(0)
    await asyncio.sleep(0)
    case("29 astream abandoned, no terminal", THREE_DOC, rt.trace,
         complete=False)


def _raising_async():
    async def node(state: State) -> State:
        await asyncio.sleep(0)
        raise ValueError("planned")
    return node


# --------------------------------------------------------------------------- #
# Report                                                                       #
# --------------------------------------------------------------------------- #

def main() -> int:
    build_sync()
    asyncio.run(build_async())

    problems = 0
    header_overclaims = []
    print(f"{'case':<48} {'declared':<28} {'terminal':<40} "
          f"{'T10':<4} {'JSON':<5} {'JSONL':<5} {'eq':<5}")
    print("-" * 148)
    for entry in CASES:
        doc = json.loads(json.dumps(entry["trace"].to_dict()))
        declared = doc["run"].get("replay") or {}
        terminals = [r for r in doc["records"]
                     if r["kind"] in ("run_completed", "run_failed", "run_cancelled")]
        final = (terminals[-1].get("replay") or {}) if terminals else {}

        def fmt(status):
            if not status:
                return "(none)"
            cs = ",".join(status.get("constraints") or [])
            return f"{status.get('capability')}[{cs}]"

        # T10, recomputed independently of the validator
        t10 = "OK"
        if declared.get("capability") in _RANK and final.get("capability") in _RANK:
            if _RANK[final["capability"]] > _RANK[declared["capability"]]:
                t10 = "UP!"
        lost = [c for c in (declared.get("constraints") or [])
                if c not in (final.get("constraints") or [])]
        if terminals and lost:
            t10 = "LOST"

        json_v = V.validate_trace(doc, entry["spec"], entry["complete"])
        jsonl_v, reassembled = V.validate_jsonl(
            entry["trace"].to_jsonl(), entry["spec"], entry["complete"])
        eq = reassembled == doc
        if json_v or jsonl_v or not eq or t10 != "OK":
            problems += 1

        # honesty of the header, judged on its own
        if declared.get("capability") == "full" and final.get("capability") != "full":
            header_overclaims.append((entry["name"], "terminal degrades it"))
        if declared.get("capability") == "full" and not terminals:
            header_overclaims.append((entry["name"], "NO TERMINAL — nothing degrades it"))

        print(f"{entry['name']:<48} {fmt(declared):<28} {fmt(final):<40} "
              f"{t10:<4} {('PASS' if not json_v else f'{len(json_v)}v'):<5} "
              f"{('PASS' if not jsonl_v else f'{len(jsonl_v)}v'):<5} {str(eq):<5}")
        for v in json_v[:3]:
            print(f"      JSON  {v.rule} {v.path}: {v.message}")
        for v in jsonl_v[:3]:
            print(f"      JSONL {v.rule} {v.path}: {v.message}")
        if entry["note"]:
            print(f"      note: {entry['note']}")

    print("-" * 148)
    print(f"{len(CASES)} cases, {problems} problem(s)")
    print("\nheader-vs-terminal honesty:")
    for name, why in header_overclaims:
        print(f"  {name}: header says full — {why}")
    return 1 if problems else 0


if __name__ == "__main__":
    raise SystemExit(main())
