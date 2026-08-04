# ADR-003 — Documentary errata: accepted status, test placement, kernel alias

| | |
|---|---|
| Status | **ACCEPTED** |
| Date | 2026-08-04 |
| Deciders | Davide Baldoni, founder · Codex, independent review · Claude, contract authorship |
| Approved by | Davide Baldoni, founder — 2026-08-04 |
| Reviewed at | `83bcb0a1a39d82a11c0007632e4e07cb7dc99ac9` |
| Independent review | Codex — PASS, no findings |
| Amends | Wording and placement only, in `contract/` and `adr/ADR-001`. No schema version, no R/T/E/SB rule, no runtime behavior, no SLO. |

## Context

ADR-001 was accepted on 2026-08-03 and ADR-002 marked the contract surfaces
active. Three normative surfaces were missed by that pass and still describe
themselves as drafts pending a signature that has since been given. Two further
points block the implementation phase:

1. `contract/README.md` rule 3 requires a schema-version test inside
   `tests/contract/`, which is mechanically frozen and predates the package —
   the test cannot be created where the contract puts it;
2. the frozen corpora use the bare name `Decision` for the LEGACY type, while
   ADR-001 forbids any package-level alias — the corpora and the naming rule
   need an explicit, bounded reconciliation before the compatibility switch.

None of this changes what the contract requires of a runtime.

## Decision

### 1. Current status wording

Exactly these substitutions, and no other text in the affected strings:

- `contract/trace.v1.schema.json`, field `description`, incipit:
  `"DRAFT v1 (post cross-review round 5). "` →
  `"v1 — accepted by ADR-001 on 2026-08-03. "`
- `contract/graphspec.v1.schema.json`, field `description`, incipit:
  `"DRAFT v1. "` → `"v1 — accepted by ADR-001 on 2026-08-03. "`
- `contract/graphspec.v1.schema.json`, field `description`, tail: remove
  ` [OPEN-07 resolution: approved by the cross-review in this SCC form; founder signature pending independent reproduction.]`
  R11 itself is unchanged; only the pendency note goes.
- `contract/node-protocol.md` line 3: `**Status: DRAFT v1.**` →
  `**Status: v1 — accepted by ADR-001 on 2026-08-03.**`
- `adr/ADR-001`, section "Decisions for the founder (from the cross-review)":
  each of the three sentences `**Founder signature pending independent
  reproduction[...]**` → `**Ratified with ADR-001's acceptance on 2026-08-03.**`

The **Review round record** in ADR-001 is NOT touched. It is the historical
register and it was accurate when written; rewriting history to look tidy is
the opposite of what this contract is for.

### 2. Where the schema-version test lives

`contract/README.md` rule 3 is restated as a forward obligation, because at the
time this errata merges the Motus package does not exist yet and no such test
can truthfully be said to be present:

> The first Motus runtime commit MUST add `tests/test_schema_version.py`,
> outside both frozen corpora, asserting that the trace schema version constant
> single-sourced in the package equals the const in
> `contract/trace.v1.schema.json`. The frozen corpora predate the package and
> cannot host it.

Milestone A of the implementation satisfies this obligation in the same commit
that introduces the package.

### 3. The kernel switch alias

`vitruvyan_motus.compat` MUST NOT export a public symbol named `Decision`. The
frozen corpora nonetheless use that bare name for the legacy type
(`tests/compat/terraveler/test_frozen_surface.py:147`). This is reconciled by
ONE authorized local alias, inside `tests/contract/kernel.py` only:

    from vitruvyan_motus.compat import LegacyDecision as Decision

That is an alias in a test module, not in the package. It is authorized only
for `tests/contract/kernel.py`, only as part of the compatibility switch, and
it does not extend to any other file, to `compat.py`, or to any consumer.

At that switch, `kernel.py` may change exactly three things: its import block,
the `KERNEL_UNDER_TEST` string, and the docstring example that documents those
imports. `__all__` keeps the same names and no assertion in either corpus
changes.

## Consequences

- No schema version changes; `schema_version` stays `1.0.0`.
- No R-, T-, E-, SB-, H-, J- or JSONL-rule changes; `contract/validate.py` and
  all 110 fixtures are untouched, and the full suite must stay green after this
  errata — that is the proof no rule moved.
- No runtime behavior, package boundary, Motus 0.5 scope or SLO target changes.
- The implementation phase can begin: its first milestone can create the
  schema-version test in a legal location, and its compatibility switch has an
  authorized form.

## Verification before merge

1. `python -m pytest tests/ -q` in the pinned environment, unchanged result.
2. `contract/validate.py` and every file under `contract/fixtures/` are
   byte-identical to the base revision.
3. Nothing under `axis/`, `orders/`, `tests/contract/` or `tests/compat/` is
   modified.
4. The diff touches exactly these paths and no others:
   - `adr/ADR-003-documentary-errata.md` (this file, added)
   - `adr/ADR-001-trace-first-single-semantics.md` (three current sentences;
     the historical Review round record untouched)
   - `contract/trace.v1.schema.json` (description incipit)
   - `contract/graphspec.v1.schema.json` (description incipit and the removed
     pendency note)
   - `contract/node-protocol.md` (status line)
   - `contract/README.md` (rule 3)

## Approval

Approved by Davide Baldoni, founder, on 2026-08-04 against reviewed SHA
`83bcb0a1a39d82a11c0007632e4e07cb7dc99ac9`, following Codex's independent
PASS with no findings. This acceptance records the decision only; it introduces
no additional contract or runtime change.
