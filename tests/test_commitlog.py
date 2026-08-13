"""The store's properties, asserted rather than described.

Several of these assert a REFUSAL. A store that quietly continues from a
restored backup has removed the guarantee without removing the sentence.
"""

from __future__ import annotations

import json

import pytest

from vitruvyan_motus.commitlog import (
    STORE_FORMAT, CommitmentLog, CommitmentLogBusy, CommitmentLogFork,
    InclusionProof,
)
from vitruvyan_motus.commitments import (
    Checkpoint, CommitmentKind, merkle_root, verify_inclusion,
)

AT = "2026-08-12T09:14:00Z"
ROOT = "sha256:" + "a" * 64


def _log(tmp_path, tenant="acme", writer="w1", **kw) -> CommitmentLog:
    return CommitmentLog(tmp_path, tenant=tenant, writer_id=writer,
                         fsync=False, **kw)


def _run(log: CommitmentLog, name: str) -> None:
    log.begin(name, at=AT, nonce=f"n-{name}")
    log.end(name, root=ROOT, outcome="completed", at=AT, nonce=f"n-{name}-end")


# -- durability -------------------------------------------------------------

def test_a_begin_is_on_disk_when_begin_returns(tmp_path):
    """The whole value of two phases is that the first left the operator's
    reach before the outcome was known. A BEGIN in a buffer has left nothing."""
    log = CommitmentLog(tmp_path, tenant="acme", writer_id="w1", fsync=True)
    log.begin("r1", at=AT, nonce="n1")
    path = log.directory / "window-000000.jsonl"
    stored = [json.loads(line) for line in path.read_text().splitlines() if line]
    assert len(stored) == 1 and stored[0]["c"]["kind"] == "begin"
    assert stored[0]["c"]["run_id"] == "r1"
    assert log.durable is True
    log.close()


def test_the_log_reopens_where_it_left_off(tmp_path):
    log = _log(tmp_path)
    _run(log, "r1")
    _run(log, "r2")
    log.close()

    again = _log(tmp_path)
    assert len(again.open_window) == 4
    assert again.open_window.next_sequence == 4
    again.begin("r3", at=AT, nonce="n3")
    assert again.open_window.next_sequence == 5
    again.close()


def test_a_second_holder_of_one_writers_chain_is_refused(tmp_path):
    """CRITICAL, found by two independent agents. Two holders wrote two
    commitments claiming sequence 0, and the chain then opened for nobody. A
    supervisor restart with the old instance not yet dead is ordinary."""
    first = _log(tmp_path)
    with pytest.raises(CommitmentLogBusy, match="another holder"):
        _log(tmp_path)
    first.close()
    second = _log(tmp_path)                 # released, so the next one may have it
    second.close()


def test_a_failed_write_leaves_nothing_in_memory(tmp_path):
    """CRITICAL. The first version appended to the window and THEN did the I/O,
    so a failed write left a phantom commitment that `seal()` hashed into a
    checkpoint the file could never reproduce -- and every proof from that
    window failed to verify with nothing raised anywhere."""
    log = _log(tmp_path)
    log.begin("r1", at=AT, nonce="n1")

    handle = log._open_handle()
    original = handle.write

    def explode(_: str) -> int:
        raise OSError(28, "No space left on device")

    handle.write = explode                                  # type: ignore[method-assign]
    with pytest.raises(OSError):
        log.begin("r2", at=AT, nonce="n2")
    handle.write = original                                 # type: ignore[method-assign]

    assert [c.run_id for c in log.open_window._commitments] == ["r1"]

    # And it does not carry on. Whether the bytes reached the file is exactly
    # what this process cannot know, so the next sequence it would issue is a
    # guess -- see test_an_uncertain_durable_write_makes_the_log_unusable.
    with pytest.raises(CommitmentLogFork, match="stopped being writable"):
        log.begin("r3", at=AT, nonce="n3")
    log.close()

    reopened = _log(tmp_path)
    reopened.begin("r3", at=AT, nonce="n3")
    checkpoint = reopened.seal(AT)
    proof = reopened.proof_for("r3", CommitmentKind.BEGIN, checkpoint.index)
    assert verify_inclusion(proof.commitment, proof.path, checkpoint.window_root)
    reopened.close()


def test_a_torn_line_with_non_ascii_content_truncates_to_the_byte(tmp_path):
    """CRITICAL. The first version measured the torn tail in CHARACTERS and
    subtracted it from a length in BYTES. One `é` left a dangling byte and the
    next restart could not parse the file at all, permanently."""
    log = _log(tmp_path)
    log.begin("北京客户-r1", at=AT, nonce="n1")
    log.close()

    path = log.directory / "window-000000.jsonl"
    intact = path.read_bytes()
    path.write_bytes(intact + '{"c":{"kind":"begin","run_id":"café-'.encode())

    again = _log(tmp_path)
    assert len(again.open_window) == 1
    assert path.read_bytes() == intact, "the torn tail was not fully removed"
    again.begin("r2", at=AT, nonce="n2")
    again.close()

    third = _log(tmp_path)                  # and the next restart still opens
    assert len(third.open_window) == 2
    third.close()


def test_a_torn_trailing_line_is_a_write_nobody_saw_finish(tmp_path):
    """`_append` fsyncs before returning, so a partial line can only be a write
    whose caller never proceeded — the run it would have covered had not
    started. Truncated, not guessed at."""
    log = _log(tmp_path)
    _run(log, "r1")
    log.close()

    path = log.directory / "window-000000.jsonl"
    path.write_text(path.read_text() + '{"kind":"begin","tenant":"acm')

    again = _log(tmp_path)
    assert len(again.open_window) == 2                 # the torn line is gone
    assert not path.read_text().endswith("acm")
    again.begin("r2", at=AT, nonce="n2")               # and the chain continues
    assert again.open_window.next_sequence == 3
    again.close()


# -- sealing and the next window -------------------------------------------

def test_sealing_publishes_a_checkpoint_and_opens_the_next_window(tmp_path):
    log = _log(tmp_path)
    _run(log, "r1")
    checkpoint = log.seal(AT)

    assert (log.directory / "checkpoint-000000.json").exists()
    assert checkpoint.count == 2 and checkpoint.index == 0
    assert log.open_window.index == 1
    assert log.open_window.previous == checkpoint.digest
    assert log.open_window.next_sequence == 2          # numbering is per WRITER
    log.close()


def test_numbering_survives_a_restart_across_a_seal(tmp_path):
    log = _log(tmp_path)
    _run(log, "r1")
    log.seal(AT)
    log.close()

    again = _log(tmp_path)
    assert again.open_window.index == 1
    assert again.open_window.next_sequence == 2
    again.begin("r2", at=AT, nonce="n2")
    assert again.open_window._commitments[0].sequence == 2
    again.close()


def test_an_empty_window_cannot_be_sealed(tmp_path):
    log = _log(tmp_path)
    with pytest.raises(ValueError, match="not evidence that nothing happened"):
        log.seal(AT)
    log.close()


# -- the proof --------------------------------------------------------------

def test_a_proof_verifies_against_the_sealed_window_root(tmp_path):
    log = _log(tmp_path)
    for i in range(5):
        _run(log, f"r{i}")
    checkpoint = log.seal(AT)

    proof = log.proof_for("r3", CommitmentKind.BEGIN, checkpoint.index)
    assert isinstance(proof, InclusionProof)
    assert proof.commitment.run_id == "r3"
    assert verify_inclusion(proof.commitment, proof.path, checkpoint.window_root)
    log.close()


def test_a_proof_carries_the_commitment_and_not_only_its_digest(tmp_path):
    """A verifier must recompute the leaf itself. Handed a bare digest it
    proves that SOME digest is in the tree, not that this run is."""
    log = _log(tmp_path)
    _run(log, "r1")
    checkpoint = log.seal(AT)
    body = log.proof_for("r1", CommitmentKind.END, checkpoint.index).to_dict()
    assert body["commitment"]["run_id"] == "r1"
    assert body["commitment"]["root"] == ROOT
    assert body["checkpoint_digest"] == checkpoint.digest
    log.close()


def test_a_proof_for_a_run_that_is_not_there_is_refused(tmp_path):
    log = _log(tmp_path)
    _run(log, "r1")
    checkpoint = log.seal(AT)
    with pytest.raises(KeyError):
        log.proof_for("never-ran", CommitmentKind.BEGIN, checkpoint.index)
    log.close()


# -- the fork ---------------------------------------------------------------

def test_a_restored_backup_is_reported_as_a_fork_not_repaired(tmp_path):
    """ADR-021: repairing it silently would let an operator choose which
    history is true by choosing which backup to restore."""
    log = _log(tmp_path)
    _run(log, "real-1")
    published = log.seal(AT)
    log.close()

    # the same window, resealed over a different run — a restored backup that
    # diverged before the seal
    (log.directory / "window-000000.jsonl").unlink()
    (log.directory / "checkpoint-000000.json").unlink()
    other = _log(tmp_path)
    _run(other, "different-1")
    other.seal(AT)

    with pytest.raises(CommitmentLogFork, match="disagrees"):
        other.check_against(published)
    other.close()


def test_a_store_behind_a_published_checkpoint_is_a_fork_too(tmp_path):
    log = _log(tmp_path)
    _run(log, "r1")
    published = log.seal(AT)
    ahead = Checkpoint(**{**published.to_dict(), "index": 7,
                          "previous": published.digest})
    with pytest.raises(CommitmentLogFork, match="absent here"):
        log.check_against(ahead)
    log.close()


def test_a_matching_checkpoint_is_not_a_fork(tmp_path):
    log = _log(tmp_path)
    _run(log, "r1")
    published = log.seal(AT)
    log.check_against(published)                        # must not raise
    log.close()


def test_a_directory_belonging_to_another_writer_is_refused(tmp_path):
    """Two writers sharing a chain is the ambiguity one-chain-per-writer
    exists to remove, and a sanitised directory name is not an identity."""
    log = _log(tmp_path)
    log.close()
    identity = log.directory / "identity.json"
    identity.write_text(json.dumps({"tenant": "acme", "writer_id": "someone-else"}))
    with pytest.raises(CommitmentLogFork, match="already belongs"):
        _log(tmp_path)


# -- identity ---------------------------------------------------------------

def test_the_verbatim_identity_is_stored_beside_the_sanitised_directory(tmp_path):
    log = _log(tmp_path, tenant="acme/prod", writer="worker #1")
    body = json.loads((log.directory / "identity.json").read_text())
    assert body == {"format": STORE_FORMAT, "tenant": "acme/prod",
                    "writer_id": "worker #1"}
    assert "/" not in log.directory.name                # the stem is sanitised
    log.close()


def test_two_writers_that_sanitise_alike_do_not_share_a_directory(tmp_path):
    a = _log(tmp_path, writer="worker/1")
    b = _log(tmp_path, writer="worker#1")
    assert a.directory != b.directory
    a.close(); b.close()


def test_a_blank_tenant_or_writer_is_refused(tmp_path):
    for kwargs in ({"tenant": "  "}, {"writer_id": ""}):
        with pytest.raises(ValueError, match="names its"):
            CommitmentLog(tmp_path, **{"tenant": "acme", "writer_id": "w1", **kwargs})


# -- what the round found, one test each ------------------------------------

def test_the_witness_acknowledgement_survives_the_disk(tmp_path):
    """HIGH. The ACK must stay OUT of the leaf digest -- it did not exist when
    the witness signed it -- but that is a different question from storing it.
    The first version answered both with no, which made EXECUTION_CONTINUITY
    unprovable for every witnessed run, 100% of the time."""
    from vitruvyan_motus.commitments import AssuranceMode, Commitment, WitnessAck

    log = _log(tmp_path)
    unwitnessed = Commitment(
        kind=CommitmentKind.BEGIN, tenant="acme", writer_id="w1", sequence=0,
        run_id="w-run", at=AT, nonce="wn")
    ack = WitnessAck(witness_id="notary.example", commitment=unwitnessed.leaf,
                     position=41, acknowledged_at=AT, signature="sig")
    live = log.begin("w-run", at=AT, nonce="wn", witness=ack)
    assert live.mode is AssuranceMode.WITNESSED
    checkpoint = log.seal(AT)
    log.close()

    again = _log(tmp_path)
    proof = again.proof_for("w-run", CommitmentKind.BEGIN, checkpoint.index)
    assert proof.commitment.witness is not None
    assert proof.commitment.witness.witness_id == "notary.example"
    assert proof.commitment.mode is AssuranceMode.WITNESSED
    assert proof.to_dict()["mode"] == "witnessed"
    assert proof.to_dict()["witness"]["witness_id"] == "notary.example"
    # and the ACK still does not move the leaf
    assert proof.commitment.leaf == unwitnessed.leaf
    assert verify_inclusion(proof.commitment, proof.path, checkpoint.window_root)
    again.close()


def test_a_retried_job_may_keep_its_run_id(tmp_path):
    """A round refused a repeated run_id, and it was refusing an ordinary case.

    Nothing in the contract or the schema requires run_id to be unique, and
    `JsonlTraceSink` keeps both accounts on purpose
    (test_the_same_run_id_twice_keeps_both_accounts). With no sealing interval
    shipped, the refusal was permanent for the life of the open window: every
    retry of a job that had already run was rejected before its first node.

    The defect it was fixing was real and it lives in `proof_for`, not here --
    two commitments of a kind for one run were both sealed and `proof_for`
    returned the first in file order with a proof that genuinely verified, a
    provable half of a self-contradiction. That is refused below, and the
    caller who means one of them names its sequence."""
    log = _log(tmp_path)
    log.begin("job-4711", at=AT, nonce="n1")
    log.end("job-4711", root=ROOT, outcome="failed", at=AT, nonce="e1")
    second = log.begin("job-4711", at=AT, nonce="n2")
    log.end("job-4711", root=ROOT, outcome="completed", at=AT, nonce="e2")

    checkpoint = log.seal(AT)
    assert checkpoint.count == 4, "an execution was dropped"

    with pytest.raises(CommitmentLogFork, match="sequence=<n>"):
        log.proof_for("job-4711", CommitmentKind.BEGIN, checkpoint.index)

    proof = log.proof_for("job-4711", CommitmentKind.BEGIN, checkpoint.index,
                          sequence=second.sequence)
    assert proof.commitment.sequence == second.sequence
    assert verify_inclusion(proof.commitment, proof.path, checkpoint.window_root)

    with pytest.raises(KeyError, match="at sequence 99"):
        log.proof_for("job-4711", CommitmentKind.END, checkpoint.index,
                      sequence=99)
    log.close()


def test_a_window_edited_after_sealing_is_refused_not_proved(tmp_path):
    """HIGH. Substituting one run id for another leaves count and range intact,
    so the checkpoint file never changes -- and the first version handed back a
    well-formed proof against a root the file no longer reproduces."""
    log = _log(tmp_path)
    _run(log, "real")
    _run(log, "other")
    checkpoint = log.seal(AT)
    log.close()

    path = log.directory / "window-000000.jsonl"
    path.write_bytes(path.read_bytes().replace(b'"real"', b'"forged"'))

    again = _log(tmp_path)
    with pytest.raises(CommitmentLogFork, match="no longer reproduces"):
        again.proof_for("forged", CommitmentKind.BEGIN, checkpoint.index)
    with pytest.raises(CommitmentLogFork, match="no longer reproduces"):
        again.check_against(checkpoint)
    again.close()


def test_the_chain_is_walked_and_a_broken_link_is_found(tmp_path):
    log = _log(tmp_path)
    _run(log, "r1"); first = log.seal(AT)
    _run(log, "r2"); log.seal(AT)
    assert log.verify_chain() == 2
    log.close()

    # an interior checkpoint replaced by a self-consistent forgery
    forged = json.loads((log.directory / "checkpoint-000000.json").read_text())
    forged["sealed_at"] = "2026-01-01T00:00:00Z"
    (log.directory / "checkpoint-000000.json").write_text(json.dumps(forged))

    # Caught at OPEN, before a single append. `verify_chain()` found this and
    # nothing called it, so a store whose chain had a hole in it opened
    # willingly and kept writing.
    with pytest.raises(CommitmentLogFork, match="links to"):
        _log(tmp_path)


def test_a_lost_checkpoint_is_a_fork_not_a_rewind(tmp_path):
    """HIGH. Deleting one checkpoint made `_recover` silently reopen the older
    window and re-issue a sequence a durable commitment in the NEXT window file
    already held -- the module's own stated reason for existing."""
    log = _log(tmp_path)
    _run(log, "r1")
    log.seal(AT)
    log.begin("r2", at=AT, nonce="n2")
    log.close()

    (log.directory / "checkpoint-000000.json").unlink()
    with pytest.raises(CommitmentLogFork, match="checkpoint was lost"):
        _log(tmp_path)


def test_a_half_written_checkpoint_is_present_and_refused(tmp_path):
    """HIGH. Writing in place left a third state -- present and corrupt --
    which no later open could recover from. It is written and renamed now, so
    a crash leaves it absent or whole; a corrupt one is somebody else's doing
    and is refused rather than crashed on."""
    log = _log(tmp_path)
    _run(log, "r1")
    log.seal(AT)
    log.close()

    path = log.directory / "checkpoint-000000.json"
    path.write_text(path.read_text()[: len(path.read_text()) // 2])
    with pytest.raises(CommitmentLogFork, match="present and unreadable"):
        _log(tmp_path)


def test_a_checkpoint_reaches_its_name_by_rename_and_not_by_copy(tmp_path):
    """Atomicity cannot be observed without a real crash, so this pins the
    MECHANISM and says so. A copy leaves the final path holding half a file for
    as long as the copy takes; a rename never does. The difference is invisible
    on a healthy machine and total on a dying one, which is exactly the class
    of property a test has to assert structurally or not at all."""
    import os as os_module

    log = _log(tmp_path)
    _run(log, "r1")

    renames: list[tuple[str, str]] = []
    real = os_module.replace

    def spy(src, dst, *a, **kw):
        renames.append((str(src), str(dst)))
        return real(src, dst, *a, **kw)

    os_module.replace = spy
    try:
        checkpoint = log.seal(AT)
    finally:
        os_module.replace = real

    target = str(log.directory / "checkpoint-000000.json")
    assert any(dst == target and src.endswith(".tmp") for src, dst in renames), (
        f"the checkpoint did not arrive by rename: {renames}")
    assert not list(log.directory.glob("*.tmp")), "a temporary file was left behind"
    assert checkpoint.index == 0
    log.close()


def test_a_checkpoint_is_never_written_over(tmp_path):
    log = _log(tmp_path)
    _run(log, "r1")
    log.seal(AT)
    log.close()

    again = _log(tmp_path)
    again.begin("r2", at=AT, nonce="n2")
    (again.directory / "checkpoint-000001.json").write_text("{}")
    with pytest.raises(CommitmentLogFork, match="already exists"):
        again.seal(AT)
    again.close()


def test_a_name_that_is_not_nfc_is_refused_rather_than_forked(tmp_path):
    """MEDIUM. Composed and decomposed `café` hash differently, so they opened
    two permanent chains for one logical writer with no signal at all --
    the one failure mode in this file that was silent."""
    import unicodedata
    decomposed = unicodedata.normalize("NFD", "café")
    with pytest.raises(ValueError, match="NFC"):
        CommitmentLog(tmp_path, tenant=decomposed, writer_id="w1", fsync=False)
    composed = unicodedata.normalize("NFC", "café")
    log = CommitmentLog(tmp_path, tenant=composed, writer_id="w1", fsync=False)
    log.close()


def test_the_directory_digest_is_wide_enough_to_mean_something(tmp_path):
    """MEDIUM. A 48-bit digest collided in 18 seconds on one core, and the
    consequence is a writer losing their identity rather than a trace losing a
    filename suffix."""
    from vitruvyan_motus.commitlog import _DIGEST_CHARS, _stem
    assert _DIGEST_CHARS >= 32
    assert len(_stem("acme").rsplit("-", 1)[1]) == _DIGEST_CHARS


def test_an_unreadable_identity_is_refused_as_a_fork(tmp_path):
    log = _log(tmp_path)
    log.close()
    (log.directory / "identity.json").write_text("{not json")
    with pytest.raises(CommitmentLogFork, match="cannot be read"):
        _log(tmp_path)


def test_garbage_in_the_middle_of_a_window_is_refused_by_name(tmp_path):
    log = _log(tmp_path)
    _run(log, "r1")
    log.close()
    path = log.directory / "window-000000.jsonl"
    lines = path.read_bytes().split(b"\n")
    path.write_bytes(lines[0] + b"\nnot-json\n" + b"\n".join(lines[1:]))
    with pytest.raises(CommitmentLogFork, match="line 2 is not a commitment"):
        _log(tmp_path)


# -- concurrency ------------------------------------------------------------

def test_concurrent_begins_never_share_a_sequence(tmp_path):
    import threading
    log = _log(tmp_path)
    start = threading.Barrier(8)

    def worker(i: int) -> None:
        start.wait()
        for j in range(15):
            log.begin(f"r-{i}-{j}", at=AT, nonce=f"n{i}{j}")

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    sequences = [c.sequence for c in log.open_window._commitments]
    assert sequences == list(range(120)), "a sequence was issued twice or skipped"

    stored = [json.loads(line) for line
              in (log.directory / "window-000000.jsonl").read_text().splitlines() if line]
    assert len(stored) == 120
    assert sorted(s["c"]["sequence"] for s in stored) == list(range(120))
    log.close()


# -- what an adversarial round found after the log was wired to the runtime --

def test_a_lost_interior_checkpoint_is_refused_at_open(tmp_path):
    """P1. Recovery read the HIGHEST checkpoint and resumed after it, so
    deleting an interior one left the store perfectly willing to open and
    append: window 2 continued a chain whose window 0 was unaccounted for.

    `verify_chain()` would have said so and nothing called it, which is the
    difference between a guarantee and a function that could have provided one.
    ADR-021 requires a lost window to be reported as a fork, and a fork
    reported only if somebody thinks to ask is not reported."""
    log = _log(tmp_path)
    _run(log, "r1"); log.seal(AT)
    _run(log, "r2"); log.seal(AT)
    log.close()

    (log.directory / "checkpoint-000000.json").unlink()
    with pytest.raises(CommitmentLogFork, match="a sealed window was lost"):
        _log(tmp_path)


def test_an_uncertain_durable_write_makes_the_log_unusable(tmp_path, monkeypatch):
    """P1, and the most damaging of the five.

    `fsync` raising AFTER the line reached the file left the in-memory window
    short by one while the file held it. The next append therefore re-issued
    that sequence, and the round reproduced sequences [0, 1, 1] on disk under a
    checkpoint that sealed two -- a store that had already lost the property it
    exists for, still accepting writes.

    The ordering rule -- disk first, memory after -- is what makes the failure
    survivable, and it is also what creates this window. The answer is not to
    guess: this process cannot know what reached the file, so it stops, and the
    reopen decides from the bytes."""
    log = CommitmentLog(tmp_path, tenant="acme", writer_id="w1", fsync=True)
    log.begin("r1", at=AT, nonce="n1")

    def boom(fd: int) -> None:
        raise OSError(5, "Input/output error")

    monkeypatch.setattr("vitruvyan_motus.commitlog.os.fsync", boom)
    with pytest.raises(OSError):
        log.begin("r2", at=AT, nonce="n2")
    monkeypatch.undo()

    with pytest.raises(CommitmentLogFork, match="stopped being writable"):
        log.begin("r3", at=AT, nonce="n3")
    with pytest.raises(CommitmentLogFork, match="stopped being writable"):
        log.seal(AT)
    log.close()

    # Recovery reads the file rather than a hopeful memory: whatever survived
    # of r2 decides where the sequence continues.
    reopened = CommitmentLog(tmp_path, tenant="acme", writer_id="w1", fsync=True)
    on_disk = [json.loads(line)["c"]["sequence"]
               for line in (reopened.directory / "window-000000.jsonl")
               .read_text().splitlines() if line.strip()]
    issued = reopened.begin("r3", at=AT, nonce="n3")
    assert issued.sequence == len(on_disk), (
        f"reopened at sequence {issued.sequence} over a file holding {on_disk}")
    assert sorted(on_disk) == list(range(len(on_disk))), (
        f"a sequence was issued twice: {on_disk}")
    checkpoint = reopened.seal(AT)
    reopened.close()
    again = CommitmentLog(tmp_path, tenant="acme", writer_id="w1", fsync=True)
    assert again.verify_chain() == 1
    assert checkpoint.count == len(on_disk) + 1
    again.close()


# -- the link between segments (ADR-023) ------------------------------------

BUNDLE = "bundle:sha256:" + "d" * 64


def test_a_continuation_names_its_predecessor_by_chain_coordinate(tmp_path):
    """The commitment log is the artefact an auditor walks and the only one an
    anchor covers, and until this it held no trace of a resume: an unpaired
    BEGIN, then an unrelated pair under a different run_id, with nothing
    joining them."""
    log = _log(tmp_path)
    log.begin("seg-1", at=AT, nonce="n1")            # crashed: no END follows
    resumed = log.begin("seg-2", at=AT, nonce="n2",
                        continues="seg-1", continues_fingerprint=BUNDLE)

    link = resumed.continues
    assert link is not None and link.resolved
    assert (link.writer_id, link.sequence) == ("w1", 0)
    assert link.run_id == "seg-1" and link.bundle_fingerprint == BUNDLE
    log.close()


def test_a_predecessor_this_store_never_saw_is_unresolved_not_denied(tmp_path):
    """The ordinary cause of a resume is a process that died, and the process
    that resumes is frequently a different writer on a different machine. A
    claim about a chain we do not hold is recorded as a claim; denying it would
    be as wrong as confirming it."""
    log = _log(tmp_path)
    resumed = log.begin("seg-2", at=AT, nonce="n1",
                        continues="somewhere-else", continues_fingerprint=BUNDLE)
    assert resumed.continues is not None
    assert resumed.continues.resolved is False
    assert "writer_id" not in resumed.continues.to_dict()
    log.close()


def test_an_ambiguous_predecessor_is_refused_and_can_be_named(tmp_path):
    """A retried job keeps its run_id (#82), so a chain can hold several
    unpaired BEGINs under one. Picking the newest would be a guess wearing a
    coordinate's authority -- the same shape `proof_for` refuses."""
    log = _log(tmp_path)
    log.begin("retried", at=AT, nonce="n1")
    log.begin("retried", at=AT, nonce="n2")

    with pytest.raises(CommitmentLogFork, match="continues_sequence"):
        log.begin("seg-2", at=AT, nonce="n3",
                  continues="retried", continues_fingerprint=BUNDLE)

    named = log.begin("seg-2", at=AT, nonce="n3", continues="retried",
                      continues_fingerprint=BUNDLE, continues_sequence=1)
    assert named.continues.sequence == 1

    with pytest.raises(CommitmentLogFork, match="not an unpaired BEGIN"):
        log.begin("seg-3", at=AT, nonce="n4", continues="retried",
                  continues_fingerprint=BUNDLE, continues_sequence=99)
    log.close()


def test_pairing_is_by_position_not_by_run_id(tmp_path):
    """A defect caught before it shipped, and it is the one this design is most
    prone to: "this run_id has an END somewhere" marks every BEGIN under a
    repeated id as closed, so a retried job's SECOND crash reads as resolved
    and the link names the wrong segment -- or none at all."""
    log = _log(tmp_path)
    log.begin("job", at=AT, nonce="n1")                          # seq 0, closed
    log.end("job", root=ROOT, outcome="completed", at=AT, nonce="e1")
    log.begin("job", at=AT, nonce="n2")                          # seq 2, crashed

    resumed = log.begin("seg-2", at=AT, nonce="n3",
                        continues="job", continues_fingerprint=BUNDLE)
    assert resumed.continues.sequence == 2, (
        "the link named the completed execution, or refused to name one at all")
    log.close()


def test_a_predecessor_in_a_sealed_window_is_still_found(tmp_path):
    """A crash and its resume can straddle a checkpoint -- that is the whole
    point of the two phases. Searching only the open window would report the
    predecessor as belonging to somebody else's chain."""
    log = _log(tmp_path)
    log.begin("seg-1", at=AT, nonce="n1")
    log.seal(AT)
    resumed = log.begin("seg-2", at=AT, nonce="n2",
                        continues="seg-1", continues_fingerprint=BUNDLE)
    assert resumed.continues.sequence == 0 and resumed.continues.resolved
    log.close()


def test_the_link_survives_the_disk_and_is_covered_by_the_checkpoint(tmp_path):
    """Inside the leaf digest, so the checkpoint commits to it. Verified by
    reopening from disk and walking the chain rather than by reading the code.
    """
    log = _log(tmp_path)
    log.begin("seg-1", at=AT, nonce="n1")
    log.begin("seg-2", at=AT, nonce="n2",
              continues="seg-1", continues_fingerprint=BUNDLE)
    checkpoint = log.seal(AT)
    log.close()

    again = _log(tmp_path)
    assert again.verify_chain() == 1
    _, commitments = again._sealed_window(checkpoint.index)
    assert commitments[1].continues.to_dict() == {
        "run_id": "seg-1", "bundle_fingerprint": BUNDLE,
        "writer_id": "w1", "sequence": 0,
    }
    proof = again.proof_for("seg-2", CommitmentKind.BEGIN, checkpoint.index)
    assert verify_inclusion(proof.commitment, proof.path, checkpoint.window_root)
    again.close()


def test_half_a_continuation_is_refused(tmp_path):
    log = _log(tmp_path)
    with pytest.raises(ValueError, match="source run_id AND the bundle"):
        log.begin("seg-2", at=AT, nonce="n1", continues="seg-1")
    with pytest.raises(ValueError, match="source run_id AND the bundle"):
        log.begin("seg-2", at=AT, nonce="n1", continues_fingerprint=BUNDLE)
    log.close()


def test_one_end_closes_one_begin_not_all_of_them(tmp_path):
    """The surviving mutant from the round on this feature.

    `test_pairing_is_by_position_not_by_run_id` cannot tell "pop the oldest
    open BEGIN" from "clear every open BEGIN for this run_id", because in
    BEGIN, END, BEGIN order the two agree. Two BEGINs before the END is where
    they part: one execution finished, one is still open, and clearing the
    whole entry loses the open one -- so a resume of a genuinely crashed
    segment would be recorded as a claim about a chain we do not hold, while
    holding it."""
    log = _log(tmp_path)
    log.begin("job", at=AT, nonce="n1")                          # seq 0
    log.begin("job", at=AT, nonce="n2")                          # seq 1
    log.end("job", root=ROOT, outcome="completed", at=AT, nonce="e1")

    resumed = log.begin("seg-2", at=AT, nonce="n3",
                        continues="job", continues_fingerprint=BUNDLE)
    assert resumed.continues.resolved, (
        "one END cleared both executions, so a predecessor this chain holds "
        "was recorded as belonging to somebody else's")
    assert resumed.continues.sequence == 1
    log.close()
