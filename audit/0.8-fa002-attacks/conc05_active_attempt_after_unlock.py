"""LENS concurrency 05 — the rollback clears `_active_attempt` AFTER it has
released `_lifecycle_lock` and therefore after another thread may already own
the Runtime.

runtime.py:861-878

    with self._lifecycle_lock:
        self._running = False        # <- the claim is free from here
        ...
    self._active_attempt = None      # <- unsynchronised, cross-run write
    raise

`_managed_execute`'s finally has the opposite, correct order (attempt cleared,
then the claim released).  The write is a no-op for the failed start itself
(`_active_attempt` is already None) so its only possible effect is on the run
that claimed the Runtime in between.

This script does NOT claim the interleaving happens by itself -- conc04
measured 0/300 rounds.  It proves the write is reachable and what it does, by
delaying only the rollback's lock release on the failing thread.  `src/` is
untouched: the Runtime's own lock object is wrapped from outside.
"""
import sys, threading, pathlib

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from vitruvyan_motus.graph import GraphSpec
from vitruvyan_motus.runtime import Runtime

SPEC = GraphSpec.from_dict({
    "schema_version": "1.0.0", "name": "conc05", "version": "1.0.0",
    "entry": "node",
    "nodes": [{"name": "node", "effect_class": "pure"}],
    "transitions": {"node": {"kind": "terminal"}},
})

rolled_back = threading.Event()
b_in_node = threading.Event()
observed = []


class Probe:
    """Forwards to the real RLock; delays ONLY the rollback's release on A."""

    def __init__(self, real):
        self._real = real
        self._exits = {}

    def acquire(self, *a, **k):
        return self._real.acquire(*a, **k)

    def release(self):
        return self._real.release()

    def __enter__(self):
        self._real.acquire()
        return self

    def __exit__(self, *exc):
        self._real.release()
        name = threading.current_thread().name
        n = self._exits[name] = self._exits.get(name, 0) + 1
        if name == "A" and n == 2:          # 1 = the claim, 2 = the rollback
            rolled_back.set()
            b_in_node.wait(5)
        return False


def node(state):
    b_in_node.set()
    for _ in range(400000):                  # a node body of a few ms
        if rt._active_attempt is None:
            observed.append("wiped")
            break
    return state


rt = Runtime(SPEC, {"node": node})
rt._lifecycle_lock = Probe(rt._lifecycle_lock)


def a_doomed():
    try:
        rt.run(run_id="q" * 201)
    except ValueError:
        pass


def b_real():
    rolled_back.wait(5)
    out.append(rt.run(run_id="the-real-run"))


out = []
ta = threading.Thread(target=a_doomed, name="A")
tb = threading.Thread(target=b_real, name="B")
ta.start(); tb.start(); ta.join(10); tb.join(10)

print("B's run status                       :", out[0].status if out else None)
print("B's node saw _active_attempt wiped   :", bool(observed))
print("_active_attempt after everything     :", rt._active_attempt)
sys.exit(0 if observed else 1)
