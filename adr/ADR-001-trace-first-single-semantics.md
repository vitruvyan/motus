# ADR-001 — Trace-first, single semantics: the Motus foundation

| | |
|---|---|
| Status | PROPOSED — awaiting founder approval |
| Date | 2026-08-03 |
| Deciders | Founder (final authority) · Codex (convergence + GitHub/CI implementation) · Claude (contract draft + independent review) |
| Source documents | *Vitruvyan Motus — fondazione v1.1* · Axis Vision 2026 independent review · Phase-A Terraveler audit · kernel microbenchmark (raw JSON in `benchmarks/` once ported) |

## Context

Axis v0.4.0 is a trace-first ordered workflow kernel with two production
consumers (Terraveler ingest and RAG chat) and a measured performance profile.
The evidence that shaped this decision:

- **Speed is not the problem.** Runner overhead for a 100-node graph is
  0.65 ms (median across 5 runs) — the once-proposed 5 ms target is already
  beaten ~8× by the interpreter. Where time actually goes: ~60%
  immutable-state derivation machinery, an O(n²) events-tuple accumulation
  (25% of total at n=1000), and a serialization path (`to_dict`) costing
  most of the run it describes, at 4.7× pure `json.dumps`. Numbers are the
  multi-run baseline in `benchmarks/`; see guarantees.md §3 for why the
  asserted statistic is the min-of-samples across runs.
- **The differentiated ground is causal.** Primary-doc verification of the
  landscape: LangGraph's caching is keyed memoization, not an effect log,
  and its time-travel re-fires LLM calls on replay; Temporal, DBOS and
  Restate already own durable replay mechanics; none of the surveyed systems
  documents per-value provenance, why-run/why-not explanations, recorded
  rejected alternatives, or causal counterfactual reports.
- **Contracts bind only where a gate checks them.** Terraveler's wire
  contracts hold because its dispatcher dead-letters violations;
  vitruvyan-core's node contracts run in `warn` by accident and its channel
  registries are enforced by nothing.
- **Two behaviors are production-relied-upon today** and constrain any
  refactor: `NodeFailed.state` failure salvage (three call sites) and the
  legacy `to_dict()` trace shape persisted in two Terraveler jsonb tables.

## Decision

1. **Identity.** The runtime is **Vitruvyan Motus**: an autonomous,
   embeddable, trace-first graph runtime, semantically neutral. Distribution
   `vitruvyan-motus`, package `vitruvyan_motus`. "Axis" remains the
   historical name of pre-rebranding versions and audits.
2. **Repository continuity.** In-place rename `vitruvyan/axis` →
   `vitruvyan/motus`. No greenfield repository. Commits, tags, branches,
   issues and PRs stay in one history. The old name is never reused for a
   new repository. The layout migrates to the approved flat form
   (`src/vitruvyan_motus/` — **ten files**, one responsibility each: the
   prototype's nine plus `compat.py`, approved by the cross-review on the
   condition that it contains exclusively legacy adapters and types — the
   compatibility view, `LegacyDecision`, the legacy `to_dict` shape — and
   no new runtime semantics) **without a semantic greenfield rewrite**:
   earned v0.4.0 behaviors enter `tests/contract/` as the inherited
   conformance corpus (guarantees.md §5) before the runtime moves. The
   public import paths are unambiguous by design and Terraveler receives no
   silent alias — its migration off the Axis 0.4.0 wheel changes import
   paths explicitly:

   ```python
   from vitruvyan_motus import Decision              # Motus-native type
   from vitruvyan_motus.compat import LegacyDecision # Axis compatibility
   ```
3. **The contract precedes the code.** The four surfaces in `contract/`
   (trace, node, graph, guarantees) plus this ADR are approved before any
   runtime change. Contract tests and Terraveler golden tests are frozen and
   are not editable by the implementing agent.
4. **One execution semantics.** No interpreted/compiled dual path. Physical
   optimizations pass through the one executor and must preserve the
   observable trace. A compiled path may be reconsidered only when a real
   consumer's measured scheduler share exceeds ~20%.
5. **The five invariants** of *fondazione v1.1* §3 are adopted verbatim into
   `contract/guarantees.md` §1 and are non-negotiable.
6. **Motus 0.5 scope** is exactly *fondazione v1.1* §4: the eight
   retrofit-hostile foundations in; the ten named exclusions out.

## Dispositions of predecessors

| Artifact | Disposition |
|---|---|
| `feat/run-metadata` | **Merged into main post-0.4.0 (PR #11).** Its substance — the run's `metadata` map (actor, causation_id, correlation_id, carta_version) — is absorbed by design into trace schema v1's run header. On main, `GraphState.metadata` also added an optional `metadata` key to `to_dict()`; the compatibility pin in guarantees.md §4 accounts for it explicitly (v0.4.0 keys + optional `metadata`). |
| `feat/order-spec` | **Merged into main post-0.4.0 (PR #12), then superseded by the boundary decision**: Orders are not Motus modules. `axis/orders.py` (`OrderSpec`, `Order`, `Channels`) leaves the Motus public surface in 0.5 and transplants to Vitruvyan OS; the `Channels(produces, consumes)` idea must survive wherever OrderSpec lands. |
| Axis v0.4.0 wheel | **Remains Terraveler's pin** until Motus 0.5 passes every frozen golden test; migration is then Terraveler's decision, with the legacy `to_dict` shape preserved by the compatibility view either way. Note: Terraveler's stored traces were written by the v0.4.0 wheel and have no `metadata` key — the golden tests pin that reality per guarantees.md §4. |
| Old branches after rename | No automatic merges. Anything needed is cherry-picked under review. |
| `orders/` stubs, epistemic types/protocols, `SynapticBus` | Leave the public surface in 0.5: stubs deleted; epistemic modules un-exported and moved toward Vitruvyan OS; the bus dissolved into `TraceSink` / `Listener` / `StreamDriver`. |

## First-commit conditions

- `py.typed` ships in the package from the first commit.
- The trace schema version exists in exactly one place in the package and a
  contract test asserts it equals `contract/trace.v1.schema.json`.
- No PyPI publication before the license decision and the name-migration
  plan are approved.
- Local remotes, CI references and documentation URLs are updated explicitly
  after the GitHub rename; no consumer is left depending on the redirect.
- The test environment is declared and pinned twice over: the `[test]`
  extra in pyproject declares intent (ranges), and `constraints/test.txt`
  records the versions actually resolved on the reference profile — CI
  installs with `pip install -e .[test] -c constraints/test.txt`.
  Rationale: a bare environment shows 29 async-plugin failures that are
  pure noise; in the pinned environment the suite is 0-failure, so "the
  suite passes" finally means something. Regenerating the constraints file
  is a deliberate, committed act.

## Review round record

- 2026-08-03, round 1 (Claude adversarial pass, pre-commit): 13 must-fix,
  applied in `6e95eac`.
- 2026-08-03, round 2 (Codex cross-review `MOTUS_FOUNDATION_CONTRACT_
  CROSS_REVIEW_V1.md`, verdict REQUEST CHANGES): MF-01…MF-18 applied, six
  open choices dispositioned as above, OPEN-07/OPEN-08 drafted for the
  founder, executable fence (validate.py + fixtures + tests) added to the
  branch.
- 2026-08-03, round 3 (independent adversarial attack on the executable
  fence, verdict FIX-THEN-GREEN): 7 must-fix — T6 converse arm for
  `active_attempt`; lifecycle exclusivity (single run_started, nothing
  after a terminal record); `routing.written_at` causally validated; T8
  outcome↔condition↔value correlation; H1 requires both sink disclosure
  keys; T9 calendar-valid timestamps (FormatChecker alone was vacuous);
  J1 RFC 8259 non-finite refusal in validator and CLI — plus 5 nice
  (failed_node in T5, R12 `===` relaxation, fixture rule-purity asserted,
  stale `status` vocabulary, JSONL blank-line tolerance documented). All
  applied. Codex's three ratifications (OPEN-07 SCC form, OPEN-08 sink
  limitation, MF-17 naming) folded in the same pass.
- 2026-08-03, round 4 (Codex cross-review v2 on `ad6e16e`, verdict REQUEST
  CHANGES): the decisive finding — the trace was validated as records, not
  as a graph execution. Applied: the **E-rules** (E1–E11, the normative
  execution state machine: entry-first, routing drives the next attempt,
  retry/abort/continue have mandatory successors, strict miss must fail as
  route_miss, exploration miss completes with the miss on record, attempt
  numbering per activation, failure-cause admissibility) and the
  **SB-rules** (SB1–SB4, spec binding: header identity, RECOMPUTED graph
  fingerprint — every fixture fingerprint is now true, the round-3 happy
  path carried a false one — effect_class correlation, violations
  truthfulness); T4 gained key↔origin binding and the `absent` origin its
  surface discriminator; T8 gained R10 staleness; J1 became recursive
  (nested non-JSON Python values, non-string keys, tuples); metadata values
  route through Value (redaction representable, forgery rejected); intent/
  messages/descriptions/reasons are declared plain-text non-redactable
  surfaces by decision (MF2-06 narrow option); R12 rejects wildcard+local;
  JSONL is LF-only with CR bytes rejected (JSONL3); `benchmarks/` now
  carries the measured baseline; and the three remaining gates
  (tests/contract/, tests/compat/terraveler/, the CI benchmark job) are
  honestly marked REQUIRED BEFORE IMPLEMENTATION rather than claimed
  present. Delivered state: 84 fixtures (11 positive, 73 negative, each
  violating exactly one rule), 39 distinct rules — every advertised rule
  carries a negative fixture, pinned by a coverage test — 96 contract
  tests, full suite 217 passed / 14 skipped / 0 failed in the pinned
  environment, all 22 reproduced cross-review attacks caught, zero changes
  under `axis/` or `orders/` against `origin/main`.
- 2026-08-03, round 5 (Codex cross-review v3 on `6a2dc4d`, verdict REQUEST
  CHANGES): seven fresh probes still passed. All closed. **T8 miss legality**
  — a miss must have been *possible*: a value that is a declared map key was
  matched, an unmapped value on a route declaring a default was defaulted,
  and every routed outcome (miss included) owes the same causal duty for
  `written_at`, with a null cause admissible only when no earlier committed
  transition ever decided the key. **T10, new rule** — replay capability only
  degrades: the terminal record may be equal to or weaker than the header's
  declaration, never stronger, and a constraint that justified a limitation
  cannot vanish. **SB4 now covers every captured read** — absent, scan and
  header origins are no longer exempt, since a declaration covering three
  quarters of the reads is not a declaration. **The effect lattice** —
  a descriptor may not exceed its node's class (schema-enforced). **The CLI
  reads bytes** — universal-newline decoding laundered CRLF into LF, so the
  API refused what the command line accepted. **The benchmark gained its
  normative warmups**, and collecting it honestly produced a finding of its
  own: on the reference profile the in-run *median* varies 27% run to run
  with the guest's load flat, so the SLO statistic is now the min-of-samples
  taken as the median across ≥5 runs (measured spread ≤ 11%), tolerance
  ±25%, with the noisy median published beside it — a method rule changed by
  measurement rather than by preference, and flagged as such. Delivered
  state: 95 fixtures (13 positive, 82 negative), 40 rules all covered, 108
  contract tests, full suite in the pinned environment, 7/7 fresh probes and
  22/22 v2 attacks rejected, `axis/` and `orders/` still untouched.
- 2026-08-03, round 6 (Codex cross-review v4 on `0dbd85f`, verdict REQUEST
  CHANGES): three probes on routing provenance, plus one enforcement gap.
  Two were bugs — the causal value comparison ran only for strings, so two
  different numbers could sit at the ends of one causal edge; and the
  "a default exists, so a miss is impossible" rule ignored value type,
  leaving a non-string value with **no legal outcome at all** against
  normative R9. The third was a design hole: a null causal edge proved only
  that nothing had written the key, so a routing could report a value that
  appears nowhere in the run. Codex offered a minimal patch or the cleaner
  model; **the cleaner model was taken**, because the minimal one leaves two
  provenance mechanisms in one schema and walls off a legitimate case: the
  scalar `written_at` could not name a SEEDED decision, so a resumed run
  could never say where its routing value came from. `written_at` is
  therefore replaced by a structured `origin` — `{transition, seq, index}`,
  `{initial, index}` or `{absent}` — symmetric with the read origins adopted
  in round 4, addressing the exact Decision rather than the record holding
  it. Value equality is now JSON-typed (the boolean `true` is not the number
  `1`, whatever Python says), `absent` must be true of the whole run and
  admits only a null value, R10 staleness covers seeded origins, and R9's
  never-coerced rule is honored: a non-string is a miss with or without a
  declared default. `collect_baseline.py` now refuses fewer than five runs
  before executing. Delivered state: 105 fixtures (16 positive, 89
  negative), 108 contract tests, suite 239 passed / 14 skipped / 0 failed in
  the pinned environment, 3/3 v4 probes, 7/7 v3 probes and 22/22 v2 attacks
  correct, `axis/` and `orders/` untouched.
- 2026-08-03, round 7 (Codex cross-review v5 on `8039582`, verdict REQUEST
  CHANGES): one finding, and a precise one. R10 defines recency as
  *last-in-array*, but staleness was computed only ACROSS sources — a later
  committed transition superseding the origin — never WITHIN the array the
  origin names. So an origin could point at index 0 of a record whose index 1
  had already superseded the same key, in a committed transition or in the
  seeded state alike, and validate clean. Exact addressability is not the
  same as being current. Closed in both directions, and deliberately
  position-based rather than payload-based: a duplicate carrying an identical
  value still supersedes, because the origin promises the exact Decision
  observed and not an equivalent one. Also applied: a test now pins the
  baseline collector's five-run floor (the executable-fence principle reaches
  the collector too), and the schema banner is refreshed. Delivered state:
  110 fixtures (18 positive, 92 negative), 124 contract tests, suite 245
  passed / 14 skipped / 0 failed in the pinned environment, 2/2 v5 probes,
  3/3 v4, 7/7 v3, 22/22 v2 and the round-3 corpus all correct, `axis/` and
  `orders/` untouched.
  ADR-001 moves to ACCEPTED only after Codex independently reproduces the
  new SHA and the founder signs.

*Reporting note:* the round-6 handoff cited 108 contract tests when the
committed number was 118 — a stale figure carried from round 5, not a code
defect, caught by the cross-review. Counts in this record are taken from the
run that produced the commit.

## Open choices — amended per the Codex cross-review (2026-08-03)

Each choice now carries Codex's disposition and the amendment applied.

1. **Trace document forms** — *AMENDED as approved*: JSON document and JSONL
   are two encodings of ONE logical model. `TraceHeader` is now a published
   `$defs` schema (JSONL line 1); UTF-8/LF/no-BOM and the equivalence rule
   are normative; T-rules run streaming to EOF, with truncation = incomplete;
   integrity hashes (when active) are over canonical object form, never
   encoding bytes; fixtures pin JSON ↔ JSONL equivalence.
2. **Routing records separate from transitions** — *APPROVED with amendment,
   applied*: candidates now carry their condition (map key / default /
   static), and T8 fixes the correlations (matched/default: exactly one
   taken candidate equal to selected; miss: zero taken, selected END; the
   candidate list is the complete route step, each entry once).
3. **Per-value causal edges** — *AMENDED as approved*: the scalar
   `written_at` is replaced by a structured `origin`
   (initial/transition/header/scan/absent, with collection + index), which
   actually identifies the value read — including read-misses and the
   closed, enumerated readable surface (node-protocol §3.1a).
4. **Attempt model** — *Codex REJECTED `skipped`-as-status; accepted and
   applied*: the trace now separates attempt `outcome`
   (returned/raised/cancelled) from runner `disposition`
   (commit/retry/abort/continue), with the transactional rule for writes
   (raised/cancelled attempts commit nothing — schema-enforced) and
   `attempt_started` records making every attempt's lifecycle — including
   hard cancellation — evidence instead of absence (replaces the old T6).
5. **Redacted values** — *intent approved, wording amended*: now "reserved
   and runtime-produced", not "unforgeable"; the schema makes `Json` and
   `RedactedValue` disjoint (the reserved discriminator cannot be
   hand-forged into a valid plain value).
6. **Additive v1.x extensions** — *approved with version gate, applied*:
   schema 1.0 accepts ONLY null in the integrity fields (`const: null`);
   1.1 activates hashes together with the algorithm, chain validator and an
   unambiguous activation indicator. No future semantics are silently
   accepted by 1.0.

## Decisions for the founder (from the cross-review)

- **OPEN-07 — cycles and termination.** APPROVED by the cross-review
  (2026-08-03) with amendments, all applied to graphspec R11: the
  reachability requirement is stated in SCC form (every strongly connected
  component terminal-reachable); `max_transitions` is explicitly part of
  the graph fingerprint (changing the limit changes the graph); the cause
  kind is `transition_limit_exceeded`. Cycles legal, static termination not
  guaranteed, safety valve not scheduler. **Founder signature pending
  independent reproduction of the final SHA.**
- **OPEN-08 — non-node failure causes.** APPROVED by the cross-review
  (2026-08-03) with one amendment, applied: `run_failed.cause` is structured
  (`node_failure | route_miss | sink_failure | validation_failure |
  transition_limit_exceeded | runner_internal`, message, record_seq), with
  `failed_node` correlated by T7 — and the **sink-failure persistence
  limitation is explicit** (schema cause description + guarantees §6): when
  the failing component is the required sink itself, the `run_failed` record
  is best-effort; the logical failure toward the caller stays guaranteed.
  **Founder signature pending independent reproduction.**
- **MF-17 — vocabulary neutrality.** APPROVED by the cross-review
  (2026-08-03), applied: `Fact`/`Decision`/`Rejection` are neutral workflow
  primitives — a Fact is a *recorded assertion, never verified truth*;
  `ruleset_version` is the neutral standard key and `carta_version` is
  consumer metadata; and legacy/native decisions have **no implicit mapping
  and no ambiguous public names** (`vitruvyan_motus.Decision` native,
  `vitruvyan_motus.compat.LegacyDecision` legacy — Terraveler migrates by
  explicit import change, never by silent alias). **Founder signature
  pending independent reproduction.**

## Consequences

- Implementation can be delegated safely: the fence (contract + frozen
  tests + CI gates) turns scope drift into a failing test.
- The compatibility view carries a real cost (legacy shape maintained
  alongside schema v1) — accepted until Terraveler migrates by decision.
- The O(n²) accumulation and the 4.1× serialization tax are fixed as a
  *consequence* of the state/log separation demanded by the contract, not as
  ad-hoc optimizations — and the SLO table holds the result in CI.
- Everything excluded from 0.5 has a named trigger or a named horizon;
  nothing is rejected silently.
