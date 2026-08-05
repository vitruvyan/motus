"""Attacks H + I: replay, resume, non-determinism control, concurrency."""

from __future__ import annotations

import copy
import json
import threading
import time
from datetime import datetime, timezone

from vitruvyan_motus import (
    Decision, EffectClass, EffectDescriptor, EffectReceipt, Fact, GraphSpec,
    Policy, ReplayEngine, ReplayStatus, Runtime, State, TraceBundle,
    UnsafeResume, ReplayMismatch,
)

NOW = datetime(2026, 8, 4, tzinfo=timezone.utc)
ident = lambda s: s

LINEAR = GraphSpec.from_dict({
    "schema_version": "1.0.0", "name": "h", "version": "1.0.0", "entry": "a",
    "nodes": [{"name": "a", "effect_class": "pure"}, {"name": "b", "effect_class": "pure"}],
    "transitions": {"a": {"kind": "next", "to": "b"}, "b": {"kind": "terminal"}},
})


def w(key, value):
    def node(state):
        return state.with_fact(Fact(key, value, "s", NOW))
    return node


print("=== H1: replay degradation for opaque / unavailable node identity ===")


class Opaque:
    def __call__(self, state):
        return state


for label, registry in (
    ("all plain functions", {"a": w("x", 1), "b": ident}),
    ("one opaque instance", {"a": Opaque(), "b": ident}),
    ("one eval'd (no source)", {"a": eval("lambda state: state"), "b": ident}),
):
    out = Runtime(LINEAR, registry).run(State.empty("h1"), replay=ReplayStatus.declared("full"))
    print(f"   {label:24} header={out.trace.run['replay']} "
          f"terminal={out.trace.records[-1]['replay']}")

print("\n=== H2: ambient nondeterminism escaping RunContext ===")


def ambient(state):
    import random as _r
    return state.with_fact(Fact("r", _r.random(), "s", datetime.now(timezone.utc)))


out = Runtime(LINEAR, {"a": ambient, "b": ident}).run(
    State.empty("h2"), replay=ReplayStatus.declared("full"))
txn = [r for r in out.trace.records if r["kind"] == "transition"][0]
print("   declared full, node used ambient random+clock")
print("   context_draws =", txn["context_draws"], "| terminal replay =",
      out.trace.records[-1]["replay"])

print("\n=== H3: verify() catches an impure 'pure' node ===")
counter = [0]


def drifting(state, ctx):
    counter[0] += 1
    return state.with_fact(Fact("n", counter[0], "s", NOW))


out = Runtime(LINEAR, {"a": drifting, "b": ident}).run(State.empty("h3"))
bundle = TraceBundle(LINEAR, out.trace)
try:
    ReplayEngine(bundle).verify({"a": drifting, "b": ident})
    print("   verify() PASSED an impure node  <-- unexpected")
except ReplayMismatch as exc:
    print("   verify() ->", exc)

print("\n=== H4: replay with a different graph fingerprint ===")
OTHER = GraphSpec.from_dict({
    "schema_version": "1.0.0", "name": "h", "version": "1.0.1", "entry": "a",
    "nodes": [{"name": "a", "effect_class": "pure"}, {"name": "b", "effect_class": "pure"}],
    "transitions": {"a": {"kind": "next", "to": "b"}, "b": {"kind": "terminal"}},
})
out = Runtime(LINEAR, {"a": w("x", 1), "b": ident}).run(State.empty("h4"))
try:
    TraceBundle(OTHER, out.trace)
    print("   bundle with mismatched spec ACCEPTED  <-- unexpected")
except ValueError as exc:
    print("   bundle with mismatched spec ->", exc)

print("\n=== H5: resume from a terminal / failed / cancelled run ===")
bundle = TraceBundle(LINEAR, out.trace)
try:
    ReplayEngine(bundle).resume(Runtime(LINEAR, {"a": w("x", 1), "b": ident}))
    print("   resume from a completed run ACCEPTED  <-- unexpected")
except UnsafeResume as exc:
    print("   resume from a completed run ->", exc)

print("\n=== H6: resume from an incomplete (stream-cancelled) run ===")
driver = Runtime(LINEAR, {"a": w("x", 1), "b": ident}).stream(State.empty("h6"))
records = [next(driver), next(driver), next(driver)]     # started, attempt, transition
partial_trace = driver.trace
print("   stopped after kinds:", [r["kind"] for r in partial_trace.records])
try:
    b = TraceBundle(LINEAR, partial_trace)
    engine = ReplayEngine(b)
    resumed = engine.resume(Runtime(LINEAR, {"a": w("x", 1), "b": ident}))
    print("   resume ->", resumed.status,
          "| start:", resumed.trace.run["resume"]["start_node"],
          "| causation:", resumed.trace.run["metadata"].get("causation_id"))
    print("   new run_id != source:", resumed.trace.run["run_id"] != partial_trace.run["run_id"])
    print("   resumed state facts :", {f.key: f.value for f in resumed.state.facts})
except Exception as exc:  # noqa: BLE001
    print(f"   resume -> {type(exc).__name__}: {exc}")
driver.close()

print("\n=== H7: resume with seeded state contradicting the trace ===")
# Forge a bundle whose spec is identical but whose committed writes disagree
# with what the routing record says was observed.
ROUTED = GraphSpec.from_dict({
    "schema_version": "1.0.0", "name": "r", "version": "1.0.0", "entry": "a",
    "nodes": [{"name": "a", "effect_class": "pure"}, {"name": "x", "effect_class": "pure"},
              {"name": "y", "effect_class": "pure"}],
    "transitions": {"a": {"kind": "route", "on": "k", "map": {"one": "x", "two": "y"}},
                    "x": {"kind": "terminal"}, "y": {"kind": "terminal"}},
})


def decide(state):
    return state.with_decision(Decision("k", "one", NOW))


out = Runtime(ROUTED, {"a": decide, "x": ident, "y": ident}).run(State.empty("h7"))
doc = out.trace.to_dict()
# tamper: redirect the recorded route to the other declared node
for record in doc["records"]:
    if record["kind"] == "routing":
        record["selected"] = "y"
        for candidate in record["candidates"]:
            candidate["taken"] = candidate["target"] == "y"
from vitruvyan_motus import Trace  # noqa: E402
try:
    TraceBundle(ROUTED, Trace.from_dict(doc))
    print("   forged route target ACCEPTED  <-- unexpected")
except ValueError as exc:
    print("   forged route target ->", exc)

# tamper: change the committed decision value so the recorded route no longer follows
doc2 = out.trace.to_dict()
for record in doc2["records"]:
    if record["kind"] == "transition" and record["writes"]["decisions"]:
        record["writes"]["decisions"][0]["value"] = "two"
try:
    TraceBundle(ROUTED, Trace.from_dict(doc2))
    print("   forged decision value ACCEPTED  <-- unexpected")
except ValueError as exc:
    print("   forged decision value ->", exc)

print("\n=== H8: external effect without receipt blocks resume ===")
EXT = GraphSpec.from_dict({
    "schema_version": "1.0.0", "name": "x", "version": "1.0.0", "entry": "a",
    "nodes": [{"name": "a", "effect_class": "external_effect"},
              {"name": "b", "effect_class": "pure"}],
    "transitions": {"a": {"kind": "next", "to": "b"}, "b": {"kind": "terminal"}},
})
for label, key, status in (("no key", None, "completed"), ("unknown receipt", "k", "unknown"),
                           ("good", "k", "completed")):
    def make_eff(key, status):
        def eff(state, ctx):
            ctx.record_effect(EffectDescriptor(
                EffectClass.EXTERNAL_EFFECT, "POST", idempotency_key=key,
                receipt=EffectReceipt("r", status)))
            return state
        return eff
    eff = make_eff(key, status)
    driver = Runtime(EXT, {"a": eff, "b": ident}).stream(State.empty("h8"))
    for _ in range(3):
        next(driver)
    trace = driver.trace
    driver.close()
    try:
        engine = ReplayEngine(TraceBundle(EXT, trace))
        engine._assert_effect_safe()
        print(f"   {label:16}: resume allowed")
    except UnsafeResume as exc:
        print(f"   {label:16}: blocked -> {exc}")

print("\n=== I1: overlapping runs on one Runtime (forced overlap) ===")
slow_started = threading.Event()


def slow(state):
    slow_started.set()
    time.sleep(0.4)
    return state.with_fact(Fact("slow", 1, "s", NOW))


rt = Runtime(LINEAR, {"a": slow, "b": ident})
outcomes, errors = [], []


def first():
    try:
        outcomes.append(("t1", rt.run(State.empty("i1a")).status))
    except BaseException as exc:  # noqa: BLE001
        errors.append(("t1", f"{type(exc).__name__}: {exc}"))


def second():
    slow_started.wait(2)
    try:
        outcomes.append(("t2", rt.run(State.empty("i1b")).status))
    except BaseException as exc:  # noqa: BLE001
        errors.append(("t2", f"{type(exc).__name__}: {exc}"))


threads = [threading.Thread(target=first), threading.Thread(target=second)]
for t in threads:
    t.start()
for t in threads:
    t.join()
print("   outcomes =", outcomes)
print("   errors   =", errors)

print("\n=== I2: many repetitions of the overlap race ===")
races = {"guard fired": 0, "both completed": 0, "other": 0}
for _ in range(200):
    rt = Runtime(LINEAR, {"a": lambda s: s, "b": ident})
    box = []

    def go():
        try:
            box.append(rt.run(State.empty("r")).status)
        except RuntimeError:
            box.append("GUARD")
        except BaseException as exc:  # noqa: BLE001
            box.append(type(exc).__name__)

    ts = [threading.Thread(target=go) for _ in range(2)]
    for t in ts:
        t.start()
    for t in ts:
        t.join()
    if "GUARD" in box:
        races["guard fired"] += 1
    elif box == ["completed", "completed"]:
        races["both completed"] += 1
    else:
        races["other"] += 1
print("   200 iterations:", races)
