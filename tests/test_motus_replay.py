from __future__ import annotations

from datetime import datetime, timezone
import json

import pytest

from contract import validate
from vitruvyan_motus import (
    Decision,
    EffectClass,
    EffectDescriptor,
    EffectReceipt,
    Fact,
    GraphSpec,
    NodeFailed,
    Policy,
    ReplayEngine,
    ReplayMismatch,
    ReplayStatus,
    Runtime,
    State,
    Trace,
    TraceBundle,
    UnsafeResume,
)

NOW = datetime(2026, 8, 4, 12, 0, tzinfo=timezone.utc)


def spec(nodes, transitions, *, entry=None):
    return GraphSpec.from_dict({
        "schema_version": "1.0.0",
        "name": "replay-test",
        "version": "1.0.0",
        "entry": entry or nodes[0]["name"],
        "nodes": nodes,
        "transitions": transitions,
    })


def test_graph_compilation_is_data_only_and_semantically_identical():
    graph = spec(
        [{"name": "a", "effect_class": "pure"}, {"name": "b"}],
        {"a": {"kind": "next", "to": "b"}, "b": {"kind": "terminal"}},
    )
    assert graph.compiled.entry == graph.entry
    assert graph.compiled.graph_fingerprint == graph.graph_fingerprint
    assert dict(graph.compiled.transitions) == dict(graph.transitions)
    assert graph.compiled.declarations["a"] == graph.nodes[0]


def test_effect_receipt_is_captured_by_the_attempt():
    receipt = EffectReceipt("provider:42", result_fingerprint="effect:sha256:" + "a" * 64)

    def node(state, ctx):
        ctx.record_effect(EffectDescriptor(
            EffectClass.RECORDED_EFFECT, "GET map tile", receipt=receipt
        ))
        return state.with_fact(Fact("tile", "ok", "http", NOW))

    graph = spec(
        [{"name": "fetch", "effect_class": "recorded_effect", "writes_declared": ["tile"]}],
        {"fetch": {"kind": "terminal"}},
    )
    result = Runtime(graph, {"fetch": node}).run(replay=ReplayStatus.declared("full"))
    transition = next(r for r in result.trace.records if r["kind"] == "transition")
    assert transition["effects"][0]["receipt"] == receipt.to_dict()
    assert validate.validate_trace(result.trace.to_dict(), graph.to_dict()) == []


def test_effect_lattice_remains_valid_when_node_also_raises():
    def invalid(state, ctx):
        ctx.record_effect(EffectDescriptor(EffectClass.RECORDED_EFFECT, "not pure"))
        raise RuntimeError("after effect")

    graph = spec(
        [{"name": "invalid", "effect_class": "pure"}],
        {"invalid": {"kind": "terminal"}},
    )
    with pytest.raises(NodeFailed) as caught:
        Runtime(graph, {"invalid": invalid}).run()
    transition = next(r for r in caught.value.trace.records if r["kind"] == "transition")
    assert transition["effects"] == []
    assert validate.validate_trace(caught.value.trace.to_dict(), graph.to_dict()) == []


def test_playback_executes_no_node_code_and_reconstructs_commits():
    calls = []

    def node(state):
        calls.append("run")
        return state.with_fact(Fact("answer", 42, "pure", NOW))

    graph = spec(
        [{"name": "node", "effect_class": "pure", "writes_declared": ["answer"]}],
        {"node": {"kind": "terminal"}},
    )
    result = Runtime(graph, {"node": node}).run(replay=ReplayStatus.declared("full"))
    bundle = TraceBundle(graph, result.trace)
    calls.clear()
    replayed = ReplayEngine(bundle).playback()
    assert calls == []
    assert replayed.state.fact("answer") == 42
    assert TraceBundle.from_json(bundle.to_json()).fingerprint == bundle.fingerprint


def test_trace_log_view_cannot_mutate_bundle_or_playback_evidence():
    graph = spec(
        [{"name": "node", "effect_class": "pure", "writes_declared": ["answer"]}],
        {"node": {"kind": "terminal"}},
    )
    result = Runtime(graph, {"node": lambda state: state.with_fact(Fact("answer", 42, "test", NOW))}).run()
    bundle = TraceBundle(graph, result.trace)
    before = bundle.fingerprint
    result.trace.log[2]["writes"]["facts"][0]["value"] = 999
    assert bundle.fingerprint == before
    assert ReplayEngine(bundle).playback().state.fact("answer") == 42


def test_imported_bundle_rejects_a_semantically_impossible_node():
    graph = spec(
        [{"name": "node", "effect_class": "pure"}],
        {"node": {"kind": "terminal"}},
    )
    result = Runtime(graph, {"node": lambda state: state}).run()
    document = TraceBundle(graph, result.trace).to_dict()
    document["trace"]["records"][2]["node"] = "ghost"
    with pytest.raises(ValueError, match="undeclared node"):
        TraceBundle.from_dict(document)


def test_verify_reexecutes_only_pure_nodes_and_detects_divergence():
    def pure(state, ctx):
        return state.with_fact(Fact("draw", ctx.rand(), "ctx", NOW))

    graph = spec(
        [{"name": "pure", "effect_class": "pure", "writes_declared": ["draw"]}],
        {"pure": {"kind": "terminal"}},
    )
    result = Runtime(graph, {"pure": pure}, random_source=lambda: 0.25).run(
        replay=ReplayStatus.declared("full")
    )
    engine = ReplayEngine(TraceBundle(graph, result.trace))
    verified = engine.verify({"pure": pure})
    assert verified.verified == (("pure", 3),)

    def changed(state, ctx):
        return state.with_fact(Fact("draw", ctx.rand() + 1, "ctx", NOW))

    with pytest.raises(ReplayMismatch, match="writes"):
        engine.verify({"pure": changed})


def test_verify_reexecutes_a_recorded_pure_failure():
    def fails(state):
        state.fact("input")
        raise LookupError("expected")

    graph = spec(
        [{"name": "fails", "effect_class": "pure", "reads_declared": ["input"]}],
        {"fails": {"kind": "terminal"}},
    )
    with pytest.raises(NodeFailed) as caught:
        Runtime(graph, {"fails": fails}).run(State.new(facts=[Fact("input", 1, "test", NOW)]))
    verified = ReplayEngine(TraceBundle(graph, caught.value.trace)).verify({"fails": fails})
    assert verified.verified == (("fails", 3),)


def test_strict_declaration_enforcement_prevents_commit():
    def node(state):
        return state.with_fact(Fact("undeclared", True, "test", NOW))

    graph = spec(
        [{"name": "node", "effect_class": "pure", "writes_declared": []}],
        {"node": {"kind": "terminal"}},
    )
    with pytest.raises(NodeFailed) as caught:
        Runtime(graph, {"node": node}).run()
    transition = next(r for r in caught.value.trace.records if r["kind"] == "transition")
    assert transition["writes"] == {"facts": [], "decisions": [], "rejections": []}
    assert transition["violations"] == [{"kind": "undeclared_write", "key": "undeclared"}]
    assert validate.validate_trace(
        caught.value.trace.to_dict(), graph.to_dict()
    ) == []


def test_verify_fails_closed_when_attempted_write_values_were_discarded():
    def node(state):
        return state.with_fact(Fact("undeclared", 1, "test", NOW))

    graph = spec(
        [{"name": "node", "effect_class": "pure", "writes_declared": []}],
        {"node": {"kind": "terminal"}},
    )
    with pytest.raises(NodeFailed) as caught:
        Runtime(graph, {"node": node}).run()
    with pytest.raises(ReplayMismatch, match="attempted_writes_not_recorded"):
        ReplayEngine(TraceBundle(graph, caught.value.trace)).verify({"node": node})


def test_resume_creates_a_linked_segment_from_last_committed_state():
    def first(state):
        return state.with_fact(Fact("first", True, "test", NOW))

    def second(state):
        assert state.fact("first") is True
        return state.with_fact(Fact("second", True, "test", NOW))

    graph = spec(
        [
            {"name": "first", "effect_class": "pure", "writes_declared": ["first"]},
            {"name": "second", "effect_class": "pure", "reads_declared": ["first"], "writes_declared": ["second"]},
        ],
        {"first": {"kind": "next", "to": "second"}, "second": {"kind": "terminal"}},
    )
    complete = Runtime(graph, {"first": first, "second": second}).run(run_id="source")
    records = list(complete.trace.records)
    cut = next(i for i, r in enumerate(records) if r["kind"] == "routing" and r["after"] == "first")
    incomplete_doc = complete.trace.to_dict()
    incomplete_doc["records"] = records[: cut + 1]
    incomplete = Trace.from_dict(incomplete_doc)
    result = ReplayEngine(TraceBundle(graph, incomplete)).resume(
        Runtime(graph, {"first": first, "second": second}), run_id="resumed"
    )
    assert result.state.fact("second") is True
    assert result.trace.run["resume"]["source_run_id"] == "source"
    assert result.trace.run["resume"]["start_node"] == "second"
    assert validate.validate_trace(result.trace.to_dict(), graph.to_dict()) == []


def test_resume_rejects_a_persisted_route_redirected_to_another_valid_node():
    def choose(state):
        return state.with_decision(Decision("branch", "safe", NOW))

    graph = spec(
        [
            {"name": "choose", "effect_class": "pure"},
            {"name": "safe", "effect_class": "pure"},
            {"name": "external", "effect_class": "external_effect"},
        ],
        {
            "choose": {
                "kind": "route",
                "on": "branch",
                "map": {"safe": "safe", "external": "external"},
            },
            "safe": {"kind": "terminal"},
            "external": {"kind": "terminal"},
        },
    )
    complete = Runtime(
        graph,
        {"choose": choose, "safe": lambda state: state, "external": lambda state: state},
    ).run(run_id="route-source")
    document = TraceBundle(graph, complete.trace).to_dict()
    route_index = next(
        i
        for i, record in enumerate(document["trace"]["records"])
        if record["kind"] == "routing" and record["after"] == "choose"
    )
    document["trace"]["records"] = document["trace"]["records"][: route_index + 1]
    route = document["trace"]["records"][-1]
    route["selected"] = "external"
    for candidate in route["candidates"]:
        candidate["taken"] = candidate["target"] == "external"

    with pytest.raises(ValueError, match="routing semantics disagree"):
        TraceBundle.from_dict(document)


def test_resume_after_exploration_continue_advances_to_the_next_node():
    calls = []

    def first(state):
        calls.append("first")
        raise RuntimeError("continue")

    def second(state):
        calls.append("second")
        return state

    graph = spec(
        [{"name": "first", "effect_class": "pure"},
         {"name": "second", "effect_class": "pure"}],
        {"first": {"kind": "next", "to": "second"},
         "second": {"kind": "terminal"}},
    )
    complete = Runtime(
        graph, {"first": first, "second": second}, policy=Policy.EXPLORATION
    ).run(run_id="continue-source")
    document = complete.trace.to_dict()
    transition_index = next(
        i for i, record in enumerate(document["records"])
        if record.get("disposition") == "continue"
    )
    document["records"] = document["records"][: transition_index + 1]
    calls.clear()
    resumed = ReplayEngine(TraceBundle(graph, Trace.from_dict(document))).resume(
        Runtime(graph, {"first": first, "second": second}, policy=Policy.EXPLORATION),
        run_id="continue-resumed",
    )
    assert calls == ["second"]
    assert resumed.trace.run["resume"]["start_node"] == "second"


def test_resume_refuses_to_reuse_the_source_run_id():
    graph = spec(
        [{"name": "node", "effect_class": "pure"}],
        {"node": {"kind": "terminal"}},
    )
    complete = Runtime(graph, {"node": lambda state: state}).run(run_id="same")
    document = complete.trace.to_dict()
    document["records"] = document["records"][:2]
    engine = ReplayEngine(TraceBundle(graph, Trace.from_dict(document)))
    with pytest.raises(UnsafeResume, match="new run_id"):
        engine.resume(Runtime(graph, {"node": lambda state: state}), run_id="same")
    with pytest.raises(UnsafeResume, match="new run_id"):
        engine.resume(Runtime(
            graph, {"node": lambda state: state}, identity=lambda: "same"
        ))


def test_resume_refuses_external_effect_without_completed_idempotent_receipt():
    def external(state, ctx):
        ctx.record_effect(EffectDescriptor(EffectClass.EXTERNAL_EFFECT, "POST charge"))
        return state.with_decision(Decision("next", "go", NOW))

    graph = spec(
        [{"name": "external", "effect_class": "external_effect"}],
        {"external": {"kind": "terminal"}},
    )
    complete = Runtime(graph, {"external": external}).run(run_id="external")
    doc = complete.trace.to_dict()
    doc["records"] = doc["records"][:-1]
    with pytest.raises(UnsafeResume, match="idempotency"):
        ReplayEngine(TraceBundle(graph, Trace.from_dict(doc))).resume(
            Runtime(graph, {"external": external})
        )


def test_resume_refuses_an_in_flight_external_attempt_with_unknown_outcome():
    def external(state):
        return state

    graph = spec(
        [{"name": "external", "effect_class": "external_effect"}],
        {"external": {"kind": "terminal"}},
    )
    complete = Runtime(graph, {"external": external}).run(run_id="unknown")
    doc = complete.trace.to_dict()
    doc["records"] = doc["records"][:2]
    with pytest.raises(UnsafeResume, match="unknown outcome"):
        ReplayEngine(TraceBundle(graph, Trace.from_dict(doc))).resume(
            Runtime(graph, {"external": external})
        )


def test_resume_refuses_external_node_with_only_recorded_effect_evidence():
    def external(state, ctx):
        ctx.record_effect(EffectDescriptor(
            EffectClass.RECORDED_EFFECT, "GET status",
            receipt=EffectReceipt("read:42"),
        ))
        return state

    graph = spec(
        [{"name": "external", "effect_class": "external_effect"}],
        {"external": {"kind": "terminal"}},
    )
    complete = Runtime(graph, {"external": external}).run(run_id="external-read")
    document = complete.trace.to_dict()
    document["records"] = document["records"][:-1]
    with pytest.raises(UnsafeResume, match="external-effect receipt"):
        ReplayEngine(TraceBundle(graph, Trace.from_dict(document))).resume(
            Runtime(graph, {"external": external})
        )


def test_bundle_rejects_effect_class_that_disagrees_with_graphspec():
    graph = spec(
        [{"name": "node", "effect_class": "external_effect"}],
        {"node": {"kind": "terminal"}},
    )
    result = Runtime(graph, {"node": lambda state: state}).run()
    document = TraceBundle(graph, result.trace).to_dict()
    document["trace"]["records"][2]["effect_class"] = "recorded_effect"
    with pytest.raises(ValueError, match="effect_class disagrees"):
        TraceBundle.from_dict(document)


def test_resume_accepts_completed_idempotent_external_effect():
    def external(state, ctx):
        ctx.record_effect(EffectDescriptor(
            EffectClass.EXTERNAL_EFFECT,
            "POST charge",
            idempotency_key="charge:42",
            receipt=EffectReceipt("charge-provider:42"),
        ))
        return state.with_fact(Fact("charged", True, "provider", NOW))

    def finish(state):
        assert state.fact("charged") is True
        return state

    graph = spec(
        [
            {"name": "external", "effect_class": "external_effect", "writes_declared": ["charged"]},
            {"name": "finish", "effect_class": "pure", "reads_declared": ["charged"]},
        ],
        {"external": {"kind": "next", "to": "finish"}, "finish": {"kind": "terminal"}},
    )
    complete = Runtime(graph, {"external": external, "finish": finish}).run(run_id="paid")
    doc = complete.trace.to_dict()
    route_index = next(
        i for i, record in enumerate(doc["records"])
        if record["kind"] == "routing" and record["after"] == "external"
    )
    doc["records"] = doc["records"][: route_index + 1]
    resumed = ReplayEngine(TraceBundle(graph, Trace.from_dict(doc))).resume(
        Runtime(graph, {"external": external, "finish": finish}), run_id="paid-resume"
    )
    assert resumed.succeeded
    assert resumed.trace.run["resume"]["start_node"] == "finish"


def test_bundle_explanation_and_viewer_are_deterministic_and_offline():
    graph = spec([{"name": "done", "effect_class": "pure"}], {"done": {"kind": "terminal"}})
    result = Runtime(graph, {"done": lambda state: state}).run(run_id="viewer")
    bundle = TraceBundle(graph, result.trace)
    assert bundle.explain() == bundle.explain()
    viewer = bundle.to_html()
    assert "<!doctype html>" in viewer
    assert bundle.fingerprint in viewer
    assert "http://" not in viewer and "https://" not in viewer


def test_trace_schema_1_0_rejects_1_1_receipts_in_json_and_jsonl():
    def node(state, ctx):
        ctx.record_effect(EffectDescriptor(
            EffectClass.RECORDED_EFFECT, "GET",
            receipt=EffectReceipt("read:1"),
        ))
        return state

    graph = spec(
        [{"name": "node", "effect_class": "recorded_effect"}],
        {"node": {"kind": "terminal"}},
    )
    document = Runtime(graph, {"node": node}).run().trace.to_dict()
    document["schema_version"] = "1.0.0"
    assert {v.rule for v in validate.validate_trace(document)} == {"SCHEMA"}
    lines = [
        json.dumps({"schema_version": document["schema_version"], "run": document["run"]}),
        *(json.dumps(record) for record in document["records"]),
    ]
    violations, _ = validate.validate_jsonl("\n".join(lines) + "\n")
    assert {v.rule for v in violations} == {"SCHEMA"}


def test_trace_schema_1_0_rejects_resume_and_wrong_bundle_namespace():
    graph = spec(
        [{"name": "node", "effect_class": "pure"}],
        {"node": {"kind": "terminal"}},
    )
    document = Runtime(graph, {"node": lambda state: state}).run().trace.to_dict()
    document["run"]["resume"] = {
        "source_run_id": "source",
        "source_seq": 2,
        "start_node": "node",
        "bundle_fingerprint": "bundle:sha256:" + "a" * 64,
    }
    document["schema_version"] = "1.0.0"
    assert {v.rule for v in validate.validate_trace(document)} == {"SCHEMA"}
    document["schema_version"] = "1.1.0"
    document["run"]["resume"]["bundle_fingerprint"] = graph.graph_fingerprint
    assert {v.rule for v in validate.validate_trace(document)} == {"SCHEMA"}


def test_result_fingerprint_is_declared_unverifiable_and_this_pins_it():
    """TRIPWIRE for ADR-013. Delete this test only when you close the gap.

    `result_fingerprint` has a format in trace.v1.schema.json and no recipe
    anywhere: nothing says what it is a fingerprint *of*, and `validate.py`
    never recomputes it. So two producers with the same effect result can stamp
    different fingerprints, which is the one thing a fingerprint exists to make
    impossible. The first external integrator found this by having to invent
    the fingerprint's meaning in their own adapter.

    ADR-013 declares the field unverifiable for now — honestly, as `receipt_id`
    already is — rather than inventing a recipe with one consumer in the world.
    The risk of that choice is doing it half-way: a recipe added to the code
    without the contract following, or the reverse. This test is the machine
    enforcement against that. It asserts the field is UNCHECKED today. The day
    someone gives it a recipe, `validate.py` will reject the tamper below, this
    test will fail, and the failure message points here — forcing ADR-013 to be
    superseded rather than silently contradicted.
    """
    receipt = EffectReceipt("provider:42", result_fingerprint="effect:sha256:" + "a" * 64)

    def node(state, ctx):
        ctx.record_effect(EffectDescriptor(
            EffectClass.RECORDED_EFFECT, "GET map tile", receipt=receipt
        ))
        return state.with_fact(Fact("tile", "ok", "http", NOW))

    graph = spec(
        [{"name": "fetch", "effect_class": "recorded_effect", "writes_declared": ["tile"]}],
        {"fetch": {"kind": "terminal"}},
    )
    result = Runtime(graph, {"fetch": node}).run(replay=ReplayStatus.declared("full"))

    document = json.loads(json.dumps(result.trace.to_dict()))
    for record in document["records"]:
        if record["kind"] == "transition":
            # Same format, a different value — the seal now describes nothing
            # that produced this run.
            record["effects"][0]["receipt"]["result_fingerprint"] = (
                "effect:sha256:" + "b" * 64
            )

    violations = validate.validate_trace(document, graph.to_dict())
    assert violations == [], (
        "validate.py now rejects a tampered result_fingerprint. If that is "
        "intended, the field gained a recipe — supersede ADR-013, replace this "
        "tripwire with a test that asserts the tamper IS caught, and update the "
        "schema description. Do not simply delete this assertion."
    )
