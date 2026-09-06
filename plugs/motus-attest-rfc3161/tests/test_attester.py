"""The RFC 3161 plug, checked offline against a recorded real response.

Every test here is offline except the one opt-in live check. The recorded
fixture (`tests/fixtures/response.tsr`) is a genuine `TimeStampResp`: a
throwaway CA and TSA certificate were created with `openssl req`/`openssl
x509`, the *query* was built with this plug's own `build_timestamp_request`
(so the bytes the TSA signed are exactly the bytes production code sends),
and `openssl ts -reply -config ts.cnf -queryfile req.tsq -out response.tsr`
signed it — real CMS, a real RSA signature, a real certificate chain, over a
request this code actually produces. `STAMPED` is the 32-byte value handed to
`attest()` as `subject`; the TSA's message imprint is `sha256(STAMPED)`,
because `build_timestamp_request` treats `subject` as the datum being
time-stamped and RFC 3161 requires the imprint to be a hash of that datum,
never the datum itself (a plug that skipped the second hash would be sending
an imprint no RFC 3161 responder would recognise as one).

A second family synthesizes `TimeStampResp` documents with a test-local DER
encoder built on a general-purpose `tlv`/`enc_len` (short- *and* long-form
lengths, so a fixture's length octets are correct by construction and never
hand-typed), so the hostile shapes an adversary will try — truncation,
oversized and indefinite lengths, deep nesting, wrong OIDs, every ASN.1 time
form — are asserted against exactly the bytes that produce them.
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import sys
from pathlib import Path

import pytest

PLUG = Path(__file__).resolve().parents[1]
REPO = PLUG.parents[1]
sys.path.insert(0, str(PLUG / "src"))
sys.path.insert(0, str(REPO / "src"))

import motus_attest_rfc3161 as m  # noqa: E402
from motus_attest_rfc3161 import (  # noqa: E402
    Rfc3161Attester, TokenError, build_timestamp_request,
)

FIXTURE = PLUG / "tests" / "fixtures" / "response.tsr"
# The 32 bytes handed to attest()/build_timestamp_request() as `subject` for
# the recorded fixture. The TSA's message imprint is sha256(SUBJECT), read
# back and checked against IMPRINT below — see the module docstring.
STAMPED = "a948904f2f0f479b8f8197694b30184b0d2ed1c1cd2a1ec0fb85d299a192a447"
SUBJECT = bytes.fromhex(STAMPED)
IMPRINT = hashlib.sha256(SUBJECT).digest()
# The instant openssl's throwaway TSA actually stamped the recorded fixture.
FIXTURE_GEN_TIME = "2026-09-06T22:45:58Z"
FIXTURE_TSA_NAME = "fake-tsa.example"

_SIGNED_DATA_OID = bytes.fromhex("2a864886f70d010702")
_TSTINFO_OID = bytes.fromhex("2a864886f70d0109100104")
_SHA256_OID = bytes.fromhex("608648016503040201")
_SHA1_OID = bytes.fromhex("2b0e03021a")
_CN_OID = bytes.fromhex("550403")


# ---------------------------------------------------------------------------
# test-local DER encoder — general-purpose so lengths are never hand-typed
# ---------------------------------------------------------------------------


def enc_len(count: int) -> bytes:
    """Minimal DER length octets, short- or long-form, for any ``count``.

    A fixture built with hand-picked length bytes is exactly the failure mode
    this plug's own review flagged: get one byte wrong and the test asserts
    on a document nobody could actually receive. Deriving the length from the
    real content length, in both encodings, makes that class of mistake
    impossible rather than merely rare.
    """
    if count < 0x80:
        return bytes([count])
    body = count.to_bytes((count.bit_length() + 7) // 8, "big")
    return bytes([0x80 | len(body)]) + body


def tlv(tag: int, content: bytes) -> bytes:
    return bytes([tag]) + enc_len(len(content)) + content


def seq(*members: bytes) -> bytes:
    return tlv(0x30, b"".join(members))


def oid(value: bytes) -> bytes:
    return tlv(0x06, value)


def integer(value: int) -> bytes:
    return tlv(0x02, value.to_bytes(max(1, (value.bit_length() + 7) // 8), "big"))


def octets(content: bytes) -> bytes:
    return tlv(0x04, content)


def message_imprint(digest: bytes, algorithm_oid: bytes = _SHA256_OID) -> bytes:
    return seq(seq(oid(algorithm_oid), b"\x05\x00"), octets(digest))


def tst_info(gen_time: bytes, *, tsa: bytes | None = None,
             imprint: bytes = IMPRINT, imprint_oid: bytes = _SHA256_OID,
             trailing: tuple[bytes, ...] = ()) -> bytes:
    """One TSTInfo: version, policy, MessageImprint, serial, genTime."""
    fields = [
        integer(1),
        oid(bytes.fromhex("2b06010401ce0f01")),
        message_imprint(imprint, imprint_oid),
        integer(7),
        gen_time,
    ]
    if tsa is not None:
        fields.append(tsa)
    fields.extend(trailing)
    return seq(*fields)


def directory_name_cn(name: str) -> bytes:
    attribute = seq(oid(_CN_OID), tlv(0x0C, name.encode("utf-8")))
    return tlv(0xA4, seq(tlv(0x31, attribute)))   # [4] Name -> RDN SET


def timestamp_resp(gen_time: bytes, *, tsa: bytes | None = None,
                    imprint: bytes = IMPRINT, imprint_oid: bytes = _SHA256_OID,
                    status: int = 0, econtent_type: bytes = _TSTINFO_OID,
                    content_type: bytes = _SIGNED_DATA_OID,
                    include_token: bool = True) -> bytes:
    """A whole TimeStampResp, laid out the way openssl lays one out."""
    status_info = seq(integer(status))
    if not include_token:
        return seq(status_info)
    tst = tst_info(gen_time, tsa=tsa, imprint=imprint, imprint_oid=imprint_oid)
    encap = seq(oid(econtent_type), tlv(0xA0, octets(tst)))
    signed = seq(integer(3), b"\x31\x00", encap)         # empty algs, no certs
    content_info = seq(oid(content_type), tlv(0xA0, signed))
    return seq(status_info, content_info)


class _FakeResponse:
    def __init__(self, body: bytes):
        self._body = body

    def read(self, n: int = -1) -> bytes:
        return self._body if n < 0 else self._body[:n]

    def __enter__(self) -> "_FakeResponse":
        return self

    def __exit__(self, *exc: object) -> bool:
        return False


def make_attester(body: bytes) -> Rfc3161Attester:
    return Rfc3161Attester("https://tsa.example/timestamp",
                            opener=lambda req, timeout=None: _FakeResponse(body))


_GENTIME_Z = tlv(0x18, b"20260906221531Z")


# ---------------------------------------------------------------------------
# the recorded real response
# ---------------------------------------------------------------------------


def test_gen_time_and_tsa_name_are_read_from_the_recorded_response():
    """A granted response yields the TSA's genTime and its GeneralName."""
    gen, name = m._parse_timestamp_resp(FIXTURE.read_bytes(), IMPRINT)
    assert gen == FIXTURE_GEN_TIME
    assert name == FIXTURE_TSA_NAME   # the TSA's GeneralName (directoryName)


def test_attest_builds_the_attestation_from_a_recorded_reply():
    """attest() turns a real reply into a complete, contract-shaped Attestation."""
    attest = make_attester(FIXTURE.read_bytes()).attest(SUBJECT)
    out = attest.to_dict()
    assert out["type"] == "rfc3161_timestamp"
    assert out["issuer"] == FIXTURE_TSA_NAME
    assert out["subject"] == "sha256:" + STAMPED
    assert out["issued_at"] == FIXTURE_GEN_TIME
    assert out["algorithm"] == "sha256"
    assert out["proof"]["tsa_url"] == "https://tsa.example/timestamp"
    assert base64.b64decode(out["proof"]["token_der"]) == FIXTURE.read_bytes()


def test_attest_via_a_monkeypatched_urlopen(monkeypatch):
    """The default HTTP path (no injected opener) is exercised too, not just the test seam."""
    monkeypatch.setattr(
        Rfc3161Attester, "_urlopen",
        staticmethod(lambda request, timeout: _FakeResponse(FIXTURE.read_bytes())))
    attester = Rfc3161Attester("https://tsa.example/timestamp")
    out = attester.attest(SUBJECT).to_dict()
    assert out["proof"]["token_der"] == base64.b64encode(FIXTURE.read_bytes()).decode("ascii")
    assert out["proof"]["tsa_url"] == "https://tsa.example/timestamp"
    assert out["algorithm"] == "sha256"
    assert out["subject"] == "sha256:" + STAMPED


def test_the_attestation_validates_against_the_contract_schema():
    """The Attestation this plug builds is not just shaped right in Python; the contract agrees."""
    from jsonschema import Draft202012Validator, FormatChecker
    from referencing import Registry, Resource
    schema = json.loads((REPO / "contract/receipt.v1.schema.json").read_text())
    commitment = json.loads(
        (REPO / "contract/commitment.v1.schema.json").read_text())
    registry = Registry().with_resources([
        (schema["$id"], Resource.from_contents(schema)),
        (commitment["$id"], Resource.from_contents(commitment)),
    ])
    validator = Draft202012Validator(
        {"$ref": schema["$id"] + "#/$defs/Attestation"},
        format_checker=FormatChecker(), registry=registry)
    out = make_attester(FIXTURE.read_bytes()).attest(SUBJECT).to_dict()
    assert list(validator.iter_errors(out)) == []


def test_the_request_der_is_the_rfc_3161_shape():
    """build_timestamp_request emits SEQUENCE(version 1, imprint, nonce, certReq TRUE)."""
    nonce = bytes.fromhex("340f72e9c6d78280")
    request = build_timestamp_request(SUBJECT, nonce)
    outer_tag, content_start, content_end = m._tlv(request, 0, len(request), 0)
    assert outer_tag == 0x30                                  # the whole request is one SEQUENCE
    elements = m._children(request, content_start, content_end, 0)
    assert [e[1] for e in elements] == [0x02, 0x30, 0x02, 0x01]
    version_el = elements[0]
    assert request[version_el[3]:version_el[4]] == b"\x01"   # version 1
    imprint_el = m._children(request, elements[1][3], elements[1][4], 1)[1]
    assert request[imprint_el[3]:imprint_el[4]] == hashlib.sha256(SUBJECT).digest()
    expected_end = b"\x02\x08" + nonce + b"\x01\x01\xff"     # nonce, certReq TRUE
    assert request[-len(expected_end):] == expected_end
    with pytest.raises(ValueError, match="32-byte"):
        build_timestamp_request(b"short", nonce)


def test_the_request_rejects_a_nonce_outside_its_bound():
    """A nonce must be 1..8 bytes with a non-zero first byte, or the request is refused before it is sent."""
    for bad_nonce in (b"", b"\x00", b"\x00" * 8, bytes(range(9))):
        with pytest.raises(ValueError, match="nonce"):
            build_timestamp_request(SUBJECT, bad_nonce)


def test_attest_normalizes_subject_to_the_digest_value():
    """attest() accepts a checkpoint digest as raw bytes or its sha256: string, and nothing else."""
    fake = make_attester(FIXTURE.read_bytes())
    assert fake.attest("sha256:" + STAMPED).to_dict()["subject"] \
        == "sha256:" + STAMPED
    with pytest.raises(ValueError, match="attest"):
        fake.attest(b"some arbitrary content")


# ---------------------------------------------------------------------------
# hostile bytes — the DER lens
# ---------------------------------------------------------------------------


def test_truncated_and_garbage_bytes_are_refused():
    """Bytes cut short at any point are refused, never guessed at."""
    fixture = FIXTURE.read_bytes()
    for hostile in (b"", b"hello", fixture[:-1], fixture[:-400],
                    fixture[:5], fixture[:40]):
        with pytest.raises(TokenError):
            m._parse_timestamp_resp(hostile, IMPRINT)


def test_an_oversized_length_is_refused():
    """A length claiming far more content than the input holds is refused, not read past the end."""
    fixture = bytearray(FIXTURE.read_bytes())
    fixture[1:4] = b"\x82\x7f\xff"          # top-level SEQ demands 32767+ bytes
    with pytest.raises(TokenError, match="length"):
        m._parse_timestamp_resp(bytes(fixture), IMPRINT)


def test_an_indefinite_length_is_refused():
    """Indefinite-length encoding is BER, not DER, and this walker only reads DER."""
    fixture = bytearray(FIXTURE.read_bytes())
    fixture[1] = 0x80                       # indefinite length is not DER
    with pytest.raises(TokenError, match="ndefinite"):
        m._parse_timestamp_resp(bytes(fixture), IMPRINT)


def test_the_tlv_walker_refuses_nesting_beyond_its_bound():
    """_tlv's own depth guard fires past _MAX_DEPTH.

    Exercised directly at `_tlv`, the function that holds the check: every
    call site in `_parse_timestamp_resp` passes a depth literal matching the
    TimeStampResp schema's own fixed shape (never past ~11), and none of the
    walker's helpers recurse on the ATTACKER's nesting rather than the
    schema's -- so a document wrapped in far more SEQUENCEs than any real
    response is refused by an ordinary shape mismatch first, not by this
    guard (see the next test). The guard is real; it is just not the reason
    that particular input fails today.
    """
    with pytest.raises(TokenError, match="nesting"):
        m._tlv(b"\x05\x00", 0, 2, m._MAX_DEPTH + 1)


def test_deeply_nested_garbage_is_refused_though_not_by_the_depth_bound():
    """A document with far more nesting than a TimeStampResp has is still refused.

    Not because the depth guard fires (it structurally cannot, given the
    walker's fixed-depth call sites -- see the test above) but because the
    schema-fixed walk hits an ordinary shape mismatch long before depth 32.
    Either way, arbitrarily deep input never reaches unbounded recursion or
    an unhandled exception.
    """
    nested = b"\x05\x00"
    for _ in range(2 * m._MAX_DEPTH + 2):
        nested = tlv(0x30, nested)          # general encoder: correct past 127 bytes too
    with pytest.raises(TokenError):
        m._parse_timestamp_resp(nested, IMPRINT)


def test_a_wrong_content_type_oid_is_refused():
    """A ContentInfo whose contentType is not id-signedData is refused by the OID bytes, not our guess."""
    bad = timestamp_resp(_GENTIME_Z, content_type=bytes.fromhex("2a864886f70d010708"))
    with pytest.raises(TokenError, match="signedData"):
        m._parse_timestamp_resp(bad, IMPRINT)


def test_a_wrong_econtent_type_oid_is_refused():
    """Encapsulated content that is not id-ct-TSTInfo is refused the same way."""
    bad = timestamp_resp(_GENTIME_Z, econtent_type=bytes.fromhex("2a864886f70d0109100105"))
    with pytest.raises(TokenError, match="TSTInfo"):
        m._parse_timestamp_resp(bad, IMPRINT)


def test_an_imprint_under_an_unadmitted_algorithm_is_refused():
    """A MessageImprint signed under SHA-1 (or anything but SHA-256) is refused by OID, never accepted as a weaker match."""
    bad = timestamp_resp(_GENTIME_Z, imprint=hashlib.sha1(SUBJECT).digest(),
                          imprint_oid=_SHA1_OID)
    with pytest.raises(TokenError, match="SHA-256"):
        m._parse_timestamp_resp(bad, IMPRINT)


def test_a_tsa_refusal_is_refused():
    """PKIStatus outside {granted, grantedWithMods} is refused, not returned as a token-shaped answer."""
    refused = timestamp_resp(_GENTIME_Z, status=2, include_token=False)
    with pytest.raises(TokenError, match="refused"):
        m._parse_timestamp_resp(refused, IMPRINT)


def test_a_granted_response_without_a_token_is_refused():
    """A status of `granted` with no token attached is refused: granted implies a token, not just says so."""
    bad = timestamp_resp(_GENTIME_Z, include_token=False)
    with pytest.raises(TokenError, match="no token"):
        m._parse_timestamp_resp(bad, IMPRINT)


def test_a_token_over_another_digest_is_refused():
    """A token whose imprint is not the digest of what we sent is refused — the binding check."""
    wrong = timestamp_resp(_GENTIME_Z, imprint=b"\x00" * 32)
    with pytest.raises(TokenError, match="imprint"):
        m._parse_timestamp_resp(wrong, IMPRINT)


def test_a_token_without_a_gen_time_is_refused():
    """TSTInfo with fewer than five fields (no genTime) is refused, never read past what is there."""
    tst = seq(integer(1), oid(bytes.fromhex("2b06010401ce0f01")),
              message_imprint(IMPRINT), integer(7))      # no genTime
    encap = seq(oid(_TSTINFO_OID), tlv(0xA0, octets(tst)))
    signed = seq(integer(3), b"\x31\x00", encap)
    bad = seq(seq(integer(0)), seq(oid(_SIGNED_DATA_OID), tlv(0xA0, signed)))
    with pytest.raises(TokenError, match="genTime|short"):
        m._parse_timestamp_resp(bad, IMPRINT)


# ---------------------------------------------------------------------------
# genTime in every ASN.1 time form
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("time_tlv,expected", [
    (tlv(0x18, b"20260906221531Z"), "2026-09-06T22:15:31Z"),          # GeneralizedTime
    (tlv(0x17, b"260906221531Z"), "2026-09-06T22:15:31Z"),            # UTCTime -> 20xx
    (tlv(0x17, b"990906221531Z"), "1999-09-06T22:15:31Z"),            # UTCTime YY>=50 -> 19xx
    (tlv(0x18, b"20260906221531.5Z"), "2026-09-06T22:15:31.5Z"),
    (tlv(0x18, b"20260906221531+0100"), "2026-09-06T21:15:31Z"),
    (tlv(0x18, b"20260906221531-0500"), "2026-09-07T03:15:31Z"),
])
def test_time_forms_parse_to_utc(time_tlv, expected):
    """Every ASN.1 time form RFC 3161 allows normalizes to the same UTC instant."""
    got, _ = m._parse_timestamp_resp(timestamp_resp(time_tlv), IMPRINT)
    assert got == expected


@pytest.mark.parametrize("time_tlv", [
    tlv(0x18, b"20260906221531"),          # no zone
    tlv(0x18, b"2026090622 00"),           # space in the value
    tlv(0x18, b"20261306221531Z"),         # month 13
    tlv(0x18, b"2026130621 331Z"),
])
def test_malformed_time_forms_are_refused(time_tlv):
    """A zoneless, malformed, or impossible time is refused rather than located by guesswork."""
    with pytest.raises(TokenError):
        m._parse_timestamp_resp(timestamp_resp(time_tlv), IMPRINT)


def test_the_response_size_bound_is_enforced():
    """A response over _MAX_RESPONSE_BYTES is refused at the HTTP read, before any DER walk."""
    oversized = b"\x00" * (m._MAX_RESPONSE_BYTES + 1)
    with pytest.raises(TokenError, match="size"):
        make_attester(oversized).attest(SUBJECT)


# ---------------------------------------------------------------------------
# the TSA GeneralName forms
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("name_tlv,expected", [
    (tlv(0x81, b"tsa@example.com"), "tsa@example.com"),   # rfc822Name
    (tlv(0x82, b"tsa.example.com"), "tsa.example.com"),   # dNSName
    (tlv(0x86, b"https://tsa.example"), "https://tsa.example"),
    (directory_name_cn("DigiCert TSA"), "DigiCert TSA"),
])
def test_the_general_name_forms_become_the_issuer(name_tlv, expected):
    """Every GeneralName CHOICE this walker recognises becomes the issuer verbatim."""
    body = timestamp_resp(_GENTIME_Z, tsa=tlv(0xA0, name_tlv))
    gen, name = m._parse_timestamp_resp(body, IMPRINT)
    assert name == expected


def test_a_token_without_tsa_name_falls_back_to_the_url():
    """A token that names no TSA leaves the receipt's issuer as the URL it was fetched from."""
    attest = make_attester(timestamp_resp(_GENTIME_Z)).attest(SUBJECT)
    assert attest.issuer == "https://tsa.example/timestamp"
    assert attest.attestation_id.startswith("rfc3161:")


# ---------------------------------------------------------------------------
# the live opt-in — ADR-031 hypothesis H1's real check
# ---------------------------------------------------------------------------

_LIVE_TSA_URL = "https://freetsa.org/tsr"


@pytest.mark.skipif(not os.environ.get("MOTUS_LIVE_TSA"),
                     reason="set MOTUS_LIVE_TSA=1 to hit a live public TSA "
                            f"({_LIVE_TSA_URL})")
def test_live_public_tsa():
    """H1's real check: a genuine TSA's TimeStampResp parses with no dependency."""
    live = Rfc3161Attester(_LIVE_TSA_URL)
    subject = hashlib.sha256(b"motus-attest-rfc3161 live check").digest()
    out = live.attest(subject).to_dict()
    assert out["type"] == "rfc3161_timestamp"
    assert out["algorithm"] == "sha256"
    assert out["proof"]["tsa_url"] == _LIVE_TSA_URL
    assert out["issued_at"].endswith("Z")
    assert base64.b64decode(out["proof"]["token_der"])   # decodes; non-empty
