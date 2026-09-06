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
import re
import threading
from dataclasses import dataclass, field
from collections.abc import Callable
from enum import Enum
from collections.abc import Sequence
from typing import Any, Literal, Protocol, runtime_checkable

from vitruvyan_motus.trace import _canonical_bytes, _strict_plain_json

__all__ = [
    "AssuranceMode", "CommitmentKind", "Commitment", "Continuation", "WitnessAck",
    "AnchorReceipt", "Checkpoint", "TenantCheckpoint", "CommitmentWindow",
    "Witness", "Anchor",
    "merkle_root", "merkle_path", "verify_merkle_path", "verify_inclusion",
]

_HASH = "sha256"
_PREFIX = f"{_HASH}:"

# The shape `TraceBundle.fingerprint` produces. Matched rather than assumed:
# a continuation's whole worth is that a verifier can recompute this value
# over the predecessor's bundle, so a value in a shape nothing recomputes is
# a link that cannot be followed.
_BUNDLE = re.compile(r"bundle:sha256:[0-9a-f]{64}")

# RFC 6962's construction, and for its reason: a leaf and an internal node are
# hashed in SEPARATE domains, so no commitment's digest can ever equal a node
# of any tree. An adversarial round found this held here already -- but by
# ACCIDENT, because a leaf's preimage is canonical JSON and always starts with
# `{` while an internal node's started with \x01. That is a property of the
# encoder, not a decision, and a format that must verify in ten years cannot
# rest on one.
_LEAF = b"\x00"
_NODE = b"\x01"
_CHECKPOINT = b"\x02"
_TENANT = b"\x03"


def _digest(payload: bytes) -> str:
    return _PREFIX + hashlib.sha256(payload).hexdigest()


def _require_text(value: Any, what: str) -> str:
    """Present, a string, and not merely whitespace.

    `if not value` accepts three spaces, which names nothing. A round found the
    same hole at five sites: a tenant, a writer, a witness, a termination
    reason and an anchor's transaction reference could each be blank-but-true.

    **And a second round found a worse one at the same five sites.** A string
    that is not Unicode text passed here, reached `_canonical_bytes` two layers
    down, and raised there — by which time `CommitmentLog.begin` had already
    fsynced a window line the store could never digest, leaving a chain that
    raises `CommitmentLogFork` on every future open. Refusing at the gate costs
    an `isascii()` branch and makes that state unreachable through this API.

    The refusal is the boundary's, not this function's invention: a Motus
    string denotes a sequence of Unicode scalar values (rule J1, ADR-026), and
    ADR-021 decision 6 leaves the FORMAT of an identifier to the embedder
    without leaving them a string that cannot be written down.
    """
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{what} must be a non-blank string")
    if not value.isascii():
        try:
            value.encode("utf-8")
        except UnicodeEncodeError as exc:
            raise ValueError(
                f"{what} carries U+{ord(value[exc.start]):04X} at index "
                f"{exc.start}, an unpaired surrogate: it denotes no character "
                "and has no UTF-8 encoding, so a commitment naming it has no "
                "digest (rule J1, ADR-026)") from None
    return value


def _require_index(value: Any, what: str) -> int:
    """A non-negative int -- and `bool` does not count as one here.

    `True` passes `isinstance(x, int)` and serialises as JSON `true`, so a
    sequence of `True` would enter a digest as a different TYPE than every
    other sequence in the chain.
    """
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError(f"{what} must be a non-negative integer")
    return value


_IDENTIFIER_MAX_LENGTH = 200  # mirrored from commitment.v1.schema.json $defs.Identifier


def _require_identifier(value: Any, what: str) -> str:
    _require_text(value, what)
    if len(value) > _IDENTIFIER_MAX_LENGTH:
        raise ValueError(
            f"{what} is {len(value)} characters long; the schema's Identifier "
            f"bounds it to at most {_IDENTIFIER_MAX_LENGTH} characters")
    return value


def _require_timestamp(value: Any, what: str) -> str:
    # commitment.v1 deliberately has no RFC3339 requirement today; this is
    # exactly its non-blank Timestamp bound. See ADR-021 and schema parity test.
    return _require_text(value, what)


def _require_digest(value: Any, what: str) -> str:
    """A digest names its algorithm or it is refused.

    The one anchor Vitruvyan has published carries a bare 64-hex string, so a
    verifier in 2034 would have to guess which function to recompute. ADR-020
    records that as a value published in a format nobody chose; this refuses to
    produce another one.
    """
    if not isinstance(value, str) or not value.startswith(_PREFIX):
        raise ValueError(f"{what} must be prefixed with {_PREFIX!r} (schema type Digest)")
    body = value[len(_PREFIX):]
    if len(body) != 64 or any(c not in "0123456789abcdef" for c in body):
        raise ValueError(f"{what} must be 64 lowercase hex characters (schema type Digest)")
    return value


def _require_bundle_fingerprint(value: Any, what: str) -> str:
    if not isinstance(value, str) or not _BUNDLE.fullmatch(value):
        raise ValueError(
            f"{what} must match bundle:sha256:<64 lowercase hex> "
            "(schema type BundleFingerprint)")
    return value


@dataclass(frozen=True, slots=True)
class _SchemaType:
    name: str
    validate: Callable[[Any, str], str]


_IDENTIFIER = _SchemaType("Identifier", _require_identifier)
_DIGEST = _SchemaType("Digest", _require_digest)
_TIMESTAMP = _SchemaType("Timestamp", _require_timestamp)
_BUNDLE_FINGERPRINT = _SchemaType("BundleFingerprint", _require_bundle_fingerprint)


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

    ``commitment`` is the leaf digest this acknowledges, and it is REQUIRED. An
    adversarial round found the first version named no commitment at all: an
    acknowledgement obtained for one run could be pasted onto a fabricated one
    and both reported WITNESSED. Motus does not verify the signature -- it may
    hold no key -- but an acknowledgement that does not say WHAT it
    acknowledges cannot be verified by anyone who does, ever, which makes it
    decoration rather than evidence.

    ``independent`` is deliberately absent. Independence is a property of *who
    runs the witness*, which this process cannot determine and must not assert;
    a reader judges it from ``witness_id`` (ADR-021 decision 3).
    """

    witness_id: str
    commitment: str
    position: int
    acknowledged_at: str
    signature: str
    algorithm: str = "ed25519"

    def __post_init__(self) -> None:
        _validate_schema_fields(self)
        _require_index(self.position, "witness position")

    def to_dict(self) -> dict[str, Any]:
        return {
            "witness_id": self.witness_id, "commitment": self.commitment,
            "position": self.position, "acknowledged_at": self.acknowledged_at,
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
        isolated = _validate_schema_fields(self)
        if "proof" in isolated:
            object.__setattr__(self, "proof", isolated["proof"])
        if self.state == "anchored":
            if self.reference is None:
                raise ValueError("an anchored receipt's transaction reference is required")
            if self.published_at is None:
                raise ValueError("an anchored receipt's publication time is required")
            # The contract requires it (receipt.v1), and the producer did not,
            # so this class could build an object the validator refuses --
            # authority order backwards. `anchored` without a time says the
            # publication finished and declines to say when, which is the one
            # thing an EXISTENCE claim is about.

    def to_dict(self) -> dict[str, Any]:
        return {
            "anchor_id": self.anchor_id, "network": self.network,
            "checkpoint": self.checkpoint, "state": self.state,
            "reference": self.reference, "published_at": self.published_at,
            # Revalidate and isolate again so mutation through the public
            # dict value cannot reach the wire without a path-aware J1 refusal.
            "proof": _strict_plain_json(
                self.proof, path="AnchorReceipt.proof"
            ),
        }


@dataclass(frozen=True, slots=True)
class Continuation:
    """What a resumed segment's BEGIN says about the segment it continues.

    ADR-023. A resumed run takes a NEW run_id and starts a fresh trace chain,
    so the durable account of a crash-and-resume is an unpaired BEGIN followed
    by an unrelated pair — with nothing connecting them. The trace documents
    carry the link; the commitment log, which is the artefact an auditor walks
    and the only one an anchor covers, did not carry it at all.

    **`run_id` does not name the predecessor and cannot.** A run_id may repeat
    — a retried job keeps its id — so a chain can hold several unpaired BEGINs
    under one, and a BEGIN carries no digest of its own segment to tell them
    apart. `(writer_id, sequence)` does name one: ADR-021 numbers per writer
    and does not restart at a window boundary.

    Those two are **absent together** when the predecessor lives in a chain the
    resuming store does not hold — another machine, another writer's log we
    were never given. Their absence is the record saying *unresolved from
    here*, which is neither a confirmation nor a denial, because it is a claim
    about somebody else's chain.
    """

    run_id: str
    bundle_fingerprint: str
    writer_id: str | None = None
    sequence: int | None = None

    def __post_init__(self) -> None:
        _validate_schema_fields(self)
        if (self.writer_id is None) != (self.sequence is None):
            raise ValueError(
                "a continuation names its predecessor by BOTH writer_id and "
                "sequence, or by neither. One without the other is a "
                "coordinate with an axis missing, and it would read as "
                "resolved while naming a set")
        if self.writer_id is not None:
            _require_index(self.sequence, "a continuation's sequence")

    @property
    def resolved(self) -> bool:
        """Does this name one commitment, or only the run it came from?"""
        return self.writer_id is not None

    def to_dict(self) -> dict[str, Any]:
        body: dict[str, Any] = {
            "run_id": self.run_id,
            "bundle_fingerprint": self.bundle_fingerprint,
        }
        if self.writer_id is not None:
            body["writer_id"] = self.writer_id
            body["sequence"] = self.sequence
        return body


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
    continues: Continuation | None = None

    def __post_init__(self) -> None:
        _validate_schema_fields(self)
        if type(self.kind) is not CommitmentKind:
            raise ValueError("Commitment.kind must be a CommitmentKind")
        _require_index(self.sequence, "sequence")
        if self.kind is CommitmentKind.BEGIN:
            if self.root is not None or self.outcome is not None:
                raise ValueError("a BEGIN precedes the outcome and cannot carry one")
        else:
            if self.continues is not None:
                raise ValueError(
                    "an END cannot continue anything: a segment is continued "
                    "at the moment it starts, and an END written after the "
                    "fact could name a predecessor the run never resumed from")
            # Presence is a business rule; its schema bound was checked above.
            if self.outcome is None:
                raise ValueError("an END's outcome -- it names why the run terminated must be present")
            # `root=None` was accepted by the field default, so an END could
            # say a run finished and point at nothing: a receipt built on it
            # proves an outcome was CLAIMED, never which trace earned it.
            # `Trace.root` returns a value for every terminal kind, so a run
            # that ended has one; None means the document does not verify, and
            # an END is the wrong place to paper that over.
            _require_digest(self.root, "an END's root -- it binds the outcome "
                                       "to the evidence that produced it")
        if self.witness is not None and type(self.witness) is not WitnessAck:
            raise ValueError("Commitment.witness must be a WitnessAck")
        if self.continues is not None and type(self.continues) is not Continuation:
            raise ValueError("Commitment.continues must be a Continuation")
        if self.witness is not None and self.witness.commitment != self.leaf:
            raise ValueError(
                "this acknowledgement is for a different commitment: an ACK "
                "that does not name what it acknowledges is decoration")

    @property
    def mode(self) -> AssuranceMode:
        """What this commitment alone entitles a receipt to claim.

        QUALIFIED is never reached here: it needs an identity attestation and a
        qualified timestamp, neither of which this distribution produces.
        """
        # __post_init__ binds the ACK to this commitment's leaf, so WITNESSED
        # at least means "somebody acknowledged THIS". It does not mean the
        # signature was checked -- Motus may hold no key, and ADR-020 records
        # the mode rather than enforcing it. The binding is what gives a
        # verifier that DOES hold the key something to check against.
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
        # In the digest deliberately (ADR-023 decision 3): the link is what
        # makes a segment a segment, and outside the leaf it would sit outside
        # the checkpoint and outside whatever anchors the checkpoint. Written
        # only when it applies, so its absence is a fact rather than a default.
        if self.continues is not None:
            body["continues"] = self.continues.to_dict()
        return body

    @property
    def leaf(self) -> str:
        return _digest(_LEAF + _canonical_bytes(self.to_dict()))


# --------------------------------------------------------------------------
# The window
# --------------------------------------------------------------------------

def _pair(left: str, right: str) -> str:
    return _digest(_NODE + left.encode() + b"\x00" + right.encode())


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
    """Recompute a root from a digest and a path. NOT a proof of leafhood.

    This is the primitive and it cannot know what it was handed: a genuine
    INTERNAL node of the same tree, offered with the shorter path belonging to
    it, recomputes the root and returns True. That is not fixable inside this
    function -- it has no way to tell -- and it is why a verifier must never
    take a leaf digest out of an untrusted document. Use
    :func:`verify_inclusion`, which recomputes the leaf from the commitment;
    domain tagging then makes leafhood structural.
    """
    current = leaf
    for side, sibling in path:
        if side == "left":
            current = _pair(sibling, current)
        elif side == "right":
            current = _pair(current, sibling)
        else:
            return False
    return current == root


def verify_inclusion(
    commitment: "Commitment", path: tuple[tuple[str, str], ...], root: str
) -> bool:
    """Is THIS commitment under that window root?

    The leaf is recomputed from the commitment rather than accepted from the
    caller, which is the whole difference from :func:`verify_merkle_path`. A
    verifier that reads a leaf digest out of a receipt and checks a path
    against it proves that *some* digest is in the tree -- not that the run in
    front of it is.
    """
    return verify_merkle_path(commitment.leaf, path, root)


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
        _validate_schema_fields(self)
        _require_index(self.index, "checkpoint index")
        # Sealed from real commitments these can never be negative, because
        # Commitment refuses a negative sequence. The raw constructor did not
        # say so, and a Checkpoint is exactly what somebody builds by hand.
        _require_index(self.first_sequence, "first_sequence")
        _require_index(self.last_sequence, "last_sequence")
        _require_index(self.count, "checkpoint count")
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
        return _digest(_CHECKPOINT + _canonical_bytes(self.to_dict()))


# TenantCheckpoint has no contract schema counterpart yet; extend these tables
# if a tenant_checkpoint schema is introduced.
_SCHEMA_FIELDS: dict[type, dict[str, _SchemaType]] = {
    WitnessAck: {
        "witness_id": _IDENTIFIER, "commitment": _DIGEST,
        "acknowledged_at": _TIMESTAMP, "algorithm": _IDENTIFIER,
        "signature": _IDENTIFIER,
    },
    AnchorReceipt: {
        "anchor_id": _IDENTIFIER, "network": _IDENTIFIER, "checkpoint": _DIGEST,
        "reference": _IDENTIFIER, "published_at": _TIMESTAMP,
    },
    Continuation: {
        "run_id": _IDENTIFIER, "bundle_fingerprint": _BUNDLE_FINGERPRINT,
        "writer_id": _IDENTIFIER,
    },
    Commitment: {
        "tenant": _IDENTIFIER, "writer_id": _IDENTIFIER, "run_id": _IDENTIFIER,
        "at": _TIMESTAMP, "nonce": _IDENTIFIER, "root": _DIGEST, "outcome": _IDENTIFIER,
    },
    Checkpoint: {
        "tenant": _IDENTIFIER, "writer_id": _IDENTIFIER, "window_root": _DIGEST,
        "sealed_at": _TIMESTAMP, "previous": _DIGEST,
    },
}


_NON_SCHEMA_FIELDS: dict[type, dict[str, str]] = {
    WitnessAck: {"position": "integer; checked by _require_index"},
    AnchorReceipt: {
        "state": "closed enum",
        "proof": "JSON object (dict); contents are opaque",
    },
    Continuation: {"sequence": "optional integer; checked by _require_index"},
    Commitment: {
        "kind": "closed enum; checked by CommitmentKind",
        "sequence": "integer; checked by _require_index",
        "witness": "nested WitnessAck; delegates validation",
        "continues": "nested Continuation; delegates validation",
    },
    Checkpoint: {
        "index": "integer; checked by _require_index",
        "count": "non-negative integer; checked by _require_index; checkpoint invariant requires >= 1",
        "first_sequence": "integer; checked by _require_index",
        "last_sequence": "integer; checked by _require_index",
    },
}

# Precompute the per-class validation tuples once at import time.
_NULLABLE_SCHEMA_FIELDS = frozenset({
    (Commitment, "root"), (Commitment, "outcome"),
    (Checkpoint, "previous"), (Continuation, "writer_id"),
    (AnchorReceipt, "reference"), (AnchorReceipt, "published_at"),
})

# Dict-valued fields in schema-bound dataclasses are explicitly allow-listed:
# their contents still have to be values the JSON contract can carry.
_JSON_FIELDS: dict[type, frozenset[str]] = {
    AnchorReceipt: frozenset({"proof"}),
}

_SCHEMA_VALIDATORS: dict[type, tuple[tuple[str, Callable[[Any, str], str], str], ...]] = {
    cls: tuple(
        (name, schema_type.validate, schema_type.name)
        for name, schema_type in fields.items()
    )
    for cls, fields in _SCHEMA_FIELDS.items()
}


def _validate_schema_fields(instance: Any) -> dict[str, Any]:
    isolated: dict[str, Any] = {}
    labels = {
        (WitnessAck, "commitment"): "acknowledged commitment",
        (AnchorReceipt, "reference"): "an anchored receipt's transaction reference",
    }
    for name, validator, what in _SCHEMA_VALIDATORS.get(type(instance), ()):
        value = getattr(instance, name)
        if value is None and (type(instance), name) in _NULLABLE_SCHEMA_FIELDS:
            continue
        label = labels.get((type(instance), name),
                           f"{type(instance).__name__}.{name}")
        validator(value, f"{label} (schema type {what})")
    for name in _JSON_FIELDS.get(type(instance), ()):
        value = getattr(instance, name)
        if name == "proof" and not isinstance(value, dict):
            # Preserve the outer-object requirement separately from J1's
            # recursive value validation.
            raise ValueError("AnchorReceipt.proof must be a dict (schema type object)")
        isolated[name] = _strict_plain_json(
            value, path=f"{type(instance).__name__}.{name}"
        )
    return isolated


@dataclass(frozen=True, slots=True)
class TenantCheckpoint:
    """The set of writer heads a tenant observed, chained to the set before it.

    A per-writer `Checkpoint` commits to one writer and nothing else, so a
    writer could appear in one round and be absent from the next without any
    retained writer's chain changing by a byte -- and the disappearance signal
    ADR-021 decision 5 relies on would not exist. A round found the ADR
    promising it and the code not providing it.

    This is the aggregate that makes it real: the SET is committed, so dropping
    a writer moves the digest. What it still cannot do is notice a writer that
    was never declared -- that is `SYSTEM_COMPLETENESS`, conceded in ADR-020
    and conceded again one level down here.
    """

    tenant: str
    index: int
    heads: dict[str, str]                     # writer_id -> Checkpoint.digest
    sealed_at: str
    previous: str | None = None

    def __post_init__(self) -> None:
        _require_text(self.tenant, "a tenant checkpoint's tenant")
        _require_text(self.sealed_at, "a tenant checkpoint's seal time")
        _require_index(self.index, "tenant checkpoint index")
        if self.previous is not None:
            _require_digest(self.previous, "previous tenant checkpoint")
        if not self.heads:
            raise ValueError(
                "a tenant checkpoint with no writers asserts that nobody ran, "
                "which is the one thing this structure cannot support")
        for writer, head in self.heads.items():
            _require_text(writer, "a writer id in a tenant checkpoint")
            _require_digest(head, f"the head for writer {writer!r}")

    @classmethod
    def over(cls, checkpoints: Sequence[Checkpoint], *, index: int,
             sealed_at: str, previous: str | None = None) -> "TenantCheckpoint":
        """Aggregate one round of per-writer checkpoints.

        Refuses two checkpoints from one writer: which of them is the head is
        exactly the ambiguity this object exists to remove.
        """
        if not checkpoints:
            raise ValueError("a round with no checkpoints is not a round")
        tenants = {c.tenant for c in checkpoints}
        if len(tenants) != 1:
            raise ValueError(f"one tenant per aggregate, got {sorted(tenants)}")
        heads: dict[str, str] = {}
        for checkpoint in checkpoints:
            if checkpoint.writer_id in heads:
                raise ValueError(
                    f"writer {checkpoint.writer_id!r} appears twice in one "
                    "round: which one is the head is undecidable")
            heads[checkpoint.writer_id] = checkpoint.digest
        return cls(tenant=tenants.pop(), index=index, heads=heads,
                   sealed_at=sealed_at, previous=previous)

    def to_dict(self) -> dict[str, Any]:
        return {
            "tenant": self.tenant, "index": self.index,
            "heads": dict(sorted(self.heads.items())),
            "writer_count": len(self.heads),
            "sealed_at": self.sealed_at, "previous": self.previous,
        }

    @property
    def digest(self) -> str:
        return _digest(_TENANT + _canonical_bytes(self.to_dict()))

    def writers_missing_from(self, later: "TenantCheckpoint") -> tuple[str, ...]:
        """Writers present here and absent from a later round.

        Not a verdict. A writer stops for ordinary reasons -- a process
        retired, a shard moved -- so this reports a question, never
        suppression, for the same reason a BEGIN without an END does.
        """
        return tuple(sorted(set(self.heads) - set(later.heads)))


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
    _sealed: str | None = field(default=None, init=False)
    _lock: threading.Lock = field(default_factory=threading.Lock, init=False, repr=False)

    def __post_init__(self) -> None:
        """The public constructor makes a writer's FIRST window only.

        A continuing window comes from :meth:`following`, which derives index,
        link and next sequence from a real ``Checkpoint``. A round found the
        first version let a caller build a window with a hand-written
        ``previous`` and start numbering at zero again -- re-issuing a sequence
        already sealed, or skipping a range, with nothing raised anywhere. The
        boundary between windows is exactly where gap detection is supposed to
        hold, so it is the one place a caller does not fill in by hand.
        """
        _require_text(self.tenant, "a window's tenant")
        _require_text(self.writer_id, "a window's writer_id")
        if self.previous is not None or self.index != 0:
            raise ValueError(
                "a continuing window is built with CommitmentWindow.following("
                "checkpoint), which derives its link and its next sequence "
                "from a real checkpoint")

    @classmethod
    def following(cls, checkpoint: Checkpoint) -> "CommitmentWindow":
        """The next window for this writer, derived from the sealed one."""
        window = cls(tenant=checkpoint.tenant, writer_id=checkpoint.writer_id)
        window.index = checkpoint.index + 1
        window.previous = checkpoint.digest
        window._resume_at = checkpoint.last_sequence + 1
        return window

    def append(self, commitment: Commitment) -> int:
        """Append under a lock: check-then-act is a race without one.

        ADR-021 decision 5 refuses a lock ACROSS writers, which would serialise
        every run start in the deployment. Within one writer's own chain a lock
        costs nothing and stops two concurrent starts claiming one sequence --
        which a round reproduced under a forced interleaving, leaving the
        window permanently unsealable.
        """
        with self._lock:
            if self._sealed is not None:
                raise ValueError(
                    "this window is sealed: appending after a seal and sealing "
                    "again yields two checkpoints at one index sharing one "
                    "previous link, which is a fork at a chain position "
                    "dressed as a continuation. Open the next window with "
                    "CommitmentWindow.following()")
            if commitment.tenant != self.tenant or commitment.writer_id != self.writer_id:
                raise ValueError("this commitment belongs to another writer's chain")
            expected = self._next_sequence_locked()
            if commitment.sequence != expected:
                raise ValueError(
                    f"out of order: this chain expects sequence {expected}, "
                    f"the commitment claims {commitment.sequence}")
            self._commitments.append(commitment)
            self._leaves.append(commitment.leaf)
            return len(self._leaves) - 1

    def _next_sequence_locked(self) -> int:
        if self._commitments:
            return self._commitments[-1].sequence + 1
        return self._resume_at

    @property
    def next_sequence(self) -> int:
        """Numbering is per WRITER and does not restart at a window boundary.

        A sequence that restarted every window would make "40 then 42" a
        statement about the window rather than about the writer, and the gap
        would stop meaning anything at exactly the boundary an operator can
        choose.
        """
        with self._lock:
            return self._next_sequence_locked()

    def __len__(self) -> int:
        return len(self._leaves)

    @property
    def leaves(self) -> tuple[str, ...]:
        return tuple(self._leaves)

    def proof_for(self, index: int) -> tuple[tuple[str, str], ...]:
        return merkle_path(self.leaves, index)

    def _checkpoint_locked(self, sealed_at: str) -> Checkpoint:
        if self._sealed is not None:
            raise ValueError("this window is already sealed")
        if not self._leaves:
            raise ValueError("an empty window is not evidence that nothing happened")
        # ONE snapshot. Reading the root from `self.leaves` and then the
        # count and terminal sequence from the live lists lets a concurrent
        # append slip between them, and the checkpoint then passes its own
        # range-vs-count check while claiming a leaf its root does not
        # cover.
        return Checkpoint(
            tenant=self.tenant, writer_id=self.writer_id, index=self.index,
            window_root=merkle_root(tuple(self._leaves)), count=len(self._leaves),
            first_sequence=self._commitments[0].sequence,
            last_sequence=self._commitments[-1].sequence,
            sealed_at=sealed_at, previous=self.previous,
        )

    def checkpoint_at(self, sealed_at: str) -> Checkpoint:
        """Return a checkpoint snapshot without sealing this window."""
        with self._lock:
            return self._checkpoint_locked(sealed_at)

    def _mark_sealed_locked(self, checkpoint: Checkpoint) -> None:
        # A full rebuild-and-compare here cost 2x a seal (measured 1.67ms ->
        # 3.77ms at 1000 leaves) to reconfirm a root `checkpoint_at` already
        # computed from the same lock-held snapshot. Windows are append-only,
        # so the root cannot have changed unless one of these four O(1) facts
        # did -- an append moves count and last_sequence, nothing moves
        # first_sequence or index, and sealing itself is the fifth way this
        # snapshot could go stale.
        if (self._sealed is not None
                or len(self._leaves) != checkpoint.count
                or checkpoint.index != self.index
                or self._commitments[-1].sequence != checkpoint.last_sequence
                or self._commitments[0].sequence != checkpoint.first_sequence):
            raise ValueError("the live window changed after its checkpoint snapshot")
        self._sealed = checkpoint.digest

    def mark_sealed(self, checkpoint: Checkpoint) -> None:
        """Seal only if the live window is the one that was snapshotted."""
        with self._lock:
            self._mark_sealed_locked(checkpoint)

    def seal(self, sealed_at: str) -> Checkpoint:
        """Snapshot and mark a checkpoint atomically under one lock."""
        with self._lock:
            checkpoint = self._checkpoint_locked(sealed_at)
            self._mark_sealed_locked(checkpoint)
            return checkpoint

    @property
    def sealed(self) -> str | None:
        """The digest this window sealed to, or None while it is still open."""
        return self._sealed


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
