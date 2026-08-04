# Motus 0.6 architecture

Motus 0.6 turns the evidence prepared by 0.5 into operational replay while
preserving the one-semantics invariant.

## Compiled topology

`GraphSpec.compiled` is an immutable, pre-indexed `CompiledPlan`. It contains
declarations, transitions, the entry node and safety limit. It contains no
callables and is not another engine: `Runtime` still owns the only execution
state machine.

## Effect evidence

Effects enter the trace only through `RunContext.record_effect`. A receipt is
an opaque assertion from the adapter that performed the operation. Motus can
require its presence and preserve it, but cannot turn it into proof about an
external database, model provider or message broker.

## Replay modes

| Mode | Executes code | Result |
|---|---|---|
| Playback | Never | State reconstructed from committed writes |
| Verify | Pure nodes only | Divergence report over reads, writes and context draws |
| Resume | New work only | New trace segment causally linked to an incomplete run |

Resume requires a matching graph fingerprint. An external effect is resumable
only with an idempotency key and completed receipt. The guarantee is bounded
at-least-once execution under that idempotency contract; exactly-once is never
promised.

## Bundle and viewer

`TraceBundle` binds the exact GraphSpec to the trace and fingerprints the
combination. `explain()` returns deterministic JSON-shaped causal steps.
`to_html()` renders the same evidence into a standalone document with no
network resources or JavaScript dependency.

## Deliberate exclusions

Hash-chain activation, budgets, MCP, capability enforcement, `PlanDelta`,
distributed scheduling and exactly-once delivery remain outside 0.6.
