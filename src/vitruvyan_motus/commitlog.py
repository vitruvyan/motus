"""Where a writer's commitments are kept, and how they survive a restart.

This is ADR-021's "second persistent artifact beside the trace" -- the store
whose lifetime is longer than a run. It is deliberately NOT the trace: a trace
must stay verifiable by somebody holding one file, and must not grow a
dependency on a store they were never given.

Three properties this file exists to hold, each of which was reached by an
adversarial round finding the absence of it somewhere else first:

* **a BEGIN is durable before the run executes, or it is not a BEGIN.** The
  whole value of two phases is that the first one left the operator's reach
  before the outcome was known (ADR-020 decision 3). A BEGIN buffered in memory
  has left nothing, so `begin()` fsyncs before it returns and the caller does
  not proceed until it has;

* **a restored backup is a FORK, not a repair.** A writer whose head disagrees
  with a published checkpoint is reported as what it is. Silently continuing
  from the older head would re-issue sequence numbers that a published
  checkpoint already covers, which is the HIGH finding of the first round on
  `commitments.py` reappearing at the storage layer;

* **a torn write never happened.** Because the fsync precedes the return, a
  partial trailing line can only be a write the caller never saw complete --
  so the run it would have covered had not started. It is truncated on
  recovery rather than guessed at.

Nothing here is reachable from the runtime unless an embedder configures it
(ADR-021 decision 1).
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from vitruvyan_motus.commitments import (
    Checkpoint, Commitment, CommitmentKind, CommitmentWindow, WitnessAck,
    merkle_path,
)
from vitruvyan_motus.errors import MotusError

__all__ = ["CommitmentLog", "CommitmentLogFork", "InclusionProof"]

_SAFE = re.compile(r"[^A-Za-z0-9._-]")
_MAX_STEM = 96
_WINDOW = "window-{index:06d}.jsonl"
_CHECKPOINT = "checkpoint-{index:06d}.json"
_IDENTITY = "identity.json"


class CommitmentLogFork(MotusError):
    """This store and a published checkpoint disagree about the same index.

    Two histories now exist for one chain position. Which is the real one is
    not a question this process can answer -- and answering it by preferring
    either would let an operator choose their history by choosing a backup.
    """


def _stem(value: str) -> str:
    """A filesystem-safe stem that still names what it came from.

    Tenant and writer are opaque strings the embedder supplies (ADR-021
    decision 6), so they may contain separators and `..`. Sanitising alone
    would collide two different writers onto one directory, so a short digest
    of the original is appended. The verbatim values live in `identity.json`
    and in every commitment; the directory name is a convenience, never the
    source of truth.
    """
    digest = hashlib.sha256(value.encode("utf-8")).hexdigest()[:12]
    safe = _SAFE.sub("_", value)[:_MAX_STEM].strip("._-") or "x"
    return f"{safe}-{digest}"


def _sync_directory(directory: Path) -> None:
    """Commit a directory entry, which fsync on the file does not."""
    descriptor = os.open(directory, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    except OSError:
        pass                       # not every filesystem permits it
    finally:
        os.close(descriptor)


@dataclass(frozen=True, slots=True)
class InclusionProof:
    """One commitment, its path to a window root, and the sealed checkpoint.

    This is what a receipt carries so that it proves a RUN and not merely a
    checkpoint (ADR-021 §7). The commitment travels whole rather than as its
    leaf digest, because a verifier must recompute the leaf itself: handed a
    bare digest it proves only that *some* digest is in the tree.
    """

    commitment: Commitment
    path: tuple[tuple[str, str], ...]
    checkpoint: Checkpoint

    def to_dict(self) -> dict[str, Any]:
        return {
            "commitment": self.commitment.to_dict(),
            "witness": (self.commitment.witness.to_dict()
                        if self.commitment.witness else None),
            "path": [list(step) for step in self.path],
            "checkpoint": self.checkpoint.to_dict(),
            "checkpoint_digest": self.checkpoint.digest,
        }


class CommitmentLog:
    """One writer's chain, on disk, recovered on open.

    A log is per (tenant, writer) by construction. ADR-021 decision 5 refuses a
    shared chain because it would serialise every run start in the deployment;
    holding that here means an embedder gets the property by using the type
    correctly rather than by remembering a rule.
    """

    __slots__ = ("_root", "_dir", "tenant", "writer_id", "_fsync",
                 "_window", "_handle", "_lock", "_closed")

    def __init__(self, directory: str | os.PathLike[str], *, tenant: str,
                 writer_id: str, fsync: bool = True) -> None:
        if not isinstance(tenant, str) or not tenant.strip():
            raise ValueError("a commitment log names its tenant")
        if not isinstance(writer_id, str) or not writer_id.strip():
            raise ValueError("a commitment log names its writer")
        self.tenant = tenant
        self.writer_id = writer_id
        self._fsync = fsync
        self._root = Path(directory)
        self._dir = self._root / _stem(tenant) / _stem(writer_id)
        self._lock = threading.Lock()
        self._closed = False
        self._handle: Any = None
        self._dir.mkdir(parents=True, exist_ok=True)
        self._write_identity()
        self._window = self._recover()

    # -- recovery ---------------------------------------------------------

    def _write_identity(self) -> None:
        """The verbatim tenant and writer, beside their sanitised directory.

        Two different writers can sanitise to one stem only if their digests
        collide, but a reader who finds this file does not have to reason about
        that at all -- and a directory tree moved by hand still says what it is.
        """
        path = self._dir / _IDENTITY
        body = {"tenant": self.tenant, "writer_id": self.writer_id}
        if path.exists():
            existing = json.loads(path.read_text(encoding="utf-8"))
            if existing != body:
                raise CommitmentLogFork(
                    f"{self._dir} already belongs to {existing!r}, not to "
                    f"{body!r} — two writers cannot share a chain")
            return
        path.write_text(json.dumps(body, indent=2) + "\n", encoding="utf-8")

    def _checkpoint_indices(self) -> list[int]:
        found = []
        for path in self._dir.glob("checkpoint-*.json"):
            try:
                found.append(int(path.stem.split("-", 1)[1]))
            except (IndexError, ValueError):
                continue
        return sorted(found)

    def _read_checkpoint(self, index: int) -> Checkpoint:
        body = json.loads((self._dir / _CHECKPOINT.format(index=index)).read_text("utf-8"))
        return Checkpoint(**body)

    def _recover(self) -> CommitmentWindow:
        """Rebuild the open window from what is on disk.

        The sealed checkpoints decide where numbering resumes; the open
        window's file decides what is already in it. A trailing partial line is
        truncated: the fsync in `_append` precedes its return, so a torn line
        is a write no caller ever saw finish, and the run it would have covered
        had not started.
        """
        sealed = self._checkpoint_indices()
        if sealed:
            last = self._read_checkpoint(sealed[-1])
            if last.tenant != self.tenant or last.writer_id != self.writer_id:
                raise CommitmentLogFork(
                    f"checkpoint {sealed[-1]} belongs to "
                    f"{last.tenant}/{last.writer_id}, not to "
                    f"{self.tenant}/{self.writer_id}")
            window = CommitmentWindow.following(last)
        else:
            window = CommitmentWindow(tenant=self.tenant, writer_id=self.writer_id)

        path = self._dir / _WINDOW.format(index=window.index)
        if path.exists():
            raw = path.read_text(encoding="utf-8")
            lines = raw.split("\n")
            complete = lines[:-1] if raw.endswith("\n") else lines[:-1]
            torn = 0 if raw.endswith("\n") else len(lines[-1])
            for line in complete:
                if not line:
                    continue
                window.append(_commitment_from(json.loads(line)))
            if torn:
                with path.open("r+", encoding="utf-8") as handle:
                    handle.truncate(len(raw.encode("utf-8")) - torn)
        return window

    # -- appending --------------------------------------------------------

    def _open_handle(self) -> Any:
        if self._handle is None:
            path = self._dir / _WINDOW.format(index=self._window.index)
            new = not path.exists()
            self._handle = path.open("a", encoding="utf-8")
            if new and self._fsync:
                _sync_directory(self._dir)
        return self._handle

    def _append(self, commitment: Commitment) -> Commitment:
        """Append and make it durable BEFORE returning.

        The order is the property. A caller that proceeds to execute on the
        strength of a BEGIN still sitting in a buffer has a run whose first
        phase can vanish in a power cut -- which is exactly the state two-phase
        commitment exists to make impossible.
        """
        if self._closed:
            raise ValueError("this commitment log is closed")
        self._window.append(commitment)
        handle = self._open_handle()
        handle.write(json.dumps(commitment.to_dict(), sort_keys=True,
                                separators=(",", ":")) + "\n")
        handle.flush()
        if self._fsync:
            os.fsync(handle.fileno())
        return commitment

    def begin(self, run_id: str, *, at: str, nonce: str,
              witness: WitnessAck | None = None) -> Commitment:
        """Commit that a run is ABOUT to execute. Durable when this returns."""
        with self._lock:
            return self._append(Commitment(
                kind=CommitmentKind.BEGIN, tenant=self.tenant,
                writer_id=self.writer_id, sequence=self._window.next_sequence,
                run_id=run_id, at=at, nonce=nonce, witness=witness))

    def end(self, run_id: str, *, root: str, outcome: str, at: str,
            nonce: str) -> Commitment:
        """Commit that a run terminated, and bind the outcome to its evidence."""
        with self._lock:
            return self._append(Commitment(
                kind=CommitmentKind.END, tenant=self.tenant,
                writer_id=self.writer_id, sequence=self._window.next_sequence,
                run_id=run_id, at=at, nonce=nonce, root=root, outcome=outcome))

    # -- sealing ----------------------------------------------------------

    def seal(self, at: str) -> Checkpoint:
        """Close the open window, publish its checkpoint, open the next.

        The checkpoint is durable before the next window exists. A crash
        between them would otherwise lose the checkpoint and let the next
        window reuse an index -- two windows at one chain position, which is
        the fork this whole file refuses.
        """
        with self._lock:
            if self._closed:
                raise ValueError("this commitment log is closed")
            checkpoint = self._window.seal(at)
            path = self._dir / _CHECKPOINT.format(index=checkpoint.index)
            with path.open("w", encoding="utf-8") as handle:
                handle.write(json.dumps(checkpoint.to_dict(), indent=2) + "\n")
                handle.flush()
                if self._fsync:
                    os.fsync(handle.fileno())
            if self._fsync:
                _sync_directory(self._dir)
            if self._handle is not None:
                self._handle.close()
                self._handle = None
            self._window = CommitmentWindow.following(checkpoint)
            return checkpoint

    # -- reading ----------------------------------------------------------

    def proof_for(self, run_id: str, kind: CommitmentKind,
                  checkpoint_index: int) -> InclusionProof:
        """The inclusion proof for one commitment inside a SEALED window."""
        checkpoint = self._read_checkpoint(checkpoint_index)
        path = self._dir / _WINDOW.format(index=checkpoint_index)
        commitments = [
            _commitment_from(json.loads(line))
            for line in path.read_text(encoding="utf-8").splitlines() if line
        ]
        leaves = tuple(c.leaf for c in commitments)
        for position, commitment in enumerate(commitments):
            if commitment.run_id == run_id and commitment.kind is kind:
                return InclusionProof(
                    commitment=commitment,
                    path=merkle_path(leaves, position),
                    checkpoint=checkpoint,
                )
        raise KeyError(f"no {kind.value} for run {run_id!r} in window {checkpoint_index}")

    def check_against(self, published: Checkpoint) -> None:
        """Does this store agree with a checkpoint somebody already published?

        A restored backup produces a writer whose head disagrees with a
        checkpoint already on a chain. ADR-021 requires that be reported as a
        fork rather than repaired: repairing it silently would let an operator
        choose which history is true by choosing which backup to restore.
        """
        if published.tenant != self.tenant or published.writer_id != self.writer_id:
            raise ValueError("this checkpoint belongs to another writer")
        try:
            ours = self._read_checkpoint(published.index)
        except FileNotFoundError:
            raise CommitmentLogFork(
                f"checkpoint {published.index} is published but absent here: "
                "this store is behind a history that already exists"
            ) from None
        if ours.digest != published.digest:
            raise CommitmentLogFork(
                f"checkpoint {published.index} disagrees: this store says "
                f"{ours.digest}, the published one says {published.digest}. "
                "Two histories exist for one chain position; which is real is "
                "not a question this process may answer.")

    @property
    def open_window(self) -> CommitmentWindow:
        return self._window

    @property
    def directory(self) -> Path:
        return self._dir

    def close(self) -> None:
        with self._lock:
            if self._handle is not None:
                self._handle.close()
                self._handle = None
            self._closed = True

    def __enter__(self) -> "CommitmentLog":
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()


def _commitment_from(body: dict[str, Any]) -> Commitment:
    """Rebuild a commitment from a stored line, without inventing anything."""
    return Commitment(
        kind=CommitmentKind(body["kind"]),
        tenant=body["tenant"], writer_id=body["writer_id"],
        sequence=body["sequence"], run_id=body["run_id"],
        at=body["at"], nonce=body["nonce"],
        root=body.get("root"), outcome=body.get("outcome"),
    )
