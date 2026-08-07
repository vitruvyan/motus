# ADR-013 — `result_fingerprint`: declared unverifiable, with a trigger and a tripwire

- **Status:** SUPERSEDED by ADR-014 (2026-08-08), before acceptance
- **Date:** 2026-08-06
- **Authority:** founder direction, after the first external integration report

> This ADR was never accepted. It proposed deferring the recipe until a named
> trigger fired; while it sat in PROPOSED the founder answered the underlying
> question instead, and the trigger it named — a consumer needing to compare
> fingerprints across two producers — turned out to be written against a
> purpose the product does not have. ADR-014 records what the field is a
> fingerprint of. Everything below stands as the analysis that produced that
> question, and its tripwire survives, retargeted.
- **Depends on:** ADR-005 (0.6 replay), the trace v1 schema
- **Amends:** `contract/trace.v1.schema.json` — the `result_fingerprint`
  property of `EffectReceipt` gains a `description`. Additive: no structure,
  pattern, or validation behaviour changes. ADR-001…012 stand unedited.

## Context

The first integration of Motus by anyone outside this repository — Terraveler,
a real RAG pipeline — attacked the trace with six tampers. Five were caught by
name. One passed: a forged `result_fingerprint`.

That a `receipt_id` is not verifiable offline is correct and already declared —
the schema says adapter evidence is "recorded by Motus, never upgraded into an
exactly-once claim", and a validator cannot query a third party's API. The
other field is the problem.

**`result_fingerprint` has a format and no meaning.** Searching the whole
project — `contract/`, `docs/`, `adr/`, `README.md` — it appears in exactly one
place: the schema, which fixes its shape (`^effect:sha256:[0-9a-f]{64}$`) and
nothing else. No recipe says what the fingerprint is taken *of*. `validate.py`
never looks at it.

The integrator found this because they had to write the adapter and **decide
themselves**. They fingerprinted the response text. Another implementer would
fingerprint the whole HTTP response, or the request body. None is wrong,
because nothing says which is right — and so the field is not comparable across
two producers, which is precisely what a fingerprint exists for.

The project's own standard makes this sharp. `graph_fingerprint` has a written
recipe, a rule (SB2) that recomputes it and caught the integrator's graph
tamper, and an explicit sentence: "every fixture fingerprint is TRUE, never
decorative." **By the standard Motus applies to itself, `result_fingerprint` is
today decorative.**

## Decision

**Declare it unverifiable for now — honestly, as `receipt_id` already is —
rather than invent a recipe with one consumer in the world.**

Inventing a normative recipe now means guessing what a second producer will
need to compare, and this project has paid, more than once, for guessing rules
without a real case to derive them from (the absolute SLO ceiling that measured
the runner; the async A/B that could not resolve the effect it reported). The
honest move is to stop the field pretending to be a guarantee, and to derive
the recipe from the evidence when a second consumer produces it.

Because "declare it and revisit later" is exactly the shape that rots in this
project — issue #32 spent a day asserting facts that had been false since the
day before — the decision is **three pieces, not one**, and it is unsafe
without all three.

### 1. The schema note (added)

The `result_fingerprint` property now carries a description stating that its
format is fixed and its subject is not, that `validate.py` does not recompute
it, and that it is declared unverifiable like `receipt_id`. An integrator
writing an adapter reads this where they decide what to stamp — so the next
consumer meets the gap the way the first did, by contact, rather than by our
memory. External contact is this project's actual discovery mechanism; the
README bets on it in as many words.

### 2. The trigger (written into the schema, in the fan-out form)

> *Trigger to give it a recipe: the first consumer that needs to compare result
> fingerprints across two producers.*

This is deliberately the same shape as `guarantees.md` §5's fan-out deferral —
"trigger: the first real consumer that needs declared concurrent topology." An
externally-fired event that no test can observe is un-deferred by a named
condition plus someone who checks it. That someone is `motus-issue-auditor`,
whose brief already includes "can a stated reopening criterion now be
evaluated?", and which runs before planning and after releases. The trigger is
not "we will remember"; it is "an agent checks each cycle whether it fired."

### 3. The tripwire (machine-enforced)

`tests/test_motus_replay.py::test_result_fingerprint_is_declared_unverifiable_and_this_pins_it`
records an effect with a `result_fingerprint`, tampers it to a different
same-format value, and asserts `validate.py` **still accepts** the trace —
pinning today's unchecked state.

The trigger guards against forgetting to *start*. The tripwire guards against
doing it *half-way*: the day anyone gives the field a recipe, `validate.py`
rejects the tamper, the test fails, and its message points here — forcing this
ADR to be superseded rather than silently contradicted. A recipe in the code
without the contract following, or the reverse, cannot pass CI.

## Consequences

- A field that looked like evidence stops claiming to be. That is a reduction
  in what the trace asserts, and it is the honest one: an unverifiable
  guarantee is worse than a declared limitation.
- Integrators are told, at the point they implement, not to rely on
  `result_fingerprint` for cross-producer comparison. In this project's
  experience that is how the *next* consumer surfaces the need with real
  evidence attached.
- No structure, pattern, or validation behaviour changes; every frozen corpus
  validates unchanged (539 tests green with the amended schema).
- When the trigger fires, a successor ADR writes the recipe **and** the
  recomputation rule together — the tripwire makes shipping one without the
  other impossible.

## Alternatives rejected

**Invent the recipe now.** The obvious fix, and wrong for the same reason the
absolute performance ceiling was: a rule derived without the case it must serve
constrains the wrong thing. With one consumer, "fingerprint of the result text"
is a guess wearing a specification.

**Leave it silent.** What it was. A field with a format, no meaning, and no
note is the state that let a tamper pass and cost the integrator the time to
discover it alone. Rejected because silence is what this ADR exists to end.

**A GitHub issue and nothing else.** The mechanism that demonstrably fails
here. #32's body stayed false for a day because an issue is a promise to
remember, and this project has measured that it does not.
