"""Re-audit Phase 3: state index, RunContext binding, identity, sinks, listeners.

Also carries corrected variants of three Phase-2 probes whose first form
measured the harness rather than the product.
"""

from __future__ import annotations

import functools
import importlib.util
import json
import pickle
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
_spec = importlib.util.spec_from_file_location("rv3", ROOT / "contract" / "validate.py")
validate = importlib.util.module_from_spec(_spec)
sys.modules["rv3"] = validate
_spec.loader.exec_module(validate)

from vitruvyan_motus import (  # noqa: E402
    Decision, DurabilityProfile, Fact, GraphSpec, InMemoryTraceSink, NodeFailed,
    Policy, Rejection, Runtime, SinkFailed, State,
)

NOW = datetime(2026, 8, 4, tzinfo=timezone.utc)
ident = lambda s: s

LINEAR_DOC = {
    "schema_version": "1.0.0", "name": "p3", "version": "1.0.0", "entry": "a",
    "nodes": [{"name": "a", "effect_class": "pure"}, {"name": "b", "effect_class": "pure"}],
    "transitions": {"a": {"kind": "next", "to": "b"}, "b": {"kind": "terminal"}},
}
LINEAR = GraphSpec.from_dict(dict(LINEAR_DOC))
SINGLE = GraphSpec.from_dict({
    "schema_version": "1.0.0", "name": "p3s", "version": "1.0.0", "entry": "a",
    "nodes": [{"name": "a", "effect_class": "pure"}],
    "transitions": {"a": {"kind": "terminal"}},
})

# ===================== corrected Phase-2 probes ============================ #
print("=== B3': END selected exactly at the limit must COMPLETE ===")
doc = {"schema_version": "1.0.0", "name": "endlim", "version": "1.0.0", "entry": "a",
       "max_transitions": 1,
       "nodes": [{"name": "a", "effect_class": "pure"}],
       "transitions": {"a": {"kind": "terminal"}}}
out = Runtime(GraphSpec.from_dict(doc), {"a": ident}).run(State.empty("b3"))
print(f"   terminal-at-limit-1: status={out.status} kinds={[r['kind'] for r in out.trace.records]}")
print(f"   validator: {len(validate.validate_trace(out.trace.to_dict(), spec=doc))} violation(s)")

print("\n=== B5': E11 catches a spec/trace activation-count disagreement ===")
CYCLE_DOC = {
    "schema_version": "1.0.0", "name": "cyc", "version": "1.0.0", "entry": "a",
    "max_transitions": 4,
    "nodes": [{"name": n, "effect_class": "pure"} for n in ("a", "b", "z")],
    "transitions": {"a": {"kind": "route", "on": "k", "map": {"loop": "b", "stop": "z"}},
                    "b": {"kind": "next", "to": "a"}, "z": {"kind": "terminal"}},
}
SEED = State.new("b5", decisions=[Decision("k", "loop", NOW)])
real = Runtime(GraphSpec.from_dict(dict(CYCLE_DOC)), {"a": ident, "b": ident, "z": ident},
               policy=Policy.EXPLORATION).run(SEED)
document = real.trace.to_dict()
for claimed in (3, 4, 5):
    spec_doc = json.loads(json.dumps(CYCLE_DOC))
    spec_doc["max_transitions"] = claimed
    v = validate.validate_trace(document, spec=spec_doc)
    rules = [x.rule for x in v]
    print(f"   spec claims max_transitions={claimed}: {len(v)} violation(s) {rules[:4]}")

print("\n=== C3': to_dict/to_json call order on ONE trace ===")
rt = Runtime(LINEAR, {"a": lambda s: s.with_fact(Fact("x", 1, "s", NOW)), "b": ident})
out = rt.run(State.empty("c3"), run_id="fixed")
d_first = out.trace.to_dict()
j_after = out.trace.to_json()
out2 = rt.run(State.empty("c3"), run_id="fixed")
j_first = out2.trace.to_json()
d_after = out2.trace.to_dict()


def strip(d):
    d = json.loads(json.dumps(d))
    d["run"].pop("created_ts", None)
    for r in d["records"]:
        r.pop("ts", None)
    return d


print("   dict-then-json vs json-then-dict identical:", strip(d_first) == strip(d_after))
print("   json output identical                     :",
      strip(json.loads(j_after)) == strip(json.loads(j_first)))

print("\n=== A1': is the pickle sentinel loss reachable through the API? ===")
r = pickle.loads(pickle.dumps(Rejection("w", "r", NOW)))


def declines_unpickled(state):
    return state.with_rejection(r)


try:
    out = Runtime(SINGLE, {"a": declines_unpickled}).run(State.empty("a1"))
    txn = next(x for x in out.trace.records if x["kind"] == "transition")
    ev = txn["writes"]["rejections"][0].get("evidence", "<absent>")
    print(f"   run status={out.status} evidence in trace = {type(ev).__name__} ({ev!r:.40})")
    try:
        out.trace.to_json()
        print("   to_json: OK")
    except Exception as exc:  # noqa: BLE001
        print(f"   to_json: {type(exc).__name__}: {exc}")
except Exception as exc:  # noqa: BLE001
    print(f"   refused at the boundary: {type(exc).__name__}: {exc}")

# ===================== state index ========================================= #
print("\n=== S1: chunk-boundary correctness (63/64/65/127/128/129) ===")


class CountingKey(str):
    comparisons = 0

    def __eq__(self, other):
        type(self).comparisons += 1
        return str.__eq__(self, other)

    def __ne__(self, other):
        return not self.__eq__(other)

    def __hash__(self):
        return str.__hash__(self)


bad = 0
for size in (1, 63, 64, 65, 127, 128, 129, 200):
    facts = [Fact(f"k{i}", i, "s", NOW) for i in range(size)]
    state = State.new("s1", facts=facts)
    for probe in (0, size // 2, size - 1):
        if state.fact(f"k{probe}") != probe:
            bad += 1
            print(f"   WRONG size={size} key=k{probe} -> {state.fact(f'k{probe}')}")
    CountingKey.comparisons = 0
    state.fact(CountingKey("k0"))
    oldest = CountingKey.comparisons
    CountingKey.comparisons = 0
    state.fact(CountingKey("nope"))
    absent = CountingKey.comparisons
    print(f"   size={size:4} oldest_key_comparisons={oldest:4} absent_key_comparisons={absent:4} "
          f"(bound 63)")
print(f"   incorrect lookups: {bad}")

print("\n=== S2: duplicate keys, identical values, namespace separation ===")
state = State.new("s2",
                  facts=[Fact("k", i, "s", NOW) for i in range(80)] +
                        [Fact("dup", "same", "s", NOW), Fact("dup", "same", "s", NOW)],
                  decisions=[Decision("k", "decision-value", NOW)])
print("   fact('k') is the LAST fact k        :", state.fact("k") == 79)
print("   decision('k') is separate namespace :", state.decision("k") == "decision-value")
print("   fact('dup') identical values        :", state.fact("dup") == "same")
latest = state._latest_decision("k")
print("   _latest_decision origin             :", latest[1])

print("\n=== S3: old snapshots keep their exact prior view across a chunk close ===")
base = State.new("s3", facts=[Fact(f"k{i}", i, "s", NOW) for i in range(63)])
snapshots = [base]
current = base
for step in range(63, 200):
    view = current._attempt_view(current._events)
    written = view.with_fact(Fact("moving", step, "s", NOW))
    current = view._committed(written, 100 + step)
    snapshots.append(current)
drift = 0
for index, snap in enumerate(snapshots):
    expected = None if index == 0 else 62 + index
    if snap.fact("moving") != expected:
        drift += 1
        print(f"   DRIFT snapshot {index}: moving={snap.fact('moving')} expected={expected}")
    if snap.fact("k0") != 0:
        drift += 1
        print(f"   DRIFT snapshot {index}: k0={snap.fact('k0')}")
print(f"   {len(snapshots)} snapshots re-checked after later commits; drift={drift}")

print("\n=== S4: branching two futures from one old snapshot ===")
root = State.new("s4", facts=[Fact(f"k{i}", i, "s", NOW) for i in range(64)])
va = root._attempt_view(root._events)
left = va._committed(va.with_fact(Fact("branch", "left", "s", NOW)), 1)
vb = root._attempt_view(root._events)
right = vb._committed(vb.with_fact(Fact("branch", "right", "s", NOW)), 2)
print("   left  :", left.fact("branch"), "| right:", right.fact("branch"),
      "| root:", root.fact("branch"))
print("   index objects distinct:", left._index is not right._index or len(root._log) % 64 != 0)

print("\n=== S5: scan order and origins are unchanged by the index ===")
state = State.new("s5", facts=[Fact(f"k{i}", i, "s", NOW) for i in range(70)])
print("   scan preserves append order:", [f.value for f in state.facts] == list(range(70)))
snap = state.snapshot()
print("   snapshot preserves order   :", [f["value"] for f in snap["facts"]] == list(range(70)))

print("\n=== S6: origin correctness for an indexed (old-chunk) decision ===")
ROUTED_DOC = {
    "schema_version": "1.0.0", "name": "s6", "version": "1.0.0", "entry": "a",
    "nodes": [{"name": "a", "effect_class": "pure"}, {"name": "x", "effect_class": "pure"},
              {"name": "y", "effect_class": "pure"}],
    "transitions": {"a": {"kind": "route", "on": "k", "map": {"one": "x", "two": "y"}},
                    "x": {"kind": "terminal"}, "y": {"kind": "terminal"}},
}
ROUTED = GraphSpec.from_dict(dict(ROUTED_DOC))
seed = State.new("s6", facts=[Fact(f"f{i}", i, "s", NOW) for i in range(100)],
                 decisions=[Decision("k", "one", NOW), Decision("k", "two", NOW)])
out = Runtime(ROUTED, {"a": ident, "x": ident, "y": ident}).run(seed)
routing = next(r for r in out.trace.records if r["kind"] == "routing")
print(f"   value={routing['value']!r} origin={routing['origin']} selected={routing['selected']}")
v = validate.validate_trace(out.trace.to_dict(), spec=ROUTED_DOC)
print(f"   validator: {len(v)} violation(s) {[x.rule for x in v][:3]}")

# ===================== RunContext binding ================================== #
print("\n=== R1: which signatures receive RunContext? ===")


class CallInstance:
    def __call__(self, state, ctx):
        seen.append(("instance-required", type(ctx).__name__))
        return state


class CallInstanceDefault:
    def __call__(self, state, ctx=None):
        seen.append(("instance-default", type(ctx).__name__))
        return state


class Bound:
    def method_required(self, state, ctx):
        seen.append(("bound-required", type(ctx).__name__))
        return state

    def method_default(self, state, ctx=None):
        seen.append(("bound-default", type(ctx).__name__))
        return state


def deco(fn):
    @functools.wraps(fn)
    def wrapper(*args, **kwargs):
        return fn(*args, **kwargs)
    return wrapper


def one(state):
    seen.append(("one-arg", "-"))
    return state


def required(state, ctx):
    seen.append(("required", type(ctx).__name__))
    return state


def defaulted(state, ctx=None):
    seen.append(("defaulted", type(ctx).__name__))
    return state


def kwonly(state, *, ctx=None):
    seen.append(("kwonly", type(ctx).__name__))
    return state


def partial_target(state, ctx, extra):
    seen.append(("partial-required", type(ctx).__name__))
    return state


def partial_target_default(state, ctx=None):
    seen.append(("partial-default", type(ctx).__name__))
    return state


bound = Bound()
cases = {
    "def f(state)": one,
    "def f(state, ctx)": required,
    "def f(state, ctx=None)": defaulted,
    "def f(state, *, ctx=None)": kwonly,
    "instance __call__(state, ctx)": CallInstance(),
    "instance __call__(state, ctx=None)": CallInstanceDefault(),
    "bound method (state, ctx)": bound.method_required,
    "bound method (state, ctx=None)": bound.method_default,
    "decorated (state, ctx)": deco(required),
    "partial fixing extra": functools.partial(partial_target, extra=1),
    "partial over defaulted": functools.partial(partial_target_default),
    "lambda s: s": lambda s: s,
    "def f(*args)": lambda *args: args[0],
    "def f(**kw)": lambda **kw: None,
    "def f()": lambda: None,
    "def f(a, b, c)": lambda a, b, c: a,
}
for label, node in cases.items():
    seen: list = []
    try:
        Runtime(SINGLE, {"a": node}).run(State.empty("r1"))
        got = seen[0][1] if seen else "?"
        print(f"   {label:36} -> ran, second param = {got}")
    except TypeError as exc:
        print(f"   {label:36} -> REFUSED at construction: {str(exc)[:52]}")
    except NodeFailed as exc:
        print(f"   {label:36} -> NodeFailed ({type(exc.cause).__name__})")
    except Exception as exc:  # noqa: BLE001
        print(f"   {label:36} -> {type(exc).__name__}: {str(exc)[:52]}")

# ===================== callable identity =================================== #
print("\n=== I1: mutable configuration changes the next run's fingerprint ===")


class Configured:
    def __init__(self, value):
        self.value = value
        self.calls = 0

    def motus_config(self):
        self.calls += 1
        return {"value": self.value}

    def __call__(self, state):
        return state.with_fact(Fact("v", self.value, "s", NOW))


node = Configured(1)
rt = Runtime(SINGLE, {"a": node})
f1 = rt.run(State.empty("i1")).trace.run["graph"]["code_fingerprint"]
f2 = rt.run(State.empty("i1")).trace.run["graph"]["code_fingerprint"]
node.value = 999
f3 = rt.run(State.empty("i1")).trace.run["graph"]["code_fingerprint"]
node.value = 1
f4 = rt.run(State.empty("i1")).trace.run["graph"]["code_fingerprint"]
print(f"   unchanged config keeps the digest : {f1 == f2}")
print(f"   changed config moves the digest   : {f1 != f3}")
print(f"   reverting restores the digest     : {f1 == f4}")
print(f"   motus_config() calls made         : {node.calls}")

print("\n   nested config value mutated in place:")
nested = Configured({"deep": [1, 2]})
rt = Runtime(SINGLE, {"a": lambda s: s.with_fact(Fact("v", 1, "s", NOW))})
rt2 = Runtime(SINGLE, {"a": nested})
g1 = rt2.run(State.empty("i1b")).trace.run["graph"]["code_fingerprint"]
nested.value["deep"].append(3)
g2 = rt2.run(State.empty("i1b")).trace.run["graph"]["code_fingerprint"]
print(f"   in-place nested mutation detected : {g1 != g2}")

print("\n   partial arguments mutated:")
args = {"v": 1}
pnode = functools.partial(lambda state, v: state.with_fact(Fact("v", v, "s", NOW)), **args)
rt3 = Runtime(SINGLE, {"a": pnode})
p1 = rt3.run(State.empty("i1c")).trace.run["graph"]["code_fingerprint"]
p2 = rt3.run(State.empty("i1c")).trace.run["graph"]["code_fingerprint"]
print(f"   partial digest stable             : {p1 == p2}")

print("\n   opaque instance -> replay constraint each run:")


class Opaque:
    def __init__(self, v):
        self.v = v

    def __call__(self, state):
        return state


rt4 = Runtime(SINGLE, {"a": Opaque(1)})
from vitruvyan_motus import ReplayStatus  # noqa: E402

o1 = rt4.run(State.empty("i1d"), replay=ReplayStatus.declared("full"))
o2 = rt4.run(State.empty("i1d"), replay=ReplayStatus.declared("full"))
print(f"   run1 terminal replay: {o1.trace.records[-1]['replay']}")
print(f"   run2 terminal replay: {o2.trace.records[-1]['replay']}")

# ===================== sinks =============================================== #
print("\n=== K1: a supplied sink is opened under every profile ===")
for profile in ("in-memory", "buffered", "synchronous"):
    sink = InMemoryTraceSink()
    out = Runtime(LINEAR, {"a": ident, "b": ident}, durability_profile=profile,
                  sink=sink).run(State.empty("k1"))
    kinds = [r["kind"] for r in sink.records]
    ok = kinds == [r["kind"] for r in out.trace.records]
    print(f"   {profile:12}: opened={len(sink.runs)} records={len(kinds)} "
          f"causal_order_matches_trace={ok}")

print("\n=== K2: sink failure semantics per profile ===")


class FailingRunSink:
    def __init__(self, predicate):
        self.predicate = predicate
        self.written = []

    def write(self, records):
        for record in records:
            if self.predicate(record):
                raise OSError(f"refuse {record['kind']}")
            self.written.append(record)


class FailingSink:
    def __init__(self, predicate, open_fails=False):
        self.run_sink = FailingRunSink(predicate)
        self.open_fails = open_fails

    def open_run(self, header):
        if self.open_fails:
            raise OSError("refuse open")
        return self.run_sink


for profile in ("in-memory", "buffered", "synchronous"):
    for label, sink in (
        ("open_run refused", FailingSink(lambda r: False, open_fails=True)),
        ("mid-trace refused", FailingSink(lambda r: r["kind"] == "routing")),
    ):
        rt = Runtime(LINEAR, {"a": ident, "b": ident}, durability_profile=profile, sink=sink)
        try:
            res = rt.run(State.empty("k2"))
            verdict = f"returned status={res.status}"
        except SinkFailed:
            verdict = "SinkFailed"
        except Exception as exc:  # noqa: BLE001
            verdict = f"{type(exc).__name__}"
        kinds = [r["kind"] for r in rt.trace.records]
        print(f"   {profile:12} {label:20}: {verdict:24} trace_terminal={kinds[-1]}")

print("\n=== K3: sink reuse across repeated runs ===")
sink = InMemoryTraceSink()
rt = Runtime(LINEAR, {"a": ident, "b": ident}, durability_profile="synchronous", sink=sink)
for i in range(3):
    rt.run(State.empty("k3"), run_id=f"k3-{i}")
print("   runs opened:", len(sink.runs),
      "| ids:", [r["header"]["run_id"] for r in sink.runs])
print("   per-run record counts:", [len(r["records"]) for r in sink.runs])

# ===================== listener boundary =================================== #
print("\n=== L1: listener isolation, counting and determinism ===")
delivered = []


class Recorder:
    def __init__(self, tag):
        self.tag = tag

    def on_record(self, record):
        delivered.append((self.tag, record["seq"]))
        record["kind"] = "FORGED"
        record.clear()


class Exploder:
    def on_record(self, record):
        raise SystemExit("boom")


rec = Recorder("r1")
rt = Runtime(LINEAR, {"a": ident, "b": ident}, listeners=(rec, Exploder(), rec))
out = rt.run(State.empty("l1"))
print("   status:", out.status, "| trace intact:", [r["kind"] for r in out.trace.records][:3])
print("   same listener registered twice delivers twice:",
      len(delivered) == 2 * len(out.trace.records))
print("   per-listener seq order:", [s for t, s in delivered if t == "r1"] ==
      sorted({s for t, s in delivered}) * 1 or "see raw")
print("   listener_failures counted:", rt._hub.listener_failures if rt._hub else "n/a")
