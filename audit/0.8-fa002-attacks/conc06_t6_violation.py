"""LENS concurrency 06 — the post-unlock `_active_attempt = None` of the
failed-start rollback, driven into an actual trace-schema T6 violation.

T6 (contract/trace.v1.schema.json, enforced by contract/validate.py):
    "run_cancelled.active_attempt, when non-null, names exactly the immediately
     preceding unclosed attempt_started, else it is null" -- and the converse:
    an unclosed attempt_started may be followed only by run_cancelled NAMING IT.

Interleaving, all of it inside behaviour the contract blesses:
  * thread A's start fails after the lifecycle claim (ADR-008 §4 wraps a
    `motus_config()` failure exactly here; a rejected run_id lands here too);
  * A's rollback frees the claim, then -- outside the lock -- writes
    `self._active_attempt = None`;
  * thread B claims the freed Runtime and reaches `attempt_started`;
  * a listener, which guarantees.md §6 explicitly permits to hold the Runtime
    and call its cancellation surface, cancels from inside the synchronous
    delivery of that record -- i.e. after `_active_attempt` was set and before
    the runtime reads it;
  * A's stray write lands in that gap.

Only A's rollback lock-release is delayed, from outside; `src/` is untouched.
"""
import sys, threading, time, pathlib, json

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "contract"))

from vitruvyan_motus.graph import GraphSpec
from vitruvyan_motus.runtime import Runtime

SPEC = GraphSpec.from_dict({
    "schema_version": "1.0.0", "name": "conc06", "version": "1.0.0",
    "entry": "node",
    "nodes": [{"name": "node", "effect_class": "pure"}],
    "transitions": {"node": {"kind": "terminal"}},
})

opener_written = threading.Event()


class Probe:
    def __init__(self, real):
        self._real = real
        self._exits = {}

    def acquire(self, *a, **k): return self._real.acquire(*a, **k)
    def release(self): return self._real.release()

    def __enter__(self):
        self._real.acquire()
        return self

    def __exit__(self, *exc):
        self._real.release()
        name = threading.current_thread().name
        n = self._exits[name] = self._exits.get(name, 0) + 1
        if name == "A" and n == 2:            # 1 = the claim, 2 = the rollback
            opener_written.wait(10)
        return False


def listener(record):
    if record["kind"] == "attempt_started":
        rt.cancel("cancelled by a listener holding the Runtime")
        opener_written.set()
        time.sleep(0.2)                        # let A's stray write land


def node(state):
    return state


rt = Runtime(SPEC, {"node": node}, listeners=(listener,))
rt._lifecycle_lock = Probe(rt._lifecycle_lock)

out = []


def a_doomed():
    try:
        rt.run(run_id="q" * 201)
    except ValueError:
        pass


def b_real():
    while True:
        try:
            out.append(rt.run(run_id="the-real-run"))
            return
        except RuntimeError:
            time.sleep(0.001)


ta = threading.Thread(target=a_doomed, name="A")
tb = threading.Thread(target=b_real, name="B")
ta.start(); time.sleep(0.05); tb.start()
ta.join(15); tb.join(15)

result = out[0]
recs = result.trace.records
print("B's run status:", result.status)
for r in recs:
    print("  ", r["seq"], r["kind"], r.get("active_attempt", ""))

violation = False
for i, r in enumerate(recs):
    if r["kind"] == "run_cancelled" and i and recs[i - 1]["kind"] == "attempt_started":
        if r.get("active_attempt") is None:
            violation = True

print()
print("T6 VIOLATION (unclosed attempt_started followed by a null active_attempt):", violation)

try:
    from validate import validate_trace
    doc = result.trace.to_document() if hasattr(result.trace, "to_document") else None
    if doc is None:
        doc = {"schema_version": result.trace.header["schema_version"],
               "run": result.trace.run, "records": list(recs)}
    report = validate_trace(doc)
    print("contract validator:", report)
except Exception as exc:
    print("contract validator could not be driven directly:", type(exc).__name__, exc)

sys.exit(0 if violation else 1)
