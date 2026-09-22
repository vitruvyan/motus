# TASK 017 — Regulatory Evidence Profile v1 architecture, regulatory-roadmap point 5

Branch: feat/regulatory-profile-v1.

Status: ADR-038 ACCEPTED by the founder on 2026-09-22. Contract-first implementation proceeds one independently verified micro-step at a time.

Read first: AGENTS.md, ADR-001, ADR-020, ADR-027, ADR-034, ADR-035, ADR-036, ADR-037, contract/README.md, src/vitruvyan_motus/evidence_api.py, src/vitruvyan_motus/system_manifest.py, src/vitruvyan_motus/risk_control.py, and src/vitruvyan_motus/human_oversight.py.

## Goal

Add the smallest jurisdiction-neutral mechanism that lets an external profile map opaque requirement references to evidence types Motus already owns, then deterministically report whether that evidence is missing, not verified, mismatched, or matched.

This task does **not** build a compliance engine.

Target release line after acceptance: **0.17.0**.

## Non-negotiable boundary

Point 5 must compose Point 1–4. It must not redesign them.

If implementation requires a semantic or wire-format change to trace, GraphSpec, commitment/checkpoint/receipt, System Manifest, Risk & Control Registry, ControlApplication, HumanOversightReceipt, or evidence package, stop and return to ADR review before changing code.

## Gate

1. Founder accepts ADR-038.
2. Contract-first implementation proceeds in independently verifiable micro-steps:
   - profile schema and semantic invariants;
   - derived profile fingerprint;
   - validator and CLI support;
   - deterministic read-only assessment API using existing Motus validators/verifiers;
   - neutral fixtures only;
   - focused regression and mutation-proof tests;
   - adversarial review;
   - Jenkins green;
   - merge.
3. No legal/framework content in the first implementation. Do not ship AI Act, ISO 42001, NIS2, D.Lgs., procurement, or customer-specific mappings as part of the Motus core mechanism.
4. No storage, UI, HTTP, MCP, scheduler, workflow engine, DB, ontology, vector store, LLM, plugin system, or legal-text ingestion.
5. No edits to tests/contract/ or tests/compat/.
6. No new runtime dependencies.
7. Do not introduce compliant, non_compliant, certified, conformant, passed, safe, or equivalent verdict fields.
8. A profile assessment must distinguish evidence absence from unverifiable or contradictory evidence.
9. Existing Motus validators and binding verifiers remain authoritative; the new layer composes them rather than reimplementing them.
10. Keep the v1 profile vocabulary closed and deliberately weak. Do not add arbitrary predicates or a rules DSL merely to anticipate future requirements.

## Proposed v1 implementation envelope after ADR acceptance

Expected new surfaces, subject to contract review:

- contract/regulatory-evidence-profile.v1.schema.json
- additions to contract/validate.py
- src/vitruvyan_motus/regulatory_profile.py
- public exports from src/vitruvyan_motus/__init__.py
- neutral fixtures in the next free fixture range
- focused contract and assessment tests
- one permanent mutation-probe definition

Expected public capability: a single assessment entry point taking a profile plus supplied evidence and returning deterministic machine-readable findings.

Orbis may later render those findings as an Evidence Matrix, gap analysis, dossier, or audit workflow; Motus itself does none of those things.

## Stop conditions

Stop implementation and report rather than expanding scope if any of these becomes necessary:

- arbitrary field predicates;
- temporal rule language;
- cardinality expressions;
- separation-of-duties policy;
- nested boolean logic;
- external network calls;
- legal interpretation;
- framework-specific source ingestion;
- persistent profile registry;
- runtime policy enforcement.

A real integration that needs one of those is evidence for a later ADR.

## Definition of done

Point 5 / 0.17.0 mechanism work is complete only when:

1. accepted ADR and contract agree;
2. existing Point 1–4 artifacts remain unchanged in semantics and wire format;
3. profile validation is fail-closed;
4. profile identity is canonically derived;
5. assessment uses existing evidence verifiers;
6. missing, not_verified, mismatched, and matched are covered by tests;
7. no compliance verdict can be emitted;
8. no framework-specific mapping is required for the test suite;
9. mutation probes prove the focused tests detect disabled invariants;
10. adversarial review has no unresolved verified finding;
11. Jenkins is green on the exact reviewed head;
12. main is green after merge.
