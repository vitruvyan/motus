# The Motus Contract

**Status: DRAFT v1 — for three-party convergence (founder, Codex, Claude). Nothing here binds until ADR-001 is approved.**

This directory is the founding, binding basis of Vitruvyan Motus. The runtime
is implemented *inside* this contract, never the other way around: a change of
behavior that contradicts a file in this directory is a bug in the runtime, and
a needed change of contract is a versioned amendment here first — with its own
ADR — before any code moves.

Source documents: *Vitruvyan Motus — fondazione v1.1* (2026-08-03, founder-approved
direction), the Axis Vision 2026 independent review, and the Phase-A Terraveler
audit. Where this draft makes a choice those documents left open, the choice is
marked `OPEN:` and listed in ADR-001 for explicit approval.

## The four surfaces

A contract is binding exactly where a gate checks it; everywhere else it is
documentation that lies. Each surface therefore names its counterparty and its
enforcement point.

| # | Surface | File | Counterparty | Enforcement point |
|---|---------|------|--------------|-------------------|
| 1 | Trace | `trace.v1.schema.json` | The outside world: databases, auditors, other languages, future versions of ourselves | The TraceSink validates at write, with JSON Schema **format assertion enabled** (draft 2020-12 treats `format` as annotation by default — conformant validators opt in). Dev profile: full validation. Prod profile: schema-checked writer at flush boundaries — under the `synchronous` durability profile every record IS a flush boundary, and that per-record cost is the intended price of that profile, not a hot-path violation |
| 2 | Node | `node-protocol.md` | Whoever writes node code | Record-and-compare: the runtime *captures* actual reads/writes; declarations, when present, are checked against captured reality. Verify-replay (0.6) makes the `pure` claim falsifiable |
| 3 | Graph | `graphspec.v1.schema.json` | The declaration/execution boundary | Static validation at construction. An invalid graph refuses to exist — it does not start-and-warn |
| 4 | Guarantees | `guarantees.md` | Operators and auditors | Executable: the conformance suite in `tests/contract/` and the CI benchmark gate. A release that violates either does not ship |

## Rules the contract imposes on itself

1. **Never in the hot path.** Validation lives at boundaries — graph
   construction, trace flush, effect ingress. The measured cost of per-event
   defensive validation in the predecessor kernel was 4.1× the pure
   serialization; that mistake is not repeated.
2. **Versioned, not eternal.** Every schema carries `schema_version`.
   A breaking change to any surface is a major version of the runtime.
   Consumers pin the contract version they built against; the trace they
   persist names the version that produced it.
3. **One source per fact.** No schema, version string, or invariant is
   duplicated. `tests/contract/` includes a test that the schema version
   constant in the package equals the one in this directory.
4. **Amendments leave a record.** A contract change is a PR touching this
   directory plus an ADR stating what changed, why, and what migrates.

## Fingerprints (canonical form)

Where a schema references a fingerprint, it means: SHA-256 over the canonical
JSON encoding of the object — UTF-8, keys sorted lexicographically, no
insignificant whitespace, floats forbidden in fingerprinted positions —
prefixed with the fingerprint kind, e.g. `graph:sha256:<hex>`.
`code_fingerprint` is computed over the ordered tuple of the nodes' qualified
names and source hashes; its exact recipe is fixed in `node-protocol.md` §6.

## What lives where

- `trace.v1.schema.json` — the trace document: run header, transition
  records, routing records, lifecycle records, redacted values, reserved
  integrity fields.
- `graphspec.v1.schema.json` — topology as data: nodes, entry, transitions
  (linear, routed, terminal), effect classes, optional declared read/write
  sets.
- `node-protocol.md` — normative obligations of node code (RFC-2119
  language).
- `guarantees.md` — the five invariants, durability profiles, replay
  semantics stated honestly, SLO table, the Terraveler compatibility
  surface, and the inherited Axis 0.4.0 conformance corpus.
