"""Attack C: state isolation, aliasing, provenance and JSON-typed equality."""

from __future__ import annotations

import json
from datetime import datetime, timezone

from vitruvyan_motus import (
    Decision, Fact, GraphSpec, Policy, Rejection, Runtime, State, redact,
)

NOW = datetime(2026, 8, 4, tzinfo=timezone.utc)
RESULTS: list[tuple[str, str]] = []


def report(name, verdict, detail=""):
    RESULTS.append((name, verdict))
    print(f"{verdict:8} {name}" + (f"  -- {detail}" if detail else ""))


def linear(nodes, transitions, entry="a"):
    return GraphSpec.from_dict({
        "schema_version": "1.0.0", "name": "iso", "version": "1.0.0",
        "entry": entry, "nodes": nodes, "transitions": transitions,
    })


# --- C1: nested mutable value written, then mutated by the node ------------
def c1():
    payload = {"nested": {"secret": "before"}, "items": [1, 2]}

    def node(state):
        out = state.with_fact(Fact("k", payload, "src", NOW))
        payload["nested"]["secret"] = "AFTER"       # mutate after write
        payload["items"].append(999)
        return out

    spec = linear([{"name": "a", "effect_class": "pure"}], {"a": {"kind": "terminal"}})
    result = Runtime(spec, {"a": node}).run(State.empty("c1"))
    recorded = result.trace.records[2]["writes"]["facts"][0]["value"]
    committed = result.state.fact("k")
    ok = recorded == {"nested": {"secret": "before"}, "items": [1, 2]} and committed == recorded
    report("C1 write-boundary isolation vs post-return mutation",
           "PASS" if ok else "FAIL", json.dumps(recorded))


# --- C2: value read out of state, then mutated by the reader ---------------
def c2():
    def writer(state):
        return state.with_fact(Fact("k", {"a": [1]}, "src", NOW))

    stolen = {}

    def reader(state):
        v = state.fact("k")
        v["a"].append(42)          # mutate the object handed back by a read
        stolen["v"] = v
        return state.with_fact(Fact("k2", 1, "src", NOW))

    spec = linear(
        [{"name": "a", "effect_class": "pure"}, {"name": "b", "effect_class": "pure"}],
        {"a": {"kind": "next", "to": "b"}, "b": {"kind": "terminal"}},
    )
    result = Runtime(spec, {"a": writer, "b": reader}).run(State.empty("c2"))
    ok = result.state.fact("k") == {"a": [1]}
    report("C2 read isolation vs reader mutation", "PASS" if ok else "FAIL",
           f"committed={result.state.fact('k')} stolen={stolen['v']}")


# --- C3: same key written twice in one transition -------------------------
def c3():
    def node(state):
        return (state
                .with_fact(Fact("k", "first", "s", NOW))
                .with_fact(Fact("k", "second", "s", NOW)))

    spec = linear([{"name": "a", "effect_class": "pure"}], {"a": {"kind": "terminal"}})
    result = Runtime(spec, {"a": node}).run(State.empty("c3"))
    writes = result.trace.records[2]["writes"]["facts"]
    report("C3 two writes of one key in one transition",
           "PASS" if len(writes) == 2 and result.state.fact("k") == "second" else "FAIL",
           f"{[w['value'] for w in writes]} -> {result.state.fact('k')}")


# --- C4: JSON true vs 1 in routing --------------------------------------
def c4():
    spec = GraphSpec.from_dict({
        "schema_version": "1.0.0", "name": "r", "version": "1.0.0", "entry": "a",
        "nodes": [{"name": "a", "effect_class": "pure"},
                  {"name": "t", "effect_class": "pure"},
                  {"name": "f", "effect_class": "pure"}],
        "transitions": {
            "a": {"kind": "route", "on": "k", "map": {"true": "t", "1": "f"}},
            "t": {"kind": "terminal"}, "f": {"kind": "terminal"},
        },
    })

    for value in (True, 1, "true", "1"):
        def node(state, value=value):
            return state.with_decision(Decision("k", value, NOW))
        ident = lambda s: s
        r = Runtime(spec, {"a": node, "t": ident, "f": ident},
                    policy=Policy.EXPLORATION).run(State.empty("c4"))
        routing = [x for x in r.trace.records if x["kind"] == "routing"][0]
        print(f"         C4 decision value {value!r:8} -> outcome={routing['outcome']:8} "
              f"selected={routing['selected']:4} recorded value={routing['value']!r}")
    report("C4 boolean/number vs string routing", "INFO")


# --- C5: seeded decisions with duplicate keys, origin exactness -----------
def c5():
    seed = State.new("c5", decisions=[Decision("k", "one", NOW), Decision("k", "two", NOW)])
    spec = GraphSpec.from_dict({
        "schema_version": "1.0.0", "name": "r", "version": "1.0.0", "entry": "a",
        "nodes": [{"name": "a", "effect_class": "pure"}, {"name": "x", "effect_class": "pure"},
                  {"name": "y", "effect_class": "pure"}],
        "transitions": {
            "a": {"kind": "route", "on": "k", "map": {"one": "x", "two": "y"}},
            "x": {"kind": "terminal"}, "y": {"kind": "terminal"},
        },
    })
    ident = lambda s: s
    r = Runtime(spec, {"a": ident, "x": ident, "y": ident}).run(seed)
    routing = [x for x in r.trace.records if x["kind"] == "routing"][0]
    ok = routing["origin"] == {"kind": "initial", "index": 1} and routing["value"] == "two"
    report("C5 duplicate seeded decision keys name the exact entry",
           "PASS" if ok else "FAIL", f"origin={routing['origin']} value={routing['value']!r}")


# --- C6: overwritten decision -> does the route name the exact origin? ----
def c6():
    def first(state):
        return state.with_decision(Decision("k", "one", NOW))

    def second(state):
        return state.with_decision(Decision("k", "two", NOW))

    spec = GraphSpec.from_dict({
        "schema_version": "1.0.0", "name": "r", "version": "1.0.0", "entry": "a",
        "nodes": [{"name": "a", "effect_class": "pure"}, {"name": "b", "effect_class": "pure"},
                  {"name": "x", "effect_class": "pure"}, {"name": "y", "effect_class": "pure"}],
        "transitions": {
            "a": {"kind": "next", "to": "b"},
            "b": {"kind": "route", "on": "k", "map": {"one": "x", "two": "y"}},
            "x": {"kind": "terminal"}, "y": {"kind": "terminal"},
        },
    })
    ident = lambda s: s
    r = Runtime(spec, {"a": first, "b": second, "x": ident, "y": ident}).run(State.empty("c6"))
    routing = [x for x in r.trace.records if x["kind"] == "routing" and x["after"] == "b"][0]
    txn = [x for x in r.trace.records if x["kind"] == "transition" and x["node"] == "b"][0]
    ok = routing["origin"] == {"kind": "transition", "seq": txn["seq"], "index": 0}
    report("C6 stale-vs-fresh decision origin exactness", "PASS" if ok else "FAIL",
           f"origin={routing['origin']} txn_seq={txn['seq']}")


# --- C7: node mutates the State object it received (attribute access) -----
def c7():
    def node(state):
        try:
            state._log = None            # direct internal mutation
        except Exception as exc:
            return state.with_fact(Fact("blocked", type(exc).__name__, "s", NOW))
        return state.with_fact(Fact("blocked", "NO", "s", NOW))

    spec = linear([{"name": "a", "effect_class": "pure"}], {"a": {"kind": "terminal"}})
    r = Runtime(spec, {"a": node}).run(State.empty("c7"))
    report("C7 direct internal mutation of received State", "INFO",
           f"result={r.state.fact('blocked')!r}")


# --- C8: absent read of a key later written -------------------------------
def c8():
    def probe(state):
        state.fact("later")                       # absent read
        state.metadata("nope")                    # absent metadata read
        _ = state.intent                          # header read
        _ = state.facts                           # scan read
        return state.with_fact(Fact("later", 1, "s", NOW))

    spec = linear([{"name": "a", "effect_class": "pure",
                    "reads_declared": [], "writes_declared": ["later"]}],
                  {"a": {"kind": "terminal"}})
    r = Runtime(spec, {"a": probe}, policy=Policy.EXPLORATION).run(State.empty("c8"))
    txn = r.trace.records[2]
    keys = sorted({v["key"] for v in txn["violations"]})
    report("C8 absent/header/scan reads are declared surface",
           "PASS" if keys == ["facts", "intent", "later", "nope"] else "FAIL",
           f"violations={keys} reads={[x['key'] for x in txn['reads']]}")


# --- C9: rejections vs writes_declared ------------------------------------
def c9():
    def node(state):
        return state.with_rejection(Rejection("undeclared-thing", "because", NOW))

    spec = linear([{"name": "a", "effect_class": "pure", "writes_declared": []}],
                  {"a": {"kind": "terminal"}})
    r = Runtime(spec, {"a": node}, policy=Policy.STRICT).run(State.empty("c9"))
    txn = r.trace.records[2]
    report("C9 rejection write under writes_declared: []", "INFO",
           f"status={r.status} violations={txn['violations']} "
           f"rejections={txn['writes']['rejections']}")


# --- C10: forged redacted value at depth ----------------------------------
def c10():
    for payload in (
        {"kind": "redacted", "hash": "redacted:sha256:" + "0" * 64, "policy_ref": "p"},
        {"outer": {"kind": "redacted", "hash": "x", "policy_ref": "p"}},
        [{"kind": "redacted"}],
    ):
        try:
            Fact("k", payload, "s", NOW)
            print(f"         C10 payload accepted: {json.dumps(payload)[:60]}")
        except Exception as exc:
            print(f"         C10 refused ({type(exc).__name__}): {json.dumps(payload)[:60]}")
    real = redact({"secret": 1}, "policy://p")
    print("         C10 genuine redact ->", real.to_dict()["hash"][:32], "...")
    report("C10 forged redacted values", "INFO")


for fn in (c1, c2, c3, c4, c5, c6, c7, c8, c9, c10):
    try:
        fn()
    except Exception as exc:  # noqa: BLE001
        report(fn.__name__, "ERROR", f"{type(exc).__name__}: {exc}")

print("\nsummary:", {v: sum(1 for _, x in RESULTS if x == v) for v in {x for _, x in RESULTS}})
