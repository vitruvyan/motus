"""Re-audit Phase 4: defects introduced BY the 0.6.1 remediation."""

from __future__ import annotations

import functools
import importlib.util
import json
import sys
import threading
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
_spec = importlib.util.spec_from_file_location("rv4", ROOT / "contract" / "validate.py")
validate = importlib.util.module_from_spec(_spec)
sys.modules["rv4"] = validate
_spec.loader.exec_module(validate)

from vitruvyan_motus import (  # noqa: E402
    Decision, Fact, GraphSpec, NodeFailed, Policy, ReplayEngine, Runtime,
    State, TraceBundle,
)

NOW = datetime(2026, 8, 4, tzinfo=timezone.utc)
ident = lambda s: s
SINGLE_DOC = {
    "schema_version": "1.0.0", "name": "p4", "version": "1.0.0", "entry": "a",
    "nodes": [{"name": "a", "effect_class": "pure"}],
    "transitions": {"a": {"kind": "terminal"}},
}
SINGLE = GraphSpec.from_dict(dict(SINGLE_DOC))
LINEAR_DOC = {
    "schema_version": "1.0.0", "name": "p4l", "version": "1.0.0", "entry": "a",
    "nodes": [{"name": "a", "effect_class": "pure"}, {"name": "b", "effect_class": "pure"}],
    "transitions": {"a": {"kind": "next", "to": "b"}, "b": {"kind": "terminal"}},
}
LINEAR = GraphSpec.from_dict(dict(LINEAR_DOC))

print("=== N1: node identity now goes through functools.lru_cache ===")
print("    (0.6.0 hashed nothing; 0.6.1 uses the callable itself as a cache key)")


@dataclass
class DataclassNode:
    """The ordinary dataclass shape: eq=True implies __hash__ = None."""

    factor: int

    def run_step(self, state):
        return state.with_fact(Fact("v", self.factor, "s", NOW))

    def __call__(self, state):
        return state.with_fact(Fact("v", self.factor, "s", NOW))


class ManualEq:
    def __init__(self, factor):
        self.factor = factor

    def __eq__(self, other):
        return isinstance(other, ManualEq) and other.factor == self.factor

    def step(self, state):
        return state.with_fact(Fact("v", self.factor, "s", NOW))


cases = {
    "bound method of a @dataclass": DataclassNode(2).run_step,
    "bound method of a class with __eq__": ManualEq(2).step,
    "callable @dataclass instance": DataclassNode(2),
    "partial over a dataclass bound method": functools.partial(DataclassNode(2).run_step),
}
for label, node in cases.items():
    try:
        out = Runtime(SINGLE, {"a": node}).run(State.empty("n1"))
        print(f"   {label:40} -> ran, status={out.status}")
    except Exception as exc:  # noqa: BLE001
        print(f"   {label:40} -> {type(exc).__name__}: {str(exc)[:70]}")

print("\n   is the same shape accepted at 0.6.0? (structural check)")
import inspect  # noqa: E402
for label, node in cases.items():
    target = inspect.unwrap(node.func if isinstance(node, functools.partial) else node)
    if not inspect.isfunction(target) and not inspect.ismethod(target) and callable(target):
        target = inspect.unwrap(type(target).__call__)
    try:
        hash(target)
        hashable = "hashable"
    except TypeError as exc:
        hashable = f"UNHASHABLE ({exc})"
    print(f"   {label:40} identity key: {hashable}")

print("\n=== N2: async node (declared shape violation) fails cleanly? ===")


async def async_node(state):
    return state


try:
    out = Runtime(SINGLE, {"a": async_node}).run(State.empty("n2"))
    print(f"   ran: status={out.status} — coroutine treated as a result?")
    txn = next(r for r in out.trace.records if r["kind"] == "transition")
    print(f"   transition outcome={txn['outcome']} error={txn['error']}")
except Exception as exc:  # noqa: BLE001
    print(f"   {type(exc).__name__}: {str(exc)[:90]}")

print("\n=== N3: resume restarts the activation counter ===")
CYCLE_DOC = {
    "schema_version": "1.0.0", "name": "cyc4", "version": "1.0.0", "entry": "a",
    "max_transitions": 2,
    "nodes": [{"name": n, "effect_class": "pure"} for n in ("a", "b", "z")],
    "transitions": {"a": {"kind": "route", "on": "k", "map": {"loop": "b", "stop": "z"}},
                    "b": {"kind": "next", "to": "a"}, "z": {"kind": "terminal"}},
}
CYCLE = GraphSpec.from_dict(dict(CYCLE_DOC))
SEED = State.new("n3", decisions=[Decision("k", "loop", NOW)])
driver = Runtime(CYCLE, {"a": ident, "b": ident, "z": ident},
                 policy=Policy.EXPLORATION).stream(SEED)
records = []
for record in driver:
    records.append(record["kind"])
    if len(records) >= 4:
        break
source = driver.trace
print(f"   stopped mid-cycle after {len(source.records)} records")
try:
    engine = ReplayEngine(TraceBundle(CYCLE, source))
    resumed = engine.resume(Runtime(CYCLE, {"a": ident, "b": ident, "z": ident},
                                    policy=Policy.EXPLORATION))
    counted = sum(1 for r in resumed.trace.records
                  if r["kind"] == "transition" and r["disposition"] in ("commit", "continue"))
    print(f"   resumed segment: status={resumed.status} counted={counted} "
          f"(limit={CYCLE_DOC['max_transitions']})")
    v = validate.validate_trace(resumed.trace.to_dict(), spec=CYCLE_DOC)
    print(f"   resumed trace violations: {len(v)} {[x.rule for x in v][:3]}")
    print("   >>> the cycle bound is per SEGMENT, so repeated resume extends it")
except Exception as exc:  # noqa: BLE001
    print(f"   resume -> {type(exc).__name__}: {str(exc)[:80]}")
try:
    driver.close()
except Exception:
    pass

print("\n=== N4: _refresh_identity runs inside _start — what if config raises? ===")


class Angry:
    def __init__(self):
        self.calls = 0

    def motus_config(self):
        self.calls += 1
        if self.calls > 1:
            raise RuntimeError("config exploded on the second run")
        return {"ok": True}

    def __call__(self, state):
        return state


angry = Angry()
rt = Runtime(SINGLE, {"a": angry})
try:
    print("   run 1:", rt.run(State.empty("n4")).status)
except Exception as exc:
    print("   run 1 ->", type(exc).__name__, str(exc)[:60])
try:
    rt.run(State.empty("n4b"))
    print("   run 2: completed")
except Exception as exc:  # noqa: BLE001
    print(f"   run 2 -> {type(exc).__name__}: {str(exc)[:70]}")
print(f"   runtime usable afterwards? _running={rt._running}")
try:
    print("   run 3:", rt.run(State.empty("n4c")).status)
except Exception as exc:  # noqa: BLE001
    print(f"   run 3 -> {type(exc).__name__}: {str(exc)[:70]}")


class NonJson:
    def motus_config(self):
        return {"when": datetime.now(timezone.utc)}

    def __call__(self, state):
        return state


try:
    Runtime(SINGLE, {"a": NonJson()})
    print("   non-JSON motus_config accepted at construction  <-- unexpected")
except Exception as exc:  # noqa: BLE001
    print(f"   non-JSON motus_config refused: {type(exc).__name__}: {str(exc)[:60]}")

print("\n=== N5: does motus_config() observe or mutate anything per run? ===")


class SideEffecting:
    def __init__(self):
        self.n = 0

    def motus_config(self):
        self.n += 1
        return {"call": self.n}          # config changes merely by being read

    def __call__(self, state):
        return state


se = SideEffecting()
rt = Runtime(SINGLE, {"a": se})
fps = [rt.run(State.empty("n5")).trace.run["graph"]["code_fingerprint"] for _ in range(3)]
print(f"   self-incrementing config -> distinct fingerprints: {len(set(fps))} of 3")
print(f"   motus_config() invocations: {se.n}")

print("\n=== N6: in-memory + sink, failure asymmetry ===")


class Sink:
    def __init__(self, fail_open=False, fail_write=False):
        self.fail_open, self.fail_write = fail_open, fail_write
        self.records = []

    def open_run(self, header):
        if self.fail_open:
            raise OSError("cannot open")
        return self

    def write(self, records):
        if self.fail_write:
            raise OSError("cannot write")
        self.records.extend(records)


from vitruvyan_motus import SinkFailed  # noqa: E402

for label, sink in (("open fails", Sink(fail_open=True)), ("write fails", Sink(fail_write=True))):
    rt = Runtime(LINEAR, {"a": ident, "b": ident}, durability_profile="in-memory", sink=sink)
    try:
        res = rt.run(State.empty("n6"))
        print(f"   in-memory, {label:12}: status={res.status}")
    except SinkFailed as exc:
        print(f"   in-memory, {label:12}: SinkFailed ({exc.cause})")
print("   contract: invariant II binds the sink REQUIRED BY THE PROFILE;")
print("   in-memory requires none, so these two outcomes cannot both be right.")

print("\n=== N7: chunk_records / flush_interval are ignored under in-memory ===")
sink = Sink()
rt = Runtime(LINEAR, {"a": ident, "b": ident}, durability_profile="in-memory",
             sink=sink, chunk_records=1000, flush_interval_ms=600000)
out = rt.run(State.empty("n7"))
print(f"   records forwarded despite chunk_records=1000: {len(sink.records)}")
print(f"   header advertises a sink object: {'sink' in out.trace.run}")

print("\n=== N8: concurrent cancel() while a run is starting ===")
outcomes = {"cancelled": 0, "completed": 0, "leaked": 0, "other": 0}
for _ in range(300):
    rt = Runtime(LINEAR, {"a": ident, "b": ident})
    barrier = threading.Barrier(2, timeout=5)

    def canceller():
        barrier.wait()
        rt.cancel("concurrent")

    thread = threading.Thread(target=canceller)
    thread.start()
    barrier.wait()
    result = rt.run(State.empty("n8"))
    thread.join()
    outcomes[result.status if result.status in outcomes else "other"] += 1
    if rt._pending_cancel_reason is not None:
        outcomes["leaked"] += 1
print(f"   300 rounds: {outcomes}")
print("   'leaked' = a cancellation left queued for an unrelated later run")

print("\n=== N9: trace/runtime agreement sweep over the changed paths ===")
cases = []


def sweep(label, build, spec_doc):
    try:
        obj = build()
    except Exception as exc:  # noqa: BLE001
        cases.append((label, f"BUILD {type(exc).__name__}", None, None, None))
        return
    trace = obj.trace
    try:
        document = trace.to_dict()
        stream = trace.to_jsonl()
    except Exception as exc:  # noqa: BLE001
        cases.append((label, f"SER {type(exc).__name__}", None, None, None))
        return
    complete = document["records"][-1]["kind"] in ("run_completed", "run_failed", "run_cancelled")
    j = validate.validate_trace(document, spec=spec_doc, expect_complete=complete)
    l, _ = validate.validate_jsonl(stream, spec=spec_doc, expect_complete=complete)
    same = sorted((x.rule, x.path, x.message) for x in j) == \
        sorted((x.rule, x.path, x.message) for x in l)
    cases.append((label, "ok", len(j), len(l), same))


from vitruvyan_motus import Rejection  # noqa: E402


def declines(state):
    return state.with_rejection(Rejection("w", "r", NOW))


def raiser(state):
    raise RuntimeError("x")


sweep("evidence-free rejection", lambda: Runtime(SINGLE, {"a": declines}).run(State.empty("s")), SINGLE_DOC)
sweep("exploration cycle at the limit",
      lambda: Runtime(CYCLE, {"a": raiser, "b": raiser, "z": raiser},
                      policy=Policy.EXPLORATION).run(SEED), CYCLE_DOC)
sweep("pre-run cancellation",
      lambda: (lambda r: (r.cancel("queued"), r.run(State.empty("s")))[1])(
          Runtime(LINEAR, {"a": ident, "b": ident})), LINEAR_DOC)


def leaked_run():
    rt = Runtime(LINEAR, {"a": ident, "b": ident})
    with rt.stream(State.empty("s")) as d:
        for _ in d:
            pass
    return rt.run(State.empty("s2"))


sweep("run after a closed exhausted stream", leaked_run, LINEAR_DOC)
sweep("indexed decision origin",
      lambda: Runtime(CYCLE, {"a": ident, "b": ident, "z": ident},
                      policy=Policy.EXPLORATION).run(
          State.new("s", facts=[Fact(f"f{i}", i, "s", NOW) for i in range(100)],
                    decisions=[Decision("k", "loop", NOW)])), CYCLE_DOC)
for label, status, j, l, same in cases:
    print(f"   {label:36} {status:22} json={j} jsonl={l} equivalent={same}")
