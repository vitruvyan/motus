"""F4: `cancel()` returns True during a start that then fails, and the request
is neither honoured nor reported as missed.

ADR-008 §1: "Lifecycle inspection and mutation occur under one lock, so a
concurrent request either binds to the run or is reported as missed; it never
leaks forward."

Both halves are exercised.  Case A (used Runtime): True, then the request is
destroyed and can never be re-lodged.  Case B (fresh Runtime): True against the
run in flight, and 4010210 now moves it forward onto the NEXT run.

The cancellation is issued from inside `motus_config()`, which `_start` calls
via `_refresh_identity` while `_running` is up -- so the window is entered
deterministically, no thread scheduling involved.  A genuine two-thread version
is at the bottom and reports the same thing.
"""
import threading
from vitruvyan_motus import Runtime
from vitruvyan_motus.graph import GraphSpec

SPEC = GraphSpec.from_dict({
    "schema_version": "1.0.0", "name": "g", "version": "1", "entry": "n",
    "nodes": [{"name": "n", "effect_class": "pure"}],
    "transitions": {"n": {"kind": "terminal"}},
})

def build(hook):
    class N:
        def __call__(self, state):
            return state
        def motus_config(self):
            hook()
            return {"k": 1}
    return N()

# ---------------- Case A: the Runtime has already executed ----------------
box = {}
armed = [False]
def hook_a():
    if armed[0]:
        armed[0] = False
        box["returned"] = box["rt"].cancel("stop, from inside the start window")
node = build(hook_a)
rt = Runtime(SPEC, {"n": node}); box["rt"] = rt
assert rt.run(run_id="first").status == "completed"
armed[0] = True
try:
    rt.run(run_id="x" * 201)
except ValueError:
    pass
print("A  cancel() during the failed start returned :", box.get("returned"))
print("A  cancel() afterwards                       :", rt.cancel("again"))
print("A  next run status                           :", rt.run(run_id="next").status)

# ---------------- Case B: the Runtime has never executed ----------------
box2 = {}
armed2 = [False]
def hook_b():
    if armed2[0]:
        armed2[0] = False
        box2["returned"] = box2["rt"].cancel("stop, from inside the start window")
node2 = build(hook_b)
rt2 = Runtime(SPEC, {"n": node2}); box2["rt"] = rt2; armed2[0] = True
try:
    rt2.run(run_id="x" * 201)
except ValueError:
    pass
print("B  cancel() during the failed start returned :", box2.get("returned"))
res = rt2.run(run_id="a-different-run")
print("B  next run status                           :", res.status)
if res.status == "cancelled":
    term = [r for r in res.trace.records if r["kind"] == "run_cancelled"][0]
    print("B  run_cancelled reason on the NEXT run      :", term["reason"])

# ---------------- Case A again, with a real second thread ----------------
gate_in, gate_out = threading.Event(), threading.Event()
result = {}
def hook_t():
    if armed3[0]:
        armed3[0] = False
        gate_in.set(); gate_out.wait(5)
armed3 = [False]
node3 = build(hook_t)
rt3 = Runtime(SPEC, {"n": node3})
assert rt3.run(run_id="first").status == "completed"
armed3[0] = True
def starter():
    try:
        rt3.run(run_id="x" * 201)
    except ValueError:
        pass
t = threading.Thread(target=starter); t.start()
gate_in.wait(5)
result["cancel"] = rt3.cancel("stop, from another thread")
gate_out.set(); t.join()
print("T  cancel() from a second thread returned    :", result["cancel"])
print("T  cancel() afterwards                       :", rt3.cancel("again"))
print("T  next run status                           :", rt3.run(run_id="next").status)
