"""Does today's Motus still verify a trace nobody touched?

``ReplayEngine.verify()`` re-executes a trace's ``pure`` nodes and raises
``ReplayMismatch`` on any disagreement. A reader who sees that exception
takes it to mean tampering or code drift — that is the entire point of the
mechanism (README, "what the trace proves"). But #56 shipped a mismatch on
an archived trace that was never touched: ``State.snapshot()`` started being
recorded as the bulk read it always was, so a trace made before that fix
disagrees with a verify() made after it, for a reason that has nothing to do
with the trace's own history. ADR-015 records the founder's call on that
specific defect — the old archives were simply invalidated, because nothing
had certified them yet. What ADR-015 also records is the durable finding
underneath: *nothing in the suite verified an archived trace against a later
version of Motus*, so nothing would catch the next one, or notice this one
happening again to a trace that never used ``snapshot()`` and has no reason
to break.

This module is that missing check. ``tests/crossversion/`` holds trace
documents produced by released Motus versions (via
``tests/crossversion/generate.py``, which exports each tag with ``git
archive`` — never a working-tree checkout) plus the exact node source that
produced them. For every one of them we assert two things a maintainer
should be able to take for granted: the contract validator still accepts the
document, and ``ReplayEngine.verify()`` does not raise. A failure here is not
"someone forged evidence" — it is "a change on this branch altered how Motus
*records* what a node does, and it just falsified real archived evidence."
The assertion messages say that explicitly, because the whole value of this
test category is that whoever meets it first knows immediately which of
those two things happened.

Proving this test can actually fail (not just "cannot raise" by
construction) is part of the change that added it: hand-corrupt one
scenario's ``reads`` to the shape a pre-#56 Motus produced for a
``snapshot()``-using node, watch this suite fail with the message below
instead of a bare ``ReplayMismatch``, then restore the file from git. See
the commit/PR description for that transcript; it is not re-run here because
a corpus file that is *supposed* to fail verify cannot also be a fixture
this module asserts passes.
"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

jsonschema = pytest.importorskip("jsonschema")

REPO_ROOT = Path(__file__).resolve().parent.parent
CONTRACT_DIR = REPO_ROOT / "contract"
CROSSVERSION_DIR = Path(__file__).resolve().parent / "crossversion"

sys.path.insert(0, str(CROSSVERSION_DIR))
import nodes  # noqa: E402  (path insert above must run first)

from vitruvyan_motus import GraphSpec, ReplayEngine, Trace, TraceBundle  # noqa: E402


def _load_validate_module():
    """Import contract/validate.py by path (contract/ is not a package).

    Copied from test_contract_fixtures.py's idiom rather than reinvented —
    two ways to load the same validator would themselves be the kind of
    drift this suite exists to distrust.
    """
    module_name = "motus_contract_validate"
    if module_name in sys.modules:
        return sys.modules[module_name]
    spec = importlib.util.spec_from_file_location(
        module_name, CONTRACT_DIR / "validate.py"
    )
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    try:
        spec.loader.exec_module(module)
    except Exception:
        sys.modules.pop(module_name, None)
        raise
    return module


validate = _load_validate_module()


def _read_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


MANIFEST = _read_json(CROSSVERSION_DIR / "manifest.json")
assert MANIFEST, (
    "tests/crossversion/manifest.json is empty — the corpus this suite "
    "depends on was never generated. Run "
    "`.venv/bin/python tests/crossversion/generate.py`."
)


def _entry_id(entry: dict) -> str:
    return f"{entry['version']}/{entry['scenario']}"


@pytest.mark.parametrize("entry", MANIFEST, ids=_entry_id)
def test_an_archived_trace_still_validates_against_the_current_contract(entry):
    """The contract validator must still accept evidence from every released version.

    contract/validate.py is what a third party runs against a Motus trace
    years later, with no runtime installed. If it starts rejecting a
    document a real released version produced and never claimed was
    malformed, the contract broke backward compatibility on the way past —
    and ADR-001's authority order says that is a decision with its own ADR,
    never a side effect of something else.
    """
    trace_doc = _read_json(CROSSVERSION_DIR / entry["trace"])
    spec_doc = _read_json(CROSSVERSION_DIR / entry["graph_spec"])
    violations = validate.validate_trace(trace_doc, spec_doc)
    assert violations == [], (
        f"contract/validate.py rejects a trace {entry['version']} itself "
        f"produced ({_entry_id(entry)}). This is a CONTRACT regression, not "
        f"a corrupted fixture: a real archived trace just failed validation "
        f"against a change on this branch. Violations: {violations!r}"
    )


@pytest.mark.parametrize("entry", MANIFEST, ids=_entry_id)
def test_verify_does_not_accuse_an_untouched_archived_trace_of_divergence(entry):
    """verify() must not raise ReplayMismatch on a trace nothing touched.

    Every trace here was produced by a released Motus, archived byte for
    byte, and has not been edited since (tests/crossversion/generate.py is
    how it was made; nothing in this repository writes to
    tests/crossversion/v*/ except that script). If ReplayEngine.verify()
    raises here, the trace is not lying — a change on THIS branch altered
    what re-executing these node functions produces or how a read/write/
    draw is recorded, and it just turned real historical evidence into a
    false accusation of tampering. That is the #56 shape of defect this
    module exists to catch before it reaches a released version instead of
    after (ADR-015).
    """
    trace_doc = _read_json(CROSSVERSION_DIR / entry["trace"])
    spec_doc = _read_json(CROSSVERSION_DIR / entry["graph_spec"])
    bundle = TraceBundle(GraphSpec.from_dict(spec_doc), Trace.from_dict(trace_doc))
    registry = {name: getattr(nodes, fn) for name, fn in entry["nodes"].items()}
    try:
        ReplayEngine(bundle).verify(registry)
    except Exception as exc:  # noqa: BLE001 — re-raised below with context, not swallowed
        raise AssertionError(
            f"ReplayEngine.verify() raised {exc!r} re-executing an archived, "
            f"untouched trace ({_entry_id(entry)}, produced by Motus "
            f"{entry['version']}). Read that as: THIS BRANCH CHANGED HOW "
            f"MOTUS RECORDS EXECUTION, not that the trace was tampered "
            f"with — see #56/ADR-015 for the shape of defect this check "
            f"exists to catch. Find what changed about how "
            f"{sorted(entry['nodes'])} are re-executed or how their reads/"
            f"writes/context draws are recorded, before this reaches a "
            f"release."
        ) from exc
