"""Two specs the contract calls identical must produce identical evidence.

`graph_fingerprint` is computed over key-sorted canonical JSON, so two
GraphSpecs whose route maps differ only in the order they were written are THE
SAME GRAPH by the contract's own identity. The runtime emitted `candidates` in
dict insertion order, so they produced different traces with different roots —
the fingerprint and the trace answering one question two ways (#124, found by
the Orbis integration, which lost half an hour to it and would have lost more
when somebody re-read an old trace).

Order cannot carry meaning here: `contract/trace.v1.schema.json` admits exactly
three condition kinds — `map`, `default`, `static` — and none of them is a
predicate, so a route is a key lookup and never a first-match scan.
`contract/validate.py` already agreed, comparing candidates as a `Counter`.
"""

from __future__ import annotations

import json

import pytest

from vitruvyan_motus import (
    Decision, Fact, GraphSpec, InMemoryTraceSink, ReplayEngine, Runtime, State,
    TraceBundle,
)

AT = "2026-01-01T00:00:00Z"


def _spec(order):
    return GraphSpec.from_dict({
        "schema_version": "1.0.0", "name": "t", "version": "1.0.0", "entry": "a",
        "nodes": [{"name": "a", "effect_class": "pure"},
                  *({"name": n, "effect_class": "pure"} for n in ("x", "y", "z"))],
        "transitions": {
            "a": {"kind": "route", "on": "d", "map": {k: k for k in order},
                  "default": "z"},
            **{n: {"kind": "terminal"} for n in ("x", "y", "z")},
        },
    })


def _nodes():
    return {
        "a": lambda s: s.with_decision(Decision("d", "x", AT)),
        "x": lambda s: s.with_fact(Fact("o", "x", "s", AT)),
        "y": lambda s: s,
        "z": lambda s: s,
    }


def _run(spec):
    return Runtime(spec, _nodes(), sink=InMemoryTraceSink()).run(
        State.empty("q"), run_id="r1")


def _routing(document):
    return next(r for r in document["records"]
                if r["kind"] == "routing" and r.get("on"))


def test_one_fingerprint_cannot_mean_two_traces():
    """The defect itself. Fails on insertion-order emission."""
    ascending, descending = _spec(["x", "y", "z"]), _spec(["z", "y", "x"])
    assert ascending.graph_fingerprint == descending.graph_fingerprint, (
        "the fingerprint sorts keys, so these two are one graph — if this "
        "assertion fails the premise of #124 has changed and the rest of this "
        "module is asking the wrong question")

    one = _routing(_run(ascending).trace.to_dict())
    two = _routing(_run(descending).trace.to_dict())
    assert one["candidates"] == two["candidates"], (
        "one graph_fingerprint produced two different candidate orders: the "
        "trace disagrees with the identity the contract assigned it")


def test_candidates_are_emitted_in_a_canonical_order():
    """Sorted by map key, default last — a property, not an accident of input."""
    for order in (["x", "y", "z"], ["z", "y", "x"], ["y", "z", "x"]):
        candidates = _routing(_run(_spec(order)).trace.to_dict())["candidates"]
        keys = [c["condition"]["key"] for c in candidates
                if c["condition"]["kind"] == "map"]
        assert keys == sorted(keys), f"written {order}, emitted {keys}"
        assert candidates[-1]["condition"] == {"kind": "default"}, (
            "the default is the one candidate with no key to sort by, and it "
            "belongs last so the order is total")


def test_the_root_no_longer_depends_on_how_the_map_was_written():
    """What an integrator actually loses to this: a spec through
    `json.dumps(sort_keys=True)` used to produce a different execution."""
    written = _spec(["z", "y", "x"])
    resorted = GraphSpec.from_dict(
        json.loads(json.dumps(written.to_dict(), sort_keys=True)))
    assert _routing(_run(written).trace.to_dict())["candidates"] == \
        _routing(_run(resorted).trace.to_dict())["candidates"]


def test_a_trace_written_before_the_fix_still_verifies():
    """The half that makes the fix safe rather than merely correct.

    Every trace written before #124 carries candidates in whatever order its
    GraphSpec happened to use. Sorting only the expectation would have refused
    them — turning a fix for evidence that could not be compared into a reason
    that older evidence could not be verified.
    """
    spec = _spec(["z", "y", "x"])
    result = _run(spec)
    document = result.trace.to_dict()

    # Put the routing record back the way a pre-fix runtime wrote it.
    routing = _routing(document)
    routing["candidates"] = list(reversed(routing["candidates"]))
    keys = [c["condition"]["key"] for c in routing["candidates"]
            if c["condition"]["kind"] == "map"]
    assert keys != sorted(keys), "the fixture must be in the OLD order"

    from vitruvyan_motus import Trace
    bundle = TraceBundle(spec, Trace.from_dict(_resealed(document)))
    # No exception, and specifically not "routing semantics disagree with the
    # GraphSpec and recorded state": replay compares candidates as a set,
    # because their order was never semantics.
    outcome = ReplayEngine(bundle).verify(_nodes())
    assert outcome.verified, outcome


def _resealed(document: dict) -> dict:
    """Re-seal the integrity chain after editing a record.

    The edit above changes bytes, so the chain no longer holds — which is the
    chain doing its job. Replay is being asked about SEMANTICS here, and it
    cannot be asked anything at all through a document that fails its own
    integrity check first.
    """
    import copy
    import hashlib

    from vitruvyan_motus.trace import _canonical_bytes

    out = copy.deepcopy(document)
    prev = "sha256:" + hashlib.sha256(_canonical_bytes({
        "schema_version": out["schema_version"], "run": out["run"]})).hexdigest()
    for record in out["records"]:
        payload = dict(record)
        payload["integrity"] = {"payload_hash": None, "prev_hash": prev}
        digest = "sha256:" + hashlib.sha256(_canonical_bytes(payload)).hexdigest()
        record["integrity"] = {"payload_hash": digest, "prev_hash": prev}
        prev = digest
    return out
