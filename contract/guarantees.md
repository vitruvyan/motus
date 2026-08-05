# Guarantees (normative)

**Status: v1 — accepted by ADR-001.** Counterparty: operators and auditors. Enforcement:
`tests/contract/` (the conformance suite) and the CI benchmark gate — a
release that violates either does not ship.

## 1. The five invariants

**I. One execution semantics.** All physical optimizations pass through the
same executor and produce the same observable trace. There are no co-equal
engines; any future alternative path must prove trace-equivalence against the
interpreter per release, in CI.

**II. TraceSink failure prevents logical success.** A run cannot declare
itself completed if its required sink has not accepted the trace. A sink is
required when the profile requires one or when the caller explicitly supplies
one. Crash guarantees still depend only on the declared profile:

| Profile | Guarantee after process loss |
|---|---|
| `in-memory` | None. The trace exists only as the returned object. |
| `buffered` | Everything up to the last confirmed flush; the loss window is declared in the run header's `sink` object (flush_interval_ms / chunk_records — required for this profile). Transition records with outcome `raised` or `cancelled`, and the terminal records (`run_failed`, `run_cancelled`), flush immediately — failure evidence is never in the loss window. |
| `synchronous` | A transition is committable only after sink ack, within the declared limits of the persistent medium (fsync semantics, replication if any). |

The profile is recorded in the run header. Claiming a stronger guarantee than
the profile bought is a contract violation. An explicitly supplied sink under
`in-memory` is synchronous (`flush_interval_ms: 0`, `chunk_records: 1`) and its
configuration is recorded in the header, but it adds no crash-survival claim.

**III. The kernel does not interpret the domain.** Motus imports and embeds no
LLM, no Vitruvyan OS, no LangChain, no Orders, no epistemic categories.
Deterministic explanations only; semantic meaning belongs to the consumer.
Enforced by an import-boundary test in the conformance suite.
*Vocabulary note (cross-review MF-17):*
`Fact`, `Decision` and `Rejection` are hereby defined as **neutral workflow
primitives** — a Fact is a *recorded assertion*, never verified truth; a
Decision is a keyed routable value; a Rejection is a recorded not-taken.
No epistemic weight is attached by the kernel. Accordingly the standard
metadata key is the domain-neutral `ruleset_version`; a consumer's own
vocabulary (Terraveler's `carta_version`) maps onto it or rides as a
consumer-defined key.

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

## 2. Replay and delivery semantics, stated honestly

Motus 0.6 implements three explicit modes. Playback reconstructs committed
state and never executes node code. Verify re-executes only `pure` nodes with
their recorded context draws and compares captured behavior. Resume creates a
new trace segment linked to the last committed state; persisted history is
never rewritten.

For external effects, resume is permitted only when every observed effect has
a non-empty idempotency key and a completed adapter-supplied receipt. This is
an **at-least-once execution with idempotent effects** condition, not a proof
that the external system committed exactly once. An absent or `unknown`
receipt preserves uncertainty and makes automatic resume fail closed.
Exactly-once is promised in no version, ever.

## 3. Performance SLOs (CI gate)

Baseline: Axis v0.4.0 on the reference VPS profile (AMD EPYC vCPU guest,
Python 3.10.12), collected by `benchmarks/collect_baseline.py`, raw document
in `benchmarks/`.

**Method, normative.** Per measurement: 2 warmup calls discarded, then ≥ 7
measured samples with `gc.collect()` before each. Per baseline: **≥ 5
independent runs** of the whole benchmark, aggregated as the median across
runs. Raw JSON committed. cProfile is never quoted as wall time.

**What the SLOs assert on, and why it changed.** Measurement on this profile
showed the in-run *median* varying **27% run to run** with the guest's own
load average flat — the contention is at the hypervisor and is invisible from
inside the VM. A gate on that statistic would fail on noise and pass on
regressions. So the asserted statistic is the **min-of-samples, taken as the
median across runs** (measured spread ≤ 11%), with the in-run median published
beside it as an observation, never as a gate. Tolerance band: **±25%**, set
above the measured spread of the asserted statistic and below the size of any
regression worth catching. The second reference profile (a GitHub-runner
class) must be characterized the same way — 5 runs, published spread — before
it may gate anything. ADR-006 completed that characterization for the native
Motus 0.6 trace; its separately enforced profile follows the immutable Axis
evidence below.

| SLO (asserted statistic: min-of-samples, median across ≥5 runs) | Target | v0.4.0 measured | run-to-run spread |
|---|---|---|---|
| Per-node overhead, full trace, n ≤ 1000 | ≤ 15 µs | 11.9 µs | 9 % |
| 100-node no-op run overhead vs bare loop | ≤ 1 ms | 0.64 ms | 11 % |
| Trace serialization (persist path) | ≤ 1.5 × pure `json.dumps` | 4.7 × | 8 % / 22 % |
| Superlinear accumulation term at n = 1000 | < 10 % of total | 25 % | 7 % |
| Trace completeness at the above numbers | 100 % — no sampling, ever | 100 % | — |

Published alongside, not asserted: the in-run median per-node cost, 12.5 µs
(spread 27 %).

A regression beyond target-plus-tolerance fails CI; improving a target
requires an ADR, not a lucky run. The two Axis rows that miss their targets
remain historical debts recorded as measured reality, not achievements.
Trace completeness is an invariant, never a debt and never a tunable sampling
rate.

### Motus 0.6.1 GitHub EPYC profile (ADR-006 + ADR-007)

The native Motus workload is deliberately separate: a 1,000-node run emits
3,002 records rather than the Axis reference's 2,002. Five independent runs
on Python 3.10.12 and AMD EPYC 9V74 produced the corrected cold-materialization
document `benchmarks/candidate-v0.6.1-epyc-py310.json`. The 0.6.0 document is
historical evidence only: its serialization row measured a cache hit and is
not a current product claim.

| Motus native SLO | Target | v0.6.1 measured | run-to-run spread |
|---|---:|---:|---:|
| Motus per-node overhead, full trace, n <= 1000 | <= 45 us | 51.2 us | 3% |
| Motus 100-node no-op overhead | <= 3.25 ms | 3.44 ms | 4% |
| Motus cold trace materialization / `json.dumps` | <= 1.5x | 1.7x | 10% / 14% |
| Motus positive superlinear accumulation at n = 1000 | < 10% | 3% | 77% ratio spread |
| Motus trace completeness | 100% — no sampling, ever | 100%; 3,002 records | — |

The common 25% tolerance produces hard ceilings of 56.25 us/node, 4.0625 ms,
1.875x serialization, and <12.5% positive superlinearity. The checker
recomputes the measured values and refuses a different interpreter, runtime,
CPU class, incomplete trace, or modified aggregate.

## 4. Terraveler compatibility surface (frozen)

The following surface, used in production by Terraveler against Axis v0.4.0,
is preserved by the compatibility view or explicitly migrated with a
deprecation path. The golden tests in `tests/compat/terraveler/` are frozen
before implementation and are not editable by the implementing agent.
*Status:* installed. The corpus lives in `tests/compat/terraveler/`, with a
golden lifted from production rather than authored for the occasion.

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

**Legacy `Decision` strategy (cross-review MF-10, strategy 1 — two types, no
cross-mapping):** the compatibility view is a *legacy-format producer, not a
translator*. Legacy runs (the `Runner(nodes, policy=)` path) record legacy
shapes, including v0.4.0's `Decision(description, timestamp)`; GraphSpec
runs record trace v1 shapes with the Motus-native `Decision(key, value,
reason?, ts)`. A legacy Decision never enters a v1 trace and no deterministic
mapping between the two is defined or promised. Routing requires native
decisions, which only GraphSpec runs produce — the legacy path has no route
table, so nothing is lost. The golden tests pin the legacy side; the contract
fixtures pin the native side. **Public names are unambiguous by decision**:
`from vitruvyan_motus import Decision` is the native type;
`from vitruvyan_motus.compat import LegacyDecision` is the Axis type — no
import path ever exposes both under one name, and Terraveler receives no
silent alias: its migration changes import paths explicitly.

## 5. Inherited conformance corpus (Axis 0.4.0)

These behaviors were earned through adversarial review and become Motus
contract tests, ported without retroactively weakening their expectations.
*Status:* installed in `tests/contract/`, stated at contract level rather
than copied from the implementation's unit tests — which keep their own,
finer-grained coverage:

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

## 6. The three observation surfaces (normative — cross-review MF-11)

The dissolution of `SynapticBus` produces three protocols. Their semantics
are contract, not implementation detail; the implementer invents none of
this.

**TraceSink** — the durable surface.
- Run binding: the durable surface is opened as
  `TraceSink.open_run(header) -> TraceRunSink`. The isolated header is
  delivered exactly once before the first record; every record batch for that
  run is written only through the returned session. A required sink's refusal
  to open the run is a sink failure. See ADR-004.
- Delivery unit: trace records, in `seq` order, at the flush boundaries the
  durability profile defines (`synchronous`: every record; `buffered`: per
  chunk/interval, with failed-transition and terminal records flushed
  immediately; `in-memory`: retained, no persistence promise).
- The stream a sink receives IS the persisted account — same records, same
  order, no sampling.
- Failure: a required sink's rejection prevents the run's logical success
  (invariant II). Sink-level retry, if any, is sink configuration; the
  runner does not silently drop and continue. **Persistence limitation
  (OPEN-08, explicit by decision):** when the failing component IS the
  required sink, the `run_failed` record with cause `sink_failure` is
  best-effort — the logical failure toward the caller is guaranteed, the
  persisted trace may end crash-truncated per its declared profile.
- A sink MUST NOT mutate records (delivered values are isolated per
  node-protocol §1.2's guarantee) and MUST NOT feed anything back into
  execution.

**Listener** — the live, non-authoritative observation surface.
- Delivery unit: each record, after it is committed to the run's log,
  in `seq` order per listener.
- Data and failure isolation: per-listener isolation in the dispatch layer; a
  listener's exception is counted and swallowed, and record mutation affects
  only a private copy. There is NO critical flag on this surface.
- Read-only: delivered records are isolated; mutation attempts affect
  copies.
- Ordering across listeners is unspecified; within one listener it is `seq`
  order. Delivery is synchronous on the runner thread in 0.6, so a callback
  can delay execution. Code that also holds the Runtime can invoke its public
  cancellation surface. Listeners therefore MUST return promptly and MUST NOT
  be presented as incapable of affecting scheduling. Moving delivery off the
  runner thread is a later, explicitly-versioned change.

**StreamDriver** — the execution-coupled surface.
- The one surface honestly allowed to gate execution: it drives the run
  (consumer-paced iteration; pause/cancel through the runner's API).
- Cancellation through a StreamDriver lands as the trace-recorded
  `run_cancelled` (with `active_attempt`), never as an abandoned generator.
- Backpressure semantics: the runner does not start the next attempt while
  the driver has not consumed the previous yield. That coupling is the
  feature; documentation MUST present StreamDriver as execution-gating,
  never as "just another listener".

## 7. Versioning

Semantic versioning on the distribution. Any breaking change to a contract
surface is a major version. `schema_version` is single-sourced in the package
and asserted equal to this directory by a contract test. Consumers pin; the
trace names the version that produced it.
