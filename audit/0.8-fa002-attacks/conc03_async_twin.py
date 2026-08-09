"""LENS concurrency 03 — the async twin of conc02, and the reason-substitution
variant of conc01.
"""
import asyncio, gc, sys, pathlib, threading

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from vitruvyan_motus.graph import GraphSpec
from vitruvyan_motus.runtime import Runtime

SPEC = GraphSpec.from_dict({
    "schema_version": "1.0.0", "name": "conc03", "version": "1.0.0",
    "entry": "node",
    "nodes": [{"name": "node", "effect_class": "pure"}],
    "transitions": {"node": {"kind": "terminal"}},
})


def fresh():
    executed = []

    def node(state):
        executed.append("node")
        return state

    return Runtime(SPEC, {"node": node}), executed


# ---------------------------------------------------------------- A: astream
async def main():
    rt, executed = fresh()
    print("A. astream, driver dropped before first __anext__")
    print("   cancel() on fresh Runtime          :", rt.cancel("shutdown before start"))
    d = rt.astream(run_id="never-advanced")
    del d
    gc.collect()
    await asyncio.sleep(0)
    print("   cancel() again                     :", rt.cancel("again"))
    after = await rt.arun(run_id="the-next-run")
    print("   next run status                    :", after.status, "| executed:", executed)
    return after.status != "cancelled"

lost_async = asyncio.run(main())

# --------------------------------------- B: reason substitution across window
print()
print("B. a pre-queued operator reason is REPLACED by a phantom run's reason")

in_window = threading.Event()
cancel_landed = threading.Event()


class SlowConfig:
    def __init__(self):
        self.slow = False
        self.executed = []

    def motus_config(self):
        if self.slow:
            in_window.set()
            cancel_landed.wait(5)
        return {"v": 1}

    def __call__(self, state):
        self.executed.append("node")
        return state


node = SlowConfig()
rt = Runtime(SPEC, {"node": node})
print("   operator queues                    :", rt.cancel("shutdown: SIGTERM"), "-> 'shutdown: SIGTERM'")

node.slow = True
raised = []


def doomed():
    try:
        rt.run(run_id="y" * 201)
    except BaseException as exc:
        raised.append(exc)


t = threading.Thread(target=doomed)
t.start()
assert in_window.wait(5)
print("   supervisor cancels the phantom run :", rt.cancel("driver closed"))
cancel_landed.set()
t.join(5)
node.slow = False

later = rt.run(run_id="the-next-run")
reasons = [r["reason"] for r in later.trace.records if r["kind"] == "run_cancelled"]
print("   next run status                    :", later.status)
print("   reason the trace attributes it to  :", reasons)
substituted = reasons == ["driver closed"]
print()
print("ASYNC ABANDON LOSES THE QUEUE        :", lost_async)
print("OPERATOR REASON DESTROYED/SUBSTITUTED:", substituted)
sys.exit(0 if (lost_async and substituted) else 1)
