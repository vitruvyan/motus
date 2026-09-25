# TASK 020 — AI System Registry v1

Parent: ADR-041, accepted by the founder on 2026-09-25.

Implement three additive jurisdiction-neutral v1 documents: AI System
Registration, AI System Registry Event and AI System Registry Snapshot. Ship
Draft 2020-12 closed schemas, strict structural validators, canonical
fingerprint helpers, neutral fixtures, CLI dispatch and focused tests before
adding supplied-document lineage, binding and lifecycle projection helpers.

The implementation records producer claims. It does not decide whether a
subject is legally an AI system, deployed, current, approved, registered with
an authority, safe, compliant or completely inventoried. Exact System Manifest
bindings remain separate from structural validity. Event correction lineage
(`supersedes`) remains separate from lifecycle order
(`predecessor_event_fingerprint`). A snapshot is a bounded supplied view and
never proof of global completeness.

The first release surface is additive, local and dependency-free. It does not
add a database, service, network lookup, regulator integration, UI, mutable
state or evidence-package change. Frozen `tests/contract/` and `tests/compat/`
remain untouched.

Verification gate: focused tests, full suite, packaging, frozen-path guard,
mutation proof, independent review, adversarial closure, relative
characterization and green Jenkins evidence before merge or release.

Implementation checkpoint: the three schemas, CLI validators, exact fingerprint
helpers, supplied correction lineage, exact System Manifest binding, bounded
lifecycle projection, snapshot membership verification and public read-only API
are covered by `tests/test_ai_system_registry.py`. The initial full-suite gate
passed with 1765 tests, 4 skipped and no failures after adversarial invalid-input
closure. Executed mutation checks killed changes to UTC validation, duplicate
semantic identity, manifest binding, lifecycle forks, cross-registry isolation
and snapshot scope. Targets are recorded in
`.factory/probes/ai-system-registry-v1.json`; independent review,
characterization and Jenkins evidence remain release gates.
