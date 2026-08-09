"""LENS concurrency 08 — can the overlapping-run guard reach the rollback?

The guard raises inside the claim's critical section and BEFORE `try:`, so it
should never run the except handler.  Two ways to test it: re-entrantly from
inside a live node (the lifecycle lock is an RLock, so the refused call runs on
the thread that already holds it) and from a second thread.

Assertion: after N refused starts against a live run, the live run's state is
untouched -- `_has_started`, `_pending_cancel_reason`, `_active_attempt`, and a
trace that still passes the contract validator.
"""
import sys, threading, pathlib

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "contract"))
from validate import validate_trace
from vitruvyan_motus.graph import GraphSpec
from vitruvyan_motus.runtime import Runtime

SPEC = GraphSpec.from_dict({
    "schema_version": "1.0.0", "name": "conc08", "version": "1.0.0",
    "entry": "a",
    "nodes": [{"name": "a", "effect_class": "pure"}, {"name": "b", "effect_class": "pure"}],
    "transitions": {"a": {"kind": "next", "to": "b"}, "b": {"kind": "terminal"}},
})

seen = []
refusals = {"reentrant": 0, "crossthread": 0, "other": []}
stop = threading.Event()
in_run = threading.Event()


def hammer():
    in_run.wait(5)
    while not stop.is_set():
        for rid in (None, "x" * 201, "ok"):
            try:
                rt.run(run_id=rid)
            except RuntimeError as exc:
                if "overlapping" in str(exc):
                    refusals["crossthread"] += 1
                else:
                    refusals["other"].append(repr(exc))
            except ValueError:
                pass
            except BaseException as exc:
                refusals["other"].append(repr(exc))


def node_a(state):
    in_run.set()
    for _ in range(50):
        try:
            rt.run(run_id="x" * 201)      # would fail AFTER the claim, if it got one
        except RuntimeError as exc:
            assert "overlapping" in str(exc), exc
            refusals["reentrant"] += 1
        except BaseException as exc:
            refusals["other"].append(repr(exc))
    seen.append(("a", rt._has_started, rt._pending_cancel_reason, rt._active_attempt))
    return state


def node_b(state):
    seen.append(("b", rt._has_started, rt._pending_cancel_reason, rt._active_attempt))
    return state


rt = Runtime(SPEC, {"a": node_a, "b": node_b})
t = threading.Thread(target=hammer, daemon=True)
t.start()
res = rt.run(run_id="the-live-run")
stop.set(); t.join(5)

print("reentrant refusals :", refusals["reentrant"])
print("crossthread refusals:", refusals["crossthread"])
print("unexpected          :", refusals["other"][:3])
print("observed inside nodes (name, _has_started, _pending, _active_attempt):")
for s in seen:
    print("   ", s)
print("status:", res.status, "| records:", [r["kind"] for r in res.trace.records])
doc = {"schema_version": res.trace.header["schema_version"], "run": res.trace.run,
       "records": list(res.trace.records)}
print("contract validator:", validate_trace(doc))
