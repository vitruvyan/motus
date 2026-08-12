"""BEGIN and END around a real run, and what happens when they cannot be paired.

This is the only Motus code on the hot path of every run, so most of these
assert what happens when something goes WRONG: a witness that hangs, a log that
refuses, a run that fails, a driver that is dropped without being advanced.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone

import pytest

from vitruvyan_motus import (
    Fact, GraphSpec, InMemoryTraceSink, NodeFailed, Runtime, State,
)
from vitruvyan_motus.commitlog import CommitmentLog
from vitruvyan_motus.commitments import AssuranceMode, CommitmentKind, WitnessAck

NOW = datetime(2026, 8, 12, tzinfo=timezone.utc)

SPEC = GraphSpec.from_dict({
    "schema_version": "1.0.0",
    "name": "lifecycle",
    "version": "1.0.0",
    "entry": "work",
    "nodes": [{"name": "work", "effect_class": "pure"}],
    "transitions": {"work": {"kind": "terminal"}},
})


def _nodes(*, fail: bool = False):
    def work(state: State) -> State:
        if fail:
            raise RuntimeError("the node refused")
        return state.with_fact(Fact("done", True, "test", NOW))
    return {"work": work}


def _log(tmp_path, **kw) -> CommitmentLog:
    return CommitmentLog(tmp_path, tenant="acme", writer_id="w1",
                         fsync=False, **kw)


def _kinds(log: CommitmentLog) -> list[tuple[str, str]]:
    return [(c.run_id, c.kind.value) for c in log.open_window._commitments]


# -- the pair ---------------------------------------------------------------

def test_a_completed_run_leaves_a_begin_and_an_end(tmp_path):
    log = _log(tmp_path)
    result = Runtime(SPEC, _nodes(), sink=InMemoryTraceSink(),
                     commitments=log).run(State.empty("x"), run_id="r1")

    assert _kinds(log) == [("r1", "begin"), ("r1", "end")]
    end = log.open_window._commitments[1]
    assert end.root == result.trace.root
    assert end.outcome == "run_completed"
    log.close()


def test_a_failed_run_still_leaves_an_end(tmp_path):
    """END is written on EVERY terminal path. If ordinary failures produced the
    same shape as suppression, BEGIN-without-END would be noise."""
    log = _log(tmp_path)
    with pytest.raises(NodeFailed):
        Runtime(SPEC, _nodes(fail=True), sink=InMemoryTraceSink(),
                commitments=log).run(State.empty("x"), run_id="r-fail")

    assert _kinds(log) == [("r-fail", "begin"), ("r-fail", "end")]
    assert log.open_window._commitments[1].outcome == "run_failed"
    log.close()


def test_the_begin_precedes_the_first_node(tmp_path):
    """The property the two phases exist for. A node that inspects the log
    while it runs must already see its own BEGIN durably there."""
    log = _log(tmp_path)
    seen: list[list[tuple[str, str]]] = []

    def work(state: State) -> State:
        seen.append(_kinds(log))
        return state.with_fact(Fact("done", True, "test", NOW))

    Runtime(SPEC, {"work": work}, sink=InMemoryTraceSink(),
            commitments=log).run(State.empty("x"), run_id="r1")
    assert seen == [[("r1", "begin")]]
    log.close()


def test_a_stream_dropped_before_it_advances_commits_nothing(tmp_path):
    """A driver created and never advanced executed no node. A BEGIN written
    at `_start` would stand alone forever for a run that never happened."""
    log = _log(tmp_path)
    runtime = Runtime(SPEC, _nodes(), sink=InMemoryTraceSink(), commitments=log)
    driver = runtime.stream(State.empty("x"), run_id="r-dropped")
    del driver
    assert _kinds(log) == []
    log.close()


# -- when the log refuses ---------------------------------------------------

def test_a_run_whose_begin_cannot_be_written_does_not_execute(tmp_path):
    """A run whose BEGIN can vanish has no execution continuity to prove, so
    it must not run at all."""
    log = _log(tmp_path)
    ran: list[str] = []

    def work(state: State) -> State:
        ran.append("yes")
        return state.with_fact(Fact("done", True, "test", NOW))

    log.begin("r1", at="2026-08-12T00:00:00Z", nonce="n")   # same run_id
    with pytest.raises(ValueError, match="already committed"):
        Runtime(SPEC, {"work": work}, sink=InMemoryTraceSink(),
                commitments=log).run(State.empty("x"), run_id="r1")
    assert ran == [], "the node executed despite an unwritable BEGIN"
    log.close()


def test_an_end_that_cannot_be_written_does_not_mask_the_runs_own_error(tmp_path):
    """Raising over an in-flight exception replaces a diagnosis with a
    symptom. The missing END is itself the record of what happened."""
    log = _log(tmp_path)

    class _EndRefuses:
        """A log whose END write fails, which `__slots__` forbids faking by
        assignment — so it is a real object the Runtime duck-types, exactly as
        an embedder's own implementation would be."""

        def __init__(self, inner: CommitmentLog) -> None:
            self._inner = inner

        def begin(self, *a, **kw):
            return self._inner.begin(*a, **kw)

        def end(self, *a, **kw):
            raise OSError(28, "No space left on device")

    with pytest.raises(NodeFailed):                     # NOT OSError
        Runtime(SPEC, _nodes(fail=True), sink=InMemoryTraceSink(),
                commitments=_EndRefuses(log)).run(State.empty("x"), run_id="r-fail")
    assert _kinds(log) == [("r-fail", "begin")]
    log.close()


def test_an_end_that_cannot_be_written_DOES_raise_on_a_clean_run(tmp_path):
    """The other half of the same rule. With nothing in flight to mask, a
    commitment that cannot be written is a failure the caller must hear about
    — swallowing it would lose continuity in silence."""
    log = _log(tmp_path)

    class _EndRefuses:
        def __init__(self, inner: CommitmentLog) -> None:
            self._inner = inner

        def begin(self, *a, **kw):
            return self._inner.begin(*a, **kw)

        def end(self, *a, **kw):
            raise OSError(28, "No space left on device")

    with pytest.raises(OSError):
        Runtime(SPEC, _nodes(), sink=InMemoryTraceSink(),
                commitments=_EndRefuses(log)).run(State.empty("x"), run_id="r-ok")
    log.close()


# -- the witness ------------------------------------------------------------

class _Notary:
    def __init__(self, *, behaviour: str = "ack") -> None:
        self.behaviour = behaviour
        self.calls = 0

    def acknowledge(self, commitment: bytes) -> WitnessAck | None:
        import hashlib
        self.calls += 1
        if self.behaviour == "raise":
            raise ConnectionError("the notary is unreachable")
        if self.behaviour == "none":
            return None
        leaf = "sha256:" + hashlib.sha256(b"\x00" + commitment).hexdigest()
        if self.behaviour == "wrong":
            leaf = "sha256:" + "f" * 64
        return WitnessAck(witness_id="notary.example", commitment=leaf,
                          position=self.calls, acknowledged_at="2026-08-12T00:00:00Z",
                          signature="sig")


def test_a_witnessed_run_reaches_the_witnessed_mode(tmp_path):
    log = _log(tmp_path)
    notary = _Notary()
    Runtime(SPEC, _nodes(), sink=InMemoryTraceSink(),
            commitments=log, witness=notary).run(State.empty("x"), run_id="r1")

    begin = log.open_window._commitments[0]
    assert notary.calls == 1
    assert begin.mode is AssuranceMode.WITNESSED
    assert begin.witness is not None and begin.witness.witness_id == "notary.example"
    log.close()


@pytest.mark.parametrize("behaviour", ["raise", "none", "wrong"])
def test_a_witness_that_fails_downgrades_and_never_blocks(tmp_path, behaviour):
    """ADR-021 decision 3: an acknowledgement, None, or a raise all mean the
    same thing to the runtime — continue, at whatever mode was reached. An
    option that let a witness stop a run would be enabled by somebody who had
    not imagined the outage."""
    log = _log(tmp_path)
    notary = _Notary(behaviour=behaviour)
    result = Runtime(SPEC, _nodes(), sink=InMemoryTraceSink(),
                     commitments=log, witness=notary).run(State.empty("x"), run_id="r1")

    assert result.status == "completed"
    begin = log.open_window._commitments[0]
    assert begin.mode is AssuranceMode.LOCAL
    assert begin.witness is None
    log.close()


def test_a_witness_without_a_log_is_refused_at_construction(tmp_path):
    with pytest.raises(ValueError, match="nothing to acknowledge"):
        Runtime(SPEC, _nodes(), witness=_Notary())


# -- the proof, end to end --------------------------------------------------

def test_a_sealed_run_proves_its_own_root(tmp_path):
    """The whole point, in one test: a run happened, its evidence has a root,
    and a proof binds that root to a checkpoint anybody can anchor."""
    from vitruvyan_motus.commitments import verify_inclusion

    log = _log(tmp_path)
    result = Runtime(SPEC, _nodes(), sink=InMemoryTraceSink(),
                     commitments=log).run(State.empty("x"), run_id="r1")
    checkpoint = log.seal("2026-08-12T10:00:00Z")

    proof = log.proof_for("r1", CommitmentKind.END, checkpoint.index)
    assert proof.commitment.root == result.trace.root
    assert verify_inclusion(proof.commitment, proof.path, checkpoint.window_root)
    assert json.loads(json.dumps(proof.to_dict()))["mode"] == "local"
    log.close()
