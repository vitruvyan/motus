---
name: adr
description: Draft an Architecture Decision Record in the Motus house style. Use when a decision changes the contract, adds public API, or resolves a question where the obvious answers are each wrong in a different way. ADRs are accepted by the founder, never by the author.
---

# Writing a Motus ADR

An ADR exists so a later reader knows **why**, including why the obvious
alternative was rejected. If the decision is obvious, it does not need one.

## Authority

`adr/ADR-001` → `contract/` → frozen corpora → implementation. An ADR is how
`contract/` changes; nothing else is. Amending a contract file without an ADR
inverts the order the whole project rests on.

**You propose. The founder accepts.** Status starts `PROPOSED`, and the
`Accepted:` line names the founder and the date — see ADR-007, 008, 009 for
the pattern. Never mark your own ADR `ACCEPTED`.

## Shape

Header: Status, Date, Authority, Depends on, **Amends** — and be precise about
what is amended. ADR-011 amends one clause of ADR-004 and one clause of
`guarantees.md`; saying "amends ADR-004" would have been false.

Then: **Context** (what is actually true, with measurements), **Decision**
(numbered, each independently checkable), **Consequences** (including what is
given up), **Alternatives rejected** (with the reason each is wrong).

## What makes these ADRs worth reading

**Numbers, not adjectives.** "The absolute figure moves 55 % between identical
jobs; the ratio moves 3 points" settles an argument. "Measurements are noisy"
does not.

**State the cost you are accepting.** ADR-010 records that its fix gives up a
repaired artifact in the clean-refusal case. A decision that only lists
benefits has not been thought through.

**Record wrong turns.** ADR-010 documents a fix that was implemented, went
green, and was then found wrong — and why the mistake was inviting. That
section stops the next reader repeating it, which is worth more than looking
decisive.

**Say when something is a guess.** If a decision rests on an unmeasured
assumption, name it as a hypothesis with a falsification plan, as ADR-012 does
with H1. Do not let a guess enter the record wearing the clothes of a finding.

**Numbers you did not choose.** If a threshold appears, say what it was derived
from. A budget picked to accommodate the change in front of it is not a budget.
