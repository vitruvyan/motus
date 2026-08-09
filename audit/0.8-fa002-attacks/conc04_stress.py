"""LENS concurrency 04 — stress: doomed starts interleaved with real runs and
concurrent cancellations, asserting the ADR-008 §1 / T6 invariants each round.

Detectors
  D1  overlapping runs (two node bodies live at once)
  D2  T6: run_cancelled.active_attempt is null although the preceding record is
      an unclosed attempt_started  (the `_active_attempt = None` that the
      rollback performs AFTER releasing the lifecycle lock)
  D3  a node observes rt._active_attempt as None while it is executing
      (the same write, detected with a wider window)
  D4  cancel() -> True on a Runtime that then neither cancels a run nor lets
      the request be lodged again
"""
import sys, threading, pathlib, itertools, random

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "contract"))
from validate import validate_trace
sys.setswitchinterval(float(sys.argv[1]) if len(sys.argv) > 1 else 5e-6)

from vitruvyan_motus.graph import GraphSpec
from vitruvyan_motus.runtime import Runtime

SPEC = GraphSpec.from_dict({
    "schema_version": "1.0.0", "name": "conc04", "version": "1.0.0",
    "entry": "a",
    "nodes": [{"name": "a", "effect_class": "pure"}, {"name": "b", "effect_class": "pure"}],
    "transitions": {"a": {"kind": "next", "to": "b"}, "b": {"kind": "terminal"}},
})

ROUNDS = int(sys.argv[2]) if len(sys.argv) > 2 else 400
findings = {"D1": 0, "D2": 0, "D3": 0, "D4": 0}
samples = []


def check_t6(trace):
    recs = trace.records
    for i, r in enumerate(recs):
        if r["kind"] != "run_cancelled":
            continue
        prev = recs[i - 1] if i else None
        unclosed = prev is not None and prev["kind"] == "attempt_started"
        if unclosed and r.get("active_attempt") is None:
            return ("null-with-unclosed", prev, r)
        if not unclosed and r.get("active_attempt") is not None:
            return ("nonnull-without-unclosed", prev, r)
    return None


for rnd in range(ROUNDS):
    inflight = [0]
    inflight_lock = threading.Lock()
    concurrent = []
    attempt_none = []

    def body(state, rt_box=None):
        with inflight_lock:
            inflight[0] += 1
            if inflight[0] > 1:
                concurrent.append(inflight[0])
        # widen the observation window over the node body
        for _ in range(200):
            if rt_box[0] is not None and rt_box[0]._active_attempt is None:
                attempt_none.append(1)
                break
        with inflight_lock:
            inflight[0] -= 1
        return state

    box = [None]
    rt = Runtime(SPEC, {"a": lambda s: body(s, box), "b": lambda s: body(s, box)})
    box[0] = rt

    results = []
    errors = []
    stop = threading.Event()

    def doomed():
        while not stop.is_set():
            try:
                rt.run(run_id="z" * 201)
            except ValueError:
                pass
            except RuntimeError:
                pass
            except BaseException as exc:
                errors.append(exc)

    def real():
        for _ in range(6):
            while True:
                try:
                    results.append(rt.run())
                    break
                except RuntimeError:
                    pass
                except BaseException as exc:
                    errors.append(exc)
                    return

    def canceller():
        while not stop.is_set():
            rt.cancel("stress cancel")

    threads = [threading.Thread(target=doomed), threading.Thread(target=real)]
    if True:
        threads.append(threading.Thread(target=canceller))
    for t in threads[1:2]:
        pass
    for t in threads:
        t.start()
    threads[1].join(30)
    stop.set()
    for t in threads:
        t.join(30)

    if concurrent:
        findings["D1"] += 1
    if attempt_none:
        findings["D3"] += 1
    for r in results:
        v = check_t6(r.trace)
        if v:
            findings["D2"] += 1
            samples.append((rnd, v))
        doc = {"schema_version": r.trace.header["schema_version"], "run": r.trace.run,
               "records": list(r.trace.records)}
        viol = validate_trace(doc)
        if viol:
            findings["D4"] += 1
            samples.append((rnd, "VALIDATOR", [str(x) for x in viol[:2]]))
    if errors:
        samples.append((rnd, "UNEXPECTED", [repr(e) for e in errors[:3]]))

print("rounds:", ROUNDS, "switchinterval:", sys.getswitchinterval())
print("findings:", findings)
for s in samples[:5]:
    print("  sample:", s)
