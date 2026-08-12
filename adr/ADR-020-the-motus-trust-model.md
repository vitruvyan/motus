# ADR-020 — the Motus trust model: what a receipt proves, what it cannot, and where the sold half attaches

- **Status:** PROPOSED — awaiting the founder
- **Date:** 2026-08-12
- **Authority:** CTO, after two claims made in the design conversation were
  shown false by an independent reviewer and had to be withdrawn — see
  *Wrong turns*
- **Depends on:** ADR-019 (the derived root is the value every level below
  attests to; without it there is nothing to attest)
- **Advances:** #51, whose founder-decided split of 2026-08-07 this ADR states
  the *reason* for, and whose item 3 it makes writable
- **Amends:** nothing yet. This ADR adds no public API. It fixes the vocabulary
  and the limits that ADR-021 (the anchor interface) will be written against.

## Context

### What exists, verified against the source rather than the documents

`grep -rn -i anchor src/` returns nine lines, all of them comments explaining
why `Trace.root` fails closed. There is no anchor protocol, no receipt type, no
`publish`, no `verify`. `contract/validate.py` has no subcommand that checks a
trace against a published anchor.

So of #51's table, items 1 and 2 are built and item 3 — the socket — is not.
The row marked **"verifying a trace against an anchor: in this repository,
always"**, which #51 calls the hard constraint, has no code at all.

The gap is already load-bearing on something we ship. `demo/attack_this_run.py`
demonstrates that a resealed trace is caught only by comparing the root against
an anchor, and the page built from it asks a reader to compare a 71-character
string on screen with a memo on a block explorer **by eye**. That is a human
performing, unreliably, the operation this repository has committed to shipping.

### What the previous anchoring did, and what it teaches

The one Vitruvyan anchor that exists on a public chain — TRON Nile,
`4891b6f4399ca8a871d8e96b1fac786053201140bc55057657e79b97fc8601ea`,
`contractRet: SUCCESS` — carries the memo:

```
VITRUVYAN_AUDIT:6347a6b16a38fd4abf44a856fe0c1b7d8dcbc16ae246260b06b4f3981feeea8e
```

64 hexadecimal characters with **no algorithm prefix**. A Motus root is
`sha256:` plus 64 hex — 71 characters — and the prefix is what tells a verifier
in 2034 which function to recompute. `VITRUVYAN_AUDIT:` plus 71 is 87
characters, inside TRON's 100-character memo limit, so nothing forced the
stripping. It was simply not decided.

That is the shape of the problem this ADR exists to prevent: a value published
where it cannot be corrected, in a format nobody chose.

### Why the vocabulary has to be fixed before the interface

Six distinct properties have been called "the anchor proves it" in the course of
one design conversation, and they have different owners, different costs and
different truth conditions. Two claims made confidently in that conversation
were false (below). Both were false in the same way: they attributed to
cryptography a property that only holds if some *non-cryptographic* condition
also holds, and the condition was never stated.

An interface designed on top of an unstated trust model encodes the confusion.

## Decision

### 1. Seven attestation levels, named and normative

A Motus receipt asserts zero or more of these. Each is separately true or false,
and no level implies a level below it.

| | level | the sentence it makes true |
|---|---|---|
| 1 | `INTEGRITY` | this evidence has not been modified since it was sealed |
| 2 | `EXISTENCE` | this evidence existed no later than T |
| 3 | `RETENTION` | evidence once committed cannot be removed or reordered without breaking a chain that is already published |
| 4 | `EXECUTION_CONTINUITY` | an execution begun through Motus left a commitment, whether or not it completed |
| 5 | `PROVENANCE` | this evidence was signed by key K |
| 6 | `IDENTITY` | key K belongs to legal entity Y |
| 7 | `LEGAL_TIME` | T carries qualified temporal value under a recognised regime |

Levels 1–4 are properties of mathematics and of a protocol. Level 5 is also
mathematics — verifying a signature against a key needs no third party. Levels
6 and 7 require somebody accredited to assert something about the world, and
cannot be reached by computation at any price.

### 2. Three continuity properties, and only two of them are provable

The word "continuity" has been used for three claims of very different strength.
They are separated permanently:

- **`RETENTION`** — a run that was committed cannot later be deleted or
  reordered without evidence. **Provable.**
- **`EXECUTION_CONTINUITY`** — a run that *began* through Motus leaves a
  commitment even if it never finished. **Provable, given decision 3.**
- **`SYSTEM_COMPLETENESS`** — every decision the organisation made passed
  through Motus. **Not provable, by us or by anyone.** It is a property of how
  the system was integrated, and it is addressed by attestation, by operational
  policy and by regulation — never by a hash.

`SYSTEM_COMPLETENESS` is named here precisely so that it can be refused when
somebody asks for it in a sales meeting.

### 3. Commitment is two-phase, and the first phase precedes the outcome

A single commitment written at the end of a run cannot support
`EXECUTION_CONTINUITY`, for the reason in *Wrong turns 1*. Therefore:

1. **`BEGIN`** is committed **before the run executes**: tenant, run id,
   sequence number, previous chain head, timestamp, nonce.
2. The run executes.
3. **`END`** is committed on **every terminal path** — success, failure,
   refusal, cancellation, exhausted retry — carrying the derived root and the
   reason for termination.

`BEGIN` enters a **local append-only chain immediately and without network**:
`H(n) = hash(commitment(n) + H(n-1))`. Requiring a sequence number from a remote
authority would couple every run's start to that authority's availability, which
a runtime must not do (alternative 1).

The chain head is **checkpointed at a fixed interval** and the checkpoint is
what gets anchored. Because `BEGIN` precedes the outcome, omitting one means
rewriting a chain whose head is already published — so the operator would have
to decide to suppress a run **before knowing how it turned out**. That is the
whole value of the two phases, and it does not exist if the commitment is
written at the end.

**The checkpoint interval is a parameter, not yet a number.** It bounds a
deniability window and must be derived from measurement — the distribution of
run durations in a real deployment, against the cost per anchored checkpoint —
not chosen because it sounds prudent. ADR-021 states the number and its
derivation, or ships without a default.

**`BEGIN` without `END` is a signal only if it is rare.** If ordinary crashes
and timeouts produce the same signature as suppression, the property is noise.
This is why `END` is required on failure paths; the runtime already models that
lifecycle (#63, #67). The residual class is a process killed between the two
writes, and it is declared here rather than discovered: **a receipt set can
contain `BEGIN` without `END` for reasons that are entirely innocent, and no
verifier may report suppression.** It reports an execution that left no
completion, which is a question, not a verdict.

### 4. What Motus does not prove — normative, and it goes in `contract/`

- **not** that a decision was correct;
- **not** that the facts recorded in it are true;
- **not** that every decision the organisation made passed through Motus;
- **not** that a run which was never registered did not happen;
- **not** compliance with any regulation. Motus produces evidence with which
  compliance can be *demonstrated*; the demonstration is somebody else's.

Publishing this list is not a disclaimer. It is the part a technical evaluator
looks for, and a system that cannot state its limits has not established any.

### 5. The commercial boundary follows from the levels

Levels 1–5 are **open, free, and stay so**: they need no human in the loop, so
charging for them would be charging for arithmetic, and any competent user could
reimplement them.

Levels 6–7 are **sold**, because each requires an accredited party to assert
something and to carry liability for the assertion. Not because we chose to
withhold them.

Note the line falls **inside** the signing story: verifying that a receipt was
signed by key K is level 5 and open; asserting that K belongs to Acme Corp is
level 6 and sold.

And the constraint from #51 is restated as binding: **verification is always
open, at every level.** A verifier must never need a Vitruvyan endpoint, a
Vitruvyan key, or a Vitruvyan company. If checking required trusting us, the
proposition would fail at exactly the point it exists to serve.

### 6. The protocol is anchor-agnostic, and no anchor ships in the runtime

`vitruvyan-motus` gains an anchor *interface* and never an anchor. Concrete
anchors are separate distributions — `motus-anchor-opentimestamps`,
`motus-anchor-tron`, a private one, a qualified one — so that the protocol is an
attestation protocol that can use a blockchain, and not a blockchain product.

The value submitted to any anchor is **`Trace.root` as ADR-019 derives it**,
never a hash of the serialised file: a file digest is defeated by
canonicalisation and commits to no chain structure. The full 71-character form
travels, prefix included.

**A receipt carries the state of its own proof.** With OpenTimestamps a proof is
incomplete until the calendar's commitment reaches Bitcoin and the proof is
upgraded; before that, verification still depends on the calendar server. A
receipt therefore declares `pending` or `anchored`, and a verifier that reports
`VERIFIED` for a pending proof is wrong. For the same reason the property is
**independent verification**, not "offline verification": it needs a chain, and
usually a node — it does not need *us*.

### 7. Signatures and revocation are reserved now, implemented later

Levels 5–7 arrive years after the receipts they will have to attach to.
Therefore the receipt format reserves, from version 1:

```
attestations: [ { type, issuer, key_id, algorithm, signature, signed_at,
                  evidence_ref } ]
```

empty and optional today. Adding an attestation later must not be a format
break, or the choice will one day be between breaking every receipt in
existence and not selling.

**Revoked is not the same as never valid.** A key valid in 2026 and revoked in
2029 signed validly in 2026, and proving that requires the signature's time to
be established independently of the signer — which is level 7. The format
records `signed_at` and permits an attestation to reference the evidence
establishing it. The semantics are decided when level 6 is built; the field is
reserved now so it can be.

## Consequences

**What this costs:**

- **two commitments per run instead of one**, doubling the volume entering the
  batcher. Merkle aggregation absorbs it — one on-chain transaction covers a
  batch regardless of its size — but the local write is on the run's hot path
  and `BEGIN` is synchronous with the run's start by construction. It cannot be
  deferred without destroying the property it exists for;
- **a deniability window remains**, bounded by the checkpoint interval, and we
  must publish that interval rather than let a reader assume it is zero;
- **an irreducible class of innocent `BEGIN` without `END`**, which weakens the
  signal in exactly the deployments most likely to crash;
- **`SYSTEM_COMPLETENESS` is conceded**, permanently and in writing. This is the
  property a buyer most wants, and we will be asked for it.

**What it buys:** the interface in ADR-021 can be written mechanically, because
every question it has to answer — what is submitted, what comes back, what a
verifier reports, where a signature attaches — now has one answer instead of six.

## Wrong turns

Both were made in this design conversation, by the author, with confidence.

**1. "Receipts 1…40 then 42, therefore run 41 was suppressed."** False. If the
sequence number is assigned when the operator submits, a suppressed run is never
numbered: the next honest run takes 41 and there is no gap. The claim is only
true if numbering is inevitable and *precedes* the decision to submit — which
nothing guaranteed. It was inviting because it is true of Certificate
Transparency, where the log is written by a party other than the one who benefits
from the omission.

**2. "A frequent checkpoint bounds the omission, because the operator must
decide within the interval."** Also false, and worse, because it survived the
first correction. The operator learns the outcome **when the run ends**, which is
before any commitment exists; no foresight and no time pressure are involved. The
idea was right and attached to the wrong event — bound to `BEGIN`, which
genuinely precedes the outcome, it does what was claimed. It is decision 3.

**3. A local append-only chain was first proposed as sufficient on its own.** It
is not: the chain is built by the same party that decides what enters it, so
omission at source leaves it perfectly consistent. It secures `RETENTION` and
contributes nothing to `EXECUTION_CONTINUITY` without the two phases.

## Alternatives rejected

**Sequence numbers assigned synchronously by a remote authority.** Closes the
`BEGIN` hole completely, and couples every run's start to a network call. A
runtime whose executions stop when a third party is unreachable is not one
anyone will put in front of a decision, and the failure mode — running anyway,
unnumbered — reinstates exactly the hole.

**Anchoring `SHA256(trace.json)`.** Simpler to explain and wrong: defeated by
canonicalisation, and it commits to a byte sequence rather than to the chain.
ADR-019 exists because a value that looks like a commitment and is not is worse
than none.

**Building a free public anchoring service first.** Proposed, and withdrawn:
OpenTimestamps already runs free calendars that aggregate commitments into
Merkle trees, anchor them to Bitcoin, and support independent verification of an
externally computed hash. Building a second one before shipping the protocol
would spend the money on the commodity half. What OTS does *not* provide is
per-tenant sequencing and low-latency confirmation — which states precisely what
a Vitruvyan authority would add, and defers the decision to build one until it
is a differentiator rather than an imitation.

**Putting an anchor in the runtime.** One import of `tronpy` makes the answer to
"what if we do not use TRON?" a fork.

**"Motus makes traces immutable."** The chain makes them verifiable. Only an
anchor makes them immutable, and only from the moment it is published.

**"Motus makes you compliant."** It does not, and the claim invites a legal
review we would lose. The defensible form is that Motus makes verifiable the
evidence with which compliance is demonstrated. Whether a given regime — the EU
AI Act's record-keeping obligations for high-risk systems being the obvious
candidate — treats such evidence as probative is a question for counsel, and
this ADR asserts nothing about it.
