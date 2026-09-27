# Motus roadmap — from here to a protocol somebody else can build on

This exists because a decision taken in conversation is not a plan. Every phase
below names what ships, what it unlocks, what gates it, and — where there is
one — **the decision the founder owns**, so that nobody discovers halfway
through that they were waiting for each other.

Sequence is load-bearing. Sizes are estimates and say so.

---

## Delivery discipline from 0.18.0

The phases later in this document remain the architectural history and long-term
protocol roadmap. The two delivery roadmaps below govern the regulatory core and
its integrations from 0.18.0 onward.

- One core roadmap point produces one release tag. Combining points or skipping
  a tag requires an explicit founder decision recorded here before implementation.
- Every new Motus semantic surface starts with a dedicated ADR, remains
  contract-first, and is released only after independent verification,
  adversarial closure, and green Jenkins evidence.
- Core artifacts remain product-, transport-, and jurisdiction-neutral.
  Integrations consume Motus contracts; they do not redefine them.
- A UI is a view over verified Motus evidence. Its presence does not prove that
  retrieval, binding verification, or runtime wiring exists behind it.

The history before this rule is intentionally not rewritten: v0.15.0 carried
three regulatory points, v0.16.0 was not tagged, and v0.17.0 carried Human
Oversight plus the Regulatory Evidence Profile. From v0.18.0 the one-point,
one-tag rule is restored.

## Regulatory core delivery roadmap

| Order | Capability | Status | Release |
|---:|---|---|---|
| 1 | Canonical Evidence API | DONE | shipped in v0.15.0 |
| 2 | Regulatory System Manifest | DONE | shipped in v0.15.0 |
| 3 | Risk & Control Registry / ControlApplication | DONE | shipped in v0.15.0 |
| 4 | Human Oversight Receipt | DONE | shipped in v0.17.0 |
| 5 | Incident / CAPA Ledger | DONE | shipped in v0.18.0 |
| 6 | Retention & Legal Hold | DONE | shipped in v0.19.0 |
| 7 | AI System Registry | DONE | shipped in v0.20.0 |
| 8 | Regulatory Evidence Profile | DONE EARLY | shipped in v0.17.0 |
| 9 | Regulatory Evidence Dossier / export | AWAITING PUBLICATION | tagged candidate v0.21.0; draft not published |
| 10 | Verification and query API/CLI | AWAITING PUBLICATION | tagged candidate v0.22.0; draft not published |
| 11 | Motus UI | NEXT; DRAFT EXISTS | release target to be decided |

The ordering remains semantic even though point 8 shipped early. Incident/CAPA,
retention/legal hold, and the AI System Registry must not be smuggled into the
already released Regulatory Evidence Profile. The profile may map only evidence
kinds Motus actually owns; adding each missing kind requires its own ADR and
release first.

### Point 5 / v0.18.0 — Incident / CAPA Ledger — shipped

Motus 0.18.0 defined the neutral boundary between:

1. an incident declaration;
2. corrective and preventive actions linked to that incident; and
3. independently verifiable Motus evidence referenced by either.

It must not infer blame, legal reportability, remediation effectiveness,
compliance, or closure merely from the presence of a record. Exact semantics,
identity, append-only and amendment behavior, execution bindings, and stop
conditions are governed by founder-accepted ADR-039.

### Point 6 / v0.19.0 — Retention & Legal Hold — shipped

Motus 0.19.0 defines the neutral boundary between:

1. a producer's retention-policy declaration;
2. a producer's legal-hold declaration and immutable scope snapshots;
3. separately verifiable evidence that a custodian applied preservation,
   disposal or deletion-blocking operations; and
4. bounded custody observations that never inherit stronger meaning from an
   anchor, receipt, policy or UI response.

It must not select applicable law, infer legal authority, treat a declared
deadline as permission to delete, or redefine ADR-020 `RETENTION` as proof of
continued custody. Exact semantics, identities, append-only history, scope
resolution, subset-scoped blocker findings and stop conditions are governed by
founder-accepted ADR-040 and implemented by the v0.19 retention contract and
read-only verification surfaces. Motus never emits disposal clearance.

### Point 7 / v0.20.0 — AI System Registry — shipped

Motus 0.20.0 defines the neutral boundary between:

1. one immutable registration claim bound to one exact System Manifest;
2. separate append-only lifecycle event claims; and
3. bounded registry snapshots over exact supplied records.

The released surface records producer claims and verifies their exact identities,
lineage and supplied bindings. It does not decide whether a subject is legally an
AI system, in scope, deployed, current, approved, registered with an authority,
safe, compliant or completely inventoried. A supplied chain or snapshot never
becomes proof of global current state or completeness.

The founder accepted ADR-041 on 2026-09-25. Contract-first implementation and
the v0.20.0 release completed without weakening the decision's stop conditions.

### Point 9 / v0.21.0 — Regulatory Evidence Dossier / export — release candidate

Motus 0.21.0 defines the neutral boundary accepted in ADR-042 between:

1. one immutable dossier manifest naming an exact bounded set of Motus artifacts;
2. the semantic fingerprints of recognized artifacts;
3. the SHA-256 digests of the exact bytes carried for transport; and
4. one deterministic byte-preserving export archive.

The candidate surface composes existing Motus validators and the exact Regulatory
Evidence Profile included by the producer. It does not infer global completeness,
legal sufficiency, official submission, regulator acceptance or compliance. It
does not change the existing execution evidence-package wire format, accept
arbitrary attachments as verified Motus evidence, fetch hidden evidence, or
rewrite member bytes under their old identities.

The founder accepted ADR-042 on 2026-09-26. Contract-first implementation,
review, qualification and tagging completed without weakening the decision's
stop conditions. ADR-032 keeps the release open until the founder publishes the
draft and the coupled PyPI/index/hash verification succeeds; that publication
is not currently authorised.

### Point 10 / v0.22.0 — Verification and query API/CLI — release candidate

ADR-043 proposes one transport-neutral, read-only Python facade and a distinct
CLI over the validators and domain verifiers Motus already owns. Artifact kind,
operation, companion material and collection scope remain explicit. Query means
a deterministic projection over an exact caller-supplied set; it is not storage
retrieval, hidden discovery, a global-current view, a general query language or
a compliance verdict.

The founder accepted ADR-043 on 2026-09-26. Contract-first implementation,
independent review, adversarial remediation, release qualification and tagging
completed without weakening the decision's stop conditions. The annotated
`v0.22.0` tag points at the Jenkins-verified release merge. The completed
workflow-run artifact is the authenticated distribution authority; separate
editable copies remain on a draft GitHub Release and become authenticated only
after the publication workflow compares them byte-for-byte with that artifact.
ADR-032 keeps the release open until the founder publishes the draft and the
coupled PyPI/index/hash verification succeeds; that publication is not
currently authorised.

### Existing Motus UI draft

A Motus UI draft already exists at `vitruvyan.dev`. It is authenticated and
partially wired to existing Motus continuity endpoints:
Perpetuum overview,
anchors, and retention use server-side proxies to query the Orbis graph
backend. Most console sections remain placeholders, Continuum does not yet
expose the full GraphSpec plus ordered trace contract, and the v0.14-v0.17
regulatory surfaces are neither represented nor wired. The draft must not be
treated as proof that the underlying capabilities are integrated.

After the neutral verification/query interface is stable, the UI must be
updated to represent the new evidence types and wired to real Motus outputs.
At minimum it must preserve the distinctions between declaration, evidence,
binding status, and regulatory mapping, and it must never translate `matched`
into a compliance verdict.

## Integration delivery roadmap

Integration work is tracked separately from the core release sequence. It may
run in parallel only after the relevant Motus public contract is stable.

| Integration | Status | Boundary |
|---|---|---|
| Orbis ↔ Motus receipt/evidence bridge | QUALIFIED | Orbis retrieves and exposes Motus-owned evidence; live qualification against Motus v0.22.0 passed on 2026-09-27 without recreating verifier semantics |
| Limen ↔ Motus evidence bridge | PLANNED | Limen consumes the same canonical evidence boundary without a private format |
| SDK / adapters for third-party stacks | IN PROGRESS; TARGET v0.23.0 | ADR-044 accepted; adapters translate transport and storage only, while Motus remains semantic authority |
| Orbis evidence UI | IMPLEMENTED; LIVE UI QUALIFICATION PENDING | source, proxy and component tests exist; the authenticated browser flow still needs separate live qualification |
| Motus UI wiring | PARTIALLY WIRED; REGULATORY INTEGRATION READY TO START | extends the existing continuity paths with real regulatory evidence retrieval and verification |

An integration being reachable, visually complete, or deployed does not prove
that it is wired to the current Motus release. Qualification must separately
identify the Motus version, the API/bridge path, executed verification, and the
UI state shown to the user.

### Orbis bridge qualification — 2026-09-27

The deployed Orbis graph service on commit
`7d32a5e8d685e2e05320208721b46559401d3138` imports Motus `0.22.0` and exposes
the read-only receipt and verification routes introduced by Orbis PR #249. A
live request for `orbis/api_graph/116` retrieved a receipt bound to that exact
BEGIN-located execution reference and passed the stored evidence package to
Motus's ADR-043 verifier. `INTEGRITY` was `matched`; the overall outcome was
correctly `not_verified` because no external anchor, retention guarantee,
continuity acknowledgement, signature, legal-identity binding, or qualified
timestamp was supplied. The bridge did not collapse that result to a boolean
or call it compliance.

The same probe confirmed `401` without the service credential, `409
other_writer` outside the serving writer, and an unchanged SHA-256 manifest of
all 371 commitment and trace artifacts before and after retrieval and
verification. The detailed, credential-free evidence is recorded in
`.factory/tasks/done/013-orbis-evidence-bridge-qualification/REPORT.md`.

This qualifies the backend bridge only. It does not qualify the authenticated
browser flow, the Orbis UI state shown to a user, Limen, a third-party adapter,
or Motus's own UI draft.

### Third-party adapter profile / v0.23.0 — in progress

The founder accepted ADR-044 and assigned the adapter profile and conformance
kit to Motus v0.23.0 on 2026-09-27. This is an integration release: it adds a
versioned transport-neutral profile, neutral conformance corpus, standard-library
runner and in-process reference example over the existing Evidence API and
ADR-043 interface. It does not add another verifier, network server,
authentication model, storage schema, generated language client or runtime
dependency.

The Python distribution remains the Python SDK. Non-Python and network hosts
must preserve exact Motus bytes and structured results while owning their own
authorization, tenancy, retrieval and deployment concerns. Passing the adapter
corpus proves preservation of this boundary only; it is not deployment,
security, retention or compliance certification.

Version 0.23.0 does not complete regulatory-core point 11. Motus UI remains a
separate later release target, and public documentation in `vitruvyan-docs`
must be updated only after the adapter implementation and release evidence are
stable.

---

## Where we are, in facts

*Last reconciled against the repository on 2026-09-27. This section is a
statement about the code, not about intentions; when it disagrees with the
code, it is this section that is wrong — and on 2026-08-19 it was, in three
places at once, which is why the reconciliation date is part of the section.*

- **0.22.0** is the newest tagged source-release candidate (2026-09-27): the
  annotated tag points at verified merge commit
  `dbe2543fab22a30f473b0b7929de5cba2dd6a5e2` for Verification and Query
  API/CLI v1. Workflow `36293906043` built and verified the distributions once,
  preserved the authenticated artifact, and created only a draft GitHub
  Release. The PyPI job was skipped; publication is not authorised, so ADR-032
  treats the release as unfinished;
- **0.21.0** is a tagged release candidate (2026-09-26): the annotated tag
  points at verified merge commit `980683beeac14813be4b86edee569c6c4fb95e40`
  for Regulatory Evidence Dossier v1. Its verified artifacts remain on a draft
  GitHub Release; publication and the coupled PyPI step are not authorised, so
  ADR-032 treats the release as unfinished;
- **0.20.0** is the current source release (2026-09-26): the annotated tag
  points at verified merge commit `6e97e222b49c6700dd86f934dffb19de2e4cba84`
  for AI System Registry v1. Its GitHub Release remains a draft and PyPI
  publication is not authorised;
- **0.19.0** (2026-09-25): the annotated tag
  points at verified merge commit `080f39cf9641de440bfa6dda72e1fa67383e317b`
  for Retention & Legal Hold v1. Its GitHub Release remains a draft and PyPI
  publication is not authorised;
- **0.18.0** (2026-09-24): the annotated tag
  points at the verified merge commit for Incident / CAPA Ledger v1. Its
  GitHub Release remains a draft and PyPI publication is not authorised;
- **0.17.0** (2026-09-23): the annotated tag
  points at the verified merge commit for Regulatory Evidence Profile v1.
  The profile composes existing Motus validators and reports only `missing`,
  `not_verified`, `mismatched`, or `matched`;
- **0.15.0** shipped the Canonical Evidence API, Regulatory System Manifest,
  and Risk & Control Registry / ControlApplication. This was the historical
  exception that combined multiple regulatory roadmap points in one tag;
- **0.14.0** shipped the receipt/package and contract-hardening line that the
  later regulatory surfaces build on;
- **0.13.0** (2026-09-05): the OpenTimestamps plug
  walks the whole proof tree and asks the calendar named in the attestation
  (six commitments had been reported `pending` for 67 hours while anchored),
  refuses an `anchored` receipt without a `published_at` resolver, and bounds
  its upgrade loop; the commitment log poisons itself after a failure past the
  durable write (#129) and digests before writing (#112); a graph's routing
  record no longer depends on map order (#124); `InclusionProof` emits a
  receipt.v1 Entry (#119); the #116 refusal was reverted for shipping without
  its contract; ADR-027 (execution identity) is PROPOSED. Per-release arm
  **passes** — −0.2 %, −0.1 %, −1.8 % over v0.12.0 across three dispatches —
  and the cumulative arm still fails, shipped under ADR-018 with a re-taken
  real-request share of 0.017 %;
- **0.12.0** (2026-08-16): ADR-026, a Motus string is a
  valid sequence of Unicode scalar values, rule `J1` scoped by the document's
  own declared schema version, and `J2` reaching JSONL record lines where the
  tamper ADR-024 exists to stop was passing clean. Its per-release arm
  **passes** — +0.5 %, +0.4 %, +2.0 % over v0.11.0 across three dispatches;
- **0.11.0** (2026-08-14) carried the verifier, the OpenTimestamps anchor, rule
  `J2` and the integration MCP. Like 0.10.0 it shipped
  with the cumulative gate **failing** — +91.4 %, +133.8 %, +27.5 % against the
  v0.6.1 anchor — under ADR-018 §3 with all four conditions met and none
  inherited. Its per-release arm passes at −0.1 %, −0.0 %, +0.4 %, by amounts
  the measurement cannot resolve, and the real-workload share was re-taken for
  it: **0.055 %**, an upper bound;
- **0.10.0** carried phase 1 in full: the commitment
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
- **the verifier exists and #51's hard constraint has code.** `verify(receipt,
  trace)` in `contract/validate.py` reports all seven ADR-020 levels including
  the ones it could not reach, and `motus-validate receipt <r> --trace <t>`
  runs it offline, with no account and against no server of ours. This bullet
  said the opposite until 2026-08-19, while Phase 2 item 1 in this same file
  was already ticked — the section contradicted itself for five days;
- **six roots are anchored on Bitcoin** through OpenTimestamps, three
  independent calendars each, blocks 962770 – 962798. They were reported
  `pending` for 67 hours by our own reader, which walked one level of a proof
  whose attestation sits four operations down and turned the calendars'
  "never heard of it" into silence. Fixed, with the walk and the swallow both
  named in `plugs/motus-anchor-opentimestamps`;
- **one root is anchored on TRON Nile**, txid `6010ded8…`, block 70013920, memo
  carrying the full root. Demonstration only, and superseded by the
  OpenTimestamps path above;
- the runtime still does not import the commitment modules unless configured,
  and a subprocess test asserts it by inspecting `sys.modules`.

---

## Phase 0 — close what is open — **DONE**

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
5. ➡ the demo page on `vitruvyan.com/motus/attack` — **moved to the end of
   this roadmap by the founder on 2026-08-13, and it needs a deep rewrite
   before anybody builds it.** See *Phase 6* below for why the position is the
   right one and not merely a convenience.

**Shipped:** an anchored root, in a release.

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

## Phase 1b — the integration MCP — **DONE, released in v0.11.0**

*Built on 2026-08-14 against ADR-022. It touches no receipt, no anchor, and
promises nothing about audit. What follows is the argument that put it here;
what it actually became is at the end of this section.*

You install Motus, point your coding agent at the MCP, and start building
without reading three hundred lines of prose.

**Why this and not more documentation. There are now three measurements, and
the third is the one that settles it.**

Commit `24adb30` is titled *"two examples for the part of the protocol a first
integration gets wrong"* — so the gap was known and the answer was two more
examples. An automated reviewer then read those examples and the Terraveler
brief and **inverted the effect classification twice in one pull request**
(#77): it called a database write `recorded_effect` where the protocol says
`external_effect`, and read-only calls the opposite.

**Then two of our own documents were found to disagree, which is worse and
more useful.** Terraveler's field report of 2026-08-13, against v0.10.0:
`check_verbatim` — a node issuing nothing but HTTP GETs — was declared
`external_effect` with a comment calling it *"conservative"*. They reported it
as an error against the brief. **It is not an error against the contract.**
`node-protocol.md` §4.2 says *"Classify honestly or conservatively"*, and §4.1
names `external_effect` the conservative reading. Their declaration is
contract-valid.

What it costs is real and it is not correctness: a read declared
`external_effect` blocks resumes that were safe. That is an integration cost,
paid silently, and nobody would ever be told they were paying it.

**The finding is about us.** `docs/TERRAVELER_MOTUS_TRON.md` states the rule as
*"the criterion is read or mutate, and nothing else"*, which leaves no room for
the latitude the contract grants — and the prompt handed to their agent
repeated the brief rather than the contract. Under the authority order the
contract wins, so the brief is the document that is wrong.

**And this is the argument for the MCP, in its strongest form.** An integrator
holding two of our documents that disagree will reason their way to one of
them, and there is no reason it should be the authoritative one. A
`motus_classify` derived from `node-protocol.md` at call time answers what
neither document says on its own: *`recorded_effect` is the honest class;
`external_effect` is permitted and costs you safe resumes.* A tool that cites
its source cannot drift from it, and cannot be out-argued by a second document
that did.

**A protocol with classifications does not travel in prose. It travels by
answering the concrete question somebody is holding, from the source, at the
moment they are holding it.**

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

**Its first test is Terraveler**, who has now completed the brief end to end
against v0.10.0. Handing the next integrator an MCP instead of a
three-hundred-line brief is the strongest evidence we can get — and the brief
is the document that was read carefully, followed exactly, and turned out to
disagree with the contract it was written from.

### What it became, and the one decision that shaped it

**The rule is in the type, not in a review.** ADR-022 asks for a test that no
prose lives in the server, and a test that reads the tools and judges their
sentences is the version that decays. So an answer is a sequence of spans and a
span is one of exactly three things — quoted from a named installed source and
**verified when it is constructed**, computed by shipped code and carrying the
command that reproduces it, or `I cannot tell` with what was tried. There is no
fourth kind and none of the three has a field an author could put an opinion in.

**The test that matters is the one that moves the document.** Every other
property would still hold if `classify` returned a constant and quoted a
matching line. `test_editing_the_table_changes_the_answer` re-declares `INSERT`
as `recorded_effect` in the text the server reads and requires the verdict to
follow the document.

**The contract gained what the tools read.** §4.4 names operations and — for
the first time anywhere — the **price of the conservative choice**: a read
declared `external_effect` costs safe resumes, and a write declared
`recorded_effect` never meets the resume guard at all. §6.4 names the ambient
draws. Both say a row is wrong where it disagrees with the clause above it.
This is the Consequence ADR-022 predicted, running the right way: the server
needed an answer, so the *document* got one.

**A mutation probe found three guarantees nothing was holding**, including the
construction-time check on a quotation — disabling it changed no test, because
every quotation the tools produce today is genuine.

### Then the round ran, and both halves of it were wrong

*Three lenses, independent, all three landing on the same component. This is
the strongest signal this project has produced, and it is recorded here rather
than in a commit message because it changes what the component is.*

**The describing half computed a class from word matching, and was wrong on
most realistic descriptions** — 15 of 20 on one lens, 6 of 6 on another, in
both directions, never refusing. *"Computes the invoice total and stores it in
Postgres"* was answered `pure`, because `compute` is a marked term and `stores`
is not; a lens then demonstrated verify-replay re-executing such a node and
**sending its mail a second time** while reporting it verified. The same defect
sat in `review_node` over Python identifiers, where it accused **our own
shipped example** of implying `external_effect` because it calls `payload.get`.

It is a class this project had already written down: *a pattern answering a
question about meaning*. Adding terms repairs the instance, so the terms were
not added. **The verdict is withdrawn** (ADR-025): the tool reports which
marked terms the caller's text contains and hands over the table, the costs,
the strictest rule and 4.1. What the measurement asked for was never a matcher
— it was that somebody be told what the conservative choice costs.

**The debug half ran the shipped rules over a document the shipped reader had
never seen.** A trace containing `records` twice was reported as verifying with
a derived root while its own reproduce line refused it as J1; one invalid byte
was laundered and reported clean; and every JSONL trace **the only durable sink
we ship** writes was answered *I cannot tell*, because a JSON object literal is
a valid Python expression and `ast.parse` accepted it.

**And a cache had made "derived" false.** A long-lived server kept answering
after the row was edited and after the document was **deleted**. The test meant
to catch that patched the reader, so it tested a mock of the mechanism.

**ADR-025 was accepted on 2026-08-14**, which closes the amendment the round
found missing. Shipped in v0.11.0. Still owed: a first integrator using the MCP
instead of a brief — and the first one is now **vitruvyan-core itself**, see the
pilot below.

**The failure mode to watch:** the MCP becoming the place where documentation
gaps hide. A question the MCP answers well is evidence the *document* should
answer it too. Tool usage is a defect report against the docs, never a
replacement for them.

---

## Phase 2 — the verifier, before any anchor a receipt depends on — **IN PROGRESS**

*Weeks. Out of order it turns the product into a promise.*

**The demo anchor of phase 0 is not a violation of this ordering, and the
distinction is worth being precise about rather than trusting to context.**
That anchor backs a page that says what it is; no receipt format depends on
it, no customer verifies against it, and nothing about it is sold. The rule
this phase protects is that **an anchor a third party is invited to rely on
must not ship before the check they would rely on it with** — which is #51's
constraint, and it is still ahead of us.

1. ✅ **`motus-validate receipt <receipt> --trace <trace>`** — reports all seven
   of ADR-020's levels including the ones it could not reach, and refuses on an
   unknown algorithm or network. **A refusal outranks a violation**: an unknown
   network is also a contract violation, and reporting it as one says "this
   document is wrong" about a document that may be perfectly correct on a chain
   we cannot read;
2. ✅ `BEGIN` without `END` reported as *an execution that left no completion* —
   never as suppression;
3. ⏳ **the public verifier page.** Drag a trace and its receipt onto a page, get a
   verdict, no account, no API of ours. This is the padlock in the address bar:
   it makes the property visible to people who do not read code, and it is worth
   more commercially than any dashboard.

**Why here and not later:** #51 fixed *verification is always open* as the hard
constraint. Shipping an anchor before its check means selling a claim nobody can
test — including us.

---

## Phase 3 — the plugs and the witness

*Weeks for the plugs; the witness is the first thing that might be a service.*

1. ✅ **`motus-anchor-opentimestamps`** — the production default, and it
   ships. Free, Bitcoin, no wallet, no key custody, no gas, no treasury that
   runs dry on a Saturday night. Separate distribution, in `plugs/`, with its
   own suite. Six demo roots are anchored through it;
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

## What the format freeze now knows that it did not

*Added 2026-08-14, because two findings in one day changed what "the format" is
understood to commit to, and phase 4 freezes exactly that.*

**The digest commits to a document only up to what the parser flattens.**
ADR-024 states the line — a text difference may be absorbed only if a reader
reads the same thing. Two families fail it. **One is closed and one is not, and
the difference is the most useful thing this section records.**

**Numeric lexemes (`J2`) are closed**, and scoped to schema `3.0.0` and above by
the document's own declared version. Below that the terminal digest covers one
record rather than the run, so there is no root for a lexical collision to
attack — and `contract/README.md` promises that evidence written before a rule
existed stays valid. The canonical number form is **frozen as a vector table in
the ADR** and checked against the live interpreter, because *"what CPython's
`json.dumps` emits"* names no version and `pyproject` supports `>=3.10` openly.

**String escapes (`J3`) were written, accepted, and withdrawn the same day.**
It covered half its own surface: `parse_string` reaches values, while
`JSONObject` reads member *names* through the module-global `scanstring`, so
the ADR's own demonstration moved from a value to a key was accepted. And its
canonical form **refused our own frozen golden** —
`tests/compat/terraveler/golden/production-ingestion-trace.json`, taken from
production and unmodifiable by CI, contains a `\u2014`.

**#98 is closed by ADR-026, and it was decided rather than repaired.**
`"appro\u0076ed"` and `"approved"` are the same JSON string; every conforming
reader gets the same value from both, so sharing a root is correct. Refusing
one would be a false accusation against a document identical in meaning to one
we accept, which `contract/README.md` ranks as the worst answer a verifier
gives. The rule, stated once: **same JSON value and structure, same commitment;
different value or structure, different commitment.** `J2` is not an exception
to it but its second half enforced where binary64 would break it.

It remains true that `grep approved` does not find the escaped form. That is a
fact about `grep`, which matches bytes and is not a JSON reader. The answer is
a linter — *this evidence is authentic and may mislead a tool that is not
JSON-aware* — which is a different sentence from what a validator says, and
collapsing the two is what `J3` did.

**What ADR-026 does refuse is a string that denotes nothing.** An unpaired
surrogate has no UTF-8 encoding and conforming implementations disagree about
it, so a document carrying one has more than one reading (rule `J1`). Scoped by
the document's own declared trace schema version to `2.0.0` and above, because
v0.5.0 through v0.7.0 **wrote** such traces and their own validators called
them valid — measured against the tags, after the first draft of the ADR
asserted the opposite. The producing side is scoped by nothing.

Whitespace and member order stay absorbed, deliberately, and the reason is
written down.

**The guarantee belongs to whoever holds the bytes.** `Trace.from_json` applies
`J2`; `Trace.from_dict` provably cannot, because by the time it is called the
two documents are one object. That residual is asserted by a test rather than
described, so that somebody setting out to fix it finds out there is nothing to
fix.

**And the freeze must inherit the rule that produced these**, not only their
outcomes: *a finding names an instance, repair the class*. Both of today's
second sites were worse than the reported first, and neither was reported by
anybody. It is now in `AGENTS.md` and in the adversarial-round skill, with the
six known classes listed for sweeping.

---

## The pilot that has to happen before phase 4 — vitruvyan-core, not Orbis

*Corrected on 2026-08-14 by the founder, and the correction matters: this was
written as "one Orbis-shaped flow", and **Orbis does not exist**. What exists is
`vitruvyan-core`, running LangGraph today. A pilot against a system that has not
been built is not a pilot.*

**The freeze is a promise that a receipt written today verifies in ten years,
and this is the last thing that can still tell us the format is wrong while
changing it is cheap.** Terraveler completed three phases against v0.10.0 and
found no Motus defect — but they used Motus as an outside integrator would.
`vitruvyan-core` is different in the way that matters: it is our own code, with
its own nodes, already orchestrated by something else.

The work, in order, and none of it is a rewrite:

1. **read how LangGraph is actually used in `vitruvyan-core`** — where the graph
   is declared, what a node receives, what crosses between them, and what is
   persisted today;
2. **name the real nodes** and, for each, the effect class §4.4 gives it and the
   price of the conservative reading. This is the first use of the MCP as the
   thing it was built to be;
3. **port one flow**, end to end, and run it beside the LangGraph one;
4. **say what Motus records that nothing records today** — and, more usefully,
   **what the format could not express**. That last answer is the one the freeze
   is waiting for.

**The order is load-bearing and it is the reason 1.0.0 is below this and not
above it.** Freezing first and porting second would mean discovering a format
defect when correcting it costs a major version, instead of an afternoon.

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

## Phase 6 — the demonstration, last and rewritten

*Moved here from phase 0 by the founder on 2026-08-13. It is the only item in
this document that got later rather than earlier, and the reason is worth
stating.*

**A demo is a claim made in public, and it can only be as strong as the weakest
thing it demonstrates.** Built during phase 0 it would have shown an anchored
root with **no shipped way to check it** — the visitor is asked to believe the
page. Built after phase 3 it can show the whole sentence: a run committed
before it executed, witnessed, sealed, anchored, and **verified by a tool the
visitor runs themselves against a chain we do not operate**.

Its current brief, `docs/SITE_ATTACK_DEMO.md`, is written against the first of
those and **must be rewritten rather than updated**. It was drafted when the
strongest available demonstration was a hash in a memo; the strongest available
demonstration is now a different thing, and a brief revised line by line would
carry the shape of the old one.

**The absolute rule survives the rewrite** and is repeated here because it is
the one that would cost the most: *never write a transaction hash that is not
in the payload* — not a placeholder, not a plausible-looking hex string. If
`anchor.txid` is null, the page renders a pending state. A demonstration of
auditability that fabricates one piece of evidence has demonstrated the
opposite, and the fabrication does not need to be load-bearing to do that.

**The scenario is already right; what changes is where the evidence comes
from.** The current brief already requires the resealed forgery — an editor who
recomputes every hash, whose trace then passes both local checks and disagrees
only with the anchored root — and it already says that row is the sales
argument for beat 3. **A rewrite must not discard that**, and the correction
notice on this entry exists because an earlier draft of it implied the brief
demonstrated something weaker than it does.

What Terraveler's field report of 2026-08-13 adds is **provenance**, which is
the half that was invented:

> A value in a real anchored trace was tampered with, and the whole downstream
> hash chain **correctly reprocessed** by hand-replicating Motus's own sealing
> recipe. `motus-validate jsonl` returns **exit 0** on the forged document. It
> fails `verify()` for one reason only: the on-chain memo was written before
> the edit and names a different root.

Real production evidence, a real Nile transaction, performed by somebody who is
not us. Build the page on their run rather than on a fixture, and keep every
requirement the brief already has.

Built by a separate agent in frontier, from a brief written here.

---

## Running in parallel

**The residues**, each needing its own ADR and its own round:

- ~~**#75**~~ — **closed 2026-08-13** by ADR-023 and the `continues` link. See
  phase 1;
- ~~**#74**~~ — **closed 2026-08-14** by ADR-024, and it turned out to be the
  cheapest thing on the critical path rather than the most expensive. The digest
  is over PARSED values, so a genuine `5e+18` rewritten to
  `5000000000000000511.0` was the same double and shared a root. **No digest
  recipe changed and no genuine root moved**: `_canonical_bytes` already
  serializes through `json.dumps`, so what we write was always canonical — the
  defect was that a *verifier* re-serializes what it parses, and
  re-serialization launders the difference. Rule `J2` refuses a document whose
  numeric lexemes are not the ones this contract writes, from schema `3.0.0`;
  `J3` would have done the same for string escapes and was **withdrawn**;
  #98 is closed by ADR-026 as intended behaviour, and the same ADR draws the
  line that `J3` could not: what is refused is a string denoting nothing, not a
  string spelled differently;
- **#73** — a trace cannot say whether its evidence reached the sink. **Still
  open, and the attempt to close it on 2026-08-14 was withdrawn the same day.**
  `RunResult.evidence` reports what the runtime told the session, not what a
  sink did with it, so a `Mock()` sink produces status `completed` with evidence
  `persisted` and nothing written anywhere. A refusal of stand-ins was written
  for that — and it rejected `xmlrpc.client.ServerProxy` from the standard
  library, a lazy sink and a failover proxy, while `Mock(spec=...)` and
  `create_autospec`, which the stdlib documentation recommends, walked through.
  **The deeper reason it had to go is that the defect was over-claimed:**
  ADR-016 defines `persisted` as *a required sink accepted every record*, and a
  sink that does not raise has accepted. The `Mock()` is the contract working as
  written. `tests/test_stand_ins.py` now **pins the limit** instead of enforcing
  a rule, so the next person to notice finds this before rewriting it;
- **#52** — `result_fingerprint`, superseding ADR-013.

**Engineering debt that will bite on schedule:** #70 (the SLO gate depends on
runner allocation), #66 (validator under a hostile switch interval, in CI),
#65 and #63 (run lifecycle — and phase 1 depends on that lifecycle being right),
#49 (distribution), #38 (H1), #40 (async resume).

**Terraveler** is the first external integrator and the proving ground. Its
brief is `docs/TERRAVELER_MOTUS_TRON.md`, and it **completed all three phases
against v0.10.0 on 2026-08-13**. Whatever it finds outranks whatever we think,
and what it found is recorded where each item is decided rather than summarised
here — phase 1b for the classification error, phase 6 for the forgery
demonstration.

Two things from that report belong nowhere else and are kept here — and one
correction to how it was first read: **their effect-class declaration was
contract-valid**, not the error they and this document initially called it. See
phase 1b; the finding turned out to be about our own documents disagreeing.

Two things from that report belong nowhere else and are kept here:

- **they voided their own published anchor rather than quietly re-anchoring.**
  Their 0.8.1 anchor was made under trace schema 2.0.0, whose digests do not
  chain, so the anchored value never moves when a non-terminal record is
  rewritten — ADR-019 names their situation as the example. They added a
  correction notice and an addendum instead of rewriting history. An external
  party reaching that conclusion from the ADR alone, and acting against their
  own interest on it, is the strongest validation this trust model has had;
- **they re-implemented our isolation invariant instead of trusting it.**
  `scripts/test_motus_inert.py` runs an unconfigured graph in a subprocess and
  asserts `commitlog`, `commitments` and `sealing` never reach `sys.modules` —
  written after reading our source rather than after reading ADR-021's comment
  about it. Verified here: `commitlog` is imported nowhere in `src/` but
  itself, and both `commitments` imports in `runtime.py` are function-local and
  sit after the `if self._commitments is None: return` guard. Their reliance on
  `a recorded_effect node cannot record external effects` is also sound — it is
  enforced twice, in `context.py` and in `runtime.py`.

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
