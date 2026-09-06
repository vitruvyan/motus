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
    Decision, Fact, GraphSpec, InMemoryTraceSink, NonIntegerNumber, Rejection,
    Runtime, State, Trace, TraceBundle, ReplayEngine, ReplayStatus,
)
from vitruvyan_motus.context import _RunController
from vitruvyan_motus.trace import _canonical_bytes

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
def test_with_fact_refuses_a_number_j4_does_not_admit(value, what):
    with pytest.raises(NonIntegerNumber) as caught:
        State.empty("x").with_fact(Fact("k", value, "s", NOW))
    message = str(caught.value)
    assert "J4" in message, message
    assert "rule J4 (ADR-030)" in message
    assert "fact 'k'" in message
    # The JSON path to the number, present so a caller can find it.
    if isinstance(value, dict):
        assert "$.value.nested[0].deep" in message
    elif isinstance(value, list):
        assert "$.value[1][1][0]" in message
    else:
        assert "$.value" in message


def test_every_producing_position_is_covered_and_not_only_facts():
    """A rule that holds on facts and not on decisions is not a rule."""
    for build in (
        lambda: State.empty("x").with_fact(Fact("k", -14.0, "s", NOW)),
        lambda: State.empty("x").with_decision(Decision("k", -14.0, NOW)),
        lambda: State.empty("x").with_rejection(
            Rejection("what", "why", NOW, evidence={"n": -14.0})),
        lambda: State.new("x", facts=[Fact("k", -14.0, "s", NOW)]),
        lambda: State.new("x", decisions=[Decision("k", 5.5, NOW)]),
        lambda: State.new("x", rejections=[Rejection(
            "what", "why", NOW, evidence=[-14.0])]),
        lambda: State.empty("x", metadata={"reading": -14.0}),
        lambda: State.empty("x", metadata={"nested": {"a": [0.5]}}),
    ):
        with pytest.raises(NonIntegerNumber):
            build()


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
    """The runtime does its metadata seeding from the State it is handed; a
    caller supplying a float metadata value must fail before anything is
    written, not mid-run and not at validation."""
    with pytest.raises(NonIntegerNumber):
        State.empty("x", metadata={"confidence": 0.87})


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

    # metadata in the header.
    def header_metadata(d):
        d["run"]["metadata"] = {"temperature": -14.0}
    violations = validate.validate_trace(_diverge(header_metadata))
    assert any(v.rule == "J4" and "run.metadata" in v.path for v in violations)


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
    """The ADR's witness, verbatim: 0.12.0 wrote schema 3.0.0 and could
    write `-14.0` into a fact. That document must load, derive its root,
    validate clean, play back and verify exactly as it did before J4."""
    def node(state):
        return state.with_fact(Fact("temperature", 1, "sensor", NOW))
    document = _run(node).trace.to_dict()
    document["schema_version"] = "3.0.0"
    for record in document["records"]:
        if record.get("kind") == "transition":
            # 0.12.0-era documents carry the array form, not null.
            record["violations"] = []
        for fact in (record.get("writes") or {}).get("facts", []):
            if fact["key"] == "temperature":
                fact["value"] = -14.0
    trace = Trace.from_json(json.dumps(_resealed(document)))
    assert trace.root is not None
    assert validate.validate_trace(json.loads(trace.to_json())) == [], (
        "a 3.0.0 document carrying -14.0 must validate exactly as it always did")
    jsonl_violations, _ = validate.validate_jsonl(trace.to_jsonl())
    assert jsonl_violations == []

    bundle = TraceBundle(_spec(), trace)
    playback = ReplayEngine(bundle).playback()
    assert playback.state.fact("temperature") == -14.0

    def same(state):
        return state.with_fact(Fact("temperature", -14, "sensor", NOW))

    verified = ReplayEngine(bundle).verify({"a": same})
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
    repository can see. A float literal in a Fact/Decision/Rejection value or
    a metadata map would be refused by rule J4 the moment it ran, so the grep
    is the migration check for our own tree. Orbis and Limen are separate
    repositories; their migration to integer fact values is checked at their
    pin bump (orbis#63) — this test asserts ours."""
    import re
    pattern = re.compile(
        r"(?:with_fact|with_decision|with_rejection|State\.new|State\.empty)"
        r"\([^\n]*?,\s*(-?\d+\.\d+)(?:,|\))", re.MULTILINE)
    offenders = []
    for directory in ("demo", "examples", "e2e"):
        for path in sorted(Path(ROOT, directory).rglob("*.py")):
            for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
                if pattern.search(line):
                    offenders.append(f"{path}:{number}: {line.strip()}")
    assert offenders == [], (
        "a producer in this tree writes a float into a producing position; "
        "rule J4 (ADR-030) would refuse it at runtime:\n" + "\n".join(offenders))


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