"""Attacks F/I/B: sink-failure traces vs the contract, and forced races."""

from __future__ import annotations

import importlib.util
import sys
import threading
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
mod = importlib.util.spec_from_file_location("motus_validate", ROOT / "contract" / "validate.py")
validate = importlib.util.module_from_spec(mod)
sys.modules["motus_validate"] = validate
mod.loader.exec_module(validate)

from vitruvyan_motus import (  # noqa: E402
    Fact, GraphSpec, NodeFailed, Policy, Runtime, SinkFailed, State,
)

NOW = datetime(2026, 8, 4, tzinfo=timezone.utc)
DOC = {
    "schema_version": "1.0.0", "name": "sf", "version": "1.0.0", "entry": "a",
    "nodes": [{"name": "a", "effect_class": "pure"}, {"name": "b", "effect_class": "pure"}],
    "transitions": {"a": {"kind": "next", "to": "b"}, "b": {"kind": "terminal"}},
}
SPEC = GraphSpec.from_dict(DOC)
ident = lambda s: s


def w(state):
    return state.with_fact(Fact("k", 1, "s", NOW))


class RunSink:
    def __init__(self, fail_on):
        self.fail_on, self.written = fail_on, []

    def write(self, records):
        for record in records:
            if self.fail_on(record):
                raise OSError(f"refuse {record['kind']}")
            self.written.append(record)


class Sink:
    def __init__(self, fail_on, open_fails=False):
        self.run_sink, self.open_fails = RunSink(fail_on), open_fails

    def open_run(self, header):
        if self.open_fails:
            raise OSError("refuse open")
        return self.run_sink


print("=== F1: are SinkFailed traces themselves contract-valid evidence? ===")
scenarios = {
    "open_run refused": (Sink(lambda r: False, open_fails=True), {"a": w, "b": ident}, Policy.STRICT),
    "run_started refused": (Sink(lambda r: r["kind"] == "run_started"), {"a": w, "b": ident}, Policy.STRICT),
    "transition refused": (Sink(lambda r: r["kind"] == "transition"), {"a": w, "b": ident}, Policy.STRICT),
    "routing refused": (Sink(lambda r: r["kind"] == "routing"), {"a": w, "b": ident}, Policy.STRICT),
    "run_completed refused": (Sink(lambda r: r["kind"] == "run_completed"), {"a": w, "b": ident}, Policy.STRICT),
}
for label, (sink, registry, policy) in scenarios.items():
    rt = Runtime(SPEC, registry, durability_profile="synchronous", sink=sink, policy=policy)
    try:
        rt.run(State.empty("sf"))
        outcome = "returned"
    except SinkFailed:
        outcome = "SinkFailed"
    except NodeFailed:
        outcome = "NodeFailed"
    trace = rt.trace
    doc = trace.to_dict()
    v = validate.validate_trace(doc, spec=DOC)
    vj, _ = validate.validate_jsonl(trace.to_jsonl(), spec=DOC)
    print(f"   {label:22} {outcome:11} kinds={[r['kind'] for r in doc['records']]}")
    print(f"   {'':22} json_violations={len(v)} jsonl_violations={len(vj)} "
          f"equivalent={sorted((x.rule,x.path) for x in v)==sorted((x.rule,x.path) for x in vj)}")
    for x in v[:4]:
        print(f"   {'':22}   {x.rule} {x.path}: {x.message[:110]}")

print("\n=== F2: run already failed, then the terminal itself cannot persist ===")


def boom(state):
    raise RuntimeError("node down")


sink = Sink(lambda r: r["kind"] == "run_failed")
rt = Runtime(SPEC, {"a": boom, "b": ident}, durability_profile="synchronous", sink=sink)
try:
    rt.run(State.empty("sf"))
    outcome = "returned"
except SinkFailed:
    outcome = "SinkFailed"
except NodeFailed:
    outcome = "NodeFailed"
doc = rt.trace.to_dict()
v = validate.validate_trace(doc, spec=DOC)
print(f"   caller sees      : {outcome}")
print(f"   in-memory kinds  : {[r['kind'] for r in doc['records']]}")
print(f"   persisted kinds  : {[r['kind'] for r in sink.run_sink.written]}")
print(f"   validator        : {len(v)} violation(s) {[x.rule for x in v]}")

print("\n=== I3: barrier-forced overlap of run() on one Runtime (500 rounds) ===")
tally = {"guard": 0, "both ok": 0, "corrupt": 0, "other": 0}
details = []
for round_index in range(500):
    rt = Runtime(SPEC, {"a": w, "b": ident})
    barrier = threading.Barrier(2)
    box = []

    def go():
        barrier.wait()
        try:
            result = rt.run(State.empty("race"))
            box.append(("ok", [r["kind"] for r in result.trace.records],
                        [r["seq"] for r in result.trace.records]))
        except RuntimeError as exc:
            box.append(("guard", str(exc), None))
        except BaseException as exc:  # noqa: BLE001
            box.append((type(exc).__name__, str(exc), None))

    ts = [threading.Thread(target=go) for _ in range(2)]
    for t in ts:
        t.start()
    for t in ts:
        t.join()
    tags = [b[0] for b in box]
    if "guard" in tags:
        tally["guard"] += 1
    elif tags == ["ok", "ok"]:
        bad = False
        for _, kinds, seqs in box:
            if kinds[0] != "run_started" or kinds[-1] != "run_completed" \
               or seqs != list(range(1, len(seqs) + 1)) \
               or kinds.count("run_started") != 1:
                bad = True
        if bad:
            tally["corrupt"] += 1
            if len(details) < 3:
                details.append(box)
        else:
            tally["both ok"] += 1
    else:
        tally["other"] += 1
        if len(details) < 3:
            details.append(box)
print("   500 barrier rounds:", tally)
for d in details:
    print("     sample:", [(t, k if isinstance(k, str) else k[:6]) for t, k, _ in d])

print("\n=== I4: abandoned StreamDriver keeps the Runtime locked? ===")
rt = Runtime(SPEC, {"a": w, "b": ident})
driver = rt.stream(State.empty("s"))
next(driver)
del driver
import gc  # noqa: E402
gc.collect()
try:
    out = rt.run(State.empty("s2"))
    print("   second run after abandoning the driver:", out.status)
except RuntimeError as exc:
    print("   second run after abandoning the driver: RuntimeError:", exc)

print("\n=== I5: StreamDriver held open, then run() on the same Runtime ===")
rt = Runtime(SPEC, {"a": w, "b": ident})
driver = rt.stream(State.empty("s"))
next(driver)
try:
    rt.run(State.empty("s2"))
    print("   overlapping run(): ALLOWED  <-- unexpected")
except RuntimeError as exc:
    print("   overlapping run(): refused ->", exc)
driver.close()

print("\n=== B1: does the trace ever show a node executed out of the selected route? ===")
seen = []


def probe(name):
    def node(state):
        seen.append(name)
        return state
    return node


rt = Runtime(SPEC, {"a": probe("a"), "b": probe("b")})
out = rt.run(State.empty("b1"))
order = [r["node"] for r in out.trace.records if r["kind"] == "attempt_started"]
routes = [(r["after"], r["selected"]) for r in out.trace.records if r["kind"] == "routing"]
print("   actually executed:", seen)
print("   trace attempts   :", order)
print("   routing decisions:", routes)
print("   consistent       :", seen == order and routes == [("a", "b"), ("b", "END")])
