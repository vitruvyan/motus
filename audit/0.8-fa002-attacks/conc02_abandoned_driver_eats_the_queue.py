"""LENS concurrency 02 — FA-002's sibling, unfixed.

FA-002 fixed "a start that RAISED".  The other way a start never becomes a run
is `stream()`/`astream()` whose driver is dropped before it is ever advanced:
`_release_if_never_started` releases `_running` and blanks `_cancel_reason`,
but leaves `_has_started` up and never restores `_pending_cancel_reason`.

Same defect, same two halves, verbatim from 4010210's own commit message:
  * the Runtime's one queued cancellation is spent on a run that wrote no
    header, executed no node and returned no result;
  * `_has_started` stays up, so ADR-008 §1 reads the Runtime as used, cancel()
    returns False from then on, and the next run proceeds uncancelled.

No threads, no timing: the finaliser fires at the `del`.
"""
import gc, sys, pathlib

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(pathlib.Path(sys.argv[1]) if len(sys.argv) > 1 else ROOT / "src"))

from vitruvyan_motus.graph import GraphSpec
from vitruvyan_motus.runtime import Runtime

SPEC = GraphSpec.from_dict({
    "schema_version": "1.0.0", "name": "conc02", "version": "1.0.0",
    "entry": "node",
    "nodes": [{"name": "node", "effect_class": "pure"}],
    "transitions": {"node": {"kind": "terminal"}},
})

executed = []


def node(state):
    executed.append("node")
    return state


rt = Runtime(SPEC, {"node": node})

print("cancel() on a fresh Runtime            :", rt.cancel("shutdown before start"))

d = rt.stream(run_id="never-advanced")     # claims the run, advances nothing
del d
gc.collect()

print("records the abandoned run produced     :", len(rt.trace.records) if rt.trace else None)
print("nodes it executed                      :", executed)
print("cancel() again (can it be re-lodged?)  :", rt.cancel("shutdown, again"))

after = rt.run(run_id="the-next-run")
print("next run status                        :", after.status)
print("nodes executed after everything        :", executed)

lost = after.status != "cancelled" and executed == ["node"]
print()
print("QUEUED CANCELLATION LOST + UNRECOVERABLE:", lost)
sys.exit(0 if lost else 1)
