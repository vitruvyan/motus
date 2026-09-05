"""The upgrade has to find the promise, and the promise is not at the root.

`upgrade()` reported `pending` for 67 hours on evidence three independent
calendars had already anchored in Bitcoin blocks 962787, 962789 and 962798. It
walked one level of the timestamp and asked about a commitment nobody had
promised anything about; the calendars said "not found", and a broad `except`
turned that into silence.

The fixture below is a REAL pending proof produced by this anchor, and the
calendar double answers for exactly one commitment — the one the pending
attestation actually names, four sha256 operations down. Ask about anything
else and it raises, which is what the live calendars did.

So the test fails without the fix in the way the defect failed: not with an
error, but with `pending` and nobody the wiser.
"""

from __future__ import annotations

import hashlib
from pathlib import Path
from types import SimpleNamespace

import pytest

from motus_anchor_opentimestamps import OpenTimestampsAnchor, _directly_verified

FIXTURE = Path(__file__).parent / "fixtures" / "pending.ots"
#: The bytes the fixture is a proof OF — the root string, utf-8, no newline.
COMMITTED = (Path(__file__).parent / "fixtures" / "pending.root.txt").read_bytes()

CALENDARS = (
    "https://alice.btc.calendar.opentimestamps.org",
    "https://bob.btc.calendar.opentimestamps.org",
    "https://finney.calendar.eternitywall.com",
)


def _receipt():
    digest = hashlib.sha256(COMMITTED).hexdigest()
    return SimpleNamespace(
        checkpoint=f"sha256:{digest}",
        state="pending",
        proof={
            "format": "ots",
            "digest": digest,
            "serialized": FIXTURE.read_bytes().hex(),
        },
    )


def test_the_promise_is_not_at_the_root_of_the_proof():
    """The load-bearing observation, stated as a test rather than a comment."""
    anchor = OpenTimestampsAnchor(calendars=CALENDARS)
    timestamp = anchor._deserialize(FIXTURE.read_bytes())

    # One level down is where the broken walk looked, and there is no
    # attestation there.
    shallow = [sub for sub in timestamp.ops.values() if sub.attestations]
    assert shallow == [], (
        "if an attestation were reachable at depth one, this whole defect "
        "could not have happened and this fixture is the wrong one")

    verified = list(_directly_verified(timestamp))
    assert len(verified) == 3, "three calendars accepted this commitment"
    for sub in verified:
        assert sub.attestations, "the walk must stop where attestations are"
        assert sub.msg and sub.msg != timestamp.msg, (
            "the attestation's commitment is the one four operations down, "
            "not the document's own digest")


def test_upgrade_descends_past_an_attested_branch(monkeypatch):
    from opentimestamps.core.notary import (
        BitcoinBlockHeaderAttestation, PendingAttestation,
    )
    from opentimestamps.core.op import OpAppend, OpSHA256
    from opentimestamps.core.timestamp import Timestamp

    digest = bytes.fromhex(hashlib.sha256(COMMITTED).hexdigest())
    timestamp = Timestamp(digest)
    branch = timestamp.ops.add(OpAppend(b"branch"))
    branch.attestations.add(PendingAttestation(CALENDARS[0]))
    leaf = branch.ops.add(OpSHA256())
    leaf.attestations.add(BitcoinBlockHeaderAttestation(812346))

    anchor = OpenTimestampsAnchor(
        calendars=CALENDARS, block_time=lambda h: "2023-08-11T12:00:00Z")
    receipt = _receipt()
    receipt.proof["serialized"] = anchor._serialize(digest, timestamp).hex()
    verified = list(_directly_verified(timestamp))
    assert branch in verified and leaf in verified

    class Calendar:
        def __init__(self, url): self.url = url
        def get_timestamp(self, commitment, timeout=None):
            return branch

    import opentimestamps.calendar as cal
    old = cal.RemoteCalendar
    cal.RemoteCalendar = Calendar
    try:
        upgraded = anchor.upgrade(receipt)
    finally:
        cal.RemoteCalendar = old
    assert upgraded.state == "anchored"
    assert upgraded.reference == "bitcoin-block:812346"


def test_upgrade_reveals_arbitrarily_deep_pending_chain(monkeypatch):
    from opentimestamps.core.notary import BitcoinBlockHeaderAttestation, PendingAttestation
    from opentimestamps.core.op import OpAppend
    from opentimestamps.core.timestamp import Timestamp

    calendars = tuple(f"https://deep-{i}.example" for i in range(7))
    digest = bytes.fromhex(hashlib.sha256(COMMITTED).hexdigest())
    root = Timestamp(digest)
    first = root.ops.add(OpAppend(b"hop-0"))
    first.attestations.add(PendingAttestation(calendars[0]))
    anchor = OpenTimestampsAnchor(
        calendars=calendars, block_time=lambda h: "2023-08-11T12:00:00Z")
    receipt = _receipt()
    receipt.proof["serialized"] = anchor._serialize(digest, root).hex()

    class Calendar:
        def __init__(self, url): self.url = url
        def get_timestamp(self, commitment, timeout=None):
            index = calendars.index(self.url)
            fresh = Timestamp(commitment)
            if index == len(calendars) - 1:
                fresh.attestations.add(BitcoinBlockHeaderAttestation(999999))
            else:
                child = fresh.ops.add(OpAppend(f"hop-{index + 1}".encode()))
                child.attestations.add(PendingAttestation(calendars[index + 1]))
            return fresh

    import opentimestamps.calendar as cal
    old = cal.RemoteCalendar
    cal.RemoteCalendar = Calendar
    try:
        upgraded = anchor.upgrade(receipt)
    finally:
        cal.RemoteCalendar = old
    assert upgraded.reference == "bitcoin-block:999999"


def test_upgrade_stops_a_fast_growing_calendar_at_the_hard_ceiling(monkeypatch):
    from opentimestamps.core.notary import PendingAttestation
    from opentimestamps.core.op import OpAppend
    from opentimestamps.core.timestamp import Timestamp

    digest = bytes.fromhex(hashlib.sha256(COMMITTED).hexdigest())
    root = Timestamp(digest)
    root.attestations.add(PendingAttestation(CALENDARS[0]))
    anchor = OpenTimestampsAnchor(calendars=(CALENDARS[0],))
    receipt = _receipt()
    receipt.proof["serialized"] = anchor._serialize(digest, root).hex()
    calls = {"n": 0, "commitments": []}

    class GrowingCalendar:
        def __init__(self, url): self.url = url
        def get_timestamp(self, commitment, timeout=None):
            calls["n"] += 1
            calls["commitments"].append(commitment.hex())
            fresh = Timestamp(commitment)
            child = fresh.ops.add(OpAppend(f"growth-{calls['n']}".encode()))
            child.attestations.add(PendingAttestation(CALENDARS[0]))
            return fresh

    import opentimestamps.calendar as cal
    old_calendar = cal.RemoteCalendar
    cal.RemoteCalendar = GrowingCalendar
    try:
        upgraded = anchor.upgrade(receipt)
    finally:
        cal.RemoteCalendar = old_calendar
    assert upgraded.state == "pending"
    assert calls["n"] == 32
    ceiling = upgraded.proof["calendars_unreachable"]
    assert len(ceiling) == 1
    assert ceiling[0]["calendar"] == CALENDARS[0]
    assert ceiling[0]["commitment"] not in calls["commitments"]
    assert ceiling[0]["reason"] == (
        "upgrade stopped after 32 passes: the proof kept growing")


def test_upgrade_removes_outage_after_same_request_recovers(monkeypatch):
    from opentimestamps.core.notary import BitcoinBlockHeaderAttestation
    from opentimestamps.core.timestamp import Timestamp

    anchor = OpenTimestampsAnchor(
        calendars=CALENDARS,
        block_time=lambda h: "2023-08-11T12:00:00Z")
    receipt = _receipt()
    recovering = {"value": True}

    class RecoveringCalendar:
        def __init__(self, url): self.url = url
        def get_timestamp(self, commitment, timeout=None):
            if recovering["value"]:
                raise ConnectionError("calendar temporarily down")
            fresh = Timestamp(commitment)
            fresh.attestations.add(BitcoinBlockHeaderAttestation(812347))
            return fresh

    import opentimestamps.calendar as cal
    old = cal.RemoteCalendar
    cal.RemoteCalendar = RecoveringCalendar
    try:
        first = anchor.upgrade(receipt)
        outages = first.proof["calendars_unreachable"]
        assert {item["calendar"] for item in outages} == set(CALENDARS)
        assert all(item["commitment"] for item in outages)
        assert all(item["reason"] == "ConnectionError: calendar temporarily down"
                   for item in outages)
        recovering["value"] = False
        second = anchor.upgrade(first)
    finally:
        cal.RemoteCalendar = old

    assert second.proof["calendars_unreachable"] == []
    assert second.proof["bitcoin_block_heights"] == [812347] * len(CALENDARS)
    upgraded_timestamp = anchor._deserialize(
        bytes.fromhex(second.proof["serialized"]))
    assert any(
        getattr(attestation, "height", None) == 812347
        for attestation in anchor._attestations(upgraded_timestamp))


def test_pending_upgrade_drops_preloaded_publication_source(monkeypatch):
    anchor = OpenTimestampsAnchor(calendars=CALENDARS)
    receipt = _receipt()
    receipt.proof["published_at_source"] = "https://stale.example/"

    from opentimestamps.core.timestamp import Timestamp
    import opentimestamps.calendar as cal
    old = cal.RemoteCalendar
    cal.RemoteCalendar = lambda url: type(
        "Calendar", (), {"get_timestamp": lambda self, commitment, timeout=None:
                          Timestamp(commitment)})()
    try:
        upgraded = anchor.upgrade(receipt)
    finally:
        cal.RemoteCalendar = old
    assert upgraded.state == "pending"
    assert upgraded.published_at is None
    assert "published_at_source" not in upgraded.proof


def test_upgrade_replaces_changed_failure_reason_for_same_request(monkeypatch):
    from opentimestamps.core.notary import PendingAttestation
    from opentimestamps.core.op import OpAppend
    from opentimestamps.core.timestamp import Timestamp

    digest = bytes.fromhex(hashlib.sha256(COMMITTED).hexdigest())
    root = Timestamp(digest)
    node = root.ops.add(OpAppend(b"single-request"))
    node.attestations.add(PendingAttestation(CALENDARS[0]))
    anchor = OpenTimestampsAnchor(
        calendars=(CALENDARS[0],), block_time=lambda h: "2023-08-11T12:00:00Z")
    receipt = _receipt()
    receipt.proof["serialized"] = anchor._serialize(digest, root).hex()
    mode = {"reason": "first outage"}

    class ChangingFailure:
        def __init__(self, url): self.url = url
        def get_timestamp(self, commitment, timeout=None):
            raise ConnectionError(mode["reason"])

    import opentimestamps.calendar as cal
    old = cal.RemoteCalendar
    cal.RemoteCalendar = ChangingFailure
    try:
        first = anchor.upgrade(receipt)
        mode["reason"] = "second outage"
        second = anchor.upgrade(first)
    finally:
        cal.RemoteCalendar = old
    assert first.proof["calendars_unreachable"][0]["reason"] == (
        "ConnectionError: first outage")
    assert second.proof["calendars_unreachable"][0]["reason"] == (
        "ConnectionError: second outage")


def test_upgrade_deduplicates_repeated_unreachable_requests(monkeypatch):
    from opentimestamps.core.notary import PendingAttestation
    from opentimestamps.core.op import OpAppend
    from opentimestamps.core.timestamp import Timestamp

    digest = bytes.fromhex(hashlib.sha256(COMMITTED).hexdigest())
    root = Timestamp(digest)
    good = root.ops.add(OpAppend(b"good"))
    good.attestations.add(PendingAttestation(CALENDARS[0]))
    bad = root.ops.add(OpAppend(b"bad"))
    bad.attestations.add(PendingAttestation(CALENDARS[1]))
    anchor = OpenTimestampsAnchor(calendars=CALENDARS)
    receipt = _receipt()
    receipt.proof["serialized"] = anchor._serialize(digest, root).hex()

    class Calendar:
        def __init__(self, url): self.url = url
        def get_timestamp(self, commitment, timeout=None):
            if self.url == CALENDARS[1]:
                raise ConnectionError("down")
            return Timestamp(commitment)

    import opentimestamps.calendar as cal
    old = cal.RemoteCalendar
    cal.RemoteCalendar = Calendar
    try:
        upgraded = anchor.upgrade(receipt)
    finally:
        cal.RemoteCalendar = old
    failures = upgraded.proof["calendars_unreachable"]
    assert len([item for item in failures if item["calendar"] == CALENDARS[1]]) == 1


def test_upgrade_asks_the_calendar_about_the_commitment_it_promised():
    """Fails without the fix — and fails by reporting `pending`, silently."""
    from opentimestamps.core.notary import PendingAttestation

    anchor = OpenTimestampsAnchor(calendars=CALENDARS)
    timestamp = anchor._deserialize(FIXTURE.read_bytes())

    # Every commitment a calendar actually promised something about, and which
    # calendar owes it. Anything else is a question the live calendars answered
    # with CommitmentNotFoundError.
    promised: dict[bytes, str] = {}
    for sub in _directly_verified(timestamp):
        for attestation in sub.attestations:
            if isinstance(attestation, PendingAttestation):
                uri = attestation.uri
                promised[sub.msg] = uri.decode() if isinstance(uri, bytes) else uri

    assert promised, "the fixture must be pending, or this test proves nothing"
    asked: list[bytes] = []

    class Calendar:
        def __init__(self, url):
            self.url = url

        def get_timestamp(self, commitment, timeout=None):
            asked.append(commitment)
            if promised.get(commitment) != self.url:
                raise KeyError(
                    "commitment not found — this calendar promised nothing "
                    "about these bytes")
            # A calendar that HAS the commitment returns a timestamp carrying
            # the block attestation. Returning the pending sub-timestamp
            # unchanged is enough to prove the right question was asked.
            for sub in _directly_verified(timestamp):
                if sub.msg == commitment:
                    return sub
            raise AssertionError("unreachable")

    import motus_anchor_opentimestamps as module
    real = module.__dict__.get("RemoteCalendar")
    import opentimestamps.calendar as cal
    monkey = cal.RemoteCalendar
    cal.RemoteCalendar = Calendar
    try:
        upgraded = anchor.upgrade(_receipt())
    finally:
        cal.RemoteCalendar = monkey
        if real is not None:                              # pragma: no cover
            module.RemoteCalendar = real

    assert asked, "upgrade() asked no calendar anything at all"
    unknown = [c for c in asked if c not in promised]
    assert not unknown, (
        f"upgrade() asked about {len(unknown)} commitment(s) no calendar "
        f"promised anything about — this is the 67-hour defect: "
        f"{[c.hex() for c in unknown]}")
    assert set(asked) == set(promised), (
        "every promised commitment has to be asked about, or a calendar that "
        "has already anchored is never heard from")
    # The receipt now says whether we could ask, which `pending` alone cannot.
    assert upgraded.proof["calendars_unreachable"] == []


def test_an_unreachable_calendar_is_recorded_rather_than_swallowed():
    """`pending` because there is no block yet, and `pending` because we could
    not ask, are different facts and must not wear the same word."""
    anchor = OpenTimestampsAnchor(calendars=CALENDARS)

    class Dead:
        def __init__(self, url):
            self.url = url

        def get_timestamp(self, commitment, timeout=None):
            raise ConnectionError("calendar is down")

    import opentimestamps.calendar as cal
    monkey = cal.RemoteCalendar
    cal.RemoteCalendar = Dead
    try:
        upgraded = anchor.upgrade(_receipt())
    finally:
        cal.RemoteCalendar = monkey

    assert upgraded.state == "pending"
    assert upgraded.proof["calendars_unreachable"], (
        "an outage that leaves no trace on the receipt is how a bug in our own "
        "request went unnoticed for three days")
    for failure in upgraded.proof["calendars_unreachable"]:
        assert "ConnectionError" in failure["reason"]
        assert failure["commitment"], (
            "which commitment could not be asked about is the part that "
            "distinguishes a partial outage from a total one")
