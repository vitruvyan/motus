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
    Continuation,
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
    if not value.isascii():
        try:
            value.encode("utf-8")
        except UnicodeEncodeError as exc:
            # Before NFC, because `unicodedata.normalize` is happy to return a
            # string that still cannot be encoded -- and before `_stem`, which
            # hashed it and raised a bare codec error from a directory name.
            raise ValueError(
                f"{what} carries U+{ord(value[exc.start]):04X} at index "
                f"{exc.start}, an unpaired surrogate: it denotes no character "
                "and has no UTF-8 encoding, so it can neither name a directory "
                "nor be digested (rule J1, ADR-026)") from None
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
                 "_poisoned", "_nonces", "_witness_deadline")

    def __init__(self, directory: str | os.PathLike[str], *, tenant: str,
                 writer_id: str, fsync: bool = True,
                 allow_unlocked: bool = False,
                 witness_deadline: float | None = None) -> None:
        self.tenant = _require_identifier(tenant, "tenant")
        self.writer_id = _require_identifier(writer_id, "writer_id")
        self._fsync = fsync
        self._root = Path(directory)
        self._dir = self._root / _stem(self.tenant) / _stem(self.writer_id)
        self._lock = threading.Lock()
        self._closed = False
        self._handle: Any = None
        self._lockfile: Any = None
        self._poisoned: str | None = None
        self._nonces: set[str] = set()
        # No default. ADR-021 decision 3 says the deadline "is part of the
        # configuration", and any number invented here would be a budget chosen
        # to accommodate whatever was in front of it. A caller who configures a
        # witness states how long they will wait for it; `begin(ask=...)`
        # refuses otherwise.
        if witness_deadline is not None and not (witness_deadline > 0):
            raise ValueError("witness_deadline must be a positive number of seconds")
        self._witness_deadline = witness_deadline
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

    def _check_links(self, sealed: list[int]) -> None:
        """Contiguous, and each checkpoint linking to the one before it.

        Recovery used to read the HIGHEST checkpoint and resume after it, so
        deleting an interior one left the store perfectly willing to open and
        append. `verify_chain()` would have said so, and nothing called it: a
        whole window could be dropped and the loss surfaced only if somebody
        thought to ask.

        This reads checkpoint files only -- no window is opened and no root is
        recomputed -- so it costs one small read per sealed window and can be
        afforded on every open. `verify_chain()` remains the full check, and it
        is the one that catches a window edited under an intact checkpoint.
        """
        previous_digest: str | None = None
        for expected, index in enumerate(sealed):
            if index != expected:
                raise CommitmentLogFork(
                    f"checkpoint {expected} is missing and checkpoint {index} "
                    "is present: a sealed window was lost, and appending after "
                    "the highest checkpoint would continue a chain with a hole "
                    "in it")
            checkpoint = self._read_checkpoint(index)
            if checkpoint.previous != previous_digest:
                raise CommitmentLogFork(
                    f"checkpoint {index} links to {checkpoint.previous!r}, but "
                    f"checkpoint {index - 1} digests to {previous_digest!r}: "
                    "these are two different chains")
            previous_digest = checkpoint.digest

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
            self._check_links(sealed)
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
        self._nonces.add(commitment.nonce)

    def _poison(self, reason: str) -> None:
        """Refuse every further write, because we no longer know what is on disk.

        Reached only from a write that failed AFTER the bytes were handed to
        the file. The line may be whole, partial or absent and this process
        cannot tell which, so the sequence it would issue next is a guess. A
        guess here re-issues a sequence a durable commitment may already hold.

        Reopening the log is the recovery: `_replay` reads the file, truncates
        a torn line on a byte boundary, and resumes from what actually
        survived. That is a decision made from evidence rather than from a
        hopeful in-memory picture.
        """
        # A STRING, and the annotation is load-bearing rather than decorative.
        # #99's class is that storing an exception on a long-lived object pins
        # every frame beneath it — and this log outlives many calls. Keeping
        # the reason as text is what makes that impossible here, so an edit
        # that stored the exception instead would reintroduce the defect at a
        # second site. Asserted rather than trusted.
        if not isinstance(reason, str):
            # A `raise`, not an `assert`: `python -O` strips assertions, and a
            # guard that disappears under a flag is a guard that is not there
            # on the machine where it mattered.
            raise TypeError("a poison reason is text, never an exception")
        self._poisoned = reason
        handle, self._handle = self._handle, None
        if handle is not None:
            try:
                handle.close()
            except OSError:
                pass

    def _refuse_if_poisoned(self) -> None:
        if self._poisoned is not None:
            raise CommitmentLogFork(
                f"this log stopped being writable: {self._poisoned}. What "
                "reached the file is unknown, so any sequence issued now may "
                "already be held by a durable commitment. Close it and open "
                "it again — recovery reads the file and resumes from what "
                "survived")

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
        self._refuse_if_poisoned()

        witness: WitnessAck | None = fields.pop("witness", None)
        ask: Witness | None = fields.pop("ask", None)
        if ask is not None and self._witness_deadline is None:
            raise ValueError(
                "a witness needs a deadline: open the log with "
                "witness_deadline=<seconds>. Without one a slow witness stops "
                "this writer for as long as it likes, which is the opposite of "
                "what a witness is allowed to do")
        nonce = fields.get("nonce")
        if nonce in self._nonces:
            raise ValueError(
                f"nonce {nonce!r} was already used in this window. A nonce is "
                "used once by definition, and reusing one lets an "
                "acknowledgement captured for an earlier commitment be "
                "replayed against this one")
        commitment = Commitment(
            kind=kind, tenant=self.tenant, writer_id=self.writer_id,
            sequence=self._window.next_sequence, run_id=run_id,
            witness=witness, **fields)

        if ask is not None and witness is None:
            acknowledged = _ask_witness(ask, commitment, self._witness_deadline)
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
        try:
            handle.write(line)
            handle.flush()
            if self._fsync:
                os.fsync(handle.fileno())
        except BaseException as exc:
            # The bytes were handed to the file and the failure arrived after.
            # Continuing would re-issue this sequence while the file may
            # already hold it -- an adversarial round produced sequences
            # [0, 1, 1] on disk against a checkpoint that sealed two.
            self._poison(f"a durable write failed after the line reached the "
                         f"file ({type(exc).__name__}: {exc})")
            raise

        self._window.append(commitment)
        self._remember(commitment)
        return commitment

    def begin(self, run_id: str, *, at: str, nonce: str,
              witness: WitnessAck | None = None,
              ask: Witness | None = None,
              continues: str | None = None,
              continues_fingerprint: str | None = None,
              continues_sequence: int | None = None) -> Commitment:
        """Commit that a run is ABOUT to execute.

        **A run_id may appear more than once, and that is not an error.** A
        retried job keeping its job id is the ordinary case; nothing in the
        contract or the schema requires uniqueness, and `JsonlTraceSink` keeps
        both accounts on purpose. This log briefly refused a repeat and would
        have rejected every retry of a job that had already run in the open
        window — before its first node, with no sealing interval to clear it.
        What distinguishes two executions is the SEQUENCE, so that is what
        `proof_for` asks for when a run_id is ambiguous.

        Durable when this returns, provided the log was opened with
        ``fsync=True`` — which is the default, and which :attr:`durable`
        reports.

        ``ask`` is a witness to consult. It is called with the canonical bytes
        of this commitment, **before the commitment is written**, because an
        acknowledgement obtained afterwards would say nothing about whether the
        commitment preceded its own outcome.

        The call happens while this writer's lock is held, and that cost is
        real and measured: with a 50 ms witness, four threads take the same
        wall-clock time as one. It is inherent rather than incidental — the
        sequence number must be assigned, witnessed and written without another
        begin interleaving, or two runs are witnessed at one position. The
        answer to more throughput is more writers (ADR-021 decision 5), not a
        shorter deadline.

        Because the lock is held, the deadline is what stops a slow or
        reentrant witness from stopping the writer, and it is required rather
        than defaulted: `witness_deadline` on the log, or `ask=` is refused.

        Anything the witness does other than return an acknowledgement in time
        — `None`, a timeout, an exception — leaves the run at `LOCAL` and does
        not stop it (ADR-021 decision 3).
        """
        with self._lock:
            link = None
            if continues is not None or continues_fingerprint is not None:
                if continues is None or continues_fingerprint is None:
                    raise ValueError(
                        "a continuation needs the source run_id AND the bundle "
                        "fingerprint: the first says which run, the second is "
                        "the only value a verifier can recompute")
                link = self._continuation(continues, continues_fingerprint,
                                          continues_sequence)
            return self._append(CommitmentKind.BEGIN, run_id, at=at,
                                nonce=nonce, witness=witness, ask=ask,
                                continues=link)

    def _continuation(self, source_run_id: str, fingerprint: str,
                      sequence: int | None = None) -> Continuation:
        """Name the predecessor by chain coordinate when this store holds it.

        ADR-023: `run_id` cannot name it. A run_id may repeat, so a chain can
        hold several unpaired BEGINs under one, and a BEGIN carries no digest
        of its own segment to tell them apart. `(writer_id, sequence)` names
        exactly one, because ADR-021 numbers per writer without restarting at
        a window boundary.

        Candidates are BEGINs for that run_id with no END after them in this
        writer's chain -- a segment that ended is not one anybody resumes, and
        `UnsafeResume` refuses a terminal trace anyway.

        Three outcomes, and the third is the one worth being careful about:

        * exactly one candidate -- resolved, and the coordinate is written;
        * more than one -- refused. Picking the newest would be a guess with a
          coordinate's authority, and this codebase already refuses the same
          shape in `proof_for`;
        * none -- **unresolved, not an error.** The ordinary cause of a resume
          is a process that died, and the process that resumes is frequently a
          different writer on a different machine. A claim about a chain we do
          not hold is recorded as such; denying it would be as wrong as
          confirming it.
        """
        chain: list[Commitment] = []
        for index in self._checkpoint_indices():
            try:
                _, sealed = self._sealed_window(index)
            except CommitmentLogFork:
                # The chain is broken elsewhere. That is `verify_chain()`'s
                # verdict to deliver, not a reason to refuse a resume: this
                # method's job is to name a predecessor, and it can honestly
                # say it could not.
                continue
            chain.extend(sealed)
        chain.extend(self._window._commitments)
        chain.sort(key=lambda c: c.sequence)

        # Paired by POSITION, not by run_id. "This run_id has an END somewhere"
        # would mark every BEGIN under a repeated id as closed, so a retried
        # job's second crash would look resolved and name the wrong segment.
        # Oldest-open first: on one writer a Runtime refuses overlapping runs,
        # so BEGIN/END alternate and the choice is moot; where two writers
        # share a log it is the only ordering that does not assume nesting.
        still_open: dict[str, list[Commitment]] = {}
        for commitment in chain:
            if commitment.kind is CommitmentKind.BEGIN:
                still_open.setdefault(commitment.run_id, []).append(commitment)
            else:
                waiting = still_open.get(commitment.run_id)
                if waiting:
                    waiting.pop(0)
        unpaired = still_open.get(source_run_id, [])

        if sequence is not None:
            # The caller broke the tie themselves, which is what the refusal
            # below asks them to do. Checked rather than trusted: a coordinate
            # naming a commitment this chain does not hold would be recorded
            # with a resolved link's authority.
            named = [c for c in unpaired if c.sequence == sequence]
            if not named:
                held = ", ".join(str(c.sequence) for c in unpaired) or "none"
                raise CommitmentLogFork(
                    f"sequence {sequence} is not an unpaired BEGIN for run "
                    f"{source_run_id!r} in this chain (unpaired: {held})")
            return Continuation(run_id=source_run_id,
                                bundle_fingerprint=fingerprint,
                                writer_id=named[0].writer_id,
                                sequence=named[0].sequence)

        if len(unpaired) > 1:
            available = ", ".join(str(c.sequence) for c in unpaired)
            raise CommitmentLogFork(
                f"this chain holds {len(unpaired)} unpaired BEGINs for run "
                f"{source_run_id!r} (sequences {available}); naming one of "
                "them as the predecessor would be a guess wearing a "
                "coordinate's authority. Name it: continues_sequence=<n>")
        if len(unpaired) == 1:
            return Continuation(run_id=source_run_id,
                                bundle_fingerprint=fingerprint,
                                writer_id=unpaired[0].writer_id,
                                sequence=unpaired[0].sequence)
        return Continuation(run_id=source_run_id, bundle_fingerprint=fingerprint)

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
            self._refuse_if_poisoned()
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
            self._nonces.clear()
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
                  checkpoint_index: int,
                  sequence: int | None = None) -> InclusionProof:
        """The inclusion proof for one commitment inside a SEALED window.

        `sequence` names WHICH execution when a run_id was used more than once
        — a retried job keeping its id. Without it an ambiguous run_id is
        refused rather than resolved: returning the first match in file order
        produced a proof that genuinely verified for one half of a pair while
        the other half was unreachable through any read API, which is a
        provable half-truth and the worst thing this store could hand out.
        """
        checkpoint, commitments = self._sealed_window(checkpoint_index)
        leaves = tuple(c.leaf for c in commitments)
        matches = [i for i, c in enumerate(commitments)
                   if c.run_id == run_id and c.kind is kind
                   and (sequence is None or c.sequence == sequence)]
        if not matches:
            wanted = "" if sequence is None else f" at sequence {sequence}"
            raise KeyError(
                f"no {kind.value} for run {run_id!r}{wanted} in window "
                f"{checkpoint_index}")
        if len(matches) > 1:
            available = ", ".join(str(commitments[i].sequence) for i in matches)
            raise CommitmentLogFork(
                f"window {checkpoint_index} holds {len(matches)} "
                f"{kind.value} commitments for run {run_id!r}; proving one of "
                f"them would hide the others. Name one with "
                f"sequence=<n> — this window has {available}")
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
    def witness_deadline(self) -> float | None:
        """How long a witness gets, or None if no witness may be used here."""
        return self._witness_deadline

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


def _ask_witness(witness: Witness, commitment: Commitment,
                 deadline: float) -> WitnessAck | None:
    """Consult a witness, for at most `deadline` seconds. Nothing it does may
    stop the run.

    ADR-021 decision 3: an acknowledgement, `None`, a timeout or a raise all
    mean the same thing to the caller — continue, at whatever mode was actually
    reached. An option that let a witness block would eventually be enabled by
    somebody who had not imagined the outage.

    **The deadline is enforced on another thread**, and that is not decoration.
    The witness is consulted while this writer's lock is held, so without a
    bound a slow witness stops this writer entirely, and a witness that calls
    back into its own log deadlocks the process permanently — a non-reentrant
    lock reacquired by the thread that holds it. An adversarial round
    reproduced both. `RLock` would not have been the fix: it excuses the same
    thread and deadlocks just the same when the reentry arrives on a new one.

    **The worker is a daemon thread, and `ThreadPoolExecutor` is not usable
    here.** The first version used one and called `shutdown(wait=False)`,
    believing that detached the worker. It does not: `concurrent.futures`
    registers an interpreter-exit hook that JOINS every pool thread, and pool
    threads are not daemons. A 20-second witness therefore returned control in
    0.25s, passed its deadline test, and then held the process open for the
    remaining 20 seconds at exit — a witness that never returns held it open
    forever. "Never blocks the run" was true and "never blocks the process" was
    false, which is not the promise ADR-021 decision 3 makes.

    The cost is stated rather than hidden: a witness that never returns leaks
    the thread it was called on until the process ends. A leaked daemon thread
    is a better outcome than a process that cannot exit, and it is bounded by
    how many times somebody configures a witness that hangs.

    `BaseException` from the witness is deliberately NOT swallowed.
    `KeyboardInterrupt` and `SystemExit` are the operator asking the process to
    stop, not a witness misbehaving, and swallowing them consumed the one
    recovery mechanism an operator has for a hung run. Raised on the worker
    they would be lost, so they are carried back and re-raised here.
    """
    payload = _canonical_bytes(commitment.to_dict())
    outcome: dict[str, Any] = {}

    def _consult() -> None:
        try:
            outcome["value"] = witness.acknowledge(payload)
        except BaseException as exc:  # carried back; see below
            # Swept for #99's class and kept: this is not a long-lived store.
            # `outcome` is local to `_ask_witness` and reachable only through
            # this worker's closure, so the stack it pins dies with the thread
            # — and the thread has just finished, because storing here means
            # `acknowledge` returned. A HUNG witness never reaches this line at
            # all. Recorded rather than repaired, so that a later reader does
            # not spend the afternoon proving the same thing twice.
            outcome["error"] = exc

    worker = threading.Thread(target=_consult, name="motus-witness", daemon=True)
    worker.start()
    worker.join(deadline)
    if worker.is_alive():
        return None
    error = outcome.get("error")
    if error is not None:
        if not isinstance(error, Exception):
            raise error
        return None
    acknowledged = outcome.get("value")
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
        continues=(Continuation(**digested["continues"])
                   if digested.get("continues") else None),
    )
    return commitment, ack
