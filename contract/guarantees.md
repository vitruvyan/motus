# Guarantees (normative)

**Status: DRAFT v1.** Counterparty: operators and auditors. Enforcement:
`tests/contract/` (the conformance suite) and the CI benchmark gate — a
release that violates either does not ship.

## 1. The five invariants

**I. One execution semantics.** All physical optimizations pass through the
same executor and produce the same observable trace. There are no co-equal
engines; any future alternative path must prove trace-equivalence against the
interpreter per release, in CI.

**II. TraceSink failure prevents logical success.** A run cannot declare
itself completed if the sink required by its durability profile has not
accepted the trace. Crash guarantees depend on the declared profile:

| Profile | Guarantee after process loss |
|---|---|
| `in-memory` | None. The trace exists only as the returned object. |
| `buffered` | Everything up to the last confirmed flush; the loss window is declared in the run header's `sink` object (flush_interval_ms / chunk_records — required for this profile). Transition records with `status: failed` and the terminal records (`run_failed`, `run_cancelled`) flush immediately — failure evidence is never in the loss window. |
| `synchronous` | A transition is committable only after sink ack, within the declared limits of the persistent medium (fsync semantics, replication if any). |

The profile is recorded in the run header. Claiming a stronger guarantee than
the profile bought is a contract violation.

**III. The kernel does not interpret the domain.** Motus imports and embeds no
LLM, no Vitruvyan OS, no LangChain, no Orders, no epistemic categories.
Deterministic explanations only; semantic meaning belongs to the consumer.
Enforced by an import-boundary test in the conformance suite.

**IV. Nondeterminism is declared and observable.**
(1) Kernel-generated timestamps, sequences and identifiers always come from
the run's clock/identity source — even in runs that declare no
reproducibility. (2) Node-level ambient nondeterminism passes through
RunContext when the run declares reproducibility. Replay capability is always
an explicit recorded property (`full | partial | none` + constraints); the
absence of a declaration never implies reproducibility.

**V. Structural completeness is not content exposure.** Causality remains
complete when content cannot be persisted: redacted values carry hash and
policy reference. Hash, reference and policy are evidence; the secret is not.

## 2. Replay semantics, stated honestly

Motus promises **at-least-once execution with idempotent effects** — never
exactly-once. In v1.0 there is no replay at all: the schema records what
replay will need (reads with causal references, context draws, effect
classes); the replay modes themselves are 0.6+. When they land: playback
never executes code; verify re-executes only `pure` nodes; effect replay
reuses recorded results only when a valid receipt exists and the replay
policy permits; resume continues from the last committed point per the
durability profile.

## 3. Performance SLOs (CI gate)

Measured baseline: Axis v0.4.0 on the reference VPS profile (AMD EPYC vCPU
guest, Python 3.10), min-of-7 methodology, raw JSON published per run.

| SLO | Target | v0.4.0 measured |
|---|---|---|
| Per-node overhead, full trace, n ≤ 1000 | ≤ 15 µs median | 11.8 µs |
| 100-node no-op run overhead vs bare loop | ≤ 1 ms | 0.80 ms |
| Trace serialization (persist path) | ≤ 1.5 × pure `json.dumps` | 4.1 × |
| Superlinear accumulation term at n = 1000 | < 10 % of total | 23 % |
| Trace completeness at the above numbers | 100 % — no sampling, ever | 100 % |

Reference hardware profiles: (a) the VPS class above; (b) one GitHub-runner
class, pinned in `benchmarks/`. cProfile numbers are never quoted as wall
time. A regression beyond target fails CI; improving a target requires an
ADR, not a lucky run.

## 4. Terraveler compatibility surface (frozen)

The following surface, used in production by Terraveler against Axis v0.4.0,
is preserved by the compatibility view or explicitly migrated with a
deprecation path. The golden tests in `tests/compat/terraveler/` are frozen
before implementation and are not editable by the implementing agent.

- `GraphState.empty(trace_id)`; `.with_intent`; `GraphState.new(prefix)`
  (present in v0.4.0, preserved defensively — the audited Terraveler surface
  builds its own trace ids)
- `.with_fact` / `.with_decision` / `.with_rejection` (via nodes)
- `.facts` / `.decisions` / `.rejections` / `.events` / `.trace_id`
  (trace schema v1's `run_id` maps to `.trace_id` on this view)
- `.to_dict()` — **legacy trace shape**: Terraveler persists this JSON in
  `ingestion_runs.trace` and `chat_traces.trace` (jsonb). The legacy shape is
  a distinct, frozen format from trace schema v1. The pinned shape is the
  **v0.4.0 wheel's output** — the exact keys Terraveler's stored rows have —
  plus the optional `metadata` object added post-0.4.0 (run-metadata, PR #11):
  the view emits it; readers of legacy rows MUST tolerate its absence. The
  view keeps producing this shape until Terraveler migrates by decision, not
  by surprise.
- `Fact` / `Decision` / `Rejection` constructors as shaped in v0.4.0
- `Runner(nodes, policy=)`; `Policy.STRICT` / `Policy.EXPLORATION`
- `NodeFailed` carrying `.state` — relied on at three production sites
  (ingest/run.py, ingest/extract.py, rag/app/main.py)

## 5. Inherited conformance corpus (Axis 0.4.0)

These behaviors were earned through adversarial review and become Motus
contract tests, ported without retroactively weakening their expectations:

1. State and trace survive node failure per contract (`NodeFailed.state`).
2. Retry exhaustion loses no collected trace; every attempt is recorded.
3. A `critical` sink can abort the run; non-critical listeners never can.
4. The file sink prevents path traversal and normalizes names safely.
5. Concurrent execution keeps the proven merge semantics **on the
   compatibility view** (legacy `ConcurrentRunner` + legacy trace shape):
   all branches run to completion against the seed, merged in declared
   order, append-only, with per-branch failure handling per policy.
   Fan-out has no representation in GraphSpec v1 or trace schema v1 —
   deliberately deferred, trigger: the first real consumer that needs
   declared concurrent topology.
6. `STRICT` and `EXPLORATION` keep their contract-defined divergence
   (fail-fast vs record-and-continue).
7. Old traces load: a persisted state without newer optional fields
   deserializes with documented defaults.

## 6. Versioning

Semantic versioning on the distribution. Any breaking change to a contract
surface is a major version. `schema_version` is single-sourced in the package
and asserted equal to this directory by a contract test. Consumers pin; the
trace names the version that produced it.
