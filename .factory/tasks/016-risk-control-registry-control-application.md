# TASK 016 — Risk & Control Registry / ControlApplication architecture, regulatory-roadmap point 3

Branch: `feat/risk-control-v1`.

Status: ADR-036 accepted by the founder on 2026-09-21. Contract-first implementation proceeds one independently verified micro-step at a time.

Read first: `AGENTS.md`, ADR-001, ADR-020, ADR-027, ADR-034, ADR-035, `contract/README.md`, `contract/system-manifest.v1.schema.json`, and `src/vitruvyan_motus/system_manifest.py`.

## Goal

Define the canonical boundary between declarative Risk & Control Registry data and execution-scoped ControlApplication evidence without introducing regulatory verdicts or changing existing execution wire formats.

## Gate

1. ADR-036 was accepted by the founder on 2026-09-21.
2. Split implementation into contract-first micro-steps:
   - registry schema + semantic invariants;
   - ControlApplication schema + semantic invariants;
   - canonical derived fingerprints;
   - executable validator/CLI;
   - public runtime verification/query surface;
   - focused mutation-proof tests;
   - adversarial review;
   - Jenkins green;
   - merge.
3. Do not combine a later micro-step into the registry-contract change merely because the ADR now permits it.
4. No storage, UI, HTTP, MCP, Orbis or Limen implementation in the contract micro-steps.
5. No edits to `tests/contract/` or `tests/compat/`.
6. No new runtime dependencies.
