"""The frozen Terraveler compatibility corpus — guarantees.md §4.

Terraveler runs against the Axis 0.4.0 wheel today, in two production paths
(batch ingestion and RAG chat), and stores what `to_dict()` produces as jsonb
in `ingestion_runs.trace` and `chat_traces.trace`.  That makes the legacy shape
a DATA FORMAT in someone else's database, not merely an API: changing it is a
migration, and a migration nobody asked for is an outage.

This corpus pins two things:

1. the nine-symbol surface the audited consumer actually uses;
2. that a trace WRITTEN BY PRODUCTION still loads — the golden here is not
   synthetic, it is a real row lifted out of `ingestion_runs`, warts included:
   six top-level keys, no `metadata`, naive timestamps inside its events.

**Frozen.** The implementing agent may not edit this file or the golden.  When
the runtime moves, `tests/contract/kernel.py` names the compatibility view and
nothing here changes; if the view cannot satisfy a line below, that is an
amendment with its own ADR.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from tests.contract.kernel import (
    Decision,
    Fact,
    GraphState,
    NodeFailed,
    Policy,
    Rejection,
    Runner,
)

GOLDEN = Path(__file__).parent / "golden" / "production-ingestion-trace.json"

#: The exact top-level keys a v0.4.0 wheel wrote, and which Terraveler's rows
#: therefore contain.  `metadata` arrived after 0.4.0: the view MAY emit it,
#: readers of stored rows MUST tolerate its absence (guarantees.md §4).
LEGACY_KEYS = {"trace_id", "intent", "facts", "decisions", "rejections", "events"}
POST_040_OPTIONAL_KEYS = {"metadata"}


def _now():
    return datetime.now(timezone.utc)


# --------------------------------------------------------------------------- #
# The golden: a row that exists in production right now                       #
# --------------------------------------------------------------------------- #


def test_the_golden_is_the_shape_production_actually_stored():
    """The premise of this corpus, asserted rather than assumed."""
    document = json.loads(GOLDEN.read_text(encoding="utf-8"))

    assert set(document) == LEGACY_KEYS, (
        "the golden must be a v0.4.0-shaped row; if this fails the golden was "
        "edited, and a golden that can be edited is not evidence"
    )
    assert "metadata" not in document, (
        "Terraveler's stored rows predate the metadata key — that absence is "
        "precisely what the compatibility view must tolerate"
    )
    assert document["trace_id"] == "xuanzang-629-20260727T094134Z"


def test_a_real_production_trace_still_loads():
    """§4 / §5.7 against a real artifact, not a synthetic one.

    The stored row carries naive timestamps inside its events (the writer of
    the day emitted them that way).  A runtime that only reads what it
    currently writes has orphaned its own archive.
    """
    document = json.loads(GOLDEN.read_text(encoding="utf-8"))

    state = GraphState.from_dict(document)

    assert state.trace_id == document["trace_id"]
    assert state.intent == document["intent"]
    assert len(state.facts) == len(document["facts"])
    assert len(state.decisions) == len(document["decisions"])
    assert len(state.events) == len(document["events"])
    assert {fact.key for fact in state.facts} == {
        entry["key"] for entry in document["facts"]
    }


def test_a_loaded_production_trace_can_be_written_back():
    """Read-only compatibility is half a guarantee.

    Terraveler re-serializes states it has loaded; a view that can parse the
    archive but not reproduce it turns every read into a lossy migration.
    """
    document = json.loads(GOLDEN.read_text(encoding="utf-8"))

    round_tripped = GraphState.from_dict(document).to_dict()

    assert LEGACY_KEYS <= set(round_tripped), "no legacy key may disappear"
    assert set(round_tripped) - LEGACY_KEYS <= POST_040_OPTIONAL_KEYS, (
        "the view may add the post-0.4.0 optional key and nothing else — a new "
        "top-level key is a format change in a production database"
    )
    assert round_tripped["trace_id"] == document["trace_id"]
    assert round_tripped["intent"] == document["intent"]
    assert json.loads(json.dumps(round_tripped)), "the result must be storable"


# --------------------------------------------------------------------------- #
# The nine-symbol surface the audit found in production use                   #
# --------------------------------------------------------------------------- #


def test_state_construction_and_intent():
    """`GraphState.empty(trace_id)`, `.new(prefix)`, `.with_intent`.

    ingest/run.py builds its own trace id and calls `empty`; `new` is present
    in v0.4.0 and preserved defensively.
    """
    empty = GraphState.empty("terraveler-run-1")
    assert empty.trace_id == "terraveler-run-1"

    minted = GraphState.new("ingest")
    assert minted.trace_id.startswith("ingest-")
    assert minted.trace_id != GraphState.new("ingest").trace_id

    with_intent = empty.with_intent("ingest:xuanzang-629")
    assert with_intent.intent == "ingest:xuanzang-629"
    assert empty.intent is None, "the original state must be untouched"


def test_the_three_record_types_keep_their_v040_shape():
    """`Fact` / `Decision` / `Rejection` constructors as Terraveler builds them.

    The pipeline constructs these positionally in ingest/pipeline.py and
    ingest/codex.py; a reordered or renamed field breaks the ingestion at
    import time, not at review time.
    """
    fact = Fact("total_docs", 3, "chunk", _now())
    assert (fact.key, fact.value, fact.source) == ("total_docs", 3, "chunk")

    decision = Decision("licence gate passed", _now())
    assert decision.description == "licence gate passed"

    rejection = Rejection("source-42", "licence forbids ingestion", _now())
    assert (rejection.description, rejection.reason) == (
        "source-42",
        "licence forbids ingestion",
    )


def test_accumulating_records_and_reading_them_back():
    """`.with_fact` / `.with_decision` / `.with_rejection` and the collections.

    Every ingestion node contributes through these, and run.py reads
    `.facts`, `.decisions`, `.rejections` and `len(.events)` to build its
    summary row.
    """
    state = (
        GraphState.empty("terraveler-accumulate")
        .with_fact(Fact("embedded", 12, "embed", _now()))
        .with_decision(Decision("upserted 12 chunks", _now()))
        .with_rejection(Rejection("img-7", "not public domain", _now()))
    )

    assert [fact.key for fact in state.facts] == ["embedded"]
    assert state.fact("embedded") == 12
    assert len(state.decisions) == 1 and len(state.rejections) == 1
    assert isinstance(state.events, tuple)


def test_runner_and_both_policies():
    """`Runner(nodes, policy=)` with `Policy.STRICT` and `Policy.EXPLORATION`.

    run.py selects the policy from a CLI flag; both must remain constructible
    and must keep their divergent behavior (pinned in the inherited corpus).
    """
    nodes = [lambda state: state.with_fact(Fact("ran", True, "node", _now()))]

    for policy in (Policy.STRICT, Policy.EXPLORATION):
        final = Runner(nodes, policy=policy).run(GraphState.empty(f"pol-{policy}"))
        assert final.fact("ran") is True


def test_node_failed_carries_state_the_way_the_three_call_sites_use_it():
    """`NodeFailed.state` — relied on in ingest/run.py, ingest/extract.py and
    rag/app/main.py.

    This reproduces the production pattern exactly: catch, salvage, persist,
    exit non-zero.  It is the single most load-bearing line of the
    compatibility surface.
    """
    def establishes(state):
        return state.with_fact(Fact("total_docs", 0, "chunk", _now()))

    def fails(state):
        raise RuntimeError("embedding backend unreachable")

    runner = Runner([establishes, fails], policy=Policy.STRICT)

    try:
        final = runner.run(GraphState.empty("xuanzang-629-20260727T094134Z"))
        failure = None
    except NodeFailed as exc:            # exactly ingest/run.py:84-86
        final = exc.state
        failure = exc

    assert failure is not None
    assert final is not None, "the salvaged state is what gets persisted"
    assert final.fact("total_docs") == 0
    stored = final.to_dict()             # the jsonb that reaches ingestion_runs
    assert json.loads(json.dumps(stored))["trace_id"] == (
        "xuanzang-629-20260727T094134Z"
    )


def test_to_dict_of_a_fresh_run_stays_within_the_stored_shape():
    """The format Terraveler's tables will keep receiving.

    A new top-level key is not a feature here; it is a schema change in a
    database this repository does not own.
    """
    state = (
        GraphState.empty("shape-check")
        .with_intent("ingest:shape")
        .with_fact(Fact("k", 1, "s", _now()))
    )

    document = state.to_dict()

    assert LEGACY_KEYS <= set(document)
    assert set(document) - LEGACY_KEYS <= POST_040_OPTIONAL_KEYS, (
        f"unexpected top-level key(s): {sorted(set(document) - LEGACY_KEYS)}"
    )
    for record in document["facts"]:
        assert set(record) == {"key", "value", "source", "timestamp"}


@pytest.mark.parametrize(
    "name",
    ["trace_id", "intent", "facts", "decisions", "rejections", "events"],
)
def test_the_state_attributes_terraveler_reads_all_exist(name):
    """run.py and chat_graph.py read these directly off the final state."""
    assert hasattr(GraphState.empty("attrs"), name)
