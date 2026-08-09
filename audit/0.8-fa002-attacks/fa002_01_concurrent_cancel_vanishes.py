"""FA-002 attack 1.

ADR-008 §1: "`Runtime.cancel()` returns whether it bound the request. ...
Lifecycle inspection and mutation occur under one lock, so a concurrent
request either binds to the run or is reported as missed; it never leaks
forward."

Construct: a Runtime that HAS run once (was_started True).  A second start is
in flight, blocked inside `_start`'s try block, so `_running` is True.  A
concurrent caller calls `cancel()` -> True ("bound to the active run").  The
start then fails.  The rollback discards `self._cancel_reason` and, because
`was_started` is True, does NOT re-queue it.

Result the caller sees: True from cancel(), no run cancelled, no trace, no
`run_cancelled` record, and cancel() from then on returns False so the request
cannot be lodged again.  Neither bound nor reported as missed.
"""
import sys, threading, pathlib
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "src"))

from vitruvyan_motus.graph import GraphSpec
from vitruvyan_motus.runtime import Runtime


def _spec():
    return GraphSpec.from_dict({
        "schema_version": "1.0.0", "name": "fa002", "version": "1.0.0",
        "entry": "node",
        "nodes": [{"name": "node", "effect_class": "pure"}],
        "transitions": {"node": {"kind": "terminal"}},
    })


in_start = threading.Event()
may_proceed = threading.Event()
executed = []


class BlockingNode:
    def __init__(self, block):
        self.block = block

    def motus_config(self):
        if self.block[0]:
            in_start.set()
            may_proceed.wait(5)
        return {"v": 1}

    def __call__(self, state):
        executed.append("node")
        return state


block = [False]
node = BlockingNode(block)
rt = Runtime(_spec(), {"node": node})

# 1. A real run, so the Runtime is "used": was_started becomes True.
assert rt.run(run_id="the-real-run").status == "completed"
print("run 1:", "completed")

# 2. Start that will fail *after* the lifecycle claim (bad run_id), blocked
#    inside the try block so a concurrent cancel sees `_running` True.
block[0] = True
err = []


def failing_start():
    try:
        rt.run(run_id="x" * 201)
    except BaseException as exc:
        err.append(exc)


t = threading.Thread(target=failing_start)
t.start()
assert in_start.wait(5), "start never reached motus_config"

bound = rt.cancel("stop this run now")
print("cancel() during the in-flight start returned:", bound)

may_proceed.set()
t.join(5)
print("the start raised:", type(err[0]).__name__, err[0])

# 3. What did that True buy the caller?
print("runtime.trace after the failed start:", rt.trace.run["run_id"] if rt.trace else None)
print("cancel() now (can the caller lodge it again?):", rt.cancel("try again"))

block[0] = False
after = rt.run(run_id="the-next-run")
print("next run status:", after.status, "| nodes executed:", executed)

if bound is True and after.status != "cancelled" and not err[0].args[0].startswith("cancel"):
    print("\nFINDING: cancel() returned True, bound to nothing, and cannot be re-lodged.")
else:
    print("\nDID NOT REPRODUCE")
