# ADR-026 — the root commits to the JSON value, not to the characters

- **Status:** PROPOSED
- **Date:** 2026-08-15
- **Authority:** the founder, who read #98 and ADR-024 and found that ADR-024
  had put two unlike problems in one container. The argument below is his; the
  measurements, one rejected leg of it, and the residual in decision 4 are the
  CTO's
- **Corrects:** ADR-024, decision 0 — the line it drew is sharpened, not
  reversed. `J2` is unchanged and stays; `J3` stays withdrawn
- **Closes:** #98, as **accepted semantic equivalence and not a defect**
- **Amends:** nothing executable. **No schema version moves, no digest recipe
  changes, no root moves, and no existing evidence becomes invalid** —
  including Terraveler's anchored traces
- **Advances:** roadmap phase 4. This is a question the freeze makes permanent,
  which is why it is decided before it and not after

## Context

ADR-024 drew this line:

> **A difference in a document's text may be absorbed by the digest only if a
> reader reads the same thing.**

It then closed two families under it — numeric lexemes (`J2`) and string
escapes (`J3`) — and treated them as instances of one problem. **They are not,
and the difference is the whole of this decision.**

### Numbers: two different values, one proof

`5e18` and `5000000000000000511.0` are **different decimal numbers**. A human
reads them as different numbers, and they are. What makes them collide is
binary64: both parse to the same IEEE-754 double in a reader that stores JSON
numbers as doubles, so both produce the same digest.

That is a real loss of information, and it is the dangerous kind: **two
mathematically distinct assertions sharing one cryptographic proof.** `J2`
prevents it, and ADR-024 was right.

### Strings: one value, two spellings

`"approved"` and `"approved"` are **the same JSON string**. Not two values
a reader happens to conflate — two lexical representations that RFC 8259
defines as denoting the same sequence of code points. Every conforming reader
produces `approved` from both.

So no assertion has changed, nothing has collided, and no proof covers two
meanings. What has changed is that

```
grep approved trace.json
```

does not find the second one.

**That is true and it is a fact about `grep`, not about the evidence.** `grep`
matches bytes; it is not a JSON reader, and it never was. ADR-024's line said
*a reader reads the same thing* — and the reader it meant, without saying so,
is a **conforming JSON reader**. Under that reading, the string case was never
on the wrong side of the line.

### What the attempt cost, measured

`J3` was written, accepted and withdrawn within a day, and its costs are
recorded here because they are evidence for this decision rather than only
against that one:

- it did not reach object **keys** — `JSONObject` reads member names through
  the module-global `scanstring` — so the ADR's own demonstration, moved from
  a value to a key, was accepted;
- it **refused a frozen production golden** of the first external integrator,
  which CI forbids modifying;
- it required Python's pure-Python scanner: **≈13× slower**, with a recursion
  budget that depended on the caller's stack depth, so a genuine trace nested
  489 deep raised `RecursionError` from the reader;
- it prescribed a byte-level materialisation of strings, which a producer in
  another language has no reason to match.

## Decision

### 1. The rule, stated once and in one direction each way

> **Same JSON value and structure → same commitment.**
> **Different JSON value or structure → different commitment.**

A text difference that every conforming JSON reader resolves to the same
value is **not tampering**: whitespace, member order, and equivalent string
escapes. A text difference that changes what a reader gets **is**.

### 2. Numbers are the exception, and the exception has a reason

`J2` stands, from trace schema 3.0.0, scoped by the document's own declared
version.

It is not an inconsistency with decision 1 — it is decision 1's second half
being enforced where a language runtime would otherwise break it. Two JSON
numbers that are mathematically different must not share a commitment, and
binary64 makes them share one unless something refuses the lexeme. The
exception is about **what an implementation loses**, not about what JSON means.

### 3. #98 is closed as intended behaviour

`"approved"` and `"approved"` share a root because they are the same
string. That is correct and it is now written down as correct, rather than
left open as a hole a reader of the issue list would take for an unresolved
defect in 1.0.0.

**Anti-obfuscation moves to tooling, where the honest distinction lives.** A
future `motus-lint` may report

```
W_STRING_ESCAPE  printable ASCII written through \uXXXX
```

and say: *this trace is valid, its root is correct, and it contains a lexical
form that a raw-text search will not match.* That is worth having and may
land after 1.0.0.

**The distinction it preserves is the product.** A validator says *this
evidence is not authentic*. A linter says *this evidence is authentic and may
mislead a tool that is not JSON-aware*. Collapsing them would make Motus
refuse a document identical in meaning to one it accepts — a false accusation,
and the worst thing a verifier does. `contract/README.md` already holds the
same rule from the other side: *a refusal outranks a violation*, because
saying "this document is wrong" of a document that may be perfectly correct is
the wrong answer.

### 4. The residual, named rather than closed

Decision 1 says *escapes that every conforming reader resolves to the same
value*. **One escape is outside that set, and it is measured rather than
supposed.**

A lone surrogate — `"\ud800"`, a high surrogate with no low one — is accepted
by this contract's strict reader today and re-serialises unchanged. It does
not denote a code point, and conforming JSON implementations disagree about
it: some refuse, some substitute U+FFFD, some pass it through. So for that one
input, *same JSON value in every reader* is not true, and decision 1's premise
does not hold.

**No rule is added for it here, deliberately.** Three times this month a
correct generalisation of a real defect has been shipped and withdrawn for
refusing legitimate documents, and a Python string can carry a lone surrogate
honestly — `surrogateescape` decoding produces them from bytes a filesystem
handed over. Adding a refusal now would be the fourth, made at the worst
possible moment.

It is recorded as an open, measured question for 1.0.0: **either the freeze
declares lone surrogates outside the format, or it declares that a producer
may emit them and readers may disagree.** Both are answers; silence is not.

### 5. What does not change

No schema version moves. No digest recipe changes. No root moves. Every trace
and receipt that validates today validates after this, including the external
integrator's anchored ones. This ADR changes what the project *says* it is
committing to, and nothing about what it computes.

## An argument for this decision that does not work, recorded so it is not reused

The proposal reached this conclusion partly by arguing that refusing escapes
would force Motus to constrain whitespace, pretty-printing and member order
too — all of which it deliberately absorbs, and one of which an integrator has
just proved it must keep absorbing (a trace surviving a `jsonb` round trip
with its keys reordered).

**Measured, that leg is false:**

| document | `grep approved` finds it |
|---|---|
| canonical | yes |
| indented | yes |
| compact | yes |
| `approved` | **no** |

Whitespace and member order do not hide a value from a raw-text search;
escapes do. The escape case is **not** symmetric with the absorptions, so the
slippery slope is not there.

The decision survives on its other leg, which is stronger and sufficient: the
two documents mean the same thing to every JSON reader, so calling one
tampered would be a false accusation. It is recorded because *a true
constraint reused one question past where it applies is more dangerous than an
ordinary mistake* — the confidence is borrowed from something real — and this
project has already paid for that once, in ADR-023.

## The costs accepted

**An auditor with `grep` can be misled, and we are saying so instead of
preventing it.** Someone counting approvals over raw JSON will undercount if a
document uses escapes. The linter is the answer and it does not exist yet, so
between now and then the only mitigation is this paragraph.

**The equivalence class is asserted for every conforming reader and tested
against one.** Our conformance evidence is Python's. A second implementation
is what would turn decision 1 from a claim into a checked property, and #49's
distribution work is where that would start.

**Somebody will read decision 3 as leniency.** *We decided the hole is fine*
is a shorter sentence than *the two documents are the same document*, and it
is the one that will be repeated. The mitigation is that the rule in decision
1 is short enough to repeat instead.

## Alternatives rejected

**Leave #98 open as a known hole.** This is what the repository does today and
it is the option this ADR exists to replace: it presents 1.0.0 as shipping
with an unresolved integrity defect, which is a false statement about the
product and an unfair one about the format.

**Re-attempt `J3` properly**, reaching keys as well as values. It closes the
`grep` gap and it prescribes byte-level string materialisation for every
producer in every language, in a format whose case for existing is that a
third party can check evidence with their own tools. The measured costs are in
Context and the interoperability cost is the one that decides it.

**Canonicalise strings on write** — emit every string in a single escape form.
Motus already does, in the sense that matters: `json.dumps` writes what it
writes. This would only add a *refusal* for documents another producer wrote
differently, which is `J3` under another name.

**Add the linter now.** It is the right tool and it is not on the critical
path to the freeze. Deciding what the format means is; building a warning
about it is not.
