# TASK 016 — Risk & Control Registry / ControlApplication architecture, regulatory-roadmap point 3

Branch: `feat/risk-control-v1`.

Status: architecture gate. No contract/runtime implementation until ADR-036 is accepted by the founder.

Read first: `AGENTS.md`, ADR-001, ADR-020, ADR-027, ADR-034, ADR-035, `contract/README.md`, `contract/system-manifest.v1.schema.json`, and `src/vitruvyan_motus/system_manifest.py`.

## Goal

Define the canonical boundary between declarative Risk & Control Registry data and execution-scoped ControlApplication evidence without introducing regulatory verdicts or changing existing execution wire formats.

## Gate

1. ADR-036 is PROPOSED only.
2. Founder reviews/accepts or requests changes.
3. No schema, runtime API, storage, UI, HTTP, MCP, Orbis or Limen implementation before acceptance.
4. After acceptance, split implementation into contract-first micro-steps:
   - registry schema + semantic invariants;
   - ControlApplication schema + semantic invariants;
   - canonical derived fingerprints;
   - executable validator/CLI;
   - public runtime verification/query surface;
   - focused mutation-proof tests;
   - adversarial review;
   - Jenkins green;
   - merge.
5. No edits to `tests/contract/` or `tests/compat/`.
6. No new runtime dependencies.
