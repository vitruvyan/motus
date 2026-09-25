# ADR-041 — An AI System Registry records immutable registration and lifecycle claims, not legal status

- **Status:** ACCEPTED
- **Date:** 2026-09-25
- **Accepted:** 2026-09-25, by the founder.
- **Authority:** CTO proposes; the founder accepts.
- **Depends on:** ADR-001 (contract authority), ADR-020 (trust and assurance levels), ADR-021 and ADR-031 (anchor evidence and trust), ADR-027 (execution identity), ADR-034 (Motus owns evidence; bridges only expose it), ADR-035 (a System Manifest is one immutable system declaration, not a registry), ADR-036 (control declaration != ControlApplication evidence), ADR-037 (claimed human act != authority or compliance), ADR-038 (profiles map evidence; they do not decide compliance), ADR-039 (append-only claims, exact revision identity and visible conflict), ADR-040 (declaration != application or custody evidence).
- **Amends on acceptance:** permits canonical, jurisdiction-neutral AI System Registration, AI System Registry Event and AI System Registry Snapshot contract surfaces plus deterministic structural, lineage and binding verification. It does **not** amend the meaning or wire format of trace, GraphSpec, commitment, checkpoint, receipt, evidence package, System Manifest, Risk & Control Registry, ControlApplication, HumanOversightReceipt, Incident / CAPA, Retention / Legal Hold or Regulatory Evidence Profile unless a later ADR explicitly does so.

## Context

Motus can describe one exact system configuration through a System Manifest and can bind independently verifiable execution and regulatory evidence to exact artifacts. It does not yet have a neutral way to record that a producer claims a system was entered into a registry, how that registration changed over time, or which exact registrations and lifecycle claims a producer included in one bounded view.

ADR-035 deliberately rejected making the System Manifest itself the registry. A manifest declares one immutable configuration revision. A registry is a collection and lifecycle surface over many stable system subjects and exact manifest revisions. Collapsing them would either make the manifest mutable or make every administrative event look like a new technical configuration.

A product registry commonly adds mutable database rows, permissions, workflow, discovery, regulator filings, deployment inventory and jurisdiction-specific classifications. Importing that product into Motus would make an offline evidence kernel depend on hidden state and legal conclusions. At the other extreme, deriving registration from the mere presence of a manifest or execution would invent a governance claim that no producer made.

The missing surface is narrower: immutable registration declarations, separate append-only lifecycle events, bounded snapshots, and verification over the exact documents a caller supplies.

The required invariant is:

**Motus records what a producer claims was registered and which lifecycle events it claims occurred, and verifies exact identities and supplied bindings; it does not decide whether the subject is legally an AI system, in scope, deployed, current, approved, registered with an authority, safe, compliant or completely inventoried.**

## Decision

1. **The System Manifest and the registry remain separate facts.**
   - An AI System Registration identifies one stable registry subject and binds it to one exact System Manifest revision.
   - The registration does not duplicate the manifest's components, execution graph, runtime configuration or declarations.
   - A valid manifest does not imply registration. A valid registration does not prove deployment, execution or current use.
   - A later manifest revision requires an explicit new registration revision or registry event; it is never adopted implicitly.

2. **Registration, lifecycle events and bounded inventory views are separate documents.**
   - AI System Registration records one producer's immutable registration claim.
   - AI System Registry Event records one claimed lifecycle action against one exact registration revision.
   - AI System Registry Snapshot records the exact registration and event fingerprints that one producer included in one bounded view at a stated observation time.
   - No document is overwritten to manufacture a mutable current row.

3. **Exact-document identity and stable references remain distinct.**
   - `registry_id` and `registration_id` are bounded producer-assigned references within a declared producer namespace.
   - Every exact registration, event and snapshot has a derived `sha256` fingerprint over canonical JSON; the fingerprint is not embedded in the document.
   - References that affect verification name exact fingerprints. Stable identifiers locate a subject but never select a winning revision.
   - A registration also carries the stable `system_id` declared by its bound System Manifest, but `system_id` alone is not an exact configuration identity.

4. **History is append-only and conflicts remain visible.**
   - Published canonical records are not updated in place or deleted by the contract.
   - A corrected registration names one immediate predecessor fingerprint through `supersedes`, preserves producer namespace, `registry_id` and `registration_id`, and receives a new fingerprint.
   - Self-reference, cycles, skipped predecessors and cross-namespace, cross-registry or cross-registration supersession are invalid.
   - Forks are reported as conflicts. Timestamp, ingestion order, actor role and last-write-wins never choose a winner.

5. **Lifecycle events report claims; they do not rewrite registration history.**
   - Version 1 uses a closed neutral action vocabulary: `registered`, `updated`, `activated`, `suspended`, `resumed`, `retired` and `decommissioned`.
   - Each event names one exact registration fingerprint and has its own stable event identity and derived fingerprint.
   - An event may supersede one prior event only where the contract permits correction of that event claim; it never silently replaces the registration or another lifecycle branch.
   - Event time, effective time, actor reference, reason and outcome are producer claims. A timestamp alone proves neither contemporaneity nor ADR-020 `LEGAL_TIME`.

6. **A supplied chain can be projected, but Motus does not assert global current state.**
   - Deterministic projection may report chain heads, lifecycle actions, missing predecessors, forks, cycles and binding findings for the exact supplied subset.
   - Where the supplied material forms one complete, conflict-free chain, a verifier may report the terminal action **derived from that supplied chain**.
   - The result is never named or documented as global truth, deployment truth, official registry status or proof that later records do not exist.
   - A missing or conflicting record produces an explicit finding; Motus does not guess a state.

7. **Registry snapshots are bounded declarations, not completeness proofs.**
   - A snapshot names its producer namespace and `registry_id`, the exact registration and event fingerprints it includes, and `observed_at`.
   - A later snapshot does not mutate an earlier one and may differ without making either document structurally invalid.
   - Snapshot validity proves neither that all relevant systems were enumerated nor that referenced records were reachable, verified, deployed or current.
   - Absence from a supplied snapshot never proves that a system does not exist or was not in use.

8. **Bindings compose existing Motus validators without inheriting stronger meaning.**
   - Every registration binds `system_id` and the exact fingerprint of one supplied System Manifest.
   - Verification distinguishes `matched`, `mismatched`, `missing`, `not_verified` and `conflict`; it does not treat schema validity as binding verification.
   - Optional exact references may point to accepted Motus Risk & Control, Human Oversight, Incident / CAPA, Retention / Legal Hold, Regulatory Profile or execution evidence.
   - Each referenced artifact retains its own semantics and validator. A valid reference does not turn its contents into proof of registration, authority, deployment or compliance.

9. **Declared parties and external references remain claims.**
   - A registration may identify producer, operator, owner, accountable-party and technical-contact references without assigning legal meaning to those labels.
   - An event may reference a HumanOversightReceipt or another authorization artifact by exact fingerprint.
   - Authentication, credential possession, a role label or a valid HumanOversightReceipt does not establish legal authority, organizational standing, competence, independence or approval sufficiency.
   - External registry, filing or certificate references are opaque values. Motus does not contact the issuer or infer their validity.

10. **The core remains jurisdiction-, product- and transport-agnostic.**
    - No statute, regulator, risk class, conformity status, prohibited-use verdict, market-access decision, certification status, national identifier semantics or country-specific role belongs in the v1 canonical vocabulary.
    - A registration may carry bounded producer-declared purposes, contexts and neutral external references, but Motus does not classify them.
    - Regulatory Evidence Profiles may map neutral registry evidence to framework requirements without changing its identity or producing a Motus compliance verdict.
    - Orbis, Limen and third-party adapters may transport and display registry records; they do not redefine registry, lifecycle or assurance semantics.

11. **The registry is evidence data, not a mutable registry service.**
    - Version 1 has no global database, network discovery, uniqueness claim across hidden state, CRUD service, access-control system, notification workflow, regulator submission, deployment scanner or automatic inventory reconciliation.
    - Deterministic verification operates on explicit caller-supplied documents and produces findings tied to exact fingerprints.
    - A deployment may index these documents for retrieval, but the index is not part of the canonical truth and cannot choose or rewrite revisions.

12. **Stop conditions are explicit.**
    Implementation stops and requires a later founder-accepted ADR if it would need to:
    - decide whether a subject is legally an AI system, in scope, high risk, prohibited, approved, certified, compliant or reportable;
    - treat authentication, a role label, a lifecycle action or a referenced receipt as sufficient authority;
    - infer registration, deployment or active use from a manifest, execution, API response, database row or UI;
    - choose a winning amendment fork, use time or ingestion order as authority, or hide missing predecessors;
    - claim a snapshot or supplied subset is globally complete;
    - require hidden server state, a network call, database, scheduler, LLM or policy engine for deterministic verification;
    - change ADR-020 assurance meanings or an existing wire format without a dedicated ADR;
    - add jurisdiction-, regulator- or product-specific legal conclusions to the neutral core.

13. **The first implementation is additive, local and dependency-free.**
    - It may add schemas, executable validation, canonical fingerprint derivation, lineage and exact binding verification, subset-scoped projection, public read-only helpers, neutral fixtures and focused tests.
    - It adds no runtime dependency, database, HTTP endpoint, MCP tool, UI, workflow engine, regulator integration, network lookup, mutable storage or evidence-package change.
    - Frozen `tests/contract/` and `tests/compat/` remain untouched.
    - Mutation proof, independent verification, adversarial closure, relative characterization, real-workload evidence where required, and green Jenkins remain release gates for v0.20.0.

## Initial conceptual shape

The exact field-by-field schemas are implementation work after acceptance. The decision constrains an AI System Registration toward a document containing `schema_version`, producer namespace, `registry_id`, `registration_id`, `system_id`, exact `system_manifest_fingerprint`, bounded producer-declared purposes and contexts, party references, optional opaque external references, `declared_at` and `supersedes`.

An AI System Registry Event is constrained toward a document containing `schema_version`, producer namespace, `registry_id`, `event_id`, exact `registration_fingerprint`, one closed neutral action, actor and reason references, claimed occurrence and effective times, optional exact evidence references, and an optional permitted predecessor for correction.

An AI System Registry Snapshot names one producer namespace and `registry_id`, its `snapshot_id`, `observed_at`, and exact registration and event fingerprints. It is an immutable inventory claim over a supplied subset, not a current-state certificate.

Deterministic verification returns findings and exact identities. Ordering follows explicit predecessor relationships with canonical fingerprint tie-breaking for reporting only; ingestion order is never evidence. A projection may report what follows from a complete conflict-free supplied chain but must carry the supplied-scope limitation in its result.

## Consequences

**A later reader can distinguish system configuration from registry governance.** A System Manifest can remain unchanged while registration or lifecycle claims accumulate, and a registration cannot silently substitute for execution evidence.

**Conflicting histories remain inspectable.** Corrections and competing branches preserve the exact documents that were supplied instead of overwriting a status field.

**The core can answer bounded questions only.** It can verify a supplied registration, enumerate supplied chain heads and derive a terminal action from a complete conflict-free supplied chain. It cannot prove that the caller supplied the whole registry or that no later event exists.

**The cost is more documents and explicit joins.** A useful view may require registrations, events, snapshots, manifests and independently validated evidence. This is accepted so one mutable row cannot erase provenance or acquire unstated legal meaning.

**Deployments still need a product layer.** Search, permissions, workflow, storage, notifications, regulator submission and UI belong outside the canonical kernel and must preserve Motus findings rather than replacing them.

## Alternatives rejected

### Make the System Manifest the AI System Registry

Rejected. A manifest is one immutable technical declaration. Registry collection and lifecycle are different facts, as ADR-035 already records.

### Use one mutable database row with a current status

Rejected. It makes verification depend on hidden state, erases prior claims and lets update order choose truth.

### Infer registration from execution or the presence of a manifest

Rejected. Technical evidence does not establish that a producer made a registration or governance claim.

### Treat the newest timestamp as current

Rejected. Producer timestamps are claims and may conflict, be untrusted or arrive out of order. Explicit predecessor relationships preserve uncertainty.

### Treat an authenticated actor or valid oversight receipt as sufficient authority

Rejected. Identity and evidence of an act do not establish legal or organizational authority.

### Make a snapshot proof of completeness

Rejected. Offline verification cannot establish what the caller omitted or what exists in an external registry.

### Embed jurisdiction-specific classifications and filing decisions

Rejected. Applicability and legal status depend on facts and authorities Motus does not own. Profiles and consumers may map neutral evidence without changing it.

### Combine registration, lifecycle, manifest and referenced evidence into one document

Rejected. The combined document would duplicate immutable artifacts, blur independent validation results and require unrelated facts to be revised together.
