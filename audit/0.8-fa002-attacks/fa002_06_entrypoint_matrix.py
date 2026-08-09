"""FA-002 attack 6: the matrix across entry points.

For each entry point, two rows:
  fresh   -- a queued cancellation must survive a start that never began a run
  used    -- a Runtime that has executed must still refuse an idle queue,
             and a cancel bound during a failing start must not evaporate.
"""
import asyncio, sys, threading, pathlib
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "src"))

from vitruvyan_motus.graph import GraphSpec
from vitruvyan_motus.runtime import Runtime

SPEC = GraphSpec.from_dict({
    "schema_version": "1.0.0", "name": "fa002", "version": "1.0.0",
    "entry": "node",
    "nodes": [{"name": "node", "effect_class": "pure"}],
    "transitions": {"node": {"kind": "terminal"}},
})

in_start = threading.Event()
go = threading.Event()


class Node:
    block = False

    def motus_config(self):
        if Node.block:
            in_start.set()
            go.wait(5)
        return {}

    def __call__(self, state):
        return state


BAD = "x" * 201


class _Status:
    def __init__(self, trace):
        kinds = [r["kind"] for r in trace.to_dict()["records"]]
        self.status = ("cancelled" if "run_cancelled" in kinds
                       else "completed" if "run_completed" in kinds else "?")


def drain(entry, rt, **kw):
    if entry == "run":
        return rt.run(**kw)
    if entry == "stream":
        d = rt.stream(**kw)
        for _ in d:
            pass
        return _Status(d.trace)
    if entry == "arun":
        return asyncio.run(rt.arun(**kw))
    if entry == "astream":
        async def go_():
            d = rt.astream(**kw)
            async for _ in d:
                pass
            return _Status(d.trace)
        return asyncio.run(go_())


for entry in ("run", "stream", "arun", "astream"):
    # --- fresh Runtime: does the queued reason survive the failed start? ---
    rt = Runtime(SPEC, {"node": Node()})
    Node.block = False
    assert rt.cancel("queued") is True
    try:
        drain(entry, rt, run_id=BAD)
    except ValueError:
        pass
    survived = rt._pending_cancel_reason is not None or rt.cancel("again") is True
    nxt = drain(entry, rt, run_id="next")
    print(f"{entry:8s} fresh: queued reason survives={survived} next-run={nxt.status}")

    # --- used Runtime + a cancel bound during a failing start ---
    rt = Runtime(SPEC, {"node": Node()})
    assert drain(entry, rt, run_id="real").status == "completed"
    Node.block = True
    in_start.clear(); go.clear()
    err = []

    def failing():
        try:
            drain(entry, rt, run_id=BAD)
        except BaseException as exc:
            err.append(exc)

    t = threading.Thread(target=failing)
    t.start()
    assert in_start.wait(5)
    bound = rt.cancel("stop the run in flight")
    go.set(); t.join(5)
    Node.block = False
    relodge = rt.cancel("try again")
    after = drain(entry, rt, run_id="unrelated-later")
    print(f"{entry:8s} used : cancel()->{bound} re-lodge->{relodge} "
          f"later-run={after.status}  (§1: an idle call must return False)")
