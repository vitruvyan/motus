"""No caller-supplied protocol may be satisfied by an object that answers
everything — at EVERY boundary, not at the one where it was noticed.

This file exists because of a specific failure of method rather than of code.
An adversarial round found a run reporting `completed` against a commitment log
that recorded nothing, and the repair validated the concrete type that log
returns. That closed the site. **It did not close the class**, and the founder
said so before the second site was found: the primary evidence path — the sink —
had the same hole, and a `Mock()` there produced status `completed` with
evidence `persisted` and nothing written anywhere.

So the rule is enforced here over the whole surface, and a new protocol that
does not appear below is a gap in this file rather than a decision.
"""

from __future__ import annotations

import types
from datetime import datetime, timezone
from unittest.mock import MagicMock, Mock

import pytest

from vitruvyan_motus import (
    Fact, GraphSpec, InMemoryTraceSink, Runtime, State,
)

NOW = datetime(2026, 8, 14, tzinfo=timezone.utc)

SPEC = GraphSpec.from_dict({
    "schema_version": "1.0.0", "name": "standins", "version": "1.0.0",
    "entry": "a", "nodes": [{"name": "a", "effect_class": "pure"}],
    "transitions": {"a": {"kind": "terminal"}},
})


def _nodes():
    return {"a": lambda state: state.with_fact(Fact("k", 1, "test", NOW))}


#: Every caller-supplied protocol the Runtime accepts. A new one belongs here.
#:
#: Each entry wires the double into ONE boundary and everything else real. The
#: first version passed the same double as both the commitment log and the
#: witness, so the log's check fired and the witness's was never reached — a
#: mutation probe removing it survived. A test that asserts the right sentence
#: while exercising the wrong thing is this project's most repeated defect, and
#: it took a probe to notice it here too.
BOUNDARIES = [
    ("sink", lambda double, tmp: {"sink": double}),
    ("commitments", lambda double, tmp: {"commitments": double}),
    ("witness", lambda double, tmp: {"commitments": _real_log(tmp),
                                     "witness": double}),
]


def _real_log(tmp_path):
    from vitruvyan_motus.commitlog import CommitmentLog
    return CommitmentLog(tmp_path, tenant="acme", writer_id="w1", fsync=False,
                         witness_deadline=5.0)


@pytest.mark.parametrize(("name", "wire"), BOUNDARIES, ids=[n for n, _ in BOUNDARIES])
@pytest.mark.parametrize("double", [Mock, MagicMock], ids=["Mock", "MagicMock"])
def test_no_boundary_accepts_an_object_that_answers_everything(
        name, wire, double, tmp_path):
    with pytest.raises(TypeError, match="no protocol declares"):
        Runtime(SPEC, _nodes(), **wire(double(), tmp_path))


def test_the_sink_was_the_site_that_stayed_open(monkeypatch):
    """Named on its own because it is the one that mattered: the sink is the
    primary evidence path and has been there since 0.1, while the commitment
    log is optional and one release old.

    Before this rule, the run below reported `completed` with evidence
    `persisted`."""
    with pytest.raises(TypeError, match="no protocol declares"):
        Runtime(SPEC, _nodes(), sink=Mock())


# -- what the rule must NOT refuse -----------------------------------------

def test_a_real_implementation_passes():
    result = Runtime(SPEC, _nodes(), sink=InMemoryTraceSink()).run(
        State.empty("x"), run_id="r1")
    assert result.status == "completed"
    assert str(result.evidence) in ("persisted", "EvidenceStatus.PERSISTED")


def test_a_hand_rolled_duck_type_passes():
    """The rule asks whether an object answers to a name no protocol declares.
    A duck type written by hand answers only what it implements, which is the
    entire point of allowing duck types at all."""
    written: list[tuple] = []

    class Session:
        def write(self, records):
            written.append(tuple(records))

        def finish(self, *, complete):
            written.append(("finish", complete))

    class Sink:
        def open_run(self, header):
            return Session()

    result = Runtime(SPEC, _nodes(), sink=Sink()).run(State.empty("x"), run_id="r1")
    assert result.status == "completed"
    assert written, "the hand-rolled sink was never written to"


def test_a_double_that_declares_what_it_stands_in_for_passes():
    """`Mock(spec=...)` answers only the names of the thing it specs, so its
    author has declared the boundary. That is a bounded test double and not an
    accident, and refusing it would make the rule hostile to legitimate
    testing."""
    class Session:
        def write(self, records): ...
        def finish(self, *, complete): ...

    class Sink:
        def open_run(self, header): ...

    sink = Mock(spec=Sink)
    sink.open_run.return_value = Mock(spec=Session)
    result = Runtime(SPEC, _nodes(), sink=sink).run(State.empty("x"), run_id="r1")
    assert result.status == "completed"
    sink.open_run.assert_called_once()


def test_a_simple_namespace_passes():
    session = types.SimpleNamespace(write=lambda records: None,
                                    finish=lambda *, complete: None)
    sink = types.SimpleNamespace(open_run=lambda header: session)
    assert Runtime(SPEC, _nodes(), sink=sink).run(
        State.empty("x"), run_id="r1").status == "completed"


def test_the_session_a_sink_returns_is_checked_too():
    """The sink itself may be perfectly real and hand back a stand-in. Checking
    only the object the caller passed would close half the boundary.

    It surfaces as `SinkFailed` rather than `TypeError`, and that is the right
    shape: a bad argument is the caller's mistake, caught at construction; a
    sink misbehaving while a run is in flight is a sink failure, and the
    runtime already has a name for that."""
    from vitruvyan_motus import SinkFailed

    class Sink:
        def open_run(self, header):
            return Mock()

    with pytest.raises(SinkFailed, match="no protocol declares"):
        Runtime(SPEC, _nodes(), sink=Sink()).run(State.empty("x"), run_id="r1")
