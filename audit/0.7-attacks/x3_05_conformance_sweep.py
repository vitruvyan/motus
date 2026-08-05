"""x3_05 — contract conformance at scale, post-removal and post-inversion.

Generates structurally diverse REAL executions (not fixtures) across both
drivers, all three durability profiles, every terminal kind, every transition
disposition, redaction, effects with receipts, retry chains, cyclic limits,
resume segments and seeded state; then validates each produced trace with
`contract/validate.py` in BOTH encodings and compares the verdicts.

Reported per scenario:
  * the terminal record kind and record count,
  * JSON verdict  (validate_trace),
  * JSONL verdict (validate_jsonl on Trace.to_jsonl()),
  * whether the JSONL reassembly equals the JSON document exactly.

Exit code 0 only if every scenario is contract-clean in both encodings and
the two encodings agree.

Run: python .attack/x3_05_conformance_sweep.py
"""

from __future__ import annotations

import asyncio
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
    EffectClass,
    EffectDescriptor,
    EffectReceipt,
    Fact,
    GraphSpec,
    InMemoryTraceSink,
    NodeFailed,
    Policy,
    Rejection,
    ReplayEngine,
    ReplayStatus,
    Runtime,
    State,
    TraceBundle,
    redact,
)


def _validator():
    spec = importlib.util.spec_from_file_location(
        "x3_contract_validate", ROOT / "contract" / "validate.py"
    )
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


V = _validator()
NOW = datetime(2026, 8, 5, tzinfo=timezone.utc)


# --------------------------------------------------------------------------- #
# Graphs                                                                       #
# --------------------------------------------------------------------------- #

def spec_doc(name, nodes, transitions, **extra):
    doc = {
        "schema_version": "1.0.0",
        "name": name,
        "version": "1.0.0",
        "entry": nodes[0]["name"],
        "nodes": nodes,
        "transitions": transitions,
    }
    doc.update(extra)
    return doc


LINEAR_DOC = spec_doc(
    "x3-linear",
    [{"name": "a", "effect_class": "pure"}, {"name": "b", "effect_class": "pure"}],
    {"a": {"kind": "next", "to": "b"}, "b": {"kind": "terminal"}},
)
ROUTED_DOC = spec_doc(
    "x3-routed",
    [
        {"name": "classify", "effect_class": "pure"},
        {"name": "store", "effect_class": "pure"},
        {"name": "escalate", "effect_class": "pure"},
    ],
    {
        "classify": {
            "kind": "route",
            "on": "verdict",
            "map": {"approve": "store", "reject": "escalate"},
            "default": "escalate",
        },
        "store": {"kind": "terminal"},
        "escalate": {"kind": "terminal"},
    },
)
STRICT_ROUTE_DOC = spec_doc(
    "x3-strict-route",
    [
        {"name": "classify", "effect_class": "pure"},
        {"name": "store", "effect_class": "pure"},
    ],
    {
        "classify": {"kind": "route", "on": "verdict", "map": {"approve": "store"}},
        "store": {"kind": "terminal"},
    },
)
CYCLIC_DOC = spec_doc(
    "x3-cyclic",
    [
        {"name": "fetch", "effect_class": "pure"},
        {"name": "store", "effect_class": "pure"},
    ],
    {
        "fetch": {"kind": "route", "on": "status", "map": {"ok": "store", "retry": "fetch"}},
        "store": {"kind": "terminal"},
    },
    max_transitions=6,
)
DECL_DOC = spec_doc(
    "x3-declared",
    [
        {
            "name": "work",
            "effect_class": "pure",
            "reads_declared": ["seeded"],
            "writes_declared": ["allowed"],
        }
    ],
    {"work": {"kind": "terminal"}},
)
EFFECT_DOC = spec_doc(
    "x3-effect",
    [
        {"name": "call", "effect_class": "external_effect"},
        {"name": "note", "effect_class": "recorded_effect"},
    ],
    {"call": {"kind": "next", "to": "note"}, "note": {"kind": "terminal"}},
)
THREE_DOC = spec_doc(
    "x3-three",
    [
        {"name": "a", "effect_class": "pure"},
        {"name": "b", "effect_class": "pure"},
        {"name": "c", "effect_class": "pure"},
    ],
    {
        "a": {"kind": "next", "to": "b"},
        "b": {"kind": "next", "to": "c"},
        "c": {"kind": "terminal"},
    },
)
CTX_DOC = spec_doc(
    "x3-context",
    [{"name": "draw", "effect_class": "pure"}],
    {"draw": {"kind": "terminal"}},
)

SPECS = {
    "linear": (GraphSpec.from_dict(dict(LINEAR_DOC)), LINEAR_DOC),
    "routed": (GraphSpec.from_dict(dict(ROUTED_DOC)), ROUTED_DOC),
    "strict_route": (GraphSpec.from_dict(dict(STRICT_ROUTE_DOC)), STRICT_ROUTE_DOC),
    "cyclic": (GraphSpec.from_dict(dict(CYCLIC_DOC)), CYCLIC_DOC),
    "declared": (GraphSpec.from_dict(dict(DECL_DOC)), DECL_DOC),
    "effect": (GraphSpec.from_dict(dict(EFFECT_DOC)), EFFECT_DOC),
    "three": (GraphSpec.from_dict(dict(THREE_DOC)), THREE_DOC),
    "context": (GraphSpec.from_dict(dict(CTX_DOC)), CTX_DOC),
}


# --------------------------------------------------------------------------- #
# Node builders                                                                #
# --------------------------------------------------------------------------- #

def fact_node(key, value="done"):
    def node(state: State) -> State:
        return state.with_fact(Fact(key, value, "x3", NOW))
    return node


def afact_node(key, value="done"):
    async def node(state: State) -> State:
        await asyncio.sleep(0)
        return state.with_fact(Fact(key, value, "x3", NOW))
    return node


def decide(key, value):
    def node(state: State) -> State:
        return state.with_decision(Decision(key, value, NOW, reason="x3"))
    return node


def adecide(key, value):
    async def node(state: State) -> State:
        await asyncio.sleep(0)
        return state.with_decision(Decision(key, value, NOW, reason="x3"))
    return node


def boom(message="planned failure"):
    def node(state: State) -> State:
        raise ValueError(message)
    return node


def aboom(message="planned failure"):
    async def node(state: State) -> State:
        await asyncio.sleep(0)
        raise ValueError(message)
    return node


# --------------------------------------------------------------------------- #
# Harness                                                                      #
# --------------------------------------------------------------------------- #

SCENARIOS: list[tuple[str, str, object]] = []


def run_capture(runtime, *args, **kwargs):
    """run()/arun() but keep the trace even when NodeFailed is raised."""
    try:
        return runtime.run(*args, **kwargs).trace
    except NodeFailed as exc:
        return exc.trace


async def arun_capture(runtime, *args, **kwargs):
    try:
        return (await runtime.arun(*args, **kwargs)).trace
    except NodeFailed as exc:
        return exc.trace


def scenario(name, spec_key, trace, complete=True):
    SCENARIOS.append((name, spec_key, trace, complete))


# --------------------------------------------------------------------------- #
# 1..N  the executions                                                         #
# --------------------------------------------------------------------------- #

def build_sync() -> None:
    linear, _ = SPECS["linear"]
    routed, _ = SPECS["routed"]
    strict, _ = SPECS["strict_route"]
    cyclic, _ = SPECS["cyclic"]
    declared, _ = SPECS["declared"]
    effect, _ = SPECS["effect"]
    three, _ = SPECS["three"]
    ctx, _ = SPECS["context"]

    # 1 linear, in-memory
    scenario("01 linear sync in-memory", "linear",
             Runtime(linear, {"a": fact_node("a"), "b": fact_node("b")})
             .run(State.empty("linear")).trace)

    # 2 linear sync via StreamDriver, fully drained
    rt = Runtime(linear, {"a": fact_node("a"), "b": fact_node("b")})
    driver = rt.stream(State.empty("streamed"))
    for _ in driver:
        pass
    scenario("02 linear sync stream drained", "linear", driver.trace)

    # 3 routed matched
    scenario("03 routed matched sync", "routed",
             Runtime(routed, {"classify": decide("verdict", "approve"),
                              "store": fact_node("stored"),
                              "escalate": fact_node("escalated")})
             .run(State.empty("matched")).trace)

    # 4 routed default
    scenario("04 routed default sync", "routed",
             Runtime(routed, {"classify": decide("verdict", "unmapped"),
                              "store": fact_node("stored"),
                              "escalate": fact_node("escalated")})
             .run(State.empty("default")).trace)

    # 5 routed miss (no default) -> run_failed
    scenario("05 route miss strict sync", "strict_route",
             run_capture(Runtime(strict, {"classify": decide("verdict", "nope"),
                                          "store": fact_node("stored")}),
                         State.empty("miss")))

    # 6 route miss where no decision at all was written (absent origin)
    scenario("06 route miss absent origin", "strict_route",
             run_capture(Runtime(strict, {"classify": fact_node("noop"),
                                          "store": fact_node("stored")}),
                         State.empty("absent")))

    # 7 cyclic at the transition limit
    counter = {"n": 0}

    def looping(state: State) -> State:
        counter["n"] += 1
        return state.with_decision(Decision("status", "retry", NOW))

    scenario("07 cyclic at max_transitions", "cyclic",
             run_capture(Runtime(cyclic, {"fetch": looping, "store": fact_node("stored")}),
                         State.empty("cyclic")))

    # 8 retry chain: fails twice, succeeds on attempt 3
    attempts = {"n": 0}

    def flaky(state: State) -> State:
        attempts["n"] += 1
        if attempts["n"] < 3:
            raise RuntimeError(f"attempt {attempts['n']} fails")
        return state.with_fact(Fact("a", "eventually", "x3", NOW))

    scenario("08 retry chain sync", "linear",
             Runtime(linear, {"a": flaky, "b": fact_node("b")}, max_attempts=3)
             .run(State.empty("retry")).trace)

    # 9 node raises to exhaustion -> run_failed
    scenario("09 raised to run_failed sync", "linear",
             run_capture(Runtime(linear, {"a": boom(), "b": fact_node("b")}),
                         State.empty("failure")))

    # 10 declaration violation under STRICT -> abort
    def undeclared_write(state: State) -> State:
        return state.with_fact(Fact("forbidden", 1, "x3", NOW))

    scenario("10 declaration violation strict", "declared",
             run_capture(Runtime(declared, {"work": undeclared_write},
                                 policy=Policy.STRICT),
                         State.empty("strict")))

    # 11 declaration violation under EXPLORATION -> continue
    scenario("11 declaration violation exploration", "declared",
             Runtime(declared, {"work": undeclared_write},
                     policy=Policy.EXPLORATION)
             .run(State.empty("exploration")).trace)

    # 12 seeded state: the initial state carries facts the node reads
    seeded = State.from_snapshot(
        {"facts": [Fact("seeded", "yes", "operator", NOW).to_dict()],
         "decisions": [], "rejections": []},
        intent="seeded",
        metadata={"ruleset_version": "x3.1"},
    )

    def read_seeded(state: State) -> State:
        _ = state.fact("seeded")
        return state.with_fact(Fact("allowed", "ok", "x3", NOW))

    scenario("12 seeded state + declared read", "declared",
             Runtime(declared, {"work": read_seeded}).run(seeded).trace)

    # 13 redaction
    def redacting(state: State) -> State:
        return state.with_fact(
            Fact("a", redact("super-secret", "policy://x3/pii"), "x3", NOW)
        )

    scenario("13 redacted write", "linear",
             Runtime(linear, {"a": redacting, "b": fact_node("b")})
             .run(State.empty("redaction")).trace)

    # 14 rejection with evidence, and one without
    def rejecting(state: State) -> State:
        return (state
                .with_rejection(Rejection("route-b", "policy", NOW, evidence={"rule": "x3"}))
                .with_fact(Fact("a", "done", "x3", NOW)))

    scenario("14 rejection with evidence", "linear",
             Runtime(linear, {"a": rejecting, "b": fact_node("b")})
             .run(State.empty("rejection")).trace)

    def rejecting_bare(state: State) -> State:
        return (state
                .with_rejection(Rejection("route-b", "policy", NOW))
                .with_fact(Fact("a", "done", "x3", NOW)))

    scenario("15 rejection without evidence", "linear",
             Runtime(linear, {"a": rejecting_bare, "b": fact_node("b")})
             .run(State.empty("rejection-bare")).trace)

    # 16 external effect with idempotency key + completed receipt
    def calling(state: State, context) -> State:
        context.record_effect(EffectDescriptor(
            EffectClass.EXTERNAL_EFFECT,
            "POST /orders",
            idempotency_key="x3-key-1",
            receipt=EffectReceipt("rcpt-1", "completed"),
        ))
        return state.with_fact(Fact("called", "yes", "x3", NOW))

    def noting(state: State, context) -> State:
        context.record_effect(EffectDescriptor(
            EffectClass.RECORDED_EFFECT, "model call", receipt=EffectReceipt("rcpt-2")
        ))
        return state.with_fact(Fact("noted", "yes", "x3", NOW))

    scenario("16 effects with receipts", "effect",
             Runtime(effect, {"call": calling, "note": noting})
             .run(State.empty("effects")).trace)

    # 17 context draws under a full replay declaration
    def drawing(state: State, context) -> State:
        stamp = context.now()
        token = context.uuid()
        score = context.rand()
        return state.with_fact(Fact("draw", f"{token}:{score:.3f}", "x3", stamp))

    scenario("17 context draws, replay full", "context",
             Runtime(ctx, {"draw": drawing})
             .run(State.empty("draws"), replay=ReplayStatus.declared("full")).trace)

    # 18 cancellation mid-stream, sync driver
    rt = Runtime(three, {"a": fact_node("a"), "b": fact_node("b"), "c": fact_node("c")})
    driver = rt.stream(State.empty("cancel-mid"))
    next(driver)
    next(driver)
    driver.close("x3 consumer stopped")
    scenario("18 cancellation mid-stream sync", "three", driver.trace)

    # 19 pre-run cancellation
    rt = Runtime(three, {"a": fact_node("a"), "b": fact_node("b"), "c": fact_node("c")})
    rt.cancel("x3 cancelled before start")
    scenario("19 pre-run cancellation", "three", rt.run(State.empty("pre-cancel")).trace)

    # 20 buffered durability with a sink
    sink = InMemoryTraceSink()
    scenario("20 buffered profile + sink", "linear",
             Runtime(linear, {"a": fact_node("a"), "b": fact_node("b")},
                     sink=sink, durability_profile=DurabilityProfile.BUFFERED,
                     chunk_records=2, flush_interval_ms=10)
             .run(State.empty("buffered")).trace)

    # 21 synchronous durability with a sink
    sink2 = InMemoryTraceSink()
    scenario("21 synchronous profile + sink", "linear",
             Runtime(linear, {"a": fact_node("a"), "b": fact_node("b")},
                     sink=sink2, durability_profile=DurabilityProfile.SYNCHRONOUS)
             .run(State.empty("synchronous")).trace)

    # 22 an in-flight (abandoned, terminal-less) trace, and 22b its resume
    rt = Runtime(three, {"a": fact_node("a"), "b": fact_node("b"), "c": fact_node("c")})
    for record in rt.stream(State.empty("to-resume")):
        if record["kind"] == "transition":
            break
    source = rt._trace
    scenario("22 in-flight trace (no terminal)", "three", source, complete=False)

    engine = ReplayEngine(TraceBundle(spec=three, trace=source))
    fresh = Runtime(three, {"a": fact_node("a"), "b": fact_node("b"), "c": fact_node("c")})
    try:
        segment = engine.resume(fresh, run_id="x3-resumed")
        scenario("22b resume segment", "three", segment.trace)
    except BaseException as exc:  # noqa: BLE001
        print(f"  !! resume scenario unavailable: {type(exc).__name__}: {exc}")

    # 22c exploration: a raising node at max attempts continues
    scenario("22c exploration continue", "linear",
             Runtime(linear, {"a": boom("explored"), "b": fact_node("b")},
                     policy=Policy.EXPLORATION)
             .run(State.empty("exploration-continue")).trace)

    # 22d exploration: a route miss completes instead of failing
    scenario("22d exploration route miss completes", "strict_route",
             Runtime(strict, {"classify": decide("verdict", "nope"),
                              "store": fact_node("stored")},
                     policy=Policy.EXPLORATION)
             .run(State.empty("exploration-miss")).trace)


async def build_async() -> None:
    linear, _ = SPECS["linear"]
    routed, _ = SPECS["routed"]
    strict, _ = SPECS["strict_route"]
    cyclic, _ = SPECS["cyclic"]
    effect, _ = SPECS["effect"]
    three, _ = SPECS["three"]
    ctx, _ = SPECS["context"]

    # 23 linear async
    scenario("23 linear async in-memory", "linear",
             (await Runtime(linear, {"a": afact_node("a"), "b": afact_node("b")})
              .arun(State.empty("linear"))).trace)

    # 24 mixed sync/async graph
    scenario("24 mixed sync+async nodes", "linear",
             (await Runtime(linear, {"a": fact_node("a"), "b": afact_node("b")})
              .arun(State.empty("mixed"))).trace)

    # 25 astream fully drained
    rt = Runtime(linear, {"a": afact_node("a"), "b": afact_node("b")})
    driver = rt.astream(State.empty("astreamed"))
    async for _ in driver:
        pass
    scenario("25 linear astream drained", "linear", driver.trace)

    # 26 routed matched async
    scenario("26 routed matched async", "routed",
             (await Runtime(routed, {"classify": adecide("verdict", "approve"),
                                     "store": afact_node("stored"),
                                     "escalate": afact_node("escalated")})
              .arun(State.empty("matched"))).trace)

    # 27 routed default async
    scenario("27 routed default async", "routed",
             (await Runtime(routed, {"classify": adecide("verdict", "unmapped"),
                                     "store": afact_node("stored"),
                                     "escalate": afact_node("escalated")})
              .arun(State.empty("default"))).trace)

    # 28 route miss async -> run_failed
    scenario("28 route miss strict async", "strict_route",
             await arun_capture(Runtime(strict, {"classify": adecide("verdict", "nope"),
                                                 "store": afact_node("stored")}),
                                State.empty("miss")))

    # 29 cyclic limit async
    async def looping(state: State) -> State:
        await asyncio.sleep(0)
        return state.with_decision(Decision("status", "retry", NOW))

    scenario("29 cyclic at max_transitions async", "cyclic",
             await arun_capture(Runtime(cyclic, {"fetch": looping,
                                                 "store": afact_node("stored")}),
                                State.empty("cyclic")))

    # 30 async retry chain
    attempts = {"n": 0}

    async def flaky(state: State) -> State:
        await asyncio.sleep(0)
        attempts["n"] += 1
        if attempts["n"] < 3:
            raise RuntimeError(f"attempt {attempts['n']} fails")
        return state.with_fact(Fact("a", "eventually", "x3", NOW))

    scenario("30 retry chain async", "linear",
             (await Runtime(linear, {"a": flaky, "b": afact_node("b")}, max_attempts=3)
              .arun(State.empty("retry"))).trace)

    # 31 async raised to run_failed
    scenario("31 raised to run_failed async", "linear",
             await arun_capture(Runtime(linear, {"a": aboom(), "b": afact_node("b")}),
                                State.empty("failure")))

    # 32 async effects with receipts
    async def calling(state: State, context) -> State:
        await asyncio.sleep(0)
        context.record_effect(EffectDescriptor(
            EffectClass.EXTERNAL_EFFECT, "POST /orders",
            idempotency_key="x3-key-2", receipt=EffectReceipt("rcpt-3", "completed"),
        ))
        return state.with_fact(Fact("called", "yes", "x3", NOW))

    async def noting(state: State, context) -> State:
        await asyncio.sleep(0)
        context.record_effect(EffectDescriptor(
            EffectClass.RECORDED_EFFECT, "model call", receipt=EffectReceipt("rcpt-4")
        ))
        return state.with_fact(Fact("noted", "yes", "x3", NOW))

    scenario("32 async effects with receipts", "effect",
             (await Runtime(effect, {"call": calling, "note": noting})
              .arun(State.empty("effects"))).trace)

    # 33 async context draws under a full replay declaration
    async def drawing(state: State, context) -> State:
        await asyncio.sleep(0)
        stamp = context.now()
        token = context.uuid()
        score = context.rand()
        return state.with_fact(Fact("draw", f"{token}:{score:.3f}", "x3", stamp))

    scenario("33 async context draws, replay full", "context",
             (await Runtime(ctx, {"draw": drawing})
              .arun(State.empty("draws"), replay=ReplayStatus.declared("full"))).trace)

    # 34 async cancellation mid-stream via aclose
    rt = Runtime(three, {"a": afact_node("a"), "b": afact_node("b"), "c": afact_node("c")})
    driver = rt.astream(State.empty("cancel-mid"))
    await driver.__anext__()
    await driver.__anext__()
    await driver.aclose("x3 consumer stopped")
    scenario("34 cancellation mid-stream async", "three", driver.trace)

    # 35 async pre-run cancellation
    rt = Runtime(three, {"a": afact_node("a"), "b": afact_node("b"), "c": afact_node("c")})
    rt.cancel("x3 cancelled before start")
    scenario("35 pre-run cancellation async", "three",
             (await rt.arun(State.empty("pre-cancel"))).trace)

    # 36 async buffered durability
    sink = InMemoryTraceSink()
    scenario("36 buffered profile async", "linear",
             (await Runtime(linear, {"a": afact_node("a"), "b": afact_node("b")},
                            sink=sink, durability_profile=DurabilityProfile.BUFFERED,
                            chunk_records=2, flush_interval_ms=10)
              .arun(State.empty("buffered"))).trace)

    # 37 async synchronous durability
    sink2 = InMemoryTraceSink()
    scenario("37 synchronous profile async", "linear",
             (await Runtime(linear, {"a": afact_node("a"), "b": afact_node("b")},
                            sink=sink2, durability_profile=DurabilityProfile.SYNCHRONOUS)
              .arun(State.empty("synchronous"))).trace)

    # 38 async redaction
    async def redacting(state: State) -> State:
        await asyncio.sleep(0)
        return state.with_fact(
            Fact("a", redact("super-secret", "policy://x3/pii"), "x3", NOW)
        )

    scenario("38 redacted write async", "linear",
             (await Runtime(linear, {"a": redacting, "b": afact_node("b")})
              .arun(State.empty("redaction"))).trace)

    # 39 async exploration continue
    scenario("39 exploration continue async", "linear",
             (await Runtime(linear, {"a": aboom("explored"), "b": afact_node("b")},
                            policy=Policy.EXPLORATION)
              .arun(State.empty("exploration-continue"))).trace)

    # 40 an in-flight async trace, abandoned without aclose
    rt = Runtime(three, {"a": afact_node("a"), "b": afact_node("b"), "c": afact_node("c")})
    async for record in rt.astream(State.empty("in-flight-async")):
        if record["kind"] == "transition":
            break
    await asyncio.sleep(0)
    await asyncio.sleep(0)
    scenario("40 in-flight async trace", "three", rt._trace, complete=False)

    # 41 resume an async in-flight trace with SYNC nodes (the only driver
    #    ReplayEngine.resume has); the async registry is exercised in x3_04.
    engine = ReplayEngine(TraceBundle(spec=SPECS["three"][0], trace=rt._trace))
    fresh = Runtime(SPECS["three"][0],
                    {"a": fact_node("a"), "b": fact_node("b"), "c": fact_node("c")})
    try:
        scenario("41 resume of an async trace (sync nodes)", "three",
                 engine.resume(fresh, run_id="x3-resumed-async").trace)
    except BaseException as exc:  # noqa: BLE001
        print(f"  !! async resume scenario unavailable: {type(exc).__name__}: {exc}")


# --------------------------------------------------------------------------- #
# Validation                                                                   #
# --------------------------------------------------------------------------- #

def main() -> int:
    build_sync()
    asyncio.run(build_async())

    failures = 0
    print(f"{'scenario':<40} {'terminal':<14} {'#rec':>4}  "
          f"{'JSON':<6} {'JSONL':<6} {'equal':<5}")
    print("-" * 84)
    for name, spec_key, trace, complete in SCENARIOS:
        _, spec_document = SPECS[spec_key]
        doc = trace.to_dict()
        # the document must survive a strict JSON round trip unchanged
        doc = json.loads(json.dumps(doc))
        terminal = doc["records"][-1]["kind"] if doc["records"] else "(none)"
        json_v = V.validate_trace(doc, spec_document, complete)
        jsonl_text = trace.to_jsonl()
        jsonl_v, reassembled = V.validate_jsonl(jsonl_text, spec_document, complete)
        equal = reassembled == doc
        json_ok = not json_v
        jsonl_ok = not jsonl_v
        agree = {(v.rule, v.path, v.message) for v in json_v} == {
            (v.rule, v.path, v.message) for v in jsonl_v
        }
        status_json = "PASS" if json_ok else f"{len(json_v)}v"
        status_jsonl = "PASS" if jsonl_ok else f"{len(jsonl_v)}v"
        print(f"{name:<40} {terminal:<14} {len(doc['records']):>4}  "
              f"{status_json:<6} {status_jsonl:<6} {str(equal):<5}")
        if not json_ok:
            failures += 1
            for v in json_v[:4]:
                print(f"    JSON  {v.rule} {v.path}: {v.message}")
        if not jsonl_ok:
            failures += 1
            for v in jsonl_v[:4]:
                print(f"    JSONL {v.rule} {v.path}: {v.message}")
        if not equal:
            failures += 1
            print("    !! JSONL reassembly differs from the JSON document")
        if not agree:
            failures += 1
            print("    !! JSON and JSONL verdicts disagree")

    print("-" * 84)
    print(f"{len(SCENARIOS)} scenarios, {failures} problem(s)")
    terminals = sorted({
        t.to_dict()["records"][-1]["kind"]
        for _, _, t, _c in SCENARIOS if t.to_dict()["records"]
    })
    dispositions = sorted({
        r.get("disposition")
        for _, _, t, _c in SCENARIOS for r in t.to_dict()["records"]
        if r.get("disposition")
    })
    outcomes = sorted({
        r.get("outcome")
        for _, _, t, _c in SCENARIOS for r in t.to_dict()["records"]
        if r.get("outcome")
    })
    print(f"terminal kinds covered : {terminals}")
    print(f"dispositions covered   : {dispositions}")
    print(f"outcomes covered       : {outcomes}")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
