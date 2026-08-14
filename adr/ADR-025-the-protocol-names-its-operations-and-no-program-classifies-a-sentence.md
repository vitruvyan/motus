# ADR-025 — the protocol names its operations, and no program classifies a sentence

- **Status:** PROPOSED
- **Date:** 2026-08-14
- **Authority:** CTO. `contract/README.md` rule 4 — *"Amendments leave a
  record. A contract change is a PR touching this directory plus an ADR stating
  what changed, why, and what migrates"* — and ADR-001, *"a change to any
  contract surface is a versioned amendment with its own ADR, not an edit"*.
  An adversarial round found the amendment had been made without one, in a pull
  request whose only ADR says **"Amends: nothing"** and predates the sections
  by a day. That finding is the reason this document exists
- **Depends on:** ADR-022 (the integration MCP), which needed answers the prose
  did not have
- **Amends:** `contract/node-protocol.md` — adds §4.4 and §6.4, corrects a cost
  cell in §4.4's table and a stale cross-reference in §6.3. **No schema version
  moves and no digest recipe changes.** What migrates is stated in decision 4
- **Advances:** roadmap phase 1b

## Context

ADR-022 built a server whose every answer is derived from a document. Two of
the questions it exists to answer had no document to derive from.

**The first is the one that was measured twice.** §4.1 defines three effect
classes and §4.2 grants latitude between them. A reader holding a concrete
operation — *"I need to INSERT a row"* — has to apply both, and two careful
readers applied them wrongly: an automated reviewer inverted the classification
twice in one pull request (#77), and an integrator's field report against
v0.10.0 declared a node issuing nothing but HTTP GETs `external_effect` and
called it conservative. **The contract permits that.** Nothing told them the
price, which is safe resumes, silently, forever.

**The second is §6.2's.** It says what happens when a node draws ambiently and
names three examples in passing. A reader auditing their own node is holding
calls, and a tool reading a node could only report a suspicion.

So the documents gained the answers, and this is ADR-022's own recorded
Consequence running the right way round: *"A question the MCP answers well is
evidence that the document should answer it too. This runs one way only."*

### What the round then found, and why it is in this ADR

The server did not stop at reading §4.4. It **classified**, by matching the
table's marked terms against the words of a caller's description and folding
the matched rows into a verdict. Two independent adversarial lenses measured
it: **15 of 20 realistic descriptions wrong on one, 6 of 6 on the other, in
both directions, with zero refusals.**

The direction matters more than the count. *"The node computes the invoice
total and stores it in Postgres"* was answered **`pure`** — because `compute`
is a marked term and `stores` is not. A write declared `pure` may not record an
effect at all and is re-executed by verify-replay; the round demonstrated the
re-execution **sending the mail a second time** while reporting the node
verified.

A third lens found the same defect in `motus_review_node`, matching the table's
terms against the last dotted component of every call: `payload.get` read as an
HTTP `GET`, `seen.append` read as appending to a file. It accused **Motus's own
shipped example** — a node whose docstring says it *"stays `pure`, which is
what makes it verifiable during replay"* — of implying `external_effect`.

This is a class this project has already written down: **a pattern answering a
question about meaning.** It is the same shape as the regex that flagged
`{"note": "cost 5.10 eur"}`, moved from JSON text to English and then to Python
identifiers. Adding terms repairs the instance and leaves the class.

## Decision

### 1. The protocol names its operations, normatively

`contract/node-protocol.md` §4.4 classifies named operations under §4.1, and
§6.4 names the ambient draws §6.1 requires to be mediated. Both are normative
and both say a row is wrong where it disagrees with the clause above it.

§4.4 carries the thing nobody was being told: **the cost of the conservative
choice**, per row. A read declared `external_effect` costs safe resumes. A
write declared `recorded_effect` never meets the resume guard at all. One error
costs availability; the other costs correctness, in a system whose purpose is
to be believed.

### 2. A table classifies operations. No program may use it to classify a sentence

This is the decision the round paid for, and it is written into §4.4 so that
the tools quote it rather than obey it.

> A program can find the marked terms that are present in a description; it
> cannot find the operation a description names in words this table does not
> mark, and **that** is the operation that would have changed the answer.

So `motus_classify` reports **which marked terms are present in the caller's
text** — a fact about the text, checkable by them — and hands over the whole
table, the asymmetry, the strictest-class rule, the fallback clause and §4.1.
It emits no class. `motus_review_node` keeps only what is structural: ambient
draws found by walking the AST against §6.4, and a missing idempotency key for
a node declared `external_effect`.

**What this gives up is the appearance of an answer**, and that is the point.
The measured failure was never that readers could not run a matcher. It was
that nobody had told them what the conservative choice costs.

### 3. The strictest-class rule is new, and nothing enforces it

§4.4 says a node whose work falls in more than one row takes the strictest
class any of them names. An earlier draft claimed §4.1 "already says so". It
does not: §4.1 gives three overlapping descriptions and no precedence, and
§4.2's latitude permits the conservative class without requiring it.

And **no gate checks it.** The runtime detects only the sub-case where a node
declared `recorded_effect` voluntarily records an `external_effect` descriptor;
a node that simply performs the write is invisible to every gate Motus has.
`contract/README.md`: *"A contract is binding exactly where a gate checks it;
everywhere else it is documentation that lies."* §4.4 now says which it is,
following the precedent §1.2 set when it was corrected for the same over-claim.

### 4. What migrates

**Nothing, and the reasoning is stated rather than assumed.** No schema version
moves, no digest recipe changes, no fixture changes — §4.4 and §6.4 have no
fixture-expressible content, because nothing validates an effect declaration
against a node's behaviour.

One case becomes decided that was previously open: a node that both reads the
outside world and mutates it, declared `recorded_effect`. Before, §4.2's
latitude left it arguable. It is now wrong. **This is a change to what the
contract says and not to what any gate does**, so no existing run, trace or
receipt becomes invalid and nothing an integrator has written stops executing.
An integrator who declared such a node conservatively was already right; one
who declared it softly was already taking the risk §4.4 now names.

We considered calling this a breaking change requiring a major version and
rejected it on the ground above: rule 2's *"breaking change to any surface"*
governs what a consumer's artefacts must satisfy, and no artefact's validity
moved. The call is recorded here because it is a call, and a reader who
disagrees with it should disagree with this paragraph rather than discover it.

### 5. Two corrections to text already shipped in this pull request

- §4.4's `pure` row said the cost of over-declaring was *"forfeits
  verify-replay"*. It is larger than that and was measured: a pure node
  declared `external_effect` **can never be resumed**, because §4.1 permits its
  resume *only* with an idempotency key and a completed receipt, and a pure
  node can honestly obtain neither. Every other `permitted` row named the
  resume cost; this one, uniquely, did not;
- §6.3 carried a reference to *"(§6.4)"* about `motus_config()` being taken at
  its word, written when no §6.4 existed. Adding §6.4 turned a dangling
  reference into a confidently wrong one, pointing at a table of
  `datetime.now` / `random.random`. It now points at the paragraph it meant.

## The costs accepted

**A tool that looks less useful.** `motus_classify` no longer answers the
question a user thinks they asked. It answers a narrower one truthfully and
hands over the document. ADR-022 already accepted this trade in the abstract —
*"it will answer 'I cannot tell' to questions a summary-writing server would
have answered fluently and sometimes wrongly. **We prefer the refusal**"* — and
this is what it costs concretely.

**Two normative tables that no gate enforces.** They are documentation, and
they say so. The risk is the ordinary one: a table read as a checklist by
somebody who believes something is checking it. Decision 3 is the mitigation
and it is only words.

**A contract that grew by eighty lines to serve a tool.** The direction is
right — the document is the authority and the server reads it — but the
pressure came from the server, and a document edited to be machine-readable can
drift towards being written for the machine. §4.4's marked terms are the first
instance; a later reader should ask whether the marking is still serving the
human reading the clause.

## Alternatives rejected

**Add the missing terms.** *mail*, *store*, *save*, *upload*, *dispatch*,
*persist*, *commit*, *emit*. This is the repair the finding invites and it is
the instance, not the class: the vocabulary of English verbs for *changing
something* has no closure, and every term added makes the tool more confident
without making it more right. It also makes the failure rarer and therefore
harder to notice, which is worse.

**Keep the verdict and mark it low-confidence.** A confidence label on a class
name is read as the class name. ADR-022 decision 4d exists because *"a
confident wrong diagnosis sends somebody to rewrite working code"*, and a
hedged wrong diagnosis sends them to the same place.

**Take the classification decision to a model.** Out of scope by decision 1 of
ADR-022 — an answer reasoned from an artefact is indistinguishable, to the
caller, from an answer derived by running our code, and wrong at a rate nobody
can measure.

**Leave §4.4 out and let the MCP hold the table.** This is the version that
fails ADR-022's own thesis: prose in the server, a second source of truth, and
our own server misstating our own protocol.
