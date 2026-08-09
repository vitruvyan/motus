# ADR-016 — a run reports whether its evidence was written

- **Status:** ACCEPTED — founder, 2026-08-09
- **Date:** 2026-08-08
- **Authority:** founder decision, 2026-08-08, choosing option 1 of three after
  the CTO reproduced all four terminal paths
- **Closes:** #42
- **Amends:** no contract text. Adds public API: `EvidenceStatus`,
  `RunResult.evidence`, `NodeFailed.evidence`, `SinkFailed.evidence`.
- **Defers:** the same fact inside the trace itself, to a schema revision —
  see *What this does not do*.

## Context

A required sink refusing to persist must prevent a claim of logical success.
`runtime.py` implements that in one line:

```python
if record["kind"] == "run_completed":
    ...
    raise SinkFailed(self._trace, exc) from exc
# The run was already failed/cancelled. Preserve that primary
# terminal in memory; persistence is honestly best-effort.
```

The comment's reasoning is sound and this ADR keeps it: if a node raised *and*
the archive refused, telling the caller "archive unavailable" hides that the
model never answered, and the node is the more important news.

The consequence was not sound. Measured on all four terminal paths, with a
required sink refusing every terminal:

```
                        archivio sano              archivio che rifiuta
run completato          status='completed'         solleva SinkFailed    ← lo sa
nodo che esplode        solleva NodeFailed         solleva NodeFailed    ← identico
rotta che non trova     status='failed'            status='failed'       ← identico
limite raggiunto        status='failed'            status='failed'       ← identico
run annullato           status='cancelled'         status='cancelled'    ← identico
```

Three silent terminals, not the two #42 names — `transition_limit_exceeded` was
in nobody's list. And on the route-miss path, with the clock and identity fixed:

```
traccia identica byte per byte : True (2783 byte)
stato finale identico          : True
esiste un record sink_failure  : False
```

**Nothing the caller could reach distinguished a healthy run from one whose
evidence had been destroyed.**

### What was already true, and reframes the issue

Three parties learn. Two of them are not the one that matters:

```
l'archivio stesso    finish(complete=False)          ← already told
chi legge il file    T3/INCOMPLETE: last record is 'routing'
chi ha chiamato      only on run_completed
```

So #42 is not "the evidence is silently lost." The artifact is an honest prefix
and any later reader sees it. It is:

> **The only party still able to act is the only party not told.**

The auditor opening the file in two years sees it. The application that has just
assessed a loan, and could still retry, alert, or withhold the decision, does
not.

## Decision

### 1. Every outcome carries `evidence`, alongside the status and never instead of it

`RunResult.evidence`, and the same attribute on `NodeFailed` and `SinkFailed` so
a caller can read it off whatever it is holding without first asking what that
is.

The status keeps meaning the primary cause. This is the second fact, carried in
parallel — which is what preserves the reasoning in the code comment above while
closing the gap it left.

### 2. Three states, not a boolean

```
persisted      a required sink accepted every record, terminal included
incomplete     a required sink refused something; what is stored is a prefix
not-required   no sink was configured, so nothing durable was promised
```

A boolean forces `not-required` to be either a lie or a false alarm. A caller
that reads a durability failure from an ordinary in-memory run will refuse to
act on every local run there is — the same class of false statement as the
defect, pointing the other way.

The in-memory *profile* is not the same thing as no sink. It accepts one, and a
refusal there is a real refusal; the distinction is drawn on `sink is None`, not
on the profile, so the weakest profile cannot hide a failure.

### 3. One fact, read twice, unable to disagree

The value is the hub's `_saw_terminal` — the identical fact already handed to the
session as `finish(complete=...)`. Not a second latch that could drift from the
first.

It travels on the `_RunHandle` beside `trace` and `state`, for the reason that
class exists: the handle's `hub` is cleared before the caller reads its result,
because by then `self._hub` may name a later run's hub.

A test asserts caller and session agree across all three profiles and both
refusal cases, rather than asserting each side's value separately — two
independent assertions can both stay green while the behaviours diverge, which
is how ADR-015's defect happened.

## Consequences

- 568 tests, up from 549. Twelve new tests plus a six-case parametrised
  invariant. Nine examples green.
- Additive API only. `RunResult.evidence` is defaulted so hand-construction keeps
  working, and the value carried is a plain `str` on every carrier — result,
  both exceptions, both stream drivers. `EvidenceStatus` is the vocabulary;
  `run.policy` in the header is `"strict"` and not a `Policy`, and this follows
  it. Comparison against the enum still reads naturally.
- `test_the_readme_names_every_public_symbol` failed on the new symbol, which is
  the guard working: the README now documents `evidence` where it documents
  durability profiles, with the example a careful caller would write.
- **On the `NodeFailed` path the value is read at raise time, before the hub is
  closed.** I wrote that this could be pessimistic — `incomplete` where a later
  flush would have succeeded. An adversarial round could not construct that
  state in 2,500 randomised cases across every profile, refusal point and
  surface: `persisted` matched reality exactly, `NodeFailed` included. A
  terminal always reaches `persist` with `force=True`, so no terminal is ever
  left buffered. The caveat described a state that does not exist, and is kept
  here only as the record of an overclaim.

### Added after the adversarial round

The first version of this change reached `run()` and `arun()` and stopped
there. **`stream()` and `astream()` carried no `evidence` at all** — the two
surfaces where the caller is most obviously still able to act, because it is
inside the loop. The value was already computed and sitting one attribute away
behind the driver. Both now expose it, and raise while the run is unfinished,
following `RunResult.status`: returning the initial `not-required` mid-stream
would be a lie for the whole duration of the stream, which is the shape of
defect this attribute exists to end.

Two smaller ones from the same round: the value was an enum on `RunResult` and a
plain string on the exceptions, so `f"{x.evidence}"` printed two different
things for one fact; and `NodeFailed`'s default could be flipped to `persisted`
without failing a single test. Both fixed and both now pinned.

## Mutation probes

| neutralised | tests that fail |
|---|---|
| `evidence` always reports `persisted` | 8 |
| `not-required` collapsed into `incomplete` — *the boolean this ADR rejects* | 1 |
| the stream drivers stop reporting it | 3 |
| `NodeFailed`'s default flipped to `persisted` | 1 |
| read before `close()` instead of after | **0** |

**The third probe stayed green, and the comment claiming that ordering was
load-bearing was wrong.** A terminal persists with `force=True` and has always
flushed by the time `close()` runs, and `close()` skips its flush once a refusal
is latched — so the two read points are equivalent and no test can distinguish
them. The comment now says exactly that: the ordering is defensive, kept so it
stays correct if terminals ever stop forcing their own flush.

Recording this rather than deleting the probe: a comment asserting a reason that
does not exist is worse than no comment, and finding one in my own work is the
argument for running the probes at all.

## What this does not do

**It does not put the fact in the trace.** A later reader of the *artifact*
already sees `T3/INCOMPLETE`, but a reader handed the in-memory trace object —
forwarded, embedded in another system — still cannot tell. That needs a field
on the terminal record, and therefore a schema revision.

**Correction, 2026-08-08.** This section originally said "needs schema 1.1",
and so did ADR-014. Both were wrong. Motus has emitted `schema_version:
"1.1.0"` since 0.6; the schema accepts 1.0.0 and 1.1.0 and the validator
branches on neither. 1.1 is a version number that was never given its content:
`payload_hash` is typed null unconditionally, so a 1.1.0 trace is refused a
hash by the same schema whose prose said 1.1 was where hashes become possible.

Proved rather than asserted — the same trace, accepted clean, then given the
chain that 1.1 supposedly permits:

```
la traccia dichiara schema_version: 1.1.0
il validatore la accetta cosi' com'e':  True
poi ci metto la catena di hash:         5 violazioni
  SCHEMA $.records[0].integrity.payload_hash: 'sha256:aaa…' is not of type 'null'
```

The schema's prose is corrected on this branch. What the three items need is a
schema *revision* carrying its own version, and they should be planned as one.

**Still open, and not fixed here (adversarial round, 2026-08-08).** The shipped
`JsonlTraceSink` publishes its artifact inside `finish`, by renaming
`.jsonl.part` to `.jsonl`. `_signal_finish` swallows exceptions — contract-
sanctioned and pre-existing — so a `finish` that fails leaves the caller reading
`persisted` while `path_for(run_id)` finds nothing. The bytes are whole and
validate clean; what is lost is discoverability, not content. It satisfies this
ADR's own definition of `persisted` ("a required sink accepted every record")
and still falsifies the README's gloss. Recorded rather than fixed: the honest
options are a fourth state or a narrower definition, and neither is obvious
enough to decide inside this change.

## Alternatives rejected

**Raise `SinkFailed` on every terminal.** The symmetric answer, and the worst.
On a node failure it replaces `NodeFailed` — a caller catching the one no longer
catches the other, and loses the actual reason the run failed. On route-miss and
transition-limit, `run()` currently *returns*, so it would start raising and
break every caller that branches on `result.status`. It trades a known gap for a
larger one.

**Document the limit and change nothing.** This project has correctly chosen that
before — ADR-013 deferred a recipe rather than invent one. It is wrong here: a
caller reasonably reads "the run failed, with a required sink" as "the failure is
on record", and in a regulated setting the whole obligation is being able to
prove *why* an application was refused.

**Rely on `finish(complete=False)`.** It already exists and nobody knows it does.
Worth documenting regardless — and it answers the sink, not the caller. The sink
cannot decide whether to release a lending decision.
