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
  0.65–0.80 ms — the once-proposed 5 ms target is already beaten 6–8× by the
  interpreter. Where time actually goes: ~60% immutable-state derivation
  machinery, an O(n²) events-tuple accumulation (23% of total at n=1000),
  and a serialization path (`to_dict`) costing 75% of the run it describes
  at 4.1× pure `json.dumps`.
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
  limitation, MF-17 naming) folded in the same pass. ADR-001 moves to
  ACCEPTED only after Codex independently reproduces the final SHA and the
  founder signs.

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
