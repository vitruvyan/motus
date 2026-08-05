# Motus 0.7 architecture

0.7 makes Motus usable for the work it was designed for: graphs that call the
outside world, and evidence a third party can pick up. Four things changed, and
one deliberately did not.

## Asynchronous execution, without a second engine

`guarantees.md` invariant I forbids co-equal engines and requires any
alternative execution path to prove trace-equivalence against the interpreter
per release, in CI. No such harness exists. The obvious way to add async — a
second executor — would have required building and maintaining one forever, to
prove two copies of the state machine had not drifted.

The node invocation is inverted instead. `Runtime._execute` no longer calls a
node: it yields an invocation request and receives `(returned, error)` back.

```
_execute   →  one state machine, unchanged
   ├── _drive  + _invoke_sync    :  result = node(state, ctx)
   └── _adrive + _invoke_async   :  result = await node(state, ctx)
```

`run`/`stream` answer on the calling thread; `arun`/`astream` await. Two drivers
of about twenty lines each. **Invariant I holds because there is no second
engine to diverge**, not because a test says so.

A graph may mix `def` and `async def` nodes. Single-lane remains single-lane:
one node at a time, awaited, emits the same seven record kinds in the same total
`seq` order, so T1's gaplessness, T6's adjacency and the E1–E11 machine are
untouched. Fan-out stays deferred under `guarantees.md` §5 with its existing
trigger — it is a different problem, and conflating the two is what hid the
asynchrony regression in the first place (ADR-009).

## A run's result belongs to the run

The lifecycle claim used to be released when the execution generator unwound,
and only *afterwards* did `run`/`arun` read `self._trace` and `self._state` to
build a `RunResult`. A second run admitted in that window rebinds both first, so
one caller received another caller's evidence — measured at 2 mismatched traces
per 3000 `arun` attempts across two threads.

Identity was already run-scoped, because run-scoped cancellation needed it
(ADR-008 §1). The result now travels on that same handle, published **before**
the claim is released. A handle belongs to exactly one run and no later run can
reach it, so this is structural rather than a wider critical section — widening
the lock would serialise unrelated runs and cost the asynchronous surface
exactly what it was built for.

The same handle carries the run's observation hub, so a driver dropped before it
was ever advanced still releases the claim and still closes its durable session.

## The sink protocol became sufficient

ADR-004 promised that "a sink receives everything needed to persist an
independently verifiable trace". It did not deliver it, and its own Verification
clause is what prevented it. Two changes (ADR-011) make the promise true:

- `open_run` receives the document's **TraceHeader** — `{schema_version, run}`,
  the JSONL first line — not the `run` object inside it. A sink can now write
  what it was handed, verbatim, and the result validates. Previously it could
  conform only by importing `TRACE_SCHEMA_VERSION` from the writer.
- `TraceRunSink.finish(*, complete: bool)` is called once, when the runtime will
  send that session nothing more. Without it a session could not tell *in
  flight* from *abandoned forever*, so a run torn down before its terminal left
  a partial artifact indistinguishable from a live one.

`finish` is optional on the sink's side and therefore **not declared as a
Protocol member** — Python has no optional protocol member, and declaring it
would reject every write-only sink.

## Evidence may be absent, or a prefix, never self-contradicting

Two paths produced an account worse than a missing one.

A sink that committed a record and then failed to acknowledge it was written to
again, so one file could hold `run_completed(seq 14)` and `run_failed(seq 14)`:
a single artifact asserting both outcomes, which the package's own reader
refuses to load. The runtime cannot distinguish "refused without persisting"
from "persisted, acknowledgement lost" — invariant II contemplates the latter by
naming replication — so it now treats any raised `write` as poisoning the
session. The cost is stated in ADR-010: a clean refusal now truncates where it
used to repair, and OPEN-08 licenses a truncated prefix in as many words.

The `buffered` profile discarded whatever it still held at teardown. Every
terminal force-flushes, so ordinary runs were safe; a run that never reached one
arrived with a full buffer and lost it, process alive throughout. `close()` now
hands it over.

## A durable sink ships

`JsonlTraceSink` writes one JSONL document per run and `fsync`s by default,
because that is what the `synchronous` profile promises and flushing alone does
not buy it. The filename is the session signal made concrete: `<run>.jsonl` for
a whole account, `<run>.partial.jsonl` for a prefix, `<run>.jsonl.part` for a
run whose process died before anything declared it over. A session that received
no records is deleted rather than published — the schema requires at least one
record, so a header-only file is something no reader can accept.

It imports nothing from `vitruvyan_motus` and inspects no record kind, enforced
by a test. It is therefore the executable proof that the protocol above is
sufficient, rather than a claim about it.

## What did not change

No schema amendment, no fixture regeneration, no frozen-corpus change across the
whole of 0.7. Single-lane asynchrony and the sink protocol are both
contract-level facts, and `contract/guarantees.md` §6 was amended to describe
the protocol a sink author actually implements — but `trace.v1.schema.json`,
`graphspec.v1.schema.json` and both frozen corpora are untouched.

## Deliberate exclusions

Fan-out. `averify`/`aresume` — the asynchronous twin of the replay surface, so a
node that is both `async def` and `pure` is still refused with
`ReplayUnsupported` rather than verified (#29). Whether a caller should learn
that a required sink refused a `run_cancelled` or `run_failed(route_miss)`
terminal — ADR-011 §Open records why each obvious answer is wrong in a different
way. Hash-chain activation, budgets, capability enforcement and distributed
scheduling remain outside, as in 0.6.
