"""FA-002 attack 5: the other entry points, and whether a failed start is ever
observable on a durable surface before the rollback denies it happened.

(a) Did any failure path inside `_start`'s try let a sink or listener see the
    run first?  Sweep every reachable failure mode with a recording sink and a
    recording listener.
(b) arun / astream / resume share `_start`: does the FA-002 rollback and the
    forward leak behave the same there?
(c) After a failed start, what does the Runtime still name?  (`_run`, `_trace`,
    `_hub`, `_control`.)
"""
import asyncio, sys, threading, pathlib
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "src"))

from vitruvyan_motus.context import ReplayStatus
from vitruvyan_motus.graph import GraphSpec
from vitruvyan_motus.observers import TraceSink
from vitruvyan_motus.replay import ReplayEngine, TraceBundle
from vitruvyan_motus.runtime import Runtime
from vitruvyan_motus.state import State

SPEC = GraphSpec.from_dict({
    "schema_version": "1.0.0", "name": "fa002", "version": "1.0.0",
    "entry": "node",
    "nodes": [{"name": "node", "effect_class": "pure"}],
    "transitions": {"node": {"kind": "terminal"}},
})


def node(state):
    return state


class Recorder(TraceSink):
    def __init__(self):
        self.headers = []
        self.sessions = []

    def open_run(self, header):
        self.headers.append(header)
        outer = self

        class S:
            def __init__(self):
                self.records = []
                self.finished = None

            def write(self, batch):
                self.records.extend(batch)

            def finish(self, *, complete):
                self.finished = complete

        s = S()
        self.sessions.append(s)
        return s


print("== (a) is a failed start ever observable on a durable/live surface? ==")


class BadClock:
    def now(self):
        raise RuntimeError("clock exploded")


cases = {
    "bad run_id": dict(run_id="x" * 201),
    "empty run_id": dict(run_id=""),
    "non-str run_id": dict(run_id=7),
}
for label, kwargs in cases.items():
    rec = Recorder()
    seen = []
    rt = Runtime(SPEC, {"node": node}, sink=rec, listeners=[seen.append])
    try:
        rt.run(**kwargs)
        print(f"  {label}: DID NOT FAIL")
        continue
    except BaseException as e:
        exc = e
    print(f"  {label}: raised {type(exc).__name__} | sink headers={len(rec.headers)} "
          f"listener records={len(seen)} | rt._run={rt._run is not None} "
          f"rt._trace={rt._trace is not None} rt._hub={rt._hub is not None}")


class BoomConfig:
    def motus_config(self):
        raise RuntimeError("config exploded")

    def __call__(self, state):
        return state


rec = Recorder()
seen = []
rt = Runtime(SPEC, {"node": node}, sink=rec, listeners=[seen.append])
rt._nodes = dict(rt._nodes)  # not mutating src; simulate by a fresh Runtime below
try:
    rt2 = Runtime(SPEC, {"node": BoomConfig()}, sink=rec)
    print("  motus_config failing at construction:", "raised nothing")
except BaseException as exc:
    print(f"  motus_config failing at construction: {type(exc).__name__}")

print()
print("== (b) same rollback on the async entry points ==")


async def probe_async(entry):
    rt = Runtime(SPEC, {"node": node})
    assert rt.cancel("queued before first use") is True
    try:
        if entry == "arun":
            await rt.arun(run_id="x" * 201)
        else:
            d = rt.astream(run_id="x" * 201)
            async for _ in d:
                pass
    except ValueError:
        pass
    return rt._pending_cancel_reason, rt._has_started


for entry in ("arun", "astream"):
    pending, started = asyncio.run(probe_async(entry))
    print(f"  {entry}: pending restored={pending!r} _has_started={started}")

print()
print("== (b2) resume(): the resumed-segment check fires inside _start ==")
src_rt = Runtime(SPEC, {"node": node})
done = src_rt.run(run_id="source-run")
engine = ReplayEngine(TraceBundle(SPEC, done.trace))
target = Runtime(SPEC, {"node": node})
assert target.cancel("queued on the resume target") is True
try:
    engine.resume(target, run_id="source-run")
except BaseException as exc:
    print(f"  resume raised {type(exc).__name__}: {exc}")
print(f"  target._pending_cancel_reason={target._pending_cancel_reason!r} "
      f"_has_started={target._has_started}")
print("  NOTE: ReplayEngine.resume raises its own UnsafeResume BEFORE _run_from,\n        so _start's resumed-segment check is not reached from this path and\n        the claim was never taken -- this case does not exercise the rollback.")

print()
print("== (b3) the forward leak, reached through arun ==")
in_start = threading.Event()
go = threading.Event()


class Blocker:
    block = False

    def motus_config(self):
        if Blocker.block:
            in_start.set()
            go.wait(5)
        return {}

    def __call__(self, state):
        return state


rt = Runtime(SPEC, {"node": Blocker()})
Blocker.block = True


async def leak():
    task = asyncio.get_running_loop().run_in_executor(None, lambda: None)
    def failing():
        try:
            asyncio.run(rt.arun(run_id="x" * 201))
        except BaseException:
            pass
    t = threading.Thread(target=failing)
    t.start()
    in_start.wait(5)
    bound = rt.cancel("abort the arun in flight")
    go.set()
    t.join(5)
    Blocker.block = False
    r = await rt.arun(run_id="an-unrelated-later-run")
    return bound, r.status


bound, status = asyncio.run(leak())
print(f"  cancel() -> {bound}; the unrelated later arun status = {status}")

print()
print("== (c) what the Runtime still names after a failed start ==")
rt = Runtime(SPEC, {"node": node})
first = rt.run(run_id="first")
before_run = rt._run
try:
    rt.run(run_id="x" * 201)
except ValueError:
    pass
print("  rt.trace.run_id  :", rt.trace.run["run_id"], "(the PREVIOUS run)")
print("  rt._run is the previous handle:", rt._run is before_run)
print("  rt._run.trace    :", rt._run.trace, " started:", rt._run.started,
      " finished:", rt._run.finished, " evidence:", rt._run.evidence)
print("  rt._control is a fresh controller for the dead start:",
      rt._control is not None)
print("  rt._run.hub is the dead start's unbound hub:", rt._run.hub is rt._hub)
