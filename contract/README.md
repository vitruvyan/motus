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

## The five surfaces

A contract is binding exactly where a gate checks it; everywhere else it is
documentation that lies. Each surface therefore names its counterparty and its
enforcement point.

| # | Surface | File | Counterparty | Enforcement point |
|---|---------|------|--------------|-------------------|
| 1 | Trace | `trace.v1.schema.json` | The outside world: databases, auditors, other languages, future versions of ourselves | The TraceSink validates at write, with JSON Schema **format assertion enabled** (draft 2020-12 treats `format` as annotation by default — conformant validators opt in). Dev profile: full validation. Prod profile: schema-checked writer at flush boundaries — under the `synchronous` durability profile every record IS a flush boundary, and that per-record cost is the intended price of that profile, not a hot-path violation |
| 2 | Node | `node-protocol.md` | Whoever writes node code | Record-and-compare: the runtime *captures* actual reads/writes; declarations, when present, are checked against captured reality. Verify-replay (0.6) makes the `pure` claim falsifiable |
| 3 | Graph | `graphspec.v1.schema.json` | The declaration/execution boundary | Static validation at construction. An invalid graph refuses to exist — it does not start-and-warn |
| 4 | Guarantees | `guarantees.md` | Operators and auditors | Executable: the conformance suite in `tests/contract/` and the CI benchmark gate. A release that violates either does not ship |
| 5 | Commitment | `commitment.v1.schema.json`, `checkpoint.v1.schema.json`, `receipt.v1.schema.json` | A third party holding a receipt, who has none of our code running and no reason to trust us | `validate.py`'s C, K and P rules, which **recompute** every digest rather than reading it back. The three schemas travel in the wheel for the same reason `trace.v1` does: a verifier somebody has to clone a repository to obtain is a verifier most of them will not run |

Trace schema family v1 accepts the frozen 1.0 corpus and the additive 1.1
receipt/resume form. `x-current-version` is the single source for the version
emitted by the current package; old evidence remains valid without rewriting.

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
   duplicated. The first Motus runtime commit MUST add
   `tests/test_schema_version.py`, outside both frozen corpora, asserting that
   the trace schema version constant single-sourced in the package equals the
   const in `contract/trace.v1.schema.json`. The frozen corpora predate the
   package and cannot host it.
4. **Amendments leave a record.** A contract change is a PR touching this
   directory plus an ADR stating what changed, why, and what migrates.

### The commitment rules, and why they recompute

`validate.py` re-implements the leaf, node and checkpoint digests instead of
importing them from `vitruvyan_motus.commitments`. That is the point: the
contract is the authority (ADR-001), so a validator that called the
implementation would be checking the implementation against itself and would
agree with any drift. RFC 6962 domain separation is written out byte for byte
in both places, and a positive fixture fails the moment they disagree.

| Rule | What it refuses |
|---|---|
| `C1` | A witness acknowledgement that names a digest other than the leaf of the commitment beside it. An acknowledgement that does not name what it acknowledges is decoration, and it would carry a witnessed receipt's authority |
| `K1` | A checkpoint whose `count` and sequence range disagree. Something was added or removed, and `count` is the half a reader trusts |
| `K2` | Checkpoint 0 linking backwards, or a later checkpoint stating no predecessor. A null link at a non-zero index drops every window before it |
| `P1` | An inclusion path that lands anywhere other than the sealed `window_root` — including a valid path with junk appended, because a path element whose side is neither `left` nor `right` is refused rather than skipped |
| `P2` | A commitment proved against a window it was never in: a sequence outside the checkpoint's range, or a different `(tenant, writer_id)` |
| `P3` | A mode this distribution cannot establish. `QUALIFIED` needs ADR-020 levels 6 and 7, and neither exists here |
| `P4` | An anchor naming a checkpoint digest other than this receipt's |
| `P5` | An anchor on a network this validator cannot evaluate. ADR-020: *a `VERIFIED` on a chain the verifier cannot evaluate is the worst lie this system can tell* |
| `P6` | A chain of segments the receipt asserts by position and by nothing else: a later segment that continues nothing, one that names a predecessor other than the segment before it, or a segment with an `END` that has a successor — a trace that reached a terminal record cannot be resumed |
| `P7` | An `execution.ref` that is malformed, or names a `(tenant, writer_id, sequence)` other than the receipt's original BEGIN |
| `P8` | A completed receipt whose `execution.fingerprint` disagrees with the root derived from the paired trace. An unfinished receipt has no END and legitimately carries `null`. |

### What the verifier will not tell you

`motus-validate receipt <receipt> --trace <trace>` answers all seven ADR-020
levels, including the ones it could not reach. Three of its refusals are the
point of the tool rather than limitations of it:

- **an anchor is a CLAIM.** This validator contacts no network — it is offline
  and stdlib-only by design — so `state: "anchored"` in a receipt is something
  the holder typed. `EXISTENCE` and `RETENTION` are reported as *claimed,
  unchecked*, with the explorer URL, so the reader can settle it against a
  chain we do not operate. ADR-021 decision 8: for `EXISTENCE` it needs the
  chain, and not us;
- **a witness acknowledgement does not establish `EXECUTION_CONTINUITY`.** It
  is bound to the commitment, which is what makes its signature checkable by
  somebody holding the witness's key. This validator holds none, and says so;
- **an unknown hash algorithm, anchor network or signature algorithm ends the
  answer.** Not a downgrade — a refusal, and the CLI exits non-zero, so a
  caller reading only the exit code cannot mistake *I cannot tell* for
  *verified*. **A refusal outranks a violation**: an unknown network is also a
  P5 violation, and reporting it as one says "this document is wrong" about a
  document that may be perfectly correct on a chain we cannot read.

## Fingerprints (canonical form)

Where a schema references a fingerprint, it means: SHA-256 over the canonical
JSON encoding of the object — UTF-8, keys sorted lexicographically, no
insignificant whitespace, strict RFC 8259 (string keys only, no NaN/Infinity,
strings that are Unicode text — see below),
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

**What that means for storage, said here because an integrator's first
question is exactly this one, and it has two halves that point opposite ways.**

**A store may reorder object keys freely.** Integrity digests are computed
over the canonical serialisation, so a trace read back with its keys in a
different order validates. The first external integrator wrote the opposite
into their own notes in three places — *never store a trace as `jsonb`,
Postgres reorders keys and the chain is over bytes* — and found out by testing
it.

**A store may not renumber.** From trace schema 3.0.0, rule `J2` (ADR-024)
requires every numeric lexeme to be what serializing its parsed value
produces. Motus writes `1e-06`; a store that hands back `0.000001` has written
the same *value* and a different *document*, and the validator refuses it —
correctly, because that is the whole point of `J2`. The two halves are not
inconsistent: **whitespace and member order are absorbed by the digest and
numeric lexemes are not**, which is exactly the line ADR-024 draws.

**A Motus string is Unicode text.** Every string in a Motus document — a value
or a member name — MUST denote a sequence of Unicode scalar values. An
unpaired surrogate (U+D800–U+DFFF with no partner) denotes no character, has
no UTF-8 encoding, and conforming JSON implementations disagree about what to
do with one — some refuse it, some substitute U+FFFD, some pass it through. A
document containing one therefore has more than one reading and is refused
(rule `J1`, ADR-026).

**The two sides are scoped differently, and deliberately.** A producer refuses
always: it writes trace schema 3.0.0 and that is where the format is defined. A
READER refuses from trace schema `2.0.0`, because releases up to 0.7.0 wrote
schema 1.x traces carrying such a string and the validators shipped alongside
them called those traces valid. Refusing them now would break the promise one
paragraph up — that evidence written before a rule stays valid without being
rewritten — and this contract ranks a wrong refusal above a missed violation.

**This is not a rule about escape forms.** `"appro\u0076ed"` and `"approved"`
are the same JSON string, share a root, and both stay valid; so do
`"\ud83d\ude00"` and the character it denotes, because a surrogate *pair* is
a character. What is refused is a string that denotes nothing.

A producer carrying bytes that are not Unicode — a filename a filesystem
handed over, a payload from a legacy source — encodes them explicitly:
base64, hex, or bytes with a declared encoding. A runtime's private convention
for smuggling such bytes through a string (Python's `surrogateescape` is the
one this project met) does not travel to a reader in another language, and the
format does not adopt one language's workaround as its meaning.

So: keep the bytes if you want a stored trace to keep validating, or satisfy
yourself that your column type preserves numeric lexemes before you rely on
one that parses them. A `text` column always does.

**And note when the first half was measured.** That test ran against 0.10.0,
before `J2` existed; it was true then and is conditional now. A measured fact
carried past the change that invalidated it is the most convincing kind of
wrong, which is why the version is named here rather than left out.

## Executable fence

The schemas alone cannot enforce the R-rules and T-rules. The fence is
executable and versioned in this repository:

- `contract/validate.py` — the semantic validator: GraphSpec R1–R12; trace
  T1–T13 (record coherence, including replay monotonicity, and from 3.0.0 the
  terminal digest that makes a root anchorable, and from 3.1.0 whether
  violations may be null), E1–E11 (the execution state machine), SB1–SB4
  (spec binding, including recomputed graph fingerprints and from 3.1.0
  whether null matches the declaration),
  H1–H2, J1–J2, JSONL1–3; JSON and JSONL forms. H2 binds
  resume provenance to a distinct run identity. It reads its input as BYTES
  and decodes explicitly — a reader that laundered CRLF into
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
- `commitment.v1.schema.json` — one BEGIN or END as stored in a window file
  under the on-disk envelope key `commitment`, or handed to another party,
  with the witness acknowledgement BESIDE the digested body and never inside
  it. Includes the `continues` link a resumed segment carries (ADR-023).
- `checkpoint.v1.schema.json` — one sealed window: the Merkle root over its
  commitments, the range it covers, and the link to the checkpoint before it.
- `receipt.v1.schema.json` — what a holder presents to a verifier: a RUN, as
  the ordered chain of segments it actually was. ADR-021 decision 7 — a receipt
  carrying only the checkpoint and the transaction proves a checkpoint and not
  a run. Every segment has a BEGIN; only the LAST may have an END, because a
  trace that reached a terminal record cannot be resumed. It travels alone, so
  it declares its own version, and the mode it CLAIMS must be supported by what
  it carries. ADR-027 decision 4 optionally adds `execution`: the BEGIN locator,
  the Trace.root fingerprint (or null for an unfinished execution), and a
  `run_id` carried for embedder correlation, explicitly not as a key.
- `node-protocol.md` — normative obligations of node code (RFC-2119
  language).
- `guarantees.md` — the five invariants, durability profiles, replay
  semantics stated honestly, SLO table, the Terraveler compatibility
  surface, and the inherited Axis 0.4.0 conformance corpus.
