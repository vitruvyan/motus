# TASK 019 — Retention and hold structural contract

Parent: ADR-040, accepted by the founder on 2026-09-24.

Implement six additive v1 documents: RetentionPolicyDeclaration,
LegalHoldDeclaration, RetentionScopeSnapshot, RetentionTriggerOccurrence,
RetentionApplication and CustodyObservation. Ship Draft 2020-12 closed schemas,
strict structural validators, canonical fingerprint helpers, neutral fixtures,
CLI dispatch and focused tests. Preserve existing wire formats and frozen tests.

This first step checks each document alone: shape, bounded values, real UTC
instants, uniqueness of typed artifact identities and canonical Motus execution
coordinates. It does not select a policy revision, prove a trigger, walk
supersession chains, resolve scope, verify external bindings, calculate target
instants, evaluate hold blockers or mutate stored evidence. A valid declaration
or observation remains a producer claim.

Verification gate: focused tests, full suite, packaging, frozen-path guard and
mutation proof after the local commit. No push, merge, tag, release or PR in
this task.
