"""F1 evidence: the half of 4010210 that no test pins.

M2 = the shipped fix minus `self._pending_cancel_reason = self._cancel_reason`.
The whole 593-test suite passes under M2 (.attack/r8_mutate_full.py).  Here is
the behaviour the suite cannot see.
"""
from vitruvyan_motus import Runtime
from vitruvyan_motus.graph import GraphSpec

SPEC = GraphSpec.from_dict({
    "schema_version": "1.0.0", "name": "g", "version": "1", "entry": "n",
    "nodes": [{"name": "n", "effect_class": "pure"}],
    "transitions": {"n": {"kind": "terminal"}},
})
ran = []
def n(state):
    ran.append(1); return state

rt = Runtime(SPEC, {"n": n})
print("cancel() queued            :", rt.cancel("shutdown"))
try:
    rt.run(run_id="x" * 201)
except ValueError:
    pass
# The caller does NOT call cancel() a second time -- which is the whole point
# of "the queued cancellation SURVIVES".
res = rt.run(run_id="the-first-real-run")
print("next run status            :", res.status, " (shipped: cancelled, M2: completed)")
print("nodes executed             :", ran)
if res.status == "cancelled":
    print("run_cancelled reason       :",
          [r for r in res.trace.records if r["kind"] == "run_cancelled"][0]["reason"])
