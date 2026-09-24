# ADR-040 — Retention and legal hold are declarations with separate application evidence

- **Status:** PROPOSED
- **Date:** 2026-09-24
- **Authority:** CTO proposes; the founder accepts.
- **Depends on:** ADR-001 (contract authority), ADR-020 (trust and assurance levels), ADR-021 and ADR-031 (anchor evidence and trust), ADR-027 (execution identity), ADR-034 (Motus owns evidence; bridges only expose it), ADR-035 (system declaration != execution proof), ADR-036 (control declaration != ControlApplication evidence), ADR-037 (claimed human act != authority or compliance), ADR-038 (profiles map evidence; they do not decide compliance), ADR-039 (append-only claims, exact revision identity and visible conflict).
- **Amends on acceptance:** permits canonical, jurisdiction-neutral RetentionPolicyDeclaration, LegalHoldDeclaration, RetentionApplication and custody-observation contract surfaces plus deterministic structural, lineage, scope-resolution and binding verification. It does **not** amend the meaning of ADR-020 `RETENTION`, trace, GraphSpec, commitment, checkpoint, receipt, evidence-package, System Manifest, Risk & Control, ControlApplication, HumanOversightReceipt, Incident / CAPA, or Regulatory Evidence Profile wire formats unless a later ADR explicitly does so.

## Context

Motus can prove that an execution produced particular evidence and, when an
independently checked external anchor exists, can make later deletion or
reordering of a commitment chain detectable. It cannot currently record how
long a producer says evidence should be kept, that a hold was declared over a
scope, which exact artifacts a storage operator treated as covered, or what
observable custody or disposal operation followed.

The word `RETENTION` already has a narrower meaning in ADR-020: assurance level
3 says that a published commitment chain makes later deletion or reordering
detectable. It does not prove that bytes remain retrievable, that a storage
duration was applied, that a legal hold is valid, or that a custodian prevented
deletion. Reusing that assurance level as a policy or custody verdict would
silently strengthen an existing contract.

A records-management product commonly combines policy calculation, mutable
hold state, legal advice, custodian workflow, storage administration, access,
disposal and jurisdiction-specific schedules. Importing that system into Motus
would turn the evidence kernel into a storage or legal-compliance engine. At
the other extreme, one mutable `retained=true` field would erase policy and
hold history while proving neither continued custody nor the operation that
was claimed.

The missing surface is narrower: immutable declarations, exact scope
snapshots, and separately verifiable evidence about application and custody.
Storage, scheduling, legal interpretation and enforcement remain outside the
kernel.

The required invariant is:

**Motus records what policy, hold, scope and storage actions a producer
declared, and verifies their identities and bindings; it does not decide legal
applicability, authorization, continued custody, lawful disposal, or
compliance from the presence of those records.**

## Decision

1. **Policy, hold, resolved scope and application evidence remain separate facts.**
   - RetentionPolicyDeclaration records one producer's versioned retention and disposal rule.
   - LegalHoldDeclaration records one producer's claim that a hold was placed, amended, released or cancelled.
   - A scope snapshot records which exact artifact identities a producer resolved from one exact policy or hold selector revision at a stated observation time.
   - RetentionApplication records a claimed storage or disposal action against exact artifacts, one exact policy revision, and any supplied hold or scope revisions relevant to that action.
   - A custody observation records a bounded observation about retrievability or absence; it is not silently manufactured from policy or application records.

2. **Declarations never prove enforcement.**
   - A valid policy does not prove that any custodian received, understood or applied it.
   - A valid hold does not prove that it was legally effective or technically enforced.
   - A valid RetentionApplication proves only that the application record satisfies the contract and its supplied bindings verify.
   - Policy, hold and application evidence may disagree; Motus preserves and reports that disagreement.

3. **Exact-document identity and stable references remain distinct.**
   - policy_id, hold_id and application_id are bounded producer-assigned references within a declared producer namespace.
   - Every exact declaration, scope snapshot, application and custody observation has a derived `sha256` fingerprint over canonical JSON; the fingerprint is not embedded in the document.
   - References that affect a verification result name exact fingerprints. Stable identifiers locate a subject but never select a winning revision.

4. **History is append-only and conflicts remain visible.**
   - Published canonical records are not updated in place or deleted by the contract.
   - Corrections name one immediate predecessor fingerprint through `supersedes`, preserve the appropriate stable identifier and receive a new fingerprint.
   - Self-reference, cycles, skipped predecessors and cross-namespace or cross-identifier supersession are invalid.
   - Forks are reported as conflicts. Timestamp, ingestion order, actor role and last-write-wins never choose a winner.

5. **Policy duration and trigger are declared inputs, not legal conclusions.**
   - Version 1 may express a bounded duration and a closed neutral trigger vocabulary such as creation, execution completion, incident closure or an externally supplied event.
   - Any trigger occurrence is a separately identified producer claim or existing Motus artifact; absence is `missing` or `not_verified`, never guessed.
   - A calculated target instant is reproducible arithmetic over declared inputs. It is not a verdict that a statutory period applies or that disposal is lawful after that instant.
   - A timestamp alone proves neither contemporaneity nor ADR-020 `LEGAL_TIME`.

6. **Policy and hold scope are declared first and resolved separately.**
   - A policy or hold may declare exact artifact fingerprints, stable Motus references, or a bounded neutral selector over identifiers Motus owns.
   - A selector may be prospective. Its declaration does not prove which present or future artifacts were captured.
   - Each scope snapshot names the exact policy or hold revision and enumerates the exact artifact identities resolved by the producer. Later artifacts require a later snapshot; an earlier snapshot is never silently expanded.
   - Direct membership may be checked without a snapshot only when the declaration itself enumerates exact artifact fingerprints. Every other per-artifact scope result requires an exact supplied scope snapshot; without one it is `missing_binding` or `not_verified`.
   - Motus can verify deterministic selector/snapshot consistency only for supplied material. It does not search an undeclared database or infer organizational possession.

7. **A claimed deadline never deletes evidence.**
   - The core has no scheduler, background deletion, storage mutation or automatic expiry transition.
   - Passing a declared target instant may make a policy record arithmetically due; it does not authorize deletion.
   - Deletion, preservation, migration, restoration, quarantine and deletion-blocked are explicit claimed operations with exact inputs and outcomes.
   - Actual mutation belongs to an external custodian. Motus records and verifies the evidence that custodian returns.

8. **The supplied evidence can show a blocker, never disposal clearance.**
   - For one supplied artifact and supplied records, deterministic evaluation reports `blocked_by_supplied_hold`, `conflicting_supplied_hold`, `missing_binding`, `not_verified`, or `no_blocker_in_supplied_evidence`.
   - If a supplied hold matches, conflicts, lacks material needed for evaluation, or is not independently verified, Motus reports that condition and never an eligibility or clearance result.
   - `no_blocker_in_supplied_evidence` means only that the caller-provided subset contains no hold blocker the verifier could establish. Motus cannot distinguish that state from omission of an otherwise relevant policy, hold, scope snapshot or artifact.
   - No finding produced by this surface authorizes or recommends deletion. A consumer must establish its complete input universe and external authority separately, and may not relabel Motus uncertainty as clearance.

9. **Release is a new declaration, never an erasure.**
   - Releasing or cancelling a hold is an immutable new LegalHoldDeclaration linked to the exact prior revision.
   - A release does not delete the placement, prior scope snapshots or application history.
   - Release time, rationale, actor_ref and authority_ref are producer claims. Authentication or possession of a credential does not establish authority.
   - Conflicting release branches remain conflicts until the consumer supplies an explicit external resolution; Motus does not choose one.

10. **Human authorization and application evidence bind without collapsing.**
    - A declaration or application may reference a HumanOversightReceipt or other accepted Motus evidence by exact fingerprint.
    - Structural validity of the retention record does not validate the referenced human act, and a valid HumanOversightReceipt does not prove legal authority, competence, independence or approval sufficiency.
    - Binding verification distinguishes matched, mismatched, missing, not_verified and conflict by composing existing validators rather than reimplementing them.

11. **Custody is observed, not inferred from integrity or anchoring.**
    - A valid receipt, package, checkpoint or external anchor proves only its existing contract semantics.
    - ADR-020 `RETENTION` continues to mean detectability of chain deletion or reordering after independently checked publication. This ADR does not make it proof that content remains stored or retrievable.
    - A custody observation identifies the observer, artifact fingerprint, method reference and bounded result. Motus verifies the record and supplied bindings, not the physical truth of remote storage.
    - Retrieval through an API, proxy or UI is one observation and never a perpetual-custody guarantee.

12. **Execution and artifact bindings use identities Motus already owns.**
    - Records may reference ADR-027 `execution_ref`, receipt/package identities, commitment positions and exact fingerprints for accepted regulatory artifacts.
    - Truncation or disposal must never renumber `(tenant, writer, sequence)` or recycle an execution identity.
    - A retention surface does not invent a second execution identity, weaken a verifier, or treat a mutable URI as an artifact identity.

13. **The core remains jurisdiction-, product- and storage-provider-agnostic.**
    - No statute, regulator, litigation status, discovery conclusion, records category, country-specific schedule, cloud-provider tier or customer workflow state belongs in the v1 canonical vocabulary.
    - External Regulatory Evidence Profiles may map the neutral records to framework requirements without changing their identity or producing a Motus compliance verdict.
    - Orbis, Limen and third-party adapters may transport records and invoke verification; they do not redefine scope, clearance or assurance semantics.

14. **Stop conditions are explicit.**
    Implementation stops and requires a later founder-accepted ADR if it would need to:
    - decide which law, jurisdiction, retention period, litigation duty or disposal authorization applies;
    - treat an authenticated actor as legally authorized or a timestamp as trusted legal time;
    - infer continued custody from an anchor, receipt, policy, hold or provider response;
    - choose a winning amendment fork, silently expand a prospective scope, or discard history;
    - delete, migrate or mutate stored evidence from inside the Motus core;
    - require hidden server state, a network call, database, scheduler, LLM or policy engine for deterministic verification;
    - change ADR-020 assurance meanings or an existing wire format without a dedicated ADR;
    - add jurisdiction- or provider-specific fields to the neutral core.

15. **The first implementation is additive, local and dependency-free.**
    - It may add schemas, executable validation, canonical fingerprint derivation, lineage and scope verification, deterministic subset-scoped blocker findings, public read-only helpers, neutral fixtures and focused tests.
    - It adds no runtime dependency, database, storage backend, deletion executor, HTTP endpoint, MCP tool, UI, scheduler, notification service, legal rules, automatic policy selection or evidence-package change.
    - Frozen `tests/contract/` and `tests/compat/` remain untouched.
    - Mutation proof, independent verification, adversarial closure, relative characterization, real-workload evidence where required, and green Jenkins remain release gates for v0.19.0.

## Initial conceptual shape

The exact field-by-field schemas are implementation work after acceptance. The
decision constrains a RetentionPolicyDeclaration toward a document containing
schema_version, producer namespace, policy_id, neutral trigger, duration or
explicit no-automatic-disposal stance, declared scope selector, producer_ref,
declared_at and supersedes.

A LegalHoldDeclaration is constrained toward a document containing
schema_version, producer namespace, hold_id, action, bounded scope selector,
reason reference, producer_ref, relevant claimed timestamps, optional exact
HumanOversightReceipt binding and supersedes.

A scope snapshot enumerates exact artifact identities resolved for one exact
policy or hold revision. A RetentionApplication names one exact policy
fingerprint; optional, explicitly supplied hold and scope fingerprints; exact
artifact identities; one closed neutral operation; the custodian and method
references; claimed outcome; observed_at; and any supplied operation evidence.
Absence of a hold binding never means that no hold exists. A custody observation
remains a separate exact record.

Deterministic evaluation returns findings and exact identities, never a mutable
current row. Ordering follows explicit predecessor relationships with canonical
fingerprint tie-breaking; ingestion order is never evidence.

## Consequences

**A later reader can distinguish intent from action.** Policy and hold records
remain inspectable even when application or custody evidence is absent.

**Prospective holds remain honest.** A selector can describe future scope, but
each claim about what was captured is an immutable snapshot rather than an
invisible query result.

**Motus never emits disposal clearance.** Missing or conflicting hold evidence
is visible, while even `no_blocker_in_supplied_evidence` remains explicitly
subset-scoped and cannot establish that no other hold exists.

**The cost is more records and explicit joins.** A complete answer may require
the policy chain, hold chain, scope snapshots, application evidence, custody
observations, human receipts and the original artifacts. This is accepted so a
single mutable status cannot rewrite history.

**Motus still does not retain anything by itself.** Deployment owners must
choose storage, replication, access, backup, deletion and recovery mechanisms
and produce evidence that can be checked independently.

## Alternatives rejected

### Reuse ADR-020 `RETENTION` as the retention-policy verdict

Rejected. That assurance level means published-chain tamper detectability. It
does not prove custody, duration, hold enforcement or lawful disposal.

### Store one mutable `retained` or `on_hold` flag

Rejected. It erases prior claims, hides conflicting instructions and provides
no exact evidence of what scope or operation the flag represents.

### Let a declared expiry automatically delete artifacts

Rejected. A declared period and trigger are producer inputs, not legal
authority or a storage command. Automatic deletion would also make a transient
or unresolved hold failure destructive.

### Treat a hold selector as proof of the artifacts actually preserved

Rejected. A prospective or database-backed selector can change membership over
time. Exact immutable scope snapshots keep declaration and application apart.

### Treat authentication as authority to place or release a hold

Rejected. Identity, credential control, organizational standing and legal
authority are different facts. Existing HumanOversightReceipt semantics
already preserve that distinction.

### Embed jurisdiction schedules and legal conclusions in the core

Rejected. Applicability and disposal law depend on facts Motus does not own.
Framework profiles and consumers may map neutral evidence without changing it.

### Make Motus the storage and deletion engine

Rejected. It would couple the evidence contract to infrastructure, require
hidden mutable state and network effects, and make deterministic offline
verification impossible.
