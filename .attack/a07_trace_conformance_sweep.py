"""Attack E: does the shipped runtime actually emit contract-valid evidence?

Generates a broad matrix of real runtime executions, then validates each
produced trace with contract/validate.py in BOTH the JSON and the JSONL form
and compares the two verdicts.  Also exercises byte-level stream attacks
(CRLF, truncation, bad UTF-8) against the same evidence.
"""

from __future__ import annotations

import importlib.util
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
spec_mod = importlib.util.spec_from_file_location("motus_validate", ROOT / "contract" / "validate.py")
validate = importlib.util.module_from_spec(spec_mod)
sys.modules["motus_validate"] = validate
spec_mod.loader.exec_module(validate)

from vitruvyan_motus import (  # noqa: E402
    Decision, DurabilityProfile, EffectClass, EffectDescriptor, EffectReceipt,
    Fact, GraphSpec, InMemoryTraceSink, NodeFailed, Policy, Rejection,
    ReplayEngine, ReplayStatus, Runtime, State, TraceBundle, redact,
)

NOW = datetime(2026, 8, 4, tzinfo=timezone.utc)
ident = lambda s: s

LINEAR = {
    "schema_version": "1.0.0", "name": "sweep", "version": "1.0.0", "entry": "a",
    "nodes": [{"name": "a", "effect_class": "pure"}, {"name": "b", "effect_class": "pure"}],
    "transitions": {"a": {"kind": "next", "to": "b"}, "b": {"kind": "terminal"}},
}
ROUTED = {
    "schema_version": "1.0.0", "name": "sweep-r", "version": "1.0.0", "entry": "a",
    "nodes": [{"name": "a", "effect_class": "pure"}, {"name": "x", "effect_class": "pure"},
              {"name": "y", "effect_class": "pure"}],
    "transitions": {"a": {"kind": "route", "on": "k", "map": {"one": "x"}, "default": "y"},
                    "x": {"kind": "terminal"}, "y": {"kind": "terminal"}},
}
ROUTED_NO_DEFAULT = {
    "schema_version": "1.0.0", "name": "sweep-m", "version": "1.0.0", "entry": "a",
    "nodes": [{"name": "a", "effect_class": "pure"}, {"name": "x", "effect_class": "pure"}],
    "transitions": {"a": {"kind": "route", "on": "k", "map": {"one": "x"}},
                    "x": {"kind": "terminal"}},
}
EXTERNAL = {
    "schema_version": "1.0.0", "name": "sweep-e", "version": "1.0.0", "entry": "a",
    "nodes": [{"name": "a", "effect_class": "external_effect"},
              {"name": "b", "effect_class": "recorded_effect"}],
    "transitions": {"a": {"kind": "next", "to": "b"}, "b": {"kind": "terminal"}},
}
DECLARED = {
    "schema_version": "1.0.0", "name": "sweep-d", "version": "1.0.0", "entry": "a",
    "nodes": [{"name": "a", "effect_class": "pure", "reads_declared": ["seen"],
               "writes_declared": ["ok"]},
              {"name": "b", "effect_class": "pure"}],
    "transitions": {"a": {"kind": "next", "to": "b"}, "b": {"kind": "terminal"}},
}
CYCLE = {
    "schema_version": "1.0.0", "name": "sweep-c", "version": "1.0.0", "entry": "a",
    "max_transitions": 3,
    "nodes": [{"name": "a", "effect_class": "pure"}, {"name": "b", "effect_class": "pure"},
              {"name": "z", "effect_class": "pure"}],
    "transitions": {"a": {"kind": "route", "on": "k", "map": {"loop": "b", "stop": "z"}},
                    "b": {"kind": "next", "to": "a"}, "z": {"kind": "terminal"}},
}


def decide(value):
    def node(state):
        return state.with_decision(Decision("k", value, NOW))
    return node


def writer(key, value, source="s"):
    def node(state):
        return state.with_fact(Fact(key, value, source, NOW))
    return node


def raiser(state):
    raise RuntimeError("boom")


def reader_undeclared(state):
    state.fact("not_declared")
    return state.with_fact(Fact("also_not", 1, "s", NOW))


def drawer(state, ctx):
    return state.with_fact(Fact("t", ctx.uuid(), "s", ctx.now()))


def redactor(state):
    return state.with_fact(Fact("secret", redact({"pw": "hunter2"}, "policy://pii"), "s", NOW))


def effectful(state, ctx):
    ctx.record_effect(EffectDescriptor(
        EffectClass.EXTERNAL_EFFECT, "POST /o", idempotency_key="k1",
        receipt=EffectReceipt("r1", "completed",
                              "effect:sha256:" + "ab" * 32)))
    return state.with_fact(Fact("posted", True, "s", NOW))


def recorded(state, ctx):
    ctx.record_effect(EffectDescriptor(EffectClass.RECORDED_EFFECT, "GET /o"))
    return state


CASES: list[tuple[str, callable]] = []


def case(name):
    def register(fn):
        CASES.append((name, fn))
        return fn
    return register


@case("happy linear")
def _(): return Runtime(GraphSpec.from_dict(LINEAR), {"a": writer("x", 1), "b": ident}).run(State.empty("i"))


@case("nested + unicode + float values")
def _():
    payload = {"deep": {"list": [1, 2.5, True, None, "ünïcode ✓"]}, "empty": {}}
    return Runtime(GraphSpec.from_dict(LINEAR), {"a": writer("x", payload), "b": ident}).run(State.empty("i"))


@case("redacted value")
def _(): return Runtime(GraphSpec.from_dict(LINEAR), {"a": redactor, "b": ident}).run(State.empty("i"))


@case("context draws")
def _(): return Runtime(GraphSpec.from_dict(LINEAR), {"a": drawer, "b": ident}).run(
    State.empty("i"), replay=ReplayStatus.declared("full"))


@case("retry then success")
def _():
    n = [0]

    def flaky(state):
        n[0] += 1
        if n[0] < 3:
            raise ValueError("later")
        return state.with_fact(Fact("x", n[0], "s", NOW))
    return Runtime(GraphSpec.from_dict(LINEAR), {"a": flaky, "b": ident},
                   max_attempts={"a": 3, "b": 1}).run(State.empty("i"))


@case("retry exhaustion -> NodeFailed")
def _():
    rt = Runtime(GraphSpec.from_dict(LINEAR), {"a": raiser, "b": ident}, max_attempts={"a": 2, "b": 1})
    try:
        rt.run(State.empty("i"))
    except NodeFailed as exc:
        return exc
    raise AssertionError


@case("exploration continues past failure")
def _(): return Runtime(GraphSpec.from_dict(LINEAR), {"a": raiser, "b": ident},
                        policy=Policy.EXPLORATION).run(State.empty("i"))


@case("route matched")
def _(): return Runtime(GraphSpec.from_dict(ROUTED), {"a": decide("one"), "x": ident, "y": ident}).run(State.empty("i"))


@case("route default")
def _(): return Runtime(GraphSpec.from_dict(ROUTED), {"a": decide("zzz"), "x": ident, "y": ident}).run(State.empty("i"))


@case("route miss strict -> failed")
def _(): return Runtime(GraphSpec.from_dict(ROUTED_NO_DEFAULT), {"a": ident, "x": ident}).run(State.empty("i"))


@case("route miss exploration -> completed")
def _(): return Runtime(GraphSpec.from_dict(ROUTED_NO_DEFAULT), {"a": ident, "x": ident},
                        policy=Policy.EXPLORATION).run(State.empty("i"))


@case("non-string decision -> miss")
def _(): return Runtime(GraphSpec.from_dict(ROUTED), {"a": decide(1), "x": ident, "y": ident},
                        policy=Policy.EXPLORATION).run(State.empty("i"))


@case("transition limit exceeded")
def _(): return Runtime(GraphSpec.from_dict(CYCLE), {"a": decide("loop"), "b": ident, "z": ident}).run(State.empty("i"))


@case("external + recorded effects")
def _(): return Runtime(GraphSpec.from_dict(EXTERNAL), {"a": effectful, "b": recorded}).run(State.empty("i"))


@case("declaration violation strict -> failed")
def _():
    rt = Runtime(GraphSpec.from_dict(DECLARED), {"a": reader_undeclared, "b": ident})
    try:
        rt.run(State.empty("i"))
    except NodeFailed as exc:
        return exc
    raise AssertionError


@case("declaration violation exploration -> recorded")
def _(): return Runtime(GraphSpec.from_dict(DECLARED), {"a": reader_undeclared, "b": ident},
                        policy=Policy.EXPLORATION).run(State.empty("i"))


@case("cancelled mid-stream")
def _():
    driver = Runtime(GraphSpec.from_dict(LINEAR), {"a": writer("x", 1), "b": ident}).stream(State.empty("i"))
    next(driver)
    next(driver)
    driver.close("stopped")
    return driver


@case("seeded initial state")
def _():
    seed = State.new("i", facts=[Fact("f", 1, "s", NOW)],
                     decisions=[Decision("k", "one", NOW)],
                     metadata={"actor": "auditor", "ruleset_version": "1"})
    return Runtime(GraphSpec.from_dict(ROUTED), {"a": ident, "x": ident, "y": ident}).run(seed)


@case("resume segment")
def _():
    driver = Runtime(GraphSpec.from_dict(LINEAR), {"a": writer("x", 1), "b": ident}).stream(State.empty("i"))
    for _ in range(3):
        next(driver)
    trace = driver.trace
    driver.close()
    engine = ReplayEngine(TraceBundle(GraphSpec.from_dict(LINEAR), trace))
    return engine.resume(Runtime(GraphSpec.from_dict(LINEAR), {"a": writer("x", 1), "b": ident}))


@case("buffered durability profile")
def _():
    sink = InMemoryTraceSink()
    return Runtime(GraphSpec.from_dict(LINEAR), {"a": writer("x", 1), "b": ident},
                   durability_profile=DurabilityProfile.BUFFERED, sink=sink,
                   chunk_records=2, flush_interval_ms=50).run(State.empty("i"))


@case("synchronous durability profile")
def _():
    sink = InMemoryTraceSink()
    return Runtime(GraphSpec.from_dict(LINEAR), {"a": writer("x", 1), "b": ident},
                   durability_profile=DurabilityProfile.SYNCHRONOUS, sink=sink).run(State.empty("i"))


@case("rejection with evidence")
def _():
    def node(state):
        return state.with_rejection(Rejection("w", "r", NOW, evidence={"n": 1}))
    return Runtime(GraphSpec.from_dict(LINEAR), {"a": node, "b": ident}).run(State.empty("i"))


@case("rejection WITHOUT evidence")
def _():
    def node(state):
        return state.with_rejection(Rejection("w", "r", NOW))
    return Runtime(GraphSpec.from_dict(LINEAR), {"a": node, "b": ident}).run(State.empty("i"))


SPECS = {
    "sweep": LINEAR, "sweep-r": ROUTED, "sweep-m": ROUTED_NO_DEFAULT,
    "sweep-e": EXTERNAL, "sweep-d": DECLARED, "sweep-c": CYCLE,
}

failures = 0
for name, build in CASES:
    try:
        obj = build()
    except Exception as exc:  # noqa: BLE001
        print(f"  BUILD-ERR {name}: {type(exc).__name__}: {exc}")
        failures += 1
        continue
    trace = obj.trace
    try:
        document = trace.to_dict()
        jsonl = trace.to_jsonl()
    except Exception as exc:  # noqa: BLE001
        print(f"  SERIALIZE-ERR {name}: {type(exc).__name__}: {exc}")
        failures += 1
        continue
    graph_spec = SPECS[document["run"]["graph"]["name"]]
    complete = document["records"][-1]["kind"] in ("run_completed", "run_failed", "run_cancelled")
    json_v = validate.validate_trace(document, spec=graph_spec, expect_complete=complete)
    jsonl_v, _ = validate.validate_jsonl(jsonl, spec=graph_spec, expect_complete=complete)
    same = sorted((v.rule, v.path, v.message) for v in json_v) == \
        sorted((v.rule, v.path, v.message) for v in jsonl_v)
    status = "OK " if not json_v and not jsonl_v else "BAD"
    if json_v or jsonl_v or not same:
        failures += 1
    print(f"  {status} {name:42} json={len(json_v):2} jsonl={len(jsonl_v):2} "
          f"equivalent={'yes' if same else 'NO'} records={len(document['records'])}")
    for v in json_v[:3]:
        print(f"       json  {v.rule} {v.path}: {v.message[:110]}")
    for v in jsonl_v[:3]:
        if not same:
            print(f"       jsonl {v.rule} {v.path}: {v.message[:110]}")

print(f"\n{len(CASES)} cases, {failures} problematic")

print("\n=== byte-level stream attacks on a valid trace ===")
good = Runtime(GraphSpec.from_dict(LINEAR), {"a": writer("x", 1), "b": ident}).run(State.empty("i"))
stream = good.trace.to_jsonl()
attacks = {
    "pristine": stream,
    "CRLF line endings": stream.replace("\n", "\r\n"),
    "truncated final line": stream[: -len(stream.splitlines()[-1]) - 5],
    "no trailing newline": stream.rstrip("\n"),
    "blank line inserted": stream.replace("\n", "\n\n", 1),
    "duplicated terminal line": stream + stream.splitlines()[-1] + "\n",
}
for label, text in attacks.items():
    v, _ = validate.validate_jsonl(text, spec=LINEAR)
    print(f"   {label:24}: {len(v)} violation(s) {[x.rule for x in v][:4]}")

print("\n=== malformed UTF-8 through the documented CLI path ===")
import subprocess, tempfile  # noqa: E402
with tempfile.TemporaryDirectory() as tmp:
    bad = Path(tmp) / "bad.jsonl"
    bad.write_bytes(stream.encode("utf-8")[:60] + b"\xff\xfe" + stream.encode("utf-8")[60:])
    out = subprocess.run([sys.executable, str(ROOT / "contract" / "validate.py"), "jsonl", str(bad)],
                         capture_output=True, text=True)
    print("   exit:", out.returncode, "|", (out.stderr or out.stdout).strip()[:120])
