# Motus roadmap — from here to a protocol somebody else can build on

This exists because a decision taken in conversation is not a plan. Every phase
below names what ships, what it unlocks, what gates it, and — where there is
one — **the decision the founder owns**, so that nobody discovers halfway
through that they were waiting for each other.

Sequence is load-bearing. Sizes are estimates and say so.

---

## Where we are, in facts

- **0.8.1** is the released version. Trace schema **3.0.0** and the derived
  `Trace.root` (ADR-019) are written and **not merged**: PRs #76 → #77 → #78 are
  stacked, CI green on all three, blocked on unresolved review threads;
- **ADR-020** (the trust model) and **ADR-021** (the two interfaces and the
  accumulator) are ACCEPTED;
- `src/vitruvyan_motus/commitments.py` exists — the arithmetic only. No runtime
  path, no store, no network. The runtime does not import it, and a test asserts
  that;
- **nothing is anchored anywhere.** #51's hard constraint — verifying a trace
  against an anchor, always open — still has no code.

---

## Phase 0 — close what is open

*Days. Nothing below can start cleanly while this is in flight.*

1. triage the seven Codex findings on #77 — reproduce each, fix the real ones,
   resolve the threads with reasons;
2. merge **#76 → #77 → #78** in order;
3. **release 0.9.0.** ADR-006 couples the version to committed baseline
   evidence, so the bump belongs to a release that regenerates it — schema
   3.0.0 is a breaking contract revision and cannot ride 0.8.1;
4. anchor the demo root on TRON Nile and regenerate the demo payload. **Blocked
   on one founder action**: a faucet click at `nileex.io`;
5. the demo page lands on `vitruvyan.com/motus/attack` (frontier, separate
   agent, brief in `docs/SITE_ATTACK_DEMO.md`).

**Ships:** an anchorable root, in a release, with a page that demonstrates it.

---

## Phase 1 — the commitment log

*Weeks. The only phase that touches the hot path.*

1. **`BEGIN`/`END` lifecycle in the runtime.** `BEGIN` durable before the first
   node executes; `END` on every terminal path — success, failure, refusal,
   cancellation, exhausted retry. The hook exists: `bind(header)` →
   `TraceSink.open_run`. **Adversarial round before it lands**, non-negotiable:
   this is the first Motus code that can lose a run's evidence by being slow;
2. **the local append-only store.** One chain per writer (ADR-021 §5). Crash
   recovery, restart, and the fork case — a restored backup whose head
   disagrees with a published checkpoint is reported as a fork, never repaired
   silently;
3. **checkpoint sealing**, and the interval. ADR-021 refuses to name a number
   until it is derived from run-duration distribution against cost per anchored
   checkpoint. **Measure first, then choose, or ship without a default**;
4. **contract work**: schemas for commitment, checkpoint and receipt; the
   `validate.py` rules that recompute them. Lands together, per the schema's own
   wording, or not at all;
5. **release 0.10.0.**

**Unlocks:** `RETENTION`. Not yet `EXECUTION_CONTINUITY` — that needs phase 3.

**The invariant to defend all through this phase:** unconfigured, Motus is
bit-for-bit 0.8.1. The moment that stops being true, Motus has become a service
and nobody decided it.

---

## Phase 1b — the integration MCP

*Days, in parallel with phase 1. Gated by nothing in the trust model: it touches
no receipt, no anchor, and promises nothing about audit.*

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

## Phase 2 — the verifier, before any anchor exists

*Weeks. Out of order it turns the product into a promise.*

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
  covers one segment. This one touches the commitment design directly and should
  be settled *during phase 1*, not after;
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
