# ADR-010 — Evidence integrity, and what the sink protocol still cannot express

- **Status:** PROPOSED
- **Date:** 2026-08-05
- **Authority:** founder direction continuing the 0.7 phase
- **Depends on:** ADR-004 (run-scoped TraceSink binding), ADR-008 §2 (durability
  profiles and the declared loss window)
- **Amends:** nothing. ADR-001…009 stand unedited. This ADR records a
  contradiction inside ADR-004 (see §Amends) without resolving it.

## Context

A fourth adversarial round, run by three independent agents, attacked the
durability surface. Two agents found the same defect from different lenses,
which is the strongest corroboration this project has had. The findings are
recorded in issue #32; what matters here is that two of them were not bugs in
the implementation of a decision, but the absence of one.

Motus's thesis is evidence a third party can re-check. That makes a *wrong*
persisted account strictly worse than a missing one, and two paths produced
exactly that.

**A sink that committed a record and failed to acknowledge it was written to
again.** `_store`'s terminal branch re-entered the sink after `persist` raised,
and nothing latched the failure on the synchronous and in-memory paths. One
file then held `run_completed(seq 14)` and `run_failed(seq 14)` — a single
artifact asserting both that the run succeeded and that it failed, at the same
`seq`. It fails T1 and T3, and `Trace.from_dict` refuses to load it.

The re-entry was not careless. It exists to *repair* the artifact when a sink
refuses a terminal cleanly: reusing the refused record's `seq` yields a valid
document ending in `run_failed(sink_failure)`. That repair is correct only if a
raised `write` committed nothing — an assumption no contract text states, and
one a remote sink cannot satisfy. `guarantees.md` invariant II contemplates
remote sinks in as many words ("replication if any"), so lost acknowledgements
are inside the model, not outside it.

**The buffered profile discarded what it still held at teardown.**
`_ObservationHub.close()` latched `_closed` and cancelled the timer without
flushing, and every path back into the timer bails on `_closed`. Every terminal
force-flushes, so ordinary runs were safe. A run that never reaches one — a node
raising `BaseException`, a cancelled task, an abandoned driver — arrived with a
full buffer: on shipped defaults, **47 records in the trace, 0 at the sink, 47
still in the buffer after `close()` returned**, process alive throughout.

**A sink cannot write a conforming artifact from the protocol alone.** The
header delivered to `open_run` is `Trace.run`, which does not carry
`schema_version`. A sink that persists exactly what it was handed fails
`JSONL1: 'schema_version' is a required property`. ADR-004's Context names this
as the problem it exists to solve and its Consequence 1 claims it solved; its
Verification item 1 pins the header to `Trace.run`, which is what excludes the
field. **The two clauses contradict each other**, and the implementation
faithfully followed the narrower one.

## Decision

### 1. After a write raises, the runtime never writes to that session again

The runtime cannot distinguish "refused without persisting" from "persisted,
acknowledgement lost". It must therefore treat any raised `write` as poisoning
the session. The synchronous and in-memory paths now latch the failure exactly
as the buffered path already did — an asymmetry that was itself the evidence
this was an oversight rather than a decision.

**This gives up the repaired artifact, and that cost is accepted knowingly.**
When a sink refuses a terminal without persisting it, the persisted account is
now a truncated prefix rather than a valid document ending in
`run_failed(sink_failure)`. OPEN-08 licenses a truncated prefix in as many
words; nothing licenses a corrupted suffix. Nothing changes for the caller:
`SinkFailed` still carries the in-memory trace with the `sink_failure` record.

Reusing the refused `seq` is what makes the repair valid in the clean case and
what makes it collide in the lost-ack case. No `seq` assignment is correct in
both — a fresh `seq` leaves a gap in one and two terminals in the other. The
repair cannot be rescued by arithmetic; it needs the sink to be able to say "I
did not take that", which the protocol cannot express. See §Open below.

### 2. `close()` hands over what it still holds

Best-effort, and never when a failure is already stored — that would be the
retry Decision 1 refuses. ADR-008 §2 already states the governing principle:
the declared loss window "does not license silent loss while the process is
alive". `node-protocol.md` §7.3 requires an interrupted attempt's unclosed
`attempt_started` to be "visible, attributable evidence, never an erased gap";
under the buffered profile it was an erased gap.

### 3. Two smaller corrections, stated because they change observable behaviour

- `RunResult.status` is guarded. It stripped `run_` off the last record's kind
  with no check, so an abandoned driver's trace answered `"transition"` — a
  value outside the three this contract names, which callers branch on. It now
  raises rather than inventing a status for a run that has none.
- `run_id=""` is refused rather than silently replaced by a generated UUID.
  `run_id or uuid()` treated the empty string as "not supplied", so the stated
  1..200 check never saw it.

## Open, deliberately not decided here

Both open items are the same shape: **the sink protocol cannot express
something a conforming sink needs.** They belong to one decision, not two.

### The header does not carry `schema_version`, and the obvious fix is wrong

A sink that persists exactly what `open_run` handed it fails
`JSONL1: 'schema_version' is a required property`. It can only conform by
importing `TRACE_SCHEMA_VERSION` from the package — the out-of-band coupling
ADR-004 exists to remove.

An earlier draft of this ADR decided to add the field to the header dict, and
that was **implemented, tested green, and then found wrong** by re-running the
adversarial round's conformance script rather than trusting the new test. The
reason is worth recording, because the mistake is inviting:

`TraceHeader` in `trace.v1.schema.json` is the **JSONL header line** —
`{schema_version, run}` — with `additionalProperties: false`. What `open_run`
receives is the **`run` object**, one level down. Putting `schema_version`
inside it makes the run object invalid, so a sink that writes
`{"schema_version": …, "run": header}` — the natural reading, and the one
ADR-004 describes — produces a document that fails validation *because of the
fix*. The accompanying test passed only because it split the key back out by
hand: it was written to the implementation instead of to what a sink would do.

The correct shape is for `open_run` to receive the **TraceHeader**, not the run
object. That is a breaking change to a published protocol, and it is exactly
the sort of change that should not be made at the end of the session that found
the need for it.

### `TraceRunSink` has no close or abort signal

This is the root of the incomplete-session problem. ADR-004 omitted one on the
reasoning that
"terminal acceptance is already the success boundary". Adversarial enumeration
shows a session can be opened and then under-served in **twelve** distinct ways,
and that a session cannot distinguish *in flight* from *abandoned forever*
because the runtime only ever calls `open_run` then `write`.

This matters for scoping: **deferring `open_run` until the first record closes
only three of the twelve.** The other nine are partial sequences no sink-side
strategy can repair. An earlier draft of this decision proposed exactly that
deferral, and the enumeration is what refuted it — recorded here because the
next reader will have the same idea.

The same missing expressiveness blocks Decision 1's lost repair: a sink that
could report a clean refusal would let the runtime repair the artifact safely.

Adding a session-completion signal is a public protocol change with real design
surface, and it is not being made at the end of the session that found the need
for it. It gets its own decision, its own branch, and its own adversarial round.
Issue #32 carries the enumeration.

## Consequences

- A persisted artifact can now be **absent, or a prefix, but never
  self-contradicting**. That is the property the product's thesis actually
  needs.
- The buffered profile's declared window means what it says: evidence is lost
  with the process, not by a healthy process choosing to drop it.
- `RunResult.status` can now raise where it previously returned a value outside
  the three this contract names. That is a behaviour change, and it is the
  point: a run with no terminal has no status.
- No schema amendment, no fixture regeneration, no frozen-corpus change. No
  change to `open_run`'s signature or to the header it receives.
  `vitruvyan_motus.__all__` is unchanged.

## Amends

ADR-004's Verification item 1 ("the sink receives a header structurally equal
to `Trace.run`") and its Consequence 1 ("a sink receives everything needed to
persist an independently verifiable trace") contradict each other, and the
implementation faithfully followed the narrower one. **This ADR does not
resolve that contradiction** — it records it, and records why the obvious
resolution is wrong. The `Amends` header above is therefore aspirational for a
successor ADR, not a change made here.
