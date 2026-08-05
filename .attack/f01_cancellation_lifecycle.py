"""Final gate — areas 1-5: StreamDriver exhaustion, cancellation lifecycle,
Runtime reuse, and the cancel/start/terminal races introduced by 81db21c."""

from __future__ import annotations

import gc
import threading
from datetime import datetime, timezone

from vitruvyan_motus import Fact, GraphSpec, NodeFailed, Policy, Runtime, State

NOW = datetime(2026, 8, 4, tzinfo=timezone.utc)
DOC = {
    "schema_version": "1.0.0", "name": "cx", "version": "1.0.0", "entry": "a",
    "nodes": [{"name": "a", "effect_class": "pure"}, {"name": "b", "effect_class": "pure"}],
    "transitions": {"a": {"kind": "next", "to": "b"}, "b": {"kind": "terminal"}},
}
SPEC = GraphSpec.from_dict(dict(DOC))
executed: list[str] = []


def node(state):
    executed.append("x")
    return state.with_fact(Fact("k", 1, "s", NOW))


def fresh():
    executed.clear()
    return Runtime(SPEC, {"a": node, "b": node})


def result_line(label, result):
    return (f"   {label:48} status={result.status:9} nodes={len(executed)} "
            f"terminal={result.trace.records[-1]['kind']}")


print("=== F1: RA-001 primary reproduction — with-block then reuse ===")
rt = fresh()
with rt.stream(State.empty("f1")) as driver:
    for _ in driver:
        pass
print(f"   after the with-block: driver closed={driver._closed} "
      f"pending={rt._pending_cancel_reason!r} has_started={rt._has_started}")
executed.clear()
print(result_line("NEXT run on the same Runtime", rt.run(State.empty("f1b"))))

print("\n=== F2: explicit close() after natural exhaustion ===")
rt = fresh()
d = rt.stream(State.empty("f2"))
for _ in d:
    pass
d.close()
print(f"   pending after explicit close(): {rt._pending_cancel_reason!r}")
executed.clear()
print(result_line("NEXT run", rt.run(State.empty("f2b"))))

print("\n=== F3: exceptional exhaustion (node raises during streaming) ===")


def boom(state):
    executed.append("x")
    raise RuntimeError("node down")


rt = Runtime(SPEC, {"a": boom, "b": node})
executed.clear()
d = rt.stream(State.empty("f3"))
try:
    for _ in d:
        pass
except NodeFailed:
    print("   NodeFailed propagated through the driver")
print(f"   driver closed={d._closed} pending={rt._pending_cancel_reason!r}")
d.close()
print(f"   after close(): pending={rt._pending_cancel_reason!r}")
executed.clear()
rt2 = Runtime(SPEC, {"a": node, "b": node})
print(result_line("a fresh Runtime still works", rt2.run(State.empty("f3b"))))
executed.clear()
try:
    print(result_line("the SAME Runtime, next run", rt.run(State.empty("f3c"))))
except NodeFailed:
    print("   same Runtime, next run: NodeFailed again (node still raises) — expected")

print("\n=== F4: with-block over a stream whose node raises ===")
rt = Runtime(SPEC, {"a": boom, "b": node})
try:
    with rt.stream(State.empty("f4")) as d:
        for _ in d:
            pass
except NodeFailed:
    pass
print(f"   after the with-block: closed={d._closed} pending={rt._pending_cancel_reason!r}")

print("\n=== F5: break out mid-stream, then close() — must still cancel THAT run ===")
rt = fresh()
d = rt.stream(State.empty("f5"))
next(d)
next(d)
d.close("stopped early")
print(f"   terminal={d.trace.records[-1]['kind']} "
      f"reason={d.trace.records[-1].get('reason')!r} "
      f"active={d.trace.records[-1].get('active_attempt')}")
print(f"   pending leaked? {rt._pending_cancel_reason!r}")
executed.clear()
print(result_line("NEXT run", rt.run(State.empty("f5b"))))

print("\n=== F6: cancel() return value across the lifecycle ===")
rt = fresh()
print(f"   before first run          : cancel() -> {rt.cancel('pre')}")
print(f"   again before first run    : cancel() -> {rt.cancel('pre2')}")
out = rt.run(State.empty("f6"))
print(f"   run 1 status={out.status} reason={out.trace.records[-1].get('reason')!r} "
      f"nodes={len(executed)}")
print(f"   idle after a run          : cancel() -> {rt.cancel('late')}")
executed.clear()
print(result_line("NEXT run must be clean", rt.run(State.empty("f6b"))))
print(f"   pending after refusal     : {rt._pending_cancel_reason!r}")

print("\n=== F7: cancel() from a listener, at every record kind ===")
for target in ("run_started", "attempt_started", "transition", "routing", "run_completed"):
    box: list[Runtime] = []
    bound: list[bool] = []

    class L:
        def __init__(self, kind):
            self.kind = kind

        def on_record(self, record):
            if record["kind"] == self.kind:
                bound.append(box[0].cancel(f"from listener at {self.kind}"))

    rt = Runtime(SPEC, {"a": node, "b": node}, listeners=(L(target),))
    box.append(rt)
    executed.clear()
    out = rt.run(State.empty("f7"))
    kinds = [r["kind"] for r in out.trace.records]
    print(f"   cancel at {target:16} -> bound={bound} status={out.status:9} "
          f"records={len(kinds)} nodes={len(executed)}")
    if out.status != "cancelled" and bound and bound[0]:
        print(f"      NOTE: cancel() returned True but the run was not cancelled")
    executed.clear()
    follow = rt.run(State.empty("f7b"))
    if follow.status != "completed":
        print(f"      LEAK: the next run is {follow.status}")

print("\n=== F8: 500 barrier-forced cancel/start races ===")
tally = {"cancelled": 0, "completed": 0, "bound_true_but_completed": 0,
         "leaked_pending": 0, "next_run_dirty": 0}
for _ in range(500):
    rt = Runtime(SPEC, {"a": node, "b": node})
    barrier = threading.Barrier(2, timeout=5)
    bound: list[bool] = []

    def canceller():
        barrier.wait()
        bound.append(rt.cancel("racing"))

    thread = threading.Thread(target=canceller)
    thread.start()
    barrier.wait()
    out = rt.run(State.empty("f8"))
    thread.join()
    tally[out.status] = tally.get(out.status, 0) + 1
    if bound and bound[0] and out.status != "cancelled":
        tally["bound_true_but_completed"] += 1
    if rt._pending_cancel_reason is not None:
        tally["leaked_pending"] += 1
    follow = rt.run(State.empty("f8b"))
    if follow.status != "completed":
        tally["next_run_dirty"] += 1
print(f"   {tally}")

print("\n=== F9: 200 races between cancel() and the terminal record ===")
late = {"true_and_cancelled": 0, "true_but_completed": 0, "false": 0, "dirty_next": 0}
for _ in range(200):
    rt = Runtime(SPEC, {"a": node, "b": node})
    bound: list[bool] = []
    gate = threading.Event()

    class TerminalRacer:
        def on_record(self, record):
            if record["kind"] == "routing":
                gate.set()

    def canceller():
        gate.wait(2)
        bound.append(rt.cancel("late racer"))

    thread = threading.Thread(target=canceller)
    rt2 = Runtime(SPEC, {"a": node, "b": node}, listeners=(TerminalRacer(),))
    bound.clear()
    gate.clear()

    def canceller2():
        gate.wait(2)
        bound.append(rt2.cancel("late racer"))

    t = threading.Thread(target=canceller2)
    t.start()
    out = rt2.run(State.empty("f9"))
    t.join()
    if bound and bound[0]:
        late["true_and_cancelled" if out.status == "cancelled" else "true_but_completed"] += 1
    else:
        late["false"] += 1
    if rt2.run(State.empty("f9b")).status != "completed":
        late["dirty_next"] += 1
print(f"   {late}")

print("\n=== F10: many sequential runs on one Runtime ===")
rt = fresh()
statuses = set()
for index in range(50):
    executed.clear()
    statuses.add(rt.run(State.empty("f10"), run_id=f"f10-{index}").status)
print(f"   50 sequential runs -> statuses={statuses}")
print(f"   pending={rt._pending_cancel_reason!r} cancel_reason={rt._cancel_reason!r} "
      f"running={rt._running}")

print("\n=== F11: two live drivers / stale driver closing a newer run ===")
rt = fresh()
d1 = rt.stream(State.empty("f11a"))
next(d1)
try:
    d2 = rt.stream(State.empty("f11b"))
    print("   a second concurrent stream was ALLOWED  <-- unexpected")
except RuntimeError as exc:
    print(f"   second concurrent stream refused: {exc}")
d1.close()
executed.clear()
print(result_line("NEXT run after closing the first driver", rt.run(State.empty("f11c"))))

print("\n=== F12: a Runtime whose first run dies inside _start ===")


class BadConfig:
    def __init__(self):
        self.n = 0

    def motus_config(self):
        self.n += 1
        if self.n > 1:
            raise RuntimeError("second evaluation explodes")
        return {"ok": True}

    def __call__(self, state):
        return state


from vitruvyan_motus.errors import NodeConfigurationError  # noqa: E402

bad = BadConfig()
rt = Runtime(GraphSpec.from_dict({
    "schema_version": "1.0.0", "name": "bc", "version": "1.0.0", "entry": "a",
    "nodes": [{"name": "a", "effect_class": "pure"}],
    "transitions": {"a": {"kind": "terminal"}}}), {"a": bad})
print(f"   queued cancel before first run: {rt.cancel('queued')}")
try:
    rt.run(State.empty("f12"))
except NodeConfigurationError as exc:
    print(f"   run 1 -> NodeConfigurationError: {str(exc)[:70]}")
print(f"   state after the failed start: running={rt._running} "
      f"has_started={rt._has_started} pending={rt._pending_cancel_reason!r} "
      f"cancel_reason={rt._cancel_reason!r}")
print(f"   can it be cancelled again?   : cancel() -> {rt.cancel('retry')}")
print(f"   trace produced               : {rt.trace}")
