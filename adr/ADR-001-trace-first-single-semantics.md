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
   (`src/vitruvyan_motus/` — nine files, one responsibility each) **without a
   semantic greenfield rewrite**: earned v0.4.0 behaviors enter
   `tests/contract/` as the inherited conformance corpus (guarantees.md §5)
   before the runtime moves.
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

## Open choices this draft makes (approve or amend)

1. **Trace document forms**: single JSON document *and* JSONL (header line +
   record lines) are both canonical. (Chosen for streaming sinks; reject if
   one canonical form is preferred.)
2. **Routing records are separate from transition records** (`kind: routing`),
   carrying taken *and* not-taken candidates. (Chosen so why-not is
   structural; the alternative — routing embedded in transitions — was
   rejected as conflating node work with runner dispatch.)
3. **Reads carry `written_at` seq references** (per-value causal edges) rather
   than bare key lists. (This is the why-value/first-divergence enabler;
   it is the single most important schema choice in v1.)
4. **`skipped` is a transition status**, defined as the exploration-policy
   outcome of a raising node — `error` non-null, captured reads and
   surviving writes preserved (matching inherited corpus item 6, the
   STRICT/EXPLORATION divergence).
5. **Redacted values are unforgeable within the API** (node-protocol §5.2's
   reservation of top-level `kind: "redacted"`).
6. **Effect receipts, hash-chain activation and replay fields are additive
   v1.x extensions**, not v2 — the v1 schema reserves their places
   (`integrity`, `EffectDescriptor.idempotency_key`) so 0.6 does not break
   the format.

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
