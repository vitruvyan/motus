"""F2: a stream() that never began a run destroys the queued cancellation.

Same family as issue #25 / FA-002 and untouched by 4010210: the start SUCCEEDS,
so the rollback in `_start` never runs, but no node executes, no `run_started`
record is written and the session is told `finish(complete=False)`.
"""
import gc
from vitruvyan_motus import Runtime, GraphSpec

SPEC = GraphSpec.from_dict({
    "schema_version": "1.0.0", "name": "g", "version": "1",
    "entry": "node",
    "nodes": [{"name": "node", "effect_class": "pure"}],
    "transitions": {"node": {"kind": "terminal"}},
})

executed = []
def node(state):
    executed.append("node")
    return state

class Recorder:
    def __init__(self): self.opened = []; self.records = []; self.finished = []
    def open_run(self, header):
        self.opened.append(header["run"]["run_id"])
        outer = self
        class S:
            def write(self, records): outer.records.extend(records)
            def finish(self, *, complete): outer.finished.append(complete)
        return S()

rec = Recorder()
rt = Runtime(SPEC, {"node": node}, sink=rec)

print("cancel() before first use          :", rt.cancel("shutdown before start"))
d = rt.stream(run_id="never-advanced")
del d
gc.collect()
print("sink open_run calls                :", rec.opened)
print("records the session ever received  :", rec.records)
print("finish(complete=)                  :", rec.finished)
print("cancel() again after the abandon   :", rt.cancel("shutdown, again"))
res = rt.run(run_id="the-next-run")
print("next run status                    :", res.status)
print("nodes executed                     :", executed)
print("run_cancelled anywhere in trace    :",
      any(r["kind"] == "run_cancelled" for r in res.trace.records))
