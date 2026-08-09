"""FA-002 attack 4 (corrected): how wide is the window `cancel()` can land in?

An earlier version of this script claimed a 100% "natural race" rate with a
sleep-based aim.  That number was not trustworthy: a `cancel()` that arrives
*before* the claim takes the legitimate pre-first-run queue path and sets
`_pending_cancel_reason` too, so the detector could not tell the two apart.
Reported here instead is the thing that is measurable without a detector: the
width of `_start`'s claim window, from `self._running = True` to the raise.

`cancel()` landing anywhere in that window takes the `_running` branch, returns
True, and writes `_cancel_reason` -- which the rollback then either destroys
(was_started True) or re-queues for a different run (was_started False).
"""
import sys, time, pathlib
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "src"))

from vitruvyan_motus.graph import GraphSpec
from vitruvyan_motus.runtime import Runtime


def build(n):
    nodes = [{"name": f"n{i}", "effect_class": "pure"} for i in range(n)]
    tr = {f"n{i}": {"kind": "next", "to": f"n{i+1}"} for i in range(n - 1)}
    tr[f"n{n-1}"] = {"kind": "terminal"}
    return GraphSpec.from_dict({
        "schema_version": "1.0.0", "name": "w", "version": "1.0.0",
        "entry": "n0", "nodes": nodes, "transitions": tr,
    })


def mk(i):
    def f(state):
        return state
    f.__name__ = f"n{i}"
    return f


for n in (3, 100, 400):
    rt = Runtime(build(n), {f"n{i}": mk(i) for i in range(n)})
    samples = []
    for _ in range(20):
        t0 = time.perf_counter()
        try:
            rt.run(run_id="x" * 201)
        except BaseException:
            pass
        samples.append((time.perf_counter() - t0) * 1e6)
    samples.sort()
    print(f"n={n:4d} nodes: claim..raise = {samples[len(samples)//2]:.0f} us median, "
          f"{samples[0]:.0f} us min")

print()
print("The window also contains arbitrary user code: `_refresh_identity` calls")
print("every node's `motus_config()` inside it, and a `motus_config` that raises")
print("(ADR-008 §4, NodeConfigurationError) is a production failure mode, not a")
print("caller typo.  fa002_03 pins the outcome deterministically with a hook.")
