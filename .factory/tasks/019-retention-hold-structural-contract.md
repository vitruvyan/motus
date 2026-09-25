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

Adversarial closure for this step: a supplied snapshot on direct exact scope is
validated and explicitly reported as unused, including malformed input;
competing unsuperseded roots for one producer-scoped stable ID are conflicts;
and cycle findings identify only the strongly connected revisions, with their
downstream descendants ordered after the cycle. The graph walk is iterative.

## Micro-step 3 — supplied bindings and hold findings

The local retention module checks exact policy, hold and scope-snapshot
fingerprints cited by a RetentionApplication. Invalid supplied documents and
ambiguous duplicate fingerprints remain visible; a cited snapshot must name an
exact supplied source in the same producer namespace. The application and its
operation outcome remain producer claims.

For one typed artifact, the supplied-hold evaluator returns one of the five
ADR-040 subset verdicts. A matching placed/amended declaration is a blocker
only in that supplied producer-claim sense. Release/cancellation cannot remove
that history or establish authority; its presence yields `not_verified` unless
the supplied lineage itself conflicts. Selector scope requires one exact
snapshot, and missing or conflicting material remains visible. Neither this
step nor a CustodyObservation establishes continued custody, global hold
absence, legal authority, or disposal permission. Target-time arithmetic and
top-level package exports remain outside this step.

Focused tests are in `tests/test_retention_bindings_blockers.py`; mutation
targets are in `.factory/probes/retention-bindings-blockers.json`.

Adversarial outcome-precedence closure: blocker evaluation now groups supplied
hold revisions by `(producer_namespace, hold_id)`. A complete matching group
establishes the subset blocker even when an independent group has a missing
snapshot, release claim, conflict or invalid record; all such defects remain
visible as findings or violations. A conflict, release or missing binding in
the matching group still prevents that group from establishing the blocker.
For direct exact scope, a redundant supplied snapshot is validated and reported
as unused without replacing the declaration's exact membership. Input order
does not decide between independent groups.

PR #192 adversarial closure: structurally invalid scope snapshots remain
supplied violations. When a readable raw `source` names the exact selector
hold revision, that lineage is `not_verified` even if another valid snapshot
names it; a same-lineage conflict still outranks this uncertainty. A malformed
or unrelated source cannot associate the defect with that hold, and an
independent complete blocker is not erased by unrelated invalid input.

Further PR #192 closure: application references first compare against
canonicalizable schema-invalid policy, hold and snapshot candidates by their
exact derived fingerprint, yielding `not_verified` even with unrelated valid
documents. Non-JSON invalid material has no derived identity; duplicate or
colliding valid/invalid fingerprints remain conflicts. A globally supplied
same-kind hold predecessor with the wrong namespace or hold ID is propagated
to its child lineage as a mismatch, not downgraded to a missing binding.
An ambiguous exact supplied predecessor similarly remains a conflict.

Final PR #192 predecessor closure: a canonicalizable schema-invalid hold is
indexed by its exact fingerprint even if its raw namespace or hold ID differs
from a valid child citing it. That child's supplied predecessor is
`not_verified`, not absent; duplicate invalid or valid/invalid identities are
`conflict`. Unfingerprintable invalid inputs remain violations only. A truly
absent predecessor remains a missing binding, and a separate complete hold
still establishes only its supplied-subset blocker.

## Micro-step 4 — public read-only package surface

The six immutable finding, identity and verdict dataclasses plus four
retention helpers are exported through `vitruvyan_motus.__init__` and its
explicit `__all__`. README lists the API and shows supplied-document use,
including the subset-only meaning of hold findings and the limits of policy,
release, application and custody claims. Public imports and wheel membership
receive focused tests. This is an additive API checkpoint, not a v0.19.0
release or a change to the roadmap delivery state.
