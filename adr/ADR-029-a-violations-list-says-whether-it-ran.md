# ADR-029 — a violations list says whether it ran

- **Status:** PROPOSED
- **Date:** 2026-09-05
- **Accepted:** —
- **Authority:** CTO proposes; the founder accepts. Closes #125 (P0, pre-freeze, contract), reported by the Orbis integration as the one item on their list that expires with the freeze.
- **Depends on:** ADR-001 (record-and-compare), `node-protocol.md` §3 (declarations are optional and checked against captured reality).
- **Amends:** `contract/trace.v1.schema.json` — `Transition.violations` admits `null` from trace schema **3.1.0**; rule `SB4` gains one clause; `TRACE_SCHEMA_VERSION` becomes `3.1.0`. Nothing else in the contract changes.

## Context

A transition record written by a node with a complete `reads_declared` and one written by a node with no declaration at all are byte-identical: the same reads, and `violations: []` in both. `_declaration_violations` (`runtime.py:1206`) appends only when `declaration.reads_declared is not None` or `writes_declared is not None`, and returns `[]` otherwise. So the field cannot distinguish *checked and clean* from *never checked*.

`violations: []` is load-bearing negative evidence. It is the sentence the product sells — *a node cannot lie its way past the trace* — and a reviewer reads the record, not the bundle. The information is recoverable from the GraphSpec bound by `graph_fingerprint`, and #124 showed that binding to be weaker than it looked, so "go and read the spec" is a worse answer than it was.

Measured by the reporter, and worth keeping: 13 nodes declared by hand covered **31 of 63** read keys, 49 %, and the omission was a category, not a scatter — `now` was missing from 13 of 13, and with it every header read and every miss. The honest path for an integrator is *generate the declaration from a listener, then review*; the documentation implies *write, then measure*. That is #127 and is not decided here.

## Decision

1. **`violations` is `null` when nothing was declared.** A transition of a node whose spec entry carries neither `reads_declared` nor `writes_declared` records `violations: null`. `[]` means a declaration existed and captured reality matched it; a non-empty list means these are the mismatches. One field, one meaning; no `declared` flag, because the spec already says who declared and two sources for one fact is the class this repository fights hardest.
2. **The rule is checkable.** `SB4` (spec supplied) gains: *`violations` is `null` iff the spec node declares nothing; a `null` under a declaration, or a list under none, is a violation*. Without a spec the validator cannot check it and says nothing, as for the rest of SB4.
3. **Scoped by version, not by flag.** `violations: null` is admitted from trace schema 3.1.0. Documents at 3.0.0 and below keep `[]` and are judged under their own recipe, as ADR-019 and ADR-026 already do for their rules: a 3.0.0 trace is a truthful record of a run under 3.0.0 semantics. The producer writes 3.1.0 from the release that carries this ADR.
4. **The half-declared node stays ambiguous, and this ADR says so.** A node that declares reads and not writes, or the reverse, records the list for the declared half; an empty list there does not say which half was checked. Splitting the field into `{reads, writes}` would resolve it at the price of changing the shape of a required field for every reader; the founder may prefer that before 1.0.0, and the freeze ADR must decide it explicitly rather than inherit this one's choice. The GraphSpec remains the source for the half.

## Consequences

- Every consumer that parses `violations` as a list must accept `null`. Orbis asked for it; Limen's decision traces declare on every node and are unaffected.
- Frozen corpora are untouched: every fixture is 3.0.0 or older and carries `[]`. New fixtures at 3.1.0 pin the three values.
- **What is given up:** a reader of a 3.0.0 trace still cannot tell the two cases apart, and the ADR does not pretend otherwise; the fix is forward-only, which is the rule for evidence already written.

## Alternatives rejected

- **`declared: true/false` beside the list.** A second source for a fact the spec owns; the first time they disagree, the record is wrong twice.
- **Omitting the field when undeclared.** `violations` is `required`; making it optional lets a writer that forgot it pass as one that declared nothing, which is the ambiguity moved, not removed.
- **Recomputing from the spec at read time.** The reviewer reads the record; the spec may not be at hand; and #124 measured the binding as weaker than assumed.

## Hypothesis, and how it fails

**H1 — no shipped consumer relies on `violations` being a list without checking.** Falsification: grep Orbis and Limen for `violations` before the pin bump; a consumer that does `len(record["violations"])` unguarded breaks on the first 3.1.0 trace. Found: the bump is a declared migration (orbis#63 pattern); not found: the migration note is still written, because a consumer we do not see is the one this rule is for.
