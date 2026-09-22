# TASK 017A — Regulatory Evidence Profile v1 core

Parent: TASK 017 / ADR-038.

Goal: implement only the product surface of Regulatory Evidence Profile v1.

In scope:
- profile schema;
- REP semantic validation;
- motus-validate CLI support;
- canonical profile fingerprint;
- assess_evidence_profile public API;
- neutral fixtures;
- packaging;
- README/contract docs.

Out of scope:
- mutation proof orchestration;
- adversarial review;
- release preparation;
- framework-specific profiles;
- any rule DSL, selector language, storage, UI, MCP, DB, LLM or Orbis code.

Gate:
1. existing Point 1–4 wire formats unchanged;
2. focused tests green;
3. full Jenkins PR build green;
4. stop product changes once green.

Current implementation lives in PR #183.
