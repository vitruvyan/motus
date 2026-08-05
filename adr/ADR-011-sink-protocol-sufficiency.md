# ADR-011 — Making the sink protocol sufficient

- **Status:** ACCEPTED
- **Date:** 2026-08-05
- **Accepted:** 2026-08-06 by the founder, after the adversarial round that
  found two defects in the change and one false claim in this document.
- **Authority:** founder direction; the 0.7 adoption track ("vai con B")
- **Depends on:** ADR-004 (run-scoped TraceSink binding), ADR-010 §Open
- **Amends:** ADR-004 §Decision — both the shape of `open_run`'s argument *and*
  the sentence "Version 0.5 adds no separate `close()` acknowledgement because
  terminal acceptance is already the success boundary", which Decision 2 below
  supersedes — and §Verification item 1. Also amends `contract/guarantees.md`
  §6's **TraceSink** clause, which is normative and must describe the protocol
  a sink author actually has to implement. ADR-001…003, 005…010 stand unedited.

## Context

ADR-010 recorded two things the sink protocol could not express, and
deliberately did not fix them. This ADR fixes them, together, because they are
one question: **can a sink written against the protocol alone produce a
conforming artifact?** Until now the answer was no, twice over.

**The header is the wrong object.** `open_run` received `Trace.run`. That is
not a `TraceHeader` — the schema requires `schema_version` beside `run`, and
forbids additional properties inside `run`. A sink could therefore conform only
by importing `TRACE_SCHEMA_VERSION` from the package, which is the out-of-band
coupling ADR-004 exists to remove. Its own Consequence 1 claims otherwise ("a
sink receives everything needed to persist an independently verifiable trace")
and its Verification item 1 is what prevented it.

ADR-010 records a wrong fix for this — adding `schema_version` *inside* the run
object — which was implemented, went green, and was caught only by re-running
an adversarial script. That is why the test accompanying this decision is
written as a sink that writes **exactly** what it was handed, unchanged.

**A session could not tell "in flight" from "abandoned forever".** The runtime
called `open_run` and then only `write`. There was no moment at which a sink
could treat a file as final, so a run torn down before its terminal left a
partial artifact indistinguishable from a live one. Adversarial enumeration
found **twelve** distinct ways a session ends under-served.

Both are breaking changes to a published protocol. They are made now because
nothing consumes the library yet: today the cost is two test assertions, and
after the first external sink it is every user who wrote one.

## Decision

### 1. `open_run` receives the document's `TraceHeader`

`{schema_version, run}` — the JSONL document's first line — rather than the
`run` object one level inside it. **ADR-004 §Verification item 1 is amended**
from "structurally equal to `Trace.run`" to "structurally equal to
`Trace.header`", resolving the contradiction with that ADR's Consequence 1 in
favour of the consequence, because the consequence is the reason ADR-004
exists.

`Trace.header` is added as the public accessor. A sink may now write
`json.dumps(header)` as line one and `json.dumps(record)` per record, and the
result validates.

### 2. `TraceRunSink` gains `finish(*, complete: bool)`

Called exactly once, when the runtime will send that session nothing more.
`complete` is True when the session received a terminal record and its sequence
is a whole trace, False when the persisted account is a prefix.

`complete` is the entire signal, deliberately. A sink can already see the
records; what it could not see is whether more were coming. Anything richer —
a reason, a status enum — would be the sink re-deriving what the records
already say.

**Optional on the sink's side.** The runtime calls `finish` when the session
provides it, exactly as it already checks `write` at bind time. A sink that
omits it behaves as before and forfeits only the distinction. Requiring it
would break every ad-hoc sink for no safety gain, since a sink that does not
implement it has no use for the signal.

**It is therefore deliberately not declared as a member of the
`TraceRunSink` Protocol.** Python has no optional protocol member: declaring it
makes `isinstance` and every static checker reject a write-only sink, imposing
exactly the break this clause promises not to impose. A first attempt did
declare it, and the package's own `_InMemoryRunSink` then failed its own
`runtime_checkable` Protocol — caught by the adversarial round, not by the
suite, because nothing in-repo calls `isinstance` on it. The signature lives in
the Protocol's docstring, and `InMemoryTraceSink` implements `finish` so the
shipped example is the complete shape rather than the partial one.

Called **outside** the hub lock, because it reaches user code that may block on
a filesystem or a network, and holding the lock across that stalls anything
else touching the hub. Best-effort: the run has already reached whatever end it
was going to reach, and raising here would replace that outcome with a
bookkeeping failure.

## Consequences

- A sink written against the protocol and nothing else produces an artifact
  that passes `contract/validate.py` — **whenever the session received at least
  one record.** This is the property ADR-004 claimed and did not deliver, and
  it is now a test rather than a claim.

  The qualifier is not a hedge. A session that received *zero* records cannot
  produce a valid artifact at all: `records` has `minItems: 1`, so a header-only
  file fails even under `--allow-incomplete`, which covers a truncated stream
  and not an empty one. That is a property of the schema, not of this decision,
  and the answer is Decision 2: a sink told `complete=False` with nothing
  written can discard the file rather than publish it. An unqualified claim here
  would have been false, and was, until the adversarial round measured it.
- Rows 1–3 of ADR-010's twelve (a session opened and given no records) remain
  possible, but are now **knowable**: the sink is told `complete=False`. The
  other nine were always partial sequences; they too are now labelled.

  Making this true took a second fix. The signal is emitted by
  `_ObservationHub.close()`, which lives in `_managed_execute`'s `finally` — and
  a generator that was never advanced never runs one, so a driver created and
  dropped left its session opened and told *nothing*, in all three profiles.
  The hub is now published on the run handle and closed by the same finaliser
  that releases the run claim. The consequence above was written before that
  was true; the round is what made it true rather than aspirational.
- The reference durable sink that M4 needs can be written honestly. That
  sequencing was the reason to do this first: a reference sink shipped against
  the old protocol would have had to import the schema version to work, and
  every user would have copied that.
- Two test assertions change, both tightened rather than weakened:
  `sink.header == result.trace.run` becomes `== result.trace.header`, and a
  shared-sink partition check reads `run["header"]["run"]["run_id"]`.
- No schema amendment, no fixture regeneration, no frozen-corpus change.
  `vitruvyan_motus.__all__` is unchanged.

## Open, deliberately not decided here

**A required sink's refusal is still swallowed on `run_cancelled` and
`run_failed(route_miss)`** — 4 of 14 rows in the adversarial matrix,
re-measured as unchanged after ADR-010's fixes. The artifacts are now honest
truncated prefixes rather than corrupted ones, which is the improvement, but
the caller is told nothing.

It is left open because the obvious answers are each wrong in a different way.
Raising `SinkFailed` for a failed run would replace `NodeFailed` and lose the
cause the caller actually needs. Appending a `sink_failure` record to the
in-memory trace would put a record after a terminal, which T3 forbids. Adding a
field to `RunResult` is a public API change for a case that may instead belong
in the profile contract. `guarantees.md` invariant II is narrowly about logical
*success*, and a cancelled run is not success — but §6 also says "the runner
does not silently drop and continue", and those two readings disagree.

That is a contract question, not an implementation one, and it should be
answered with the adversarial round's input rather than at the end of the
session that inherited it.
