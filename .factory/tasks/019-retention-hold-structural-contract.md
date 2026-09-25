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

Adversarial closure: `RetentionScopeSnapshot.snapshot_id` is a required,
bounded producer-scoped stable reference so future lineage can preserve
`(producer_namespace, snapshot_id)` across revisions. Duration seconds must
be an actual JSON integer; an integral float is refused by RET4 in the API and
CLI. The inherited strict parser can raise `RecursionError` at roughly 20k
nesting levels. That parser-robustness finding predates this surface and is
recorded as out of scope for Task 019; no parser change is made here.

Verification gate: focused tests, full suite, packaging, frozen-path guard and
mutation proof after the local commit. No push, merge, tag, release or PR in
this task.

## Micro-step 2 — supplied lineage and scope resolution

The additive `vitruvyan_motus.retention` module verifies same-kind,
same-namespace, same-stable-ID immediate predecessors for all six accepted
documents. It preserves missing links, amendment forks, cycles, and digest
collisions as findings. Deterministic ordering uses predecessor edges and exact
fingerprints; timestamps and input order do not select a revision.

For policy and hold selectors, exact artifact lists resolve directly. An
execution-reference or tenant/writer selector requires one supplied, valid scope
snapshot bound to the exact declaration fingerprint, source kind and namespace.
The returned typed identities describe that supplied snapshot only; the helper
does not infer completeness or custody. Binding evaluation and public package
exports are subsequent steps.

Focused tests and mutation probes live in `tests/test_retention_lineage_scope.py`
and `.factory/probes/retention-lineage-scope.json` respectively.
