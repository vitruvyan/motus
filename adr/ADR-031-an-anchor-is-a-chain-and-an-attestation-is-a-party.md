# ADR-031 — an anchor is a chain, an attestation is a party

- **Status:** ACCEPTED
- **Date:** 2026-09-06
- **Accepted:** 2026-09-06 by the founder
- **Authority:** CTO proposes; the founder accepts. Closes the decision #123 asks for (M1 freeze requirement, blocks 1.0.0). Depends on #117, closed by ADR-027.
- **Depends on:** ADR-020 (the seven levels; decision 8 reserves `attestations`), ADR-021 (decision 7: what a receipt carries; decision 8: unknown attestation type → refuse), ADR-027 (`execution`, the last block added to `receipt.v1`).
- **Amends:** `contract/receipt.v1.schema.json` — adds one optional top-level array, `attestations`, and one `$defs` object, `Attestation` (decisions 2–4). `contract/README.md` — two rules, `P9` and `P10`, and one refusal. **ADR-020 decision 8** — its field list only (decision 3 says what changed and why); its two statements, *reserved from version 1* and *adding one must not be a format break*, are kept and this ADR is how they are honoured. ADR-021 decision 7 — one word: *"anchor attestations"* becomes *"anchors"* (decision 1 says why the word matters). Nothing about `anchors` changes.

## Context

**The contract does not carry what ADR-020 reserved.** ADR-020 decision 8, accepted 2026-08-12, says the receipt format reserves *from version 1* an `attestations` block, *empty and optional today*, so that a signature, an identity or a qualified timestamp can attach later without a format break. `receipt.v1.schema.json` as of `ac2ccaf` has five top-level properties — `schema_version`, `mode`, `segments`, `anchors`, `execution` — and `additionalProperties: false`. There is no `attestations`. HANDOFF §4 and ROADMAP (1.0.0) both say the freeze must *"prove the `attestations` block extensible by actually adding one"*, and the block they name does not exist to be extended. #123 noticed the drift and asked the question underneath it: is `anchors` that block under another name, or are there two?

**What an anchor is, measured against the code.** `receipt.v1 $defs.Anchor` requires `anchor_id, network, checkpoint, state`, and `state` is `pending | anchored`. The validator (`contract/validate.py`, rules P4 and P5) refuses an anchor whose `checkpoint` is not one of the receipt's own, and refuses to *evaluate* one on a network outside `KNOWN_ANCHOR_NETWORKS = {tron:nile, opentimestamps:bitcoin}`. For an `anchored` entry it reports EXISTENCE and RETENTION as **CLAIMED**, with the explorer address, because it contacts no network. The one plug that exists (`plugs/motus-anchor-opentimestamps`; `tron:nile` is a known network with no plug in this repository) implements the `Anchor` protocol: `publish(checkpoint: bytes) -> AnchorReceipt`, `state(receipt)`. Nobody asserts anything in this path: a chain *records a datum*, and whoever verifies asks the chain. That is the property ADR-021 decision 8 calls *independent verification — it needs a chain, not us*.

**What the three things #123 lists are.** A qualified electronic timestamp (eIDAS art. 42) is a signed token from an accredited party saying *this digest existed at T*; it has no network and no `pending` state — it is issued or it is not. An electronic seal is a signature by a legal person over a datum. An identity attestation is a party saying *key K belongs to Y*. Every one of them is **an issuer asserting something under a key**, and verifying any of them means two steps an anchor never has: check the signature against the issuer's key, then decide whether that issuer is to be believed for that claim — for levels 6 and 7 a legal fact (a trusted list), not a computation. Under `$defs.Anchor` each would have to invent a `network` and a `checkpoint` state to be representable at all, and a verifier reading `network: "eidas"` would look for a chain that does not exist.

**A signature already lives in the receipt, in the right place.** `WitnessAck` (`commitment.v1`) is `witness_id, commitment, position, acknowledged_at, algorithm, signature`; rule C1 refuses one that does not name the leaf it acknowledges; the validator does not verify the signature (it holds no key) and says so. It is an assertion by a party over a digest — the shape of an attestation — but it attaches to a **BEGIN**, because its meaning (*I held this before the run finished*) is about a moment in the chain, and it stays there. It is the precedent for how this validator treats a party's signature: structurally checked, cryptographically **claimed**, never reported as established.

**A measurement about the schema itself.** The validator runs offline and stdlib-only (ADR-001, ADR-021 decision 8). The Python standard library verifies **no** asymmetric signature and parses **no** ASN.1. So whatever attestation type 1.0.0 adds, the contract validator will be able to check its *structure* and its *binding* to the receipt, and will have to report its *cryptography* as claimed — exactly as it does for an anchor. That is not a limitation to apologise for; it is the property that lets the validator be published and trusted without trusting us.

## Decision

1. **Two containers, because two verification procedures.** `anchors[]` keeps its meaning and its schema unchanged: *a checkpoint published to a public chain, with a state that is `pending` until a block carries it*. `attestations[]` is added beside it: *an issuer's signed assertion about a digest in this receipt*. The test that separates them is what a verifier needs to check one: an anchor needs **a chain** and no key; an attestation needs **the issuer's key** and, for levels 6–7, a decision about the issuer. The word *attestation* is reserved for the second and removed from ADR-021 decision 7's description of the first, so that the two words stop meaning one thing in one document and two in another.

2. **`receipt.v1` gains one optional top-level array**, `attestations`, of `$defs.Attestation`. Absent and empty mean the same thing: nobody asserted anything. It is optional at 1.0.0 and stays optional — a receipt at level 1–4 with no party involved is the common case, and a required empty list would make every existing receipt and fixture invalid for nothing.

3. **The shape of an attestation.** ADR-020 decision 8 sketched `{type, issuer, key_id, algorithm, signature, signed_at, evidence_ref}`. Two of those names change and one is added, and each change is a correction, not a preference:
   ```json
   {
     "attestation_id": Identifier,
     "type":           Identifier,        // decision 4: closed set, refused when unknown
     "issuer":         Identifier,        // who asserts; a name a reader can look up, never "us"
     "subject":        Digest,            // WHAT is asserted about: a checkpoint digest or the run root in THIS receipt
     "issued_at":      Timestamp,         // when the issuer says it asserted; the issuer's claim, not ours
     "algorithm":      Identifier,
     "signature":      Identifier,        // opaque token; format decided by `type`
     "proof":          object             // strict plain JSON (J1), bounded; what a reader hands to the issuer's tooling
   }
   ```
   `additionalProperties: false`; all of `attestation_id, type, issuer, subject, issued_at, algorithm, signature` required; `proof` optional and defaulting to `{}`. `evidence_ref` became `subject` and became **required**: an attestation that does not say what it attests is decoration, the C1 argument again, and this time it is written into the schema rather than found by a round. `signed_at` became `issued_at` because a timestamp token is *issued*, not signed by the subject, and the field means the same thing for all types. `key_id` is dropped from the envelope and lives in `proof` for the types that have one: an RFC 3161 token carries its signer's certificate inside the token, and a required `key_id` beside it would be a second copy a forger could make disagree.

4. **`type` is a closed set, and unknown means refuse.** ADR-021 decision 8 already says so; this ADR gives it a rule and a first member:

   | `type` | what the issuer asserts | level it can support | at 1.0.0 |
   |---|---|---|---|
   | `rfc3161_timestamp` | *`subject` existed no later than `issued_at`* — a Time-Stamp Token (RFC 3161) from a public TSA | 2 EXISTENCE | **added** (decision 5) |
   | `qualified_timestamp` | the same token from a TSA on an EU trusted list | 7 LEGAL_TIME (+2) | named, refused |
   | `electronic_seal` | *`subject` was produced by legal person `issuer`* | 5 PROVENANCE, 6 IDENTITY | named, refused |
   | `identity` | *key K belongs to `issuer`'s principal* | 6 IDENTITY | named, refused |

   The three refused rows are in this table so that adding one is a decision recorded against a row rather than a string that appears one day in a receipt. Each needs its own ADR, because each brings a semantics the validator does not have (a trusted list; a certificate chain; revocation, which ADR-020 says is *not never-valid*). The validator's known set is a frozenset beside `KNOWN_ANCHOR_NETWORKS`, and a test enumerates the four rows with their verdicts so a fifth fails the suite until somebody writes its row.

5. **The one added for real is `rfc3161_timestamp`, and it is chosen because it is the weakest.** It supports level 2 only — the level an anchor already supports — so adding it proves the block is extensible **without pretending a level this distribution cannot reach**. A public TSA issues these free of charge, over HTTP, against a SHA-256 imprint; the plug `plugs/motus-attest-rfc3161` implements `Attester.attest(subject: bytes) -> Attestation`, builds the DER request and reads `genTime` and the TSA name out of the response **without a dependency** (hypothesis H1 below). The contract validator checks structure (`P9`, `P10`), reports EXISTENCE as **CLAIMED** naming the issuer and the `openssl ts -verify` incantation in `proof`, and never verifies the CMS signature — same posture as an anchor, same reason.

6. **Two rules and one refusal**, mirroring P4 and P5 so a reader who knows anchors knows attestations:
   - **`P9`** — an attestation whose `subject` is not a checkpoint digest or the run root of this receipt. *An assertion about a digest that is not here says nothing about this run.*
   - **`P10`** — an attestation whose `type` is outside the known set. Reported as a violation **and** as a refusal (non-zero exit), for the reason README gives for P5: *a refusal outranks a violation*, because a document may be correct under a type we cannot read.
   - An attestation never raises a level above what its row allows, and no attestation moves `mode`: `qualified` stays refused by P3 until the `qualified_timestamp` and `identity` rows are opened, and even then P3 will ask for both.

7. **What an attestation is not.** It is not a witness acknowledgement (that stays on the BEGIN, in `commitment.v1`, with C1); it is not an anchor (nothing is *pending*; a token is issued or absent); and it is not verified by this validator (claimed, with the issuer named). A receipt whose only support for EXISTENCE is an attestation is at level 2 *on the issuer's word*; a receipt with an anchor is at level 2 *on a chain's record*. The verdict text says which, because a buyer will ask.

## Consequences

**What this costs.**

- A second container is a second everything: a second binding rule, a second known-set, a second refusal path, a second thing `verify_package()` and `motus-validate package` must walk. Measured against the anchor side that is ~90 lines in `validate.py`, two fixtures, and one row in README. Accepted, because the alternative (decision *Alternatives 1*) costs the meaning of `anchors`.
- `KNOWN_ATTESTATION_TYPES` has one member for a long time. A closed set with one member looks like ceremony. It is the same ceremony as `KNOWN_ANCHOR_NETWORKS` had with one member, and it is what made adding OpenTimestamps a row instead of a rewrite.
- The RFC 3161 plug talks to a TSA we do not run and cannot make independent. The receipt records who it was; independence is a reader's judgement from `issuer`, the ADR-021 decision 3 rule, and this ADR does not pretend otherwise.
- **Nothing in this ADR reaches level 6 or 7.** It makes the format able to carry a level-7 token when a QTSP issues one; ADR-020's constraint that Vitruvyan is not and will not be built as a QTSP is untouched.

**What it buys.** ADR-020 decision 8's promise becomes true in the schema, before the freeze, with a real member; HANDOFF's 1.0.0 requirement is met by a token anyone can obtain and check with tools that are not ours; and `anchors` keeps the one property that makes it worth verifying: no party to trust.

## Hypotheses

- **H1 — the RFC 3161 plug fits without a dependency.** Encoding a `TimeStampReq` (SEQUENCE of a version, a `MessageImprint`, a nonce, `certReq TRUE`) and reading `genTime` and the TSA's `GeneralName` back out of the `TimeStampResp` is a bounded DER walk. Guess: under 200 lines including the walker. **Falsified if** the walker needs `pyasn1`/`cryptography`, or if the three public TSAs tried disagree in a way the walker cannot tell apart from a malformed response. If falsified, the plug ships the token opaque (`proof.token_der`, base64) with `issued_at` taken from the HTTP exchange and the receipt says so — still a real attestation, weaker on `issuer`.
- **H2 — no receipt in the compatibility corpus changes.** The block is optional; every fixture and every Limen receipt anchored so far verifies byte for byte after this change. Checked by the existing corpus in CI the moment the schema lands; falsified by any fixture failing.

## Alternatives rejected

1. **One container, `anchors` widened** (`network` optional, `state` gains `issued`). Rejected: `network` and `checkpoint` would mean something for half the entries, the validator's *unknown network → refuse* rule would have to learn a second meaning of *unknown*, and the sentence *an anchor is verified against a chain, not a party* — the only sentence in the product a buyer cannot get elsewhere — would stop being true of the block it names.
2. **One container, renamed `attestations`, with `anchor` as a `type`.** Rejected: an anchor has `pending`, no issuer and no signature; forcing it into `{issuer, signature}` invents an issuer for Bitcoin. Also the rename would break every receipt in existence, the one thing ADR-020 decision 8 forbids.
3. **Add the block empty and add no type** — "reserve now, decide later". Rejected by HANDOFF's own words: extensibility is *proven by adding one*, and an empty reserved block was what ADR-020 wrote and the schema then failed to carry. A block nobody has added to is a block nobody has tested.
4. **Add `qualified_timestamp` as the first type**, since that is what a customer will ask for. Rejected: it would make the format say *level 7* about a token this distribution cannot evaluate against a trusted list, and P3 would have to be weakened to let the mode follow. Level 2 first, from an issuer anybody can query, proves the mechanism; level 7 is a row waiting for its ADR and its legal track (ROADMAP phase 5).
5. **Move `WitnessAck` into `attestations`** for uniformity. Rejected: its meaning is positional (*before the run finished*, bound to a BEGIN's leaf); an array at the top of the receipt loses the position, and C1 would have to be re-derived. Uniformity is not worth a weaker claim.
