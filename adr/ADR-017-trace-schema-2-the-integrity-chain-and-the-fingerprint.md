# ADR-017 — trace schema 2.0.0: the integrity chain, and the fingerprint it does not replace

- **Status:** ACCEPTED — founder, 2026-08-09
- **Date:** 2026-08-08
- **Authority:** founder decisions, 2026-08-08 — one schema revision rather than
  several; seals always rather than on request
- **Supersedes in part:** ADR-014 §6, which said the fingerprint "lands with
  schema 1.1". This ADR is the revision ADR-014 deferred the field's *name* to.
- **Advances:** #51 (chain, root), #52 (fingerprint recipe)
- **Tension, unresolved here:** ADR-012's release budget. See *The cost*.

## Context

Three open items each needed a schema change, and doing them one at a time would
have meant three revisions of a published contract. The founder's instruction
was a single revision.

While scoping it, two things turned out to be false — both stated by me, twice,
in ADR-014 and ADR-016:

**"They need schema 1.1."** Motus has emitted `schema_version: "1.1.0"` since
0.6. 1.1 is a version number that was never given content: `payload_hash` was
typed `null` unconditionally, so a 1.1.0 trace was refused a hash by the same
schema whose prose said 1.1 is where hashes become possible. Demonstrated rather
than asserted — the same trace, accepted clean, then given the chain 1.1
supposedly permitted:

```
la traccia dichiara schema_version: 1.1.0
il validatore la accetta cosi' com'e':  True
poi ci metto la catena di hash:         5 violazioni
  SCHEMA $.records[0].integrity.payload_hash: 'sha256:aaa…' is not of type 'null'
```

**"#42's remaining half belongs here."** It does not, and it is not achievable at
all. The terminal record is written *before* anyone knows whether writing it
succeeded, so it cannot report its own persistence. All three ways out are
closed: a record after the terminal is forbidden by T3; replacing the terminal
erases the primary cause, which ADR-016 rejects for good reason; and a terminal
asserting "every record before me was persisted" leaves the in-memory trace of a
refused terminal identical to a healthy one, which is the case #42 was about. It
is dropped, and that is a third less work rather than a deferral.

## Decision

### 1. Schema 2.0.0, and the version is the activation indicator

`payload_hash` and `prev_hash` change from `type: null` to hash-or-null. Which
one applies is decided by **rule T11**, not by the type: 2.0.0 requires the
chain, 1.0.0 and 1.1.0 require both null.

**There is deliberately no second flag.** A `chained: true` field could disagree
with the hashes actually present, and a reader would then have to decide which
to believe. The version already answers the question, and one answer cannot
contradict itself.

Keeping 1.x refusing hashes is the part that matters: an unverified hash must
never be able to ride in an old trace and pass for tamper evidence. That was the
original clause's instinct, and it survives — it was only its *prose* that had
become false.

### 2. The chain carries no state, and the root is derived

Each record's `payload_hash` is sha256 over the canonical object form of that
record **without its own integrity block** — a hash cannot cover itself — and
`prev_hash` is the preceding record's `payload_hash`. The first record's is
null: it has no predecessor, and a genesis constant would look like evidence
while carrying none.

**The chain starts at the header, not at the first record.** An automated
reviewer found that chaining records alone left `run_id`, `policy`, `metadata`
and `graph.code_fingerprint` outside the root: all four could be rewritten, the
terminal's `payload_hash` did not move, and the validator passed the result
clean. Reproduced before believing it:

```
run_id  : originale -> un-altro-run
policy  : strict -> exploration
radice invariata: True
il validatore: NIENTE — passa pulito
```

An anchor over that root proves a sequence of records existed and says nothing
about **whose** run they were, under **which** policy, of **which** graph — in a
product whose whole claim is the anchored root, that is a hole in the claim
rather than a detail. So the header's digest is the first record's `prev_hash`,
and `prev_hash` is never null: a null first link is the same hole with a name.

The previous hash is already in the previous record, so the chain needs no
accumulator. **A trace therefore carries its own chain and is re-checkable from
nothing but itself**, which is what makes an offline validator possible at all.

The **root is the terminal record's `payload_hash`** — derived, never stored.
A stored copy could only ever disagree with the derived one. An unfinished trace
has no root, which is the honest answer rather than a partial one: anchoring a
prefix publishes a value that a later complete trace contradicts.

### 3. T11 recomputes; it does not inspect shape

A chain checked only for shape — non-null, right length, right pattern — proves
nothing. An editor who alters a record recomputes a well-formed hash trivially.
What they cannot reproduce is the root that was anchored outside their reach.

So the validator recomputes every digest and every link. This is the difference
between a chain and a decoration, and it is the reason the anchor (#51 item 2)
is not optional to the story.

### 4. Sealing happens before persistence, and finding out why is the substance

The first implementation sealed inside `Trace._append_runtime`, which looked
like the correct single funnel — every record reaches the trace through it.

It is the wrong funnel. `_store` hands the record to the **sink** before the
trace appends it, so the artifact on disk received null hashes while the
in-memory trace received real ones. **Two accounts of one run that disagree are
worse than neither having a chain**, and the disagreement would have been
invisible to anyone holding only one of them.

`_store` now seals once, at the top, and the same sealed dict goes to the sink
and to the trace. The sink-failure records, built inside `_store`, are sealed
there too.

### 5. The fingerprint gets its name, its salt, and an honest guarantee

ADR-014 deferred the successor field's name to "the ADR that writes the schema
revision, next to the schema it belongs in." This is that ADR.

`interaction_fingerprint` — named for what it covers: **what was asked and what
came back, bound together**. `result_fingerprint` is not redefined in place; a
1.x value means what 1.x said it meant, forever.

`fingerprint_salt` accompanies it, and **rule T12** requires the pair to be
complete: a digest without its salt cannot be recomputed by the verifier holding
the archive, and a salt without a digest describes nothing.

**Neither is recomputable by the validator**, because the covered material is
deliberately not in the trace — that is the whole point of the field. So the
schema states the guarantee in the narrow form that is true: verifiable by a
party holding the trace **and** the archive together, and **a clean validation
never means the fingerprint was checked**.

Enforcing only what is enforceable, and publishing which half that is, is the
difference between a narrow guarantee and a misleading one.

### 6. The salt is described for what it does, not for what it sounds like

Recorded, therefore public. It gives domain separation — two runs holding the
same interaction produce different digests, so an archive cannot be correlated
across runs — and it defeats precomputation. **It does not confer
confidentiality on a guessable input**: a holder of the trace has the digest and
the salt together.

What makes that acceptable is decision 5's *subject*, not the salt: the digest
covers request and result together, and a free-text result is not guessable. A
deployment whose request and result are both low-entropy needs a keyed digest
and must choose it knowingly.

## The cost, and the rule it collides with

ADR-012 gates a release at **+10 %** against the previous one. Measured here,
interleaved against `main` on a busy host, the ratio ranged **1.27 → 2.04**, and
a profile confirms per-record canonicalisation is close to half of execution time
on the synthetic no-op graph.

**I estimated this cost wrong twice, in both directions** — first ~3 %, on a
premise that turned out false (the hashed object and the written line are
necessarily different objects, so their serialisations cannot be shared), then
~13 %. I am not offering a third estimate from this machine.

ADR-012 already says the right thing: *"a single job is an observation, never a
verdict."* The defensible number is the median of job ratios across at least
three dispatches, produced by the project's own interleaved harness. That
measurement is what the pull request exists to obtain.

It confirmed we are well over budget. Measured with that harness, three runs,
median of job ratios, against `main`:

```
noop_100_overhead_ms             1.955      budget 1.10
realistic_1000_us_per_node_min   1.690      budget 1.10
to_dict_min_ms_realistic         1.329      budget 1.10
```

Optimisation was attempted before an exception was asked for, and is recorded in
*The cost* above: the best available is 1.955 on the synthetic metric, down from
2.462, and almost nothing on the realistic one.

**Founder decision, 2026-08-09: declared, not accommodated.** The three ratios
go into ADR-012's own `DECLARED_EXCEPTIONS`, keyed to the 0.8.0 release, each
with the reason and a hypothesis to falsify — the mechanism ADR-012 provides for
exactly this, and the opposite of widening the budget. ADR-012 names that
"re-baselining under a different name", and not bending our own rules is the
entire product.

The version bump itself is not in this change. A release's identity is coupled
to characterization evidence produced on the reference runner class, which is a
CI artifact and cannot be written here; the exceptions are declared in advance
so the release does not have to invent them under time pressure.

The gate measures a 100-node graph of no-op nodes, which is deliberately the case
where the chain costs most relative to work done and matters least in practice —
against a node that calls a model, two milliseconds of hashing is invisible. That
observation is an argument to be made in the open, not a reason to skip the
measurement.

## Consequences

- 574 tests, 9 examples, 131 contract fixtures.
- Two rules published: **T11** (the chain) and **T12** (the fingerprint pair),
  each with a negative fixture, because a rule with no fixture is a rule nothing
  proves.
- Fixture 15 is repointed from SCHEMA to T11 and renamed for what it now proves:
  a hash inside a 1.x trace. Six new fixtures, all generated from real runs.
- **Every fixture that edits a record must now re-seal the chain**, and the
  corpus's rule-purity assertion is what surfaced that. It is the chain working:
  an edit that is not re-sealed is a T11 violation, so a fixture testing
  something else has to be honest about not tampering.
- Two tests adjusted rather than accommodated. The sync/async equivalence
  normaliser drops `integrity`, because the chain is derived from fields that
  test already declares legitimately different. ADR-014's fingerprint tripwire
  re-seals after tampering, because otherwise it fired on T11 and would have
  read as though the field had gained a recipe — and an editor who can re-seal
  still passes it, which is exactly why the chain is not a substitute for a
  recipe.
- The runtime populates the chain. It does **not** yet populate
  `interaction_fingerprint`: that needs the adapter side, and the field is
  defined now so the revision happens once.

## Alternatives rejected

**Chaining on request, off by default.** Fits the release budget trivially and
was the founder's rejected option. The claim degrades from "a Motus trace is
sealed" to "can be sealed", and the first reviewer asks the only question that
matters: *and when it is not?* An evidence product cannot answer that well.

**A separate `chained` header flag as the activation indicator.** Two sources of
truth for one fact, and the schema version already answers it.

**Storing the root as a field.** A derived value stored twice is a value that can
disagree with itself. The terminal's hash already covers everything.

**Reusing one serialisation for the hash and the written line.** Measured, and it
was *slower*: the hashed object excludes the integrity block and the written line
includes it, so they are different objects. Recorded because it was my premise
for the "~3 %" figure that informed the decision.

**Defining the fingerprint fields in a later revision.** Two revisions of a
published contract to save writing two properties now. The founder's instruction
was explicitly one revision, and it is the right one.
