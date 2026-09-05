You are motus-lead. After TASK 006 (#132) is reported, start this one: `git fetch origin && git checkout -b
factory/125-violations-null origin/main`. Read: adr/ADR-029, GitHub issue #125, src/vitruvyan_motus/runtime.py
(`_declaration_violations` :1206), contract/trace.v1.schema.json (`Transition.violations`, the SB4 text in the
description, `x-current-version`), contract/validate.py (SB4, version scoping as ADR-019/026 do it),
src/vitruvyan_motus/__init__.py (TRACE_SCHEMA_VERSION), tests/test_contract_fixtures.py. Flow: architect (claude,
because the version bump touches the validator's scoping) → ONE implementer (pi luna) → verifier (pi luna) → TWO
adversaries (claude): (1) old traces under the new validator and new traces under old readers; (2) the
half-declared node. Never commit/push. Frozen dirs untouched. REPORT.md with MUTATION TARGETS.

# TASK 007 — #125 under ADR-029: a violations list says whether it ran

1. Contract: `Transition.violations` admits `null` from trace schema 3.1.0 (schema `x-current-version` 3.1.0;
   the description's SB4 gains the iff clause; the README's rule table too). TRACE_SCHEMA_VERSION = "3.1.0".
   Validator: `null` is refused on documents < 3.1.0 (as today) and admitted from 3.1.0; with a spec, SB4
   checks `null` iff the node declares nothing (both halves undeclared) and reports a violation either way it
   is broken. Version-scoped exactly the way ADR-019/ADR-026 rules are.
2. Runtime: `_declaration_violations` returns `None` when neither half is declared; the transition record
   writes `violations: null`. Nothing else changes; the half-declared node keeps the list for its half.
3. Fixtures: new 3.1.0 fixtures pinning the three values (null / [] / [..]), a spec-bound fixture for each
   direction of the SB4 violation; every existing fixture untouched and still green.
4. Tests fail without the code; frozen paths PASS; the packaging test (`test_schema_version.py`) agrees with
   the new version; the SLO gate untouched (measure: the write path gains one `None` check, nothing else).
5. README migration note for consumers: "from 3.1.0, `violations` may be null; a reader that does
   len(violations) unguarded breaks" — the orbis#63 pattern. Note it for the 0.14.0 release notes.
