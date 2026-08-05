"""Attack C/E: Rejection without `evidence` poisons the trace with a raw
Python object.

trace.py uses a module-level sentinel ``_MISSING = object()`` to mean "this
Rejection carries no evidence".  ``State.with_rejection`` stores
``copy.deepcopy(rejection)`` -- and ``copy.deepcopy(object())`` returns a NEW
object, so the copy's ``evidence`` is no longer *identical* to ``_MISSING``.
``Rejection.to_dict`` tests identity, so it then emits the sentinel object
itself as the ``evidence`` value.

``Trace._append_runtime`` deliberately skips ``_strict_plain_json`` ("already
built from validated runtime primitives"), so the non-JSON object reaches the
trace unchecked.
"""

from __future__ import annotations

import copy
import json
import tempfile
from datetime import datetime, timezone
from pathlib import Path

from vitruvyan_motus import (
    GraphSpec, Rejection, ReplayEngine, Runtime, State, TraceBundle,
)

NOW = datetime(2026, 8, 4, tzinfo=timezone.utc)

SPEC = GraphSpec.from_dict({
    "schema_version": "1.0.0", "name": "rej", "version": "1.0.0", "entry": "a",
    "nodes": [{"name": "a", "effect_class": "pure"}],
    "transitions": {"a": {"kind": "terminal"}},
})


def node(state):
    return state.with_rejection(Rejection("thing", "not applicable", NOW))


print("=== 0. the sentinel survives deepcopy? ===")
from vitruvyan_motus.trace import _MISSING  # noqa: E402
r = Rejection("w", "r", NOW)
print("   original.evidence is _MISSING :", r.evidence is _MISSING)
print("   deepcopy.evidence is _MISSING :", copy.deepcopy(r).evidence is _MISSING)
print("   deepcopy.to_dict() keys       :", sorted(copy.deepcopy(r).to_dict()))

print("\n=== 1. a run that writes an evidence-free rejection ===")
result = Runtime(SPEC, {"a": node}).run(State.empty("rej"))
print("   status                        :", result.status, "/ succeeded:", result.succeeded)
txn = [x for x in result.trace.records if x["kind"] == "transition"][0]
print("   trace writes.rejections       :", txn["writes"]["rejections"])
print("   evidence type in the trace    :", type(txn["writes"]["rejections"][0].get("evidence")).__name__)

print("\n=== 2. can the trace be serialized at all? ===")
for label, fn in (("to_json", result.trace.to_json), ("to_jsonl", result.trace.to_jsonl),
                  ("to_dict", result.trace.to_dict)):
    try:
        fn()
        print(f"   {label:9}: OK")
    except Exception as exc:  # noqa: BLE001
        print(f"   {label:9}: {type(exc).__name__}: {exc}")

print("\n=== 3. can the run state be snapshotted / replayed? ===")
try:
    snap = result.state.snapshot()
    print("   snapshot()                    : OK ->", snap["rejections"])
except Exception as exc:  # noqa: BLE001
    print(f"   snapshot()                    : {type(exc).__name__}: {exc}")
try:
    bundle = TraceBundle(SPEC, result.trace)
    print("   TraceBundle()                 : OK")
    try:
        ReplayEngine(bundle).playback()
        print("   playback()                    : OK")
    except Exception as exc:  # noqa: BLE001
        print(f"   playback()                    : {type(exc).__name__}: {exc}")
    try:
        bundle.explain()
        print("   explain()                     : OK")
    except Exception as exc:  # noqa: BLE001
        print(f"   explain()                     : {type(exc).__name__}: {exc}")
    try:
        bundle.to_json()
        print("   bundle.to_json()              : OK")
    except Exception as exc:  # noqa: BLE001
        print(f"   bundle.to_json()              : {type(exc).__name__}: {exc}")
except Exception as exc:  # noqa: BLE001
    print(f"   TraceBundle()                 : {type(exc).__name__}: {exc}")

print("\n=== 4. a realistic JSON sink under the synchronous profile ===")


class JsonFileRunSink:
    def __init__(self, path):
        self.path = path

    def write(self, records):
        with open(self.path, "a", encoding="utf-8") as handle:
            for record in records:
                handle.write(json.dumps(record) + "\n")


class JsonFileSink:
    def __init__(self, path):
        self.path = path

    def open_run(self, header):
        return JsonFileRunSink(self.path)


with tempfile.TemporaryDirectory() as tmp:
    path = Path(tmp) / "trace.jsonl"
    try:
        out = Runtime(SPEC, {"a": node}, durability_profile="synchronous",
                      sink=JsonFileSink(path)).run(State.empty("rej"))
        print("   synchronous sink run          : status =", out.status)
    except Exception as exc:  # noqa: BLE001
        print(f"   synchronous sink run          : {type(exc).__name__}: {exc}")
        print("   persisted lines               :", len(path.read_text().splitlines()) if path.exists() else 0)

print("\n=== 5. a rejection WITH evidence (control) ===")


def node_ok(state):
    return state.with_rejection(Rejection("thing", "not applicable", NOW, evidence={"n": 1}))


ok = Runtime(SPEC, {"a": node_ok}).run(State.empty("rej"))
print("   to_json                       : OK, len =", len(ok.trace.to_json()))

print("\n=== 6. seeded (initial-state) rejection, same defect ===")
seeded = State.new("seed", rejections=[Rejection("w", "r", NOW)])
try:
    out = Runtime(SPEC, {"a": lambda s: s}).run(seeded)
    out.trace.to_json()
    print("   seeded run to_json            : OK")
except Exception as exc:  # noqa: BLE001
    print(f"   seeded run to_json            : {type(exc).__name__}: {exc}")
