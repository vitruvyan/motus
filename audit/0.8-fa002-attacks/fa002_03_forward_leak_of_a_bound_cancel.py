"""FA-002 attack 3: the rollback retargets a *bound* cancellation at a later run.

New in 4010210:  on a failed start with `was_started` False the handler moves
`self._cancel_reason` into `self._pending_cancel_reason`.  `_cancel_reason` at
that moment is not only "the reason queued before the call" -- `cancel()` writes
there for ANY caller that arrives while `_running` is up, and `_running` is up
for the whole of `_start`'s try block.

So a caller who was told `True` = "bound to the active run" has that request
silently converted into a queue entry, and it then cancels a different, later
run started by different code with a different run_id.

ADR-008 §1: "a concurrent request either binds to the run or is reported as
missed; it never leaks forward."
"""
import sys, threading, pathlib
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "src"))

from vitruvyan_motus.graph import GraphSpec
from vitruvyan_motus.runtime import Runtime

in_start = threading.Event()
may_proceed = threading.Event()
executed = []


class BlockingNode:
    def __init__(self):
        self.block = False

    def motus_config(self):
        if self.block:
            in_start.set()
            may_proceed.wait(5)
        return {"v": 1}

    def __call__(self, state):
        executed.append("node")
        return state


spec = GraphSpec.from_dict({
    "schema_version": "1.0.0", "name": "fa002", "version": "1.0.0",
    "entry": "node",
    "nodes": [{"name": "node", "effect_class": "pure"}],
    "transitions": {"node": {"kind": "terminal"}},
})

node = BlockingNode()
rt = Runtime(spec, {"node": node})          # fresh: never ran, was_started False
node.block = True
err = []


def thread_a():
    try:
        rt.run(run_id="x" * 201)            # rejected AFTER the lifecycle claim
    except BaseException as exc:
        err.append(exc)


a = threading.Thread(target=thread_a, name="A")
a.start()
assert in_start.wait(5), "A never reached the window"

# Thread B cancels what it is told is the active run.
bound = rt.cancel("abort thread A's run")
print("B: cancel() ->", bound, "(True means 'bound to the active run')")

may_proceed.set()
a.join(5)
print("A: start raised", type(err[0]).__name__, "-- no trace, no run_cancelled record")
print("   runtime._pending_cancel_reason is now:", repr(rt._pending_cancel_reason))

# Thread C starts a completely different run.  It never asked to be cancelled.
node.block = False
c_result = rt.run(run_id="c-important-and-unrelated")
print("C: status =", c_result.status, "| nodes executed:", executed)
cancel_rec = [r for r in c_result.trace.to_dict()["records"] if r["kind"] == "run_cancelled"]
if cancel_rec:
    print("C: trace records run_cancelled with reason:", repr(cancel_rec[0]["reason"]))
    print("\nFINDING: a cancellation reported as bound to run A cancelled run C,")
    print("         and C's trace states A's reason as its own.")
else:
    print("\nDID NOT REPRODUCE")
