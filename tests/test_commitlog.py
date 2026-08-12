"""The store's properties, asserted rather than described.

Several of these assert a REFUSAL. A store that quietly continues from a
restored backup has removed the guarantee without removing the sentence.
"""

from __future__ import annotations

import json

import pytest

from vitruvyan_motus.commitlog import (
    CommitmentLog, CommitmentLogFork, InclusionProof,
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
    assert len(stored) == 1 and stored[0]["kind"] == "begin"
    assert stored[0]["run_id"] == "r1"
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
    assert body == {"tenant": "acme/prod", "writer_id": "worker #1"}
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
    assert sorted(s["sequence"] for s in stored) == list(range(120))
    log.close()
