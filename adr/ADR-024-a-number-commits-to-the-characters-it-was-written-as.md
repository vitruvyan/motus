# ADR-024 — a value commits to the characters it was written as

- **Status:** ACCEPTED
- **Date:** 2026-08-14
- **Accepted:** 2026-08-14 by the founder
- **Authority:** CTO. #74 says the choice "needs its own ADR and its own
  adversarial round: making this call two days after two schema revisions,
  without one, is exactly how the defect ADR-019 corrects was introduced"
- **Depends on:** ADR-019 (the root must commit to the chain), ADR-017 (the
  integrity chain and its recipe)
- **Advances:** #74. It is on the critical path to 1.0.0 because it touches what
  the root commits to, and the root is what gets frozen
- **Amends:** nothing. **No digest recipe changes and no schema version moves.**
  Every genuine trace keeps the root it already has — see decision 2

## Context

T11's digest is taken over **parsed** values. A number in a trace therefore
commits to its IEEE-754 double and not to the characters in the file.

Measured, on a genuine kernel run writing `Fact("importo", 5e18, ...)`:

```
rewritten to 5000000000000000511.0  -> validator_exit=0  same_root=True
rewritten to 4999999999999999489.0  -> validator_exit=0  same_root=True
```

`jq`, `git diff` and a human read a different number; the cryptography does
not. A snowflake-shaped id has 256 integer literals sharing one root. At the
other end a genuine `0.0` accepts `1e-400` on the same terms, so *"zero
exposure"* and *"nonzero exposure"* share a root with no large magnitude
involved.

**This is not a chain defect.** The chain is sound. The subject it commits to
is narrower than the contract implies, and `contract/README.md` already named
the hazard for the fingerprint recipe — *"floats forbidden there precisely
because their canonical representation is not settled"* — while T11 hashes
whole records, where caller floats live in `writes.facts[].value`,
`reads[].value`, `initial_state` and `run.metadata`. The settlement it defers
to never arrived, and 3.0.0 is the version that promoted the terminal digest to
an anchorable commitment.

### Why the three repairs #74 lists are not the answer

**1. Forbid non-integer numbers in hashed positions.** This is what
`contract/README.md` implies, and it breaks every caller writing a float — a
confidence, a score, a price, an exposure. Motus exists to record what a system
decided, and a runtime that refuses `0.87` has refused the ordinary case.
Pushing floats into strings does not remove the problem; it moves it into the
caller's encoding, where nothing checks it.

**2. Canonicalize numbers by their source lexeme.** Correct in intent and
**not implementable as stated**, for a reason that is the whole content of this
ADR and is easy to miss: *on the producing side there is no source lexeme.* A
live run holds a Python float. The lexeme does not exist until the serializer
creates it. "Commit to the lexeme it was written as" and "require the lexeme to
be the one the serializer writes" are therefore **the same rule**, and only the
second can be stated at both ends.

**3. Hash the number positions as bytes.** Collapses into 2 for the same
reason, and adds a hand-rolled serializer on the write hot path of a project
whose cumulative performance gate is already failing.

## Decision

### 0. The line, stated before the rules that implement it

> **A difference in a document's text may be absorbed by the digest only if a
> reader reads the same thing.**

The digest is taken over parsed values, so *every* text difference the parser
flattens is a candidate collision. #74 reported one — numbers. It is not the
only one, and repairing it alone would have closed an instance and left the
class open. Measured:

| text difference | same root | verdict |
|---|---|---|
| a number rewritten to another lexeme with the same double | yes | **refused** — a reader reads a different number |
| a string escape (`appro\u0076ed`) | yes | **refused** — see below |
| the solidus escape, non-ASCII escapes | yes | **refused** |
| whitespace | yes | **absorbed** — nobody reads it |
| member order | yes | **absorbed** — nobody reads it, and canonical JSON sorts it |
| Unicode normalisation (`é` vs `e´`) | **no** | not a member; the values differ |

**The string case is sharp rather than cosmetic**, and it is the one this ADR
would have missed:

```
dd0397dbad6f6b07  {"decision":"approved"}
dd0397dbad6f6b07  {"decision":"appro\u0076ed"}
```

Same root. The second renders as *approved* in any viewer, and `grep approved`
over the raw file **does not find it** — so somebody auditing files by hand
counts the approvals wrong.

Whitespace and member order must stay absorbed: the JSON ⟷ JSONL equivalence
that T11 and the fixtures pin depends on it, and a pretty-printed document is
the same evidence.

### 1. A numeric lexeme must be the one the canonical serializer produces

> **A JSON number in a Motus document is well-formed only if its characters are
> exactly what serializing its parsed value produces. A document containing any
> other numeric lexeme is REFUSED — rule `J2`.**

Refused, not repaired. A document that says `5000000000000000511.0` where a
genuine one would say `5e+18` is either machine-produced by something that is
not us, or edited by hand. Both are questions for the holder, and neither is
ours to normalise away.

`J2` sits beside `J1` deliberately: both are rules about the **text**, both
refuse rather than interpret, and both exist because a parser hands you a value
that has already discarded the evidence.

### 1b. A string's escapes must be the ones the canonical serializer produces

Rule `J3`, and it is the same rule as `J2` applied to the other lexeme family.
A string written with an escape this contract does not write is refused.

It costs more than `J2` and the cost is stated: Python's C scanner ignores a
custom `parse_string`, so the pure-Python scanner must be used — **13× slower**,
1.2 ms for a 200-record document, paid at loading and never on the write path.

**A rule about characters cannot be enforced by a pattern over the text**, for
both families. `parse_float`, `parse_int` and `parse_string` are called only for
real JSON tokens; a regular expression over the raw bytes flags
`{"note": "cost 5.10 eur"}`, where `5.10` is somebody's prose. The first probe
written for this ADR was a regular expression and had exactly that false
positive.

### 2. No digest recipe changes, and no genuine root moves

This is why the decision is cheap enough to take before the freeze, and it is
worth showing rather than asserting.

`_canonical_bytes` already serializes through `json.dumps`, so **a document we
produced already contains canonical lexemes** — measured across `5e18`, `0.0`,
`0.1`, `1/3`, `1e-300`, `2**53`, `-0.5` and `1.0`, with zero non-canonical
forms. The defect was never in what we write. It was that a **verifier**
re-serializes what it parses, and re-serialization launders the difference.

So the repair is a **precondition on loading**, not a change to hashing:

| | before | after |
|---|---|---|
| genuine document | root R | root R, unchanged |
| tampered lexeme, same double | root R — collides | **refused** |
| schema version | 3.0.0 | 3.0.0 |

Cost, measured on a real 2 655-byte trace: **0.33 ms per document, at
verification time**, and nothing at all on the write path.

### 3. Where the guarantee is available, and where it is not

**`Trace.root` computed from an already-parsed document cannot see this, and no
implementation can.** A parsed `5e18` from a genuine file and a parsed `5e18`
from a tampered one are the same object. The evidence was destroyed by the
parser before we were called.

So the guarantee belongs to **whoever holds the bytes**:

- `contract/validate.py` reads bytes and applies `J2`. Full guarantee;
- a loader given the document **text** applies `J2` before parsing. Full
  guarantee;
- `Trace.from_dict` on a dict somebody else parsed: **no guarantee, and it is
  stated rather than implied.** The caller discarded the evidence upstream of
  us.

This is the same shape as `J1`, which exists precisely because an API caller
hands over Python objects the parser's fence never saw. Stating the residual is
not a weakness of the design; **the design that pretends otherwise is the one
this project keeps having to correct.**

### 4. The number form is now specified, and it is specified by naming an implementation

`contract/README.md` says the canonical representation of floats "is not
settled". This settles it, and honesty requires saying how narrowly.

*Shortest decimal that round-trips* is **not** a sufficient cross-language
specification. Python renders `5e18` as `5e+18`; JavaScript's
`Number.prototype.toString` renders the same double as `5000000000000000000`.
Both are shortest round-trips. They disagree on when to use an exponent, and a
rule that admits both admits exactly the collision this ADR closes.

So the canonical form is **what CPython's `json.dumps` emits**, which is
`repr(float)` — the shortest round-tripping decimal, with CPython's exponent
thresholds. A producer in another language must match it to write a Motus
document. That is a real burden and it is the price of a number form that is
*decidable*; the alternative is a form under which two byte-different documents
share a root, which is the defect.

## The costs accepted

**A non-Python producer must implement CPython's float formatting.** The rules
are small and documented, but they are ours rather than a standard's. Somebody
will hit this, and the error message must say what was expected rather than
"invalid number".

**A hand-edited document is refused even when the edit was innocent.** Adding a
trailing zero to a value for readability makes a trace unverifiable. That is
intended — the document is the evidence — and it will surprise somebody.

**Two documents that differ only in a numeric lexeme are two documents.** Which
is the correct semantics and removes a convenience that existed by accident: a
trace could previously survive a round trip through any JSON library that
reformatted numbers. It can no longer. **The convenience was the defect.**

**`Trace.from_dict` keeps a hole that cannot be closed.** Decision 3.

## Wrong turns

**0. I repaired the instance and not the class, and the founder caught it.**
#74 reports numbers, so I wrote a rule about numbers, verified it thoroughly and
was ready to merge. The objection was general — *"my fear with a pattern is that
it solves the single problem and does not fix the problem globally"* — and it
was right about something more specific than it claimed: the question is not
which tool matches the text, it is **what else the parser discards**. Ten
minutes of measurement found string escapes, and the grep evasion inside them.

The repair is not "and also strings". It is decision 0, which states the line,
plus a test that **enumerates the class**: every value-preserving text
transformation we know of, each declared refused or absorbed with a reason, and
each asserted to genuinely produce the same root — because a row that changes
the parsed value proves nothing. A new member goes in that table, and the table
forces a decision instead of allowing a quiet default.

**1. A `float` subclass whose `__repr__` returns the lexeme.** The obvious
first idea, and it does not work: `json.dumps` ignores it. The C-accelerated
encoder calls `float.__repr__` directly rather than the instance's, so a
subclass carrying `'5000000000000000511.0'` serialized as `5e+18` like every
other. Recorded because it looks correct, tests clean in isolation, and would
have shipped a lexeme-preserving encoder that preserved nothing.

**2. I began by assuming the fix had to change the digest**, and therefore the
schema, and therefore that #74 was a breaking change to be paid for before the
freeze. It is not. Measuring what our own serializer emits — before choosing a
repair — showed the write side was already correct and the defect lived
entirely in re-serialization on the read side. **The cheap fix was invisible
until the measurement, and the expensive one was fully designed by then.**

## Alternatives rejected

**Commit to the file's bytes in addition to the parsed digest.** Would close it
without any number form at all. Rejected: a trace is appended record by record,
so the bytes are not final until the end, and the JSON ⟷ JSONL equivalence that
T11 and the fixtures pin would break — the same document in two encodings would
carry two roots.

**Normalise non-canonical lexemes on load instead of refusing them.** Turns a
tampered document into a valid one with a different root, silently. A verifier
would then report a mismatch with no way to say why, which is strictly worse
than a refusal that names the lexeme.

**Warn rather than refuse.** The runtime does not have a warning level for
evidence questions, deliberately. A document either earns a root or does not.
