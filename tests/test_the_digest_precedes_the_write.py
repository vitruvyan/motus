"""What the durable writer can put on disk, the digest must be able to read.

motus#112. `CommitmentLog._append` used two encoders with a durable write
between them: the window line went out through `json.dumps` at its default
`ensure_ascii=True`, and the digest was taken two frames later through
`_canonical_bytes`, which is `ensure_ascii=False` and encodes to UTF-8.

Anything the first could write and the second could not became a line the store
can never digest — written, flushed, fsynced, and **not poisoned**, because the
write had succeeded. Every future open then failed on a chain it could not
rebuild, and no reopen recovered it.

The reachable instance was a lone surrogate and `_require_text` closed it. This
is about the CLASS: the ordering that makes any such divergence unreachable,
whatever value finds it next.
"""

from __future__ import annotations

import json

import pytest

from vitruvyan_motus.commitlog import CommitmentLog

AT = "2026-01-01T00:00:00Z"


def _log(tmp_path):
    return CommitmentLog(tmp_path, tenant="t", writer_id="w")


def _files(root):
    """Every file in the store, with its size.

    **Recursive on purpose.** A store nests as
    `<root>/<tenant>/<writer>/window-*.jsonl`, so an earlier draft of this
    module compared `root.iterdir()` — which lists one directory whose name and
    size never change — and asserted nothing at all. A test that passes over
    the mutation it was written to catch is worse than no test.
    """
    return {str(f.relative_to(root)): f.stat().st_size
            for f in sorted(root.rglob("*")) if f.is_file()}


def test_a_commitment_that_cannot_be_digested_never_reaches_the_disk(tmp_path, monkeypatch):
    """The property, stated over the mechanism rather than over one bad value.

    A value that the durable writer can serialise and the digest cannot is
    forced here by making the digest refuse. What matters is not which value
    does it — `_require_text` already refuses the one that used to — but that
    the store is untouched when it happens.
    """
    from vitruvyan_motus import commitments

    log = _log(tmp_path)
    log.begin("healthy", at=AT, nonce="n0")
    before = _files(tmp_path)
    assert any(name.endswith(".jsonl") for name in before), (
        "the fixture must have written a window line, or the comparison below "
        "is between two empty pictures")

    def refuse(value):
        raise UnicodeEncodeError("utf-8", "x", 0, 1, "the digest cannot encode this")

    monkeypatch.setattr(commitments, "_canonical_bytes", refuse)

    with pytest.raises(UnicodeEncodeError):
        log.begin("undigestible", at=AT, nonce="n1")

    monkeypatch.undo()
    assert _files(tmp_path) == before, (
        "a commitment that cannot be digested reached the disk — this is #112: "
        "the line is written, fsynced and never poisoned, and the store can "
        "never digest it again")


def test_and_the_store_still_opens_and_verifies_afterwards(tmp_path, monkeypatch):
    """The consequence that made #112 worth its priority: not a lost run, an
    unrecoverable directory."""
    from vitruvyan_motus import commitments

    log = _log(tmp_path)
    log.begin("r1", at=AT, nonce="n0")
    log.end("r1", root="sha256:" + "a" * 64, outcome="completed", at=AT, nonce="n1")

    def refuse(value):
        raise UnicodeEncodeError("utf-8", "x", 0, 1, "the digest cannot encode this")

    monkeypatch.setattr(commitments, "_canonical_bytes", refuse)
    with pytest.raises(UnicodeEncodeError):
        log.begin("r2", at=AT, nonce="n2")
    monkeypatch.undo()
    log.close()

    reopened = _log(tmp_path)
    try:
        assert reopened.verify_chain() >= 0
        # And it still accepts work, which a poisoned store would not.
        reopened.begin("r3", at=AT, nonce="n3")
    finally:
        reopened.close()


def test_the_window_line_and_the_digest_read_the_same_commitment(tmp_path):
    """The two encoders still exist; what is fixed is that one cannot outrun
    the other. Reading the line back must reconstruct exactly what was digested.
    """
    log = _log(tmp_path)
    commitment = log.begin("r1", at=AT, nonce="n1")
    log.close()

    line = next(
        json.loads(raw)
        for path in sorted(tmp_path.rglob("window-*.jsonl"))
        for raw in path.read_text(encoding="utf-8").splitlines()
        if raw.strip()
    )
    assert line["c"] == commitment.to_dict(), (
        "the durable line and the digested object are the same commitment, or "
        "the store proves something other than what it wrote")
