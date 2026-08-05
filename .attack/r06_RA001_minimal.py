"""RA-001 minimal reproduction: a stale cancellation contaminates a later run.

No threads, no timing, no private attributes touched -- only the documented
public API: Runtime.stream() used as a context manager, then Runtime.run().

Expected (ADR-007 §4): "A later run is not cancelled unless another request is
made."
"""

from __future__ import annotations

from vitruvyan_motus import GraphSpec, Runtime, State

SPEC = GraphSpec.from_dict({
    "schema_version": "1.0.0", "name": "ra001", "version": "1.0.0", "entry": "a",
    "nodes": [{"name": "a", "effect_class": "pure"}],
    "transitions": {"a": {"kind": "terminal"}},
})

executed: list[str] = []


def the_only_node(state):
    executed.append("a")
    return state


runtime = Runtime(SPEC, {"a": the_only_node})

# 1. Stream one run to its natural completion inside the documented
#    context manager.  Nothing here asks for a cancellation.
with runtime.stream(State.empty("first")) as driver:
    for _ in driver:
        pass

print("run 1 : status =", "completed (streamed)", "| nodes executed =", len(executed))

# 2. Reuse the Runtime, as `_running`'s 'no overlapping runs' guard invites.
executed.clear()
second = runtime.run(State.empty("second"))

print("run 2 : status =", second.status, "| nodes executed =", len(executed))
print("        terminal record :", second.trace.records[-1]["kind"])
print("        reason attributed:", repr(second.trace.records[-1].get("reason")))
print()
if second.status == "cancelled" and not executed:
    print("RESULT: FAIL — run 2 was silently cancelled and executed no node,")
    print("        blaming a cancellation the caller never requested for it.")
else:
    print("RESULT: clean")
