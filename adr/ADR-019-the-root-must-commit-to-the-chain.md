# ADR-019 — the root must commit to the chain, and 2.0.0's does not

- **Status:** ACCEPTED
- **Date:** 2026-08-12
- **Accepted:** 2026-08-12 by the founder, after an adversarial round of three
  independent agents found the first version of this fix re-opened the same
  defect one level up — see *Decision 3*
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
      It is not an anchorable commitment to the run (ADR-019); re-run under
      3.0.0 to obtain one.
```

Not a violation: the document is not malformed, and calling it one would refuse
evidence that is telling the truth about a real run. The defect is in what the
root *proves*, and the honest place to state that is next to the value.

### 3. `Trace.root` is DERIVED, and returns None whenever it cannot be

The property exists to be handed to an anchor, so it must not repeat a claim
the document makes about itself. It recomputes the header digest and every
record digest from the document's own contents, under that version's recipe,
and returns a value only when what the document declares is what its contents
produce. Otherwise `None`, and the integrator anchors nothing.

An adversarial round found three attacks that reading the field permits, all
within a day of the recipe being fixed, and the third is the one that matters:

- **a rewritten header with a stale first link.** The editor changes `policy`
  and `metadata`, leaves `records[0].prev_hash` pointing at the OLD header
  digest, and reseals. Every declared digest recomputes, the chain is
  internally perfect, and the terminal digest does not move. Reproduced:

  ```
  root pubblicata : sha256:1829ff10e54652db…
  root del forgiato: sha256:1829ff10e54652db…
  campo grezzo INVARIATO: True
  ```

  This is §1's defect one level up. Including `prev_hash` in the digest makes
  each record commit to *the string that names its predecessor*, and a string
  is not its predecessor. The header enters the root only if the reader hashes
  the header itself — which is what deriving means, and why the fix is not
  another recipe change;
- **a 2.0.0 document relabelled 3.0.0**, which turned an unanchorable value
  into an anchorable-looking one by editing one string;
- **any invented chain at all**: `from_dict` accepts a document without
  verifying it, so `root` was returning whatever the file said.

The version gate is an ALLOW-list (`_CHAIN_BINDS_PREV`), not a deny-list. The
deny-list it replaced failed open: a version nobody enumerated — a typo, a
future 4.0.0, whatever an editor put in the header — was treated as chained.

This is a breaking change to a public property, deliberately: the failure it
replaces is silent and the failure it introduces is loud. It is also O(n) in
records on first access, memoized per instance; a `Trace` is immutable and
every append returns a new one, so no cached root can outlive its records.

### 4. Two ways a file can be evidence of more than one run

Neither is about the chain, and both defeat an anchor, so both are closed here:

- **repeated member names.** A document carrying two complete accounts of a
  run — two `run` objects, two `records` arrays — validated clean with the root
  reproduced exactly. Python, jq, node and `jsonb` keep the last; a human,
  `git diff` and a first-wins reader see the first. Refused as J1: which
  reading is "the" document is not a question this validator may answer;
- **unpaired surrogates.** A lone surrogate is a Python `str`, passes every
  other J1 check, and has no UTF-8 encoding, so it raised out of `Runtime.run()`
  from inside the seal — not `NodeFailed`, so the caller got no trace, and the
  sink kept a run with no terminal record. `json.loads('"\ud800"')` produces
  one silently, so a node parsing an external payload reaches it without an
  attacker. Now refused on the API path like NaN and tuples, with the run
  failing normally.

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

## What this ADR does not fix, and knows it

**Numbers are committed at binary64 precision, not as written.** The digest is
taken over parsed values, so a float in a trace commits to its IEEE-754 double
and not to its literal. `5e+18` in an artifact can be rewritten to
`5000000000000000511.0` — validator green, root unchanged, and `jq` reads a
different number than the one that was sealed. A snowflake-shaped id has 256
literals sharing one root. `contract/README.md` already names this hazard
("floats forbidden there precisely because their canonical representation is
not settled") for the fingerprint recipe; T11 hashes whole records, where
caller floats live, and the settlement never arrived.

It is not fixed here because every repair is a contract decision with its own
blast radius — forbid non-integer numbers in hashed positions, canonicalize by
source lexeme, or hash the number positions as bytes — and choosing one two
days after two schema revisions, without its own round, is how the defect this
ADR corrects was introduced. Tracked separately; the honest statement in the
meantime is that the root commits to the run *as parsed*.

**Segments are not chained to each other.** A resumed run starts a fresh chain
from its own header. `run.resume.bundle_fingerprint` is a digest over the whole
source document and sits inside the header, so it IS inside the root — verified
— but no root binds segment N to segment N−1 as one anchorable run. That
question belongs to #51.

## The cost

Every trace emitted before this ADR is unanchorable, and the two releases that
produced them, 0.8.0 and 0.8.1, said otherwise in their README. That statement
was wrong when it was written — by me, on 2026-08-10, in commit `1d2ba42` — and
the correction ships with the fix rather than after it.

**The release is blocked on evidence, not on code.** `__version__` is still
`0.8.1` — a released version — while the trace schema moved and a public
property changed semantics. ADR-006 couples the version string to committed
baseline evidence, so the bump belongs to a release that regenerates it; until
then this branch deliberately carries a version it must not keep.

Terraveler ran 0.8.1 in production and holds traces from it. They remain valid,
replayable evidence; they are not anchorable, and phase 3 of their migration
brief must not run until 3.0.0 is in their hands.
