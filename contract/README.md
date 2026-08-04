# The Motus Contract

**Status: v1 — accepted by ADR-001 on 2026-08-03.**

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
insignificant whitespace, strict RFC 8259 (string keys only, no NaN/Infinity),
integers only in fingerprinted numeric positions (floats forbidden there
precisely because their canonical representation is not settled until the
integrity schema 1.1 fixes it) — prefixed with the fingerprint kind, e.g.
`graph:sha256:<hex>`. `graph_fingerprint` is computed over the canonical
encoding of the ENTIRE GraphSpec document as validated (no materialized
defaults); the validator recomputes it (SB2) — every fixture fingerprint is
TRUE, never decorative. Hashes are computed over the canonical object form,
never over the bytes of a particular encoding (JSON vs JSONL).
`code_fingerprint` is computed over the ordered node identity list —
declared name, qualified name, source hash, config fingerprint — per the
exact recipe in `node-protocol.md` §6.3.

## Executable fence

The schemas alone cannot enforce the R-rules and T-rules. The fence is
executable and versioned in this repository:

- `contract/validate.py` — the semantic validator: GraphSpec R1–R12; trace
  T1–T10 (record coherence, including replay monotonicity), E1–E11 (the
  execution state machine), SB1–SB4 (spec binding, including recomputed
  graph fingerprints), H1, J1, JSONL1–3; JSON and JSONL forms. It reads its
  input as BYTES and decodes explicitly — a reader that laundered CRLF into
  LF would judge a document the file does not contain.
- `contract/fixtures/` — versioned positive and negative instances; every
  negative declares the rule it violates, and the contract tests assert it
  fails for that reason and no other.
- `tests/test_contract_fixtures.py` — runs metaschema, schema, and semantic
  validation over all fixtures.

A contract change that does not update fixtures alongside it is incomplete
by definition.

## Gates required before implementation — status

Surface 4's full enforcement names three further gates. All three are now in
the tree and executable; implementation remains subordinate to them.

| Gate | Status |
|---|---|
| `tests/contract/` — the inherited Axis 0.4.0 conformance corpus (guarantees.md §5), ported without weakening | **present** |
| `tests/compat/terraveler/` — the frozen Terraveler corpus (guarantees.md §4), golden included | **present** |
| the CI job asserting guarantees.md §3 against the baseline in `benchmarks/` | **present** — `.github/workflows/ci.yml`, job `slo-baseline` |

### The frozen corpora, and the one file that may move

`tests/contract/` and `tests/compat/` state what the runtime must do. The
implementing agent may not edit them: a runtime that cannot satisfy them is
wrong, and a corpus that bends to the implementation proves nothing.

They do have to name a package, and that name changes exactly once — when the
runtime moves from `axis` to `vitruvyan_motus`. That rename touches
`tests/contract/kernel.py` and nothing else; it is the single editable file in
either directory, and it resolves to the **compatibility view**, not the
Motus-native surface. If a name it exports cannot be provided, that is an
amendment with its own ADR.

The golden in `tests/compat/terraveler/golden/` is a real row lifted out of
`ingestion_runs` — six top-level keys, no `metadata`, naive timestamps inside
its events. It is evidence precisely because nobody wrote it for a test.

Mechanical protection is provided by `.github/workflows/frozen-contract.yml`
and `tools/check_frozen_paths.py`. The workflow runs the checker from the
trusted base revision under `pull_request_target`: code in a pull request
cannot weaken the check that judges that same pull request. The sole exception
is `tests/contract/kernel.py`, exactly as described above.

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
