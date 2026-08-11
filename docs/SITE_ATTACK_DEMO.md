# Build the "Attack this run" demo into vitruvyan.com/motus

Paste this whole file as the opening prompt of a session working in the
`frontier` repository. You are building one page.

---

## What you are building, and why it has to be exact

Motus's claim is that its trace can be checked by someone who does not trust
whoever wrote it. That claim is abstract until a reader watches a document get
tampered with and sees who notices. This page is that demonstration.

It shows one verdict — a security review desk approving the release of an
advisory — recorded twice: as an **event log** of the kind most systems keep,
and as a **Motus trace**. Then the same eight tamperings are applied to both,
and the page reports which of them were visible.

```
manomissione                              event log      traccia Motus
Ribalta il verdetto                         beccata   beccata (validatore)
Declassifica una fonte                      beccata   beccata (validatore)
Cancella un passo                           beccata   beccata (validatore)
Riscrive di chi e' la run                invisibile   beccata (validatore)
Cambia la policy                         invisibile   beccata (validatore)
Duplica un passo                         invisibile   beccata (validatore)
Ribalta il verdetto E RISIGILLA          invisibile   beccata (ancora)
Riscrive l'intestazione, link stantio    invisibile   beccata (validatore)
                                                3/8                8/8
```

**The two rows that carry the argument, and which you must not flatten into
the other six:**

- **the seventh.** An editor who has read the published contract recomputes
  every hash after editing. The tampered trace is then internally perfect and
  passes *both* checks. The only thing that disagrees is a root published
  somewhere the editor cannot reach. This row is the case for anchoring, and it
  is the reason the page exists;
- **the eighth.** The validator catches it, but the *raw* terminal field still
  carries the published root — so anyone comparing that field to an anchor sees
  agreement. Only the accessor, which recomputes, answers "no". This row is why
  the API has an accessor at all.

## The data is given to you. Do not invent any of it.

Every verdict on this page was produced by `contract/validate.py` running in a
separate process against a real Motus run. **You cannot regenerate it**: Motus
is a Python runtime, the site is Next.js, and a browser cannot execute it. The
payload is shipped as a build-time artifact:

```
motus/demo/out/attack_this_run.json      <- copy into the frontier repo
```

Copy it to `ui/public/motus/attack_this_run.json` (or import it directly as a
JSON module — your call, but it must be a build-time asset, not a fetch to an
API that does not exist). Its shape:

```jsonc
{
  "generated_by": "demo/attack_this_run.py (vitruvyan/motus)",
  "schema_version": "3.0.0",
  "run":   { "run_id": "sub-4471", "policy": "strict", "metadata": {...}, ... },
  "root":  "sha256:0a2810b9…",           // 71 characters, not 64
  "record_count": 14,
  "citations": [ { "doc": "...", "page": 4, "tlp": "TLP:AMBER" }, ... ],
  "baseline": { "log": "accepted: …", "trace": "accepted" },
  "timeline": [ { "event_type": "transition", "source": "decide", ... } ],
  "records":  [ … the trace records, if you want to show one … ],
  "tampers": [
    {
      "title": "Ribalta il verdetto",
      "detail": "APPROVED diventa REJECTED",
      "log":   { "caught": true,  "message": "evt-0009: payload_hash does not match the payload" },
      "trace": { "caught": true,
                 "caught_by": "validatore",      // "validatore" | "ancora" | "nessuno"
                 "message": "T11 $.records[2]…", // the validator's actual first line
                 "raw_field_unmoved": true,      // the raw terminal field did not move
                 "derived_root": null }          // what trace.root returns: null = do not anchor
    }
  ],
  "totals": { "log": 3, "trace": 8, "of": 8 }
}
```

**Render `trace.message` verbatim, in a monospace face.** It is the validator's
own output. Rewriting it into friendlier prose destroys the only thing that
makes the page an argument rather than a claim: the reader is supposed to see
that the verdict was not written by us.

If a number on screen disagrees with `totals`, the page is wrong. Do not
hardcode 3/8 anywhere; compute from the payload.

## Where it goes

- **`app/motus/attack/page.tsx`** — the demonstration, full width.
- A short block in **`app/motus/page.tsx`**, as its own `SectionReveal`, that
  states the claim in two sentences and links to it. Place it after the section
  that introduces the trace, not in the hero.

## The design system is not optional

`app/motus/page.tsx` is the reference for this page's register and you should
read it before writing anything.

- **Colour comes from tokens, never from hex.** `var(--paper)`, `var(--raised)`,
  `var(--surface)`, `var(--ink)`, `var(--ink-secondary)`, `var(--ink-faint)`,
  `var(--rule)`.
- **Motus is identified by verderame** — `var(--verderame)` — and the page root
  carries `dark`, exactly as the Motus page does, so the subtree resolves to the
  night register while the rest of the site keeps the reader's choice.
- **The three pigments name products, not states.** Do not press
  `var(--sanguigna)` into service as "error red" across the table. Refused paths
  and limits are drawn from the ink scale. If you need one accent for "this was
  caught", verderame is the one you have; distinguish "invisible" by weight and
  by the ink scale, not by inventing a second pigment.
- **Reuse, do not rebuild:** `SectionReveal` (`num`, `dark`), `Card` / `CardGrid`
  from `components/specimen/card` (`pigment`, `index`, `title`, `meta`, `as`),
  `Typewriter`, `AccentWord`. There is a `MotusTraceCanvas` already on the Motus
  page — look at it before drawing anything of your own.
- Copy in **Italian**, matching the register of the existing Motus page.

## The shape of the page

1. **The verdict, as a review screen.** Header (desk, reviewer, timestamp,
   policy, record count), question and answer side by side, the seals, the
   cited sources with their TLP marking. This is the "before": an ordinary,
   credible admin screen. A reader must believe this is a real decision before
   they can care that it was tampered with.
2. **The root**, set apart and quoted in full. One line beside it: this is the
   only value you publish. Say that it is 71 characters and not 64 because it
   names its hash function — an integrator sizing a carrier needs that, and it
   costs one sentence.
3. **The eight tamperings.** One row each, two verdict columns. Let the reader
   trigger them — click a row, or step through them — rather than presenting a
   finished table; the demonstration is the *act* of tampering. Reveal
   progressively, but make the full table reachable without interaction (a
   reader on a phone, or a crawler, must still get the argument).
4. **The two rows that matter**, given their own treatment after the table. Do
   not rely on the reader inferring why the seventh and eighth are different.
5. **A closing note on what this does not prove.** Motus ships no anchor. The
   chain proves internal consistency; immutability needs a root published where
   the operator cannot rewrite it, and that interface is still being designed.
   **Do not write "immutable" anywhere on this page.**

## What would make this page a lie

State these limits or omit the claims that need them:

- the event log here is **not a strawman**: it carries a `payload_hash` per row,
  which is the strongest thing such a table usually has, and the page credits it
  with catching all three payload edits. Say so. A demo that wins by weakening
  its opponent convinces nobody who knows the subject;
- "the trace catches everything" is false. The seventh is caught by the
  anchor, not by the validator, and Motus does not ship the anchor;
- the root commits to the run **as parsed**: numbers commit at binary64
  precision (motus issue #74). If the page shows a numeric value in the trace,
  do not claim that value is committed exactly.

## Done means

- `pnpm build` (or the repo's build command) passes, no new lint errors;
- every number on screen derives from the payload;
- the page reads correctly in both light and dark register, and at 375px wide;
- no `fetch` to a runtime API — the artifact is a build-time asset;
- `motus/demo/attack_this_run.py --json` regenerates the payload, and the page
  keeps working when it does.
