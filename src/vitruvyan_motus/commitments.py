"""Commitments, windows and checkpoints — the structures ADR-021 decides.

Nothing here touches the runtime and nothing here reaches a network. This module
is the arithmetic: what a commitment *is*, how a window accumulates them, and
what a checkpoint commits to. The interfaces that carry those values elsewhere —
``Witness`` and ``Anchor`` — are Protocols with no implementation in this
distribution, by ADR-021 decision 4.

Two properties are worth stating before the code, because both were reached by
being wrong first (ADR-020 *Wrong turns*):

* a Merkle window buys a proof that is O(log n) **and discloses only sibling
  digests**. Under a linear chain, proving one run means handing over every
  later commitment — for a credit desk that is not a size problem, it is a
  reason not to buy;
* the tree does **not** buy custody. A window built by the operator can be built
  with a leaf missing and is perfectly consistent. Only a witness closes that,
  and a witness lives on the other side of a Protocol precisely so that nobody
  can mistake the tree for the guarantee.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Literal, Protocol, runtime_checkable

from vitruvyan_motus.trace import _canonical_bytes

__all__ = [
    "AssuranceMode", "CommitmentKind", "Commitment", "WitnessAck",
    "AnchorReceipt", "Checkpoint", "CommitmentWindow",
    "Witness", "Anchor",
    "merkle_root", "merkle_path", "verify_merkle_path",
]

_HASH = "sha256"
_PREFIX = f"{_HASH}:"


def _digest(payload: bytes) -> str:
    return _PREFIX + hashlib.sha256(payload).hexdigest()


def _require_digest(value: str, what: str) -> str:
    """A digest names its algorithm or it is refused.

    The one anchor Vitruvyan has published carries a bare 64-hex string, so a
    verifier in 2034 would have to guess which function to recompute. ADR-020
    records that as a value published in a format nobody chose; this refuses to
    produce another one.
    """
    if not isinstance(value, str) or not value.startswith(_PREFIX):
        raise ValueError(f"{what} must be prefixed with {_PREFIX!r}")
    body = value[len(_PREFIX):]
    if len(body) != 64 or any(c not in "0123456789abcdef" for c in body):
        raise ValueError(f"{what} must be 64 lowercase hex characters")
    return value


class AssuranceMode(str, Enum):
    """What a receipt is entitled to claim (ADR-020 decision 4).

    Recorded, never enforced. A run whose witness was unreachable achieves
    LOCAL and says so; it does not fail, and nobody may describe it as
    WITNESSED afterwards.
    """

    LOCAL = "local"
    WITNESSED = "witnessed"
    QUALIFIED = "qualified"


class CommitmentKind(str, Enum):
    BEGIN = "begin"
    END = "end"


@dataclass(frozen=True, slots=True)
class WitnessAck:
    """An independent party's statement that it already held a commitment.

    ``independent`` is deliberately absent. Independence is a property of *who
    runs the witness*, which this process cannot determine and must not assert;
    a reader judges it from ``witness_id`` (ADR-021 decision 3).
    """

    witness_id: str
    position: int
    acknowledged_at: str
    signature: str
    algorithm: str = "ed25519"

    def __post_init__(self) -> None:
        if not self.witness_id:
            raise ValueError("a witness acknowledgement names its witness")
        if self.position < 0:
            raise ValueError("witness position is a non-negative index")

    def to_dict(self) -> dict[str, Any]:
        return {
            "witness_id": self.witness_id, "position": self.position,
            "acknowledged_at": self.acknowledged_at,
            "algorithm": self.algorithm, "signature": self.signature,
        }


@dataclass(frozen=True, slots=True)
class AnchorReceipt:
    """Where a checkpoint was published, and whether that has finished.

    ``state`` is not decoration. An OpenTimestamps proof is incomplete until its
    calendar commitment reaches Bitcoin, and a verifier that reports EXISTENCE
    for a pending proof states something false (ADR-020 decision 7).
    """

    anchor_id: str
    network: str
    checkpoint: str
    state: Literal["pending", "anchored"]
    reference: str | None = None
    published_at: str | None = None
    proof: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.state not in ("pending", "anchored"):
            raise ValueError("an anchor receipt is pending or anchored")
        if self.state == "anchored" and not self.reference:
            raise ValueError("an anchored receipt names the transaction that anchors it")
        _require_digest(self.checkpoint, "anchored checkpoint")

    def to_dict(self) -> dict[str, Any]:
        return {
            "anchor_id": self.anchor_id, "network": self.network,
            "checkpoint": self.checkpoint, "state": self.state,
            "reference": self.reference, "published_at": self.published_at,
            "proof": self.proof,
        }


@dataclass(frozen=True, slots=True)
class Commitment:
    """One BEGIN or END, as it enters a window.

    A BEGIN is written before the first node executes and carries no root — the
    run has not produced one. An END carries the derived root (ADR-019) and the
    reason the run terminated, on *every* terminal path, so that BEGIN without
    END stays rare enough to mean something.
    """

    kind: CommitmentKind
    tenant: str
    writer_id: str
    sequence: int
    run_id: str
    at: str
    nonce: str
    root: str | None = None
    outcome: str | None = None
    witness: WitnessAck | None = None

    def __post_init__(self) -> None:
        if not self.tenant or not self.writer_id:
            raise ValueError("a commitment names its tenant and its writer")
        if self.sequence < 0:
            raise ValueError("sequence is a non-negative index")
        if self.kind is CommitmentKind.BEGIN:
            if self.root is not None or self.outcome is not None:
                raise ValueError("a BEGIN precedes the outcome and cannot carry one")
        else:
            if not self.outcome:
                raise ValueError("an END names why the run terminated")
            if self.root is not None:
                _require_digest(self.root, "committed root")

    @property
    def mode(self) -> AssuranceMode:
        """What this commitment alone entitles a receipt to claim.

        QUALIFIED is never reached here: it needs an identity attestation and a
        qualified timestamp, neither of which this distribution produces.
        """
        return AssuranceMode.WITNESSED if self.witness else AssuranceMode.LOCAL

    def to_dict(self) -> dict[str, Any]:
        """The commitment as data. The witness ACK is NOT part of it.

        A witness acknowledges the commitment, so the commitment cannot contain
        the acknowledgement without the leaf digest depending on a value that
        did not exist when the witness saw it.
        """
        body: dict[str, Any] = {
            "kind": self.kind.value, "tenant": self.tenant,
            "writer_id": self.writer_id, "sequence": self.sequence,
            "run_id": self.run_id, "at": self.at, "nonce": self.nonce,
        }
        if self.kind is CommitmentKind.END:
            body["root"] = self.root
            body["outcome"] = self.outcome
        return body

    @property
    def leaf(self) -> str:
        return _digest(_canonical_bytes(self.to_dict()))


# --------------------------------------------------------------------------
# The window
# --------------------------------------------------------------------------

def _pair(left: str, right: str) -> str:
    return _digest(b"\x01" + left.encode() + b"\x00" + right.encode())


def merkle_root(leaves: tuple[str, ...]) -> str:
    """The window's root.

    An odd node is promoted rather than duplicated. Duplicating it is the
    classic construction and it is the classic vulnerability: a tree of n and a
    tree of n+1 whose last leaf repeats produce the same root, so two different
    windows would present one value.
    """
    if not leaves:
        raise ValueError("an empty window has no root")
    level = list(leaves)
    while len(level) > 1:
        nxt: list[str] = []
        for i in range(0, len(level) - 1, 2):
            nxt.append(_pair(level[i], level[i + 1]))
        if len(level) % 2:
            nxt.append(level[-1])
        level = nxt
    return level[0]


def merkle_path(leaves: tuple[str, ...], index: int) -> tuple[tuple[str, str], ...]:
    """Siblings from leaf to root, each tagged with the side it sits on.

    This is the whole disclosure: sibling digests, never the commitments they
    stand for. A reader learns that other runs exist in this window — they
    cannot learn what any of them was, or whose.
    """
    if not 0 <= index < len(leaves):
        raise IndexError("no such leaf in this window")
    path: list[tuple[str, str]] = []
    level = list(leaves)
    while len(level) > 1:
        nxt: list[str] = []
        for i in range(0, len(level) - 1, 2):
            if index == i:
                path.append(("right", level[i + 1]))
            elif index == i + 1:
                path.append(("left", level[i]))
            nxt.append(_pair(level[i], level[i + 1]))
        if len(level) % 2:
            nxt.append(level[-1])
        index //= 2
        level = nxt
    return tuple(path)


def verify_merkle_path(leaf: str, path: tuple[tuple[str, str], ...], root: str) -> bool:
    current = leaf
    for side, sibling in path:
        if side == "left":
            current = _pair(sibling, current)
        elif side == "right":
            current = _pair(current, sibling)
        else:
            return False
    return current == root


@dataclass(frozen=True, slots=True)
class Checkpoint:
    """One window, sealed and linked to the window before it.

    ``count`` is committed on purpose. A window rebuilt with a leaf removed
    moves both the root and the count, and the count is the value a witness's
    independent tally can be compared against — the tree alone cannot notice a
    leaf that was never offered to it.
    """

    tenant: str
    writer_id: str
    index: int
    window_root: str
    count: int
    first_sequence: int
    last_sequence: int
    sealed_at: str
    previous: str | None = None

    def __post_init__(self) -> None:
        _require_digest(self.window_root, "window root")
        if self.previous is not None:
            _require_digest(self.previous, "previous checkpoint")
        if self.count <= 0:
            raise ValueError("a checkpoint seals at least one commitment")
        if self.last_sequence - self.first_sequence + 1 != self.count:
            raise ValueError(
                "the sealed range and the leaf count disagree: a commitment is "
                "missing from the window or the range is wrong")

    def to_dict(self) -> dict[str, Any]:
        return {
            "tenant": self.tenant, "writer_id": self.writer_id,
            "index": self.index, "window_root": self.window_root,
            "count": self.count, "first_sequence": self.first_sequence,
            "last_sequence": self.last_sequence, "sealed_at": self.sealed_at,
            "previous": self.previous,
        }

    @property
    def digest(self) -> str:
        """CP(n) = H(root(n) ‖ CP(n-1) ‖ meta(n)), as ADR-021 decision 2 states.

        The link is inside the digested body rather than concatenated after it,
        which is the correction ADR-019 had to make to the trace chain: a digest
        that does not cover its own link leaves the link free to be restated.
        """
        return _digest(_canonical_bytes(self.to_dict()))


@dataclass(slots=True)
class CommitmentWindow:
    """Commitments accumulating between two checkpoints, for ONE writer.

    One chain per writer, never one per tenant: a shared head would put a lock
    on every run start and make Motus slower the more a customer used it
    (ADR-021 decision 5).
    """

    tenant: str
    writer_id: str
    index: int = 0
    previous: str | None = None
    _leaves: list[str] = field(default_factory=list, init=False)
    _commitments: list[Commitment] = field(default_factory=list, init=False)
    _resume_at: int = field(default=0, init=False)

    def append(self, commitment: Commitment) -> int:
        if commitment.tenant != self.tenant or commitment.writer_id != self.writer_id:
            raise ValueError("this commitment belongs to another writer's chain")
        expected = self.next_sequence
        if commitment.sequence != expected:
            raise ValueError(
                f"out of order: this chain expects sequence {expected}, "
                f"the commitment claims {commitment.sequence}")
        self._commitments.append(commitment)
        self._leaves.append(commitment.leaf)
        return len(self._leaves) - 1

    @property
    def next_sequence(self) -> int:
        """Numbering is per WRITER and does not restart at a window boundary.

        A sequence that restarted every window would make "40 then 42" a
        statement about the window rather than about the writer, and the gap
        would stop meaning anything at exactly the boundary an operator can
        choose.
        """
        if self._commitments:
            return self._commitments[-1].sequence + 1
        return self._resume_at

    def resume_at(self, sequence: int) -> None:
        """Continue a writer's numbering across a checkpoint boundary."""
        if self._commitments:
            raise ValueError("a window in progress cannot be renumbered")
        self._resume_at = sequence

    def __len__(self) -> int:
        return len(self._leaves)

    @property
    def leaves(self) -> tuple[str, ...]:
        return tuple(self._leaves)

    def proof_for(self, index: int) -> tuple[tuple[str, str], ...]:
        return merkle_path(self.leaves, index)

    def seal(self, sealed_at: str) -> Checkpoint:
        """Close the window. Refuses to seal nothing.

        An empty checkpoint would assert that a writer produced no commitments
        in an interval, which is a claim this structure cannot support: silence
        and absence are the same shape here, and ADR-020 forbids reporting one
        as the other.
        """
        if not self._leaves:
            raise ValueError("an empty window is not evidence that nothing happened")
        return Checkpoint(
            tenant=self.tenant, writer_id=self.writer_id, index=self.index,
            window_root=merkle_root(self.leaves), count=len(self._leaves),
            first_sequence=self._commitments[0].sequence,
            last_sequence=self._commitments[-1].sequence,
            sealed_at=sealed_at, previous=self.previous,
        )


# --------------------------------------------------------------------------
# The two sockets. Neither is implemented here, by ADR-021 decision 4.
# --------------------------------------------------------------------------

@runtime_checkable
class Witness(Protocol):
    """Somebody who will say they already held this before the run finished.

    It is called with the canonical bytes of a BEGIN, before the first node
    executes. Returning ``None``, timing out or raising all mean the same thing
    to the runtime: **continue**, at LOCAL. There is deliberately no
    configuration that lets a witness stop a run — an option to block on a third
    party is one somebody enables without imagining the outage.
    """

    def acknowledge(self, commitment: bytes) -> WitnessAck | None: ...


@runtime_checkable
class Anchor(Protocol):
    """Somewhere a checkpoint can be published that we cannot rewrite.

    Called with a checkpoint, never with a run and never with a trace: anchor
    submissions scale with checkpoints, not with decisions taken. Publishing the
    same checkpoint to two networks is the recommended posture.
    """

    def publish(self, checkpoint: bytes) -> AnchorReceipt: ...

    def state(self, receipt: AnchorReceipt) -> Literal["pending", "anchored"]: ...
