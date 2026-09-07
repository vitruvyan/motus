# motus-attest-rfc3161

An `Attester` for Motus receipts that obtains an [RFC 3161](https://datatracker.ietf.org/doc/html/rfc3161)
timestamp from a TSA over HTTP — the one attestation type ADR-031 adds at
1.0.0, chosen because it is the weakest: an EXISTENCE claim (level 2), the
same level an external anchor already supports, issued free of charge by
public TSAs against a SHA-256 imprint.

```python
from motus_attest_rfc3161 import Rfc3161Attester

attester = Rfc3161Attester("https://freetsa.org/tsr")      # a public TSA
attestation = attester.attest(checkpoint_digest_bytes)     # the digest VALUE bytes
receipt = log.receipt_for("acme/w1/0", attestations=(attestation,))
```

`attest(subject)` builds the DER TimeStampReq (SHA-256 imprint, nonce,
`certReq TRUE`), POSTs it, and reads two facts back out of the TimeStampResp
with a **bounded DER walker that has no dependency** (ADR-031 hypothesis H1):
`genTime` — the moment the TSA says it issued — and the TSA's GeneralName,
which becomes the receipt's `issuer`. The walker refuses hostile paths with a
named `TokenError`: truncation, indefinite or oversized lengths, a wrong OID,
a token whose imprint is not the digest we sent, and a response that does not
echo the nonce this request sent (RFC 3161 §2.4.2 — otherwise a replayed
token would answer a query it was never issued for). `_fetch` follows no
redirect (a TSA is not who a redirect sends you to) and turns an HTTP error
or a timeout into the same named `TokenError`, never a bare exception from
`urllib`. No `pyasn1`, no `cryptography`.

## It is a claim, and the receipt says so

The contract validator reports an attestation as **CLAIMED**, with the issuer
named, and never verifies the CMS signature — it holds no TSA key, same
posture as an anchor, same reason. It DOES check the structural binding:
`proof.message_imprint` must equal `sha256(subject's digest bytes)` — the
double hash RFC 3161 itself requires, because `subject` is already a digest
and the TSA's imprint is a hash of the thing being stamped. What makes the
claim independently settleable is that `proof.token_der` carries the
COMPLETE TimeStampResp: base64-decode it to `response.tsr` and run

    openssl ts -reply -in response.tsr -token_out -out token.p7
    openssl pkcs7 -inform DER -in token.p7 -print_certs -out certs.pem
    openssl ts -verify -in response.tsr -digest <message_imprint> -CAfile certs.pem

(`-CAfile` and `-digest` are both required: `openssl ts -verify` needs the
certificate chain to check the signature against and, since nobody here
still holds the original data, the digest it was computed over — the
certificates travel inside `response.tsr` itself, extracted by the first two
commands, and `<message_imprint>` is `proof.message_imprint` from the
receipt.)

## Install

Not on PyPI, and neither is Motus.

    pip install ./plugs/motus-attest-rfc3161

`vitruvyan-motus` declares **zero** runtime dependencies and this package must
not be able to change that, which is why it is a separate distribution.

The suite is offline: it replays a recorded TimeStampResp (generated with a
throwaway CA and `openssl ts -reply` locally, signed the normal way, and still
verifying against its own CA) and one test hits a live public TSA only when
`MOTUS_LIVE_TSA=1` is set.