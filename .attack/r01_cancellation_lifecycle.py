"""Re-audit: ADR-007 §4 pre-run cancellation is 'consumed once'.

Runtime.cancel() now routes to _pending_cancel_reason whenever the Runtime is
not running.  StreamDriver.close() calls that same public cancel().  If the
stream has already finished, _running is False by then -- so a cooperative
close (or a `with` block exit) after a completed stream queues a cancellation
that belongs to no run at all.
"""

from __future__ import annotations

from datetime import datetime, timezone

from vitruvyan_motus import Fact, GraphSpec, Policy, Runtime, State

NOW = datetime(2026, 8, 4, tzinfo=timezone.utc)
SPEC = GraphSpec.from_dict({
    "schema_version": "1.0.0", "name": "cancel", "version": "1.0.0", "entry": "a",
    "nodes": [{"name": "a", "effect_class": "pure"}, {"name": "b", "effect_class": "pure"}],
    "transitions": {"a": {"kind": "next", "to": "b"}, "b": {"kind": "terminal"}},
})
executed: list[str] = []


def node(state):
    executed.append("x")
    return state.with_fact(Fact("k", 1, "s", NOW))


def fresh():
    executed.clear()
    return Runtime(SPEC, {"a": node, "b": node})


def show(label, result):
    print(f"   {label:46} status={result.status:9} nodes_run={len(executed)} "
          f"kinds={[r['kind'] for r in result.trace.records]}")


print("=== C1: ADR-007 §4 — pre-run cancel is consumed exactly once ===")
rt = fresh()
rt.cancel("queued before the run")
first = rt.run(State.empty("c1a"))
show("run 1 after a queued cancel", first)
executed.clear()
second = rt.run(State.empty("c1b"))
show("run 2 (must NOT be cancelled)", second)

print("\n=== C2: multiple pre-run cancel() calls collapse to one ===")
rt = fresh()
rt.cancel("first")
rt.cancel("second")
rt.cancel("third")
out = rt.run(State.empty("c2"))
show("run 1", out)
print(f"   reason recorded: {out.trace.records[-1].get('reason')!r}")
executed.clear()
show("run 2", rt.run(State.empty("c2b")))

print("\n=== C3: a fully consumed StreamDriver, then close() ===")
rt = fresh()
driver = rt.stream(State.empty("c3"))
consumed = [record["kind"] for record in driver]        # iterate to exhaustion
print(f"   stream ran to completion, kinds={consumed}")
print(f"   runtime._running after exhaustion = {rt._running}")
driver.close()                                          # the documented cleanup
print(f"   after driver.close(): _pending_cancel_reason = "
      f"{rt._pending_cancel_reason!r}")
executed.clear()
after = rt.run(State.empty("c3-next"))
show("NEXT run on the same Runtime", after)
print("   >>> LEAK" if after.status == "cancelled" else "   >>> clean")

print("\n=== C4: the same thing through the documented `with` block ===")
rt = fresh()
with rt.stream(State.empty("c4")) as driver:
    for _ in driver:
        pass
print(f"   after the with-block: _pending_cancel_reason = {rt._pending_cancel_reason!r}")
executed.clear()
after = rt.run(State.empty("c4-next"))
show("NEXT run on the same Runtime", after)
print("   >>> LEAK" if after.status == "cancelled" else "   >>> clean")

print("\n=== C5: control — close() on a stream stopped mid-run ===")
rt = fresh()
driver = rt.stream(State.empty("c5"))
next(driver)
next(driver)
driver.close("stopped early")
print(f"   mid-run close: terminal={driver.trace.records[-1]['kind']} "
      f"reason={driver.trace.records[-1].get('reason')!r}")
print(f"   _pending_cancel_reason = {rt._pending_cancel_reason!r}")
executed.clear()
after = rt.run(State.empty("c5-next"))
show("NEXT run on the same Runtime", after)
print("   >>> LEAK" if after.status == "cancelled" else "   >>> clean")

print("\n=== C6: cancel() during a node (in-run path still works) ===")
box: list[Runtime] = []


def self_cancelling(state):
    executed.append("x")
    box[0].cancel("cancelled from inside a node")
    return state


rt = Runtime(SPEC, {"a": self_cancelling, "b": node})
box.append(rt)
executed.clear()
out = rt.run(State.empty("c6"))
show("cancel from inside a node", out)
print(f"   active_attempt={out.trace.records[-1].get('active_attempt')}")
executed.clear()
show("NEXT run (must be clean)", rt.run(State.empty("c6-next")))

print("\n=== C7: cancel() from a listener (ADR-007 §8 boundary) ===")
lbox: list[Runtime] = []


class CancellingListener:
    def on_record(self, record):
        if record["kind"] == "run_started":
            lbox[0].cancel("cancelled from a listener")


rt = Runtime(SPEC, {"a": node, "b": node}, listeners=(CancellingListener(),))
lbox.append(rt)
executed.clear()
out = rt.run(State.empty("c7"))
show("listener cancellation", out)
print(f"   trace-visible: kind={out.trace.records[-1]['kind']} "
      f"reason={out.trace.records[-1].get('reason')!r}")
executed.clear()
show("NEXT run (listener fires again)", rt.run(State.empty("c7-next")))

print("\n=== C8: an exception escaping run() must not strand a queued cancel ===")


def boom(state):
    raise KeyboardInterrupt("hard stop")


rt = Runtime(SPEC, {"a": boom, "b": node})
try:
    rt.run(State.empty("c8"))
except BaseException as exc:
    print(f"   run raised {type(exc).__name__}; _running={rt._running} "
          f"_cancel_reason={rt._cancel_reason!r} _pending={rt._pending_cancel_reason!r}")
executed.clear()
show("NEXT run after the escape", rt.run(State.empty("c8-next")))
