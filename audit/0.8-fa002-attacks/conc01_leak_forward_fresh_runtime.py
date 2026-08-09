"""LENS concurrency 01 — the rollback's `not was_started` branch leaks a
concurrent cancellation forward onto a LATER, UNRELATED run.

ADR-008 §1: "Lifecycle inspection and mutation occur under one lock, so a
concurrent request either binds to the run or is reported as missed; it never
leaks forward."

Construct, entirely inside declared failure modes:
  * fresh Runtime (was_started False)
  * thread A starts a run whose identity refresh is slow (ADR-008 §4 says
    `motus_config()` is evaluated at every run start) and whose run_id is
    rejected AFTER the refresh -> the start raises ValueError
  * thread B, while `_running` is up, calls cancel("stop THE DOOMED RUN") -> True
  * A's rollback moves that reason into `_pending_cancel_reason`
  * a DIFFERENT caller later starts an unrelated run -> it is cancelled, and
    the `run_cancelled` record carries B's reason for a run B never saw.
"""
import sys, threading, pathlib

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(pathlib.Path(sys.argv[1]) if len(sys.argv) > 1 else ROOT / "src"))

from vitruvyan_motus.graph import GraphSpec
from vitruvyan_motus.runtime import Runtime

SPEC = GraphSpec.from_dict({
    "schema_version": "1.0.0", "name": "conc01", "version": "1.0.0",
    "entry": "node",
    "nodes": [{"name": "node", "effect_class": "pure"}],
    "transitions": {"node": {"kind": "terminal"}},
})

in_window = threading.Event()
cancel_landed = threading.Event()
executed = []


class SlowlyConfiguredNode:
    """A node whose configuration is fetched, i.e. takes wall time."""

    def __init__(self):
        self.slow = False

    def motus_config(self):
        if self.slow:
            in_window.set()
            cancel_landed.wait(5)
        return {"config_version": 1}

    def __call__(self, state):
        executed.append("node")
        return state


node = SlowlyConfiguredNode()
rt = Runtime(SPEC, {"node": node})

node.slow = True
raised = []


def doomed_start():
    try:
        rt.run(run_id="x" * 201)          # rejected AFTER _refresh_identity
    except BaseException as exc:
        raised.append(exc)


t = threading.Thread(target=doomed_start, name="A-doomed-start")
t.start()
assert in_window.wait(5), "never reached the window"

bound = rt.cancel("stop THE DOOMED RUN")
cancel_landed.set()
t.join(5)

print("cancel() during the doomed start returned :", bound)
print("the doomed start raised                   :", type(raised[0]).__name__)
print("nodes executed by the doomed start        :", executed)

node.slow = False
later = rt.run(run_id="an-unrelated-later-run")
print("later, unrelated run status               :", later.status)
kinds = [r["kind"] for r in later.trace.records]
print("later run record kinds                    :", kinds)
reasons = [r.get("reason") for r in later.trace.records if r["kind"] == "run_cancelled"]
print("reason recorded on the later run          :", reasons)

leaked = (
    bound is True
    and later.status == "cancelled"
    and reasons == ["stop THE DOOMED RUN"]
)
print()
print("LEAKED FORWARD:", leaked)
sys.exit(0 if leaked else 1)
