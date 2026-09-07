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
# The nonce every synthetic TimeStampResp below echoes by default (item 13:
# RFC 3161 2.4.2 -- a response must carry back the nonce THIS request sent).
NONCE = bytes.fromhex("11223344aabbccdd")
# The nonce actually baked into the recorded fixture -- read back with the
# walker's own `_tst_nonce` from `response.tsr` once, not guessed at.
FIXTURE_NONCE = bytes.fromhex("340f72e9c6d78280")

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
             nonce: bytes | None = NONCE,
             trailing: tuple[bytes, ...] = ()) -> bytes:
    """One TSTInfo: version, policy, MessageImprint, serial, genTime, nonce.

    `nonce=None` omits the field, for the tests that want a response
    carrying none (item 13: that is refused, not treated as "no nonce sent").
    """
    fields = [
        integer(1),
        oid(bytes.fromhex("2b06010401ce0f01")),
        message_imprint(imprint, imprint_oid),
        integer(7),
        gen_time,
    ]
    if nonce is not None:
        fields.append(tlv(0x02, nonce))
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
                    include_token: bool = True,
                    nonce: bytes | None = NONCE) -> bytes:
    """A whole TimeStampResp, laid out the way openssl lays one out."""
    status_info = seq(integer(status))
    if not include_token:
        return seq(status_info)
    tst = tst_info(gen_time, tsa=tsa, imprint=imprint, imprint_oid=imprint_oid,
                   nonce=nonce)
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
    gen, name = m._parse_timestamp_resp(FIXTURE.read_bytes(), IMPRINT, FIXTURE_NONCE)
    assert gen == FIXTURE_GEN_TIME
    assert name == FIXTURE_TSA_NAME   # the TSA's GeneralName (directoryName)


def test_attest_builds_the_attestation_from_a_recorded_reply(monkeypatch):
    """attest() turns a real reply into a complete, contract-shaped Attestation."""
    # The recorded fixture echoes FIXTURE_NONCE, baked in when it was signed;
    # attest() sends a FRESH random nonce each call (item 13), so replaying a
    # canned reply needs the fixed nonce it actually answers, not a new one.
    monkeypatch.setattr(m, "_fresh_nonce", lambda: FIXTURE_NONCE)
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
    monkeypatch.setattr(m, "_fresh_nonce", lambda: FIXTURE_NONCE)
    monkeypatch.setattr(
        Rfc3161Attester, "_urlopen",
        staticmethod(lambda request, timeout: _FakeResponse(FIXTURE.read_bytes())))
    attester = Rfc3161Attester("https://tsa.example/timestamp")
    out = attester.attest(SUBJECT).to_dict()
    assert out["proof"]["token_der"] == base64.b64encode(FIXTURE.read_bytes()).decode("ascii")
    assert out["proof"]["tsa_url"] == "https://tsa.example/timestamp"
    assert out["algorithm"] == "sha256"
    assert out["subject"] == "sha256:" + STAMPED


def test_the_attestation_validates_against_the_contract_schema(monkeypatch):
    """The Attestation this plug builds is not just shaped right in Python; the contract agrees."""
    monkeypatch.setattr(m, "_fresh_nonce", lambda: FIXTURE_NONCE)
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
    """A nonce must be 1..8 bytes with a non-zero first byte whose high bit is
    clear, or the request is refused before it is sent. The high-bit case
    (item 13) is the one `_fresh_nonce` avoids by construction; a caller
    building the bytes by hand must be refused the same way."""
    for bad_nonce in (b"", b"\x00", b"\x00" * 8, bytes(range(9)),
                      b"\x80" + b"\x00" * 7, b"\xff" * 8):
        with pytest.raises(ValueError, match="nonce"):
            build_timestamp_request(SUBJECT, bad_nonce)


def test_attest_normalizes_subject_to_the_digest_value(monkeypatch):
    """attest() accepts a checkpoint digest as raw bytes or its sha256: string, and nothing else."""
    monkeypatch.setattr(m, "_fresh_nonce", lambda: FIXTURE_NONCE)
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
            m._parse_timestamp_resp(hostile, IMPRINT, NONCE)


def test_an_oversized_length_is_refused():
    """A length claiming far more content than the input holds is refused, not read past the end."""
    fixture = bytearray(FIXTURE.read_bytes())
    fixture[1:4] = b"\x82\x7f\xff"          # top-level SEQ demands 32767+ bytes
    with pytest.raises(TokenError, match="length"):
        m._parse_timestamp_resp(bytes(fixture), IMPRINT, NONCE)


def test_an_indefinite_length_is_refused():
    """Indefinite-length encoding is BER, not DER, and this walker only reads DER."""
    fixture = bytearray(FIXTURE.read_bytes())
    fixture[1] = 0x80                       # indefinite length is not DER
    with pytest.raises(TokenError, match="ndefinite"):
        m._parse_timestamp_resp(bytes(fixture), IMPRINT, NONCE)


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
        m._parse_timestamp_resp(nested, IMPRINT, NONCE)


def test_a_wrong_content_type_oid_is_refused():
    """A ContentInfo whose contentType is not id-signedData is refused by the OID bytes, not our guess."""
    bad = timestamp_resp(_GENTIME_Z, content_type=bytes.fromhex("2a864886f70d010708"))
    with pytest.raises(TokenError, match="signedData"):
        m._parse_timestamp_resp(bad, IMPRINT, NONCE)


def test_a_wrong_econtent_type_oid_is_refused():
    """Encapsulated content that is not id-ct-TSTInfo is refused the same way."""
    bad = timestamp_resp(_GENTIME_Z, econtent_type=bytes.fromhex("2a864886f70d0109100105"))
    with pytest.raises(TokenError, match="TSTInfo"):
        m._parse_timestamp_resp(bad, IMPRINT, NONCE)


def test_an_imprint_under_an_unadmitted_algorithm_is_refused():
    """A MessageImprint signed under SHA-1 (or anything but SHA-256) is refused by OID, never accepted as a weaker match."""
    bad = timestamp_resp(_GENTIME_Z, imprint=hashlib.sha1(SUBJECT).digest(),
                          imprint_oid=_SHA1_OID)
    with pytest.raises(TokenError, match="SHA-256"):
        m._parse_timestamp_resp(bad, IMPRINT, NONCE)


def test_a_tsa_refusal_is_refused():
    """PKIStatus outside {granted, grantedWithMods} is refused, not returned as a token-shaped answer."""
    refused = timestamp_resp(_GENTIME_Z, status=2, include_token=False)
    with pytest.raises(TokenError, match="refused"):
        m._parse_timestamp_resp(refused, IMPRINT, NONCE)


def test_a_granted_response_without_a_token_is_refused():
    """A status of `granted` with no token attached is refused: granted implies a token, not just says so."""
    bad = timestamp_resp(_GENTIME_Z, include_token=False)
    with pytest.raises(TokenError, match="no token"):
        m._parse_timestamp_resp(bad, IMPRINT, NONCE)


def test_a_token_over_another_digest_is_refused():
    """A token whose imprint is not the digest of what we sent is refused — the binding check."""
    wrong = timestamp_resp(_GENTIME_Z, imprint=b"\x00" * 32)
    with pytest.raises(TokenError, match="imprint"):
        m._parse_timestamp_resp(wrong, IMPRINT, NONCE)


def test_a_token_without_a_gen_time_is_refused():
    """TSTInfo with fewer than five fields (no genTime) is refused, never read past what is there."""
    tst = seq(integer(1), oid(bytes.fromhex("2b06010401ce0f01")),
              message_imprint(IMPRINT), integer(7))      # no genTime
    encap = seq(oid(_TSTINFO_OID), tlv(0xA0, octets(tst)))
    signed = seq(integer(3), b"\x31\x00", encap)
    bad = seq(seq(integer(0)), seq(oid(_SIGNED_DATA_OID), tlv(0xA0, signed)))
    with pytest.raises(TokenError, match="genTime|short"):
        m._parse_timestamp_resp(bad, IMPRINT, NONCE)


# ---------------------------------------------------------------------------
# the nonce — RFC 3161 2.4.2, item 13                                        #
# ---------------------------------------------------------------------------


def test_a_response_with_no_nonce_is_refused():
    """A response that omits the nonce cannot be told apart from a replay of
    a token issued before this request ever sent one, so it is refused --
    not read as if silence meant agreement."""
    body = timestamp_resp(_GENTIME_Z, nonce=None)
    with pytest.raises(TokenError, match="nonce"):
        m._parse_timestamp_resp(body, IMPRINT, NONCE)


def test_a_response_echoing_a_different_nonce_is_refused():
    """A token real in every other respect, but for a DIFFERENT query's
    nonce, is exactly a replayed token: refused rather than accepted because
    its other fields happen to check out."""
    body = timestamp_resp(_GENTIME_Z, nonce=b"\x01\x02\x03\x04")
    with pytest.raises(TokenError, match="nonce"):
        m._parse_timestamp_resp(body, IMPRINT, NONCE)


def test_a_response_echoing_the_sent_nonce_is_accepted():
    """The positive case: this is what a real TSA's echo looks like, and it
    must not be refused now that item 13 checks it."""
    body = timestamp_resp(_GENTIME_Z, nonce=NONCE)
    got, _ = m._parse_timestamp_resp(body, IMPRINT, NONCE)
    assert got == "2026-09-06T22:15:31Z"


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
    got, _ = m._parse_timestamp_resp(timestamp_resp(time_tlv), IMPRINT, NONCE)
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
        m._parse_timestamp_resp(timestamp_resp(time_tlv), IMPRINT, NONCE)


@pytest.mark.parametrize("tag,raw", [
    # item 9: the TAG selects the grammar, not the digit count found by
    # trying either -- a 14-digit value under a UTCTime tag, or a 12-digit
    # value under a GeneralizedTime tag, is refused rather than read as if
    # the other tag had been sent (the cross-product the lens built).
    (0x17, b"20260906221531Z"),        # UTCTime tag + 14 digits (GenTime shape)
    (0x18, b"260906221531Z"),          # GeneralizedTime tag + 12 digits (UTCTime shape)
    (0x18, b"500906221531Z"),          # ditto, the YY>=50 pivot case
    (0x17, b"9909062215310Z"),         # 13 digits under a UTCTime tag
    (0x18, b"20260906221531.0Z"),      # fraction of exactly zero, one digit
    (0x18, b"20260906221531.00Z"),     # fraction of exactly zero, two digits
    (0x18, b"20260906221531.000000Z"), # fraction of exactly zero, six digits
    (0x18, b"20260906221531.500Z"),    # non-zero fraction, trailing zero
    (0x18, b"20260906221531.50Z"),     # ditto, shorter
    (0x18, b"20260906221531\xc3\xa9Z"),  # non-ASCII byte in the value
])
def test_the_tag_selects_the_grammar_and_der_fractions_only(tag, raw):
    """A digit count or a fraction that does not belong to the SENT tag is
    refused, never guessed at under the other tag's rule (item 9)."""
    with pytest.raises(TokenError):
        m._asn1_time(tag, raw)


@pytest.mark.parametrize("tag,raw,expected", [
    (0x18, b"20260906221531.5Z", "2026-09-06T22:15:31.5Z"),   # no trailing 0
    (0x18, b"20260906221531.05Z", "2026-09-06T22:15:31.05Z"), # trailing digit is 5, not 0
    (0x17, b"260906221531Z", "2026-09-06T22:15:31Z"),         # UTCTime has no fraction at all
])
def test_a_der_conformant_fraction_is_accepted(tag, raw, expected):
    """The DER rule refuses trailing/all zeros, not every fraction — the
    positive case must keep working."""
    assert m._asn1_time(tag, raw) == expected


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
    gen, name = m._parse_timestamp_resp(body, IMPRINT, NONCE)
    assert name == expected


def test_a_token_without_tsa_name_falls_back_to_the_url(monkeypatch):
    """A token that names no TSA leaves the receipt's issuer as the URL it was fetched from."""
    monkeypatch.setattr(m, "_fresh_nonce", lambda: NONCE)
    attest = make_attester(timestamp_resp(_GENTIME_Z)).attest(SUBJECT)
    assert attest.issuer == "https://tsa.example/timestamp"
    assert attest.attestation_id.startswith("rfc3161:")


def test_the_default_attestation_id_includes_the_subject_so_two_tokens_from_one_tsa_differ(monkeypatch):
    """Item 6: the plug's earlier default (`rfc3161:{issuer}`) collided by
    construction whenever one TSA stamped two different subjects for the
    same receipt -- a P10 violation the plug itself made trivially
    reachable. The id must include enough of `subject` to tell them apart."""
    monkeypatch.setattr(m, "_fresh_nonce", lambda: FIXTURE_NONCE)
    attest = make_attester(FIXTURE.read_bytes()).attest(SUBJECT)
    assert attest.attestation_id == f"rfc3161:{FIXTURE_TSA_NAME}:{STAMPED[:16]}"

    other_subject = hashlib.sha256(b"a different checkpoint").digest()
    monkeypatch.setattr(m, "_fresh_nonce", lambda: FIXTURE_NONCE)
    other_body = timestamp_resp(
        _GENTIME_Z, tsa=directory_name_cn(FIXTURE_TSA_NAME),
        imprint=hashlib.sha256(other_subject).digest(), nonce=FIXTURE_NONCE)
    other = make_attester(other_body).attest(other_subject)
    assert other.attestation_id != attest.attestation_id


@pytest.mark.parametrize("name_tlv", [
    tlv(0x82, b"good.tsa\nEXISTENCE: VERIFIED"),   # dNSName with a newline
    tlv(0x82, b"good.tsa\x00evil"),                # dNSName with a NUL
    directory_name_cn("good.tsa\nEXISTENCE: VERIFIED"),
])
def test_a_non_printable_tsa_name_falls_back_to_the_url(monkeypatch, name_tlv):
    """Item 14: a GeneralName with a control character never becomes `issuer`
    -- a TSA is not required to send a sane name, and the fallback exists so
    a hostile one cannot ride into the verdict as if it were a name at all."""
    monkeypatch.setattr(m, "_fresh_nonce", lambda: NONCE)
    body = timestamp_resp(_GENTIME_Z, tsa=tlv(0xA0, name_tlv))
    attest = make_attester(body).attest(SUBJECT)
    assert attest.issuer == "https://tsa.example/timestamp"


# ---------------------------------------------------------------------------
# the network — real HTTP, item 12                                          #
# ---------------------------------------------------------------------------


@pytest.fixture()
def loopback_pair():
    """Two real local HTTP servers: `victim` (the configured TSA) and `other`
    (where a redirect would try to send the query). Real sockets, not a
    monkeypatched urllib, because the no-redirect opener is exactly the part
    that only a real handler chain exercises."""
    import http.server
    import socketserver
    import threading

    class _Handler(http.server.BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, *a):
            pass

        def do_POST(self):
            n = int(self.headers.get("Content-Length") or 0)
            self.rfile.read(n)
            self.server.hits.append(self.server.server_address[1])
            mode = self.server.mode
            if mode == "redirect":
                self.send_response(302)
                self.send_header("Location", self.server.redirect_to)
                self.send_header("Content-Length", "0")
                self.end_headers()
            elif mode == "500":
                self.send_response(500)
                self.send_header("Content-Length", "3")
                self.end_headers()
                self.wfile.write(b"no!")
            elif mode == "hang":
                import time
                time.sleep(5)

    def serve(mode):
        srv = socketserver.TCPServer(("127.0.0.1", 0), _Handler)
        srv.mode = mode
        srv.hits = []
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        return srv

    other = serve("ok")
    victim = serve("redirect")
    victim.redirect_to = f"http://127.0.0.1:{other.server_address[1]}/elsewhere"
    try:
        yield victim, other
    finally:
        victim.shutdown()
        other.shutdown()


def test_a_redirect_is_refused_and_never_followed(loopback_pair):
    """`_fetch` must not follow a redirect: `proof.tsa_url` names who
    answered, and a redirected query would let a DIFFERENT host answer in
    the configured TSA's name (item 12)."""
    victim, other = loopback_pair
    url = f"http://127.0.0.1:{victim.server_address[1]}/tsr"
    with pytest.raises(TokenError, match="redirect"):
        Rfc3161Attester(url, timeout=5.0).attest(SUBJECT)
    assert victim.hits == [victim.server_address[1]]
    assert other.hits == []            # the redirect was never followed


def test_an_http_error_status_is_a_token_error():
    """A non-2xx response is refused with a named reason, never a bare
    urllib.error.HTTPError escaping this module's promise."""
    import http.server
    import socketserver
    import threading

    class _Handler(http.server.BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, *a):
            pass

        def do_POST(self):
            n = int(self.headers.get("Content-Length") or 0)
            self.rfile.read(n)
            self.send_response(500)
            self.send_header("Content-Length", "3")
            self.end_headers()
            self.wfile.write(b"no!")

    srv = socketserver.TCPServer(("127.0.0.1", 0), _Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        url = f"http://127.0.0.1:{srv.server_address[1]}/tsr"
        with pytest.raises(TokenError, match="500"):
            Rfc3161Attester(url, timeout=5.0).attest(SUBJECT)
    finally:
        srv.shutdown()


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
