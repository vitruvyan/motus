"""An OpenTimestamps anchor for Motus checkpoints.

ADR-021 decision 4 names the interface; ADR-021 also says **no implementation
of it ships in `vitruvyan-motus`**, which is why this is a separate
distribution and not an extra. ADR-020 rejected putting an anchor in the
runtime for a reason that applies to us as much as to TRON: *one import makes
the answer to "what if we do not use this one?" a fork.*

What this buys, and it is the whole argument for the default:

* free, so no treasury runs dry on a Saturday night;
* no wallet and no key, so there is nothing to lose and nothing to leak;
* Bitcoin, so the thing being trusted is not us.

What it costs is **latency**, and the cost is not smoothed over anywhere below.
"""

from __future__ import annotations

import hashlib
import io
from datetime import datetime, timezone
from typing import Any, Iterable

__all__ = ["OpenTimestampsAnchor", "NETWORK", "DEFAULT_CALENDARS"]

#: The value that lands in `AnchorReceipt.network`, and the string a verifier
#: matches against its own allow-list. Namespaced so that "bitcoin" alone can
#: never be read as "we watched a Bitcoin node": what this anchor knows is what
#: an OpenTimestamps calendar told it.
NETWORK = "opentimestamps:bitcoin"

#: The public calendars. Submitting to several is the recommended posture and
#: not an edge case (ADR-021 decision 4): each is an independent operator, and
#: a commitment held by one of them is a commitment held by somebody who can
#: lose it.
DEFAULT_CALENDARS = (
    "https://alice.btc.calendar.opentimestamps.org",
    "https://bob.btc.calendar.opentimestamps.org",
    "https://finney.calendar.eternitywall.com",
)


def _directly_verified(stamp: Any):
    """Descend until a sub-timestamp carries attestations, then stop.

    This is the shape the first `upgrade()` got wrong. A proof is a tree and
    the attestation that names a calendar is not at its root: in the proofs
    this project produces it sits four `sha256` operations down. Anything that
    looks only at `timestamp.ops` is asking about a commitment nobody made a
    promise about.
    """
    if stamp.attestations:
        yield stamp
        return
    for sub in stamp.ops.values():
        yield from _directly_verified(sub)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace(
        "+00:00", "Z")


class OpenTimestampsAnchor:
    """Publish a checkpoint digest to OpenTimestamps calendars.

    The object is stateless between calls: everything needed to resume an
    upgrade later lives in the receipt, which is the artefact that gets stored
    and handed on. An anchor that kept state a receipt did not carry would make
    the receipt insufficient, and the receipt is the whole product.
    """

    def __init__(self, calendars: Iterable[str] = DEFAULT_CALENDARS,
                 *, timeout: float = 10.0, anchor_id: str = "opentimestamps",
                 block_time: Any = None) -> None:
        self._calendars = tuple(calendars)
        if not self._calendars:
            raise ValueError(
                "an anchor with no calendar publishes nowhere, and would "
                "return receipts that look exactly like published ones")
        if not timeout > 0:
            raise ValueError("timeout must be a positive number of seconds")
        self._timeout = float(timeout)
        self._anchor_id = anchor_id

    # -- publishing --------------------------------------------------------
        #: height -> ISO-8601 UTC, or None. An OpenTimestamps attestation
        #: proves "committed under the merkle root of block N" and says
        #: nothing about the hour; turning N into a time is a fact of the
        #: blockchain, and this anchor has no node. The embedder supplies the
        #: resolver, and by supplying it decides whom to believe about it.
        self._block_time = block_time

    def publish(self, checkpoint: bytes) -> Any:
        """Submit a checkpoint digest. **Always returns `pending`.**

        An OpenTimestamps commitment reaches Bitcoin when a block does, which
        is hours away. ADR-020 decision 7: a verifier reporting `EXISTENCE` for
        a pending proof states something false — so this never rounds a
        submission up to a publication, however many calendars accepted it.

        Accepted by ONE calendar is enough to return; the others are redundancy
        and not a quorum. A calendar that refuses is recorded in the proof
        rather than swallowed, because "three calendars hold this" and "one
        does" are different facts about how recoverable the commitment is.
        """
        from opentimestamps.calendar import RemoteCalendar
        from opentimestamps.core.op import OpSHA256
        from opentimestamps.core.timestamp import Timestamp

        digest = self._digest_bytes(checkpoint)
        timestamp = Timestamp(digest)
        accepted: list[str] = []
        refused: dict[str, str] = {}
        for url in self._calendars:
            try:
                partial = RemoteCalendar(url).submit(digest, timeout=self._timeout)
                timestamp.merge(partial)
                accepted.append(url)
            except Exception as exc:                      # noqa: BLE001
                refused[url] = f"{type(exc).__name__}: {exc}"
        if not accepted:
            raise RuntimeError(
                "no calendar accepted this checkpoint: "
                + "; ".join(f"{k} {v}" for k, v in refused.items())
                + ". Nothing was published, and a receipt saying otherwise "
                  "would be the only lie this component could tell")

        return self._receipt(
            checkpoint=self._digest_string(checkpoint),
            state="pending",
            reference=None,
            published_at=None,
            proof={
                "format": "ots",
                "network": NETWORK,
                "digest": digest.hex(),
                "serialized": self._serialize(digest, timestamp).hex(),
                "calendars_accepted": accepted,
                "calendars_refused": refused,
                "submitted_at": _now(),
            },
        )

    # -- confirming --------------------------------------------------------

    def state(self, receipt: Any) -> str:
        """`pending` or `anchored`, asked of the calendars and not of the file.

        Reading `receipt.state` back would answer whatever the holder last
        wrote. This re-derives it from the proof: `anchored` means a Bitcoin
        block header attestation is present, which is a claim about a block and
        not about us.
        """
        upgraded = self.upgrade(receipt)
        return upgraded.state

    def upgrade(self, receipt: Any) -> Any:
        """Ask whether the commitment has reached a block yet.

        Returns a NEW receipt. The old one stays valid — it was true when it
        was written, and a pending proof does not become false by being
        superseded.

        ## This reported `pending` for 67 hours on evidence Bitcoin already held

        The first version of this method walked ONE level of the timestamp and
        asked each configured calendar about `sub.msg` at that depth. A real
        OpenTimestamps proof is a tree, and the `PendingAttestation` that names
        a calendar sits several operations down — after four `sha256` steps in
        the proofs this project produces. So the calendars were being asked
        about a commitment nobody had promised, they answered "not found", and
        the broad `except Exception: continue` below turned that into silence.

        Two defects, and the second is what made the first invisible:

        1. **A shape assumption about a tree.** The walk has to descend until
           it finds attestations, which is what `_directly_verified` does.
        2. **A swallow that could not tell a wrong question from an outage.**
           The intention was right — an unreachable calendar must never become
           a verdict — but the same clause hid a bug in our own request for
           three days, and reported the evidence as unconfirmed to every
           visitor of the demo while three independent calendars had already
           anchored it in blocks 962787, 962789 and 962798.

        So failures are now COLLECTED and returned on the receipt. `pending`
        with an empty `unreachable` list means the calendars were asked and
        have nothing yet; `pending` with entries in it means we could not ask,
        which is a different fact and must not wear the same word.

        The calendar asked is the one named in the attestation, not whichever
        calendars this instance happens to be configured with — a promise is
        owed by whoever made it. It still has to be one we are willing to talk
        to, so the configured set acts as the whitelist.
        """
        from opentimestamps.calendar import RemoteCalendar
        from opentimestamps.core.notary import (
            BitcoinBlockHeaderAttestation, PendingAttestation,
        )

        proof = dict(getattr(receipt, "proof", None) or {})
        if proof.get("format") != "ots":
            raise ValueError(
                "this receipt does not carry an OpenTimestamps proof, so this "
                "anchor has nothing to upgrade and will not guess")
        timestamp = self._deserialize(bytes.fromhex(proof["serialized"]))

        # **THE PROOF MUST BE A PROOF OF THIS CHECKPOINT.** `proof` is an
        # ordinary mutable field on a receipt the holder keeps, so without this
        # they can drop in an already-anchored `.ots` for an unrelated digest,
        # call upgrade(), and receive a receipt saying THEIR checkpoint reached
        # a Bitcoin block. The Bitcoin attestation is genuine; what it attests
        # is somebody else's document. Three values have to agree — the
        # timestamp's own message, the digest recorded beside it, and the
        # checkpoint the receipt names — and any two of them agreeing is not
        # enough, because the attack supplies a matched pair.
        stated = bytes.fromhex(proof["digest"])
        if timestamp.msg != stated:
            raise ValueError(
                f"this proof timestamps {timestamp.msg.hex()} and the receipt "
                f"records {proof['digest']}. A proof of another document says "
                "nothing about this checkpoint, however real its attestation")
        if self._digest_bytes(receipt.checkpoint) != stated:
            raise ValueError(
                f"this receipt names checkpoint {receipt.checkpoint} and "
                f"carries a proof of {proof['digest']}. Upgrading it would "
                "publish somebody else's attestation under this checkpoint's "
                "name")

        # A LIST, not a dict keyed by calendar. The first version popped the
        # entry on success, so a calendar that answered one commitment and
        # failed another came out looking entirely healthy — a partial outage
        # wearing the face of a working one. The mutation probe found it, which
        # is what the probe is for. What a receipt reader needs is which
        # commitment could not be asked about, and of whom.
        unreachable: list[dict[str, str]] = []
        # A calendar returns the whole path it knows, so one pass is normally
        # enough. The loop is for the case where a merge exposes a pending
        # attestation that was not reachable before; it stops as soon as a pass
        # changes nothing, and the bound is a guard rather than an algorithm.
        for _ in range(4):
            asked_any = False
            for sub in list(_directly_verified(timestamp)):
                for attestation in list(sub.attestations):
                    if not isinstance(attestation, PendingAttestation):
                        continue
                    uri = attestation.uri
                    if isinstance(uri, bytes):
                        uri = uri.decode("utf-8", "replace")
                    if uri not in self._calendars:
                        unreachable.append({
                            "calendar": uri,
                            "commitment": sub.msg.hex(),
                            "reason": "the proof names a calendar this anchor "
                                      "is not configured to talk to",
                        })
                        continue
                    try:
                        fresh = RemoteCalendar(uri).get_timestamp(
                            sub.msg, timeout=self._timeout)
                    except Exception as error:            # noqa: BLE001
                        # Still not a verdict — but no longer silent. A
                        # calendar that cannot answer leaves the receipt as it
                        # was, and says so on the receipt.
                        unreachable.append({
                            "calendar": uri,
                            "commitment": sub.msg.hex(),
                            "reason": f"{type(error).__name__}: {error}",
                        })
                        continue
                    sub.merge(fresh)
                    asked_any = True
            if not asked_any:
                break

        heights = sorted(
            a.height for a in self._attestations(timestamp)
            if isinstance(a, BitcoinBlockHeaderAttestation))
        proof["serialized"] = self._serialize(
            bytes.fromhex(proof["digest"]), timestamp).hex()
        proof["upgraded_at"] = _now()
        proof["calendars_unreachable"] = unreachable
        if not heights:
            proof["bitcoin_block_heights"] = []
            return self._receipt(
                checkpoint=receipt.checkpoint, state="pending",
                reference=None, published_at=None, proof=proof)

        proof["bitcoin_block_heights"] = heights
        return self._receipt(
            checkpoint=receipt.checkpoint,
            state="anchored",
            # The reference names a BLOCK, not a transaction, and says so. An
            # OpenTimestamps attestation is "the commitment is under the merkle
            # root of block N"; calling that a txid would invite a reader to
            # look for one.
            reference=f"bitcoin-block:{heights[0]}",
            # **NOT `upgraded_at`.** That is the clock of whichever machine
            # happened to run this method, and the first version published it
            # as the moment the evidence was timestamped — three days late, and
            # sourced from us. On a receipt whose entire purpose is that you do
            # not have to take our word for a time, our own clock is the one
            # value that must never appear in this field.
            #
            # What the attestation proves is "committed under the merkle root
            # of block N". Turning N into a wall-clock time is a fact of the
            # blockchain, available to anyone with a node, and this anchor does
            # not have one — so it publishes the block and stays quiet about
            # the hour rather than inventing it.
            published_at=self._resolve_block_time(heights[0]),
            proof=proof,
        )

    def _resolve_block_time(self, height: int) -> str:
        """The time of the block, from whoever the embedder decided to ask.

        **The first version put `_now()` here** — the clock of whichever
        machine happened to run the upgrade, three days after the block in the
        case that exposed it, published on a receipt whose whole purpose is
        that you do not have to take our word for a time. It was not false
        (the evidence did exist by then) but it was ours, and the demo rendered
        it as "Evidence timestamp", which made it wrong on screen.

        The contract is right to require a time on an anchored receipt: one
        without a time supports the existence of nothing in particular. So this
        refuses rather than substituting. An anchor that cannot say when has to
        say that, and let the embedder decide whether an explorer, a node or
        nothing at all is the answer.
        """
        if self._block_time is None:
            raise ValueError(
                f"this commitment is under Bitcoin block {height}, and an "
                f"anchored receipt has to carry the time it was published. An "
                f"OpenTimestamps attestation names the block and not the hour, "
                f"and this anchor has no Bitcoin node — so pass "
                f"`block_time=` a callable taking a height and returning an "
                f"ISO-8601 UTC string. Substituting this machine's own clock "
                f"would publish our word as the external proof, which is the "
                f"one thing this receipt exists to avoid")
        value = self._block_time(height)
        if not isinstance(value, str) or not value.strip():
            raise ValueError(
                f"the block-time resolver returned {value!r} for block "
                f"{height}; an anchored receipt needs a non-blank timestamp")
        return value

    # -- the parts that are only arithmetic --------------------------------

    @staticmethod
    def _digest_bytes(checkpoint: bytes | str) -> bytes:
        """32 bytes, whatever the caller had.

        A caller holding `Checkpoint.digest` has `sha256:<hex>`; one following
        the Protocol literally has bytes. Both are accepted and both reach the
        same commitment, because a plug that silently timestamped the ASCII of
        a digest would produce proofs that verify against nothing anybody has.
        """
        if isinstance(checkpoint, str):
            body = checkpoint.split(":", 1)[-1]
            return bytes.fromhex(body)
        if len(checkpoint) == 32:
            return bytes(checkpoint)
        return hashlib.sha256(checkpoint).digest()

    @staticmethod
    def _digest_string(checkpoint: bytes | str) -> str:
        if isinstance(checkpoint, str) and checkpoint.startswith("sha256:"):
            return checkpoint
        return "sha256:" + OpenTimestampsAnchor._digest_bytes(checkpoint).hex()

    @staticmethod
    def _serialize(digest: bytes, timestamp: Any) -> bytes:
        from opentimestamps.core.op import OpSHA256
        from opentimestamps.core.timestamp import DetachedTimestampFile
        from opentimestamps.core.serialize import BytesSerializationContext

        detached = DetachedTimestampFile(OpSHA256(), timestamp)
        context = BytesSerializationContext()
        detached.serialize(context)
        return context.getbytes()

    @staticmethod
    def _deserialize(raw: bytes) -> Any:
        from opentimestamps.core.timestamp import DetachedTimestampFile
        from opentimestamps.core.serialize import BytesDeserializationContext

        return DetachedTimestampFile.deserialize(
            BytesDeserializationContext(raw)).timestamp

    @staticmethod
    def _attestations(timestamp: Any) -> list[Any]:
        found = list(timestamp.attestations)
        for sub in timestamp.ops.values():
            found.extend(OpenTimestampsAnchor._attestations(sub))
        return found

    def _receipt(self, **fields: Any) -> Any:
        """Build the receipt, using Motus's own type when it is installed.

        Importing it lazily keeps this package usable for somebody who wants
        the proof machinery without the runtime, and keeps the failure mode
        legible when it is not installed: an ImportError naming Motus rather
        than an AttributeError three frames deep.
        """
        from vitruvyan_motus.commitments import AnchorReceipt

        return AnchorReceipt(anchor_id=self._anchor_id, network=NETWORK, **fields)
