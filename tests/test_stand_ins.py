"""What a stand-in at a protocol boundary does and does not prove — and why the
obvious repair was withdrawn.

A `Mock()` sink produces `status=completed` and `evidence=persisted` with
nothing written anywhere. That looks like a defect and **it is declared
behaviour**: ADR-016 defines `persisted` as *a required sink accepted every
record, terminal included*, and §6 says a sink that does not raise has accepted.
The property *persisted implies durable* was never on offer and **cannot be
checked from inside the process** — which is what #73 is about.

A rule was written to refuse objects that "answer everything", and an
adversarial round killed it: it refused `xmlrpc.client.ServerProxy` from the
standard library, a lazy-loading sink, and a failover proxy — every
`__getattr__`-forwarding idiom — while `Mock(spec=...)` and `create_autospec`,
which the stdlib docs *recommend over bare Mock*, sailed through it. It refused
more legitimate code than stand-ins.

These tests pin the limit so that the next person to notice it finds this file
before writing the same rule again.
"""

from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import Mock, create_autospec

import pytest

from vitruvyan_motus import Fact, GraphSpec, InMemoryTraceSink, Runtime, State

NOW = datetime(2026, 8, 14, tzinfo=timezone.utc)

SPEC = GraphSpec.from_dict({
    "schema_version": "1.0.0", "name": "standins", "version": "1.0.0",
    "entry": "a", "nodes": [{"name": "a", "effect_class": "pure"}],
    "transitions": {"a": {"kind": "terminal"}},
})


def _nodes():
    return {"a": lambda state: state.with_fact(Fact("k", 1, "test", NOW))}


def test_a_sink_that_writes_nothing_still_reports_persisted(tmp_path):
    """**The limit, asserted rather than described.** `persisted` means the sink
    accepted every record, not that anything reached a disk. A stand-in accepts
    everything, so it reports `persisted` — and so would a real sink whose
    `write` is `pass`, which is the case that shows this is not about doubles.

    Closing this needs #73, and #73 needs the sink to say something back. Do not
    close it by trying to recognise stand-ins: that was tried and it refused
    `xmlrpc.client.ServerProxy`."""
    class WritesNothing:
        def open_run(self, header):
            return self

        def write(self, records):
            pass

        def finish(self, *, complete):
            pass

    result = Runtime(SPEC, _nodes(), sink=WritesNothing()).run(
        State.empty("x"), run_id="r1")
    assert result.status == "completed"
    assert str(result.evidence) in ("persisted", "EvidenceStatus.PERSISTED")


@pytest.mark.parametrize("double", ["bare", "spec", "autospec"])
def test_every_kind_of_double_reaches_the_same_place(double):
    """The rule that was withdrawn caught only the first of these. The stdlib
    documentation recommends the other two, so a team following its advice
    reproduced the case the rule was written for while a bare Mock — the least
    likely thing in production code — was the only one it stopped."""
    class Session:
        def write(self, records): ...
        def finish(self, *, complete): ...

    class Sink:
        def open_run(self, header): ...

    if double == "bare":
        sink = Mock()
    elif double == "spec":
        sink = Mock(spec=Sink)
        sink.open_run.return_value = Mock(spec=Session)
    else:
        sink = create_autospec(Sink, instance=True)
        sink.open_run.return_value = create_autospec(Session, instance=True)

    result = Runtime(SPEC, _nodes(), sink=sink).run(State.empty("x"), run_id="r1")
    assert result.status == "completed"


def test_a_forwarding_proxy_is_a_legitimate_sink(tmp_path):
    """What the withdrawn rule refused, and the reason it had to go.

    Resolving the target at call time is how a lazy connection, a shard router,
    a failover wrapper and an RPC stub are written — `xmlrpc.client.ServerProxy`
    in the standard library has exactly this shape. A rule that refuses it
    refuses more working code than it protects."""
    from vitruvyan_motus.sinks import JsonlTraceSink

    inner = JsonlTraceSink(tmp_path, fsync=False)

    class LazyProxy:
        def __getattr__(self, name):
            def call(*args, **kwargs):
                return getattr(inner, name)(*args, **kwargs)
            return call

    result = Runtime(SPEC, _nodes(), sink=LazyProxy()).run(
        State.empty("x"), run_id="r1")
    assert result.status == "completed"
    assert list(tmp_path.rglob("*.jsonl")), "the proxy's target wrote nothing"


def test_the_commitment_log_check_is_a_different_rule_and_it_stays(tmp_path):
    """#82's check is not the withdrawn one and does not share its fate. It
    validates the CONCRETE TYPE the log returns, which is checkable because
    something concrete comes back — and it is why a log that records nothing
    cannot let a run report `completed`. `TraceRunSink.write` returns None, so
    the same repair was never available for the sink."""
    class RecordsNothing:
        def begin(self, *args, **kwargs):
            return None

        def end(self, *args, **kwargs):
            return None

    with pytest.raises(TypeError, match="must return the Commitment"):
        Runtime(SPEC, _nodes(), sink=InMemoryTraceSink(),
                commitments=RecordsNothing()).run(State.empty("x"), run_id="r1")
