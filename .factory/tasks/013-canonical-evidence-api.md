# TASK 013 — Canonical Evidence API, first regulatory-roadmap slice

Branch: `feat/regulatory-evidence-profile-v1`.

Status: implementation complete; CI green; adversarial review requested.

Read first: `AGENTS.md`, ADR-020, ADR-021, ADR-027, accepted ADR-034, `src/vitruvyan_motus/evidence.py`, `src/vitruvyan_motus/commitlog.py`, `src/vitruvyan_motus/replay.py`, and the public-surface section of `README.md`.

## Goal

Create the first Motus-only integration boundary for evidence consumers. Orbis, Limen and third-party stacks must be able to depend on Motus evidence without taking ownership of receipt construction or verifier semantics.

## Scope

1. Implement a small, transport-neutral public Evidence API keyed by `execution_ref`.
2. Reuse `CommitmentLog.receipt_for`, `pack`, and `verify_package`; do not duplicate receipt/package/verifier logic.
3. Keep the API read-only.
4. Keep Orbis/Limen/customer vocabulary out of the package.
5. Introduce the minimum source protocol required so a consumer is not coupled to one filesystem or one live writer object.
6. Provide one stdlib-only local reference source only if it can be done without weakening commitment-log locking or evidence integrity; otherwise stop and report the blocker.
7. Add public API exports and README documentation only after ADR-034 is accepted.
8. Add tests that fail when `run_id` is used as primary key, retrieval mutates/seals evidence, verification is inferred rather than executed, or product-specific fields enter the canonical model.

## Explicitly out of scope

- System Manifest
- Risk/Control Registry
- Human Oversight Receipt
- Incident/CAPA
- Retention/Legal Hold
- AI System Registry
- Regulatory mapping
- Orbis or Limen code
- UI
- HTTP server

## Acceptance

A stable Motus public boundary a bridge can consume, with no new runtime dependency and no change to trace/commitment/checkpoint/receipt/package wire formats. Full suite, frozen-path guard, packaging/public-API checks, and an adversarial round are required before merge.
