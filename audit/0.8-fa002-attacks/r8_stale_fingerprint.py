"""F3: a start that raises leaves `_identity_cache` half-committed.

`_refresh_identity` updates the per-node cache row by row and only recomputes
`_code_fingerprint` after the loop.  A node that raises `NodeConfigurationError`
part-way therefore commits the EARLIER nodes' new material into the cache while
the fingerprint keeps describing the OLD material.  On the next start those
nodes compare equal to their own cache, `changed` stays False, and the run's
header records a `code_fingerprint` that no longer describes the configuration
that produced it.

The rollback added by 4010210 restores `_has_started` and the queued
cancellation from the same failed start; it does not restore this.
"""
from vitruvyan_motus import Runtime
from vitruvyan_motus.graph import GraphSpec
from vitruvyan_motus.errors import NodeConfigurationError

SPEC = GraphSpec.from_dict({
    "schema_version": "1.0.0", "name": "g", "version": "1",
    "entry": "a",
    "nodes": [
        {"name": "a", "effect_class": "pure"},
        {"name": "b", "effect_class": "pure"},
    ],
    "transitions": {
        "a": {"kind": "next", "to": "b"},
        "b": {"kind": "terminal"},
    },
})

A_CONFIG = {"threshold": 1}
B_BROKEN = [False]

class NodeA:
    def __call__(self, state):
        return state
    def motus_config(self):
        return dict(A_CONFIG)

class NodeB:
    def __call__(self, state):
        return state
    def motus_config(self):
        if B_BROKEN[0]:
            raise RuntimeError("configuration source unavailable")
        return {"k": "constant"}

a, b = NodeA(), NodeB()

rt = Runtime(SPEC, {"a": a, "b": b})

fp0 = rt.run(run_id="r0").trace.header["run"]["graph"]["code_fingerprint"]
print("run 0  a.threshold=1            fingerprint:", fp0)

# The operator changes a's configuration AND b's config source is briefly down.
A_CONFIG["threshold"] = 2
B_BROKEN[0] = True
try:
    rt.run(run_id="r1")
except NodeConfigurationError as exc:
    print("run 1  refused:", type(exc).__name__, "->", exc.args[0] if exc.args else exc)

# b recovers; its own material is exactly what it always was.
B_BROKEN[0] = False
fp2 = rt.run(run_id="r2").trace.header["run"]["graph"]["code_fingerprint"]
print("run 2  a.threshold=2            fingerprint:", fp2)
print("fingerprint changed with the config it describes:", fp0 != fp2)

# Control: the same config change WITHOUT the failed start in between.
rt2 = Runtime(SPEC, {"a": a, "b": b})
A_CONFIG["threshold"] = 1
f0 = rt2.run(run_id="c0").trace.header["run"]["graph"]["code_fingerprint"]
A_CONFIG["threshold"] = 2
f1 = rt2.run(run_id="c1").trace.header["run"]["graph"]["code_fingerprint"]
print("control (no failed start) fingerprint changed :", f0 != f1)
print("and run 2's fingerprint equals the 1-threshold one:", fp2 == f0)

print()
print("--- internal state, to rule out a coincidence ---")
A_CONFIG["threshold"] = 1
B_BROKEN[0] = False
rt3 = Runtime(SPEC, {"a": a, "b": b})
print("after ctor   cached a material:", rt3._identity_cache["a"][1],
      " fp:", rt3._code_fingerprint[-8:])
A_CONFIG["threshold"] = 2
B_BROKEN[0] = True
try:
    rt3.run(run_id="q1")
except NodeConfigurationError:
    pass
print("after refusal cached a material:", rt3._identity_cache["a"][1],
      " fp:", rt3._code_fingerprint[-8:], " <- cache moved, fingerprint did not")
B_BROKEN[0] = False
r = rt3.run(run_id="q2")
print("run q2 header fingerprint      :", r.trace.header["run"]["graph"]["code_fingerprint"][-8:])
print("a.motus_config() at that moment:", a.motus_config())

print()
print("--- and the artifact validates clean: nothing downstream can see it ---")
import importlib.util, pathlib
import sys
spec_mod = importlib.util.spec_from_file_location("v", "/home/vitruvyan/motus/contract/validate.py")
v = importlib.util.module_from_spec(spec_mod); sys.modules["v"] = v; spec_mod.loader.exec_module(v)
print("violations on run q2:", v.validate_trace(r.trace.to_dict(), spec=SPEC.to_dict()))
