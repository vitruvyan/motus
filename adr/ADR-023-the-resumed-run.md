# ADR-023 — the resumed run: the anchorable unit is the segment, and the link between segments belongs in the commitment

- **Status:** ACCEPTED
- **Date:** 2026-08-13
- **Accepted:** 2026-08-13 by the founder
- **Corrected:** 2026-08-13, hours after acceptance, by four findings on #86.
  **One of them reverses decision 1** — a run-level commitment is not
  impossible, it already exists transitively and I had argued it away. The
  founder accepted a version whose first decision was wrong, and that is
  recorded here rather than smoothed over. Decision 3 survives and gets
  stronger; decisions 2 and 5 are untouched
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
usually, not in the crash case — never.

The implication runs **one way only**, and the first draft of this ADR called
the two conditions "exact complements", which is false and dangerous in the
direction it is false. *Resumable ⟹ rootless* is what holds and is all this
decision needs. *Rootless ⟹ resumable* does **not**: a trace ending after a
route that selected `END`, an aborted transition, a routing miss or an unsafe
external effect is rootless and refused by `_next_node` just the same. A
validator built on the stronger sentence would treat every rootless trace as
resumable.

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

### 1. The segment is what gets committed. The run is already committed, transitively, and that was argued away in the first draft

**Corrected. The first draft said a combined root "would require a value the
predecessor does not have" and therefore could not exist. That is wrong**, and
the mistake is worth more than the correction.

Segment N's root covers segment N's header. That header carries
`bundle_fingerprint`, a digest over segment N−1's whole bundle — which contains
segment N−1's header, which carries its own `bundle_fingerprint` if it was
itself a resume. **The binding is recursive.** Change anything in any earlier
segment and the final root moves. So a completed chain of segments *does* have
a single value committing to all of it, and it needed no predecessor root to
build: it is the last segment's root, and it already exists.

What is true, and is what the decision should have said:

- **the unit the commitment log records is the segment.** One `BEGIN`, one
  `END`, one root per segment, because that is what a run produces;
- **anchoring the last segment of a completed chain commits to the whole
  run**, transitively;
- **the transitive commitment is checkable only by holding every predecessor's
  bundle.** It binds to their *bytes*, not to any published value of theirs —
  which is not a weakness of the design but a consequence of the predecessors
  having no published value at all;
- **an unfinished chain has no run-level value.** If the last segment is the
  one that died, there is no root anywhere, which is decision 4's second claim
  and ADR-020's residual class again.

The receipt format must therefore be able to express **a chain**, not only a
segment — which is the practical thing the first draft would have got wrong,
and the reason this correction had to arrive before the format was frozen
rather than after.

### 2. `bundle_fingerprint` stays, and the trace schema does not change

Answered by measurement rather than preference: there is no root to bind to.
The `resume` block is correct as it stands and its
`additionalProperties: false` is left intact.

### 3. The link goes in the commitment: `BEGIN` carries `continues`

A `BEGIN` for a resumed segment carries:

```
continues: {"writer_id": <writer of the predecessor's chain>,
            "sequence":  <the predecessor's BEGIN, in that chain>,
            "run_id":    <source_run_id>,
            "bundle_fingerprint": <bundle:sha256:…>}
```

Absent on a `BEGIN` that starts a fresh run — the ADR-021 rule that a field
which does not apply is not written, so its absence is a fact and not a
default.

**`run_id` alone cannot name the predecessor, and the first draft used it
alone.** #82 established that a `run_id` may repeat: a retried job keeps its
id, so a chain can hold several unpaired `BEGIN`s under one `run_id`, and none
of them carries a bundle fingerprint to match against — a `BEGIN` is written
before anything is known, so it has no digest of its own segment. A link that
names only the `run_id` therefore identifies a *set* and claims to identify a
member, which is the failure mode `proof_for` already refuses in the same
codebase.

`sequence` is the coordinate that works: ADR-021 numbers **per writer and does
not restart at a window boundary**, so `(writer_id, sequence)` names exactly
one commitment in one chain. `writer_id` is carried explicitly rather than
assumed, because the ordinary cause of a resume is a process that died, and the
process that resumes is frequently a different writer.

**When the predecessor is not in a chain this store holds** — another machine,
another writer whose log we were never given — the link is recorded and
reported as *unresolved from here*. It is a claim about somebody else's chain,
and this store may not confirm or deny it. Reporting it as broken would be as
wrong as resolving it.

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
  recomputing `bundle_fingerprint`, and **the fingerprint is over the BUNDLE,
  not the trace**. `TraceBundle.fingerprint` digests the canonical bundle:
  `bundle_version`, the complete `graph_spec`, and the trace. Keeping the trace
  document and discarding the spec leaves the claim exactly as uncheckable as
  keeping nothing. The first draft said "that document" and was wrong by one
  artefact, which is the sort of error a retention policy inherits verbatim.

So: if segment 1's bundle is discarded under a retention policy, segment 2
remains fully verifiable **as a segment**, and its continuation claim becomes
*unverifiable* — not false, and not verified. A verifier that reports the whole
receipt as `VERIFIED` in that state has told the strongest available lie, and
one that reports it as broken has told a different one.

**What a retention policy must therefore keep**, if the chain is to stay
checkable: the whole bundle of every segment, not its trace alone.

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

**3. I argued a combined root was impossible, and it already existed.** Written
into decision 1 of the accepted text and reversed hours later. Having just
established the sharp fact that a resumable segment has no root, I reached for
it again one paragraph on, where a *different* question was being asked — what
binds a chain — and answered it with the same sentence. The binding was already
there, recursive through the nested `bundle_fingerprint`s, in code I had read
that morning.

The pattern: **a true and hard-won constraint, reused one question past where
it applies.** The first use was right; the second was the first use wearing the
authority of having been right. That is more dangerous than an ordinary
mistake, because the confidence is borrowed from something real.

**4. The link named the predecessor by `run_id`.** Written the day after #82
removed the uniqueness rule that would have made a `run_id` sufficient — my own
change, in my own commit, whose whole message is that a `run_id` identifies a
set and the sequence identifies the member. A `BEGIN` carries no digest of its
segment, so nothing else in the record could have disambiguated it either.

## Alternatives rejected

**A run-level receipt that verifies without the predecessors.** This is what a
combined root would have to be to be worth having, and it is the thing that
genuinely cannot exist: the predecessors publish nothing, so there is nothing
to check them against other than their own bytes. A receipt claiming to verify
a chain while holding only its last segment would be checking that the last
segment says what it says. Decision 1's transitive commitment is real and it is
not this.

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
