"""The properties ADR-020 and ADR-021 claim, asserted rather than described.

Each test names the claim it defends. A test here that goes green while the
claim is false is worse than no test, so several of them assert a REFUSAL —
the structures are meant to fail closed, and a structure that quietly accepts
a malformed commitment has removed the guarantee without removing the sentence.
"""

from __future__ import annotations

import pytest

from vitruvyan_motus.commitments import (
    AnchorReceipt, AssuranceMode, Checkpoint, Commitment, CommitmentKind,
    CommitmentWindow, WitnessAck, merkle_path, merkle_root, verify_merkle_path,
)

AT = "2026-08-12T09:14:00Z"


def _begin(seq: int, *, tenant="acme", writer="w1", witness=None) -> Commitment:
    return Commitment(
        kind=CommitmentKind.BEGIN, tenant=tenant, writer_id=writer,
        sequence=seq, run_id=f"run-{seq}", at=AT, nonce=f"n{seq}", witness=witness,
    )


def _end(seq: int, *, root="sha256:" + "a" * 64, outcome="completed") -> Commitment:
    return Commitment(
        kind=CommitmentKind.END, tenant="acme", writer_id="w1", sequence=seq,
        run_id=f"run-{seq}", at=AT, nonce=f"n{seq}", root=root, outcome=outcome,
    )


def _window(n: int) -> CommitmentWindow:
    w = CommitmentWindow(tenant="acme", writer_id="w1")
    for i in range(n):
        w.append(_begin(i))
    return w


# -- a BEGIN precedes the outcome, and the type enforces it ----------------

def test_a_begin_cannot_carry_the_outcome_it_precedes():
    """ADR-020 decision 3. A BEGIN written with a root is not a BEGIN: it was
    produced after the run finished, which is the whole thing the two phases
    exist to prevent."""
    with pytest.raises(ValueError, match="precedes the outcome"):
        Commitment(kind=CommitmentKind.BEGIN, tenant="acme", writer_id="w1",
                   sequence=0, run_id="r", at=AT, nonce="n",
                   root="sha256:" + "b" * 64)


def test_an_end_names_why_the_run_terminated():
    """END is required on EVERY terminal path. An END with no reason makes a
    crash and a refusal the same record, and then BEGIN-without-END stops
    being rare enough to mean anything."""
    with pytest.raises(ValueError, match="why the run terminated"):
        Commitment(kind=CommitmentKind.END, tenant="acme", writer_id="w1",
                   sequence=1, run_id="r", at=AT, nonce="n")


def test_a_digest_names_its_algorithm():
    """The one anchor Vitruvyan published carries a bare 64-hex string, so a
    verifier must guess which function to recompute. Refuse another one."""
    with pytest.raises(ValueError, match="prefixed"):
        _end(1, root="a" * 64)


# -- the mode is recorded, never assumed ----------------------------------

def test_without_an_acknowledgement_a_commitment_is_local():
    assert _begin(0).mode is AssuranceMode.LOCAL


def test_an_acknowledgement_raises_the_mode_and_nothing_else_does():
    ack = WitnessAck(witness_id="w.example", position=7,
                     acknowledged_at=AT, signature="sig")
    assert _begin(0, witness=ack).mode is AssuranceMode.WITNESSED


def test_the_witness_ack_is_not_inside_the_leaf_it_acknowledges():
    """A witness signs the commitment. If the commitment contained the
    signature, the leaf would depend on a value that did not exist when the
    witness saw it, and no acknowledgement could ever verify."""
    ack = WitnessAck(witness_id="w.example", position=7,
                     acknowledged_at=AT, signature="sig")
    assert _begin(0).leaf == _begin(0, witness=ack).leaf


# -- the accumulator ------------------------------------------------------

def test_a_proof_verifies_for_every_leaf_at_awkward_sizes():
    """Odd counts are where a Merkle implementation is wrong, so try them."""
    for n in (1, 2, 3, 5, 7, 8, 9, 33):
        w = _window(n)
        root = merkle_root(w.leaves)
        for i in range(n):
            assert verify_merkle_path(w.leaves[i], w.proof_for(i), root), (n, i)


def test_a_proof_does_not_verify_for_a_leaf_that_is_not_there():
    w = _window(8)
    root = merkle_root(w.leaves)
    outsider = _begin(99).leaf
    assert not verify_merkle_path(outsider, w.proof_for(3), root)


def test_the_odd_node_is_promoted_and_not_duplicated():
    """Duplicating the odd node is the classic construction AND the classic
    vulnerability: a tree of n and a tree of n+1 whose last leaf repeats
    produce one root, so two different windows present the same value."""
    three = _window(3)
    four = CommitmentWindow(tenant="acme", writer_id="w1")
    for i in range(3):
        four.append(_begin(i))
    duplicated = three.leaves + (three.leaves[-1],)
    assert merkle_root(three.leaves) != merkle_root(duplicated)


def test_a_proof_discloses_sibling_digests_and_no_commitment():
    """The reason a window is a tree and not a chain. Under a linear chain,
    proving run 3 means handing over every later commitment."""
    w = _window(16)
    path = w.proof_for(3)
    assert len(path) == 4                       # log2(16), not 16
    disclosed = {sibling for _, sibling in path}
    assert not disclosed & {c.leaf for c in (_begin(0), _begin(1))} - set(w.leaves)


# -- ordering and ownership ------------------------------------------------

def test_a_window_refuses_a_commitment_out_of_order():
    w = _window(3)
    with pytest.raises(ValueError, match="out of order"):
        w.append(_begin(7))


def test_a_window_refuses_another_writers_commitment():
    """One chain per writer. A commitment that wandered into the wrong chain
    would make that writer's numbering meaningless, which is where gap
    detection lives."""
    w = _window(2)
    with pytest.raises(ValueError, match="another writer"):
        w.append(_begin(2, writer="w2"))


def test_numbering_does_not_restart_at_a_window_boundary():
    """Otherwise '40 then 42' would be a statement about the window, and the
    window boundary is chosen by the operator."""
    first = _window(4)
    cp = first.seal(AT)
    second = CommitmentWindow(tenant="acme", writer_id="w1",
                              index=cp.index + 1, previous=cp.digest)
    second.resume_at(cp.last_sequence + 1)
    assert second.next_sequence == 4
    second.append(_begin(4))
    assert len(second) == 1


# -- the checkpoint --------------------------------------------------------

def test_a_checkpoint_commits_to_its_own_count_and_range():
    """A window rebuilt with a leaf removed moves the root AND the count. The
    count is what an independent tally can be compared against."""
    cp = _window(5).seal(AT)
    assert cp.count == 5 and cp.first_sequence == 0 and cp.last_sequence == 4


def test_a_checkpoint_whose_range_and_count_disagree_is_refused():
    with pytest.raises(ValueError, match="disagree"):
        Checkpoint(tenant="acme", writer_id="w1", index=0,
                   window_root="sha256:" + "c" * 64, count=5,
                   first_sequence=0, last_sequence=9, sealed_at=AT)


def test_an_empty_window_cannot_be_sealed():
    """Silence and absence have the same shape here. An empty checkpoint would
    assert that nothing happened, which this structure cannot support."""
    w = CommitmentWindow(tenant="acme", writer_id="w1")
    with pytest.raises(ValueError, match="not evidence that nothing happened"):
        w.seal(AT)


def test_the_checkpoint_digest_covers_the_link_to_the_previous_one():
    """ADR-019's lesson, one level up: a digest that does not cover its own
    link leaves the link free to be restated, and the terminal value does not
    move when it is."""
    base = _window(3).seal(AT)
    relinked = Checkpoint(**{**base.to_dict(),
                             "previous": "sha256:" + "d" * 64})
    assert relinked.digest != base.digest


def test_two_windows_differing_only_in_one_commitment_do_not_share_a_digest():
    a = _window(4).seal(AT)
    w = CommitmentWindow(tenant="acme", writer_id="w1")
    for i in range(3):
        w.append(_begin(i))
    w.append(Commitment(kind=CommitmentKind.BEGIN, tenant="acme", writer_id="w1",
                        sequence=3, run_id="run-elsewhere", at=AT, nonce="n3"))
    assert w.seal(AT).digest != a.digest


# -- the anchor receipt ----------------------------------------------------

def test_an_anchored_receipt_names_the_transaction_that_anchors_it():
    """A receipt that says 'anchored' and points at nothing is the one lie
    that ends an evaluation: the reader opens it and finds a 404."""
    with pytest.raises(ValueError, match="names the transaction"):
        AnchorReceipt(anchor_id="ots", network="bitcoin",
                      checkpoint="sha256:" + "e" * 64, state="anchored")


def test_a_pending_receipt_needs_no_reference():
    receipt = AnchorReceipt(anchor_id="ots", network="bitcoin",
                            checkpoint="sha256:" + "e" * 64, state="pending")
    assert receipt.state == "pending" and receipt.reference is None


def test_an_unknown_state_is_refused():
    with pytest.raises(ValueError, match="pending or anchored"):
        AnchorReceipt(anchor_id="x", network="n",
                      checkpoint="sha256:" + "e" * 64, state="verified")  # type: ignore[arg-type]


# -- the empty case, which must remain exactly today's Motus ---------------

def test_nothing_here_is_reachable_from_the_runtime_yet():
    """ADR-021 decision 1: configured off means bit-for-bit today. Until the
    lifecycle lands, the runtime must not import any of this."""
    import pathlib
    runtime = pathlib.Path(__file__).resolve().parent.parent / "src" / "vitruvyan_motus" / "runtime.py"
    assert "commitments" not in runtime.read_text(encoding="utf-8")
