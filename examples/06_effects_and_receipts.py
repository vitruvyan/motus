"""Telling the trace you touched the world, and what that buys you.

    python examples/06_effects_and_receipts.py

A node that reads a database, calls an API or charges a card has done
something the trace cannot infer and cannot undo. Motus does not detect it --
nothing can, from the outside of a function -- so the node declares it.

The criterion is **read or mutate**, and nothing else:

    recorded_effect   you READ the world. HTTP GET, an LLM call, a file read:
                      the RESULT is the effect, and repeating it costs nothing
                      but a round trip.
    external_effect   you CHANGED the world. A row written, a message sent, a
                      POST. Repeating it may not be free, and the run cannot
                      undo it.

It is *not* "here versus out there", and it is not how important the call
feels. A `SELECT` against your own database is a recorded effect; an `INSERT`
against that same database is an external one.

The declaration is what makes resume safe. Motus will restart an incomplete run
only when every observed EXTERNAL effect carries BOTH a non-empty idempotency
key AND a completed receipt. Anything weaker and it refuses, because "the write
may or may not have landed" is not a state you resume from. This is an
at-least-once condition with idempotent effects, never a proof of exactly-once
-- Motus promises exactly-once in no version, ever.

Declaring a read as `external_effect` is merely over-conservative: it blocks
resumes that were safe. Declaring a write as `recorded_effect` is the dangerous
direction, and it is silent -- the resume guard simply never runs.
"""

from __future__ import annotations

import hashlib
from datetime import datetime, timezone

from vitruvyan_motus import (
    Decision, EffectClass, EffectDescriptor, EffectReceipt, Fact, GraphSpec,
    InMemoryTraceSink, ReplayEngine, Runtime, State, Trace, TraceBundle,
    UnsafeResume,
)

NOW = datetime(2026, 8, 10, tzinfo=timezone.utc)
BODY = b"the long sought for majestic Niger, glittering to the morning sun"

SPEC = GraphSpec.from_dict({
    "schema_version": "1.0.0",
    "name": "effects",
    "version": "1.0.0",
    "entry": "fetch",
    "nodes": [
        # A GET. The result IS the effect, and the archive is no different
        # afterwards -- so `recorded_effect`, and no receipt is owed.
        {"name": "fetch", "effect_class": "recorded_effect"},
        # An INSERT. The database IS different afterwards, and this run cannot
        # take it back -- so `external_effect`, and resume will ask for
        # evidence before it restarts anything.
        {"name": "store", "effect_class": "external_effect"},
        {"name": "confirm", "effect_class": "pure"},
    ],
    "transitions": {"fetch": {"kind": "next", "to": "store"},
                    "store": {"kind": "next", "to": "confirm"},
                    "confirm": {"kind": "terminal"}},
})


def fingerprint(payload: bytes) -> str:
    """`effect:sha256:<hex>` -- the format is fixed, the subject is yours.

    Schema 2.0.0 supersedes this field with `interaction_fingerprint`, which
    ADR-014 defines over the request and the result bound together: covering
    the result alone leaves the request swappable underneath an unchanged
    answer. The API for it has not landed yet (issue #52), so this is what a
    node can stamp today.
    """
    return "effect:sha256:" + hashlib.sha256(payload).hexdigest()


def fetch(state: State, ctx) -> State:
    # Declared before anything is concluded from it, so an attempt interrupted
    # here still leaves evidence that the call may have happened.
    #
    # No idempotency key and no receipt: this is a read. Motus omits the key
    # entirely rather than emitting a null-filled object, and resume will
    # re-execute this node without asking anyone's permission -- repeating a
    # GET changes nothing outside the run.
    ctx.record_effect(EffectDescriptor(
        EffectClass.RECORDED_EFFECT,
        "GET archive.org/download/travelsininter00park"))
    return state.with_fact(Fact("bytes", len(BODY), "archive.org", NOW))


def make_store(*, receipted: bool, honest: bool = True):
    """`honest=False` declares the write as a read, self-consistently.

    `writes` counts how many times the body actually ran, because the point
    below is not what the trace says -- it is that the INSERT is executed a
    second time.
    """
    writes: list[str] = []

    def store(state: State, ctx) -> State:
        writes.append("INSERT")
        ctx.record_effect(EffectDescriptor(
            EffectClass.EXTERNAL_EFFECT if honest else EffectClass.RECORDED_EFFECT,
            "INSERT INTO verdict_traces",
            # The key the external system deduplicates on. Yours to choose,
            # and meaningless unless the remote side honours it.
            idempotency_key="store:park-1795:v1" if receipted else None,
            receipt=EffectReceipt(
                receipt_id="verdict_traces/2026-08-10T00:00:00Z",
                status="completed",
                result_fingerprint=fingerprint(BODY),
            ) if receipted else None,
        ))
        return state.with_decision(Decision("stored", "yes", NOW))

    store.writes = writes            # type: ignore[attr-defined]
    return store


# The same graph with one word changed, and the node's own descriptor changed
# to match -- which is what a confused author actually writes, since nobody
# declares a `recorded_effect` node whose descriptor says EXTERNAL_EFFECT. That
# split version is caught immediately by a separate consistency check and never
# reaches a resume at all, so it does NOT demonstrate the danger. This does.
MISDECLARED = GraphSpec.from_dict({
    **SPEC.to_dict(),
    "nodes": [
        {"name": "fetch", "effect_class": "recorded_effect"},
        {"name": "store", "effect_class": "recorded_effect"},   # the mistake
        {"name": "confirm", "effect_class": "pure"},
    ],
})


def confirm(state: State) -> State:
    return state.with_fact(Fact("outcome", "recorded", "desk", NOW))


def effects_of(trace: Trace) -> list[tuple[str, dict]]:
    return [(r["node"], effect)
            for r in trace.records if r["kind"] == "transition"
            for effect in r["effects"]]


def _nodes(*, receipted: bool, honest: bool = True) -> dict:
    return {"fetch": fetch,
            "store": make_store(receipted=receipted, honest=honest),
            "confirm": confirm}


def main() -> None:
    result = Runtime(
        SPEC, _nodes(receipted=True), sink=InMemoryTraceSink(),
    ).run(State.empty("fetch and store"), run_id="effects")

    print("what the trace says happened:\n")
    for node, effect in effects_of(result.trace):
        print(f"  {node:8} {effect['class']}")
        print(f"           {effect['description']}")
        if "idempotency_key" in effect:
            print(f"           idempotency_key: {effect['idempotency_key']}")
        receipt = effect.get("receipt")
        print(f"           receipt: {receipt if receipt else 'none — and the key is absent, not null'}")
        print()

    # --- what the declaration buys: a safe restart --------------------------
    #
    # The cut falls AFTER the write. That is the only interesting place to die:
    # the INSERT was declared, the run never reached its end, and nobody knows
    # from the outside whether the row is there.

    def cut_after_store(trace: Trace) -> Trace:
        """An honest prefix: the process died before the run reached its end."""
        document = trace.to_dict()
        records = document["records"]
        stop = next(i for i, r in enumerate(records)
                    if r["kind"] == "routing" and r["after"] == "store")
        document["records"] = records[: stop + 1]
        return Trace.from_dict(document)

    resumed = ReplayEngine(TraceBundle(SPEC, cut_after_store(result.trace))).resume(
        Runtime(SPEC, _nodes(receipted=True), sink=InMemoryTraceSink()),
        run_id="effects-resumed")

    print("resume, with an idempotency key and a completed receipt:")
    print(f"  status        : {resumed.status}")
    print(f"  restarted at  : {resumed.trace.run['resume']['start_node']}")
    print(f"  linked to     : {resumed.trace.run['resume']['source_run_id']}")
    print("  the INSERT was NOT re-executed; its receipt was taken as given\n")

    # The same run, from a node that declared the effect and evidenced nothing.
    bare = Runtime(
        SPEC, _nodes(receipted=False), sink=InMemoryTraceSink(),
    ).run(State.empty("fetch and store"), run_id="effects-bare")

    print("resume, with the same effect and no receipt:")
    try:
        ReplayEngine(TraceBundle(SPEC, cut_after_store(bare.trace))).resume(
            Runtime(SPEC, _nodes(receipted=False), sink=InMemoryTraceSink()))
    except UnsafeResume as refusal:
        print(f"  refused       : {refusal}")
        print("\n  This is the whole point. Motus cannot know whether the row")
        print("  was written, so it will not restart a run that might write it")
        print("  twice. Fail closed: the uncertainty is preserved, not resolved")
        print("  by assumption.")
    # --- one word changed, and the guard stops existing -------------------
    #
    # This used to be a sentence printed at the end. It is executed now,
    # because a warning nobody can reproduce is indistinguishable from a
    # warning that is wrong -- and the first attempt at demonstrating it used a
    # cut where the resume restarts AFTER the write, so nothing was written
    # twice and the sentence would have been false.
    #
    # The cut that matters is DURING the write: the effect is in the trace and
    # the run never moved on. Nobody, from outside, can say whether the row is
    # there.

    def cut_during_store(trace: Trace) -> Trace:
        document = trace.to_dict()
        records = document["records"]
        # The attempt is in the trace and its transition is NOT: the node
        # started, the process died, and nobody outside can say whether the
        # row landed. Cutting after the transition instead would leave the
        # resume restarting at `confirm`, which writes nothing twice and
        # proves nothing -- that was the first version of this helper.
        stop = next(i for i, r in enumerate(records)
                    if r["kind"] == "attempt_started" and r["node"] == "store")
        document["records"] = records[: stop + 1]
        return Trace.from_dict(document)

    print("\ninterrupted DURING the write, declared honestly:")
    honest_run = Runtime(SPEC, _nodes(receipted=False), sink=InMemoryTraceSink()).run(
        State.empty("fetch and store"), run_id="effects-inflight")
    try:
        ReplayEngine(TraceBundle(SPEC, cut_during_store(honest_run.trace))).resume(
            Runtime(SPEC, _nodes(receipted=False), sink=InMemoryTraceSink()))
    except UnsafeResume as refusal:
        print(f"  refused       : {refusal}")

    print("\nthe same interruption, with `store` declared `recorded_effect`:")
    misdeclared = Runtime(MISDECLARED, _nodes(receipted=False, honest=False),
                          sink=InMemoryTraceSink()).run(
        State.empty("fetch and store"), run_id="effects-misdeclared")
    again = _nodes(receipted=False, honest=False)
    resumed = ReplayEngine(
        TraceBundle(MISDECLARED, cut_during_store(misdeclared.trace))
    ).resume(Runtime(MISDECLARED, again, sink=InMemoryTraceSink()))

    print(f"  status        : {resumed.status}")
    print(f"  restarted at  : {resumed.trace.run['resume']['start_node']}")
    print(f"  INSERTs run   : {len(again['store'].writes)}"
          f"  <- the row is written AGAIN")
    print("\n  No refusal, no receipt asked for, no exception. The guard reads")
    print("  the node's DECLARATION, never the effect it actually recorded, so")
    print("  a write that calls itself a read is never checked at all. That is")
    print("  the node protocol's honesty requirement stated as a price: Motus")
    print("  cannot do better than trust you, and this is what it costs when")
    print("  the declaration is wrong.")


if __name__ == "__main__":
    main()
