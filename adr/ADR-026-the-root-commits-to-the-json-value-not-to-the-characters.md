# ADR-026 — the root commits to the JSON value, not to the characters

- **Status:** PROPOSED
- **Date:** 2026-08-15
- **Authority:** the founder, who read #98 and ADR-024 and found that ADR-024
  had put two unlike problems in one container, and who then closed decision 4
  against the CTO's recommendation to leave it open. The argument in decisions
  1–3 is his; the measurements and one rejected leg of it are the CTO's
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

The rule's premise is *every conforming reader resolves it to the same value*,
and one input fails that premise rather than the rule. Decision 4 removes it
from the format instead of weakening the sentence.

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

### 4. Unpaired surrogates are outside the Motus format

Decision 1 says *escapes that every conforming reader resolves to the same
value*. **One escape is outside that set**, and the first draft of this ADR
left it open as a question for 1.0.0. The founder closed it instead, and the
reason is decision 1 itself: a rule whose premise fails for some input is not a
rule with an exception, it is a rule with a hole.

> **A Motus string MUST represent a valid sequence of Unicode scalar values.**

A surrogate code point (U+D800–U+DFFF) is not a scalar value. Unpaired, it
denotes no character, has no UTF-8 encoding, and conforming JSON
implementations disagree about it — some refuse, some substitute U+FFFD, some
pass it through. A document containing one has more than one reading, which is
the same disqualification a duplicate member name carries, and it belongs to
the same rule: **`J1`, not a new rule**, because `J1` has always meant *strict
RFC 8259* and the producer has always refused these under it.

A surrogate **pair** is a character and stays: `"\ud83d\ude00"` is U+1F600,
the decoder joins it before anyone sees it, and what reaches a document is one
scalar value. **This does not touch escape forms.** `"appro\u0076ed"` and
`"approved"` are the same string, share a root, and are both accepted, exactly
as decision 1 says. What is refused is a string that denotes nothing.

**`surrogateescape` is not a reason to admit them**, and this is the argument
the first draft made and the founder rejected. Python's convention for
carrying undecodable bytes through a `str` is one language's workaround; it
does not travel to a reader in another language, and adopting it would write a
runtime's private mechanism into the meaning of the format. A producer that
must preserve bytes which are not Unicode — a filename a filesystem handed
over, a payload from a legacy source — encodes them explicitly: base64, hex, or
bytes with a declared encoding.

#### What this actually repaired, which was not what the question looked like

Measured before writing any of it. **The producer has refused unpaired
surrogates since 0.11.0** (`trace._encodable`, added 2026-08-12). Both readers
accepted them. So this was never a question about what the format permits — it
was **a producer/verifier asymmetry**, the same class this project already
found once in the same function, where the runtime's loader accepted duplicate
members that the contract validator refused.

And *"we accept lone surrogates today"* is false. A trace carrying one does not
validate — it **crashes**, with `UnicodeEncodeError` raised from inside
`canonical_json`, naming the codec rather than the document and naming no rule
at all. Turning an unattributable crash into an attributable refusal is not a
change to a supported behaviour, which is what settles the schema version.

The sweep found the class had two more members than the report did:

| frontier | before | after |
|---|---|---|
| `validate._loads_strict` | accepted | refuses, naming the JSON path |
| `trace._loads_canonical` | accepted | refuses |
| `validate._j1_violations` (Python objects, no text) | **passed silently** | `J1`, structural |
| `validate.canonical_json` / `fingerprint` | `UnicodeEncodeError` | attributable refusal |
| `trace._canonical_bytes` | `UnicodeEncodeError` | attributable refusal |
| producer: fact values, run_id, metadata | already refused | unchanged |
| producer: a node's exception message | never copied into the trace | unchanged |

The two that no report named — the Python-object path and the fingerprint —
were found by the ordered sweep, and the Python-object path was then found
*again* by a mutation probe, which is what forced it to be tested rather than
merely fixed.

#### Why this is not `J3` under another name

`J3` asked **which escape form was written**. Only the lexeme answers that, so
it needed `parse_string`, which needed CPython's pure-Python scanner: 13× slower,
a recursion budget that depended on the caller's stack depth, and object
**keys** structurally out of reach because `JSONObject` calls the module-global
`scanstring`. It also refused a frozen production golden for an em-dash.

This asks **what value did the reader get**. The parsed document answers, so
the C scanner keeps running, keys are covered because keys are in the document,
and the frozen golden — which writes `—` as the escape `\u2014`, the exact
artefact `J3` refused — is accepted. That golden is now a test.

The cost, measured on a 54 KiB trace: one C-level serialise decides and the
walk runs only to locate a string in a document already known to be bad, so
`_loads_strict` is **+15%** and `Trace.from_json` **+16%**; the naive walk was
+63%. `validate_trace` is **+0.2%** — schema validation dominates it.

### 5. What does not change, each with the measurement that says so

- **No schema version moves.** A lone-surrogate document was never accepted end
  to end; it crashed mid-verification. `J2`'s version scoping does not apply
  here and must not be copied: `J2` was scoped to 3.0.0+ because documents
  written under earlier rules did not have it, whereas `J1` has meant *strict
  RFC 8259* at every version and the producer has enforced it — before
  `_encodable` existed, such a run raised at the seal rather than writing a
  trace.
- **No digest recipe changes.** `canonical_json` and `_canonical_bytes`
  serialise exactly as before; the only addition is a `try/except` that renames
  an exception on a path that was already raising, and costs nothing on the
  path that does not.
- **No root moves.** Every JSON and JSONL document in the tree was scanned:
  **zero** carry an unpaired surrogate. That scan is a test, so it is re-taken
  on every run rather than quoted from here.
- **The external integrator's evidence stays valid**, including the anchored
  traces and the frozen production golden.

This ADR changes what the project *says* it is committing to. The code change
under it makes two readers agree with a producer that was already right.

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

**A producer that was relying on `surrogateescape` to carry filesystem bytes
through a Motus string now has work to do**, and this refuses their documents
where 0.11.0 crashed on them. We know of none, the corpus contains none, and
the honest statement is that the crash was never a supported path — but a
clearer failure is still a failure, arriving at a different time.

**The predicate is duplicated** between the package and the contract validator,
because the kernel is stdlib-only and cannot import `jsonschema`. Duplication
is how two sides drift, which is this defect exactly. The mitigation is a test
running both over one table of strings and asserting they agree, which is
weaker than sharing the code and is the strongest thing available under the
zero-dependency constraint.

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
