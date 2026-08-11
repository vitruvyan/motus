# ADR-019 — the root must commit to the chain, and 2.0.0's does not

- **Status:** PROPOSED — awaiting the founder
- **Date:** 2026-08-12
- **Authority:** CTO, after reproducing the defect against a real 2.0.0 trace
- **Supersedes in part:** ADR-017 §2, whose remedy does not achieve what it
  claims. This ADR keeps that section's *intent* and replaces its recipe.
- **Advances:** #51 (the anchor interface — this is its precondition)
- **Affects:** every trace emitted by 0.8.0 and 0.8.1.

## Context

ADR-017 §2 decided that the chain starts at the header rather than at the first
record, because an automated reviewer had shown that chaining records alone left
`run_id`, `policy`, `metadata` and `graph.code_fingerprint` outside the root:
all four could be rewritten, the terminal's `payload_hash` did not move, and the
validator passed the result clean.

The diagnosis was right. The remedy does not work, and it fails for exactly the
reason it was written to fix.

A record's digest is computed over that record with its **entire** integrity
block nulled — `prev_hash` included:

```python
# trace.py, _seal
payload["integrity"] = {"payload_hash": None, "prev_hash": None}
digest = "sha256:" + hashlib.sha256(_canonical_bytes(payload)).hexdigest()
```

So no digest incorporates its predecessor. These are not links in a chain; they
are independent hashes standing next to a pointer that nothing hashes. The
terminal record's `payload_hash` — which README, rule T11 and the Terraveler
migration brief all call *the root* — is the digest of the terminal record and
of nothing else. Moving the chain's start to the header changed which value
`records[0].prev_hash` holds, and left the root covering the same one record it
always did.

Reproduced against a real trace produced by 0.8.1, where the attacker edits the
trace **and reseals the chain** — the recipe is public, it is rule T11, and no
secret is involved:

```
root pubblicata sull'ancora: sha256:dd212db1baedebc4900cbc878fa510b8…

* ribalta il verdetto APPROVED -> REJECTED   validatore=VERDE   root INVARIATA
* riscrive il run_id nell'header             validatore=VERDE   root INVARIATA
* cambia la policy nell'header               validatore=VERDE   root INVARIATA
* cancella un record intermedio              validatore=rifiuta root INVARIATA
```

Three of the four pass the published validator. **All four leave the anchored
value untouched**, which is the part that matters: an anchor exists precisely to
be checked by someone who was not there, and it reports agreement on a document
that has been rewritten.

The fourth is refused, but not by the chain — deleting a record breaks the
structural rules about attempts and sequence numbers. That refusal is real and
would survive any recipe; it is not evidence that the chain works.

Two things obscured this until now.

**The terminal record carries a microsecond `ts`.** Two distinct runs therefore
have distinct roots, and the value behaves like an identifier under every
ordinary test. It distinguishes runs by accident of the clock, not by
cryptographic coverage — and an attacker who does not touch the terminal record
inherits its root exactly.

**The comment defending the recipe is half true.** It says *"what a digest must
not cover is its own value, and a constant null is not one"*. That is correct for
`payload_hash` and false for `prev_hash`, which is fully determined before the
digest is computed and is the one field that must be inside it. The reasoning
protected the right field and took a second one with it.

## Decision

### 1. Schema 3.0.0: the digest covers `prev_hash`

Under 3.0.0 a record's `payload_hash` is sha256 over the canonical object form
of that record with **`payload_hash` nulled and `prev_hash` present**. Every
digest then commits to its predecessor, transitively to the first record, and
through the first record's `prev_hash` to the header. The terminal digest
commits to the whole run, which is what the root was always documented to do.

`prev_hash` remains a stored field and is not made derivable: a reader who
checks one record in isolation must be able to see which predecessor it claims
without recomputing the entire trace.

### 2. 2.0.0 is not amended, and stays readable

Traces already written are genuine records of real runs, and rewriting the rule
under them would make honest documents fail. A 2.0.0 trace continues to validate
under the 2.0.0 recipe.

What changes is that the validator **says what a 2.0.0 root is worth**, on
stderr, without altering the exit code:

```
note: schema 2.0.0 — this trace's root covers only its terminal record.
      It is not an anchorable commitment to the run (ADR-019).
```

Not a violation: the document is not malformed, and calling it one would refuse
evidence that is telling the truth about a real run. The defect is in what the
root *proves*, and the honest place to state that is next to the value.

### 3. `Trace.root` returns None below 3.0.0

The property exists to be handed to an anchor. Below 3.0.0 there is no value it
can return that means what a caller will assume, so it returns `None` and an
integrator anchors nothing rather than anchoring a decoration.

This is a breaking change to a public property, deliberately: the failure it
replaces is silent and the failure it introduces is loud.

## What this does not do

**It does not make a trace tamper-proof.** Anyone holding the file can rewrite
it and reseal it under the correct recipe too; what they cannot do is make the
result agree with a root published somewhere they do not control. The chain
makes tampering *detectable given an anchor*, and Motus still ships no anchor
(#51). Without one this buys internal consistency and an honest root — which is
the precondition for #51, not a substitute for it.

**It does not touch the frozen fixture corpus.** The 2.0.0 fixtures under
`contract/fixtures/` continue to describe 2.0.0 exactly as they do today. 3.0.0
gets its own, added alongside.

## The cost

Every trace emitted before this ADR is unanchorable, and the two releases that
produced them, 0.8.0 and 0.8.1, said otherwise in their README. That statement
was wrong when it was written — by me, on 2026-08-10, in commit `1d2ba42` — and
the correction ships with the fix rather than after it.

Terraveler ran 0.8.1 in production and holds traces from it. They remain valid,
replayable evidence; they are not anchorable, and phase 3 of their migration
brief must not run until 3.0.0 is in their hands.
