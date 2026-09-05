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


SPEC_CHAIN = GraphSpec.from_dict({
    "schema_version": "1.0.0",
    "name": "lifecycle-chain",
    "version": "1.0.0",
    "entry": "first",
    "nodes": [
        {"name": "first", "effect_class": "pure", "writes_declared": ["first"]},
        {"name": "second", "effect_class": "pure",
         "reads_declared": ["first"], "writes_declared": ["second"]},
    ],
    "transitions": {"first": {"kind": "next", "to": "second"},
                    "second": {"kind": "terminal"}},
})


def _chain_nodes():
    return {
        "first": lambda s: s.with_fact(Fact("first", True, "test", NOW)),
        "second": lambda s: s.with_fact(Fact("second", True, "test", NOW)),
    }


def _nodes(*, fail: bool = False):
    def work(state: State) -> State:
        if fail:
            raise RuntimeError("the node refused")
        return state.with_fact(Fact("done", True, "test", NOW))
    return {"work": work}


def _log(tmp_path, **kw) -> CommitmentLog:
    kw.setdefault("witness_deadline", 5.0)
    return CommitmentLog(tmp_path, tenant="acme", writer_id="w1",
                         fsync=False, **kw)


def _kinds(log: CommitmentLog) -> list[tuple[str, str]]:
    return [(c.run_id, c.kind.value) for c in log.open_window._commitments]


class _RefusingLog:
    """A real log with a switch that makes the next `begin` fail the way a full
    disk does.

    These tests used to provoke the failure by committing the run_id first, so
    the log's own uniqueness rule refused it. That rule is gone — a retried job
    keeping its id is the ordinary case and refusing it rejected every retry
    before its first node — so the failure is now injected where it actually
    comes from: the write.
    """

    def __init__(self, inner: CommitmentLog) -> None:
        self._inner = inner
        self.refuse = False

    def begin(self, *args, **kwargs):
        if self.refuse:
            raise OSError(28, "No space left on device")
        return self._inner.begin(*args, **kwargs)

    def end(self, *args, **kwargs):
        return self._inner.end(*args, **kwargs)

    @property
    def witness_deadline(self):
        return self._inner.witness_deadline


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
    refusing = _RefusingLog(log)
    refusing.refuse = True
    ran: list[str] = []

    def work(state: State) -> State:
        ran.append("yes")
        return state.with_fact(Fact("done", True, "test", NOW))

    with pytest.raises(OSError):
        Runtime(SPEC, {"work": work}, sink=InMemoryTraceSink(),
                commitments=refusing).run(State.empty("x"), run_id="r1")
    assert ran == [], "the node executed despite an unwritable BEGIN"
    log.close()


def test_a_failed_begin_costs_one_run_and_not_the_runtime(tmp_path):
    """CRITICAL, and a defect this file had already fixed once by another route.

    With `_commit_begin` above the try, a BEGIN that could not be written raised
    from a point nothing cleaned up: `_start` had returned so its except was
    gone, the finally had not been entered so the claim was never released, and
    `_release_if_never_started` refuses to help because `handle.started` is
    already True. The Runtime was then wedged forever — every later run(),
    stream(), arun() and resume() raising "cannot execute overlapping runs".
    """
    log = _log(tmp_path)
    refusing = _RefusingLog(log)
    runtime = Runtime(SPEC, _nodes(), sink=InMemoryTraceSink(),
                      commitments=refusing)

    refusing.refuse = True
    with pytest.raises(OSError):
        runtime.run(State.empty("x"), run_id="taken")
    refusing.refuse = False

    assert runtime._running is False, "the Runtime is wedged"
    result = runtime.run(State.empty("x"), run_id="fresh")
    assert result.status == "completed"
    assert ("fresh", "end") in _kinds(log)
    log.close()


def test_a_failed_begin_still_finishes_the_sink_session(tmp_path):
    """guarantees.md §6: without `finish`, a session cannot tell in flight from
    abandoned forever. The wedge left a required sink's session open."""
    log = _log(tmp_path)
    finished: list[bool] = []

    class _Session:
        def write(self, records) -> None:
            pass

        def finish(self, *, complete: bool) -> None:
            finished.append(complete)

    class _Sink:
        def open_run(self, header):
            return _Session()

    refusing = _RefusingLog(log)
    refusing.refuse = True
    runtime = Runtime(SPEC, _nodes(), sink=_Sink(), commitments=refusing)
    with pytest.raises(OSError):
        runtime.run(State.empty("x"), run_id="taken")
    assert finished == [False], "the sink session was left open forever"
    log.close()


@pytest.mark.parametrize("surface", ["run", "stream", "cancel"])
def test_a_failed_end_costs_the_result_and_not_the_runtime(tmp_path, surface):
    """CRITICAL, the second half of the same defect and on the other side.

    `_commit_end` re-raises when nothing else is in flight — a clean run whose
    END could not be written is a failure the caller must hear about — and from
    ABOVE the lifecycle release that exception escaped before `_running` was
    set back to False. The instance was wedged forever, through every entry
    point and through cooperative cancellation alike, while the persisted
    account stayed perfectly honest.
    """
    log = _log(tmp_path)

    class _EndRefuses:
        def __init__(self, inner: CommitmentLog) -> None:
            self._inner = inner

        def begin(self, *a, **kw):
            return self._inner.begin(*a, **kw)

        def end(self, *a, **kw):
            raise OSError(28, "No space left on device")

    runtime = Runtime(SPEC, _nodes(), sink=InMemoryTraceSink(),
                      commitments=_EndRefuses(log))
    with pytest.raises(OSError):
        if surface == "run":
            runtime.run(State.empty("x"), run_id="r-boom")
        elif surface == "stream":
            driver = runtime.stream(State.empty("x"), run_id="r-boom")
            for _ in driver:
                pass
        else:
            driver = runtime.stream(State.empty("x"), run_id="r-boom")
            next(driver)
            runtime.cancel("operator")
            for _ in driver:
                pass

    assert runtime._running is False, "the Runtime is wedged"
    log.close()

    # and the SAME instance runs again, with a log that works
    good = _log(tmp_path / "second")
    result = Runtime(SPEC, _nodes(), sink=InMemoryTraceSink(),
                     commitments=good).run(State.empty("x"), run_id="r-ok")
    assert result.status == "completed"
    good.close()


def test_a_log_that_records_nothing_is_refused_before_a_node_runs(tmp_path):
    """A Mock has every attribute, satisfies any Protocol, accepts every call
    and returns another Mock. A run reported `completed` with an audit trail
    that was never written, indistinguishable from one that was.

    Caught by the return-value check: a log must hand back the Commitment it
    wrote, and a Mock hands back another Mock."""
    from unittest.mock import Mock

    ran: list[str] = []

    def work(state: State) -> State:
        ran.append("yes")
        return state.with_fact(Fact("done", True, "test", NOW))

    with pytest.raises(TypeError, match="must return the Commitment"):
        Runtime(SPEC, {"work": work}, sink=InMemoryTraceSink(),
                commitments=Mock()).run(State.empty("x"), run_id="r1")
    assert ran == [], "a node executed against a log that records nothing"


def test_a_hand_rolled_log_that_returns_nothing_is_still_refused(tmp_path):
    """The general rule catches an object that answers everything. It does not
    catch a deliberate stand-in that answers only the protocol — so the
    return-value check that #82 added stays, and this is what still needs it."""
    ran: list[str] = []

    def work(state: State) -> State:
        ran.append("yes")
        return state.with_fact(Fact("done", True, "test", NOW))

    class _RecordsNothing:
        def begin(self, *args, **kwargs):
            return None

        def end(self, *args, **kwargs):
            return None

    with pytest.raises(TypeError, match="must return the Commitment"):
        Runtime(SPEC, {"work": work}, sink=InMemoryTraceSink(),
                commitments=_RecordsNothing()).run(State.empty("x"), run_id="r1")
    assert ran == [], "a node executed against a log that records nothing"


def test_an_abandoned_stream_leaves_a_trace_and_a_log_that_agree(tmp_path):
    """A driver dropped mid-run leaves a BEGIN with no END — and the TRACE is
    incomplete in exactly the same way, with no terminal record and no root.

    The two agree, which is what makes it honest rather than a discrepancy: the
    evidence and the commitment both say this execution left no completion.
    ADR-020 decision 3 states the residual class as *a run that reached no
    terminal record*, and lists this case as its second member alongside
    process death. This test is where that correction came from: the ADR said
    "a process killed between the two writes" until this run produced an
    unpaired BEGIN with no crash anywhere. It stays here so the class keeps
    being what the tests can produce, not what the ADR can imagine.
    """
    log = _log(tmp_path)
    runtime = Runtime(SPEC, _nodes(), sink=InMemoryTraceSink(), commitments=log)
    driver = runtime.stream(State.empty("x"), run_id="r-abandoned")
    next(driver)
    next(driver)
    trace = driver.trace
    del driver

    assert _kinds(log) == [("r-abandoned", "begin")]
    assert trace.root is None
    assert trace.records[-1]["kind"] not in ("run_completed", "run_failed",
                                             "run_cancelled")
    assert runtime._running is False
    log.close()


def test_a_drained_stream_pairs_exactly_once(tmp_path):
    log = _log(tmp_path)
    runtime = Runtime(SPEC, _nodes(), sink=InMemoryTraceSink(), commitments=log)
    driver = runtime.stream(State.empty("x"), run_id="r1")
    for _ in driver:
        pass
    driver.close()
    driver.close()
    import gc
    gc.collect()
    assert _kinds(log) == [("r1", "begin"), ("r1", "end")]
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


def test_a_witness_without_a_deadline_is_refused_at_construction(tmp_path):
    """ADR-021 decision 3 says the deadline is part of the configuration, and
    for two commits it was part of the docstring only. Refused at construction
    rather than on the hot path of the first real decision."""
    log = CommitmentLog(tmp_path, tenant="acme", writer_id="w1", fsync=False)
    with pytest.raises(ValueError, match="no witness_deadline"):
        Runtime(SPEC, _nodes(), commitments=log, witness=_Notary())
    log.close()


def test_a_witness_that_hangs_is_abandoned_at_the_deadline(tmp_path):
    """CRITICAL. Without a deadline a slow witness stops the writer for as long
    as it likes — the literal opposite of "it never blocks the run"."""
    import time

    class _Hangs:
        def acknowledge(self, commitment: bytes):
            time.sleep(30)
            raise AssertionError("should never be reached")

    log = _log(tmp_path, witness_deadline=0.2)
    started = time.perf_counter()
    result = Runtime(SPEC, _nodes(), sink=InMemoryTraceSink(),
                     commitments=log, witness=_Hangs()).run(
        State.empty("x"), run_id="r1")
    elapsed = time.perf_counter() - started

    assert result.status == "completed"
    assert elapsed < 5, f"the run waited {elapsed:.1f}s on a hanging witness"
    assert log.open_window._commitments[0].mode is AssuranceMode.LOCAL
    log.close()


def test_a_witness_that_calls_back_into_its_own_log_does_not_deadlock(tmp_path):
    """CRITICAL. `_ask_witness` runs while the writer's lock is held, and the
    lock is not reentrant, so a witness that reenters wedged the process
    permanently. RLock would not have fixed it: it excuses the same thread and
    deadlocks identically when the reentry arrives on a new one."""
    import time

    log = _log(tmp_path, witness_deadline=0.3)

    class _Reenters:
        def acknowledge(self, commitment: bytes):
            log.begin("reentrant", at="2026-08-12T00:00:00Z", nonce="re")
            return None

    started = time.perf_counter()
    result = Runtime(SPEC, _nodes(), sink=InMemoryTraceSink(),
                     commitments=log, witness=_Reenters()).run(
        State.empty("x"), run_id="r1")
    elapsed = time.perf_counter() - started

    assert result.status == "completed"
    assert elapsed < 5, f"the reentrant witness held the process {elapsed:.1f}s"
    assert log.open_window._commitments[0].mode is AssuranceMode.LOCAL
    log.close()


def test_a_witness_may_not_swallow_the_operators_interrupt(tmp_path):
    """`except BaseException` consumed KeyboardInterrupt and SystemExit — the
    operator asking the process to stop, not a witness misbehaving. It took
    away the one recovery mechanism available for a hung run."""
    class _Interrupts:
        def acknowledge(self, commitment: bytes):
            raise KeyboardInterrupt

    log = _log(tmp_path)
    with pytest.raises(KeyboardInterrupt):
        Runtime(SPEC, _nodes(), sink=InMemoryTraceSink(),
                commitments=log, witness=_Interrupts()).run(
            State.empty("x"), run_id="r1")
    log.close()


def test_a_nonce_cannot_be_reused_in_a_window(tmp_path):
    """A nonce is used once by definition. Reusing one lets an acknowledgement
    captured for an earlier commitment be replayed against a later one, which
    an agent reproduced across a store restore."""
    log = _log(tmp_path)
    log.begin("r1", at="2026-08-12T00:00:00Z", nonce="same")
    with pytest.raises(ValueError, match="already used"):
        log.begin("r2", at="2026-08-12T00:00:00Z", nonce="same")
    log.close()


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
    assert proof.mode == "local"
    assert json.loads(json.dumps(proof.to_dict()))["proof"]
    log.close()


# -- what an adversarial round found after the first four ---------------------

def test_an_end_that_records_nothing_is_refused(tmp_path):
    """P1. `_commit_begin` validated the returned Commitment and `_commit_end`
    did not, so a store whose `end()` returned None without persisting anything
    let `run()` report `completed` while the durable account held only a BEGIN.

    The asymmetry survived a round because each half was read on its own and
    each half was correct on its own."""
    log = _log(tmp_path)

    class _NoOpEnd:
        def begin(self, *args, **kwargs):
            return log.begin(*args, **kwargs)

        def end(self, *args, **kwargs):
            return None

    runtime = Runtime(SPEC, _nodes(), sink=InMemoryTraceSink(),
                      commitments=_NoOpEnd())
    with pytest.raises(TypeError, match="not a Commitment"):
        runtime.run(State.empty("x"), run_id="r1")
    assert _kinds(log) == [("r1", "begin")]
    assert runtime._running is False, "the Runtime is wedged"
    log.close()


def test_a_retried_run_id_reaches_the_log_twice(tmp_path):
    """The store's refusal of a repeated run_id was invisible until a run hit
    it: a retried job was rejected BEFORE its first node, by the audit layer,
    for keeping the id that identifies it. `JsonlTraceSink` supports exactly
    this (test_the_same_run_id_twice_keeps_both_accounts) and the two must not
    disagree about what a legal run is."""
    log = _log(tmp_path)
    runtime = Runtime(SPEC, _nodes(fail=True), sink=InMemoryTraceSink(),
                      commitments=log)
    with pytest.raises(NodeFailed):
        runtime.run(State.empty("x"), run_id="job-4711")

    result = Runtime(SPEC, _nodes(), sink=InMemoryTraceSink(),
                     commitments=log).run(State.empty("x"), run_id="job-4711")
    assert result.status == "completed"
    assert _kinds(log) == [("job-4711", "begin"), ("job-4711", "end"),
                           ("job-4711", "begin"), ("job-4711", "end")]
    log.close()


def test_a_hung_witness_does_not_hold_the_process_open(tmp_path):
    """P1, and the deadline test passed while this was broken.

    The witness ran on a `ThreadPoolExecutor` and the timeout path called
    `shutdown(wait=False)`, which reads as "detach it". It does not:
    `concurrent.futures` registers an interpreter-exit hook that JOINS every
    pool thread, and pool threads are not daemons. So `begin()` returned in
    0.25s, the deadline test went green, and the process then sat for the
    remaining 20 seconds at exit -- a witness that never returned held it open
    for good.

    "Never blocks the run" was true. "Never blocks the process" was the promise
    ADR-021 decision 3 actually makes, and only a test that measures the whole
    process can tell them apart -- which is why this one spawns one."""
    import pathlib
    import subprocess
    import sys
    import time

    root = pathlib.Path(__file__).resolve().parent.parent
    probe = (
        "import sys, time, tempfile;"
        "from vitruvyan_motus.commitlog import CommitmentLog;"
        "sloth = type('S', (), {'acknowledge': lambda self, c: time.sleep(120)})();"
        "log = CommitmentLog(tempfile.mkdtemp(), tenant='t', writer_id='w',"
        " fsync=False, witness_deadline=0.2);"
        "log.begin('r', at='2026-08-12T00:00:00Z', nonce='n', ask=sloth);"
        "log.close()"
    )
    started = time.monotonic()
    done = subprocess.run([sys.executable, "-c", probe], capture_output=True,
                          text=True, cwd=root, timeout=60,
                          env={"PYTHONPATH": str(root / "src"),
                               "PATH": "/usr/bin:/bin"})
    elapsed = time.monotonic() - started
    assert done.returncode == 0, done.stderr
    assert elapsed < 15, (
        f"the process took {elapsed:.1f}s to exit with a 0.2s witness "
        f"deadline and a witness that sleeps for 120s"
    )


# -- a resumed run, end to end (ADR-023) -------------------------------------

def test_a_resumed_run_links_to_the_segment_it_continued(tmp_path):
    """The measurement that produced ADR-023: without this, the durable account
    of a crash-and-resume is an unpaired BEGIN followed by an unrelated pair
    under a different run_id, with nothing connecting them -- while the trace
    documents connect them perfectly well.

    It matters twice. An auditor holding only the commitments can now follow
    the chain; and ADR-020's unpaired BEGIN, whose value depends on being rare,
    stops being manufactured anonymously by every resume."""
    from vitruvyan_motus.replay import ReplayEngine, TraceBundle

    log = _log(tmp_path)
    runtime = Runtime(SPEC_CHAIN, _chain_nodes(), sink=InMemoryTraceSink(),
                      commitments=log)
    driver = runtime.stream(State.empty("x"), run_id="seg-1")
    next(driver)
    next(driver)
    segment_one = driver.trace
    del driver                          # the crash: no terminal record, no root

    assert segment_one.root is None
    assert _kinds(log) == [("seg-1", "begin")]

    resumed = ReplayEngine(TraceBundle(SPEC_CHAIN, segment_one)).resume(
        Runtime(SPEC_CHAIN, _chain_nodes(), sink=InMemoryTraceSink(),
                commitments=log),
        run_id="seg-2")

    written = log.open_window._commitments
    link = written[1].continues
    assert link is not None, "the resumed BEGIN carries no link to what it continued"
    assert link.run_id == "seg-1"
    assert (link.writer_id, link.sequence) == ("w1", written[0].sequence)
    assert link.bundle_fingerprint == resumed.trace.run["resume"]["bundle_fingerprint"]
    assert written[0].continues is None, "a fresh run must carry no link"
    log.close()


def test_a_fresh_run_carries_no_link(tmp_path):
    log = _log(tmp_path)
    Runtime(SPEC, _nodes(), sink=InMemoryTraceSink(),
            commitments=log).run(State.empty("x"), run_id="r1")
    assert all(c.continues is None for c in log.open_window._commitments)
    log.close()
