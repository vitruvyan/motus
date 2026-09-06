# ADR-030 — a number in the trace is an integer a browser reads back unchanged

- **Status:** ACCEPTED
- **Date:** 2026-09-06
- **Accepted:** 2026-09-06 by the founder
- **Authority:** CTO proposes; the founder accepts. Closes #116 (P0, pre-freeze, contract) and gives #130 (vectors in Facts) its premise. Records the founder's decision of 2026-08-29 that consumers do not need floats, which 506faec implemented without this document and 0aced05 reverted for that reason.
- **Depends on:** ADR-024 (rule `J2`, a store may not renumber), ADR-026 (the root commits to the JSON value, decision 1: same value → same commitment).
- **Amends:** `contract/trace.v1.schema.json` (`x-current-version` 3.2.0; one rule, `J4`, in the description), `contract/README.md` (the storage section gains the third thing a store does), `contract/node-protocol.md` §3 (what a value may be). ADR-024 and ADR-026 are not amended: this rule sits beside them.

## Context

Take a clean trace written by v0.12.0, pass it through `JSON.parse` and `JSON.stringify` in Node 20, validate it: **T11, payload_hash does not match the record**, the tampering finding — and what changed is `-14.0` → `-14` in one Fact. RFC 8259 has one number type; `-14` and `-14.0` are the same JSON number; Motus gave them different commitments because its canonical form runs through CPython's int/float distinction and `repr(float)`. ADR-026 decision 1 promises *same value → same commitment* and this case breaks it, in the direction that accuses a browser of tampering.

Measured (0aced05, kept because it was measurement and not code): across 12 030 values written by `json.dumps` and by Node's `JSON.stringify` on identical bit patterns, the two disagree exactly on **integral floats, floats outside [1e-4, 1e16), and integers beyond 2^53**; the boundary witness for the integer case is `2^53 + 1`, not `2^53`. Nothing else disagreed.

Measured on the consumers: Orbis found **10 of 90** real traces unverifiable from Node, all through three float fields (`confidence`, `intent_confidence`, `primary_elapsed_ms`), and has already decided to carry `confidence` in basis points (orbis#63). Limen's decision and effect runs carry no float. The founder's decision of 2026-08-29: **consumers do not need floats**.

What went wrong the first time, so it is not repeated: 506faec refused non-integers in `_value`, which every reader also goes through, so `demo/out/domains/cold_chain_release.json` — a published, calendar-submitted artefact carrying `-14.0` — loaded, validated, and was then refused by `TraceBundle`, `playback`, `verify`, `explain` and `to_html`. Its test built a document with `"records": []` and never reached the constructor. The rule was sound; the site was wrong and the test was written against the fix.

## Decision

1. **From trace schema 3.2.0, every number in a trace is a JSON integer with |n| ≤ 2^53 − 1.** This covers every position T11 hashes: Fact, Decision and Rejection values at any depth, `initial_state`, metadata, routing values, and the recorded context draws (decision 5). Floats, integral floats, and integers beyond the safe range are not admitted. A quantity that is not an integer is carried as an integer at a declared scale (basis points, milliseconds, micro-units) or as a string the producer owns; the contract does not choose the scale, the producer's declaration does (`reads_declared`/`writes_declared` say the key; the vertical's contract says the unit).
2. **Refused at the producing boundary, never at read.** `State.with_fact`, `with_decision`, `with_rejection`, the runtime's metadata seeding and the CLI's document parser refuse a non-conforming number with rule id `J4` and the path, before anything is written. Readers — `State.from_snapshot`, `_replay_commit`, `Trace.from_json`, the validator — accept every document under the recipe of its own version, so 0.12.0's `-14.0` loads, replays and verifies as it always did.
3. **Scoped by version, not by flag.** The validator applies `J4` to documents at 3.2.0 and above; below, it says nothing, because those documents were truthful under their rule. The producer writes 3.2.0 from the release that carries this ADR.
4. **The canonical form of the past is named, not fixed.** For documents below 3.2.0 the canonical number form remains CPython's, and `contract/README.md` says so as a requirement on a verifier written in another language, with the three disagreement classes listed. That is option 3 of #116 for the evidence already written, and it is the honest sentence: nobody can re-derive those roots without replicating `repr(float)`.
5. **Mediated randomness is recorded as the integer it came from.** `ctx.rand()` is the contract's own second numeric source: today `_RunController._node_rand` records each draw as a float in `context_draws[].value` (`context.py:273-280`), which `J4` would refuse — found by review of this ADR, not by its author. From 3.2.0 the record carries the 53-bit integer `n` the draw is made from, and the value handed to the node is `n / 2^53`, exactly the construction of CPython's `random.random()` and exact in binary64; replay reads `n` and reproduces the same float on every platform, which the float itself did not guarantee. The same applies to any future mediated source: what the trace records is the integer, what the node receives is derived from it by a formula the contract states.
6. **A verifier in another language is now possible for 3.2.0 documents without replicating CPython**, which is the property the freeze promises: integers within 2^53 serialise identically in every JSON implementation in use.

## Consequences

- Orbis: `confidence` and its two siblings become integers at the pin bump — the declared migration orbis#63 already scheduled — and the shadow tree gains 10 of 90 traces verifiable from Node. Limen: nothing changes. The demo: the cold-chain scenario writes `-14` at scale 1 (degrees) or `-140` at scale 10, and says which; the frozen `demo/out` stays as written.
- **What is given up:** a producer with a real number has to choose a scale, and a badly chosen one loses precision silently. That is the producer's contract to declare, and #130 (vectors, 82 % of a consumer's numbers) is the case where "an integer" is not the answer at all: a vector is carried by reference to bytes, not as a JSON array, and that ADR follows this one.
- Nothing about roots already anchored moves: the rule applies to documents not yet written.

## Alternatives rejected

- **Option 2 of #116 — a Python-independent canonical form for floats** (ECMA-262 `Number::toString`, or a specified shortest-round-trip). It would make "check it with your own tools" true for floats too, at the price of specifying and testing a float printer in the contract and requiring CPython to adapt. Rejected for now because the consumers do not need it; recorded as the door to reopen if a consumer ever does, and it must be reopened by ADR, before 1.0.0 or as 2.0.0.
- **Option 3 alone — declare the format Python-canonical.** True of the past, and this ADR says so, but as the rule for the future it makes every second implementation a CPython emulator, which is the opposite of a freeze.
- **Refusing in `_value` for readers too** (506faec). Refused by a round for breaking published evidence; the test that proves this ADR's implementation must load the cold-chain artefact through every consumer path.

## Hypothesis, and how it fails

**H1 — no consumer needs a non-integer number in a trace value.** Measured true on Orbis (three fields, all scalable) and Limen (none). It fails the day a producer has a value with no sane integer scale and no string form; then option 2 is reopened. The implementation adds a test that greps the two consumers' pinned code for float writes into Facts, so the migration is checked, not assumed.

**H2 — the safe-integer bound is 2^53 − 1 on both sides.** Falsified by any JSON implementation in use that loses integers below that bound; none is known, and the reverted commit's table records `2^53 + 1` as the first disagreement.
