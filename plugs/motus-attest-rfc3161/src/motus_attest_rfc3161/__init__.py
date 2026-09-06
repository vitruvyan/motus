"""An `Attester` for Motus that obtains RFC 3161 timestamps over HTTP.

ADR-031 decision 5: `rfc3161_timestamp` is the one attestation type this
distribution adds, chosen because it is the weakest level-2 claim and because
a public TSA issues it free of charge over HTTP against a SHA-256 imprint.

What this plug does is build the TimeStampReq, hand it to the TSA, and read
two facts back out of the TimeStampResp with a bounded DER walker that needs
no dependency (ADR-031 hypothesis H1): `genTime` — the moment the TSA says it
issued — and the TSA's GeneralName, which becomes the receipt's `issuer`. It
never verifies the CMS signature (it holds no TSA key); `proof.token_der`
carries the COMPLETE TimeStampResp so a reader can, with `openssl ts -verify
-in response.tsr`.

The walker is the part an adversarial round will attack, so every refusal is
a named `TokenError` and every bound — nesting depth, length arithmetic,
total response size — is stated once below. No pyasn1, no cryptography: if
this file ever needs either, hypothesis H1 is falsified and the fallback of
ADR-031 decision H1 applies (token opaque, `issued_at` from the HTTP
exchange).
"""

from __future__ import annotations

import base64
import hashlib
import os
import re
import urllib.request
from datetime import datetime, timedelta, timezone
from typing import Any

__all__ = ["Rfc3161Attester", "ATTESTATION_TYPE", "build_timestamp_request"]

#: The value the contract's KNOWN_ATTESTATION_TYPES admits (ADR-031 decision
#: 4). Not "rfc3161" — that string is not a member of the closed set, and an
#: Attestation carrying it would be refused by validate.py's P10 as an
#: unknown type, silently, the first time anyone ran it through the contract.
ATTESTATION_TYPE = "rfc3161_timestamp"

# Bounds that make the walker and the HTTP read total, whatever a responder
# sends. A genuine TimeStampResp with its certificate chain is a few tens of
# KiB; the bounds sit orders of magnitude above that and refuse anything that
# would take unbounded memory or time to walk.
_MAX_DEPTH = 32
_MAX_RESPONSE_BYTES = 2 * 1024 * 1024

# The OCTETS, so "wrong OID" is a byte comparison and never our guess.
_ID_SIGNED_DATA = bytes.fromhex("2a864886f70d010702")     # 1.2.840.113549.1.7.2
_ID_CT_TSTINFO = bytes.fromhex("2a864886f70d0109100104")  # 1.2.840.113549.1.9.16.1.4
_OID_SHA256 = bytes.fromhex("608648016503040201")         # 2.16.840.1.101.3.4.2.1
_OID_CN = bytes.fromhex("550403")                          # 2.5.4.3

# GeneralName CHOICE tags whose VALUE is an IA5String name (RFC 5280 4.2.1.6).
_IA5_NAME_TAGS = frozenset({0x81, 0x82, 0x86})   # rfc822Name, dNSName, URI

# ASN.1 TIME forms the walker turns into an instant: UTCTime (YYMMDDHHMMSS)
# or GeneralizedTime (YYYYMMDDHHMMSS), optional fraction, then Z, ±HHMM, or
# nothing. RFC 3161 2.4.4 requires UTC, so the no-zone form is refused rather
# than located by guesswork.
_TIME_RE = re.compile(
    rb"^(?P<year>\d{2}|\d{4})(?P<mon>\d{2})(?P<day>\d{2})"
    rb"(?P<hour>\d{2})(?P<min>\d{2})(?P<sec>\d{2})"
    rb"(?:\.(?P<frac>\d{1,6}))? *"
    rb"(?P<zone>Z|[+-]\d{4})?$"
)

_GRANTED_STATUSES = (0, 1)  # PKIStatus: granted, grantedWithMods


class TokenError(ValueError):
    """The TSA answered something this walker refuses to name."""


# ---------------------------------------------------------------------------
# a bounded DER walker (hypothesis H1: no pyasn1, no cryptography)
# ---------------------------------------------------------------------------


def _tlv(data: bytes, pos: int, limit: int, depth: int) -> tuple[int, int, int]:
    """``(tag, content_start, content_end)`` of the TLV at ``pos``.

    Refuses every shape a DER walker must not guess at: truncation, an
    indefinite length (not DER), a length that overruns the input, leading
    zeroes in a long-form length, and nesting beyond ``_MAX_DEPTH``.
    """
    if pos + 2 > limit:
        raise TokenError("truncated tag or length")
    tag = data[pos]
    pos += 1
    length = data[pos]
    pos += 1
    if length & 0x80:
        count = length & 0x7F
        if count == 0:
            raise TokenError("indefinite length is not DER")
        if count > 8 or pos + count > limit:
            raise TokenError("length overflows the input")
        if data[pos] == 0:
            raise TokenError("long-form length with a leading zero")
        length = int.from_bytes(data[pos:pos + count], "big")
        pos += count
    end = pos + length
    if length > limit - pos:
        raise TokenError("length exceeds the input")
    if depth > _MAX_DEPTH:
        raise TokenError("nesting beyond the walker's bound")
    return tag, pos, end


def _children(data: bytes, start: int, end: int, depth: int):
    """Elements of a constructed value: ``(tag, full_tag, primitive, s, e)``.

    Trailing bytes inside a constructed value are refused — a decoder that
    ignores them reads a different document from the bytes in front of it.
    """
    out: list[tuple[int, int, bool, int, int]] = []
    pos = start
    while pos < end:
        tag, s, e = _tlv(data, pos, end, depth + 1)
        out.append((tag & 0x1F, tag, not bool(tag & 0x20), s, e))
        pos = e
    if pos != end:
        raise TokenError("trailing bytes inside a constructed value")
    return out


def _integer(data: bytes, element: tuple, what: str) -> int:
    """The VALUE of an INTEGER element, demanding minimal DER encoding."""
    _, tag, primitive, s, e = element
    if tag != 0x02 or not primitive:
        raise TokenError(f"{what} is not an INTEGER")
    raw = data[s:e]
    if not raw or raw[0] & 0x80 or (len(raw) > 1 and raw[0] == 0):
        raise TokenError(f"{what} is not minimal DER")
    return int.from_bytes(raw, "big")


def _asn1_time(tag: int, raw: bytes) -> str:
    """A contract Timestamp string from a UTCTime/GeneralizedTime value.

    Z and ±HHMM zones are accepted (offsets converted to UTC), a missing
    zone is refused, and fractional seconds survive trimmed to microseconds.
    """
    if tag not in (0x17, 0x18):
        raise TokenError(f"tag {tag:#x} is not a time")
    match = _TIME_RE.fullmatch(raw)
    if match is None:
        raise TokenError(f"malformed ASN.1 time {raw!r}")
    year = match.group("year")
    if len(year) == 2:  # UTCTime: 50..99 -> 19xx, 00..49 -> 20xx (RFC 5280)
        year = ("19" if int(year) >= 50 else "20") + year.decode("ascii")
    parts = (int(match.group(g)) for g in
             ("mon", "day", "hour", "min", "sec"))
    try:
        moment = datetime(int(year), *parts, tzinfo=timezone.utc)
    except ValueError as exc:
        raise TokenError(f"impossible ASN.1 time {raw!r}: {exc}") from exc
    zone = match.group("zone") or b""
    if zone == b"Z":
        pass
    elif zone:
        hours, minutes = int(zone[1:3]), int(zone[3:5])
        if hours > 23 or minutes > 59:
            raise TokenError(f"impossible zone {zone!r}")
        offset = timedelta(hours=hours, minutes=minutes)
        if zone[:1] == b"-":
            offset = -offset
        moment = (moment - offset).replace(tzinfo=timezone.utc)
    else:
        raise TokenError(f"ASN.1 time {raw!r} carries no zone; RFC 3161 "
                         "requires UTC")
    frac = match.group("frac")
    if frac is None:
        return moment.strftime("%Y-%m-%dT%H:%M:%SZ")
    micros = int(frac.decode("ascii")) * 10 ** (6 - len(frac))
    return moment.strftime("%Y-%m-%dT%H:%M:%S") + "." + \
        f"{micros:06d}".rstrip("0") + "Z"


def _directory_cn(data: bytes, start: int, end: int, depth: int) -> str | None:
    """The value of the commonName attribute in an X.501 Name, or None.

    Walked generically: each RDN is a SET OF AttributeTypeAndValue; the one
    whose type OID is 2.5.4.3 and whose value is a primitive string names the
    holder. Anything else yields None and the caller falls back to the URL.
    """
    names = _children(data, start, end, depth + 1)
    if len(names) != 1 or names[0][1] != 0x30:
        return None
    for rdn in _children(data, names[0][3], names[0][4], depth + 2):
        if rdn[1] != 0x31:                      # SET OF AttributeTypeAndValue
            continue
        for attribute in _children(data, rdn[3], rdn[4], depth + 3):
            if attribute[1] != 0x30:
                continue
            parts = _children(data, attribute[3], attribute[4], depth + 4)
            if len(parts) != 2 or parts[0][1] != 0x06:
                continue
            if data[parts[0][3]:parts[0][4]] != _OID_CN:
                continue
            value = parts[1]
            if not value[2] or value[1] not in (0x0C, 0x13, 0x16, 0x1A):
                continue                     # UTF8, Printable, IA5, Visible
            try:
                return data[value[3]:value[4]].decode("utf-8")
            except UnicodeDecodeError:
                return None
    return None


def _tsa_name(data: bytes, tst: list, depth: int) -> str | None:
    """The TSA GeneralName from TSTInfo's optional `tsa [0]`, or None.

    UniformResourceIdentifier / dNSName / rfc822Name are taken verbatim;
    directoryName yields its commonName. A name nobody can look up is worth
    nothing, so any other CHOICE form returns None and the Attester falls
    back to the URL it talked to.
    """
    for element in tst[5:]:
        if element[1] != 0xA0:                  # tsa [0] EXPLICIT
            continue
        named = _children(data, element[3], element[4], depth + 1)
        if len(named) != 1:
            raise TokenError("the tsa field is one GeneralName")
        _, raw, primitive, s, e = named[0]
        if raw in _IA5_NAME_TAGS and primitive:
            return data[s:e].decode("ascii", "replace")
        if raw == 0xA4:                         # directoryName
            return _directory_cn(data, s, e, depth + 1)
        return None
    return None


def _parse_timestamp_resp(data: bytes, imprint: bytes) -> tuple[str, str | None]:
    """``(genTime, TSA name)`` from a TimeStampResp, walking it bounded.

    The walk peels the explicit wrappers a real TSA emits — TimeStampResp,
    ContentInfo, SignedData, encapContentInfo, TSTInfo — checking each OID
    and the status, and verifies the BINDING: the token's MessageImprint must
    be the SHA-256 of precisely the bytes we asked to have stamped.
    """
    top = _children(data, 0, len(data), 0)
    if len(top) != 1 or top[0][1] != 0x30:
        raise TokenError("a TimeStampResp is one SEQUENCE")
    root = _children(data, top[0][3], top[0][4], 1)
    if not root:
        raise TokenError("the response has no status")
    status_seq = _children(data, root[0][3], root[0][4], 2)
    if not status_seq:
        raise TokenError("the status is empty")
    status = _integer(data, status_seq[0], "the PKIStatus")
    if status not in _GRANTED_STATUSES:
        raise TokenError(f"the TSA refused this query (PKIStatus {status})")
    if len(root) < 2:
        raise TokenError("a granted response carries no token")

    # ContentInfo: { contentType OID, [0] EXPLICIT SignedData }.
    content_info = _children(data, root[1][3], root[1][4], 2)
    if not content_info or content_info[0][1] != 0x06:
        raise TokenError("ContentInfo has no contentType")
    if data[content_info[0][3]:content_info[0][4]] != _ID_SIGNED_DATA:
        raise TokenError("the token is not an id-signedData ContentInfo")
    if len(content_info) < 2 or content_info[1][1] != 0xA0:
        raise TokenError("ContentInfo has no [0] content")
    explicit = _children(data, content_info[1][3], content_info[1][4], 3)
    if len(explicit) != 1 or explicit[0][1] != 0x30:
        raise TokenError("the [0] content is not SignedData")
    signed_data = _children(data, explicit[0][3], explicit[0][4], 4)
    # version, digestAlgorithms, encapContentInfo, certificates, signerInfos.
    if len(signed_data) < 3:
        raise TokenError("SignedData is too short")
    eci = _children(data, signed_data[2][3], signed_data[2][4], 5)
    if not eci or eci[0][1] != 0x06:
        raise TokenError("encapContentInfo has no eContentType")
    if data[eci[0][3]:eci[0][4]] != _ID_CT_TSTINFO:
        raise TokenError("the encapsulated content is not TSTInfo")
    if len(eci) < 2 or eci[1][1] != 0xA0:
        raise TokenError("encapContentInfo has no [0] eContent")
    econtent = _children(data, eci[1][3], eci[1][4], 6)
    if len(econtent) != 1 or econtent[0][1] != 0x04:
        raise TokenError("eContent is not an OCTET STRING")
    tst_wrap = _children(data, econtent[0][3], econtent[0][4], 6)
    if len(tst_wrap) != 1 or tst_wrap[0][1] != 0x30:
        raise TokenError("the eContent value is not one TSTInfo")
    tst = _children(data, tst_wrap[0][3], tst_wrap[0][4], 7)

    # TSTInfo: version, policy, messageImprint, serial, genTime, ...
    if len(tst) < 5:
        raise TokenError("TSTInfo is too short")
    if tst[4][1] not in (0x17, 0x18):
        raise TokenError("the fifth TSTInfo field is not the genTime")
    gen_time = _asn1_time(tst[4][1], data[tst[4][3]:tst[4][4]])

    imprint_seq = _children(data, tst[2][3], tst[2][4], 6)
    if len(imprint_seq) != 2:
        raise TokenError("MessageImprint is not AlgorithmIdentifier + OCTET")
    algorithm = _children(data, imprint_seq[0][3], imprint_seq[0][4], 7)
    if not algorithm or data[algorithm[0][3]:algorithm[0][4]] != _OID_SHA256:
        raise TokenError("the token's imprint algorithm is not SHA-256")
    if imprint_seq[1][1] != 0x04 or data[imprint_seq[1][3]:imprint_seq[1][4]] != imprint:
        raise TokenError("the token's imprint is not the digest we sent")

    return gen_time, _tsa_name(data, tst, 5)


# ---------------------------------------------------------------------------
# TimeStampReq — built, not parsed, so it is DER by construction
# ---------------------------------------------------------------------------


def _der(tag: int, content: bytes) -> bytes:
    """One minimal TLV for the request side (short-form length only)."""
    if len(content) >= 0x80:
        raise TokenError("the request outgrew the short-form length")
    return bytes((tag, len(content))) + content


def _sequence(*parts: bytes) -> bytes:
    return _der(0x30, b"".join(parts))


def build_timestamp_request(subject: bytes, nonce: bytes) -> bytes:
    """The DER TimeStampReq for a SHA-256 imprint: version 1, nonce, certReq.

    ``subject`` is the 32 bytes a checkpoint digest (or run root) consists of;
    the imprint a TSA stamps is the SHA-256 of those bytes.
    """
    if len(subject) != 32:
        raise ValueError("the imprinted subject must be a 32-byte digest value")
    if not 0 < len(nonce) <= 8 or nonce[0] == 0:
        raise ValueError("a nonce is 1..8 bytes with a non-zero first byte")
    imprint = hashlib.sha256(subject).digest()
    message_imprint = _sequence(
        _sequence(_der(0x06, _OID_SHA256), _der(0x05, b"")),
        _der(0x04, imprint))
    return _sequence(
        _der(0x02, b"\x01"),        # version 1
        message_imprint,            # MessageImprint over the subject
        _der(0x02, nonce),          # our nonce, echoed by the TSA
        _der(0x01, b"\xff"),        # certReq TRUE: carry the TSA certs
    )


# ---------------------------------------------------------------------------
# the Attester
# ---------------------------------------------------------------------------


def _digest_bytes(subject: bytes | str) -> bytes:
    """The 32 digest-value bytes of whatever the caller wants asserted.

    The contract's subject is a Digest (sha256:<64 hex>) or its 32 bytes —
    those are the same value in two encodings, and a checkpoint digest's VALUE
    is the only thing a receipt's P9 will recognise. Anything else is refused:
    silently hashing arbitrary bytes would attest to a digest that matches
    nothing in any receipt.
    """
    if isinstance(subject, bytes) and len(subject) == 32:
        return subject
    if isinstance(subject, bytes) and re.fullmatch(rb"sha256:[0-9a-f]{64}", subject):
        return bytes.fromhex(subject[7:].decode("ascii"))
    if isinstance(subject, str) and re.fullmatch(r"sha256:[0-9a-f]{64}", subject):
        return bytes.fromhex(subject[7:])
    raise ValueError(
        "attest() takes a checkpoint digest's 32 bytes or its sha256: string")


def _fresh_nonce() -> bytes:
    nonce = os.urandom(8)
    while nonce[0] & 0x80 or nonce[0] == 0:
        nonce = os.urandom(8)
    return nonce


class Rfc3161Attester:
    """An `Attester` that asks ONE TSA to stamp a digest.

    The object is stateless between calls: everything a reader needs lives in
    the Attestation it returns, which is the artefact that gets stored and
    handed on. Independence is a reader's judgement from ``issuer``; a URL is
    recorded so the reader knows exactly whose word the claim rests on.
    """

    def __init__(self, tsa_url: str, *, timeout: float = 15.0,
                 attestation_id: str | None = None,
                 opener: Any = None) -> None:
        if not isinstance(tsa_url, str) or not tsa_url.startswith(("http://", "https://")):
            raise ValueError("a TSA URL is an http(s) endpoint")
        if not isinstance(timeout, (int, float)) or timeout <= 0:
            raise ValueError("timeout must be a positive number of seconds")
        self.tsa_url = tsa_url
        self._timeout = float(timeout)
        self._attestation_id = attestation_id
        self._opener = opener if opener is not None else self._urlopen

    @staticmethod
    def _urlopen(request: Any, timeout: float) -> Any:
        return urllib.request.urlopen(request, timeout=timeout)

    def _fetch(self, request: bytes) -> bytes:
        query = urllib.request.Request(
            self.tsa_url, data=request,
            headers={"Content-Type": "application/timestamp-query",
                     "Accept": "application/timestamp-reply"},
            method="POST")
        with self._opener(query, timeout=self._timeout) as response:
            reply = response.read(_MAX_RESPONSE_BYTES + 1)
        if len(reply) > _MAX_RESPONSE_BYTES:
            raise TokenError("the TSA response exceeds the size bound")
        return bytes(reply)

    def attest(self, subject: bytes) -> Any:
        """Ask the TSA to stamp a digest; return the ready Attestation.

        ``subject`` is the digest value as bytes (or its sha256: string);
        ``proof.token_der`` carries the full TimeStampResp so verification is
        possible without this plug, and ``issued_at`` is the token's own
        genTime — the TSA's word about when, never this machine's clock.
        """
        raw = _digest_bytes(subject)
        request = build_timestamp_request(raw, _fresh_nonce())
        reply = self._fetch(request)
        issued_at, tsa_name = _parse_timestamp_resp(reply, hashlib.sha256(raw).digest())

        from vitruvyan_motus.commitments import Attestation
        issuer = tsa_name or self.tsa_url
        return Attestation(
            attestation_id=self._attestation_id or f"rfc3161:{issuer}",
            type=ATTESTATION_TYPE,
            issuer=issuer,
            subject="sha256:" + raw.hex(),
            issued_at=issued_at,
            algorithm="sha256",
            proof={
                "token_der": base64.b64encode(reply).decode("ascii"),
                "tsa_url": self.tsa_url,
            },
        )