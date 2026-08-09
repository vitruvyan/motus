"""Control for conc06: same listener, same probe, NO failing start.

If the T6 violation is produced by the listener or by the probe rather than by
the rollback's post-unlock write, it must appear here too.
"""
import sys, threading, time, pathlib

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "contract"))
from validate import validate_trace
from vitruvyan_motus.graph import GraphSpec
from vitruvyan_motus.runtime import Runtime

SPEC = GraphSpec.from_dict({
    "schema_version": "1.0.0", "name": "conc06b", "version": "1.0.0",
    "entry": "node",
    "nodes": [{"name": "node", "effect_class": "pure"}],
    "transitions": {"node": {"kind": "terminal"}},
})


class Probe:
    def __init__(self, real): self._real = real
    def acquire(self, *a, **k): return self._real.acquire(*a, **k)
    def release(self): return self._real.release()
    def __enter__(self): self._real.acquire(); return self
    def __exit__(self, *e): self._real.release(); return False


def listener(record):
    if record["kind"] == "attempt_started":
        rt.cancel("cancelled by a listener holding the Runtime")
        time.sleep(0.2)


rt = Runtime(SPEC, {"node": lambda s: s}, listeners=(listener,))
rt._lifecycle_lock = Probe(rt._lifecycle_lock)
res = rt.run(run_id="control")
recs = res.trace.records
print("status:", res.status)
for r in recs:
    print("  ", r["seq"], r["kind"], r.get("active_attempt", ""))
doc = {"schema_version": res.trace.header["schema_version"], "run": res.trace.run, "records": list(recs)}
print("contract validator:", validate_trace(doc))
