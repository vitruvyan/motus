"""Attacks A + J: GraphSpec validation, fingerprint fidelity, node identity."""

from __future__ import annotations

import copy
import functools
import json
import sys
import threading
from datetime import datetime, timezone

from vitruvyan_motus import (
    Fact, GraphSpec, GraphSpecValidationError, Runtime, State,
)

NOW = datetime(2026, 8, 4, tzinfo=timezone.utc)

BASE = {
    "schema_version": "1.0.0", "name": "g", "version": "1.0.0", "entry": "a",
    "nodes": [{"name": "a", "effect_class": "pure"}, {"name": "b", "effect_class": "pure"}],
    "transitions": {"a": {"kind": "next", "to": "b"}, "b": {"kind": "terminal"}},
}
ident = lambda s: s


def fp(doc):
    return GraphSpec.from_dict(copy.deepcopy(doc)).graph_fingerprint


print("=== A1: does every execution-relevant change move the fingerprint? ===")
base_fp = fp(BASE)
mutations = {
    "entry a->b (+reachability fix)": {**copy.deepcopy(BASE), "entry": "b",
                                       "nodes": [{"name": "b", "effect_class": "pure"}],
                                       "transitions": {"b": {"kind": "terminal"}}},
    "node effect_class pure->external": {
        **copy.deepcopy(BASE),
        "nodes": [{"name": "a", "effect_class": "external_effect"},
                  {"name": "b", "effect_class": "pure"}]},
    "effect_class omitted (defaults external)": {
        **copy.deepcopy(BASE), "nodes": [{"name": "a"}, {"name": "b", "effect_class": "pure"}]},
    "transition to swapped": {**copy.deepcopy(BASE),
                              "transitions": {"a": {"kind": "terminal"},
                                              "b": {"kind": "terminal"}},
                              "nodes": [{"name": "a", "effect_class": "pure"}]},
    "graph version bump": {**copy.deepcopy(BASE), "version": "1.0.1"},
    "max_transitions added": {**copy.deepcopy(BASE), "max_transitions": 5},
    "reads_declared added": {**copy.deepcopy(BASE),
                             "nodes": [{"name": "a", "effect_class": "pure", "reads_declared": []},
                                       {"name": "b", "effect_class": "pure"}]},
}
for label, doc in mutations.items():
    try:
        changed = fp(doc) != base_fp
        print(f"   {'MOVES  ' if changed else 'SAME!! '} {label}")
    except GraphSpecValidationError as exc:
        print(f"   REFUSED {label}: {sorted(exc.rules)}")

print("\n=== A2: representation-only differences ===")
reordered_nodes = {**copy.deepcopy(BASE),
                   "nodes": [{"effect_class": "pure", "name": "a"},
                             {"name": "b", "effect_class": "pure"}]}
print("   key order inside a node object      :",
      "SAME (canonical)" if fp(reordered_nodes) == base_fp else "DIFFERS")
node_list_swapped = {**copy.deepcopy(BASE),
                     "nodes": [{"name": "b", "effect_class": "pure"},
                               {"name": "a", "effect_class": "pure"}]}
print("   nodes[] array order (a,b) -> (b,a)  :",
      "SAME" if fp(node_list_swapped) == base_fp else "DIFFERS (array order is significant)")
int_float = {**copy.deepcopy(BASE), "max_transitions": 5}
float_form = {**copy.deepcopy(BASE), "max_transitions": 5.0}
print("   max_transitions 5 vs 5.0            :",
      "SAME" if fp(int_float) == fp(float_form) else "DIFFERS (documented: declared form)")

print("\n=== A3: post-validation mutation of the source document ===")
doc = copy.deepcopy(BASE)
spec = GraphSpec.from_dict(doc)
before = spec.graph_fingerprint
doc["entry"] = "b"
doc["nodes"].append({"name": "c"})
after = spec.graph_fingerprint
print("   caller mutated the dict it passed in :",
      "IMMUNE" if before == after else "FINGERPRINT DRIFTED")
print("   spec.entry still         :", spec.entry)
try:
    spec.to_dict()["entry"] = "zzz"
    print("   to_dict() mutation leaks  :", "NO" if spec.entry == "a" else "YES")
except Exception as exc:  # noqa: BLE001
    print("   to_dict() mutation        :", type(exc).__name__)

print("\n=== A4: node identity — indistinguishable callables ===")


class Configurable:
    def __init__(self, value):
        self.value = value

    def motus_config(self):
        return {"value": self.value}

    def __call__(self, state):
        return state.with_fact(Fact("v", self.value, "s", NOW))


class Opaque:
    def __init__(self, value):
        self.value = value

    def __call__(self, state):
        return state.with_fact(Fact("v", self.value, "s", NOW))


def make_closure(value):
    def node(state):
        return state.with_fact(Fact("v", value, "s", NOW))
    return node


def code_fp(registry):
    rt = Runtime(GraphSpec.from_dict(copy.deepcopy(BASE)), registry)
    out = rt.run(State.empty("x"))
    return out.trace.run["graph"]["code_fingerprint"], out.trace.run["replay"]


for label, a_node in (
    ("configurable(1)", Configurable(1)),
    ("configurable(2)", Configurable(2)),
    ("opaque(1)", Opaque(1)),
    ("opaque(2)", Opaque(2)),
    ("closure(1)", make_closure(1)),
    ("closure(2)", make_closure(2)),
    ("partial(1)", functools.partial(lambda state, v: state.with_fact(Fact("v", v, "s", NOW)), v=1)),
    ("partial(2)", functools.partial(lambda state, v: state.with_fact(Fact("v", v, "s", NOW)), v=2)),
):
    f, replay = code_fp({"a": a_node, "b": ident})
    print(f"   {label:16} {f[12:28]}  replay={replay['capability']:7} {replay['constraints']}")

print("\n=== A5: mutable node config captured after fingerprinting ===")
node = Configurable(1)
runtime = Runtime(GraphSpec.from_dict(copy.deepcopy(BASE)), {"a": node, "b": ident})
first = runtime.run(State.empty("x1")).trace.run["graph"]["code_fingerprint"]
node.value = 999                       # behaviour changes after construction
second = runtime.run(State.empty("x2")).trace.run["graph"]["code_fingerprint"]
print("   config mutated between runs on one Runtime:",
      "STALE FINGERPRINT" if first == second else "fingerprint updated")
print("   run 1 wrote", runtime.run(State.empty("x3")).state.fact("v"))

print("\n=== A6: ambiguous node signature (state, cached=default) ===")


def defaulted(state, cache={}):          # noqa: B006 - deliberately the idiom
    cache["seen"] = type(state).__name__
    return state


rt = Runtime(GraphSpec.from_dict({
    "schema_version": "1.0.0", "name": "s", "version": "1.0.0", "entry": "a",
    "nodes": [{"name": "a", "effect_class": "pure"}],
    "transitions": {"a": {"kind": "terminal"}}}), {"a": defaulted})
try:
    rt.run(State.empty("sig"))
except Exception as exc:
    print("   run raised:", type(exc).__name__, "->", type(exc.cause).__name__ if hasattr(exc, "cause") else "")
print("   second positional param received:", defaulted.__defaults__[0])

print("\n=== A7: deep / wide graphs ===")
for size, shape in ((2000, "linear"), (2000, "wide")):
    if shape == "linear":
        names = [f"n{i}" for i in range(size)]
        doc = {"schema_version": "1.0.0", "name": "deep", "version": "1.0.0",
               "entry": names[0],
               "nodes": [{"name": n, "effect_class": "pure"} for n in names],
               "transitions": {n: ({"kind": "terminal"} if i == size - 1
                                   else {"kind": "next", "to": names[i + 1]})
                               for i, n in enumerate(names)}}
    else:
        names = [f"n{i}" for i in range(size)]
        doc = {"schema_version": "1.0.0", "name": "wide", "version": "1.0.0",
               "entry": "hub",
               "nodes": [{"name": "hub", "effect_class": "pure"}]
                        + [{"name": n, "effect_class": "pure"} for n in names],
               "transitions": {"hub": {"kind": "route", "on": "k",
                                       "map": {n: n for n in names}, "default": names[0]},
                               **{n: {"kind": "terminal"} for n in names}}}
    import time
    t = time.perf_counter()
    s = GraphSpec.from_dict(doc)
    s.graph_fingerprint
    print(f"   {shape} {size}: validated+fingerprinted in {(time.perf_counter()-t)*1000:.1f} ms")

print("\n=== A8: recursion limit on a deeply nested JSON value ===")
deep = current = {}
for _ in range(400):
    current["n"] = {}
    current = current["n"]
try:
    Fact("k", deep, "s", NOW)
    print("   400-deep nested value accepted")
except RecursionError as exc:
    print("   400-deep nested value -> RecursionError (uncaught):", exc)
except Exception as exc:  # noqa: BLE001
    print(f"   400-deep nested value -> {type(exc).__name__}: {exc}")

print("\n=== A9: two threads sharing one Runtime ===")
rt = Runtime(GraphSpec.from_dict(copy.deepcopy(BASE)), {"a": ident, "b": ident})
errors, results = [], []


def worker():
    try:
        results.append(rt.run(State.empty("t")).status)
    except BaseException as exc:  # noqa: BLE001
        errors.append(f"{type(exc).__name__}: {exc}")


threads = [threading.Thread(target=worker) for _ in range(2)]
for t in threads:
    t.start()
for t in threads:
    t.join()
print("   results =", results, "| errors =", errors)
print("   final trace kinds =", [r["kind"] for r in rt.trace.records])
