"""Telling the trace you touched the world, and what that buys you.

    python examples/06_effects_and_receipts.py

A node that reads a database, calls an API or charges a card has done
something the trace cannot infer and cannot undo. Motus does not detect it --
nothing can, from the outside of a function -- so the node declares it:

    recorded_effect   something happened, and here is what it was
    external_effect   something happened OUT THERE, and it may not be repeatable

The declaration is what makes resume safe. Motus will restart an incomplete run
only when every observed external effect carries BOTH a non-empty idempotency
key AND a completed receipt. Anything weaker and it refuses, because "the call
may or may not have gone through" is not a state you resume from. This is an
at-least-once condition with idempotent effects, never a proof of exactly-once
-- Motus promises exactly-once in no version, ever.
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
        {"name": "fetch", "effect_class": "external_effect"},
        {"name": "record", "effect_class": "recorded_effect"},
    ],
    "transitions": {"fetch": {"kind": "next", "to": "record"},
                    "record": {"kind": "terminal"}},
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


def make_fetch(*, receipted: bool):
    def fetch(state: State, ctx) -> State:
        # The effect is declared BEFORE anything is concluded from it, so an
        # attempt interrupted here leaves evidence that the call may have
        # happened -- recorded as unknown, never guessed at.
        ctx.record_effect(EffectDescriptor(
            EffectClass.EXTERNAL_EFFECT,
            "GET archive.org/download/travelsininter00park",
            # The key the external system deduplicates on. Yours to choose,
            # and meaningless unless the remote side honours it.
            idempotency_key="fetch:park-1795:v1" if receipted else None,
            receipt=EffectReceipt(
                receipt_id="dn760108.eu.archive.org/2026-08-10T00:00:00Z",
                status="completed",
                result_fingerprint=fingerprint(BODY),
            ) if receipted else None,
        ))
        return state.with_fact(Fact("bytes", len(BODY), "archive.org", NOW))

    return fetch


def record(state: State, ctx) -> State:
    # A recorded effect: it happened here, inside our own system, and it is
    # ours to repeat. No receipt is needed and none is invented -- Motus omits
    # the key entirely rather than emitting a null-filled object.
    ctx.record_effect(EffectDescriptor(
        EffectClass.RECORDED_EFFECT, "INSERT INTO verdict_traces"))
    return state.with_decision(Decision("stored", "yes", NOW))


def effects_of(trace: Trace) -> list[tuple[str, dict]]:
    return [(r["node"], effect)
            for r in trace.records if r["kind"] == "transition"
            for effect in r["effects"]]


def main() -> None:
    result = Runtime(
        SPEC, {"fetch": make_fetch(receipted=True), "record": record},
        sink=InMemoryTraceSink(),
    ).run(State.empty("fetch and store"), run_id="effects")

    print("what the trace says happened:\n")
    for node, effect in effects_of(result.trace):
        print(f"  {node:7} {effect['class']}")
        print(f"          {effect['description']}")
        if "idempotency_key" in effect:
            print(f"          idempotency_key: {effect['idempotency_key']}")
        receipt = effect.get("receipt")
        print(f"          receipt: {receipt if receipt else 'none — and the key is absent, not null'}")
        print()

    # --- what the declaration buys: a safe restart --------------------------

    def cut_after_fetch(trace: Trace) -> Trace:
        """An honest prefix: the process died before the run reached its end."""
        document = trace.to_dict()
        records = document["records"]
        stop = next(i for i, r in enumerate(records)
                    if r["kind"] == "routing" and r["after"] == "fetch")
        document["records"] = records[: stop + 1]
        return Trace.from_dict(document)

    nodes = {"fetch": make_fetch(receipted=True), "record": record}
    resumed = ReplayEngine(TraceBundle(SPEC, cut_after_fetch(result.trace))).resume(
        Runtime(SPEC, nodes, sink=InMemoryTraceSink()), run_id="effects-resumed")

    print("resume, with an idempotency key and a completed receipt:")
    print(f"  status        : {resumed.status}")
    print(f"  restarted at  : {resumed.trace.run['resume']['start_node']}")
    print(f"  linked to     : {resumed.trace.run['resume']['source_run_id']}")
    print("  the fetch was NOT re-executed; its recorded result was taken as given\n")

    # The same run, from a node that declared the effect and evidenced nothing.
    bare = Runtime(
        SPEC, {"fetch": make_fetch(receipted=False), "record": record},
        sink=InMemoryTraceSink(),
    ).run(State.empty("fetch and store"), run_id="effects-bare")

    print("resume, with the same effect and no receipt:")
    try:
        ReplayEngine(TraceBundle(SPEC, cut_after_fetch(bare.trace))).resume(
            Runtime(SPEC, {"fetch": make_fetch(receipted=False), "record": record}))
    except UnsafeResume as refusal:
        print(f"  refused       : {refusal}")
        print("\n  This is the whole point. Motus cannot know whether the archive")
        print("  was reached, so it will not restart a run that might repeat the")
        print("  call. Fail closed: the uncertainty is preserved, not resolved")
        print("  by assumption.")


if __name__ == "__main__":
    main()
