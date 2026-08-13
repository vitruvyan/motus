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
                 *, timeout: float = 10.0, anchor_id: str = "opentimestamps") -> None:
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
        """
        from opentimestamps.calendar import RemoteCalendar
        from opentimestamps.core.notary import BitcoinBlockHeaderAttestation

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

        for url in self._calendars:
            for commitment, sub in list(timestamp.ops.items()):
                if any(isinstance(a, BitcoinBlockHeaderAttestation)
                       for a in sub.attestations):
                    continue
                try:
                    fresh = RemoteCalendar(url).get_timestamp(
                        sub.msg, timeout=self._timeout)
                    sub.merge(fresh)
                except Exception:                          # noqa: BLE001
                    # A calendar that cannot answer leaves the receipt exactly
                    # as it was, which is the correct outcome: not upgraded is
                    # not the same as not anchored, and this method must never
                    # turn an outage into a verdict.
                    continue

        heights = sorted(
            a.height for a in self._attestations(timestamp)
            if isinstance(a, BitcoinBlockHeaderAttestation))
        proof["serialized"] = self._serialize(
            bytes.fromhex(proof["digest"]), timestamp).hex()
        proof["upgraded_at"] = _now()
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
            published_at=proof["upgraded_at"],
            proof=proof,
        )

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
