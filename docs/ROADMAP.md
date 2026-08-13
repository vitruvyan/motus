# Motus roadmap — from here to a protocol somebody else can build on

This exists because a decision taken in conversation is not a plan. Every phase
below names what ships, what it unlocks, what gates it, and — where there is
one — **the decision the founder owns**, so that nobody discovers halfway
through that they were waiting for each other.

Sequence is load-bearing. Sizes are estimates and say so.

---

## Where we are, in facts

*Last reconciled against the repository on 2026-08-13. This section is a
statement about the code, not about intentions; when it disagrees with the
code, it is this section that is wrong.*

- **0.10.0** is the released version, carrying phase 1 in full: the commitment
  log, the `BEGIN`/`END` lifecycle, the resume link, the anchoring-cadence
  arithmetic, and the commitment contract. Like 0.9.0 it ships with the
  cumulative performance gate **failing**, under ADR-018 §3 with all four
  conditions met — a documented exception, not a passing gate. Its per-release
  arm passes, and by an amount the measurement cannot resolve;
- **ADR-020** (the trust model) and **ADR-021** (the two interfaces and the
  accumulator) are ACCEPTED. ADR-020 was **corrected on 2026-08-13**: the
  residual class of `EXECUTION_CONTINUITY` was stated as one of its members,
  and is now *a run that reached no terminal record* — process death is one
  member, an abandoned stream driver is another. No mechanism changed; what
  changed is what a reader may assume about how often it happens;
- `src/vitruvyan_motus/commitments.py` is on `main` — the arithmetic only;
- **`src/vitruvyan_motus/commitlog.py` and the `BEGIN`/`END` lifecycle are on
  `main`** (#82). Four adversarial rounds, then five P1 findings from an
  automated review **after** those four rounds had run — every one
  reproduced, and two of them were code that passed its own test while
  breaking the promise the test was named after;
- **one root is anchored**, on TRON Nile, txid `6010ded8…`, block 70013920,
  memo carrying the full root. Demonstration only — there is still no
  verifier, so #51's hard constraint (verifying a trace against an anchor,
  always open) still has no code. Phase 2 is what closes that, and until it
  does the anchor proves something we cannot yet let anybody check;
- the runtime still does not import the commitment modules unless configured,
  and a subprocess test asserts it by inspecting `sys.modules`.

---

## Phase 0 — close what is open — **DONE, less the page**

1. ✅ triage the seven Codex findings on #77;
2. ✅ merge **#76 → #77 → #78** in order;
3. ✅ **release 0.9.0**, with the ADR-018 exception recorded in the release
   evidence and every failing ratio stated in the README;
4. ✅ anchor the demo root on TRON Nile. `demo/anchor_root.py` queries the
   chain before signing and refuses if the scanner cannot answer, so an
   ordinary re-run does not mint a second anchor for the same root. **Bounded,
   and the bound is stated because it is real**: the scanner reads the last 50
   transactions from the sender and does not paginate, so a retry made after
   50 later transactions from the same address would not see the original and
   would publish a duplicate (#84);
5. ⏸ the demo page on `vitruvyan.com/motus/attack` — **deferred by the
   founder**, not blocked. Brief in `docs/SITE_ATTACK_DEMO.md`, to be
   implemented by a separate agent in frontier.

**Shipped:** an anchored root, in a release. The page that demonstrates it is
the one piece outstanding, and it is a founder scheduling call.

---

## Phase 1 — the commitment log — **DONE, released as v0.10.0**

*Weeks. The only phase that touches the hot path.*

1. ✅ **`BEGIN`/`END` lifecycle in the runtime** — merged (#82). `BEGIN`
   durable before the first node executes; `END` on every terminal path. Four
   adversarial rounds, each of which found a defect in the previous round's
   fix. Two of those defects were the same shape twice — a commit placed where
   a failure leaks the lifecycle lock and wedges every later run — first on the
   `BEGIN` side, then on the `END` side;
2. ✅ **the local append-only store** — merged (#82). One chain per writer
   (ADR-021 §5), `flock`, crash recovery on bytes, and the fork case: a head
   that disagrees with a published checkpoint is reported, never repaired;
3. ✅ **checkpoint sealing**, and the interval — **shipped without a default,
   which ADR-021 permits and this measurement earns.** `vitruvyan_motus.sealing`
   derives the interval from a measured run-duration distribution and prices it
   in anchors per hour. Deriving it surfaced that the ADR's phrasing conflated
   two rates: sealing is local and protects nothing, since ADR-020 says
   *"checkpointed" means externally anchored, never merely written*. The rate
   that bounds the rewrite window is the **anchoring** one. And the arithmetic
   then says something nobody expects — runs measured in seconds need an anchor
   every few seconds, which no chain does at a price anybody pays, so **an
   anchor cannot give execution continuity to a run shorter than its own
   cadence.** That is what the witness is for, and it is the reason ADR-021 was
   right to refuse a number;
4. ✅ **contract work**: `commitment.v1`, `checkpoint.v1` and `receipt.v1`, with
   eight rules (C1, K1, K2, P1–P5) that **recompute** every digest rather than
   reading it back. `validate.py` re-implements the leaf, node and checkpoint
   digests instead of importing them — the contract is the authority, so a
   validator that called the implementation would agree with any drift. A test
   pins that it never imports `vitruvyan_motus`, and another compares both
   implementations on real objects;
5. ✅ **release 0.10.0** — tagged `v0.10.0` on the verified merge commit,
   under ADR-018 §3 with all four conditions met. The per-release arm passes at
   −1.5 %, −0.8 % and +0.3 % against v0.9.0 — **every one of them smaller than
   the measurement's own paired spread**, so the release note says the
   instrument cannot resolve this release's cost rather than claiming it has
   none. Real-workload share re-taken for this release, as §4 requires and an
   inherited number would violate: **0.053 %**.

**Unlocks:** `RETENTION`. Not yet `EXECUTION_CONTINUITY` — that needs phase 3.

**The lesson from #82 that outlives it**, and it should shape how the rest of
this phase is reviewed: after four adversarial rounds an automated reviewer
found five more P1s. Two were of the same kind — a promise verified against
its *mechanism* and never against its *scope*. The witness deadline test was
green while a hung witness held the whole process open for twenty seconds,
because the test measured the call and the promise is about the process. **Ask
what the sentence claims, not what the function does.**

**Also owed inside this phase:**

- ~~**#75**~~ — **settled.** ADR-023 (accepted, then corrected by four findings
  hours later) fixes the segment as the recorded unit, keeps `bundle_fingerprint`
  because a resumable segment provably has no root to bind to, and puts the link
  in the **commitment**: a resumed `BEGIN` carries `continues`, naming its
  predecessor by `(writer_id, sequence)` because a `run_id` names a set. It is
  inside the leaf digest, so an anchor covers it.

  What the correction changed: a run-level commitment is **not** impossible. The
  last segment's root already binds every predecessor transitively through
  nested `bundle_fingerprint`s, so **the receipt format must be able to express
  a chain**, not only a segment — which is why this had to be settled before
  point 4 and not after.

- **#75** (superseded above, kept for the reader who arrives from the issue) — a resumed run's segments are not chained to each other, so an
  anchor covers one segment. The roadmap has always placed this *during*
  phase 1 and the phase is now open; settling it after the formats are frozen
  would mean changing them again.

**The invariant to defend all through this phase:** unconfigured, Motus is
bit-for-bit the last release — 0.9.0 today. The moment that stops being true,
Motus has become a service and nobody decided it. It is currently enforced by a
subprocess test that runs a real graph and asserts no module whose name contains
`commit` was loaded; keep the enforcement mechanical, because this is exactly
the kind of invariant that erodes one convenient import at a time.

---

## Phase 1b — the integration MCP — **NOT STARTED**

*Days, in parallel with phase 1. Gated by nothing in the trust model: it touches
no receipt, no anchor, and promises nothing about audit. Its ADR — carrying the
derivation rule below — comes before any code.*

You install Motus, point your coding agent at the MCP, and start building
without reading three hundred lines of prose.

**Why this and not more documentation.** We have the measurement. Commit
`24adb30` is titled *"two examples for the part of the protocol a first
integration gets wrong"* — so the gap was known and the answer was two more
examples. An automated reviewer then read those examples and the Terraveler
brief and **inverted the effect classification twice in one pull request**
(#77): it called a database write `recorded_effect` where the protocol says
`external_effect`, and read-only calls the opposite. A protocol with
classifications does not travel in prose. It travels by answering the concrete
question somebody is holding.

**The rule that makes it impossible to rot, and it goes in an ADR before any
code:**

> Every answer is derived from a source in the repository at call time, and
> every answer cites that source. No prose lives in the server. A tool that
> cannot cite `contract/node-protocol.md`, a schema, or an ADR must not exist.

If the source changes the answer changes; if the source is deleted the tool
fails instead of inventing. A test asserts that every advertised tool returns
content that is actually in the repository — cheap, and it is what keeps this
honest across releases. **A server of hand-written summaries would be a second
source of truth, and in this project of all projects an MCP that lies about our
own protocol demonstrates the opposite of the thesis.**

**The surface.** Three of these execute rather than describe, which is the
advantage only Motus has here — the validator already exists:

- `motus_classify(description)` — *"I need to INSERT a row"* → `external_effect`,
  with the receipt requirements and the citation. This is the tool that pays for
  the whole thing, because it is the error we measured;
- `motus_review_graph(spec)` — the real validator, on their `GraphSpec`, with
  the rule that fired. Not advice: the verdict of the same code that will refuse
  their graph at runtime;
- `motus_review_node(source)` — a `pure` node reading a module-level global, an
  effect node with no idempotency key;
- `motus_explain(error)` — `DeclarationViolation`, `ReplayMismatch`,
  `UnsafeResume`: what the runtime means and what usually causes it;
- `motus_start_here()` — the shape of a Motus program in thirty lines;
- `motus_where(intent)` — where this code goes.

**Shipped as an optional extra**, `pip install vitruvyan-motus[mcp]`, no impact
on the runtime — the same house rule as ADR-021 decision 1: off until asked for.

**What it changes about the documentation.** The README does two jobs today —
convince a human to care, and teach an implementer — which is why it is eight
hundred lines and serves neither well. They separate: the README is what a human
reads to decide; the MCP is what an agent uses to build.

**Its first test is Terraveler**, who is integrating now. Handing them an MCP
instead of a three-hundred-line brief is the strongest evidence we can get, and
the brief is the document a review already found wrong in several places.

**The failure mode to watch:** the MCP becoming the place where documentation
gaps hide. A question the MCP answers well is evidence the *document* should
answer it too. Tool usage is a defect report against the docs, never a
replacement for them.

---

## Phase 2 — the verifier, before any anchor a receipt depends on

*Weeks. Out of order it turns the product into a promise.*

**The demo anchor of phase 0 is not a violation of this ordering, and the
distinction is worth being precise about rather than trusting to context.**
That anchor backs a page that says what it is; no receipt format depends on
it, no customer verifies against it, and nothing about it is sold. The rule
this phase protects is that **an anchor a third party is invited to rely on
must not ship before the check they would rely on it with** — which is #51's
constraint, and it is still ahead of us.

1. **`motus-validate receipt <trace> <receipt>`** — reports which of ADR-020's
   levels it could establish, and refuses on an unknown algorithm, network or
   attestation type. A `VERIFIED` on a chain the verifier cannot evaluate is the
   worst lie this system can tell;
2. `BEGIN` without `END` reported as *an execution that left no completion* —
   never as suppression;
3. **the public verifier page.** Drag a trace and its receipt onto a page, get a
   verdict, no account, no API of ours. This is the padlock in the address bar:
   it makes the property visible to people who do not read code, and it is worth
   more commercially than any dashboard.

**Why here and not later:** #51 fixed *verification is always open* as the hard
constraint. Shipping an anchor before its check means selling a claim nobody can
test — including us.

---

## Phase 3 — the plugs and the witness

*Weeks for the plugs; the witness is the first thing that might be a service.*

1. **`motus-anchor-opentimestamps`** — the production default. Free, Bitcoin,
   no wallet, no key custody, no gas, no treasury that runs dry on a Saturday
   night. Separate distribution;
2. **`motus-anchor-tron`** — kept for demonstration, because a memo is legible
   in a meeting and an `.ots` file is not;
3. **the witness wire protocol**, and a reference implementation, open source.
   This is what turns `EXECUTION_CONTINUITY` from a definition into a property:
   a `BEGIN` that left the operator's control before the outcome was known;
4. → **FOUNDER DECISION: does Vitruvyan operate a public witness?** Deferred
   deliberately in ADR-020. Running one is what makes the Let's Encrypt analogy
   a plan rather than a slogan — and it is the first standing infrastructure
   commitment the company would take on. Not answering is fine until phase 3;
   answering late is not.

**Unlocks:** `EXISTENCE` and, with a witness, `EXECUTION_CONTINUITY`.

---

## Phase 4 — 1.0.0, which means the format stops moving

*The milestone that matters for adoption, and it is NOT the last phase.*

`1.0.0` is not "feature complete". It is the promise that **a receipt written
today verifies in ten years**. Nobody integrates a receipt format that might
change, so this gates every external adopter — and it comes after phase 2,
long before the commercial half.

What it requires: the commitment, checkpoint and receipt formats frozen; the
`attestations` block reserved and proven extensible by actually adding one in a
branch; a compatibility corpus of receipts that later versions must keep
verifying.

---

## Phase 5 — the sold half

*Months, and the legal track must start earlier than the engineering one.*

1. **identity attestation (level 6)** — keys, rotation, and the revocation
   semantics ADR-020 reserved. *Revoked is not never-valid*, and proving a
   signature's validity at signing time needs level 7, so these two are one
   piece of work;
2. **qualified timestamp (level 7)** — integrate an existing QTSP. Becoming one
   is an accreditation with audits and capital requirements; **this is a
   commercial and legal track, and its lead time is the reason to start it
   during phase 3, not after phase 4**;
3. **retention and continuity as a service** — we hold the proofs for ten years
   and produce the verification report on demand, after the customer has changed
   stack;
4. **the monitor** — the free half writes the sequence; reading it for the
   customer and waking somebody at three in the morning when a gap appears is a
   product;
5. **private authority** — self-hosted witness and anchor for those who cannot
   let a hash leave the building.

**None of this closes a line of code.** That constraint is from #51 and it holds.

---

## Running in parallel

**The residues**, each needing its own ADR and its own round:

- **#75** — a resumed run's segments are not chained to each other, so an anchor
  covers one segment. Listed under phase 1 above, where it is now due: phase 1
  is open, and this touches the commitment design directly;
- **#74** — the root commits to numbers at binary64 precision;
- **#73** — a trace cannot say whether its evidence reached the sink;
- **#52** — `result_fingerprint`, superseding ADR-013.

**Engineering debt that will bite on schedule:** #70 (the SLO gate depends on
runner allocation), #66 (validator under a hostile switch interval, in CI),
#65 and #63 (run lifecycle — and phase 1 depends on that lifecycle being right),
#49 (distribution), #38 (H1), #40 (async resume).

**Terraveler** is the first external integrator and the proving ground. Its
brief is `docs/TERRAVELER_MOTUS_TRON.md`. Whatever it finds outranks whatever we
think.

**Documentation** tracks code and never leads it: README, `kb.vitruvyan.com`
(repo `vitruvyan-docs`), and the site. Documenting unshipped behaviour is the
one habit that would cost us the credibility the rest of this is built on.

---

## What would derail this

- **the commitment store turning Motus into a service.** Guarded by the phase 1
  invariant, and it will be under pressure the whole way;
- **anchoring before verifying** — phase 3 before phase 2. It would ship a claim
  with no check;
- **the QTSP lead time**, discovered during phase 5 instead of started in
  phase 3;
- **a witness that can block a run.** Refused in ADR-021 and it will be proposed
  again, reasonably, by somebody who wants a stronger guarantee;
- **`SYSTEM_COMPLETENESS`**, promised by anybody in a meeting. It is conceded in
  writing, permanently, and it is the property a buyer most wants;
- **an MCP that answers from memory instead of from source.** It would go stale
  silently, and it would be *our* server misstating *our* protocol — the exact
  shape of the thing this product exists to make impossible.
