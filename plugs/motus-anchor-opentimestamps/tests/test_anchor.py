"""What this anchor will and will not say, checked without a network.

Every test here is offline. The calendars are real in a smoke run and faked
here on purpose: a suite that needs three third-party services to pass is a
suite that reports their weather, and the properties below are about this code.
"""

from __future__ import annotations

import hashlib
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))
from contract import validate

from motus_anchor_opentimestamps import NETWORK, OpenTimestampsAnchor

DIGEST = "sha256:" + hashlib.sha256(b"a checkpoint").hexdigest()


class _Calendar:
    """A calendar that hands back a timestamp for whatever it is given."""

    def __init__(self, url):
        self.url = url

    def submit(self, digest, timeout=None):
        from opentimestamps.core.timestamp import Timestamp
        from opentimestamps.core.op import OpAppend
        from opentimestamps.core.notary import PendingAttestation

        stamp = Timestamp(digest)
        child = stamp.ops.add(OpAppend(b"\x00"))
        child.attestations.add(PendingAttestation(self.url))
        return stamp

    def get_timestamp(self, commitment, timeout=None):
        from opentimestamps.core.timestamp import Timestamp
        return Timestamp(commitment)


class _Refusing(_Calendar):
    def submit(self, digest, timeout=None):
        raise OSError("calendar unreachable")

    def get_timestamp(self, commitment, timeout=None):
        raise OSError("calendar unreachable")


def _patch(monkeypatch, factory):
    monkeypatch.setattr("opentimestamps.calendar.RemoteCalendar", factory)


def test_publishing_is_always_pending(monkeypatch):
    """ADR-020 decision 7: a verifier reporting EXISTENCE for a pending proof
    states something false. An OpenTimestamps commitment reaches Bitcoin when a
    block does, so a submission accepted by every calendar in the world is
    still pending, and this never rounds it up."""
    _patch(monkeypatch, _Calendar)
    receipt = OpenTimestampsAnchor().publish(DIGEST)
    assert receipt.state == "pending"
    assert receipt.reference is None and receipt.published_at is None
    assert receipt.network == NETWORK
    assert receipt.checkpoint == DIGEST
    assert len(receipt.proof["calendars_accepted"]) == 3


def test_every_plug_receipt_shape_matches_the_anchor_schema(monkeypatch):
    validator = validate._registry_validator(
        "ots-anchor-shape-test", {
            "$ref": "https://vitruvyan.com/motus/contract/receipt.v1.schema.json#/$defs/Anchor"
        })
    _patch(monkeypatch, _Calendar)
    pending = OpenTimestampsAnchor().publish(DIGEST)
    upgraded_pending = OpenTimestampsAnchor().upgrade(pending)
    anchored_anchor = OpenTimestampsAnchor(
        block_time=lambda h: "2023-08-11T12:00:00Z")
    anchored = anchored_anchor.publish(DIGEST)
    stamp = anchored_anchor._deserialize(bytes.fromhex(anchored.proof["serialized"]))
    from opentimestamps.core.notary import BitcoinBlockHeaderAttestation
    next(iter(stamp.ops.values())).attestations.add(
        BitcoinBlockHeaderAttestation(812345))
    anchored.proof["serialized"] = anchored_anchor._serialize(
        bytes.fromhex(anchored.proof["digest"]), stamp).hex()
    upgraded_anchored = anchored_anchor.upgrade(anchored)
    _patch(monkeypatch, _Refusing)
    unreachable = OpenTimestampsAnchor().upgrade(pending)
    for receipt in (pending, upgraded_pending, upgraded_anchored, unreachable):
        assert list(validator.iter_errors(receipt.to_dict())) == []


def test_a_refusing_calendar_is_recorded_and_not_swallowed(monkeypatch):
    """"Three calendars hold this" and "one does" are different facts about how
    recoverable the commitment is, and a receipt that hides the difference has
    dropped the more useful one."""
    calls = {"n": 0}

    def factory(url):
        calls["n"] += 1
        return _Refusing(url) if calls["n"] == 1 else _Calendar(url)

    _patch(monkeypatch, factory)
    receipt = OpenTimestampsAnchor().publish(DIGEST)
    assert len(receipt.proof["calendars_accepted"]) == 2
    assert len(receipt.proof["calendars_refused"]) == 1
    assert "calendar unreachable" in next(iter(
        receipt.proof["calendars_refused"].values()))


def test_no_calendar_accepting_raises_rather_than_returning_a_receipt(monkeypatch):
    """Nothing was published, and a receipt saying otherwise would be the only
    lie this component is in a position to tell."""
    _patch(monkeypatch, _Refusing)
    with pytest.raises(RuntimeError, match="no calendar accepted"):
        OpenTimestampsAnchor().publish(DIGEST)


def test_an_anchor_with_no_calendar_is_refused():
    with pytest.raises(ValueError, match="publishes nowhere"):
        OpenTimestampsAnchor(calendars=())


def test_an_upgrade_that_finds_a_block_names_the_block_and_not_a_txid(monkeypatch):
    """An OpenTimestamps attestation says "this commitment is under the merkle
    root of block N". Calling that a transaction id would send a reader looking
    for one, and there is not one."""
    from opentimestamps.core.notary import BitcoinBlockHeaderAttestation

    _patch(monkeypatch, _Calendar)
    # The block time comes from whoever the embedder decided to ask. See
    # `_resolve_block_time`: the anchor used to put its own clock here.
    anchor = OpenTimestampsAnchor(
        block_time=lambda h: "2023-08-11T12:00:00Z",
        block_time_source="https://blockstream.info/api/")
    receipt = anchor.publish(DIGEST)

    timestamp = anchor._deserialize(bytes.fromhex(receipt.proof["serialized"]))
    for sub in timestamp.ops.values():
        sub.attestations.add(BitcoinBlockHeaderAttestation(812345))
    receipt.proof["serialized"] = anchor._serialize(
        bytes.fromhex(receipt.proof["digest"]), timestamp).hex()

    upgraded = anchor.upgrade(receipt)
    assert upgraded.state == "anchored"
    assert upgraded.reference == "bitcoin-block:812345"
    assert upgraded.published_at == "2023-08-11T12:00:00Z", (
        "the publication time is the block's, not this machine's")
    assert upgraded.proof["published_at_source"] == "https://blockstream.info/api/"
    assert upgraded.proof["bitcoin_block_heights"] == [812345]


def test_an_anchor_without_a_block_time_refuses_and_leaves_receipt_unchanged(
        monkeypatch):
    """The defect this rule exists for.

    `published_at` used to be `_now()` — the clock of whichever machine ran the
    upgrade. True (the evidence did exist by then) and ours, on a receipt whose
    purpose is that you do not have to take our word for a time. The demo
    rendered it as "Evidence timestamp" and it read three days late.
    """
    from opentimestamps.core.notary import BitcoinBlockHeaderAttestation

    _patch(monkeypatch, _Calendar)
    anchor = OpenTimestampsAnchor()                       # no resolver
    receipt = anchor.publish(DIGEST)

    timestamp = anchor._deserialize(bytes.fromhex(receipt.proof["serialized"]))
    for sub in timestamp.ops.values():
        sub.attestations.add(BitcoinBlockHeaderAttestation(812345))
    receipt.proof["serialized"] = anchor._serialize(
        bytes.fromhex(receipt.proof["digest"]), timestamp).hex()

    before = dict(receipt.proof)
    with pytest.raises(ValueError, match="block_time="):
        anchor.upgrade(receipt)
    assert receipt.proof == before


def test_a_supplied_block_time_failure_still_raises(monkeypatch):
    from opentimestamps.core.notary import BitcoinBlockHeaderAttestation

    _patch(monkeypatch, _Calendar)
    anchor = OpenTimestampsAnchor(block_time=lambda height: 1 / 0)
    receipt = anchor.publish(DIGEST)
    timestamp = anchor._deserialize(bytes.fromhex(receipt.proof["serialized"]))
    next(iter(timestamp.ops.values())).attestations.add(
        BitcoinBlockHeaderAttestation(812345))
    receipt.proof["serialized"] = anchor._serialize(
        bytes.fromhex(receipt.proof["digest"]), timestamp).hex()
    with pytest.raises(ZeroDivisionError):
        anchor.upgrade(receipt)


def test_an_unreachable_calendar_leaves_the_receipt_pending(monkeypatch):
    """Not upgraded is not the same as not anchored. An outage must never be
    turned into a verdict."""
    _patch(monkeypatch, _Calendar)
    anchor = OpenTimestampsAnchor()
    receipt = anchor.publish(DIGEST)

    _patch(monkeypatch, _Refusing)
    upgraded = anchor.upgrade(receipt)
    assert upgraded.state == "pending"
    assert upgraded.proof["bitcoin_block_heights"] == []


def test_state_re_derives_rather_than_reading_the_receipt_back(monkeypatch):
    """Reading `receipt.state` would answer whatever the holder last wrote."""
    _patch(monkeypatch, _Calendar)
    anchor = OpenTimestampsAnchor()
    receipt = anchor.publish(DIGEST)
    object.__setattr__(receipt, "state", "anchored")   # a holder's edit
    assert anchor.state(receipt) == "pending"

    timestamp = anchor._deserialize(bytes.fromhex(receipt.proof["serialized"]))
    from opentimestamps.core.notary import BitcoinBlockHeaderAttestation
    next(iter(timestamp.ops.values())).attestations.add(
        BitcoinBlockHeaderAttestation(812345))
    receipt.proof["serialized"] = anchor._serialize(
        bytes.fromhex(receipt.proof["digest"]), timestamp).hex()
    _patch(monkeypatch, _Refusing)
    assert anchor.state(receipt) == "anchored"


def test_a_receipt_without_an_ots_proof_is_refused_not_guessed(monkeypatch):
    _patch(monkeypatch, _Calendar)
    anchor = OpenTimestampsAnchor()
    receipt = anchor.publish(DIGEST)
    receipt.proof["format"] = "something-else"
    with pytest.raises(ValueError, match="nothing to upgrade"):
        anchor.upgrade(receipt)


@pytest.mark.parametrize("supplied", [
    DIGEST,
    bytes.fromhex(DIGEST.split(":")[1]),
])
def test_the_same_checkpoint_reaches_the_same_commitment(monkeypatch, supplied):
    """A caller holding `Checkpoint.digest` has a string; one following the
    Protocol literally has bytes. A plug that timestamped the ASCII of a digest
    would produce proofs verifying against nothing anybody holds."""
    _patch(monkeypatch, _Calendar)
    receipt = OpenTimestampsAnchor().publish(supplied)
    assert receipt.proof["digest"] == DIGEST.split(":")[1]
    assert receipt.checkpoint == DIGEST


def test_a_longer_payload_is_hashed_rather_than_refused(monkeypatch):
    """The Protocol says `publish(checkpoint: bytes)`, and a caller may hand
    over the canonical bytes of a checkpoint rather than its digest."""
    _patch(monkeypatch, _Calendar)
    body = b'{"index":0,"window_root":"sha256:..."}'
    receipt = OpenTimestampsAnchor().publish(body)
    assert receipt.proof["digest"] == hashlib.sha256(body).hexdigest()


def test_a_proof_of_another_document_cannot_upgrade_this_receipt(monkeypatch):
    """The severe one. `proof` is an ordinary mutable field on a receipt the
    holder keeps, so without this check they can drop in an ALREADY-ANCHORED
    `.ots` for an unrelated digest and receive a receipt saying their
    checkpoint reached a Bitcoin block.

    The attestation in that proof is genuine. What it attests is somebody
    else's document, and the receipt would carry a real block height under a
    checkpoint that never went anywhere."""
    from opentimestamps.core.notary import BitcoinBlockHeaderAttestation

    _patch(monkeypatch, _Calendar)
    anchor = OpenTimestampsAnchor()

    mine = anchor.publish(DIGEST)
    theirs = anchor.publish("sha256:" + hashlib.sha256(b"somebody else").hexdigest())

    # Their proof really does reach a block.
    stamp = anchor._deserialize(bytes.fromhex(theirs.proof["serialized"]))
    for sub in stamp.ops.values():
        sub.attestations.add(BitcoinBlockHeaderAttestation(812345))
    anchored_elsewhere = anchor._serialize(
        bytes.fromhex(theirs.proof["digest"]), stamp).hex()

    mine.proof["serialized"] = anchored_elsewhere
    with pytest.raises(ValueError, match="timestamps"):
        anchor.upgrade(mine)

    # And the pair-matching version of the same attack: move the recorded
    # digest too, so the proof and `proof["digest"]` agree with each other and
    # only the checkpoint is left behind.
    mine.proof["digest"] = theirs.proof["digest"]
    with pytest.raises(ValueError, match="somebody else's attestation"):
        anchor.upgrade(mine)
