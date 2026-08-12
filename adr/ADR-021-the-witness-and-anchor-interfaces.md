# ADR-021 — two interfaces, an accumulator, and a commitment log that is off until asked for

- **Status:** PROPOSED — awaiting the founder
- **Date:** 2026-08-12
- **Authority:** CTO, under ADR-020's directive that the trust model states
  conditions and this ADR chooses mechanisms
- **Depends on:** ADR-020 (every term used here is defined there), ADR-019 (the
  derived root is what a commitment carries)
- **Advances:** #51 item 3, and the row #51 calls the hard constraint —
  verifying a trace against an anchor — which has had no code since 2026-08-07
- **Amends:** nothing. Everything here is additive and inert until configured.

## Context

ADR-020 fixed the vocabulary and refused to choose mechanisms. It left four
questions, and each has exactly one defensible answer once the constraints are
written down together:

1. how a commitment reaches a proof without disclosing every other commitment;
2. what a witness is, as a type, given it must never block a run;
3. what an anchor is, as a type, given that no anchor ships here;
4. what happens to a Motus that never configures any of it.

Question 4 governs the other three. Motus is a library that runs inside somebody
else's process. A library that acquires a persistent cross-run store, a tenant
identity and a network dependency **by default** has become a service, and
nobody decided that.

## Decision

### 1. Everything here is off unless configured, and off means bit-for-bit today

No commitment log configured → no `BEGIN`, no store, no tenant, no witness, no
anchor, and a runtime whose behaviour is byte-identical to 0.8.1's. The trace,
its chain and `Trace.root` are unchanged and remain complete evidence on their
own.

This is not a migration convenience. It is the property that keeps Motus
embeddable, and it is testable: the existing suite must pass unmodified.

### 2. The accumulator: a Merkle tree per window, and a chain between windows

A linear hash chain alone makes an inclusion proof O(n) and — the reason that
decides it — **discloses every other commitment in the window**. Proving that one
credit decision happened would reveal how many others did. Therefore:

- **within a checkpoint window**, commitments are leaves of a Merkle tree. A
  proof of inclusion is O(log n) and reveals sibling *hashes* only;
- **between windows**, checkpoints are chained:
  `CP(n) = H(root(n) ‖ CP(n-1) ‖ meta(n))`. This is what makes a whole window
  impossible to drop after the fact;
- **the checkpoint commits to its leaf count.** A window whose tree is rebuilt
  with a leaf removed changes both root and count, and the count is what a
  verifier can compare against a witness's independent tally.

Each structure does one job, and saying which is half the value:

```
Merkle tree      this commitment belongs to this window
checkpoint chain this window follows that window
external anchor  this checkpoint existed no later than T
witness          this commitment existed before its own outcome did
```

**The tree solves proof size and disclosure. It does not solve custody.** A tree
built at window close is built by the operator, who can omit a leaf and produce
a perfectly consistent tree — *Wrong turns 4* of ADR-020, one level down. Only
the witness closes that, which is why the two are separate decisions and not one.

### 3. The witness interface

```python
class Witness(Protocol):
    def acknowledge(self, commitment: bytes) -> WitnessAck | None: ...
```

- it is called with the canonical bytes of a `BEGIN`, **before the first node
  executes**;
- it returns an acknowledgement, or `None`, or raises. **All three mean the same
  thing to the runtime: continue.** A witness that is slow, absent, broken or
  hostile downgrades the run's assurance mode to `LOCAL` and changes nothing
  else. There is no configuration that makes a witness able to stop a run,
  because that configuration would eventually be used;
- the acknowledgement carries the witness's identity, its own sequence position
  and a signature. Motus verifies the signature if it holds the key, records it
  regardless, and **never asserts the witness is independent** — decision 7.

A deadline applies and is part of the configuration; expiry is a downgrade, not
an error.

### 4. The anchor interface

```python
class Anchor(Protocol):
    def publish(self, checkpoint: bytes) -> AnchorReceipt: ...
    def state(self, receipt: AnchorReceipt) -> Literal["pending", "anchored"]: ...
```

- it is called with a **checkpoint**, never with a run and never with a trace.
  Anchor submissions scale with checkpoints per tenant and are independent of
  how many decisions were taken;
- `state` exists because ADR-020 requires a receipt to declare `pending` or
  `anchored`: an OpenTimestamps proof is incomplete until upgraded, and a
  verifier reporting `VERIFIED` for a pending proof is stating something false;
- `publish` may be called again for the same checkpoint. Anchoring twice to two
  networks is the recommended posture, not an edge case.

**No implementation of either Protocol ships in `vitruvyan-motus`.**

### 5. Concurrency: one chain per writer, not one per tenant

Two processes serving one tenant cannot share a chain head without a lock on
every run start, which is a scaling ceiling disguised as a correctness measure.

Instead: **each writer holds its own chain**, identified by a `writer_id` it
declares. A checkpoint commits to the **set of writer heads** it observed. Gap
detection stays per writer, where sequence numbers are unambiguous, and a
writer that appears in one checkpoint and vanishes from the next is itself a
question a verifier can raise.

The cost is stated plainly: a writer that never checkpoints is invisible, and
**the set of writers is asserted by the operator**. This bounds continuity to
declared writers and is exactly `SYSTEM_COMPLETENESS` reappearing one level
down — conceded in ADR-020, conceded again here.

### 6. Tenant and writer are opaque strings the embedder supplies

Motus mints neither. It has no notion of an account, and inventing one would
make it a service. Both are recorded verbatim in every commitment and must be
stable; the ADR states no format because any format we chose would be wrong for
somebody's estate.

### 7. What a receipt must carry, or it proves the wrong thing

```
run root (ADR-019 derived)  +  the BEGIN and END commitments
Merkle path to the window root
the checkpoint and its link to the previous checkpoint
zero or more anchor attestations, each with network and state
zero or more witness acknowledgements
the assurance mode actually achieved
```

A receipt carrying only the checkpoint and the transaction **proves a checkpoint
and not a run**. A receipt whose mode says `WITNESSED` without an acknowledgement
is malformed, not optimistic.

### 8. The verifier fails closed, and says which level it reached

`motus-validate receipt <trace> <receipt>` reports, per ADR-020's levels, what
it could establish — and refuses rather than guesses:

- an unknown hash algorithm, anchor network or attestation type → **refuse**. A
  `VERIFIED` on a network the verifier cannot evaluate is the worst lie this
  system can tell;
- a `pending` anchor → `INTEGRITY` established, `EXISTENCE` **not yet**;
- `BEGIN` with no `END` → reported as an execution that left no completion.
  Never as suppression (ADR-020 decision 3).

It needs no network for `INTEGRITY`, and for `EXISTENCE` it needs the chain —
not us.

## Consequences

- **a second persistent artifact** beside the trace, with a lifetime longer than
  a run. Its loss costs `RETENTION` and `EXECUTION_CONTINUITY` for the affected
  window and costs the traces nothing — they remain valid evidence alone, which
  is why the two stores are separate;
- **`BEGIN` is on the hot path** of every run start, synchronously, by
  construction. A local append is microseconds; a witness call is not, which is
  why its deadline is configuration and its expiry is a downgrade;
- **restoring an old backup of the commitment log** produces a writer whose head
  disagrees with a published checkpoint. This is detectable and must be reported
  as what it is — a fork — rather than repaired silently;
- **an operator can still declare fewer writers than they run.**

## Alternatives rejected

**One chain per tenant.** Correct, simple, and it serialises every run start in
the deployment. Motus would be slower the more a customer uses it, which is the
worst possible shape for that curve.

**Putting commitments in the trace.** They are cross-run and the trace is
deliberately self-contained: a trace must stay verifiable by somebody holding
one file, and it must not grow a dependency on a store they were never given.

**Letting a witness block a run.** Offered as a configuration flag for
"high-assurance" deployments, and refused: an option that stops decisions when a
third party is unreachable will be enabled by somebody who has not thought about
the outage, and the escape hatch — run anyway — reinstates the hole invisibly.
Assurance is recorded, never enforced by refusal.

**A Vitruvyan-operated chain.** A chain we run is a database we control, and
ADR-020 binds verification never to require our company. What that proposal
reaches for is per-tenant sequencing at low latency, which is decision 3 and
needs no chain of its own.
