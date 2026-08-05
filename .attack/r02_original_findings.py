"""Re-audit Phase 2: reproduce each original 0.6 blocker, then falsify the fix."""

from __future__ import annotations

import copy
import importlib.util
import json
import pickle
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
_spec = importlib.util.spec_from_file_location("rv", ROOT / "contract" / "validate.py")
validate = importlib.util.module_from_spec(_spec)
sys.modules["rv"] = validate
_spec.loader.exec_module(validate)

from vitruvyan_motus import (  # noqa: E402
    Decision, DurabilityProfile, Fact, GraphSpec, NodeFailed, Policy,
    Rejection, ReplayEngine, Runtime, State, Trace, TraceBundle,
)
from vitruvyan_motus.trace import _MISSING, _Missing  # noqa: E402

NOW = datetime(2026, 8, 4, tzinfo=timezone.utc)
ident = lambda s: s

SINGLE_DOC = {
    "schema_version": "1.0.0", "name": "rej", "version": "1.0.0", "entry": "a",
    "nodes": [{"name": "a", "effect_class": "pure"}],
    "transitions": {"a": {"kind": "terminal"}},
}
SINGLE = GraphSpec.from_dict(dict(SINGLE_DOC))


def both_forms(trace, spec_doc):
    document = trace.to_dict()
    complete = document["records"][-1]["kind"] in ("run_completed", "run_failed", "run_cancelled")
    j = validate.validate_trace(document, spec=spec_doc, expect_complete=complete)
    l, _ = validate.validate_jsonl(trace.to_jsonl(), spec=spec_doc, expect_complete=complete)
    same = sorted((v.rule, v.path, v.message) for v in j) == sorted((v.rule, v.path, v.message) for v in l)
    return j, l, same


# ========================= A. evidence-free Rejection ====================== #
print("=== A1: the sentinel across every isolation boundary ===")
r = Rejection("w", "r", NOW)
probes = {
    "original": r,
    "copy.copy": copy.copy(r),
    "copy.deepcopy": copy.deepcopy(r),
    "deepcopy x3": copy.deepcopy(copy.deepcopy(copy.deepcopy(r))),
    "pickle roundtrip": pickle.loads(pickle.dumps(r)),
}
for label, probe in probes.items():
    print(f"   {label:20} evidence is _MISSING: {probe.evidence is _MISSING!s:5} "
          f"to_dict keys: {sorted(probe.to_dict())}")

print("\n=== A2: user values that resemble the private sentinel ===")
for label, value in (
    ("a fresh _Missing()", _Missing()),
    ("a lookalike class", type("Fake", (), {"__deepcopy__": lambda s, m: s})()),
    ("the string '_MISSING'", "_MISSING"),
    ("None", None),
    ("explicit dict", {"missing": True}),
):
    try:
        wire = Rejection("w", "r", NOW, evidence=value).to_dict()
        print(f"   {label:22} accepted -> {json.dumps(wire, default=str)[:78]}")
    except Exception as exc:  # noqa: BLE001
        print(f"   {label:22} refused  -> {type(exc).__name__}: {str(exc)[:60]}")


def declines(state):
    return state.with_rejection(Rejection("thing", "not applicable", NOW))


print("\n=== A3: a full run, every persistence and validation surface ===")
result = Runtime(SINGLE, {"a": declines}).run(State.empty("a3"))
txn = next(x for x in result.trace.records if x["kind"] == "transition")
print("   status               :", result.status)
print("   wire rejection       :", txn["writes"]["rejections"][0])
print("   'evidence' present   :", "evidence" in txn["writes"]["rejections"][0])
print("   to_json              :", len(result.trace.to_json()), "chars")
print("   to_jsonl             :", len(result.trace.to_jsonl()), "chars")
j, l, same = both_forms(result.trace, SINGLE_DOC)
print(f"   validator json/jsonl : {len(j)}/{len(l)} violations, equivalent={same}")
print("   snapshot             :", result.state.snapshot()["rejections"])
bundle = TraceBundle(SINGLE, result.trace)
print("   bundle + playback    :", ReplayEngine(bundle).playback().mode,
      "| explain terminal:", bundle.explain()["terminal"])

print("\n=== A4: rejection survives retry / continue / seeded paths ===")
RETRY_DOC = {
    "schema_version": "1.0.0", "name": "rej2", "version": "1.0.0", "entry": "a",
    "nodes": [{"name": "a", "effect_class": "pure"}, {"name": "b", "effect_class": "pure"}],
    "transitions": {"a": {"kind": "next", "to": "b"}, "b": {"kind": "terminal"}},
}
RETRY = GraphSpec.from_dict(dict(RETRY_DOC))
calls = [0]


def flaky_declines(state):
    calls[0] += 1
    out = state.with_rejection(Rejection(f"try{calls[0]}", "no", NOW))
    if calls[0] < 3:
        raise ValueError("later")
    return out


out = Runtime(RETRY, {"a": flaky_declines, "b": ident}, max_attempts={"a": 3, "b": 1}).run(State.empty("a4"))
j, l, same = both_forms(out.trace, RETRY_DOC)
print(f"   retry-then-commit    : status={out.status} json/jsonl={len(j)}/{len(l)} equiv={same}")

out = Runtime(RETRY, {"a": lambda s: (_ for _ in ()).throw(RuntimeError("x")), "b": ident},
              policy=Policy.EXPLORATION).run(State.empty("a4b"))
print(f"   exploration continue : status={out.status}")

seeded = State.new("a4c", rejections=[Rejection("seed", "why", NOW)])
out = Runtime(SINGLE, {"a": ident}).run(seeded)
j, l, same = both_forms(out.trace, SINGLE_DOC)
print(f"   seeded initial state : json/jsonl={len(j)}/{len(l)} equiv={same} "
      f"initial={out.trace.records[0]['initial_state']['rejections']}")

print("\n=== A5: a real JSON sink under the synchronous profile ===")


class RunSink:
    def __init__(self, path):
        self.path = path

    def write(self, records):
        with open(self.path, "a", encoding="utf-8") as fh:
            for record in records:
                fh.write(json.dumps(record) + "\n")


class Sink:
    def __init__(self, path):
        self.path = path

    def open_run(self, header):
        return RunSink(self.path)


with tempfile.TemporaryDirectory() as tmp:
    path = Path(tmp) / "t.jsonl"
    out = Runtime(SINGLE, {"a": declines}, durability_profile="synchronous",
                  sink=Sink(path)).run(State.empty("a5"))
    print("   status:", out.status, "| persisted lines:", len(path.read_text().splitlines()))

# ========================= B. max_transitions ============================== #
print("\n=== B1: EXPLORATION cycle where every node raises (the original hang) ===")
CYCLE_DOC = {
    "schema_version": "1.0.0", "name": "cyc", "version": "1.0.0", "entry": "a",
    "max_transitions": 4,
    "nodes": [{"name": n, "effect_class": "pure"} for n in ("a", "b", "z")],
    "transitions": {"a": {"kind": "route", "on": "k", "map": {"loop": "b", "stop": "z"}},
                    "b": {"kind": "next", "to": "a"}, "z": {"kind": "terminal"}},
}
CYCLE = GraphSpec.from_dict(dict(CYCLE_DOC))
SEED = State.new("b1", decisions=[Decision("k", "loop", NOW)])


def down(state):
    raise RuntimeError("dependency down")


out = Runtime(CYCLE, {"a": down, "b": down, "z": down}, policy=Policy.EXPLORATION).run(SEED)
counted = sum(1 for r in out.trace.records
              if r["kind"] == "transition" and r["disposition"] in ("commit", "continue"))
print(f"   status={out.status} cause={out.trace.records[-1]['cause']['kind']} "
      f"records={len(out.trace.records)} counted_activations={counted}")
j, l, same = both_forms(out.trace, CYCLE_DOC)
print(f"   validator json/jsonl : {len(j)}/{len(l)} equivalent={same}")

print("\n=== B2: mixed commit / continue / retry accounting ===")
state_box = {"n": 0}


def alternating(state):
    state_box["n"] += 1
    if state_box["n"] % 2:
        raise RuntimeError("odd fails")
    return state


out = Runtime(CYCLE, {"a": alternating, "b": alternating, "z": ident},
              policy=Policy.EXPLORATION, max_attempts={"a": 2, "b": 2, "z": 1}).run(SEED)
rows = [(r["node"], r["attempt"], r["outcome"], r["disposition"])
        for r in out.trace.records if r["kind"] == "transition"]
counted = sum(1 for r in out.trace.records
              if r["kind"] == "transition" and r["disposition"] in ("commit", "continue"))
print(f"   status={out.status} counted={counted} rows={rows}")
j, l, same = both_forms(out.trace, CYCLE_DOC)
print(f"   validator json/jsonl : {len(j)}/{len(l)} equivalent={same}")

print("\n=== B3: boundary limits 1 and 2, self-loop, END exactly at the limit ===")
for limit in (1, 2, 3, 4):
    doc = json.loads(json.dumps(CYCLE_DOC))
    doc["max_transitions"] = limit
    spec = GraphSpec.from_dict(doc)
    out = Runtime(spec, {"a": ident, "b": ident, "z": ident},
                  policy=Policy.EXPLORATION).run(SEED)
    counted = sum(1 for r in out.trace.records
                  if r["kind"] == "transition" and r["disposition"] in ("commit", "continue"))
    j, _, same = both_forms(out.trace, doc)
    print(f"   limit={limit}: status={out.status:8} counted={counted} "
          f"cause={out.trace.records[-1].get('cause', {}).get('kind')} violations={len(j)}")

print("\n   self-loop:")
SELF_DOC = {
    "schema_version": "1.0.0", "name": "self", "version": "1.0.0", "entry": "a",
    "max_transitions": 3,
    "nodes": [{"name": "a", "effect_class": "pure"}, {"name": "z", "effect_class": "pure"}],
    "transitions": {"a": {"kind": "route", "on": "k", "map": {"loop": "a", "stop": "z"}},
                    "z": {"kind": "terminal"}},
}
SELF = GraphSpec.from_dict(dict(SELF_DOC))
out = Runtime(SELF, {"a": ident, "z": ident}).run(
    State.new("b3", decisions=[Decision("k", "loop", NOW)]))
counted = sum(1 for r in out.trace.records
              if r["kind"] == "transition" and r["disposition"] in ("commit", "continue"))
j, _, same = both_forms(out.trace, SELF_DOC)
print(f"      status={out.status} counted={counted} violations={len(j)}")

print("\n   END selected exactly at the limit (must COMPLETE, not fail):")
doc = json.loads(json.dumps(SELF_DOC))
doc["max_transitions"] = 1
spec = GraphSpec.from_dict(doc)
out = Runtime(spec, {"a": ident, "z": ident}).run(
    State.new("b3b", decisions=[Decision("k", "stop", NOW)]))
print(f"      a->z at limit 1: status={out.status} "
      f"kinds={[r['kind'] for r in out.trace.records]}")

print("\n=== B4: retry exhaustion must not count toward the limit ===")
doc = json.loads(json.dumps(CYCLE_DOC))
doc["max_transitions"] = 2
spec = GraphSpec.from_dict(doc)
out = Runtime(spec, {"a": down, "b": down, "z": ident}, policy=Policy.EXPLORATION,
              max_attempts={"a": 3, "b": 3, "z": 1}).run(SEED)
rows = [(r["node"], r["attempt"], r["disposition"]) for r in out.trace.records
        if r["kind"] == "transition"]
counted = sum(1 for r in out.trace.records
              if r["kind"] == "transition" and r["disposition"] in ("commit", "continue"))
j, _, _ = both_forms(out.trace, doc)
print(f"   status={out.status} counted={counted} violations={len(j)}")
print(f"   rows={rows}")

print("\n=== B5: forged traces with wrong activation counts ===")
doc = json.loads(json.dumps(CYCLE_DOC))
good = Runtime(CYCLE, {"a": ident, "b": ident, "z": ident},
               policy=Policy.EXPLORATION).run(SEED)
base = good.trace.to_dict()
for label, mutate in (
    ("drop one counted transition", lambda d: d["records"].__setitem__(
        slice(0, len(d["records"])),
        [r for i, r in enumerate(d["records"]) if not (r["kind"] == "transition" and r["seq"] == 6)])),
):
    forged = json.loads(json.dumps(base))
    mutate(forged)
    for index, record in enumerate(forged["records"], start=1):
        record["seq"] = index
    v = validate.validate_trace(forged, spec=doc)
    print(f"   {label:32}: {len(v)} violation(s) {[x.rule for x in v][:4]}")

print("\n=== B6: does the runtime ever emit the terminal at count != limit? ===")
mismatch = 0
for limit in range(1, 9):
    for policy in (Policy.STRICT, Policy.EXPLORATION):
        doc = json.loads(json.dumps(CYCLE_DOC))
        doc["max_transitions"] = limit
        spec = GraphSpec.from_dict(doc)
        out = Runtime(spec, {"a": ident, "b": ident, "z": ident}, policy=policy).run(SEED)
        if out.trace.records[-1].get("cause", {}).get("kind") != "transition_limit_exceeded":
            continue
        counted = sum(1 for r in out.trace.records
                      if r["kind"] == "transition" and r["disposition"] in ("commit", "continue"))
        v = validate.validate_trace(out.trace.to_dict(), spec=doc)
        if counted != limit or v:
            mismatch += 1
            print(f"   MISMATCH limit={limit} policy={policy.value} counted={counted} v={[x.rule for x in v]}")
print(f"   mismatches across limits 1..8 x 2 policies: {mismatch}")

# ========================= C. to_dict materialization ====================== #
print("\n=== C1: to_dict() performs fresh materialization ===")
import vitruvyan_motus.trace as trace_module  # noqa: E402
from unittest import mock  # noqa: E402

big = Runtime(RETRY, {"a": lambda s: s.with_fact(Fact("x", list(range(50)), "s", NOW)),
                      "b": ident}).run(State.empty("c1"))
real = trace_module.json.dumps
calls = []


def counting(*a, **k):
    calls.append(1)
    return real(*a, **k)


with mock.patch.object(trace_module.json, "dumps", counting):
    for _ in range(9):
        big.trace.to_dict()
print(f"   9 to_dict() calls -> {len(calls)} encoder invocations (must be 9)")

calls.clear()
with mock.patch.object(trace_module.json, "dumps", counting):
    big.trace.to_json()
    big.trace.to_json()
    for _ in range(3):
        big.trace.to_dict()
print(f"   to_json x2 then to_dict x3 -> {len(calls)} invocations "
      f"(1 cached encode + 3 materializations = 4)")

print("\n=== C2: isolation of returned documents ===")
d1 = big.trace.to_dict()
d1["records"][0]["kind"] = "FORGED"
d1["run"]["run_id"] = "FORGED"
d2 = big.trace.to_dict()
print("   second to_dict() unaffected  :", d2["records"][0]["kind"] != "FORGED"
      and d2["run"]["run_id"] != "FORGED")
print("   trace.records unaffected     :", big.trace.records[0]["kind"] == "run_started")
print("   to_json unaffected           :", "FORGED" not in big.trace.to_json())
print("   d1 and d2 are distinct objects:", d1["records"] is not d2["records"])

print("\n=== C3: call order does not change semantics ===")
a = Runtime(RETRY, {"a": lambda s: s.with_fact(Fact("x", 1, "s", NOW)), "b": ident}).run(
    State.empty("c3"), run_id="fixed-c3")
first_json = a.trace.to_json()
first_dict = a.trace.to_dict()
b = Runtime(RETRY, {"a": lambda s: s.with_fact(Fact("x", 1, "s", NOW)), "b": ident}).run(
    State.empty("c3"), run_id="fixed-c3")
second_dict = b.trace.to_dict()
second_json = b.trace.to_json()


def strip(document):
    document = json.loads(json.dumps(document))
    document["run"].pop("created_ts", None)
    for record in document["records"]:
        record.pop("ts", None)
    return document


print("   dict-then-json == json-then-dict:", strip(first_dict) == strip(second_dict))
print("   to_json stable across order      :",
      json.dumps(strip(json.loads(first_json))) == json.dumps(strip(json.loads(second_json))))
