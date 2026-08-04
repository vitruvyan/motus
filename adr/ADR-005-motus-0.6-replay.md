# ADR-005 — Motus 0.6: compiled topology, receipts and replay

- **Status:** ACCEPTED
- **Date:** 2026-08-04
- **Authority:** founder directive to continue the agreed 0.6 roadmap
- **Depends on:** ADR-001, ADR-004

## Context

Motus 0.5 established one trace-first interpreter and the evidence required
for later replay. The accepted contract already assigns the next release four
retrofit-sensitive capabilities: a compiled topology path, effect receipts,
playback/verify/resume, and portable explanation. They must not create a
second execution semantics or make stronger delivery claims than the evidence
supports.

## Decision

### One semantics, compiled data

Every validated `GraphSpec` produces an immutable `CompiledPlan`. It contains
pre-indexed declarations and routing tables, but no executable node code and
no alternate state machine. `Runtime` consumes this plan by default. A test
must prove that its topology decisions equal the typed GraphSpec view.

### Effect evidence

Nodes record effects only through `RunContext.record_effect`. An
`EffectDescriptor` may carry an `EffectReceipt`: an opaque receipt id, a
terminal status, and an optional result fingerprint. Receipt contents are
evidence supplied by the effect adapter, not proof invented by Motus.
External effects remain unsafe to resume unless every observed external
effect has both a non-empty idempotency key and a completed receipt. This is
an at-least-once-with-idempotency condition; exactly-once is never claimed.

Trace schema family v1 gains the additive 1.1 wire form. The accepted 1.0
fixtures remain valid; `x-current-version` identifies the version emitted by
the current runtime. Hash-chain activation remains a separate decision and
integrity fields therefore remain nullable.

### Replay modes

- **playback** reconstructs committed state from trace evidence and executes
  no node code;
- **verify** re-executes only nodes declared `pure`, feeds their recorded
  context draws, and compares reads, writes and draw consumption;
- **resume** creates a new, causally linked trace segment from the last
  committed state. It refuses terminal traces, graph mismatches, ambiguous
  next nodes, and unsafe external effects.

A resumed segment records its source run, source sequence and start node in
the run header. It is a new run, not a mutation of persisted history.

### Portable evidence UX

`TraceBundle` binds the exact GraphSpec and trace, has a canonical bundle
fingerprint, produces deterministic explanations, and renders a standalone
HTML viewer without network resources or third-party runtime dependencies.

### Declaration enforcement

In strict policy, captured undeclared reads or writes fail the attempt before
commit. Exploration continues with the violation recorded. Enforcement is
based on captured behavior, never solely on declarations.

## Explicit exclusions

Motus 0.6 does not activate integrity hash chains, budgets, MCP, capability
enforcement, dynamic `PlanDelta`, distributed scheduling or exactly-once
delivery. Each requires a separate contract decision.

## Compatibility

The Axis compatibility view and the frozen Axis/Terraveler corpora do not
change. The native distribution remains dependency-free and Apache-2.0.
