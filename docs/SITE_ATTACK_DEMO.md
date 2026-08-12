# Build "Attack this run" — the Motus proof page for vitruvyan.com

Paste this whole file as the opening prompt of a session in the `frontier`
repository. You are building one page and one link to it.

---

## Who this is for, and what they must leave with

Not a peer reviewer. **A CTO evaluating us, or an investor doing diligence.**
Ninety seconds, often on a phone or projected in a room. They do not know what
Motus is and they will not read a specification.

They must leave able to repeat one sentence to a colleague:

> *Every decision their system makes is recorded in evidence that not even they
> can rewrite — and the proof is published on a public blockchain that I can go
> and read myself.*

Every section serves that sentence. If a section does not, cut it. This page is
a **demonstration**, not a paper: the reader's belief comes from watching
something happen and then verifying it on a site we do not control, not from
being told.

## The three beats, in this order

**1. A real decision, in a screen they recognise.**
A security review desk approves the release of an advisory. Header, question and
answer, the seals, the cited sources with their TLP markings. It has to look
like an ordinary, credible internal system — an admin screen they have seen a
hundred times. Nobody cares that a fake thing was tampered with.

**2. They attack it. The page does not attack it for them.**
Eight tamperings, each a control the reader triggers. Two verdict columns side
by side: **an ordinary event log** (the kind almost every company already has)
and **the Motus trace**. As they fire each attack, the two columns disagree, and
a running score climbs: the log ends at 3 of 8, the trace at 8 of 8.

The act is the argument. A finished table presented on arrival is worth a
fraction of a table the reader fills in by clicking.

**3. The proof is on a public chain, and they go and read it.**
The trace's root — one 71-character string — was published in a TRON
transaction. The page shows the root, shows the transaction's memo field, and
links out to the block explorer. The reader compares the two strings with their
own eyes on a site we do not run.

**Beat 3 is the close and it is the reason the page converts. Do not bury it
below the fold of a long technical section, and do not render it as a badge.**
It gets its own full section, with room.

## The data is given to you. Do not invent any of it — and above all not the txid.

Every verdict on this page was produced by `contract/validate.py` running in a
separate process against a real Motus run. **You cannot regenerate it**: Motus
is a Python runtime, the site is Next.js, a browser cannot execute it. The
payload is a build-time artifact:

```
motus/demo/out/attack_this_run.json   ->  ui/public/motus/attack_this_run.json
```

Import it as a JSON module or read it at build time — your call, but it must be
a build-time asset. **No `fetch` to a runtime API: there is no API.**

```jsonc
{
  "generated_by": "demo/attack_this_run.py (vitruvyan/motus)",
  "schema_version": "3.0.0",
  "run":   { "run_id": "sub-4471", "policy": "strict", ... },
  "root":  "sha256:0a2810b9…",            // 71 chars: 7 for "sha256:" + 64 hex
  "record_count": 14,
  "citations": [ { "doc": "...", "page": 4, "tlp": "TLP:AMBER" }, ... ],
  "baseline": { "log": "accepted: …", "trace": "accepted" },

  "anchor": {                              // BEAT 3. See the rule below.
    "network": "nile",                     // "nile" (public TRON test network)
    "txid": "4891b6f4…",                   // null until we have published it
    "memo": "VITRUVYAN_AUDIT:sha256:0a2810b9…",
    "explorer_url": "https://nile.tronscan.org/#/transaction/4891b6f4…",
    "published_at": "2026-08-12T…Z",
    "block": 12345678
  },

  "tampers": [
    { "title": "Flip the verdict",
      "detail": "APPROVED becomes REJECTED",
      "log":   { "caught": true, "message": "evt-0009: payload_hash does not match the payload" },
      "trace": { "caught": true,
                 "caught_by": "validator",   // "validator" | "anchor" | "nobody"
                 "message": "T11 $.records[2]…",  // the validator's actual first line
                 "raw_field_unmoved": true,
                 "derived_root": null } }
  ],
  "totals": { "log": 3, "trace": 8, "of": 8 }
}
```

**The txid rule, and it is absolute.** If `anchor.txid` is `null`, render the
anchor section in an explicit *"publication pending"* state, or leave the
section out and tell me. **Never write a transaction hash that is not in the
payload** — not a placeholder, not a plausible-looking hex string, not one
copied from another page of this site. The entire value of beat 3 is that the
reader can open the link and find it. A hash that 404s on Tronscan is worse
than no blockchain section at all: it is the one thing that ends an evaluation.

If a number on screen disagrees with `totals`, the page is wrong. **Do not
hardcode 3/8 anywhere** — compute from the payload, because the payload is
regenerated.

**Render `trace.message` verbatim, in a monospace face.** It is the validator's
own output. Rewriting it into friendlier prose destroys the only thing that
makes the page an argument instead of a claim: the reader is meant to see that
the verdict was not written by us.

## The section on the chain — how to build beat 3

Give it these four things, and nothing else competing for attention:

1. **The root, quoted in full**, big enough to read across a room, monospace,
   character-selectable. One line beside it: *this is the only value we publish
   — the decision itself never leaves your infrastructure.* That line answers
   the first objection a CTO has ("you are putting my data on a blockchain?")
   before they raise it, which is worth more than any feature bullet.
2. **The transaction's memo field**, quoted the same way, directly beneath, so
   the two strings sit one above the other and the eye does the comparison.
3. **The link out.** `anchor.explorer_url`, opening in a new tab, labelled as
   what it is: *read the transaction on Tronscan*. Not a badge. Not an icon. A
   real, obviously clickable thing that a sceptic will click.
4. **One sentence of honesty, stated plainly rather than hidden:** published on
   the TRON **Nile public network**; mainnet is a change of endpoint, not of
   design. State it once, in normal type. A reader who discovers it themselves
   distrusts everything else on the page; a reader who is told finds it
   unremarkable.

Say why the anchor is what makes the difference, in one sentence a
non-cryptographer follows: *we can rewrite the file on our own servers; we
cannot rewrite a transaction that is already in a public blockchain's history.*

## The word "immutable" — you may now use it, once, and only about the anchor

The previous version of this brief forbade it outright, because we had nothing
published. With a real transaction that changes, but the scope does not: the
**anchored root** is immutable. The trace file on our disk is not — it is
*verifiable*, which is a different property and the one the eight attacks
demonstrate.

So: "the root is anchored on a public chain and cannot be altered after the
fact" — yes. "Motus produces immutable logs" — no, and any technical reader
will take that sentence apart in the meeting.

## What must not appear on this page

Three claims. Each one is the kind that a hostile technical reader tests first,
and each is false:

- **that the event log is a strawman.** It is not, and saying so is the strongest
  move available to us. It carries a `payload_hash` on every row — the strongest
  thing such a table usually has — and the page credits it with three catches out
  of eight. *"We compared ourselves to the best version of what you already
  have"* is a far better line than a rigged fight;
- **that the trace catches everything by itself.** One of the eight is caught by
  the anchor, not by the validator. That row is not an embarrassment, it is the
  sales argument for beat 3 — the attacker who knows our format, reseals every
  hash perfectly, and is still caught by the one value they could not reach.
  Give that row its moment;
- **that the run's numeric values are committed exactly.** The root commits to
  numbers as parsed, at binary64 precision. Don't put a numeric quantity on
  screen and claim that exact figure is sealed.

## For the technical reader, below the fold

Two of the eight rows are subtler than the others, and a CTO's engineer will
ask. Put them in an expandable block **after** the main table — reachable,
never in the way of the ninety seconds:

- **the resealed verdict.** An editor who has read our published format
  recomputes every hash after editing. The trace is then internally perfect and
  passes both checks. Only the root published where they cannot reach it
  disagrees;
- **the stale first link.** The validator catches it, but the *raw* terminal
  field still carries the published root — so anyone comparing that field to the
  anchor sees agreement. Only the accessor, which recomputes from the document,
  answers "no". This is why the API has an accessor at all.

## Where it goes

- **`app/motus/attack/page.tsx`** — the page, full width.
- A block in **`app/motus/page.tsx`** as its own `SectionReveal`, after the
  section that introduces the trace, not in the hero: two sentences and a link.
  Lead with the chain, not with the tampering — *"the proof of a decision,
  published where we cannot reach it"* is what makes someone click.
- Consider a link from **`app/orbis/page.tsx`** too, where auditability is
  claimed: this page is that claim's evidence. One line, your judgement.

## The design system is not optional

Read `ui/app/motus/page.tsx` before writing anything. It is the reference for
register and structure.

- **The site is in English.** All copy on this page is English, matching the
  register of the existing Motus page. The payload's labels are already English;
  the validator's messages are its own output and stay verbatim.
- **Colour from tokens, never hex**: `var(--paper)`, `var(--raised)`,
  `var(--surface)`, `var(--ink)`, `var(--ink-secondary)`, `var(--ink-faint)`,
  `var(--rule)`.
- **Motus is identified by verderame** — `var(--verderame)` — and the page root
  carries `dark`, exactly as the Motus page does, so the subtree resolves to the
  night register while the rest of the site keeps the reader's choice.
- **The three pigments name products, not states.** Do not press
  `var(--sanguigna)` into service as a generic error red across the table.
  "Caught" is verderame; "invisible" is distinguished by weight and by the ink
  scale, not by a second pigment.
- **Reuse, do not rebuild:** `SectionReveal` (`num`, `dark`), `Card`/`CardGrid`
  from `components/specimen/card`, `Typewriter`, `AccentWord`, and
  `MotusTraceCanvas`, which is already on the Motus page — look at it before
  drawing anything of your own. There is a `BlockchainLedgerBadge` in
  `components/blockchain/`; read it for the explorer-URL convention, but do not
  reduce beat 3 to that badge.

## Done means

- `pnpm build` passes with no new lint errors;
- **every number on screen derives from the payload** — nothing hardcoded;
- **the txid on screen is the txid in the payload**, and the explorer link opens
  a transaction that exists;
- the full table and the score are reachable **without any interaction** — a
  phone reader, a crawler, or a screen reader still gets the whole argument;
- it reads correctly in both registers and at 375px wide;
- no `fetch` to a runtime API;
- `motus/demo/attack_this_run.py --json` regenerates the payload and the page
  keeps working when it does.

If you finish and the page still needs a paragraph of explanation before a
stranger understands what they are looking at, it is not finished.
