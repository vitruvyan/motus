"""Final gate — areas 6-11 plus the two leads from f01."""

from __future__ import annotations

import copy
import importlib.util
import json
import multiprocessing
import pickle
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
_sp = importlib.util.spec_from_file_location("fv", ROOT / "contract" / "validate.py")
validate = importlib.util.module_from_spec(_sp)
sys.modules["fv"] = validate
_sp.loader.exec_module(validate)

from vitruvyan_motus import (  # noqa: E402
    DurabilityProfile, Fact, GraphSpec, InMemoryTraceSink, Rejection, Runtime,
    SinkFailed, State,
)
from vitruvyan_motus.errors import NodeConfigurationError  # noqa: E402
from vitruvyan_motus.trace import _MISSING, _Missing  # noqa: E402

NOW = datetime(2026, 8, 4, tzinfo=timezone.utc)
ident = lambda s: s
DOC = {
    "schema_version": "1.0.0", "name": "fg", "version": "1.0.0", "entry": "a",
    "nodes": [{"name": "a", "effect_class": "pure"}, {"name": "b", "effect_class": "pure"}],
    "transitions": {"a": {"kind": "next", "to": "b"}, "b": {"kind": "terminal"}},
}
SPEC = GraphSpec.from_dict(dict(DOC))
SINGLE_DOC = {
    "schema_version": "1.0.0", "name": "fg1", "version": "1.0.0", "entry": "a",
    "nodes": [{"name": "a", "effect_class": "pure"}],
    "transitions": {"a": {"kind": "terminal"}},
}
SINGLE = GraphSpec.from_dict(dict(SINGLE_DOC))


def both(trace, spec_doc):
    document = trace.to_dict()
    complete = document["records"][-1]["kind"] in ("run_completed", "run_failed", "run_cancelled")
    j = validate.validate_trace(document, spec=spec_doc, expect_complete=complete)
    l, _ = validate.validate_jsonl(trace.to_jsonl(), spec=spec_doc, expect_complete=complete)
    same = sorted((x.rule, x.path, x.message) for x in j) == \
        sorted((x.rule, x.path, x.message) for x in l)
    return len(j), len(l), same, [x.rule for x in j][:3]


# ---- leads from f01 ------------------------------------------------------- #
print("=== G1: f01/F7 control — a ONE-SHOT listener cancellation ===")


class OneShot:
    def __init__(self, box):
        self.box, self.fired = box, False

    def on_record(self, record):
        if record["kind"] == "run_started" and not self.fired:
            self.fired = True
            self.box[0].cancel("one-shot")


box: list[Runtime] = []
listener = OneShot(box)
rt = Runtime(SPEC, {"a": ident, "b": ident}, listeners=(listener,))
box.append(rt)
first = rt.run(State.empty("g1"))
second = rt.run(State.empty("g1b"))
print(f"   run 1 status={first.status} | run 2 status={second.status} "
      f"| pending={rt._pending_cancel_reason!r}")
print("   -> the earlier 'LEAK' line was the listener re-firing, not a leaked request"
      if second.status == "completed" else "   -> GENUINE LEAK")

print("\n=== G2: a queued cancellation lost when the run fails to start ===")
for label, kwargs, state in (
    ("run_id too long", {"run_id": "x" * 201}, State.empty("g2")),
    ("state is not a State", {}, "not a state"),
):
    rt = Runtime(SPEC, {"a": ident, "b": ident})
    queued = rt.cancel("operator shutdown")
    try:
        rt.run(state, **kwargs)
        outcome = "ran"
    except Exception as exc:  # noqa: BLE001
        outcome = f"{type(exc).__name__}"
    again = rt.cancel("retry shutdown")
    follow = rt.run(State.empty("g2b"))
    print(f"   {label:22} queued={queued} start={outcome:16} "
          f"requeue={again!s:5} next_run={follow.status}")

# ---- sinks ---------------------------------------------------------------- #
print("\n=== G3: sink open/write failure across all three profiles ===")


class RunSink:
    def __init__(self, fail_write):
        self.fail_write, self.records = fail_write, []

    def write(self, records):
        if self.fail_write:
            raise OSError("write refused")
        self.records.extend(records)


class Sink:
    def __init__(self, fail_open=False, fail_write=False):
        self.fail_open = fail_open
        self.run_sink = RunSink(fail_write)

    def open_run(self, header):
        if self.fail_open:
            raise OSError("open refused")
        return self.run_sink


for profile in ("in-memory", "buffered", "synchronous"):
    row = []
    for label, sink in (("open", Sink(fail_open=True)), ("write", Sink(fail_write=True))):
        rt = Runtime(SPEC, {"a": ident, "b": ident}, durability_profile=profile, sink=sink)
        try:
            res = rt.run(State.empty("g3"))
            verdict = f"status={res.status}"
        except SinkFailed:
            verdict = "SinkFailed"
        except Exception as exc:  # noqa: BLE001
            verdict = type(exc).__name__
        terminal = rt.trace.records[-1]["kind"] if rt.trace else "-"
        row.append(f"{label}->{verdict}/{terminal}")
    print(f"   {profile:12}: " + "   ".join(row))

print("\n=== G4: header disclosure for an attached sink, all profiles ===")
for profile in ("in-memory", "buffered", "synchronous"):
    sink = InMemoryTraceSink()
    out = Runtime(SPEC, {"a": ident, "b": ident}, durability_profile=profile, sink=sink,
                  chunk_records=7, flush_interval_ms=250).run(State.empty("g4"))
    j, l, same, rules = both(out.trace, DOC)
    print(f"   {profile:12}: header.sink={out.trace.run.get('sink')} "
          f"delivered={len(sink.records)} json={j} jsonl={l} equiv={same}")
print("   no sink attached:")
out = Runtime(SPEC, {"a": ident, "b": ident}).run(State.empty("g4b"))
print(f"                 header has 'sink' key: {'sink' in out.trace.run}")

print("\n=== G5: sink failure evidence is itself contract-valid ===")
for profile in ("in-memory", "synchronous"):
    sink = Sink(fail_open=True)
    rt = Runtime(SPEC, {"a": ident, "b": ident}, durability_profile=profile, sink=sink)
    try:
        rt.run(State.empty("g5"))
    except SinkFailed:
        pass
    j, l, same, rules = both(rt.trace, DOC)
    print(f"   {profile:12}: kinds={[r['kind'] for r in rt.trace.records]} "
          f"json={j} jsonl={l} equiv={same} {rules}")

print("\n=== G6: an in-memory sink still buys no crash guarantee (record count) ===")
sink = InMemoryTraceSink()
out = Runtime(SPEC, {"a": ident, "b": ident}, durability_profile="in-memory",
              sink=sink, chunk_records=1000, flush_interval_ms=600000).run(State.empty("g6"))
print(f"   chunk_records=1000 ignored, forwarded synchronously: {len(sink.records)} records")
print(f"   profile in header: {out.trace.run['durability_profile']}")

# ---- rejection ------------------------------------------------------------ #
print("\n=== G7: rejection absence across every isolation mechanism ===")
r = Rejection("w", "r", NOW)
probes = {
    "original": r,
    "copy.copy": copy.copy(r),
    "copy.deepcopy": copy.deepcopy(r),
    "deepcopy x3": copy.deepcopy(copy.deepcopy(copy.deepcopy(r))),
}
for protocol in range(2, pickle.HIGHEST_PROTOCOL + 1):
    probes[f"pickle proto {protocol}"] = pickle.loads(pickle.dumps(r, protocol))
probes["pickle of the sentinel itself"] = Rejection(
    "w", "r", NOW, evidence=None) if False else r
for label, probe in probes.items():
    print(f"   {label:28} absent={('evidence' not in probe.to_dict())!s:5} "
          f"is_singleton={probe.evidence is _MISSING}")

print("\n   the bare sentinel through pickle:")
for protocol in range(2, pickle.HIGHEST_PROTOCOL + 1):
    restored = pickle.loads(pickle.dumps(_MISSING, protocol))
    print(f"      proto {protocol}: is _MISSING -> {restored is _MISSING}")

print("\n   nested containers holding a Rejection:")
nested = {"a": [Rejection("w", "r", NOW), {"b": (Rejection("x", "y", NOW),)}]}
back = pickle.loads(pickle.dumps(nested))
flat = [back["a"][0], back["a"][1]["b"][0]]
print(f"      all absent after round trip: {all('evidence' not in x.to_dict() for x in flat)}")


def _child(queue):
    from vitruvyan_motus import Rejection as R
    from vitruvyan_motus.trace import _MISSING as M
    obj = queue.get()
    queue.put(("evidence" not in obj.to_dict(), obj.evidence is M))


print("\n   multiprocessing round trip:")
try:
    ctx = multiprocessing.get_context("spawn")
    queue = ctx.Queue()
    proc = ctx.Process(target=_child, args=(queue,))
    proc.start()
    queue.put(Rejection("w", "r", NOW))
    absent, singleton = queue.get(timeout=30)
    proc.join(timeout=30)
    print(f"      child sees absent={absent} is_child_singleton={singleton}")
except Exception as exc:  # noqa: BLE001
    print(f"      {type(exc).__name__}: {exc}")


def declines(state):
    return state.with_rejection(Rejection("thing", "no", NOW))


print("\n   end-to-end run using an unpickled Rejection:")
unpickled = pickle.loads(pickle.dumps(Rejection("w", "r", NOW)))
out = Runtime(SINGLE, {"a": lambda s: s.with_rejection(unpickled)}).run(State.empty("g7"))
j, l, same, rules = both(out.trace, SINGLE_DOC)
txn = next(x for x in out.trace.records if x["kind"] == "transition")
print(f"      wire={txn['writes']['rejections'][0]} json={j} jsonl={l} equiv={same}")
print(f"      to_json ok: {len(out.trace.to_json())} chars")

print("\n   a user-constructed _Missing is still refused:")
for value in (_Missing(), pickle.loads(pickle.dumps(_Missing()))):
    try:
        Rejection("w", "r", NOW, evidence=value).to_dict()
        print(f"      accepted <-- unexpected ({value is _MISSING=})")
    except TypeError as exc:
        print(f"      refused: {str(exc)[:60]}")

# ---- motus_config --------------------------------------------------------- #
print("\n=== G8: motus_config obligations ===")


def probe_config(label, config_impl, at_construction=True):
    cls = type("N", (), {"motus_config": config_impl,
                         "__call__": lambda self, state: state})
    try:
        rt = Runtime(SINGLE, {"a": cls()})
    except Exception as exc:  # noqa: BLE001
        print(f"   {label:34} construction -> {type(exc).__name__}: {str(exc)[:44]}")
        return
    outcomes = []
    for _ in range(3):
        try:
            outcomes.append(rt.run(State.empty("g8")).trace.run["graph"]["code_fingerprint"][12:22])
        except Exception as exc:  # noqa: BLE001
            outcomes.append(f"{type(exc).__name__}")
    print(f"   {label:34} runs -> {outcomes}")


probe_config("returns a constant", lambda self: {"k": 1})
probe_config("raises always", lambda self: (_ for _ in ()).throw(RuntimeError("boom")))
probe_config("returns non-JSON", lambda self: {"t": datetime.now(timezone.utc)})
counter = {"n": 0}


def _side(self):
    counter["n"] += 1
    return {"n": counter["n"]}


probe_config("side-effecting (self-incrementing)", _side)


class Mutable:
    def __init__(self):
        self.value = 1

    def motus_config(self):
        return {"v": self.value}

    def __call__(self, state):
        return state.with_fact(Fact("v", self.value, "s", NOW))


print("\n   mutable configuration across runs:")
node = Mutable()
rt = Runtime(SINGLE, {"a": node})
f1 = rt.run(State.empty("g8")).trace.run["graph"]["code_fingerprint"]
f2 = rt.run(State.empty("g8")).trace.run["graph"]["code_fingerprint"]
node.value = 99
f3 = rt.run(State.empty("g8")).trace.run["graph"]["code_fingerprint"]
node.value = 1
f4 = rt.run(State.empty("g8")).trace.run["graph"]["code_fingerprint"]
print(f"      stable={f1 == f2} moves_on_change={f1 != f3} restores={f1 == f4}")

print("\n   is NodeConfigurationError a MotusError, and is it publicly importable?")
from vitruvyan_motus import MotusError  # noqa: E402
import vitruvyan_motus as pkg  # noqa: E402

print(f"      issubclass(NodeConfigurationError, MotusError): "
      f"{issubclass(NodeConfigurationError, MotusError)}")
print(f"      'NodeConfigurationError' in vitruvyan_motus.__all__: "
      f"{'NodeConfigurationError' in pkg.__all__}")
print(f"      hasattr(vitruvyan_motus, 'NodeConfigurationError'): "
      f"{hasattr(pkg, 'NodeConfigurationError')}")
try:
    exec("from vitruvyan_motus import NodeConfigurationError")
    print("      from vitruvyan_motus import NodeConfigurationError -> OK")
except ImportError as exc:
    print(f"      from vitruvyan_motus import NodeConfigurationError -> ImportError: {exc}")
print(f"      contract/node-protocol.md names it: "
      f"{'NodeConfigurationError' in (ROOT / 'contract' / 'node-protocol.md').read_text()}")
