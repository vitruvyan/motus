"""Final gate — areas 12-15: trace agreement, packaging, SLO evidence,
and a fresh-defect sweep over the code 81db21c introduced."""

from __future__ import annotations

import gc
import importlib.util
import json
import statistics
import sys
import threading
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
_sp = importlib.util.spec_from_file_location("fv4", ROOT / "contract" / "validate.py")
validate = importlib.util.module_from_spec(_sp)
sys.modules["fv4"] = validate
_sp.loader.exec_module(validate)

from vitruvyan_motus import (  # noqa: E402
    Decision, DurabilityProfile, EffectClass, EffectDescriptor, EffectReceipt,
    Fact, GraphSpec, InMemoryTraceSink, NodeFailed, Policy, Rejection,
    ReplayEngine, ReplayStatus, Runtime, SinkFailed, State, TraceBundle, redact,
)

NOW = datetime(2026, 8, 4, tzinfo=timezone.utc)
ident = lambda s: s

LINEAR_DOC = {
    "schema_version": "1.0.0", "name": "sw", "version": "1.0.0", "entry": "a",
    "nodes": [{"name": "a", "effect_class": "pure"}, {"name": "b", "effect_class": "pure"}],
    "transitions": {"a": {"kind": "next", "to": "b"}, "b": {"kind": "terminal"}},
}
CYCLE_DOC = {
    "schema_version": "1.0.0", "name": "swc", "version": "1.0.0", "entry": "a",
    "max_transitions": 3,
    "nodes": [{"name": n, "effect_class": "pure"} for n in ("a", "b", "z")],
    "transitions": {"a": {"kind": "route", "on": "k", "map": {"loop": "b", "stop": "z"}},
                    "b": {"kind": "next", "to": "a"}, "z": {"kind": "terminal"}},
}
EXT_DOC = {
    "schema_version": "1.0.0", "name": "swe", "version": "1.0.0", "entry": "a",
    "nodes": [{"name": "a", "effect_class": "external_effect"},
              {"name": "b", "effect_class": "recorded_effect"}],
    "transitions": {"a": {"kind": "next", "to": "b"}, "b": {"kind": "terminal"}},
}
SEED = State.new("sw", decisions=[Decision("k", "loop", NOW)])

cases: list[tuple[str, object, dict]] = []


def sweep(label, build, spec_doc):
    try:
        obj = build()
    except Exception as exc:  # noqa: BLE001
        cases.append((label, f"BUILD {type(exc).__name__}", 0, 0, True))
        return
    trace = obj.trace if hasattr(obj, "trace") else obj
    try:
        document, stream = trace.to_dict(), trace.to_jsonl()
    except Exception as exc:  # noqa: BLE001
        cases.append((label, f"SER {type(exc).__name__}", 0, 0, True))
        return
    complete = document["records"][-1]["kind"] in ("run_completed", "run_failed", "run_cancelled")
    j = validate.validate_trace(document, spec=spec_doc, expect_complete=complete)
    l, _ = validate.validate_jsonl(stream, spec=spec_doc, expect_complete=complete)
    same = sorted((x.rule, x.path, x.message) for x in j) == \
        sorted((x.rule, x.path, x.message) for x in l)
    cases.append((label, "ok" if not j and not l else "VIOLATIONS", len(j), len(l), same))
    for x in j[:2]:
        cases.append((f"    {x.rule} {x.path}", x.message[:80], 0, 0, True))


LINEAR = GraphSpec.from_dict(dict(LINEAR_DOC))
CYCLE = GraphSpec.from_dict(dict(CYCLE_DOC))
EXT = GraphSpec.from_dict(dict(EXT_DOC))


def raiser(state):
    raise RuntimeError("x")


def declines(state):
    return state.with_rejection(Rejection("w", "r", NOW))


def redactor(state):
    return state.with_fact(Fact("s", redact({"pw": "x"}, "policy://p"), "s", NOW))


def drawer(state, ctx):
    return state.with_fact(Fact("t", ctx.uuid(), "s", ctx.now()))


def effectful(state, ctx):
    ctx.record_effect(EffectDescriptor(EffectClass.EXTERNAL_EFFECT, "POST",
                                       idempotency_key="k",
                                       receipt=EffectReceipt("r", "completed")))
    return state


def recorded(state, ctx):
    ctx.record_effect(EffectDescriptor(EffectClass.RECORDED_EFFECT, "GET"))
    return state


def failing_sink_run():
    class S:
        def open_run(self, header):
            raise OSError("no")
    rt = Runtime(LINEAR, {"a": ident, "b": ident}, sink=S())
    try:
        rt.run(State.empty("s"))
    except SinkFailed:
        pass
    return rt


def cancelled_pre_run():
    rt = Runtime(LINEAR, {"a": ident, "b": ident})
    rt.cancel("queued")
    return rt.run(State.empty("s"))


def stream_then_reuse():
    rt = Runtime(LINEAR, {"a": ident, "b": ident})
    with rt.stream(State.empty("s")) as d:
        for _ in d:
            pass
    return rt.run(State.empty("s2"))


def sink_profile(profile):
    def build():
        return Runtime(LINEAR, {"a": ident, "b": ident}, durability_profile=profile,
                       sink=InMemoryTraceSink()).run(State.empty("s"))
    return build


print("=== S1: trace / schema / validator agreement over every changed path ===")
sweep("happy linear", lambda: Runtime(LINEAR, {"a": ident, "b": ident}).run(State.empty("s")), LINEAR_DOC)
sweep("evidence-free rejection", lambda: Runtime(LINEAR, {"a": declines, "b": ident}).run(State.empty("s")), LINEAR_DOC)
sweep("redacted value", lambda: Runtime(LINEAR, {"a": redactor, "b": ident}).run(State.empty("s")), LINEAR_DOC)
sweep("context draws", lambda: Runtime(LINEAR, {"a": drawer, "b": ident}).run(State.empty("s"), replay=ReplayStatus.declared("full")), LINEAR_DOC)
sweep("exploration cycle at the limit", lambda: Runtime(CYCLE, {"a": raiser, "b": raiser, "z": raiser}, policy=Policy.EXPLORATION).run(SEED), CYCLE_DOC)
sweep("effects + receipts", lambda: Runtime(EXT, {"a": effectful, "b": recorded}).run(State.empty("s")), EXT_DOC)
sweep("pre-run cancellation", cancelled_pre_run, LINEAR_DOC)
sweep("stream exhausted then reused", stream_then_reuse, LINEAR_DOC)
sweep("sink open failure", failing_sink_run, LINEAR_DOC)
for profile in ("in-memory", "buffered", "synchronous"):
    sweep(f"attached sink, {profile}", sink_profile(profile), LINEAR_DOC)
for label, status, j, l, same in cases:
    if status in ("ok", "VIOLATIONS"):
        print(f"   {label:34} {status:12} json={j} jsonl={l} equivalent={same}")
    else:
        print(f"   {label:34} {status}")

print("\n=== S2: packaging, public API and frozen paths ===")
import subprocess  # noqa: E402

import vitruvyan_motus as pkg  # noqa: E402

base_all = subprocess.run(
    ["git", "show", "origin/main:src/vitruvyan_motus/__init__.py"],
    capture_output=True, text=True, cwd=str(ROOT)).stdout
base_names = set(json.loads(json.dumps(
    [n.strip().strip('",') for n in base_all.split("__all__ = [")[1].split("]")[0]
     .replace("\n", " ").split(",") if n.strip().strip('",')])))
head_names = set(pkg.__all__)
print(f"   __all__ size: base={len(base_names)} head={len(head_names)}")
print(f"   removed from the public API: {sorted(base_names - head_names) or 'none'}")
print(f"   added to the public API    : {sorted(head_names - base_names) or 'none'}")
print(f"   every __all__ name resolves: {all(hasattr(pkg, n) for n in pkg.__all__)}")
frozen = subprocess.run(["git", "diff", "--stat", "origin/main...HEAD", "--",
                         "tests/contract/", "tests/compat/"],
                        capture_output=True, text=True, cwd=str(ROOT)).stdout.strip()
print(f"   frozen corpora diff: {frozen or '(empty)'}")

print("\n=== S3: SLO evidence re-verified from the committed raw runs ===")
import hashlib  # noqa: E402

path = ROOT / "benchmarks" / "candidate-v0.6.1-epyc-py310.json"
print(f"   sha256={hashlib.sha256(path.read_bytes()).hexdigest()}")
doc = json.loads(path.read_text())
runs = doc["runs"]


def across(*keys):
    out = []
    for run in runs:
        node = run
        for key in keys:
            node = node[key]
        out.append(node)
    return out


per_node = statistics.median(across("3_runner_realistic", "1000", "us_per_node_min"))
noop = statistics.median([r["2_runner_noop"]["100"]["overhead_us_per_node_min"] * 0.1 for r in runs])
ratio = (statistics.median(across("6_serialization", "realistic_1000", "to_dict_min_ms")) /
         statistics.median(across("6_serialization", "realistic_1000", "json_dumps_min_ms")))
scaling = statistics.median(across("4_scaling", "growth_ratio_w10_over_w1"))
superlinear = max(0.0, (scaling - 1.0) / scaling * 100.0)
ceilings = {"per_node": 45 * 1.25, "noop": 3.25 * 1.25, "ratio": 1.5 * 1.25, "superlinear": 12.5}
print(f"   runs={len(runs)} per_node={per_node:.4f} (ceiling {ceilings['per_node']}) "
      f"noop={noop:.5f} (ceiling {ceilings['noop']})")
print(f"   cold ratio={ratio:.4f} (ceiling {ceilings['ratio']}) "
      f"superlinear={superlinear:.4f}% (ceiling {ceilings['superlinear']})")
print(f"   inside every ceiling: "
      f"{per_node <= ceilings['per_node'] and noop <= ceilings['noop'] and ratio <= ceilings['ratio'] and superlinear < ceilings['superlinear']}")
print(f"   completeness: "
      f"{all(r['6_serialization']['realistic_1000']['events_len'] == 3002 and r['6_serialization']['realistic_1000']['violations_len'] == 0 for r in runs)}")
print(f"   identity: {sorted({r['env']['runtime'] for r in runs})} "
      f"{sorted({r['env']['python'] for r in runs})}")
print(f"   benchmarks unchanged since the reviewed 746b91b: "
      f"{not subprocess.run(['git','diff','--stat','746b91b..HEAD','--','benchmarks/'], capture_output=True, text=True, cwd=str(ROOT)).stdout.strip()}")

print("\n=== S4: fresh-defect sweep over the 81db21c code ===")
LOCK_DOC = dict(LINEAR_DOC)
print("   a) reentrant cancel() from inside a listener (lock is an RLock):")
box: list[Runtime] = []


class Reentrant:
    def on_record(self, record):
        box[0].cancel("reentrant")
        box[0].cancel("reentrant again")


rt = Runtime(LINEAR, {"a": ident, "b": ident}, listeners=(Reentrant(),))
box.append(rt)
out = rt.run(State.empty("s4a"))
print(f"      status={out.status} (no deadlock)")

print("   b) cancel() from many threads during one run:")
rt = Runtime(LINEAR, {"a": lambda s: s, "b": lambda s: s})
results: list[bool] = []
stop = threading.Event()


def hammer():
    while not stop.is_set():
        results.append(rt.cancel("hammer"))


threads = [threading.Thread(target=hammer, daemon=True) for _ in range(4)]
for t in threads:
    t.start()
out = rt.run(State.empty("s4b"))
stop.set()
for t in threads:
    t.join(timeout=2)
print(f"      status={out.status} cancel() calls={len(results)} "
      f"true={sum(results)} false={len(results)-sum(results)}")
print(f"      pending leaked={rt._pending_cancel_reason!r}")

print("   c) abandoned StreamDriver, GC finalisation, then reuse:")
rt = Runtime(LINEAR, {"a": ident, "b": ident})
d = rt.stream(State.empty("s4c"))
next(d)
del d
gc.collect()
print(f"      running after GC={rt._running} pending={rt._pending_cancel_reason!r}")
try:
    print(f"      next run: {rt.run(State.empty('s4c2')).status}")
except RuntimeError as exc:
    print(f"      next run refused: {exc}")

print("   d) StreamDriver reused after close(), and double close():")
rt = Runtime(LINEAR, {"a": ident, "b": ident})
d = rt.stream(State.empty("s4d"))
next(d)
d.close()
d.close()
try:
    next(d)
    print("      next() after close returned a record  <-- unexpected")
except StopIteration:
    print("      next() after close -> StopIteration (correct)")
print(f"      next run: {rt.run(State.empty('s4d2')).status}")

print("   e) exhausted driver: is the trace still readable and terminal?")
rt = Runtime(LINEAR, {"a": ident, "b": ident})
with rt.stream(State.empty("s4e")) as d:
    kinds = [r["kind"] for r in d]
print(f"      streamed kinds={kinds[-1]} driver.trace terminal="
      f"{d.trace.records[-1]['kind']} records={len(d.trace.records)}")

print("   f) sink attached AND a listener that cancels — ordering:")


class CancelAt:
    def __init__(self, box, kind):
        self.box, self.kind = box, kind

    def on_record(self, record):
        if record["kind"] == self.kind:
            self.box[0].cancel("listener")


sink = InMemoryTraceSink()
box = []
rt = Runtime(LINEAR, {"a": ident, "b": ident}, durability_profile="synchronous",
             sink=sink, listeners=(CancelAt(box, "transition"),))
box.append(rt)
out = rt.run(State.empty("s4f"))
print(f"      status={out.status} sink_records={len(sink.records)} "
      f"trace_records={len(out.trace.records)} identical="
      f"{[r['kind'] for r in sink.records] == [r['kind'] for r in out.trace.records]}")
j = validate.validate_trace(out.trace.to_dict(), spec=LINEAR_DOC)
print(f"      validator: {len(j)} violation(s) {[x.rule for x in j][:3]}")

print("   g) replay/resume still intact after the changes:")
rt = Runtime(LINEAR, {"a": lambda s: s.with_fact(Fact("x", 1, "s", NOW)), "b": ident})
d = rt.stream(State.empty("s4g"))
for _ in range(3):
    next(d)
source = d.trace
d.close()
engine = ReplayEngine(TraceBundle(LINEAR, source))
resumed = engine.resume(Runtime(LINEAR, {"a": lambda s: s.with_fact(Fact("x", 1, "s", NOW)), "b": ident}))
print(f"      resume status={resumed.status} start={resumed.trace.run['resume']['start_node']} "
      f"new_id={resumed.trace.run['run_id'] != source.run['run_id']}")
print(f"      playback fact x = {engine.playback().state.fact('x')}")
