"""Attacks D, F, G: retry, failure, cancellation, sinks, observers."""

from __future__ import annotations

import json
from datetime import datetime, timezone

from vitruvyan_motus import (
    DurabilityProfile, EffectClass, EffectDescriptor, EffectReceipt, Fact,
    GraphSpec, InMemoryTraceSink, Policy, Runtime, SinkFailed, State,
)

NOW = datetime(2026, 8, 4, tzinfo=timezone.utc)


def spec(nodes=None, transitions=None):
    return GraphSpec.from_dict({
        "schema_version": "1.0.0", "name": "d", "version": "1.0.0", "entry": "a",
        "nodes": nodes or [{"name": "a", "effect_class": "pure"},
                           {"name": "b", "effect_class": "pure"}],
        "transitions": transitions or {"a": {"kind": "next", "to": "b"},
                                       "b": {"kind": "terminal"}},
    })


LINEAR = spec()
SINGLE = spec([{"name": "a", "effect_class": "pure"}], {"a": {"kind": "terminal"}})
ident = lambda s: s


def kinds(trace):
    return [r["kind"] for r in trace.records]


print("=== D1: a node raising BaseException (not Exception) ===")


def base_boom(state):
    raise KeyboardInterrupt("hard interrupt inside the node")


rt = Runtime(SINGLE, {"a": base_boom})
try:
    rt.run(State.empty("d1"))
    print("   run returned normally (unexpected)")
except BaseException as exc:  # noqa: BLE001
    print(f"   run raised {type(exc).__name__}")
    trace = rt.trace
    print("   record kinds       :", kinds(trace))
    print("   terminal recorded  :", any(k in ("run_completed", "run_failed", "run_cancelled")
                                         for k in kinds(trace)))
    print("   trace serializable :", isinstance(trace.to_json(), str))

print("\n=== D2: two terminal outcomes / execution after terminal? ===")
r = Runtime(LINEAR, {"a": ident, "b": ident}).run(State.empty("d2"))
terminals = [k for k in kinds(r.trace) if k in ("run_completed", "run_failed", "run_cancelled")]
print("   terminals =", terminals, "last =", kinds(r.trace)[-1])

print("\n=== D3: retry exhaustion, attempt numbering, NodeFailed.state ===")
calls = [0]


def flaky(state):
    calls[0] += 1
    raise RuntimeError(f"attempt {calls[0]}")


from vitruvyan_motus import NodeFailed  # noqa: E402

rt = Runtime(LINEAR, {"a": flaky, "b": ident}, max_attempts={"a": 3, "b": 1})
try:
    rt.run(State.empty("d3"))
except NodeFailed as exc:
    attempts = [(r["node"], r["attempt"], r["outcome"], r["disposition"])
                for r in rt.trace.records if r["kind"] == "transition"]
    print("   attempts   :", attempts)
    print("   kinds      :", kinds(rt.trace))
    print("   .state type:", type(exc.state).__name__, "| .node =", exc.node)

print("\n=== D4: cancellation raised from inside a Listener ===")


class CancellingListener:
    def __init__(self, runtime_box, after_kind):
        self.box, self.after = runtime_box, after_kind
        self.seen = []

    def on_record(self, record):
        self.seen.append(record["kind"])
        if record["kind"] == self.after:
            self.box[0].cancel("cancelled from a listener")


box = [None]
listener = CancellingListener(box, "run_started")
rt = Runtime(LINEAR, {"a": ident, "b": ident}, listeners=(listener,))
box[0] = rt
r = rt.run(State.empty("d4"))
print("   status        :", r.status)
print("   kinds         :", kinds(r.trace))
print("   active_attempt:", r.trace.records[-1].get("active_attempt"))
print("   -> a Listener DID gate execution:", r.status == "cancelled")

print("\n=== D5: listener that raises (must never affect execution) ===")


class Exploding:
    def on_record(self, record):
        raise SystemExit("listener detonates")


r = Runtime(LINEAR, {"a": ident, "b": ident}, listeners=(Exploding(),)).run(State.empty("d5"))
print("   status =", r.status, "| records =", len(r.trace.records))

print("\n=== D6: sink failures at controlled points (synchronous) ===")


class FailingRunSink:
    def __init__(self, fail_on):
        self.fail_on, self.written = fail_on, []

    def write(self, records):
        for record in records:
            if self.fail_on(record):
                raise OSError(f"sink refuses {record['kind']} seq={record['seq']}")
            self.written.append(record)


class FailingSink:
    def __init__(self, fail_on, open_fails=False):
        self.run_sink = FailingRunSink(fail_on)
        self.open_fails = open_fails

    def open_run(self, header):
        if self.open_fails:
            raise OSError("sink refuses to open the run")
        return self.run_sink


scenarios = {
    "refuse open_run": FailingSink(lambda r: False, open_fails=True),
    "refuse first record": FailingSink(lambda r: r["kind"] == "run_started"),
    "refuse mid-trace": FailingSink(lambda r: r["kind"] == "routing"),
    "refuse terminal": FailingSink(lambda r: r["kind"] == "run_completed"),
}
for label, sink in scenarios.items():
    rt = Runtime(LINEAR, {"a": ident, "b": ident},
                 durability_profile=DurabilityProfile.SYNCHRONOUS, sink=sink)
    try:
        out = rt.run(State.empty("d6"))
        print(f"   {label:22}: returned status={out.status}  <-- NO SinkFailed")
    except SinkFailed as exc:
        persisted = [r["kind"] for r in sink.run_sink.written]
        mem = kinds(rt.trace)
        print(f"   {label:22}: SinkFailed | persisted={persisted} | in-memory={mem}")

print("\n=== D7: buffered profile — is failure evidence really outside the loss window? ===")


class CountingRunSink:
    def __init__(self):
        self.batches = []

    def write(self, records):
        self.batches.append([r["kind"] for r in records])


class CountingSink:
    def __init__(self):
        self.run_sink = CountingRunSink()

    def open_run(self, header):
        return self.run_sink


def raiser(state):
    raise ValueError("nope")


sink = CountingSink()
rt = Runtime(LINEAR, {"a": raiser, "b": ident},
             durability_profile=DurabilityProfile.BUFFERED, sink=sink,
             chunk_records=1000, flush_interval_ms=600000)
try:
    rt.run(State.empty("d7"))
except NodeFailed:
    pass
print("   flush batches:", sink.run_sink.batches)
print("   header sink object:", rt.trace.run.get("sink"))

print("\n=== D8: effects — pure node emitting an effect, and retry duplication ===")
EFFECT_SPEC = GraphSpec.from_dict({
    "schema_version": "1.0.0", "name": "e", "version": "1.0.0", "entry": "a",
    "nodes": [{"name": "a", "effect_class": "external_effect"}],
    "transitions": {"a": {"kind": "terminal"}},
})
seen = [0]


def effectful(state, ctx):
    seen[0] += 1
    ctx.record_effect(EffectDescriptor(
        EffectClass.EXTERNAL_EFFECT, f"POST /orders #{seen[0]}",
        idempotency_key="k-1",
        receipt=EffectReceipt("r-1", "completed"),
    ))
    if seen[0] < 3:
        raise RuntimeError("transient")
    return state.with_fact(Fact("done", True, "s", NOW))


r = Runtime(EFFECT_SPEC, {"a": effectful}, max_attempts=3).run(State.empty("d8"))
for rec in r.trace.records:
    if rec["kind"] == "transition":
        print(f"   attempt {rec['attempt']} outcome={rec['outcome']:8} "
              f"effects={[e['description'] for e in rec['effects']]}")

print("\n=== D9: pure node that records an effect ===")
PURE_SPEC = GraphSpec.from_dict({
    "schema_version": "1.0.0", "name": "p", "version": "1.0.0", "entry": "a",
    "nodes": [{"name": "a", "effect_class": "pure"}],
    "transitions": {"a": {"kind": "terminal"}},
})


def sneaky(state, ctx):
    try:
        ctx.record_effect(EffectDescriptor(EffectClass.RECORDED_EFFECT, "sneak"))
    except Exception as exc:  # noqa: BLE001
        return state.with_fact(Fact("blocked", type(exc).__name__, "s", NOW))
    return state.with_fact(Fact("blocked", "NO", "s", NOW))


r = Runtime(PURE_SPEC, {"a": sneaky}).run(State.empty("d9"))
print("   blocked =", r.state.fact("blocked"), "| status =", r.status)

print("\n=== D10: InMemoryTraceSink under in-memory profile receives nothing ===")
sink = InMemoryTraceSink()
r = Runtime(LINEAR, {"a": ident, "b": ident},
            durability_profile=DurabilityProfile.IN_MEMORY, sink=sink).run(State.empty("d10"))
print("   sink runs =", len(sink.runs), "| header =", sink.header is not None,
      "| records =", len(sink.records))
