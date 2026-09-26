# TASK 022 — Verification and query API/CLI v1

Parent: ADR-043, accepted by the founder on 2026-09-26.

Branch: `feat/verification-query-api-cli-v1`.

Target release: **0.22.0**.

## Goal after acceptance

Add the smallest transport-neutral, read-only facade and CLI that explicitly
inspect and verify existing Motus artifact kinds and produce deterministic
projections over an exact caller-supplied collection.

The surface unifies access to existing authoritative validators. It is not a
compliance engine, evidence store, discovery service, general query language,
network service, or source of global current state.

## Gate

1. ADR-043 was accepted by the founder on 2026-09-26.
2. Implementation proceeds contract-first in independently
   verifiable micro-steps:
   - closed artifact-kind and operation vocabulary;
   - stable request/result envelope and resource bounds;
   - structural inspection and exact identity derivation;
   - explicit dispatch to existing binding, lineage, package, dossier, and
     assessment verifiers;
   - bounded deterministic projections over exact supplied collections;
   - distinct CLI entry point with human and JSON renderings;
   - neutral fixtures, focused tests, permanent mutation probes;
   - independent verification and adversarial closure;
   - release characterization and green Jenkins evidence.
4. No edits to `tests/contract/` or `tests/compat/`.
5. No new runtime dependencies.
6. No change to existing artifact, trace, evidence-package, dossier, or
   `motus-validate` contracts.
7. No artifact-kind guessing, hidden discovery, silent candidate selection,
   global-current inference, or compliance/legal verdict.
8. No database, filesystem search, object store, network, HTTP, MCP, UI, LLM,
   plugin system, arbitrary query language, or policy DSL.

## Proposed implementation envelope after ADR acceptance

Expected surfaces, subject to contract review:

- one versioned interface contract for requests/results that does not declare
  them evidence artifacts;
- strict additions to the contract dispatcher only where needed to reuse
  canonical validation and fingerprint rules;
- one new public module for the facade;
- one distinct packaged CLI entry point;
- neutral fixtures and focused dispatch, ambiguity, scope, bounds, determinism,
  compatibility, and rendering tests;
- public exports only after contract and behavior are independently verified;
- one permanent mutation-probe definition.

The implementation must compose existing domain verifiers rather than duplicate
them. Query projections must be closed, deterministic, based only on validated
supplied artifacts, and explicit about conflicts and incomplete scope.

## Stop conditions

Stop and return to ADR review if implementation needs to infer artifact kind,
fetch evidence, select a globally latest/current record, treat input order as
authority, suppress forks or conflicts, weaken an existing verifier, emit a
legal/compliance/safety verdict, mutate evidence, change an existing wire
format, add arbitrary predicates or executable query logic, or add a runtime
dependency.

## Definition of done

Point 10 / 0.22.0 is complete only when:

1. accepted ADR, interface contract, Python facade, and CLI agree;
2. every supported kind and operation is explicit and closed;
3. existing validators and domain verifiers remain the semantic authority;
4. structural, identity, binding, lineage, transport, conflict, and scope
   findings remain distinguishable;
5. identical exact inputs produce identical structured results and CLI JSON;
6. missing companions, duplicates, ambiguous candidates, competing revisions,
   forks, and incomplete lineage fail closed or remain visible as specified;
7. query results claim only the exact supplied-set scope;
8. `motus-validate` and all existing public APIs remain compatible;
9. resource bounds and hostile-input behavior are tested;
10. focused and full tests, packaging, frozen-path guards, and lazy import pass;
11. mutation probes kill every targeted invariant;
12. independent and adversarial review have no unresolved verified finding;
13. Jenkins is green on the exact reviewed head and on `main` after merge;
14. release `v0.22.0` is characterized and closed under repository release
    discipline.

## Current checkpoint — 2026-09-26

- dedicated branch created from `origin/main` at
  `3e40983ad4379b208854e371f2eff5145d1c828b`;
- ADR-043 accepted by the founder on 2026-09-26;
- contract implementation is now authorised but has not yet started.
