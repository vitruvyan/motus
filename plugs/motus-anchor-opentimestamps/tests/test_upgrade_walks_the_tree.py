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
