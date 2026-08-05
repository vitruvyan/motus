"""Attacks the audited implementation correctly resisted.

These are regression tests, not findings: each one is an attack that WOULD
have been a finding, pinned so a future release cannot quietly lose the
property. They all pass against
2a860a7c5c57f95dd471ccc1ff66bcf7bd10c7f6.

They are deterministic — no sleeps, no threads, no wall-clock thresholds.
"""

from __future__ import annotations

import importlib.util
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest

from vitruvyan_motus import (
    Decision,
    DurabilityProfile,
    EffectClass,
    EffectDescriptor,
    EffectReceipt,
    Fact,
    GraphSpec,
    GraphSpecValidationError,
    NodeFailed,
    Policy,
    Rejection,
    ReplayEngine,
    ReplayMismatch,
    ReplayStatus,
    Runtime,
    SinkFailed,
    State,
    Trace,
    TraceBundle,
    UnsafeResume,
    redact,
)

ROOT = Path(__file__).resolve().parents[2]
NOW = datetime(2026, 8, 4, tzinfo=timezone.utc)


def _load_validator():
    spec = importlib.util.spec_from_file_location(
        "motus_contract_validate_adv", ROOT / "contract" / "validate.py"
    )
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


validate = _load_validator()


def _identity(state: State) -> State:
    return state


LINEAR_DOC = {
    "schema_version": "1.0.0",
    "name": "adv-linear",
    "version": "1.0.0",
    "entry": "a",
    "nodes": [
        {"name": "a", "effect_class": "pure"},
        {"name": "b", "effect_class": "pure"},
    ],
    "transitions": {"a": {"kind": "next", "to": "b"}, "b": {"kind": "terminal"}},
}
LINEAR = GraphSpec.from_dict(dict(LINEAR_DOC))

ROUTED_DOC = {
    "schema_version": "1.0.0",
    "name": "adv-routed",
    "version": "1.0.0",
    "entry": "a",
    "nodes": [
        {"name": "a", "effect_class": "pure"},
        {"name": "x", "effect_class": "pure"},
        {"name": "y", "effect_class": "pure"},
    ],
    "transitions": {
        "a": {"kind": "route", "on": "k", "map": {"one": "x", "two": "y"}},
        "x": {"kind": "terminal"},
        "y": {"kind": "terminal"},
    },
}
ROUTED = GraphSpec.from_dict(dict(ROUTED_DOC))


# --------------------------------------------------------------------------- #
# C — state isolation and causal provenance                                    #
# --------------------------------------------------------------------------- #


def test_written_value_is_isolated_from_post_return_mutation():
    payload = {"nested": {"secret": "before"}, "items": [1, 2]}

    def node(state: State) -> State:
        out = state.with_fact(Fact("k", payload, "s", NOW))
        payload["nested"]["secret"] = "AFTER"
        payload["items"].append(999)
        return out

    result = Runtime(LINEAR, {"a": node, "b": _identity}).run(State.empty("iso"))
    recorded = next(r for r in result.trace.records if r["kind"] == "transition")
    assert recorded["writes"]["facts"][0]["value"] == {
        "nested": {"secret": "before"},
        "items": [1, 2],
    }
    assert result.state.fact("k") == {"nested": {"secret": "before"}, "items": [1, 2]}


def test_value_returned_by_a_read_cannot_reach_committed_state():
    def writer(state: State) -> State:
        return state.with_fact(Fact("k", {"a": [1]}, "s", NOW))

    def reader(state: State) -> State:
        borrowed = state.fact("k")
        borrowed["a"].append(42)
        return state

    result = Runtime(LINEAR, {"a": writer, "b": reader}).run(State.empty("iso"))
    assert result.state.fact("k") == {"a": [1]}


def test_routing_names_the_exact_decision_entry_not_an_equal_payload():
    """Two seeded decisions share a key; the route must name index 1."""
    seed = State.new(
        "prov", decisions=[Decision("k", "one", NOW), Decision("k", "two", NOW)]
    )
    result = Runtime(ROUTED, {"a": _identity, "x": _identity, "y": _identity}).run(seed)
    routing = next(r for r in result.trace.records if r["kind"] == "routing")
    assert routing["value"] == "two"
    assert routing["origin"] == {"kind": "initial", "index": 1}
    assert routing["selected"] == "y"


def test_overwritten_decision_route_names_the_committing_transition():
    def first(state: State) -> State:
        return state.with_decision(Decision("k", "one", NOW))

    def second(state: State) -> State:
        return state.with_decision(Decision("k", "two", NOW))

    doc = {
        "schema_version": "1.0.0",
        "name": "adv-overwrite",
        "version": "1.0.0",
        "entry": "a",
        "nodes": [
            {"name": "a", "effect_class": "pure"},
            {"name": "b", "effect_class": "pure"},
            {"name": "x", "effect_class": "pure"},
            {"name": "y", "effect_class": "pure"},
        ],
        "transitions": {
            "a": {"kind": "next", "to": "b"},
            "b": {"kind": "route", "on": "k", "map": {"one": "x", "two": "y"}},
            "x": {"kind": "terminal"},
            "y": {"kind": "terminal"},
        },
    }
    spec = GraphSpec.from_dict(doc)
    result = Runtime(
        spec, {"a": first, "b": second, "x": _identity, "y": _identity}
    ).run(State.empty("prov"))
    transition_b = next(
        r for r in result.trace.records if r["kind"] == "transition" and r["node"] == "b"
    )
    routing_b = next(
        r for r in result.trace.records if r["kind"] == "routing" and r["after"] == "b"
    )
    assert routing_b["origin"] == {
        "kind": "transition",
        "seq": transition_b["seq"],
        "index": 0,
    }


@pytest.mark.parametrize("value", [True, False, 1, 0, 1.0, None, ["one"], {"k": "one"}])
def test_non_string_decision_values_never_match_a_string_route_key(value):
    """JSON true and the string "true" must not be conflated by routing."""
    doc = dict(ROUTED_DOC)
    doc = json.loads(json.dumps(doc))
    doc["name"] = "adv-typed"
    doc["transitions"]["a"]["map"] = {"true": "x", "1": "y"}
    spec = GraphSpec.from_dict(doc)

    def decide(state: State) -> State:
        return state.with_decision(Decision("k", value, NOW))

    result = Runtime(
        spec, {"a": decide, "x": _identity, "y": _identity}, policy=Policy.EXPLORATION
    ).run(State.empty("typed"))
    routing = next(r for r in result.trace.records if r["kind"] == "routing")
    assert routing["outcome"] == "miss"
    assert routing["selected"] == "END"


def test_absent_scan_and_header_reads_all_owe_a_declaration():
    """node-protocol 3.2: no origin kind is exempt from reads_declared."""

    def probe(state: State) -> State:
        state.fact("later")
        state.metadata("nope")
        _ = state.intent
        _ = state.facts
        return state

    doc = json.loads(json.dumps(LINEAR_DOC))
    doc["name"] = "adv-declared"
    doc["nodes"][0]["reads_declared"] = []
    spec = GraphSpec.from_dict(doc)
    result = Runtime(
        spec, {"a": probe, "b": _identity}, policy=Policy.EXPLORATION
    ).run(State.empty("declared"))
    transition = next(r for r in result.trace.records if r["kind"] == "transition")
    assert sorted(v["key"] for v in transition["violations"]) == [
        "facts",
        "intent",
        "later",
        "nope",
    ]


@pytest.mark.parametrize(
    "payload",
    [
        {"kind": "redacted", "hash": "redacted:sha256:" + "0" * 64, "policy_ref": "p"},
        {"outer": {"kind": "redacted", "hash": "x", "policy_ref": "p"}},
        [{"kind": "redacted"}],
    ],
)
def test_hand_forged_redacted_values_are_refused_at_any_depth(payload):
    with pytest.raises(ValueError):
        Fact("k", payload, "s", NOW)


def test_genuine_redaction_records_hash_and_policy_only():
    marker = redact({"password": "hunter2"}, "policy://pii")
    wire = Fact("k", marker, "s", NOW).to_dict()["value"]
    assert set(wire) == {"kind", "hash", "policy_ref"}
    assert "hunter2" not in json.dumps(wire)


@pytest.mark.parametrize("bad", [float("nan"), float("inf"), float("-inf")])
def test_non_finite_numbers_are_refused_at_the_write_boundary(bad):
    with pytest.raises(ValueError):
        Fact("k", bad, "s", NOW)


@pytest.mark.parametrize("bad", [(1, 2), {1, 2}, object(), NOW])
def test_non_json_python_values_are_refused_at_the_write_boundary(bad):
    with pytest.raises((TypeError, ValueError)):
        Fact("k", bad, "s", NOW)


def test_non_string_object_keys_are_refused():
    with pytest.raises(TypeError):
        Fact("k", {1: "x"}, "s", NOW)


def test_a_foreign_state_cannot_be_committed():
    """node-protocol 1.2(c): the returned state must extend the input prefix."""
    foreign = State.empty("elsewhere").with_fact(Fact("evil", 1, "s", NOW))

    def node(state: State) -> State:
        return foreign

    runtime = Runtime(LINEAR, {"a": node, "b": _identity})
    with pytest.raises(NodeFailed):
        runtime.run(State.empty("lineage"))
    transition = next(r for r in runtime.trace.records if r["kind"] == "transition")
    assert transition["outcome"] == "raised"
    assert transition["writes"] == {"facts": [], "decisions": [], "rejections": []}


# --------------------------------------------------------------------------- #
# B/D — execution state machine, retries, failure                              #
# --------------------------------------------------------------------------- #


def test_raised_attempts_commit_nothing_and_keep_their_reads():
    def node(state: State) -> State:
        state.fact("probe")
        state.with_fact(Fact("never", 1, "s", NOW))
        raise RuntimeError("after reading and staging a write")

    runtime = Runtime(LINEAR, {"a": node, "b": _identity})
    with pytest.raises(NodeFailed):
        runtime.run(State.empty("txn"))
    transition = next(r for r in runtime.trace.records if r["kind"] == "transition")
    assert transition["outcome"] == "raised"
    assert transition["writes"] == {"facts": [], "decisions": [], "rejections": []}
    assert [r["key"] for r in transition["reads"]] == ["probe"]


def test_retry_exhaustion_preserves_every_attempt_and_fails_the_run():
    attempts = []

    def flaky(state: State) -> State:
        attempts.append(len(attempts) + 1)
        raise ValueError("always")

    runtime = Runtime(
        LINEAR, {"a": flaky, "b": _identity}, max_attempts={"a": 3, "b": 1}
    )
    with pytest.raises(NodeFailed) as caught:
        runtime.run(State.empty("retry"))
    recorded = [
        (r["attempt"], r["outcome"], r["disposition"])
        for r in runtime.trace.records
        if r["kind"] == "transition"
    ]
    assert recorded == [(1, "raised", "retry"), (2, "raised", "retry"), (3, "raised", "abort")]
    assert isinstance(caught.value.state, State)
    assert runtime.trace.records[-1]["kind"] == "run_failed"
    assert runtime.trace.records[-1]["cause"]["kind"] == "node_failure"


def test_exactly_one_terminal_record_and_nothing_after_it():
    result = Runtime(LINEAR, {"a": _identity, "b": _identity}).run(State.empty("term"))
    kinds = [r["kind"] for r in result.trace.records]
    terminals = [k for k in kinds if k.startswith("run_") and k != "run_started"]
    assert terminals == ["run_completed"]
    assert kinds[-1] == "run_completed"
    assert [r["seq"] for r in result.trace.records] == list(
        range(1, len(result.trace.records) + 1)
    )


def test_strict_route_miss_fails_and_exploration_completes():
    doc = json.loads(json.dumps(ROUTED_DOC))
    doc["name"] = "adv-miss"
    doc["transitions"]["a"] = {"kind": "route", "on": "k", "map": {"one": "x"}}
    doc["nodes"] = [n for n in doc["nodes"] if n["name"] != "y"]
    del doc["transitions"]["y"]
    spec = GraphSpec.from_dict(doc)

    strict = Runtime(spec, {"a": _identity, "x": _identity}).run(State.empty("miss"))
    assert strict.status == "failed"
    assert strict.trace.records[-1]["cause"]["kind"] == "route_miss"

    relaxed = Runtime(
        spec, {"a": _identity, "x": _identity}, policy=Policy.EXPLORATION
    ).run(State.empty("miss"))
    assert relaxed.status == "completed"


def test_declaration_violation_under_strict_prevents_the_commit():
    def node(state: State) -> State:
        return state.with_fact(Fact("undeclared", 1, "s", NOW))

    doc = json.loads(json.dumps(LINEAR_DOC))
    doc["name"] = "adv-strictdecl"
    doc["nodes"][0]["writes_declared"] = ["allowed"]
    spec = GraphSpec.from_dict(doc)
    runtime = Runtime(spec, {"a": node, "b": _identity})
    with pytest.raises(NodeFailed):
        runtime.run(State.empty("decl"))
    transition = next(r for r in runtime.trace.records if r["kind"] == "transition")
    assert transition["error"]["type"] == "DeclarationViolation"
    assert transition["writes"]["facts"] == []


def test_cancellation_between_records_records_the_terminal():
    """Cancelling once the run is under way is honoured and recorded."""
    box: list[Runtime] = []

    class CancelAfterStart:
        def on_record(self, record):
            if record["kind"] == "run_started":
                box[0].cancel("stopped by the operator")

    runtime = Runtime(LINEAR, {"a": _identity, "b": _identity}, listeners=(CancelAfterStart(),))
    box.append(runtime)
    result = runtime.run(State.empty("cancel"))
    assert result.status == "cancelled"
    assert result.trace.records[-1]["reason"] == "stopped by the operator"
    assert result.trace.records[-1]["active_attempt"] is None


def test_stream_close_lands_a_recorded_cancellation_not_an_abandoned_generator():
    driver = Runtime(LINEAR, {"a": _identity, "b": _identity}).stream(State.empty("s"))
    next(driver)
    next(driver)
    driver.close("consumer stopped")
    kinds = [r["kind"] for r in driver.trace.records]
    assert kinds[-1] == "run_cancelled"
    assert driver.trace.records[-1]["active_attempt"] == {"node": "a", "attempt": 1}


# --------------------------------------------------------------------------- #
# E/F — trace integrity, sinks, durability                                     #
# --------------------------------------------------------------------------- #


def _validate_both(trace, spec_doc):
    document = trace.to_dict()
    complete = document["records"][-1]["kind"] in (
        "run_completed",
        "run_failed",
        "run_cancelled",
    )
    json_violations = validate.validate_trace(
        document, spec=spec_doc, expect_complete=complete
    )
    jsonl_violations, _ = validate.validate_jsonl(
        trace.to_jsonl(), spec=spec_doc, expect_complete=complete
    )
    return json_violations, jsonl_violations


def test_runtime_traces_validate_identically_as_json_and_as_jsonl():
    def writer(state: State) -> State:
        return state.with_fact(Fact("x", {"nested": [1, 2.5, True, None, "ü✓"]}, "s", NOW))

    result = Runtime(LINEAR, {"a": writer, "b": _identity}).run(State.empty("conf"))
    json_violations, jsonl_violations = _validate_both(result.trace, LINEAR_DOC)
    assert json_violations == []
    assert jsonl_violations == []


class _RefusingRunSink:
    def __init__(self, predicate):
        self.predicate = predicate
        self.written: list[dict] = []

    def write(self, records):
        for record in records:
            if self.predicate(record):
                raise OSError(f"sink refuses {record['kind']}")
            self.written.append(record)


class _RefusingSink:
    def __init__(self, predicate, open_fails=False):
        self.run_sink = _RefusingRunSink(predicate)
        self.open_fails = open_fails

    def open_run(self, header):
        if self.open_fails:
            raise OSError("sink refuses to open the run")
        return self.run_sink


@pytest.mark.parametrize(
    "label,sink",
    [
        ("open_run", _RefusingSink(lambda r: False, open_fails=True)),
        ("first record", _RefusingSink(lambda r: r["kind"] == "run_started")),
        ("mid trace", _RefusingSink(lambda r: r["kind"] == "routing")),
        ("terminal", _RefusingSink(lambda r: r["kind"] == "run_completed")),
    ],
)
def test_required_sink_failure_prevents_logical_success(label, sink):
    """Invariant II, plus: the resulting evidence is itself contract-valid."""
    runtime = Runtime(
        LINEAR,
        {"a": _identity, "b": _identity},
        durability_profile=DurabilityProfile.SYNCHRONOUS,
        sink=sink,
    )
    with pytest.raises(SinkFailed):
        runtime.run(State.empty("sink"))
    kinds = [r["kind"] for r in runtime.trace.records]
    assert kinds[-1] == "run_failed"
    assert "run_completed" not in kinds
    assert runtime.trace.records[-1]["cause"]["kind"] == "sink_failure"
    json_violations, jsonl_violations = _validate_both(runtime.trace, LINEAR_DOC)
    assert json_violations == []
    assert jsonl_violations == []


def test_buffered_profile_flushes_failure_evidence_outside_the_loss_window():
    batches: list[list[str]] = []

    class RunSink:
        def write(self, records):
            batches.append([r["kind"] for r in records])

    class Sink:
        def open_run(self, header):
            return RunSink()

    def boom(state: State) -> State:
        raise RuntimeError("down")

    runtime = Runtime(
        LINEAR,
        {"a": boom, "b": _identity},
        durability_profile=DurabilityProfile.BUFFERED,
        sink=Sink(),
        chunk_records=1000,
        flush_interval_ms=600000,
    )
    with pytest.raises(NodeFailed):
        runtime.run(State.empty("buffered"))
    flushed = [kind for batch in batches for kind in batch]
    assert "transition" in flushed
    assert "run_failed" in flushed
    assert runtime.trace.run["sink"] == {
        "flush_interval_ms": 600000,
        "chunk_records": 1000,
    }


def test_a_sink_cannot_mutate_the_trace_or_the_run():
    class RunSink:
        def write(self, records):
            for record in records:
                record["kind"] = "FORGED"
                record["seq"] = 999

    class Sink:
        def open_run(self, header):
            header["run_id"] = "HIJACKED"
            return RunSink()

    result = Runtime(
        LINEAR,
        {"a": _identity, "b": _identity},
        durability_profile=DurabilityProfile.SYNCHRONOUS,
        sink=Sink(),
    ).run(State.empty("mutate"), run_id="honest-run")
    assert result.trace.run["run_id"] == "honest-run"
    assert "FORGED" not in [r["kind"] for r in result.trace.records]


def test_a_listener_exception_never_reaches_the_caller():
    class Exploding:
        def on_record(self, record):
            raise SystemExit("listener detonates")

    result = Runtime(
        LINEAR, {"a": _identity, "b": _identity}, listeners=(Exploding(),)
    ).run(State.empty("listener"))
    assert result.status == "completed"


def test_trace_records_and_run_header_are_isolated_from_callers():
    result = Runtime(LINEAR, {"a": _identity, "b": _identity}).run(State.empty("iso"))
    header = result.trace.run
    header["run_id"] = "TAMPERED"
    records = result.trace.records
    records[0]["kind"] = "TAMPERED"
    assert result.trace.run["run_id"] != "TAMPERED"
    assert result.trace.records[0]["kind"] == "run_started"


def test_trace_from_dict_refuses_seq_gaps_and_records_after_a_terminal():
    result = Runtime(LINEAR, {"a": _identity, "b": _identity}).run(State.empty("t"))
    gapped = result.trace.to_dict()
    gapped["records"][2]["seq"] = 99
    with pytest.raises(ValueError):
        Trace.from_dict(gapped)

    trailing = result.trace.to_dict()
    trailing["records"].append(dict(trailing["records"][-1], seq=len(trailing["records"]) + 1))
    with pytest.raises(ValueError):
        Trace.from_dict(trailing)


def test_jsonl_stream_rejects_cr_bytes_and_reports_truncation():
    result = Runtime(LINEAR, {"a": _identity, "b": _identity}).run(State.empty("t"))
    stream = result.trace.to_jsonl()

    crlf, _ = validate.validate_jsonl(stream.replace("\n", "\r\n"), spec=LINEAR_DOC)
    assert [v.rule for v in crlf] == ["JSONL3"]

    truncated = stream[: -len(stream.splitlines()[-1]) - 5]
    cut, _ = validate.validate_jsonl(truncated, spec=LINEAR_DOC)
    assert [v.rule for v in cut] == ["T3/INCOMPLETE"]


# --------------------------------------------------------------------------- #
# A — GraphSpec validation and fingerprinting                                  #
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "mutate,rule",
    [
        (lambda d: d.update(entry="ghost"), "R1"),
        (lambda d: d["transitions"]["a"].update(to="ghost"), "R2"),
        (lambda d: d["nodes"].append({"name": "orphan"}), "R4"),
        (lambda d: d["nodes"].append({"name": "a", "effect_class": "pure"}), "R5"),
        (lambda d: d["nodes"].append({"name": "END"}), "R4"),
        (lambda d: d.update(requires_motus=">=1.0,"), "R12"),
    ],
)
def test_invalid_graphs_refuse_to_exist(mutate, rule):
    doc = json.loads(json.dumps(LINEAR_DOC))
    mutate(doc)
    with pytest.raises(GraphSpecValidationError) as caught:
        GraphSpec.from_dict(doc)
    assert rule in caught.value.rules


def test_a_cyclic_spec_without_max_transitions_is_refused():
    doc = {
        "schema_version": "1.0.0",
        "name": "adv-cyclic",
        "version": "1.0.0",
        "entry": "a",
        "nodes": [
            {"name": "a", "effect_class": "pure"},
            {"name": "b", "effect_class": "pure"},
            {"name": "z", "effect_class": "pure"},
        ],
        "transitions": {
            "a": {"kind": "route", "on": "k", "map": {"loop": "b", "stop": "z"}},
            "b": {"kind": "next", "to": "a"},
            "z": {"kind": "terminal"},
        },
    }
    with pytest.raises(GraphSpecValidationError) as caught:
        GraphSpec.from_dict(doc)
    assert "R11" in caught.value.rules


def test_fingerprint_is_immune_to_mutation_of_the_source_document():
    doc = json.loads(json.dumps(LINEAR_DOC))
    spec = GraphSpec.from_dict(doc)
    before = spec.graph_fingerprint
    doc["entry"] = "b"
    doc["nodes"].append({"name": "ghost"})
    assert spec.graph_fingerprint == before
    assert spec.entry == "a"


@pytest.mark.parametrize(
    "mutate",
    [
        lambda d: d.update(version="1.0.1"),
        lambda d: d.update(max_transitions=5),
        lambda d: d["nodes"][0].update(effect_class="external_effect"),
        lambda d: d["nodes"][0].update(reads_declared=[]),
        lambda d: d["nodes"][0].pop("effect_class"),
    ],
)
def test_every_execution_relevant_change_moves_the_graph_fingerprint(mutate):
    base = GraphSpec.from_dict(json.loads(json.dumps(LINEAR_DOC))).graph_fingerprint
    doc = json.loads(json.dumps(LINEAR_DOC))
    mutate(doc)
    assert GraphSpec.from_dict(doc).graph_fingerprint != base


def test_node_registry_must_match_the_declared_topology():
    with pytest.raises(ValueError):
        Runtime(LINEAR, {"a": _identity})
    with pytest.raises(ValueError):
        Runtime(LINEAR, {"a": _identity, "b": _identity, "c": _identity})


@pytest.mark.parametrize(
    "node",
    [
        lambda: (lambda: None),
        lambda: (lambda state, ctx, extra: state),
        lambda: (lambda *args: args[0]),
    ],
)
def test_nodes_outside_the_declared_shapes_are_refused_at_construction(node):
    with pytest.raises(TypeError):
        Runtime(
            GraphSpec.from_dict(
                {
                    "schema_version": "1.0.0",
                    "name": "adv-shape",
                    "version": "1.0.0",
                    "entry": "a",
                    "nodes": [{"name": "a", "effect_class": "pure"}],
                    "transitions": {"a": {"kind": "terminal"}},
                }
            ),
            {"a": node()},
        )


# --------------------------------------------------------------------------- #
# G — effects                                                                  #
# --------------------------------------------------------------------------- #


def test_a_pure_node_cannot_record_an_effect():
    def sneaky(state, ctx):
        ctx.record_effect(EffectDescriptor(EffectClass.RECORDED_EFFECT, "sneak"))
        return state

    runtime = Runtime(LINEAR, {"a": sneaky, "b": _identity})
    with pytest.raises(NodeFailed):
        runtime.run(State.empty("pure"))
    transition = next(r for r in runtime.trace.records if r["kind"] == "transition")
    assert transition["outcome"] == "raised"
    assert transition["effects"] == []


def test_a_recorded_effect_node_cannot_record_an_external_effect():
    doc = json.loads(json.dumps(LINEAR_DOC))
    doc["name"] = "adv-recorded"
    doc["nodes"][0]["effect_class"] = "recorded_effect"
    spec = GraphSpec.from_dict(doc)

    def node(state, ctx):
        ctx.record_effect(EffectDescriptor(EffectClass.EXTERNAL_EFFECT, "POST"))
        return state

    runtime = Runtime(spec, {"a": node, "b": _identity})
    with pytest.raises(NodeFailed):
        runtime.run(State.empty("recorded"))


def test_each_retry_records_only_its_own_effects():
    doc = json.loads(json.dumps(LINEAR_DOC))
    doc["name"] = "adv-effect-retry"
    doc["nodes"][0]["effect_class"] = "external_effect"
    spec = GraphSpec.from_dict(doc)
    seen = [0]

    def node(state, ctx):
        seen[0] += 1
        ctx.record_effect(
            EffectDescriptor(
                EffectClass.EXTERNAL_EFFECT,
                f"POST #{seen[0]}",
                idempotency_key="k",
                receipt=EffectReceipt("r", "completed"),
            )
        )
        if seen[0] < 3:
            raise RuntimeError("transient")
        return state

    result = Runtime(
        spec, {"a": node, "b": _identity}, max_attempts={"a": 3, "b": 1}
    ).run(State.empty("effects"))
    per_attempt = [
        [e["description"] for e in r["effects"]]
        for r in result.trace.records
        if r["kind"] == "transition" and r["node"] == "a"
    ]
    assert per_attempt == [["POST #1"], ["POST #2"], ["POST #3"]]


# --------------------------------------------------------------------------- #
# H — replay and resume                                                        #
# --------------------------------------------------------------------------- #


def test_a_bundle_refuses_a_trace_from_a_different_graph():
    doc = json.loads(json.dumps(LINEAR_DOC))
    doc["version"] = "9.9.9"
    other = GraphSpec.from_dict(doc)
    result = Runtime(LINEAR, {"a": _identity, "b": _identity}).run(State.empty("h"))
    with pytest.raises(ValueError):
        TraceBundle(other, result.trace)


def test_a_forged_route_target_is_refused_by_recomputation():
    def decide(state: State) -> State:
        return state.with_decision(Decision("k", "one", NOW))

    result = Runtime(ROUTED, {"a": decide, "x": _identity, "y": _identity}).run(
        State.empty("forge")
    )
    document = result.trace.to_dict()
    for record in document["records"]:
        if record["kind"] == "routing":
            record["selected"] = "y"
            for candidate in record["candidates"]:
                candidate["taken"] = candidate["target"] == "y"
    with pytest.raises(ValueError):
        TraceBundle(ROUTED, Trace.from_dict(document))


def test_a_forged_committed_decision_is_refused_by_recomputation():
    def decide(state: State) -> State:
        return state.with_decision(Decision("k", "one", NOW))

    result = Runtime(ROUTED, {"a": decide, "x": _identity, "y": _identity}).run(
        State.empty("forge")
    )
    document = result.trace.to_dict()
    for record in document["records"]:
        if record["kind"] == "transition" and record["writes"]["decisions"]:
            record["writes"]["decisions"][0]["value"] = "two"
    with pytest.raises(ValueError):
        TraceBundle(ROUTED, Trace.from_dict(document))


def test_a_terminal_trace_cannot_be_resumed():
    result = Runtime(LINEAR, {"a": _identity, "b": _identity}).run(State.empty("h"))
    engine = ReplayEngine(TraceBundle(LINEAR, result.trace))
    with pytest.raises(UnsafeResume):
        engine.resume(Runtime(LINEAR, {"a": _identity, "b": _identity}))


def test_resume_produces_a_new_causally_linked_run_id():
    def writer(state: State) -> State:
        return state.with_fact(Fact("x", 1, "s", NOW))

    driver = Runtime(LINEAR, {"a": writer, "b": _identity}).stream(State.empty("h"))
    for _ in range(3):
        next(driver)
    source = driver.trace
    driver.close()

    engine = ReplayEngine(TraceBundle(LINEAR, source))
    resumed = engine.resume(Runtime(LINEAR, {"a": writer, "b": _identity}))
    assert resumed.trace.run["run_id"] != source.run["run_id"]
    assert resumed.trace.run["resume"]["source_run_id"] == source.run["run_id"]
    assert resumed.trace.run["resume"]["start_node"] == "b"
    assert resumed.trace.run["metadata"]["causation_id"] == source.run["run_id"]
    assert resumed.state.fact("x") == 1


def test_resume_refuses_to_reuse_the_source_run_id():
    def writer(state: State) -> State:
        return state.with_fact(Fact("x", 1, "s", NOW))

    driver = Runtime(LINEAR, {"a": writer, "b": _identity}).stream(State.empty("h"))
    for _ in range(3):
        next(driver)
    source = driver.trace
    driver.close()
    engine = ReplayEngine(TraceBundle(LINEAR, source))
    with pytest.raises(UnsafeResume):
        engine.resume(
            Runtime(LINEAR, {"a": writer, "b": _identity}),
            run_id=source.run["run_id"],
        )


@pytest.mark.parametrize(
    "key,status,safe",
    [(None, "completed", False), ("k", "unknown", False), ("k", "completed", True)],
)
def test_external_effect_resume_fails_closed_without_key_and_receipt(key, status, safe):
    doc = json.loads(json.dumps(LINEAR_DOC))
    doc["name"] = "adv-external"
    doc["nodes"][0]["effect_class"] = "external_effect"
    spec = GraphSpec.from_dict(doc)

    def node(state, ctx):
        ctx.record_effect(
            EffectDescriptor(
                EffectClass.EXTERNAL_EFFECT,
                "POST",
                idempotency_key=key,
                receipt=EffectReceipt("r", status),
            )
        )
        return state

    driver = Runtime(spec, {"a": node, "b": _identity}).stream(State.empty("h"))
    for _ in range(3):
        next(driver)
    source = driver.trace
    driver.close()
    engine = ReplayEngine(TraceBundle(spec, source))
    if safe:
        engine._assert_effect_safe()
    else:
        with pytest.raises(UnsafeResume):
            engine._assert_effect_safe()


def test_verify_falsifies_a_node_that_only_claims_to_be_pure():
    counter = [0]

    def drifting(state: State) -> State:
        counter[0] += 1
        return state.with_fact(Fact("n", counter[0], "s", NOW))

    result = Runtime(LINEAR, {"a": drifting, "b": _identity}).run(State.empty("h"))
    engine = ReplayEngine(TraceBundle(LINEAR, result.trace))
    with pytest.raises(ReplayMismatch):
        engine.verify({"a": drifting, "b": _identity})


def test_replay_capability_only_ever_degrades():
    class Opaque:
        def __call__(self, state):
            return state

    result = Runtime(LINEAR, {"a": Opaque(), "b": _identity}).run(
        State.empty("h"), replay=ReplayStatus.declared("full")
    )
    assert result.trace.run["replay"] == {"capability": "full", "constraints": []}
    terminal = result.trace.records[-1]["replay"]
    assert terminal["capability"] == "partial"
    assert terminal["constraints"] == ["node:a:opaque_config"]


def test_playback_reconstructs_committed_state_without_running_nodes():
    ran = []

    def writer(state: State) -> State:
        ran.append("a")
        return state.with_fact(Fact("x", 41, "s", NOW))

    result = Runtime(LINEAR, {"a": writer, "b": _identity}).run(State.empty("h"))
    ran.clear()
    restored = ReplayEngine(TraceBundle(LINEAR, result.trace)).playback()
    assert restored.state.fact("x") == 41
    assert ran == []


# --------------------------------------------------------------------------- #
# J/L — packaging and viewer                                                   #
# --------------------------------------------------------------------------- #


def test_the_standalone_viewer_escapes_hostile_content_and_loads_no_remote_asset():
    hostile = '</title><script>alert(1)</script>"onload="x'

    def node(state: State) -> State:
        return state.with_fact(Fact("k", "<img src=x onerror=alert(2)>", "s", NOW))

    result = Runtime(LINEAR, {"a": node, "b": _identity}).run(
        State.empty("<b>intent</b>"), run_id=hostile
    )
    page = TraceBundle(LINEAR, result.trace).to_html()
    assert "<script>" not in page
    assert "onerror=" not in page
    assert "&lt;script&gt;" in page
    assert "http://" not in page and "https://" not in page


def test_the_native_namespace_never_exposes_the_legacy_decision():
    import vitruvyan_motus
    import vitruvyan_motus.compat as compat

    assert vitruvyan_motus.Decision is not compat.LegacyDecision
    assert "Decision" not in compat.__all__
    assert not hasattr(vitruvyan_motus, "LegacyDecision")
    assert all(hasattr(vitruvyan_motus, name) for name in vitruvyan_motus.__all__)


def test_the_package_imports_no_third_party_or_predecessor_module():
    """Invariant III's import fence, measured in a pristine interpreter — this
    test process has already imported the jsonschema-backed validator, so the
    check has to happen somewhere that has not."""
    import subprocess

    program = (
        "import importlib, pkgutil, sys, vitruvyan_motus\n"
        "for m in pkgutil.iter_modules(vitruvyan_motus.__path__):\n"
        "    importlib.import_module('vitruvyan_motus.' + m.name)\n"
        "banned = {'axis','jsonschema','langchain','openai','anthropic',"
        "'requests','sqlalchemy','pydantic','numpy'}\n"
        "print(sorted(banned & {n.split('.')[0] for n in sys.modules}))\n"
    )
    out = subprocess.run(
        [sys.executable, "-c", program], capture_output=True, text=True, check=True
    )
    assert out.stdout.strip() == "[]"


def test_the_distribution_version_and_the_schema_version_stay_distinct():
    import vitruvyan_motus

    schema = json.loads((ROOT / "contract" / "trace.v1.schema.json").read_text("utf-8"))
    assert vitruvyan_motus.TRACE_SCHEMA_VERSION == schema["x-current-version"]
    assert vitruvyan_motus.TRACE_SCHEMA_VERSION in schema["properties"]["schema_version"]["enum"]
    assert vitruvyan_motus.__version__ != vitruvyan_motus.TRACE_SCHEMA_VERSION
