from __future__ import annotations

import time
from datetime import datetime, timezone

import pytest

from vitruvyan_motus.state import State
from vitruvyan_motus.trace import Decision, Fact, RedactedValue, Rejection, Trace, redact


NOW = datetime(2026, 8, 4, tzinfo=timezone.utc)


def test_write_boundary_and_reads_are_non_aliasing():
    source = {"nested": [1]}
    state = State.new("isolate", facts=[Fact("x", source, "test", NOW)])
    source["nested"].append(2)
    observed = state.fact("x")
    observed["nested"].append(3)
    assert state.fact("x") == {"nested": [1]}


@pytest.mark.parametrize("bad", [(1, 2), {1, 2}, {1: "x"}, float("nan"), float("inf")])
def test_strict_json_boundary_refuses_coercion_and_non_finite_values(bad):
    with pytest.raises((TypeError, ValueError)):
        Fact("bad", bad, "test", NOW)


def test_redaction_is_runtime_produced_and_secret_never_survives():
    secret = {"token": "very-secret"}
    value = redact(secret, "policy:credentials")
    assert isinstance(value, RedactedValue)
    fact = Fact("credential", value, "test", NOW)
    wire = fact.to_dict()["value"]
    assert wire["kind"] == "redacted"
    assert "very-secret" not in str(wire)
    with pytest.raises(ValueError, match="reserved"):
        Fact("forged", wire, "test", NOW)


def test_pending_writes_commit_with_per_value_origins_and_prefix_check():
    base = State.new("run", facts=[Fact("seed", 1, "test", NOW)])
    attempt = base._attempt_view(Trace({}).log)
    returned = attempt.with_fact(Fact("x", 2, "node", NOW)).with_decision(
        Decision("route", "yes", NOW)
    )
    committed = attempt._committed(returned, 7)
    next_attempt = committed._attempt_view(Trace({}).log)
    assert next_attempt.fact("x") == 2
    assert next_attempt._reads_wire()[-1]["origin"] == {
        "kind": "transition", "seq": 7, "collection": "facts", "index": 0,
    }
    foreign = State.empty("foreign")
    with pytest.raises(ValueError, match="prefix"):
        attempt._committed(foreign, 8)


def test_attempt_local_writes_are_not_readable_before_their_causal_commit():
    pending = State.empty()._attempt_view(Trace({}).log).with_fact(
        Fact("x", 1, "test", NOW)
    )
    with pytest.raises(RuntimeError, match="before commit"):
        pending.fact("x")
    with pytest.raises(RuntimeError, match="before commit"):
        _ = pending.facts


def test_chunk_index_preserves_old_and_sibling_snapshot_views():
    base = State.new(
        "chunk-index",
        facts=[Fact(f"k{index}", index, "seed", NOW) for index in range(64)],
    )

    def branch(value):
        attempt = base._attempt_view(Trace({}).log)
        returned = attempt.with_fact(Fact("k0", value, "branch", NOW))
        return attempt._committed(returned, 1)

    left = branch("left")
    right = branch("right")

    assert base.fact("k0") == 0
    assert left.fact("k0") == "left"
    assert right.fact("k0") == "right"
    assert left.fact("k63") == 63
    assert right.fact("absent") is None


def test_reads_capture_initial_absent_header_and_scan_origins():
    state = State.new(
        "intent", facts=[Fact("seed", 1, "test", NOW)], metadata={"actor": "d"}
    )._attempt_view(Trace({}).log)
    assert state.fact("seed") == 1
    assert state.decision("missing") is None
    assert state.intent == "intent"
    assert state.metadata("actor") == "d"
    assert state.metadata("absent") is None
    assert len(state.facts) == 1
    origins = [read["origin"] for read in state._reads_wire()]
    assert origins == [
        {"kind": "initial", "collection": "facts", "index": 0},
        {"kind": "absent", "surface": "decisions"},
        {"kind": "header", "field": "intent"},
        {"kind": "header", "field": "metadata"},
        {"kind": "absent", "surface": "metadata"},
        {"kind": "scan", "collection": "facts"},
    ]


def test_chunked_accumulation_is_not_quadratic_at_the_predecessor_scale():
    def build(count):
        state = State.empty()
        start = time.perf_counter()
        for index in range(count):
            state = state.with_fact(Fact(str(index), index, "bench", NOW))
        return time.perf_counter() - start

    small = min(build(400) for _ in range(3))
    large = min(build(800) for _ in range(3))
    assert large < small * 3.5


def test_trace_json_and_jsonl_encode_the_same_records():
    run = {"run_id": "r", "metadata": {}}
    trace = Trace(run).append({"seq": 1, "kind": "x"})
    json_doc = trace.to_dict()
    lines = [__import__("json").loads(line) for line in trace.to_jsonl().splitlines()]
    assert lines[0]["run"] == json_doc["run"]
    assert lines[1:] == json_doc["records"]


def test_trace_views_cannot_mutate_the_append_only_evidence():
    trace = Trace({"run_id": "r", "metadata": {}}).append({
        "seq": 1, "kind": "x", "nested": {"items": [1]}
    })
    document = trace.to_dict()
    document["records"][0]["kind"] = "tampered"
    document["records"][0]["nested"]["items"].append(2)
    dict.__setitem__(document["run"], "run_id", "forged")
    document["schema_version"] = "local-copy"
    fresh = trace.to_dict()
    assert fresh["schema_version"] != "local-copy"
    assert fresh["run"]["run_id"] == "r"
    assert fresh["records"][0]["kind"] == "x"
    assert fresh["records"][0]["nested"]["items"] == [1]


@pytest.mark.parametrize(
    "timestamp",
    ["2026-08-04 00:00:00Z", "2026-08-04T00:00:00.1234567Z"],
)
def test_wire_timestamps_match_the_contracts_canonical_form(timestamp):
    with pytest.raises(ValueError, match="canonical RFC 3339"):
        Fact("x", 1, "test", timestamp)


def test_initial_collections_and_standard_metadata_are_schema_safe():
    with pytest.raises(TypeError, match="facts must contain only Fact"):
        State.new(facts=[Decision("x", "y", NOW)])
    with pytest.raises(TypeError, match="decisions must contain only Decision"):
        State.new(decisions=[Fact("x", 1, "test", NOW)])
    with pytest.raises(TypeError, match="metadata must be a JSON object"):
        State.empty(metadata=[])
    with pytest.raises(TypeError, match="'actor' must be a string"):
        State.empty(metadata={"actor": 123})


def test_snapshot_from_inside_a_node_is_a_bulk_read_and_is_recorded_as_one():
    """The defect this pins, and it made the contract's central claim false.

    `snapshot()` returned the entire committed state and recorded nothing. So a
    node could declare `reads_declared: []`, read every fact — including ones it
    had no business seeing — and leave behind a trace stating it read nothing.
    The README's promise is that the trace describes *what each node read and
    wrote*; that was untrue for any node using this method.

    It was never a missing mechanism. `_scan` already recorded a bulk read
    correctly, so the same state had two doors into the same room and only one
    had a guard. Both now go through `_note_scan`.

    Three scans are recorded, not the keys present, because `snapshot()` asks
    for all three collections regardless of what is in them. Recording only the
    non-empty ones would make the required declaration depend on the data — the
    same node would need a different one against a state with no decisions yet,
    and a declaration that varies with the data is not a declaration.
    """
    state = State.new(
        "intent",
        facts=[Fact("salary", 120000, "hr", NOW)],
        decisions=[Decision("route", "approve", NOW, "why")],
    )._attempt_view(Trace({}).log)

    out = state.snapshot()

    # The read was real: the value is in the caller's hands.
    assert [item["key"] for item in out["facts"]] == ["salary"]
    assert out["facts"][0]["value"] == 120000

    origins = [read["origin"] for read in state._reads_wire()]
    assert origins == [
        {"kind": "scan", "collection": "facts"},
        {"kind": "scan", "collection": "decisions"},
        {"kind": "scan", "collection": "rejections"},
    ]


def test_snapshot_and_the_scan_property_record_an_identical_read():
    """The property that keeps the two doors from drifting apart again.

    Asserting each side's absolute output would let them diverge one edit at a
    time while both tests stayed green. What matters is that reading every fact
    is recorded the same way whichever surface was used, so this compares them.
    """
    def fresh():
        return State.new(
            "intent", facts=[Fact("salary", 120000, "hr", NOW)]
        )._attempt_view(Trace({}).log)

    def facts_read(state):
        return [
            read for read in state._reads_wire()
            if read["origin"].get("collection") == "facts"
        ]

    via_property = fresh()
    assert len(via_property.facts) == 1

    via_snapshot = fresh()
    via_snapshot.snapshot()

    assert facts_read(via_property) == facts_read(via_snapshot)
    assert facts_read(via_snapshot) != []


def test_snapshot_from_the_host_records_nothing():
    """Serialising state for storage or replay is not a node reading it.

    `_reads` is None outside an attempt, so the host path must stay silent —
    otherwise every `to_jsonl` and every resume would invent reads that no node
    performed, which is the same class of lie in the opposite direction.
    """
    state = State.new("intent", facts=[Fact("salary", 120000, "hr", NOW)])
    assert state._reads is None
    assert state.snapshot()["facts"][0]["key"] == "salary"
    assert state._reads is None
