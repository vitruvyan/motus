"""FA-002 attack 2: the adjacent 'start that never began a run'.

`_release_if_never_started` releases the claim for a stream driver that was
created and dropped without ever being advanced -- "a run that had not executed
a single node", in its own docstring.  It clears `_cancel_reason` and leaves
`_has_started` up.

So the exact defect the commit closed for `_start`'s raise path is still open
one method away: a queued cancellation is consumed by a run that executed
nothing, destroyed, and cannot be lodged again because ADR-008 §1 now reads the
Runtime as used.  The next run proceeds uncancelled and runs the node.
"""
import gc, sys, pathlib
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "src"))

from vitruvyan_motus.graph import GraphSpec
from vitruvyan_motus.observers import InMemoryTraceSink
from vitruvyan_motus.runtime import Runtime

executed = []


def node(state):
    executed.append("node")
    return state


spec = GraphSpec.from_dict({
    "schema_version": "1.0.0", "name": "fa002", "version": "1.0.0",
    "entry": "node",
    "nodes": [{"name": "node", "effect_class": "pure"}],
    "transitions": {"node": {"kind": "terminal"}},
})

for label, maker in (
    ("stream", lambda rt: rt.stream()),
    ("astream", lambda rt: rt.astream()),
):
    sink = InMemoryTraceSink()
    rt = Runtime(spec, {"node": node}, sink=sink)
    executed.clear()

    print(f"--- {label} ---")
    print("cancel() before first use:", rt.cancel("shutdown before start"))
    print("  _pending:", rt._pending_cancel_reason)

    d = maker(rt)
    print("  after the driver exists: _running=%s _has_started=%s _cancel_reason=%r"
          % (rt._running, rt._has_started, rt._cancel_reason))
    del d
    gc.collect()
    print("  after dropping it unstarted: _running=%s _has_started=%s "
          "_cancel_reason=%r _pending=%r"
          % (rt._running, rt._has_started, rt._cancel_reason, rt._pending_cancel_reason))

    print("  sink sessions opened:", len(sink.runs), "records in them:",
          [len(r["records"]) for r in sink.runs])
    print("  can the caller lodge it again? cancel() ->", rt.cancel("again"))

    result = rt.run(run_id="the-next-run")
    print("  next run status:", result.status, "| nodes executed:", executed)
    if result.status != "cancelled":
        print("  FINDING: the queued cancellation was eaten by a run that "
              "executed no node, and cannot be re-lodged.")
