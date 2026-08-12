"""Where a writer's commitments are kept, and how they survive a restart.

ADR-021's "second persistent artifact beside the trace" -- the store whose
lifetime is longer than a run. Deliberately NOT the trace: a trace must stay
verifiable by somebody holding one file, and must not grow a dependency on a
store they were never given.

The first version of this file was attacked by three independent agents before
it was wired to anything, and they found five defects that would each have made
it unsafe to build on. Every one is now a property with a test, and the reasons
are kept here because each was arrived at the hard way:

* **the write happens before the memory does.** The first version appended to
  the in-memory window and then did the I/O. A write that failed -- a full
  disk, an EIO from fsync, a read-only directory -- left a phantom commitment
  in memory that `seal()` then hashed into a checkpoint the file could never
  reproduce, and every proof from that window failed to verify with no error
  anywhere;

* **one writer, one process, enforced by the operating system.** Two
  `CommitmentLog` objects on one directory each held their own lock and neither
  saw the other: two commitments claiming sequence 0, then a chain no future
  process could open. A supervisor restart with the old instance not yet dead
  is an ordinary operational event, not an attack;

* **truncation is arithmetic on bytes.** The first version measured a torn line
  in characters and subtracted it from a length in bytes. One `é` in a run_id
  left a dangling byte, the next restart raised, and the writer was bricked
  forever. Tenant and run ids are opaque strings (ADR-021 decision 6), so
  non-ASCII is legal input rather than misuse;

* **a checkpoint is written atomically or not at all.** Writing in place left a
  third state the design had not considered -- present and corrupt -- which is
  strictly worse than the absent one the code did handle;

* **the witness acknowledgement is persisted beside the commitment.** It must
  stay out of the leaf digest, because it did not exist when the witness signed
  it. That is not the same as not storing it, and the first version conflated
  the two, which made `EXECUTION_CONTINUITY` unprovable for every witnessed run.

Nothing here is reachable from the runtime unless an embedder configures it
(ADR-021 decision 1).
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import threading
import unicodedata
from dataclasses import dataclass
from pathlib import Path
from typing import Any

try:
    import fcntl
except ImportError:                                    # pragma: no cover
    fcntl = None                                       # type: ignore[assignment]

from vitruvyan_motus.commitments import (
    Checkpoint, Commitment, CommitmentKind, CommitmentWindow, Witness,
    WitnessAck, merkle_path, merkle_root,
)
from vitruvyan_motus.errors import MotusError
from vitruvyan_motus.trace import _canonical_bytes

__all__ = ["CommitmentLog", "CommitmentLogFork", "CommitmentLogBusy",
           "InclusionProof", "STORE_FORMAT"]

STORE_FORMAT = "motus-commitlog/1"

_SAFE = re.compile(r"[^A-Za-z0-9._-]")
_MAX_STEM = 96
_DIGEST_CHARS = 32                 # 128 bits. 48 collided in 18 seconds.
_WINDOW = "window-{index:06d}.jsonl"
_CHECKPOINT = "checkpoint-{index:06d}.json"
_IDENTITY = "identity.json"
_LOCKFILE = ".writer.lock"


class CommitmentLogFork(MotusError):
    """This store and a history that already exists disagree.

    Two accounts now exist for one chain position. Which is the real one is not
    a question this process can answer, and answering it by preferring either
    would let an operator choose their history by choosing a backup.
    """


class CommitmentLogBusy(MotusError):
    """Another live holder owns this writer's chain.

    One chain per writer is not advice. Two holders produce two commitments at
    one sequence number, and the chain then opens for nobody.
    """


def _stem(value: str) -> str:
    """A filesystem-safe stem that still names what it came from.

    Tenant and writer are opaque strings the embedder supplies, so they may
    contain separators and `..`. Sanitising alone would collide two different
    writers onto one directory, so a digest of the original is appended.

    The digest is 128 bits and not the 48 that `JsonlTraceSink` uses, because
    the consequence differs: there a collision costs one trace file a suffix,
    here it costs a writer their identity. An adversarial round found a 48-bit
    collision in 18 seconds on one core.
    """
    digest = hashlib.sha256(value.encode("utf-8")).hexdigest()[:_DIGEST_CHARS]
    safe = _SAFE.sub("_", value)[:_MAX_STEM].strip("._-") or "x"
    return f"{safe}-{digest}"


def _require_identifier(value: Any, what: str) -> str:
    """A non-blank string in NFC, refused loudly rather than forked silently.

    `café` composed and `café` decomposed are different byte sequences and hash
    differently, so they would open two permanent chains for one logical
    writer, with no signal at all. ADR-021 decision 6 says the format is the
    embedder's to choose -- so this does not normalise on their behalf, it
    refuses and says what to pass instead.
    """
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"a commitment log names its {what}")
    if unicodedata.normalize("NFC", value) != value:
        raise ValueError(
            f"{what} is not in Unicode NFC form: {value!r}. Two normalisations "
            "of one name would open two chains for one writer and nothing "
            "would say so, so this is refused rather than normalised for you")
    return value


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

    What a receipt carries so that it proves a RUN and not merely a checkpoint
    (ADR-021 §7). The commitment travels whole rather than as its leaf digest,
    because a verifier must recompute the leaf itself: handed a bare digest it
    proves only that *some* digest is in the tree.
    """

    commitment: Commitment
    path: tuple[tuple[str, str], ...]
    checkpoint: Checkpoint

    def to_dict(self) -> dict[str, Any]:
        return {
            "format": STORE_FORMAT,
            "commitment": self.commitment.to_dict(),
            "witness": (self.commitment.witness.to_dict()
                        if self.commitment.witness else None),
            "mode": self.commitment.mode.value,
            "path": [list(step) for step in self.path],
            "checkpoint": self.checkpoint.to_dict(),
            "checkpoint_digest": self.checkpoint.digest,
        }


class CommitmentLog:
    """One writer's chain, on disk, recovered on open and locked while held.

    Per (tenant, writer) by construction AND by an exclusive OS lock: ADR-021
    decision 5 refuses a chain shared across writers because it would serialise
    every run start, and the corollary -- two holders of ONE writer's chain --
    is what the lock prevents. `by construction` was the first version's claim
    and it was false; constructing two objects is using the public API.
    """

    __slots__ = ("_root", "_dir", "tenant", "writer_id", "_fsync",
                 "_window", "_handle", "_lock", "_closed", "_lockfile",
                 "_seen")

    def __init__(self, directory: str | os.PathLike[str], *, tenant: str,
                 writer_id: str, fsync: bool = True,
                 allow_unlocked: bool = False) -> None:
        self.tenant = _require_identifier(tenant, "tenant")
        self.writer_id = _require_identifier(writer_id, "writer_id")
        self._fsync = fsync
        self._root = Path(directory)
        self._dir = self._root / _stem(self.tenant) / _stem(self.writer_id)
        self._lock = threading.Lock()
        self._closed = False
        self._handle: Any = None
        self._lockfile: Any = None
        self._seen: set[tuple[str, str]] = set()
        self._dir.mkdir(parents=True, exist_ok=True)
        self._acquire(allow_unlocked=allow_unlocked)
        try:
            self._write_identity()
            self._window = self._recover()
        except BaseException:
            self._release()
            raise

    # -- exclusion --------------------------------------------------------

    def _acquire(self, *, allow_unlocked: bool) -> None:
        """Take the writer's lock, or refuse to open at all.

        `flock` is per open file description, so a second holder fails whether
        it is another process or the same one. On a platform without `fcntl`
        the exclusion would be intra-process only, which is a weaker guarantee
        than this class states -- so it is refused unless the caller says
        `allow_unlocked=True` and thereby owns the consequence.
        """
        if fcntl is None:                              # pragma: no cover
            if not allow_unlocked:
                raise CommitmentLogBusy(
                    "this platform has no fcntl, so one-writer-per-chain "
                    "cannot be enforced across processes. Pass "
                    "allow_unlocked=True to open anyway and own the risk")
            return
        handle = (self._dir / _LOCKFILE).open("a+")
        try:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            handle.close()
            raise CommitmentLogBusy(
                f"another holder owns {self.tenant}/{self.writer_id}. Two "
                "holders write two commitments at one sequence number, and "
                "the chain then opens for nobody") from None
        self._lockfile = handle

    def _release(self) -> None:
        if self._lockfile is not None:
            try:
                if fcntl is not None:
                    fcntl.flock(self._lockfile.fileno(), fcntl.LOCK_UN)
            finally:
                self._lockfile.close()
                self._lockfile = None

    # -- recovery ---------------------------------------------------------

    def _write_identity(self) -> None:
        """The verbatim identity, beside its sanitised directory name."""
        path = self._dir / _IDENTITY
        body = {"format": STORE_FORMAT, "tenant": self.tenant,
                "writer_id": self.writer_id}
        if path.exists():
            try:
                existing = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, ValueError) as exc:
                raise CommitmentLogFork(
                    f"{path} cannot be read as an identity ({exc}); this store "
                    "will not guess whose chain it is") from None
            if existing != body:
                raise CommitmentLogFork(
                    f"{self._dir} already belongs to {existing!r}, not to "
                    f"{body!r} — two writers cannot share a chain")
            return
        _atomic_write(path, json.dumps(body, indent=2) + "\n", fsync=self._fsync)

    def _checkpoint_indices(self) -> list[int]:
        found = []
        for path in self._dir.glob("checkpoint-*.json"):
            try:
                found.append(int(path.stem.split("-", 1)[1]))
            except (IndexError, ValueError):
                raise CommitmentLogFork(
                    f"{path.name} is not a checkpoint this store wrote") from None
        return sorted(found)

    def _window_indices(self) -> list[int]:
        found = []
        for path in self._dir.glob("window-*.jsonl"):
            try:
                found.append(int(path.stem.split("-", 1)[1]))
            except (IndexError, ValueError):
                raise CommitmentLogFork(
                    f"{path.name} is not a window this store wrote") from None
        return sorted(found)

    def _read_checkpoint(self, index: int) -> Checkpoint:
        path = self._dir / _CHECKPOINT.format(index=index)
        try:
            body = json.loads(path.read_text(encoding="utf-8"))
            return Checkpoint(**body)
        except FileNotFoundError:
            raise
        except (OSError, ValueError, TypeError) as exc:
            raise CommitmentLogFork(
                f"{path.name} is present and unreadable as a checkpoint "
                f"({exc}). A half-written checkpoint is not an absent one, and "
                "this store will not guess which it was") from None

    def _recover(self) -> CommitmentWindow:
        """Rebuild the open window from what is on disk.

        The sealed checkpoints decide where numbering resumes; the open
        window's file decides what is already in it. A window file that exists
        BEYOND the one the checkpoints imply is not ignored: it means a
        checkpoint was lost, and continuing would re-issue sequence numbers a
        durable commitment already holds.
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

        beyond = [i for i in self._window_indices() if i > window.index]
        if beyond:
            raise CommitmentLogFork(
                f"window {beyond[0]} exists but its checkpoint does not: a "
                "checkpoint was lost, and resuming at window "
                f"{window.index} would re-issue sequence numbers that "
                "commitments in that file already hold")

        path = self._dir / _WINDOW.format(index=window.index)
        if path.exists():
            self._replay(path, window)
        return window

    def _replay(self, path: Path, window: CommitmentWindow) -> None:
        """Replay a window file, truncating one torn trailing line.

        The arithmetic is on BYTES throughout. Measuring the torn tail in
        characters and subtracting it from a byte length leaves dangling bytes
        for any non-ASCII content, and the next restart cannot parse the file
        at all -- permanently.
        """
        raw = path.read_bytes()
        torn = 0
        if raw and not raw.endswith(b"\n"):
            torn = len(raw) - (raw.rfind(b"\n") + 1)
        body = raw[: len(raw) - torn] if torn else raw
        for number, line in enumerate(body.split(b"\n"), start=1):
            if not line.strip():
                continue
            try:
                commitment, ack = _commitment_from(json.loads(line))
            except (ValueError, TypeError, KeyError) as exc:
                raise CommitmentLogFork(
                    f"{path.name} line {number} is not a commitment this store "
                    f"wrote ({exc})") from None
            try:
                window.append(commitment)
            except ValueError as exc:
                raise CommitmentLogFork(
                    f"{path.name} line {number} does not continue this chain: "
                    f"{exc}") from None
            self._remember(commitment)
            if ack is not None:
                object.__setattr__(commitment, "witness", ack)
        if torn:
            with path.open("r+b") as handle:
                handle.truncate(len(raw) - torn)
                handle.flush()
                if self._fsync:
                    os.fsync(handle.fileno())

    # -- appending --------------------------------------------------------

    def _remember(self, commitment: Commitment) -> None:
        self._seen.add((commitment.run_id, commitment.kind.value))

    def _open_handle(self) -> Any:
        if self._handle is None:
            path = self._dir / _WINDOW.format(index=self._window.index)
            new = not path.exists()
            self._handle = path.open("a", encoding="utf-8")
            if new and self._fsync:
                _sync_directory(self._dir)
        return self._handle

    def _append(self, kind: CommitmentKind, run_id: str, **fields: Any) -> Commitment:
        """Write it, make it durable, and ONLY THEN let memory know.

        The order is the property. The first version appended to the window
        first, so a write that failed left a commitment in memory that `seal()`
        hashed into a checkpoint the file could never reproduce -- and every
        proof from that window then failed to verify, with no error raised
        anywhere along the way.
        """
        if self._closed:
            raise ValueError("this commitment log is closed")
        if (run_id, kind.value) in self._seen:
            raise ValueError(
                f"this writer already committed a {kind.value} for run "
                f"{run_id!r}. Two of them make the run's account "
                "self-contradictory, and a proof would silently pick one")

        witness: WitnessAck | None = fields.pop("witness", None)
        ask: Witness | None = fields.pop("ask", None)
        commitment = Commitment(
            kind=kind, tenant=self.tenant, writer_id=self.writer_id,
            sequence=self._window.next_sequence, run_id=run_id,
            witness=witness, **fields)

        if ask is not None and witness is None:
            acknowledged = _ask_witness(ask, commitment)
            if acknowledged is not None:
                witness = acknowledged
                commitment = Commitment(
                    kind=kind, tenant=self.tenant, writer_id=self.writer_id,
                    sequence=commitment.sequence, run_id=run_id,
                    witness=witness, **fields)

        line = json.dumps(
            {"c": commitment.to_dict(),
             "witness": witness.to_dict() if witness else None},
            sort_keys=True, separators=(",", ":")) + "\n"
        handle = self._open_handle()
        handle.write(line)
        handle.flush()
        if self._fsync:
            os.fsync(handle.fileno())

        self._window.append(commitment)
        self._remember(commitment)
        return commitment

    def begin(self, run_id: str, *, at: str, nonce: str,
              witness: WitnessAck | None = None,
              ask: Witness | None = None) -> Commitment:
        """Commit that a run is ABOUT to execute.

        Durable when this returns, provided the log was opened with
        ``fsync=True`` — which is the default, and which :attr:`durable`
        reports.

        ``ask`` is a witness to consult. It is called with the canonical bytes
        of this commitment, **before the commitment is written**, because an
        acknowledgement obtained afterwards would say nothing about whether the
        commitment preceded its own outcome.

        The call happens while this writer's lock is held, and that cost is
        real: one writer's begins serialise behind the witness's deadline. It
        is inherent rather than incidental — the sequence number must be
        assigned, witnessed and written without another begin interleaving, or
        two runs are witnessed at one position. The answer to more throughput
        is more writers (ADR-021 decision 5), not a shorter deadline.

        Anything the witness does other than return an acknowledgement —
        `None`, a timeout, an exception — leaves the run at `LOCAL` and does
        not stop it (ADR-021 decision 3).
        """
        with self._lock:
            return self._append(CommitmentKind.BEGIN, run_id, at=at,
                                nonce=nonce, witness=witness, ask=ask)

    def end(self, run_id: str, *, root: str, outcome: str, at: str,
            nonce: str) -> Commitment:
        """Commit that a run terminated, binding the outcome to its evidence."""
        with self._lock:
            return self._append(CommitmentKind.END, run_id, at=at, nonce=nonce,
                                root=root, outcome=outcome)

    # -- sealing ----------------------------------------------------------

    def seal(self, at: str) -> Checkpoint:
        """Close the open window, publish its checkpoint, open the next.

        The checkpoint is written to a temporary name and renamed, so a crash
        leaves it either absent or whole. Writing in place left a third state
        the first version had not considered -- present and corrupt -- which no
        later open could recover from.
        """
        with self._lock:
            if self._closed:
                raise ValueError("this commitment log is closed")
            path = self._dir / _CHECKPOINT.format(index=self._window.index)
            if path.exists():
                raise CommitmentLogFork(
                    f"{path.name} already exists: another account of this "
                    "chain position was sealed before ours")
            checkpoint = self._window.seal(at)
            _atomic_write(path, json.dumps(checkpoint.to_dict(), indent=2) + "\n",
                          fsync=self._fsync)
            if self._handle is not None:
                self._handle.close()
                self._handle = None
            self._window = CommitmentWindow.following(checkpoint)
            self._seen.clear()
            return checkpoint

    # -- reading ----------------------------------------------------------

    def _sealed_window(self, index: int) -> tuple[Checkpoint, list[Commitment]]:
        """A sealed window, checked against the checkpoint that sealed it.

        Re-reading the file and trusting it is what the first version did. A
        window edited after sealing -- one run id substituted for another,
        leaving count and range untouched -- then produced a well-formed proof
        against a root the file no longer reproduces, and nothing said so.
        """
        checkpoint = self._read_checkpoint(index)
        path = self._dir / _WINDOW.format(index=index)
        try:
            raw = path.read_bytes()
        except FileNotFoundError:
            raise CommitmentLogFork(
                f"checkpoint {index} exists and its window file does not"
            ) from None
        commitments = []
        for line in raw.split(b"\n"):
            if line.strip():
                commitments.append(_commitment_from(json.loads(line))[0])
        if len(commitments) != checkpoint.count:
            raise CommitmentLogFork(
                f"window {index} holds {len(commitments)} commitments and its "
                f"checkpoint sealed {checkpoint.count}")
        if merkle_root(tuple(c.leaf for c in commitments)) != checkpoint.window_root:
            raise CommitmentLogFork(
                f"window {index} no longer reproduces the root its checkpoint "
                "sealed: the file and the checkpoint are two different accounts")
        return checkpoint, commitments

    def proof_for(self, run_id: str, kind: CommitmentKind,
                  checkpoint_index: int) -> InclusionProof:
        """The inclusion proof for one commitment inside a SEALED window."""
        checkpoint, commitments = self._sealed_window(checkpoint_index)
        leaves = tuple(c.leaf for c in commitments)
        matches = [i for i, c in enumerate(commitments)
                   if c.run_id == run_id and c.kind is kind]
        if not matches:
            raise KeyError(
                f"no {kind.value} for run {run_id!r} in window {checkpoint_index}")
        if len(matches) > 1:
            raise CommitmentLogFork(
                f"window {checkpoint_index} holds {len(matches)} "
                f"{kind.value} commitments for run {run_id!r}; proving one of "
                "them would hide the others")
        return InclusionProof(commitment=commitments[matches[0]],
                              path=merkle_path(leaves, matches[0]),
                              checkpoint=checkpoint)

    def verify_chain(self) -> int:
        """Walk every sealed checkpoint. Returns how many were checked.

        Each window must reproduce the root its checkpoint sealed, each
        checkpoint must link to the one before it, and the indices must be
        contiguous. ADR-021 decision 2 says chaining is "what makes a whole
        window impossible to drop after the fact" — which is only true if
        somebody walks the chain, and until now nobody did.
        """
        indices = self._checkpoint_indices()
        previous_digest: str | None = None
        for expected, index in enumerate(indices):
            if index != expected:
                raise CommitmentLogFork(
                    f"checkpoint {expected} is missing: the chain skips to {index}")
            checkpoint, _ = self._sealed_window(index)
            if checkpoint.previous != previous_digest:
                raise CommitmentLogFork(
                    f"checkpoint {index} links to {checkpoint.previous!r}, but "
                    f"checkpoint {index - 1} digests to {previous_digest!r}")
            previous_digest = checkpoint.digest
        return len(indices)

    def check_against(self, published: Checkpoint) -> None:
        """Does this store agree with a checkpoint somebody already published?

        Walks the chain first, so a window edited under an untouched checkpoint
        is caught too — comparing digests alone would pass it.
        """
        if published.tenant != self.tenant or published.writer_id != self.writer_id:
            raise ValueError("this checkpoint belongs to another writer")
        self.verify_chain()
        try:
            ours = self._read_checkpoint(published.index)
        except FileNotFoundError:
            sealed = self._checkpoint_indices()
            reached = f"sealed up to {sealed[-1]}" if sealed else "sealed nothing"
            raise CommitmentLogFork(
                f"checkpoint {published.index} is published and absent here; "
                f"this store has {reached}") from None
        if ours.digest != published.digest:
            raise CommitmentLogFork(
                f"checkpoint {published.index} disagrees: this store says "
                f"{ours.digest}, the published one says {published.digest}. "
                "Two accounts exist for one chain position; which is real is "
                "not a question this process may answer.")

    @property
    def durable(self) -> bool:
        """Whether `begin` returning means the commitment survives a power cut."""
        return self._fsync

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
        self._release()

    def __enter__(self) -> "CommitmentLog":
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()


def _ask_witness(witness: Witness, commitment: Commitment) -> WitnessAck | None:
    """Consult a witness. Nothing it does may stop the run.

    ADR-021 decision 3: an acknowledgement, `None`, or a raise all mean the
    same thing to the caller — continue, at whatever mode was actually reached.
    An option that let a witness block would eventually be enabled by somebody
    who had not imagined the outage.

    An acknowledgement that names a different commitment is discarded rather
    than attached: `Commitment.__post_init__` would refuse it anyway, and
    refusing here would turn a hostile witness into a way to stop runs.
    """
    try:
        acknowledged = witness.acknowledge(_canonical_bytes(commitment.to_dict()))
    except BaseException:
        return None
    if acknowledged is None:
        return None
    if not isinstance(acknowledged, WitnessAck):
        return None
    if acknowledged.commitment != commitment.leaf:
        return None
    return acknowledged


def _atomic_write(path: Path, text: str, *, fsync: bool) -> None:
    """Whole or absent, never half. A rename is the only atomic step here."""
    temporary = path.with_name(path.name + ".tmp")
    with temporary.open("w", encoding="utf-8") as handle:
        handle.write(text)
        handle.flush()
        if fsync:
            os.fsync(handle.fileno())
    os.replace(temporary, path)
    if fsync:
        _sync_directory(path.parent)


def _commitment_from(body: dict[str, Any]) -> tuple[Commitment, WitnessAck | None]:
    """Rebuild a commitment and its acknowledgement from a stored line.

    The ACK lives BESIDE the digested body, never inside it: a leaf cannot
    depend on a value that did not exist when the witness signed it. Storing it
    beside is a different question from hashing it in, and the first version
    answered both with "no", which lost every witnessed run's
    EXECUTION_CONTINUITY.
    """
    digested = body["c"]
    ack_body = body.get("witness")
    ack = WitnessAck(**ack_body) if ack_body else None
    commitment = Commitment(
        kind=CommitmentKind(digested["kind"]),
        tenant=digested["tenant"], writer_id=digested["writer_id"],
        sequence=digested["sequence"], run_id=digested["run_id"],
        at=digested["at"], nonce=digested["nonce"],
        root=digested.get("root"), outcome=digested.get("outcome"),
        witness=ack,
    )
    return commitment, ack
