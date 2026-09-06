"""Rule J4 (ADR-030): every number in a 3.2.0+ trace is a JSON integer.

Whatever else #116 is about, the property it bought is this: a 3.2.0 trace
can be read by a browser — `JSON.parse` + `JSON.stringify` in Node touches
every number — and come back with its root unchanged, because integers within
2^53 serialise identically in every JSON implementation in use. A float,
integral ones included, and an integer beyond 2^53 cannot, so they are refused
at the producing boundary before anything is written, and documents that
declare themselves 3.2.0 are refused by readers and the validator. Below
3.2.0 the rule says nothing: those documents were truthful under their own
version, and 0.12.0's `-14.0` loads, replays and verifies exactly as it
always did.

This file is the checkable surface of ADR-030 decisions 1, 2, 3, 5, 6 and 7.
"""

from __future__ import annotations

import copy
import hashlib
import importlib.util
import json
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest

from vitruvyan_motus import (
    Decision, DurabilityProfile, Fact, GraphSpec, InMemoryTraceSink, NodeFailed,
    NonIntegerNumber, Rejection, Runtime, State, Trace, TraceBundle,
    ReplayEngine, ReplayStatus,
)
from vitruvyan_motus.context import _RunController
from vitruvyan_motus.trace import _canonical_bytes, _ChunkedLog

ROOT = Path(__file__).resolve().parent.parent
NOW = datetime(2026, 9, 6, tzinfo=timezone.utc)
# The runtime that produced fixtures 306-310 ran on a fixed 08:00 clock; a
# node replayed against those documents must stamp its writes with the same
# instant or the writes are a different document.
_FIXTURE_NOW = datetime(2026, 9, 6, 8, 0, tzinfo=timezone.utc)
MAX_SAFE = 2 ** 53 - 1


def _validate_module():
    name = "motus_contract_validate"
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(
        name, ROOT / "contract" / "validate.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


validate = _validate_module()


def _spec(name="j4", entry="a"):
    return GraphSpec.from_dict({
        "schema_version": "1.0.0", "name": name, "version": "1.0.0",
        "entry": entry,
        "nodes": [{"name": "a", "effect_class": "pure"}],
        "transitions": {"a": {"kind": "terminal"}},
    })


def _run(node, *, random_source=None, metadata=None):
    kwargs = {}
    if random_source is not None:
        kwargs["random_source"] = random_source
    return Runtime(_spec(), {"a": node}, **kwargs).run(
        State.empty("x", metadata=metadata or {}), run_id="r1")


def _resealed(document):
    """Re-seal a document after injecting values the producing API refuses."""
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


def _base_document():
    """A genuinely produced 3.2.0 trace to mutate: one int fact, no draws."""
    def node(state):
        return state.with_fact(Fact("temperature", 1, "sensor", NOW))
    return _run(node).trace.to_dict()


def _diverge(mutate):
    document = _base_document()
    mutate(document)
    return _resealed(document)


# --------------------------------------------------------------------------- #
# Decision 2 — the producing boundary refuses, with rule J4 and the path      #
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("value,what", [
    (-14.0, "an integral float, #116's own witness"),
    (0.0, "an integral zero"),
    (0.87, "an ordinary fraction"),
    (2 ** 53, "the safe-integer bound itself"),
    (-(2 ** 53) - 1, "beyond it downwards"),
    ({"nested": [{"deep": 1.5}]}, "a float at depth"),
    ([1, [2, [3.0]]], "an integral float at depth"),
])
def test_with_fact_does_not_refuse_but_the_node_write_does(value, what):
    """ADR-030 decision 2: `State` is version-agnostic, so `with_fact` itself
    never refuses — it cannot know whether the trace this write will land in
    is even governed by J4. Decision 1 moves the refusal to the trace-
    producing boundary the runtime checks a node's writes against, where it
    becomes that node's own failure, exactly as any exception it raised
    would (`NodeFailed`, a `run_failed` terminal, never a bare
    `NonIntegerNumber` out of `.run()`)."""
    State.empty("x").with_fact(Fact("k", value, "s", NOW))  # does NOT raise

    def node(state):
        return state.with_fact(Fact("k", value, "s", NOW))

    with pytest.raises(NodeFailed) as caught:
        _run(node)
    cause = caught.value.__cause__
    assert isinstance(cause, NonIntegerNumber)
    message = str(cause)
    assert "J4" in message, message
    assert "rule J4 (ADR-030)" in message
    # The JSON path to the number is DOCUMENT-ABSOLUTE, naming the write's
    # position in the transition record being refused before it is stored.
    if isinstance(value, dict):
        assert "$.writes.facts[0].value.nested[0].deep" in message
    elif isinstance(value, list):
        assert "$.writes.facts[0].value[1][1][0]" in message
    else:
        assert "$.writes.facts[0].value" in message


def test_every_producing_position_is_covered_and_not_only_facts():
    """A rule that holds on facts and not on decisions is not a rule.

    A node's own write becomes that node's failure (`NodeFailed`). The run's
    SEED — its initial state and its metadata, both settled before any node
    runs — aborts the run itself with a bare `NonIntegerNumber`, because
    there is no node attempt to blame it on (ADR-030 decisions 1 and 2)."""
    for node in (
        lambda state: state.with_fact(Fact("k", -14.0, "s", NOW)),
        lambda state: state.with_decision(Decision("k", -14.0, NOW)),
        lambda state: state.with_rejection(
            Rejection("what", "why", NOW, evidence={"n": -14.0})),
    ):
        with pytest.raises(NodeFailed) as caught:
            _run(node)
        assert isinstance(caught.value.__cause__, NonIntegerNumber)

    for seed in (
        State.new("x", facts=[Fact("k", -14.0, "s", NOW)]),
        State.new("x", decisions=[Decision("k", 5.5, NOW)]),
        State.new("x", rejections=[Rejection(
            "what", "why", NOW, evidence=[-14.0])]),
        State.empty("x", metadata={"reading": -14.0}),
        State.empty("x", metadata={"nested": {"a": [0.5]}}),
    ):
        with pytest.raises(NonIntegerNumber):
            Runtime(_spec(), {"a": lambda s: s}).run(seed, run_id="seed")


def test_a_rejection_without_evidence_passes():
    state = State.new("x", rejections=[Rejection("what", "why", NOW)])
    assert state.rejections[0].what == "what"


@pytest.mark.parametrize("value", [
    True, False, "a string", None, 0, 42, -7, MAX_SAFE, -(MAX_SAFE),
    {"ok": [1, True, None, "text"]},
])
def test_the_boundary_accepts_what_j4_admits(value):
    state = State.new("x", facts=[Fact("k", value, "s", NOW)])
    assert state.facts[0].value == value
    # bool is not a number to JSON: isinstance(True, int) must not accuse it.
    State.empty("x").with_decision(Decision("k", {"b": True}, NOW))
    State.empty("x", metadata={"flag": True, "label": None})


def test_bool_is_not_a_number_and_the_walk_does_not_descend_into_strings():
    """The false positive a regular expression produces, and the reason the
    check is a walk of the parsed value: `5.10` inside a string is prose."""
    Fact("k", {"note": "cost 5.10 eur"}, "s", NOW)
    State.empty("x").with_fact(Fact("k", {"note": "cost 5.10 eur"}, "s", NOW))
    # A JSON string may contain any number-shaped text; it is not a number.
    state = State.new("x", facts=[Fact("k", ["1.5", "2.7"], "s", NOW)])
    assert state.facts[0].value == ["1.5", "2.7"]


def test_the_metadata_refusal_happens_before_the_run_starts():
    """`State.empty` itself does not refuse (ADR-030 decision 2) — the
    metadata becomes the trace HEADER, and `Trace.__init__` is where a
    non-conforming value is refused, before `_start` writes anything: no
    `run_started`, no node attempt, a bare `NonIntegerNumber` out of
    `.run()` rather than a `NodeFailed` or a validation finding."""
    state = State.empty("x", metadata={"confidence": 0.87})  # does NOT raise
    with pytest.raises(NonIntegerNumber) as caught:
        Runtime(_spec(), {"a": lambda s: s}).run(state, run_id="seed")
    message = str(caught.value)
    assert "J4" in message
    assert "$.run.metadata.confidence" in message


def test_the_header_walk_covers_the_kernels_own_sink_numbers_not_only_metadata():
    """Round 3 M11: a mutant that narrowed `Trace.__init__`'s header walk to
    `$.run.metadata` survived the whole suite, because nothing exercised the
    OTHER header field T11 hashes -- `sink.flush_interval_ms`/`chunk_records`,
    the kernel's own numbers, never a node's or a caller's
    (`.attack/116/round3/r07_surviving_mutants.py`, target M11 / round 2's
    b03). A run with a header sink value beyond 2^53 must be refused before
    `_hub.bind` ever opens a session for it, exactly like a bad metadata
    value is."""
    with pytest.raises(NonIntegerNumber) as caught:
        Runtime(_spec(), {"a": lambda s: s},
                sink=InMemoryTraceSink(),
                durability_profile=DurabilityProfile.BUFFERED,
                flush_interval_ms=2 ** 60, chunk_records=2 ** 60,
                ).run(State.empty("x"), run_id="seed")
    message = str(caught.value)
    assert "J4" in message
    assert "$.run.sink." in message


def test_the_writers_version_gate_still_governs_which_documents_j4_sees():
    """Round 3 M12: a mutant that deleted the version check inside
    `Trace._refuse_j4` -- so it walked at every version, not only ones J4
    governs -- also survived, because nothing built a pre-3.2.0 writer by
    hand and fed it a value only 3.2.0 refuses
    (`.attack/116/round3/r07_surviving_mutants.py`, target M12). ADR-030
    decision 3: a document below 3.2.0 was truthful under its own rule, and a
    writer at that version admits what it always admitted."""
    header = {"run_id": "r", "metadata": {"temperature": -14.0}}
    accepted = Trace(header, schema_version="3.1.0").append(
        {"seq": 1, "kind": "x", "value": 0.5})
    assert json.loads(accepted.to_json())["records"][0]["value"] == 0.5
    with pytest.raises(NonIntegerNumber):
        Trace(header, schema_version="3.2.0")


def test_a_runtime_run_never_emits_a_document_j4_refuses():
    """The whole point, as a property: anything the runtime can produce
    validates clean under its own version's rules."""
    def node(state, ctx):
        return (state
                .with_fact(Fact("drawn", int(ctx.rand() * 1_000_000), "ctx", NOW))
                .with_fact(Fact("max", MAX_SAFE, "test", NOW))
                .with_decision(Decision("on", "keep", NOW, reason="integer"))
                .with_rejection(Rejection("null", "no sample", NOW,
                                          evidence={"count": 0})))
    result = _run(node, random_source=lambda: 0.25,
                  metadata={"policy": "v2", "limit": 100})
    document = result.trace.to_dict()
    assert document["schema_version"] == "3.2.0"
    assert validate.validate_trace(document) == []
    jsonl_violations, _ = validate.validate_jsonl(result.trace.to_jsonl())
    assert jsonl_violations == []
    assert Trace.from_json(result.trace.to_json()).root == result.trace.root


# --------------------------------------------------------------------------- #
# Decision 3 — readers are scoped by the document's own version               #
# --------------------------------------------------------------------------- #


def test_a_3_2_0_document_with_a_float_is_refused_at_every_reader():
    document = _diverge(lambda d: d["records"][0]["initial_state"]["facts"].append(
        {"key": "cold", "value": -14.0, "source": "s", "ts": d["records"][0]["ts"]}))
    with pytest.raises(NonIntegerNumber) as caught:
        Trace.from_json(json.dumps(document))
    assert "J4" in str(caught.value)
    assert "$.records[0].initial_state" in str(caught.value)
    with pytest.raises(NonIntegerNumber):
        Trace.from_dict(document)
    violations = validate.validate_trace(json.loads(json.dumps(document)))
    assert [v.rule for v in violations] == ["J4"]


def _routed_document():
    """A genuine 3.2.0 trace whose graph routes on a decision — so its
    record sequence contains a routing record with a `value`."""
    spec = GraphSpec.from_dict({
        "schema_version": "1.0.0", "name": "j4r", "version": "1.0.0",
        "entry": "route",
        "nodes": [{"name": "route", "effect_class": "pure"},
                   {"name": "done", "effect_class": "pure"}],
        "transitions": {
            "route": {"kind": "route", "on": "decision",
                       "map": {"go": "done"}},
            "done": {"kind": "terminal"},
        },
    })

    def route(state):
        return state.with_decision(Decision("decision", "go", NOW))

    result = Runtime(spec, {"route": route, "done": lambda s: s}).run(
        State.empty("x"), run_id="r")
    return result.trace.to_dict()


def test_j4_reaches_every_position_t11_hashes_in_a_3_2_0_document():
    # context_draws[].value — decision 5 makes the genuine form an integer,
    # so a float there is a 3.2.0 document lying about its own rules.
    def same_draw(d):
        next(r for r in d["records"] if r["kind"] == "transition")[
            "context_draws"] = [{"source": "rand", "value": 0.25},
                                {"source": "uuid", "value": "u"}]
    violations = validate.validate_trace(_diverge(same_draw))
    assert [v.rule for v in violations] == ["J4"]
    assert any("context_draws[0].value" in v.path for v in violations)
    with pytest.raises(NonIntegerNumber):
        Trace.from_json(json.dumps(_diverge(same_draw)))

    # routing values (a miss may carry any value; a float is not admissible
    # in a 3.2.0 document, because it is a number in the trace). The reader's
    # doc-level walk is the clean witness here — the validator's schema layer
    # also refuses a float in a MATCHED routing value (its value is a string
    # by the outcome conditional), so it refuses first and J4 never reports.
    routed = _routed_document()
    route = next(r for r in routed["records"] if r["kind"] == "routing")
    route["value"] = 0.5
    resealed = _resealed(routed)
    with pytest.raises(NonIntegerNumber) as caught:
        Trace.from_json(json.dumps(resealed))
    assert "$.records[3].value" in str(caught.value)
    assert validate.validate_trace(json.loads(json.dumps(resealed)))

    # metadata in the header — and the reader's path must be the SAME path
    # the validator names, document-absolute (b08: they once disagreed,
    # `Trace.from_dict` saying `$.metadata.temperature` while the validator
    # said `$.run.metadata.temperature` for the identical document, because
    # the reader's header walk started at `$` instead of `$.run`).
    def header_metadata(d):
        d["run"]["metadata"] = {"temperature": -14.0}
    header_document = _diverge(header_metadata)
    violations = validate.validate_trace(json.loads(json.dumps(header_document)))
    assert [v.rule for v in violations] == ["J4"]
    validator_path = violations[0].path
    assert validator_path == "$.run.metadata.temperature"
    with pytest.raises(NonIntegerNumber) as caught:
        Trace.from_dict(header_document)
    assert validator_path in str(caught.value), (
        f"reader says {caught.value!s}, validator says {validator_path!r} — "
        "one document, two paths")


@pytest.mark.parametrize("value", [2 ** 53, -(2 ** 53) - 1, -14.0, 0.87])
def test_a_3_2_0_document_with_a_non_conforming_number_is_j4(value):
    def diverge(d):
        d["records"][0]["initial_state"]["facts"].append(
            {"key": "v", "value": value, "source": "s",
             "ts": d["records"][0]["ts"]})
    violations = validate.validate_trace(_diverge(diverge))
    assert [v.rule for v in violations] == ["J4"], violations
    with pytest.raises(NonIntegerNumber):
        Trace.from_json(json.dumps(_diverge(diverge)))


def test_a_3_2_0_document_at_the_safe_boundary_is_valid():
    for value in (MAX_SAFE, -MAX_SAFE, 0, 1, -1):
        document = _run(lambda s: s.with_fact(
            Fact("v", value, "s", NOW))).trace.to_dict()
        assert validate.validate_trace(document) == []
        assert Trace.from_json(json.dumps(document)).root is not None


def test_a_zero_point_twelve_trace_with_minus_14_0_loads_replays_and_verifies():
    """The ADR's witness, corrected (2026-09-06 review correction): the
    original version of this test substituted a DIFFERENT node body at
    verify time — one writing the integer `-14` — because on the first
    version of this implementation the ORIGINAL body, writing a genuine
    non-integral float, would have made `with_fact` itself raise `J4` even
    though the DOCUMENT being replayed is 3.0.0. That substitution hid
    exactly the defect decision 2 exists to fix: it made the test pass
    whether or not `State` was version-agnostic. `0.87` (not `-14.0`, which
    is an INTEGRAL float and would pass even a check that only special-cased
    whole numbers) replayed through the SAME node body the document itself
    used is the property — `State` does not know or care what version of
    trace it is helping to reconstruct."""
    def node(state):
        return state.with_fact(Fact("temperature", 0.87, "sensor", NOW))
    document = _run(lambda s: s.with_fact(
        Fact("temperature", 1, "sensor", NOW))).trace.to_dict()
    document["schema_version"] = "3.0.0"
    for record in document["records"]:
        if record.get("kind") == "transition":
            # 0.12.0-era documents carry the array form, not null.
            record["violations"] = []
        for fact in (record.get("writes") or {}).get("facts", []):
            if fact["key"] == "temperature":
                fact["value"] = 0.87
    trace = Trace.from_json(json.dumps(_resealed(document)))
    assert trace.root is not None
    assert validate.validate_trace(json.loads(trace.to_json())) == [], (
        "a 3.0.0 document carrying 0.87 must validate exactly as it always did")
    jsonl_violations, _ = validate.validate_jsonl(trace.to_jsonl())
    assert jsonl_violations == []

    bundle = TraceBundle(_spec(), trace)
    playback = ReplayEngine(bundle).playback()
    assert playback.state.fact("temperature") == 0.87

    # The ORIGINAL node body — not a substitute writing an acceptable int.
    # Without ADR-030 decision 2 (State stays version-agnostic), `with_fact`
    # here would raise NonIntegerNumber and this would be a NodeFailed, not
    # a verified replay of a document from before rule J4 existed.
    verified = ReplayEngine(bundle).verify({"a": node})
    assert verified.verified == (("a", 3),)


def test_from_snapshot_and_replay_commit_are_readers_that_never_refuse():
    """Stuffing a state with float values is how old evidence is read back.
    `from_snapshot` and `_replay_commit` reconstruct it without J4 — the
    document's own version decided at load, where it is visible."""
    state = State.from_snapshot(
        {"facts": [{"key": "cold", "value": -14.0, "source": "s",
                    "ts": "2026-09-06T00:00:00Z"}],
         "decisions": [], "rejections": []},
        metadata={"probe": 0.5},
    )
    assert state.fact("cold") == -14.0
    assert state.metadata("probe") == 0.5
    replay = State.empty("r")._replay_commit(
        {"facts": [{"key": "more", "value": 0.87, "source": "s",
                    "ts": "2026-09-06T00:00:00Z"}],
         "decisions": [], "rejections": []}, 7)
    assert replay.fact("more") == 0.87


def test_the_validator_and_the_jsonl_form_give_the_same_verdict():
    document = _diverge(lambda d: d["records"][0]["initial_state"]["facts"].append(
        {"key": "v", "value": -14.0, "source": "s", "ts": d["records"][0]["ts"]}))
    text = json.dumps(document)
    stream = "\n".join([
        json.dumps({"schema_version": document["schema_version"],
                    "run": document["run"]}),
        *[json.dumps(r) for r in document["records"]],
    ]) + "\n"
    violations = validate.validate_trace(json.loads(text))
    jsonl_violations, doc = validate.validate_jsonl(stream)
    assert [v.rule for v in violations] == ["J4"]
    assert [v.rule for v in jsonl_violations] == ["J4"]
    assert jsonl_violations[0].path == violations[0].path


def test_the_cli_names_j4_for_3_2_0_and_stays_silent_below(tmp_path):
    """The CLI's document parser refuses a 3.2.0 document that is not
    integers-only with rule J4 — and says nothing about a 3.1.0 document
    carrying the same float, because that document is truthful."""
    float_doc = _diverge(lambda d: d["records"][0]["initial_state"]["facts"].append(
        {"key": "v", "value": -14.0, "source": "s", "ts": d["records"][0]["ts"]}))

    current = tmp_path / "current.json"
    current.write_text(json.dumps(float_doc), encoding="utf-8")
    done = subprocess.run(
        [sys.executable, str(ROOT / "contract" / "validate.py"), "trace",
         str(current)],
        capture_output=True, text=True, timeout=60)
    assert done.returncode == 1, done.stdout
    assert done.stdout.startswith("J4 "), done.stdout
    assert "-14.0" in done.stdout

    older = tmp_path / "older.json"
    older_doc = copy.deepcopy(float_doc)
    older_doc["schema_version"] = "3.1.0"
    older.write_text(json.dumps(_resealed(older_doc)), encoding="utf-8")
    done = subprocess.run(
        [sys.executable, str(ROOT / "contract" / "validate.py"), "trace",
         str(older)],
        capture_output=True, text=True, timeout=60)
    assert done.returncode == 0, done.stdout
    assert done.stdout == ""


# --------------------------------------------------------------------------- #
# Decision 5 — the draw record carries the integer it was made from           #
# --------------------------------------------------------------------------- #

def test_the_draw_is_recorded_as_an_integer_and_the_node_receives_the_float():
    control = _RunController(random_source=lambda: 0.25)
    assert control.node_context.rand() == 0.25
    draws = control.draws_since(0)
    assert [d.to_dict() for d in draws] == [{"source": "rand", "value": 2 ** 51}]
    # The wire form of the draw is a JSON integer, so the transition record is
    # J4-clean by construction.
    assert 0 <= draws[0].value < 2 ** 53


def test_the_draws_are_the_same_floats_as_0_13_0_for_the_same_seed():
    """Compat lens: the node must receive exactly what 0.13.0 handed it for
    the same source. 0.13.0 recorded the float and returned it; from 3.2.0 the
    record carries the integer n and the node receives n / 2^53 — which for a
    `random.random()`-shaped source is the SAME float, exactly."""
    import random
    seed = random.Random(20260906)
    control = _RunController(random_source=seed.random)
    for _ in range(200):
        given = control.node_context.rand()               # n / 2^53
        n = control.draws_since(0)[-1].value              # the recorded integer
        assert isinstance(n, int) and not isinstance(n, bool)
        assert n / 2 ** 53 == given
    # And a deterministic seeded run reproduces its own floats: replay the
    # recorded integers and compare with what the nodes were handed.
    def node(state, ctx):
        return state.with_fact(Fact("draw", int(ctx.rand() * 10 ** 9), "ctx", NOW))
    source = random.Random(42)
    result = _run(node, random_source=source.random)
    bundle = TraceBundle(_spec(), result.trace)
    verified = ReplayEngine(bundle).verify({"a": node})
    assert verified.verified == (("a", 3),)


def test_a_pre_3_2_0_trace_with_float_draws_still_verifies():
    """0.13.0 recorded draws as floats; replay must hand those floats back
    verbatim. The paper trail: take a genuine 3.1.0 trace, put a float in the
    draw record where 0.13.0 did, reseal — and verify-replay still agrees."""
    def node(state, ctx):
        return state.with_fact(Fact("draw", int(ctx.rand() * 1000), "ctx", NOW))
    document = _run(node, random_source=lambda: 0.125).trace.to_dict()
    document["schema_version"] = "3.1.0"
    transition = next(r for r in document["records"] if r["kind"] == "transition")
    transition["context_draws"] = [{"source": "rand", "value": 0.125}]
    trace = Trace.from_dict(_resealed(document))
    assert validate.validate_trace(json.loads(trace.to_json())) == []
    verified = ReplayEngine(TraceBundle(_spec(), trace)).verify({"a": node})
    assert verified.verified == (("a", 3),)


# --------------------------------------------------------------------------- #
# Decision 7 — the Node/JS round-trip witness from #116                       #
# --------------------------------------------------------------------------- #

NODE = shutil.which("node")


@pytest.mark.skipif(
    NODE is None,
    reason="node is not on PATH; the JS round-trip witness needs a JS engine",
)
def test_a_3_2_0_trace_survives_a_javascript_json_round_trip():
    """#116's measurement, in the opposite direction: `-14.0` died in a
    browser because Python and JavaScript write DIFFERENT text for the same
    RFC 8259 number. A 3.2.0 trace carries only integers within 2^53, which
    every JSON implementation serialises identically — so JSON.parse +
    JSON.stringify in Node, the exact operation that accused a browser of
    tampering, leaves the root byte-identical."""
    def node(state, ctx):
        return (state
                .with_fact(Fact("temperature", -14, "sensor", NOW))
                .with_fact(Fact("max", MAX_SAFE, "test", NOW))
                .with_fact(Fact("drawn", int(ctx.rand() * 10 ** 9), "ctx", NOW))
                .with_decision(Decision("on", "keep", NOW))
                .with_rejection(Rejection("null", "no sample", NOW,
                                          evidence={"count": 0, "n": 1})))
    result = _run(node, random_source=lambda: 0.25, metadata={"scale": 10,
                                                              "ref": "x"})
    payload = result.trace.to_json()
    assert validate.validate_trace(result.trace.to_dict()) == []

    done = subprocess.run(
        [NODE, "-e",
         "process.stdout.write(JSON.stringify(JSON.parse("
         "require('fs').readFileSync(0, 'utf8'))))"],
        input=payload, capture_output=True, text=True, timeout=60)
    assert done.returncode == 0, done.stderr
    round_tripped = done.stdout

    original = Trace.from_json(payload)
    witness = Trace.from_json(round_tripped)
    assert witness.root == original.root, (
        "the exact operation from #116 changed the root — the round trip must "
        "be byte-stable for 3.2.0 documents")
    assert json.loads(round_tripped) == json.loads(payload)
    assert validate.validate_trace(json.loads(round_tripped)) == []


# --------------------------------------------------------------------------- #
# Decision 6 — the fixtures, loaded as the contract says a consumer loads them #
# --------------------------------------------------------------------------- #

def _fixture(name: str) -> dict:
    return json.loads((ROOT / "contract" / "fixtures" / name).read_text(encoding="utf-8"))


def _fixture_spec():
    """The GraphSpec the J4 fixtures' runtime runs declare: entry `worker`."""
    return GraphSpec.from_dict({
        "schema_version": "1.0.0", "name": "j4fx", "version": "1.0.0",
        "entry": "worker",
        "nodes": [{"name": "worker", "effect_class": "pure"}],
        "transitions": {"worker": {"kind": "terminal"}},
    })


def test_the_3_2_0_draw_fixture_replays():
    """Fixture 310: a genuine 3.2.0 trace whose draw is recorded as the
    integer n. Loading it, verifying it and replaying the transition must all
    reproduce the float the node was handed."""
    instance = _fixture("310-trace-3-2-j4-draw-integer.json")["instance"]
    trace = Trace.from_json(json.dumps(instance))
    transition = next(r for r in trace.records if r["kind"] == "transition")
    assert transition["context_draws"] == [{"source": "rand", "value": 2 ** 51}]

    def node(state, ctx):
        return state.with_fact(Fact("drawn", int(ctx.rand() * 1000), "ctx",
                                   _FIXTURE_NOW))

    bundle = TraceBundle(_fixture_spec(), trace)
    verified = ReplayEngine(bundle).verify({"worker": node})
    assert verified.verified == (("worker", 3),)
    assert verified.state.fact("drawn") == 250


def test_the_3_1_0_float_fixture_loads_silently():
    """Fixture 309: a 3.1.0 document carrying -14.0 and an old-style float
    draw. It must load, derive its root and play back with no J4 anywhere."""
    instance = _fixture("309-trace-3-1-float-loads.json")["instance"]
    trace = Trace.from_json(json.dumps(instance))
    assert trace.root is not None
    assert validate.validate_trace(json.loads(trace.to_json())) == []


# --------------------------------------------------------------------------- #
# The migration is checked, not assumed (ADR-030, H1)                         #
# --------------------------------------------------------------------------- #

def test_this_repository_writes_only_integers_into_producing_positions():
    """The demo, the examples and the e2e probe are the producers this
    repository can see (ADR-030 H1, 2026-09-06 review correction: the ADR
    cannot grep Orbis's or Limen's pinned code from this repository — that
    check is theirs, at their own pin bump, orbis#63). This asserts ours.

    An AST walk, not a regular expression: a pattern matching `,\\s*-?\\d+\\.\\d+`
    answers a question about CHARACTERS, and this question is about values —
    it would miss a float built any way other than a bare literal in the
    obvious argument position, and it would flag a float-shaped STRING it
    never should have looked at in the first place (AGENTS.md's own example).
    Parsing the source and walking each call's actual argument tree finds a
    literal float at any depth — inside a nested list or dict passed as
    `evidence=` or `metadata=`, not only as the second positional argument."""
    import ast

    producing_calls = {
        "Fact", "Decision", "Rejection",
        "with_fact", "with_decision", "with_rejection",
    }

    def call_name(node: ast.Call) -> str | None:
        func = node.func
        if isinstance(func, ast.Name):
            return func.id
        if isinstance(func, ast.Attribute):
            return func.attr
        return None

    offenders: list[str] = []
    seen: set[tuple[str, int, int]] = set()
    for directory in ("demo", "examples", "e2e"):
        for path in sorted(Path(ROOT, directory).rglob("*.py")):
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            for node in ast.walk(tree):
                if not isinstance(node, ast.Call) or call_name(node) not in producing_calls:
                    continue
                arguments = list(node.args) + [kw.value for kw in node.keywords]
                for argument in arguments:
                    for sub in ast.walk(argument):
                        if isinstance(sub, ast.Constant) and isinstance(sub.value, float):
                            key = (str(path), sub.lineno, sub.col_offset)
                            if key not in seen:
                                seen.add(key)
                                offenders.append(
                                    f"{path}:{sub.lineno}: {call_name(node)}(...) "
                                    f"writes the float literal {sub.value!r}")
    assert offenders == [], (
        "a producer in this tree writes a float literal into a producing "
        "position; rule J4 (ADR-030) would refuse it at runtime:\n" +
        "\n".join(sorted(offenders)))


def test_an_injected_float_cannot_hide_inside_a_3_2_0_document_hand_built():
    """The boundary the walk must hold even when the document never went
    through the runtime: a hand-built 3.2.0 document with a float anywhere in
    a value position is refused at load and by the validator, no matter how
    deep."""
    document = _base_document()
    transition = next(r for r in document["records"] if r["kind"] == "transition")
    transition["writes"]["facts"].append(
        {"key": "smuggle", "value": {"a": [{"b": [0.5]}]}, "source": "s",
         "ts": transition["ts"]})
    resealed = _resealed(document)
    violations = validate.validate_trace(json.loads(json.dumps(resealed)))
    assert [v.rule for v in violations] == ["J4"]
    assert any(
        "$.records[2].writes.facts[1].value.a[0].b[0]" == v.path
        for v in violations), [v.path for v in violations]
    with pytest.raises(NonIntegerNumber):
        Trace.from_json(json.dumps(resealed))


# --------------------------------------------------------------------------- #
# A lying `__abs__` cannot smuggle a magnitude past the bound check           #
# --------------------------------------------------------------------------- #

class _SneakyInt(int):
    """`abs()` lies; `json.dumps` still writes the true PyLong bits (b04/b05).

    The C JSON encoder never calls a Python-level dunder for a real int, so
    this is not a synthetic worry: `json.dumps(_SneakyInt(2 ** 70))` writes
    `1180591620717411303424` regardless of what `__abs__` answers, which is
    exactly why the bound check must read the same bits the encoder does.
    """

    def __abs__(self) -> int:
        return 0


def test_an_int_subclass_with_a_lying_abs_cannot_smuggle_a_magnitude_past_j4():
    """A bound check written as `abs(item) > MAX` trusts the object to grade
    its own homework. `_SneakyInt(2 ** 70).__abs__()` answers `0`, so such a
    check would accept it — and the wire would still carry `2**70`, because
    `json.dumps` reads the interpreter's own PyLong bits, not `__abs__`.
    `operator.index` reads those same bits, so the check and the bytes on
    disk can never disagree about what is being written."""
    assert abs(_SneakyInt(2 ** 70)) == 0, "the premise: __abs__ really lies"
    assert json.dumps(_SneakyInt(2 ** 70)) == "1180591620717411303424", (
        "the premise: json.dumps writes the true magnitude regardless")

    def node(state):
        return state.with_fact(Fact("amount", _SneakyInt(2 ** 70), "s", NOW))

    with pytest.raises(NodeFailed) as caught:
        _run(node)
    cause = caught.value.__cause__
    assert isinstance(cause, NonIntegerNumber), cause
    assert "1180591620717411303424" in str(cause), str(cause)


def test_the_validators_bound_check_reads_the_same_bits_json_dumps_does():
    """The validator's own walk (`_j4_violations`) has the identical hole
    and the identical fix, over a hand-built document rather than a run."""
    violations = validate._j4_violations({"amount": _SneakyInt(2 ** 70)})
    assert [v.rule for v in violations] == ["J4"]
    assert "1180591620717411303424" in violations[0].message


def test_the_walks_own_bound_check_is_not_only_saved_by_isolation():
    """`_integer_only` called directly — as `Trace.from_dict` calls it on an
    already-isolated document, and as any future caller might on data that
    never passed through `_strict_plain_json` at all. This is the walk's
    OWN defence, independent of `_strict_plain_json` normalising the value
    first: the two are separate fixes for the same class of hole, and this
    proves the walk does not rely solely on the other one having run."""
    from vitruvyan_motus.trace import _integer_only
    with pytest.raises(NonIntegerNumber) as caught:
        _integer_only({"amount": _SneakyInt(2 ** 70)})
    assert "1180591620717411303424" in str(caught.value)


def test_an_accepted_int_subclass_is_normalised_to_a_plain_int_on_the_wire():
    """Decision 1's "serialise THAT plain int", proven on the value rather
    than on the refusal: an int SUBCLASS within range is accepted (nothing
    about its magnitude is wrong), and what actually lands in the trace is a
    genuine `int` — not the subclass instance — so nothing downstream that
    ever inspects `type(value)` or calls an overridden dunder for some
    unrelated reason inherits a landmine that merely happens to be dormant
    today. This is `_strict_plain_json`'s own contribution, independent of
    `_integer_only`'s bound check: a value can be perfectly IN RANGE and
    still be worth normalising."""
    class _Loud(int):
        """A harmless subclass; the point under test is TYPE, not behaviour."""

    def node(state):
        return state.with_fact(Fact("amount", _Loud(42), "s", NOW))

    result = _run(node)
    written = next(r for r in result.trace.records if r["kind"] == "transition")
    value = written["writes"]["facts"][0]["value"]
    assert value == 42
    assert type(value) is int, (
        f"the wire still carries a {type(value).__name__}, not a plain int")

def test_the_public_append_is_a_producing_boundary_too():
    """A caller building a trace by hand, not through `Runtime`, meets the
    same J4 refusal at `Trace.append`: the gate belongs to the writer that
    stamps the version, whoever calls it. The mutation probe that removed
    the gate from `append` alone survived the suite until this test."""
    def node(state, ctx):
        return state.with_fact(Fact("n", 1, "test", NOW))
    trace = _run(node).trace
    before = len(trace.to_dict()["records"])
    with pytest.raises(NonIntegerNumber) as refused:
        trace.append({"kind": "note", "payload": {"nested": [{"ratio": 0.5}]}})
    assert "J4" in str(refused.value)
    assert f"$.records[{before}].payload.nested[0].ratio" in str(refused.value)
    # nothing was appended by the refused call
    assert len(trace.to_dict()["records"]) == before
    # and an integer at the same place is accepted
    grown = trace.append({"kind": "note", "payload": {"nested": [{"ratio": 5000}]}})
    assert len(grown.to_dict()["records"]) == before + 1


def test_the_constructor_is_a_producing_boundary_too_for_supplied_records():
    """ADR-030 decision 1 (2026-09-06 review correction): `Trace.__init__`
    also takes `records`, and until this walk a record supplied there
    reached the wire completely unchecked -- the refusal at `append` only
    ever saw records added one at a time through it
    (`.attack/116/round3/r01_ctor_records.py`). The r01c shape: doctor an
    int into the float it started as, inside a genuinely produced 3.2.0 run,
    and reseal it through the constructor -- the reseal pattern `Trace._seal`
    documents as one of the two accounts a sink and a trace can hold of one
    run (`.attack/116/round3/r01c_ctor_real_run.py`)."""
    def node(state):
        return state.with_fact(Fact("temperature", 1, "sensor", NOW))
    document = _run(node).trace.to_dict()
    transition_index = next(
        i for i, r in enumerate(document["records"]) if r["kind"] == "transition")
    document["records"][transition_index]["writes"]["facts"][0]["value"] = -14.0
    resealed = _resealed(document)
    log = _ChunkedLog().extend(resealed["records"])

    with pytest.raises(NonIntegerNumber) as caught:
        Trace(document["run"], log, schema_version="3.2.0")
    message = str(caught.value)
    assert "J4" in message
    assert f"$.records[{transition_index}].writes.facts[0].value" in message

    # The same records, at 3.1.0, are truthful under their own version and
    # stay accepted (ADR-030 decision 3) -- the version gate governs the
    # constructor's records walk exactly as it governs `append`'s.
    accepted = Trace(document["run"], log, schema_version="3.1.0")
    stored = accepted.to_dict()["records"][transition_index]["writes"]["facts"][0]["value"]
    assert stored == -14.0


def _numeric_offenders(value, path="$"):
    """Yield ``(path, value)`` for every float, or integer with |n| >= 2**31,
    anywhere in a JSON-shaped tree.

    Deliberately independent of anything `trace.py` exports: this is a
    from-scratch walk written the way a suspicious outside reader would write
    one, not a reuse of `_integer_only`, so it cannot pass merely because it
    shares a bug with the code it is checking. `2**31` (not J4's own
    `2**53 - 1`) is chosen because nothing a genuine run below produces comes
    anywhere near it — every kernel-built number in a trace record is a
    small count (a seq, an attempt, an index) or a bounded configuration
    value (`flush_interval_ms`, `chunk_records`) — so a value crossing this
    bound is itself a signal that something unexpected reached the document,
    even before asking whether J4 admits it.
    """
    if isinstance(value, bool):
        return
    if isinstance(value, float):
        yield path, value
    elif isinstance(value, int) and abs(value) >= 2 ** 31:
        yield path, value
    elif isinstance(value, dict):
        for key, item in value.items():
            yield from _numeric_offenders(item, f"{path}.{key}")
    elif isinstance(value, list):
        for index, item in enumerate(value):
            yield from _numeric_offenders(item, f"{path}[{index}]")


def test_every_record_kind_the_runtime_can_emit_is_j4_clean():
    """Round 6 (#116): the backstop that used to walk every runtime record a
    second time (round 4) or fold that second walk into isolation (round 5)
    is gone — `_append_runtime` no longer checks anything, on the strength of
    an INVARIANT (see its docstring) rather than a re-check. This is the
    other half of making that safe: a document exercising every record kind
    the runtime can emit at 3.2.0 must still validate clean under J4, and —
    the structural guard a re-check would have given for free, and a mere
    "no violations" report would not — must carry no float and no integer
    anywhere near the range only a caller's number would occupy. A new
    kernel field that smuggled a caller's number past the invariant this
    round relies on would be caught here, by its VALUE, not by trusting that
    whoever added it also updated a walk."""
    documents = []

    # transition (raised, then retried and returned), attempt_started x2,
    # routing, run_completed -- and, under this run's sink, the header's own
    # sink numbers.
    attempts: list[int] = []

    def flaky(state):
        attempts.append(1)
        if len(attempts) == 1:
            raise RuntimeError("first attempt fails")
        return (state.with_fact(Fact("k", 1, "s", NOW))
                     .with_decision(Decision("d", "go", NOW)))

    retry_spec = GraphSpec.from_dict({
        "schema_version": "1.0.0", "name": "j4allkinds", "version": "1.0.0",
        "entry": "flaky",
        "nodes": [{"name": "flaky", "effect_class": "pure"},
                  {"name": "done", "effect_class": "pure"}],
        "transitions": {
            "flaky": {"kind": "route", "on": "d", "map": {"go": "done"}},
            "done": {"kind": "terminal"},
        },
    })
    retried = Runtime(
        retry_spec, {"flaky": flaky, "done": lambda s: s}, max_attempts=2,
        sink=InMemoryTraceSink(), durability_profile=DurabilityProfile.BUFFERED,
    ).run(State.empty("x"), run_id="all-kinds-retry")
    documents.append(retried.trace.to_dict())

    # run_failed: STRICT aborts a node that never succeeds.
    def always_fails(state):
        raise RuntimeError("never succeeds")
    with pytest.raises(NodeFailed) as failed:
        Runtime(_spec("j4fail"), {"a": always_fails}).run(
            State.empty("x"), run_id="all-kinds-fail")
    documents.append(failed.value.trace.to_dict())

    # run_cancelled: lodged before the run starts, so run_started is the
    # only other record.
    cancelling = Runtime(_spec("j4cancel"), {"a": lambda s: s})
    cancelling.cancel("shutdown before start")
    cancelled = cancelling.run(State.empty("x"), run_id="all-kinds-cancel")
    documents.append(cancelled.trace.to_dict())

    # resume/continuation: the new segment's `initial_state` carries the
    # SOURCE segment's committed facts -- assembled by the runtime from a
    # caller-supplied State, not written by any node in THIS segment, which
    # is exactly the value this round moved off the removed backstop walk
    # and onto `Runtime._execute`'s own check.
    def first(state):
        return state.with_fact(Fact("first", 7, "test", NOW))

    def second(state):
        return state.with_fact(Fact("second", 9, "test", NOW))

    resume_spec = GraphSpec.from_dict({
        "schema_version": "1.0.0", "name": "j4resume", "version": "1.0.0",
        "entry": "first",
        "nodes": [{"name": "first", "effect_class": "pure"},
                  {"name": "second", "effect_class": "pure"}],
        "transitions": {"first": {"kind": "next", "to": "second"},
                         "second": {"kind": "terminal"}},
    })
    complete = Runtime(resume_spec, {"first": first, "second": second}).run(
        State.empty("x"), run_id="all-kinds-resume-source")
    records = list(complete.trace.records)
    cut = next(i for i, r in enumerate(records) if r["kind"] == "routing")
    partial = complete.trace.to_dict()
    partial["records"] = partial["records"][: cut + 1]
    resumed = ReplayEngine(TraceBundle(resume_spec, Trace.from_dict(partial))).resume(
        Runtime(resume_spec, {"first": first, "second": second}),
        run_id="all-kinds-resume-resumed",
    )
    documents.append(complete.trace.to_dict())
    documents.append(resumed.trace.to_dict())

    seen_kinds = {
        record["kind"] for document in documents for record in document["records"]
    }
    expected_kinds = {
        "run_started", "attempt_started", "transition", "routing",
        "run_completed", "run_failed", "run_cancelled",
    }
    assert expected_kinds <= seen_kinds, expected_kinds - seen_kinds
    assert resumed.trace.to_dict()["run"].get("resume") is not None

    for document in documents:
        violations = validate.validate_trace(document)
        assert violations == [], violations
        offenders = list(_numeric_offenders(document))
        assert offenders == [], offenders
