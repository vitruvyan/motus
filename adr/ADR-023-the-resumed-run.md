# ADR-023 — the resumed run: the anchorable unit is the segment, and the link between segments belongs in the commitment

- **Status:** PROPOSED
- **Date:** 2026-08-13
- **Authority:** CTO. #75 asks three questions and says they must be answered
  before the shape is designed; this ADR answers them, and one of the three is
  answered by a measurement that removes the option the issue was leaning
  toward
- **Depends on:** ADR-019 (the derived root), ADR-020 (what an unpaired `BEGIN`
  may be reported as), ADR-021 (the commitment and its leaf digest)
- **Advances:** #75, and the phase 1 contract work that cannot be frozen until
  this is settled
- **Amends:** nothing yet. The change it proposes is to the commitment, not to
  the trace: `trace.v1.schema.json`'s `resume` block is `additionalProperties:
  false` and is **left exactly as it is**

## Context

A resumed run starts a fresh chain from its own header and is required to take
a **new `run_id`** (`replay.py`, `UnsafeResume: "a resumed segment must have a
new run_id"`). Its header carries a `resume` block:

```json
{"source_run_id": "cut-1", "source_seq": 4, "start_node": "second",
 "bundle_fingerprint": "bundle:sha256:9e34c4…"}
```

`bundle_fingerprint` is a digest over the **entire source trace document**, and
it sits in the header, so it is inside record 0's digest under the 3.0.0
recipe. It is genuinely load-bearing: rewrite one fact in the pre-crash segment
and the resumed root moves.

#75 asks whether that is the right binding, **or whether the second segment's
header needs the predecessor's *root* explicitly.**

### The measurement that decides it

```
UnsafeResume: a terminal trace cannot be resumed
```

`ReplayEngine._next_node` refuses a trace that reached a terminal record. And
`Trace.root` answers `None` for a document that has not reached one (ADR-019).

Put together: **a resumable segment never has a root, by construction.** Not
usually, not in the crash case — never. The two conditions are exact
complements, and there is no state in which a segment can be both resumed and
rooted.

Measured, on the code as it stands:

| | |
|---|---|
| segment 1 root | `None` |
| segment 2 root | `sha256:1ccc1981…` |
| what binds them | `bundle_fingerprint` over segment 1's document |

So the alternative #75 was leaning toward — bind to the predecessor's root —
**cannot be implemented, because the value does not exist for exactly the
inputs resume accepts.** `bundle_fingerprint` is not a compromise chosen over a
stronger binding. It is the only value there is.

### What the commitment log actually shows

Wired to a real resume, the log holds this:

```
seq=0 begin  run_id='cut-1'
seq=1 end    run_id='cut-1'  root=sha256:240f8b92…      ← absent after a real crash
seq=2 begin  run_id='cut-2'
seq=3 end    run_id='cut-2'  root=sha256:1ccc1981…
```

After a real crash — the case resume exists for — segment 1 leaves **a `BEGIN`
with no `END`**, because `_commit_end` returns early when the trace has no
derived root. So the durable account of a resumed run is: an unpaired `BEGIN`,
followed by an unrelated pair under a different `run_id`, **with nothing
connecting them.**

That is the defect, and it is sharper than #75 states. The trace documents
carry the link. The commitment log — the artefact an auditor walks, and the
only one an anchor covers — does not carry it at all.

It also collides with ADR-020. An unpaired `BEGIN` is the signal that a run
left no completion, and the property depends on that being rare. **A resumed
run manufactures one every time**, and today nothing distinguishes it from an
abandoned run.

## Decision

### 1. The anchorable unit is the segment, not the run

A run made of segments is verified by walking the segments. There is no
combined root over a multi-segment run, and there will not be one: computing it
would require a value the predecessor does not have.

This is stated as a decision rather than left implicit because "anchor the run"
is the phrase everybody reaches for, and the receipt format is about to be
frozen around whichever unit we mean.

### 2. `bundle_fingerprint` stays, and the trace schema does not change

Answered by measurement rather than preference: there is no root to bind to.
The `resume` block is correct as it stands and its
`additionalProperties: false` is left intact.

### 3. The link goes in the commitment: `BEGIN` carries `continues`

A `BEGIN` for a resumed segment carries, copied from the header it was written
for:

```
continues: {"run_id": <source_run_id>, "bundle_fingerprint": <bundle:sha256:…>}
```

Absent on a `BEGIN` that starts a fresh run — the ADR-021 rule that a field
which does not apply is not written, so its absence is a fact and not a
default.

Three things follow, and the third is the reason this is the right place:

- **the commitment log alone shows the chain.** An auditor holding the
  commitments, without any trace document, can see that `cut-2` continues
  `cut-1`;
- **an unpaired `BEGIN` that was later resumed becomes distinguishable from one
  that was abandoned.** This directly strengthens ADR-020's residual class: the
  verifier can report *this execution left no completion, and a later execution
  states it continued from it* — still a question, but a better-informed one;
- **it lands inside the leaf digest**, so it is covered by the checkpoint and
  by whatever anchors the checkpoint. Putting the link only in the trace would
  leave the anchor committing to a segment while the fact that it *is* a
  segment stayed outside everything we publish.

### 4. What a verifier reports, and the two claims it must keep apart

A receipt for segment 2 supports two different statements, and collapsing them
is the failure mode this decision exists to prevent:

- **segment 2's own integrity** — its root, its inclusion, whatever levels
  ADR-020 allows. This holds whether or not segment 1 still exists anywhere;
- **the claim that segment 2 continued segment 1** — checkable only by
  recomputing `bundle_fingerprint` over segment 1's document, which requires
  **holding that document**. There is no root that can stand in for it.

So: if segment 1 is discarded under a retention policy, segment 2 remains fully
verifiable **as a segment**, and its continuation claim becomes
*unverifiable* — not false, and not verified. A verifier that reports the whole
receipt as `VERIFIED` in that state has told the strongest available lie, and
one that reports it as broken has told a different one.

### 5. What is deliberately not decided here

Whether a run's segments should be discoverable from the log — *give me every
segment of `cut-1`* — is an index question, and an index is a read convenience
that must never become a thing the guarantee depends on. Decision 3 makes the
answer derivable by walking; making the walk fast comes later, if it comes.

## The costs accepted

**A resumed run still produces an unpaired `BEGIN`.** Decision 3 explains it,
it does not remove it, and it cannot: at the moment segment 1 dies there is no
root to end it with. Anyone reading the raw commitment count will see a rate of
unpaired `BEGIN`s that tracks the crash-and-resume rate. ADR-020's corrected
residual class already says this must be measured in the deployment rather than
assumed, and this is one of the things doing the measuring.

**The continuation claim is only as durable as the predecessor's document.** We
are choosing a binding that a retention policy can render uncheckable, because
the alternative does not exist. It is stated in the receipt semantics rather
than discovered by a customer whose auditor asks.

**A field on `BEGIN` that most `BEGIN`s do not have.** Optional fields are
where schemas rot. The mitigation is decision 3's rule — absent means fresh,
never defaulted — and a validator rule that a `continues` naming a `run_id`
with no `BEGIN` anywhere in the reachable chain is reported.

## Wrong turns

**1. I would have implemented the predecessor's root.** #75 asks the question
in a form that suggests it — *"is `bundle_fingerprint` the right binding, or
does the second segment's header need the predecessor's root explicitly?"* —
and it is the better-sounding answer: a root is the thing we publish, a
document digest is not. It took running a resume to find `UnsafeResume: a
terminal trace cannot be resumed`, which makes the better-sounding answer
impossible rather than merely worse.

The general shape is the one ADR-020's *Wrong turns* keeps recording under a
different disguise: **an argument about which binding is stronger, conducted
without checking whether the stronger one exists for the inputs in question.**

**2. The first draft of this ADR put `continues` in the trace's `resume`
block.** It is the obvious home — the resume block is where resume provenance
lives. It is also the wrong one twice over: the trace already carries that link
(`source_run_id` is right there), and the artefact that lost it is the
commitment. Adding a field to a schema to fix a gap in a different artefact
would have changed the frozen trace schema for no gain at all.

## Alternatives rejected

**A combined root over all segments of a run.** The clean answer to "what does
an anchor cover", and it needs a value from each segment. The predecessor has
none. Rejected because it cannot be computed, not because it is undesirable.

**Requiring a resumed segment to reuse the source `run_id`.** Would make the
chain visible for free. Refused already by the runtime and rightly: two
executions under one id with no other distinguishing field make the trace
documents ambiguous, and #82 has just finished establishing that the way to
distinguish executions is the sequence, not the id.

**Writing an `END` for the dead segment at resume time.** Tempting, because it
removes the unpaired `BEGIN` that decision 3 merely explains. It is a forgery:
it would state an outcome nobody observed, written by a process that was not
there, and binding it to a root the segment never earned. The unpaired `BEGIN`
is the truth about what happened, and ADR-020 exists to make truths like that
reportable rather than tidy.
