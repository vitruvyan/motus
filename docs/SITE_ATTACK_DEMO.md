# Build "Attack this run" — the Motus proof page for vitruvyan.com

Paste this whole file as the opening prompt of a session in the `frontier`
repository.

**This brief was rewritten on 2026-08-15, not revised.** The version before it
was drafted in phase 0, when the strongest available demonstration was a hash in
a memo and nothing shipped could check it. The strongest available demonstration
is now a different thing, and a brief edited line by line would have carried the
shape of the old one. The scenario survives intact; where the evidence comes
from does not.

---

## Before you write a line: four facts, and you must re-verify each

The Motus repository is at `/home/vitruvyan/motus` on this machine and it is the
authority. This brief is a synthesis. **Where they disagree, the repository is
right** — and it has been ahead of its own documentation more than once this
month, which is why every fact below carries the command that checks it.

**1. The root in the payload is anchored, and the live page says it is not.**

```bash
python -c "import json;print(json.load(open('/home/vitruvyan/frontier/ui/public/motus/attack_this_run.json'))['anchor'])"
python -c "import json;print(json.load(open('/home/vitruvyan/motus/demo/out/attack_this_run.json'))['anchor'])"
```

The file the site serves carries `anchor` with every field `null`. The file in
the Motus repository carries a real TRON Nile transaction — txid
`6010ded8…`, memo `VITRUVYAN_AUDIT:sha256:cb6829d3…`, block 70013920, published
2026-08-12T17:15:48Z. So `/motus/attack` renders *"Publication pending"* for a
root that has been on a public chain for three days.

That is not the failure the absolute rule below guards against. It is the
opposite one: real evidence withheld, a page weaker than the truth. **Fixing it
is the first thing you do**, and it is a data fix, not a code fix — the existing
component already reads the txid from the payload and already renders a pending
state when it is null, which is correct and must stay.

**2. The Motus repository is PRIVATE and Motus is not on PyPI.**

```bash
gh repo view vitruvyan/motus --json isPrivate
curl -s -o /dev/null -w "%{http_code}\n" https://pypi.org/pypi/vitruvyan-motus/json   # 404
```

This breaks the sentence the page exists to earn. The phase-6 entry in
`docs/ROADMAP.md` says the demonstration must be *"verified by a tool the
visitor runs themselves against a chain we do not operate"* — and today a
visitor can neither clone the repository nor install the package. **Read "The
one architectural question" below before designing around this.** Do not paste
`pip install vitruvyan-motus` onto the page; it fails, and the site's own README
said it for two releases before an audit caught it.

**3. The current release was v0.12.0 when this brief was written** (2026-08-16,
tagged that day at `d0c024e`); since 2026-09-05 it is **v0.13.0**, and the
README's release line is the authority, not this paragraph. v0.12.0 carries
ADR-026 — *a Motus string must represent a valid sequence of Unicode scalar
values* — and closed #98 as **intended behaviour**: `"approved"` and
`"approved"` are the same JSON string and share a root, correctly. If the
page says anything about what a trace commits to, that is the sentence:

> Same JSON value and structure → same commitment.
> Different value or structure → different commitment.

**4. Terraveler's forgery is real and it is better than any fixture we could
build.** From their field report of 2026-08-13: a value in a real anchored trace
was tampered with, and the whole downstream hash chain **correctly reprocessed**
by hand-replicating Motus's own sealing recipe. `motus-validate jsonl` returns
**exit 0** on the forged document. It fails `verify()` for one reason only: the
on-chain memo was written before the edit and names a different root.

Real production evidence, a real Nile transaction, performed by somebody who is
not us. **Build beat 3 on their run, not on a fixture.**

---

## The absolute rule

> **Never write a transaction hash that is not in the payload.**

Not a placeholder. Not a plausible-looking hex string. Not a truncated example
in a mockup that somebody later forgets is a mockup. If `anchor.txid` is null,
the page renders a pending state and says so plainly.

A demonstration of auditability that fabricates one piece of evidence has
demonstrated the opposite, and **the fabrication does not need to be
load-bearing to do that**. The reader's whole conclusion is *these people do not
make things up.* One invented hex string, found later, costs more than the page
ever earned.

The same rule governs every number on the page. If you write a test count, a
latency, a percentage, it comes from a command you ran and can run again in
front of somebody. This project has published a figure taken from one
hand-picked document and had an adversarial round refute it in public; the
refutation is in ADR-026 under its own heading. Do not add to that list.

---

## Who this is for

Not a peer reviewer. **A CTO evaluating us, or an investor doing diligence.**
Ninety seconds, often on a phone or projected in a room. They do not know what
Motus is and they will not read a specification.

They must leave able to repeat one sentence to a colleague:

> *Every decision their system makes is recorded in evidence that not even they
> can rewrite — and the proof is published on a public blockchain that I can go
> and read myself.*

Every section serves that sentence. If a section does not, cut it.

---

## The three beats, in this order

**1. A real decision, in a screen they recognise.** A security review desk
approves the release of an advisory. Header, question and answer, the seals, the
cited sources with their TLP markings. It has to look like an ordinary,
credible internal system — an admin screen they have seen a hundred times. The
reader must not yet know they are looking at a proof.

**2. The attack, performed in front of them.** Change the decision. Watch the
chain break. This is the beat that is *easy* and it is not the sales argument —
any hash chain does this, and a reader who has seen one will not be moved.

**3. The resealed forgery, and this is the whole page.** An editor who does not
merely edit the file but **recomputes every hash correctly**, replicating
Motus's own sealing recipe by hand. Their trace is internally perfect. It passes
`motus-validate` with exit 0. Every local check agrees with it.

It disagrees with exactly one thing: **the root written on a public chain before
the edit existed.**

That is the argument. Not "we detect tampering" — everybody claims that. The
claim is: *an attacker who does the work correctly still cannot move a
commitment that was published before they started.* Beat 3 is where a diligent
reader stops being polite and starts being interested, and it must be the
longest, slowest, most concrete section on the page.

---

## The one architectural question, which you must answer before building

The page's promise is that the visitor checks it themselves. Today they can
check **half** of it with no help from us:

- **the anchor half is already independent.** The Nile transaction is public.
  The visitor opens the explorer — a site we do not operate — and reads the memo
  with their own eyes. Link it prominently; this half needs nothing from us and
  is the strongest thing on the page.
- **the recompute half is not.** Deriving the root from the trace needs our
  validator, which lives in a private repository and is not on any index.

You have three ways to close it. Pick one, and bring the reasoning back before
you build:

**(a) Recompute in the visitor's browser.** Reimplement the canonical form and
the SHA-256 root in TypeScript, client-side, over the trace the visitor
downloaded. The visitor's own machine derives the root and compares it to the
memo on the chain. Nothing of ours is trusted in that path.

This is the strongest option and it is **also the riskiest, in the way this
project likes**: it is a genuine second implementation of the digest recipe, and
ADR-026's own "costs accepted" section names the absence of one as the reason
decision 1 is a claim rather than a checked property. So it does more than serve
the page.

**It must be proven to agree with the Python before anything ships.** Run it
over every trace in `/home/vitruvyan/motus/contract/fixtures/` and over the demo
artefact, and compare against `contract/validate.py`'s `derived_root`. **If it
disagrees, that is a finding and not a bug in your page** — stop, write it down
with the document that produced the disagreement, and bring it to Davide. A
disagreement found here is worth more than the page.

The recipe is in `contract/README.md` under "Fingerprints (canonical form)" and
in `node-protocol.md` §6.3. Canonical JSON is UTF-8, keys sorted at every depth,
no insignificant whitespace. Read those, not the Python — a reimplementation
derived from our implementation checks nothing.

**(b) Publish the validator alone.** `contract/validate.py` is one file. It
needs `jsonschema`. Offer it as a download with a one-line command. Weaker than
(a) — the visitor runs our code — but honest, and it works today.

**(c) Wait for #49.** Publishing to PyPI closes this properly and PR #72 already
carries the machinery, held open deliberately. Not your decision and not a
blocker you can resolve; name it if you think the page should wait.

---

## What the page may not claim, and why the refusals are the product

The shipped verifier answers on all seven levels **including the ones it did not
reach**, and it refuses rather than downgrading. Your page inherits that
discipline exactly:

- **an anchor is a claim until somebody looks.** The validator contacts no
  network, so existence is *declared, not checked* — with the explorer URL
  beside it. An earlier version read the state out of the receipt and believed
  it, which let a holder mint the property by typing it. Do not write
  "verified on-chain" anywhere. Write what is true: *this root was published in
  this transaction; here is where to look.*
- **a witness acknowledgement does not establish execution continuity**, because
  we do not hold the key to check its signature. Present, bound, not checked.
- **a refusal outranks a violation.** Saying "this document is wrong" about a
  document that may be perfectly correct on a chain we cannot read is the wrong
  answer, and `contract/README.md` says so in those words.

If the page ever has to choose between sounding strong and being checkable,
it is checkable. That choice **is** the product.

---

## Practical

- The route exists: `ui/app/motus/attack/page.tsx` (385 lines) and
  `ui/app/motus/page.tsx` (904 lines). The artefact is served from
  `ui/public/motus/attack_this_run.json`. Rewrite the attack page against this
  brief; the txid-from-payload handling in it is already correct and stays.
- Nile is a **testnet**. Never touch mainnet, and never handle a key: the
  anchoring already happened and its receipt is committed. You need no
  credentials for any of this work, and if a step seems to need one, stop.
- Do not contact `167.86.119.200` for any reason, including reads.
- Mobile first. Beat 3 must be legible on a phone held by somebody standing up.
- Every command you put on the page, run first. Every number, measure first.

## What to bring back before you build

1. which of (a), (b), (c) you chose, and why;
2. if (a): the fixture-by-fixture comparison against `derived_root`, and any
   disagreement, before you write UI;
3. anything in this brief the repository contradicts — that has happened, it
   will happen again, and reporting it is worth more than working around it.
