# ADR-015 — `State.snapshot()` is a bulk read, and is recorded as one

- **Status:** ACCEPTED
- **Date:** 2026-08-08
- **Accepted:** 2026-08-09 by the founder, having accepted with it that traces already
  archived stop verifying — the project certifies nothing yet, and this is the
  last window in which that answer is available
- **Authority:** founder decision, 2026-08-08, after a hostile round found the
  defect and the CTO reproduced it on a credit-assessment graph
- **Amends:** no contract text. §3.1a and §3.2 of `contract/node-protocol.md`
  already required this; the code did not do it. This ADR records a behaviour
  change in the shipped runtime, not a change of rule.
- **Related:** #45 (what §1.2 can and cannot enforce) — a different defect with
  a superficially similar shape. See *Not this ADR* below.

## Context

`contract/node-protocol.md` §3.1a enumerates the readable surface and includes
"whole collections by scan". §3.2 is unambiguous about what a declaration
covers:

> **"Captured reality" means every read, of every origin kind** — there is no
> exempt corner of the readable surface … a scanned collection is declared by
> the collection's name … A node that touched something undeclared owes a
> violation for it whatever shape the touch had.

The parenthesis that follows records why the sentence is that emphatic: a
previous cross-review found `absent`, `scan` and `header` reads exempted from
the comparison, "which let a node probe an undeclared key and still publish an
empty violations list — a declaration that covers three quarters of the reads
is not a declaration."

`State.snapshot()` returned the entire committed state — every fact, decision
and rejection — and recorded nothing at all. So the exemption the earlier review
closed was still open through a different door, and this one was wider: not one
probed key, the whole file.

Reproduced on a loan assessment. A node declaring `reads_declared: []` calls
`snapshot()`, receives the applicant's national identifier along with everything
else, and puts it in its output. The trace:

```
QUELLO CHE DICE LA TRACCIA
  riassunto  letture registrate: nessuna    violazioni: nessuna

QUELLO CHE È SUCCESSO
  riassunto  ha visto: codice_fiscale, debiti, merito, reddito

IL TESTO PRODOTTO
  Richiedente BLDDVD80A01H501K: merito 0.73
```

The run completed. The identifier is in the product and the evidence says nobody
looked at it — the exact reading a credit file cannot survive.

**The mechanism was never missing.** `_scan` recorded a bulk read correctly, and
the same state therefore had two doors into one room with a guard on only one:

```
state.facts        letture: [{"key": "facts", "origin": {"kind": "scan"}}]
                   violazioni: [undeclared_read: facts]   →  the run stops

state.snapshot()   letture: []
                   violazioni: nessuna                    →  the run passes
```

The one that reads more is the one that passes.

## Decision

### 1. `snapshot()` records a scan of each of the three collections

Called from inside a node it records `facts`, `decisions` and `rejections` as
scan reads, exactly as the corresponding properties do. Reading the whole state
stays **permitted** — a node that summarises everything known so far is a real
thing to want, and the obvious shape for a node backed by a model. It is no
longer *silent*.

An honest declaration already worked before this change and needed no new API:
`reads_declared: ["facts", "decisions", "rejections"]` passes. That is strictly
more information for a reviewer than the old `[]`, which was both smaller and
false.

### 2. All three collections, always — not the keys present, not the non-empty ones

Recording only the non-empty collections is tidier and wrong. It would make the
required declaration depend on the **data**: the same node against a state that
has no decisions yet would need a different declaration than the same node one
run later.

**A declaration that varies with the data is not a declaration.** So the reads
describe what the code asked for, which is everything.

Recording the individual keys present is wrong for the same reason, and the
schema already says so — `scan` is documented as "coarse by nature: a scan
depends on everything in the collection at that point."

### 3. One method owns the recording

Both surfaces now route through `State._note_scan`. The defect was drift between
two implementations of one idea, so the fix removes the possibility of drifting
again rather than adding a second place to remember.

A test asserts the two record an *identical* read rather than asserting each
side's output separately — two independent assertions can both stay green while
the behaviours diverge.

### 4. Now, in 0.7.x, as a fix

This breaks callers: a node that calls `snapshot()` under a declaration stops
where it used to pass. Inside Motus there are zero such callers — all three call
sites are host-side, which is why 544 existing tests passed unchanged both
before and after. Outside, there is one consumer.

**Now is the cheapest this will ever be.** With one consumer it is a line of
declaration; in six months it is a migration. The founder's call was explicit.

## Consequences

- 549 tests (up from 544), five of them new. Nine examples green. No existing
  test changed — and the fact that none of them caught this is itself the
  finding: a whole-state read left no trace to assert against.
- Two mutation probes confirm the tests are load-bearing. Neutralising the
  recording fails three; making it record only non-empty collections — the
  rejected alternative — fails two, so the **decision** is pinned and not just
  the code.
- Host-side behaviour is unchanged: `_reads` is None outside an attempt, so
  serialising for storage or replay still records nothing. Inventing reads no
  node performed would be the same lie in the opposite direction, and a test
  pins that too.
- Terraveler must be told. Any node calling `snapshot()` needs its declaration
  updated in the same change. The delivery route is likely PyPI, which is #49.

## It also breaks traces already archived, and that is accepted

An adversarial round found what §4 above did not say. The break is not only to
callers: **`verify()` now accuses an already-archived trace of a divergence that
never happened.**

Same trace, same node source, verified with each version:

```
con 0.7.0     verify() -> OK, verificati: (('riassunto', 3),)
col ramo      verify() SOLLEVA ReplayMismatch:
              verify replay diverged at node 'riassunto', record 3, field reads
```

Replay re-executes the `pure` node under the new recording rule, gets three scan
reads where the archived trace has none, and compares with `!=`. The message
names the node and the field, so a reader takes it for tampering or code drift.
Neither occurred; only Motus's recording rule changed.

For a product whose thesis is checkable evidence this is the worst shape of
failure — **a false accusation** — and it cannot be made selective: the trace
header carries `schema_version` and `graph.spec_schema_version`, no Motus
version, so `verify()` has no way to know it is looking at an older trace.

**Founder decision, 2026-08-08: accepted.** Archived traces are deleted. The
project is in development and certifies nothing, so there is no evidence whose
loss costs anything.

Worth stating because it expires: **this is the last window in which that is
true.** The first real deployment closes it permanently. That is an argument for
making the breaking changes now, together, rather than discovering later that
each one needs its own migration.

What the round exposed underneath is the durable finding: **nothing in the suite
verifies an archived trace against a later version.** F3 is unlikely to be the
only instance of what that would catch.

## Not this ADR

**#45 is a different defect with a similar silhouette**, and conflating them
would lose both. There, §1.2 prohibits nodes passing data through shared
module-level objects and no mechanism can observe it — that is a limit of what
is knowable in Python, and the honest fix is to correct the contract's claim of
"structural" enforcement.

Here, nothing was unknowable. The read crossed Motus's own boundary, through
Motus's own API, and Motus recorded it as not having happened. **One is a
boundary; the other was a false record.** This ADR closes the second and touches
the first not at all.

## Alternatives rejected

**Forbid `snapshot()` inside a node.** Defensible — it is a serialisation method
and serialisation is a host concern. Rejected because the bulk read is
legitimate and the contract already permits it with a record: `state.facts` has
always been allowed to sweep a collection. Forbidding one bulk read while
allowing another, for no reason a caller could infer, trades a false record for
an arbitrary rule.

**Record the keys actually returned.** More precise, and it would make a
declaration break whenever an upstream node starts writing a new key — coupling
every summarising node to every producer in the graph. The schema's own word for
a scan is "coarse", deliberately.

**Document the gap and leave the code.** This is the option this project has
correctly taken before, and it is wrong here. The failure mode ADR-014 was
written to prevent is a field that reads like proof and is not; `reads: []` on a
node that read everything is that failure in its purest form.
