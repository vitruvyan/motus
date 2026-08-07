# ADR-014 — `result_fingerprint`: what it is a fingerprint of

- **Status:** PROPOSED
- **Date:** 2026-08-08
- **Authority:** founder decision, 2026-08-07, after three adversarial rounds
  against the first external integration
- **Supersedes:** ADR-013. Its context stands and its tripwire survives; its
  central claim — that the field's subject is undefined — no longer holds.
- **Depends on:** the trace v1 schema; #51 (integrity chain), with which the
  normative half of this decision ships.

## Context

ADR-013 declared `result_fingerprint` unverifiable because nothing said what it
was a fingerprint **of**, and deferred the recipe to a named trigger: "the first
consumer that needs to compare result fingerprints across two producers."

That trigger has not fired, and on the evidence it never will in the form it was
written. It assumed the field's purpose was **cross-producer comparability** —
that the value exists so two independent implementations can be checked against
each other. Under that assumption the deferral was correct: with one consumer in
the world, any recipe would have been a guess wearing a specification, and this
project has twice paid for rules derived without the case they must serve.

The founder has since decided a different purpose, and it changes the analysis
rather than merely answering the open question.

## Decision

### 1. Subject: the request and the result, bound together

The fingerprint covers **what was asked of the external system and what came
back**, as one value — not the result alone.

Fingerprinting only the result leaves the request swappable underneath an
unchanged answer. In an advisory setting that is precisely how a recorded
interaction is disputed: not "you never said that" but "that is not what I
asked". The pair is the unit that has to survive.

It generalises past models, which matters because `EffectReceipt` is the receipt
for *any* external effect. For a payment it is the instruction and the bank's
answer; for an upsert, the rows offered and the rows accepted.

### 2. Purpose: tamper evidence, not comparability

The fingerprint exists to establish that **an archived interaction is the one
this run produced**.

This is the decision that dissolves ADR-013's problem rather than answering it.
Comparability across producers required two producers to agree on a recipe, and
nobody was in a position to write one. Tamper evidence requires no such
agreement: the only two parties are this run and its own archive.

It is also the property the first integration cannot get any other way. There,
the conversation text never enters the trace — the trace records shapes (a
length, a count, a score) and the text lives in a separate table. **The
fingerprint is the binding between the trace and material held elsewhere.** Edit
the archived row and it no longer matches. That is exactly what an ordinary
event log, writing to a table it also controls, cannot do.

### 3. Canonicalisation: reuse, do not invent

The covered material is serialised by the same canonical form Motus already
applies for `graph_fingerprint`. Key order, whitespace and unicode
normalisation are pinned there; a second rule would be a second thing that can
disagree with the first.

Without this, re-serialising an unchanged interaction reads as a tamper — a
false positive in a mechanism whose whole value is that a positive means
something.

### 4. The digest is salted, per effect, and the salt is recorded

A hash of a short, low-entropy input is guessable. A holder of the trace can
hypothesise the question, hash it, and check; a few thousand attempts recover a
short one.

So an unsalted fingerprint of a request **partially reintroduces the request
into the trace** — in a form that looks protected and is not. Where text is kept
out of the trace deliberately, which is the case that motivated this field, that
defeats the reason it was kept out. `redact` exists in this codebase precisely
because that concern is real; a fingerprint that quietly undoes it would be a
worse leak than an obvious one, because nobody would look for it.

A per-effect random salt, recorded beside the receipt, closes it. The intended
verifier holds the archive and the trace and can recompute; a holder of the
trace alone can enumerate nothing. **The salt costs the intended verifier
nothing, because the intended verifier was never the one guessing.**

This is stated as the technical consequence of the decided purpose, not as a
preference. A deployment that genuinely wants the trace alone to be checkable
against a known input is choosing a different purpose, and should say so.

### 5. `validate.py` cannot check it, and the schema says which guarantee holds

If the covered material is not in the trace, the validator lacks the input and
cannot recompute the digest. That is not a gap to be closed later; it follows
from the design.

So the field's guarantee is narrower than "the validator checks it" and stronger
than "unverifiable":

> verifiable by a party holding the trace **and** the archive together;
> not verifiable by the validator alone.

The schema must say that sentence. The failure this ADR exists to prevent is a
field that reads like proof and is not, and "the validator accepts the trace"
must never be mistaken for "the fingerprint was checked".

### 6. It lands with schema 1.1, alongside the chain

The salt is a new field, so this is a schema change, and schema 1.0 refuses
those by construction — the same clause that keeps `payload_hash` null until
1.1 ships "the algorithm, the chain validator and an unambiguous activation
indicator".

Both #51 and #52 therefore need 1.1. They answer different questions — the
chain asks *was this trace altered*, the fingerprint asks *does this archived
interaction match the trace* — and neither substitutes for the other, but they
share an activation event and should be planned as one.

**Until 1.1 ships, nothing changes.** The field keeps today's behaviour and
today's honest description. What this ADR settles is what the recipe will be,
so that the work is a decision already taken rather than one deferred again.

### 7. The name

`result_fingerprint` covering the request invites the next implementer to
fingerprint only the result — reproducing the defect. The 1.1 field is named for
what it covers, and `result_fingerprint` is not redefined in place: a 1.0 trace
means what 1.0 said it meant, forever.

The successor name is left to the 1.1 ADR, which writes it next to the schema it
belongs in. Naming it here, three months before the schema exists, is how a name
gets chosen for the wrong shape.

## Consequences

- The trigger mechanism ADR-013 relied on is retired for this field. It was
  written against a purpose the product does not have, and an agent checking
  each cycle for a condition that cannot occur is worse than no check: it
  reports "not yet" indefinitely and reads like diligence.
- The tripwire survives, retargeted. `test_result_fingerprint_is_declared_
  unverifiable_and_this_pins_it` still pins today's unchecked state, and its
  message now points here. It fails the day the field gains a recipe in code,
  forcing the schema and the validator to move together — which is the whole
  reason ADR-013 built it.
- One decision is deferred deliberately and named: the successor field's name,
  to the ADR that writes the 1.1 schema.
- Nothing in the shipped runtime changes today. 544 tests, the frozen corpora
  and every fixture are untouched by this ADR.

## Alternatives rejected

**Keep deferring.** ADR-013's own reasoning: a recipe without a real case
constrains the wrong thing. That reasoning was sound and no longer applies —
the case exists and named its own purpose. Deferring past the point where the
question is answered is not caution, it is a habit.

**Define the recipe and ship it in 1.0.** Impossible without the salt, and the
salt needs a field. Shipping the recipe *without* the salt to stay inside 1.0
would trade a confidentiality property for a schedule, in the one field whose
job is to be trustworthy.

**Cross-producer comparability as well.** Attractive, and unearned: it requires
a second producer to have a need, and there is still exactly one. If that need
ever arrives it can be added on top — a comparable fingerprint is a salted one
with the salt agreed rather than random, which is a smaller change than getting
it wrong now.

**Fingerprint the result only, and record the request separately.** Equivalent
in what it stores, weaker in what it proves: two values that are each intact do
not establish that they belong together. The binding is the point.
