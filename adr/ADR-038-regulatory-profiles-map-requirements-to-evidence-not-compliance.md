# ADR-038 — Regulatory profiles map requirements to evidence; they do not decide compliance

- **Status:** ACCEPTED
- **Date:** 2026-09-22
- **Accepted:** 2026-09-22, by the founder.
- **Authority:** CTO proposes; the founder accepts.
- **Depends on:** ADR-001, ADR-020, ADR-027, ADR-034, ADR-035, ADR-036, ADR-037.
- **Amends on acceptance:** permits one canonical, jurisdiction-neutral Regulatory Evidence Profile contract and a deterministic read-only assessment surface that maps external requirement references to existing Motus evidence. It does not amend trace, GraphSpec, commitment, checkpoint, receipt, System Manifest, Risk & Control Registry, ControlApplication, HumanOversightReceipt, or evidence-package wire formats.

## Context

Motus can now keep five questions separate:

1. what execution evidence exists;
2. what system configuration an operator declared;
3. what risks and controls an operator declared;
4. whether one declared control was evaluated or applied at one execution boundary;
5. whether one human oversight event was recorded against an execution-scoped subject.

None of those artifacts says whether an external legal, regulatory, contractual, certification, or management-system requirement is satisfied.

The next integration problem is narrower than a compliance engine. A consumer may know that an external requirement — an article, clause, policy statement, procurement control, or internal governance rule — expects certain classes of evidence. Today that mapping must be written ad hoc in each consumer.

Putting regulatory references into existing evidence would couple neutral evidence identity to one framework. Making Motus interpret legal text would create a second authority whose conclusions cannot be derived from the execution contract. Building a general rules engine would solve a much larger problem than the one we have.

The missing object is a profile: a versioned declaration that one external requirement reference expects named Motus evidence kinds. The missing operation is a deterministic assessment of whether the supplied Motus evidence is present and verifiable under existing Motus contracts.

The required invariant is:

**A Regulatory Evidence Profile can say which Motus evidence a requirement expects and whether that evidence is available and verifiable; it cannot say that the requirement is legally satisfied.**

## Decision

1. **A Regulatory Evidence Profile is mapping data, not evidence and not law.**
   - It maps opaque external requirement references to expectations over evidence types Motus already owns.
   - It does not duplicate, embed, or replace the evidence it references.
   - It is not an execution artifact.

2. **External requirement references are opaque to the Motus kernel.**
   - A profile may identify a framework and version and carry bounded labels or references needed to identify a requirement.
   - Motus does not parse statutes, standards, guidance, policies, or legal prose.
   - Motus does not decide whether two external requirements are legally equivalent.

3. **Version 1 may reference only evidence surfaces that already exist in Motus.**
   - The exact closed vocabulary is implementation work after acceptance.
   - It may include canonical execution evidence and neutral artifacts introduced by ADR-035 through ADR-037.
   - Adding a new profile must not require changing those artifacts.

4. **Version 1 deliberately avoids a policy DSL.**
   - Evidence expectations remain simple declarations.
   - Version 1 does not introduce arbitrary predicates, expressions, scripts, regular-expression conditions, or user-defined code.
   - Richer selection semantics require measured evidence and a later ADR.

5. **Assessment reports evidence state, not compliance state.**
   - At minimum it distinguishes:
     - missing — no evidence of the required kind was supplied;
     - not_verified — evidence was supplied but required independent binding material was unavailable;
     - mismatched — supplied evidence contradicts a binding that an existing Motus verifier can evaluate;
     - matched — the evidence expectation was evaluated using existing Motus contract/binding semantics and matched.
   - Any aggregate must be named in evidence terms such as evidence_complete, never compliant, conformant, certified, passed, safe, or a synonym.

6. **Existing Motus verifiers remain the authority for evidence semantics.**
   - Profile assessment composes existing validators and binding verifiers.
   - It must not re-implement weaker versions of System Manifest, ControlApplication, HumanOversightReceipt, receipt, or package verification.
   - An artifact reported mismatched by an existing verifier cannot be counted as matched by the profile layer.

7. **Missing evidence and unverifiable evidence remain different facts.**
   - Absence of a document is missing.
   - Presence without material needed to verify a claimed binding is not_verified.
   - Neither state is success.

8. **One exact profile has a derived identity.**
   - profile_fingerprint is derived from the canonical profile document using existing canonical JSON rules.
   - The fingerprint is not embedded in the profile.
   - An assessment names the exact profile fingerprint it used.

9. **The evaluator is pure, deterministic, read-only, and local.**
   - Same profile plus same supplied evidence yields the same assessment.
   - No network call, database lookup, LLM call, clock read, hidden state, mutable cache, or external policy service is required by the core evaluator.
   - Storage and transport remain consumer concerns.

10. **Framework content does not ship in Motus core merely because the mechanism can consume it.**
    - The first implementation proves the mechanism with neutral fixtures.
    - AI Act, ISO 42001, NIS2, D.Lgs., procurement, customer, or sector-specific profiles may be maintained outside the kernel and versioned independently.
    - Updating a legal or standards mapping must not require a Motus runtime release.

11. **The core remains incapable of issuing a legal verdict.**
    - A complete evidence mapping proves only that the profile's declared evidence expectations matched the supplied evidence.
    - It does not establish legal sufficiency, applicability, organizational compliance, effectiveness, adequacy, certification, conformity, competence, independence, or fitness for purpose.

12. **The first implementation is deliberately small.**
    - One profile schema.
    - Executable structural and semantic validation.
    - Derived fingerprint.
    - One deterministic public assessment API.
    - Neutral fixtures and focused tests.
    - Mutation proof and adversarial review.
    - No new runtime dependency.
    - No storage, database, HTTP, MCP, UI, scheduler, workflow engine, plugin framework, legal-text ingestion, ontology, vector store, or LLM.

## Initial conceptual shape

The exact field-by-field schema is implementation work after acceptance. The decision constrains it toward a document with:

- schema_version;
- profile.id and profile.version;
- a bounded requirements list;
- each requirement carrying an opaque requirement_ref;
- each requirement listing one or more existing Motus evidence kinds.

A corresponding assessment names the profile_fingerprint, the requirement_ref, one finding per expected evidence kind, and optionally evidence_complete. That aggregate means only completeness against the profile's declared evidence expectations.

## Consequences

**Regulatory vocabulary moves out of neutral evidence.** The same evidence can be assessed against multiple frameworks without changing identity.

**Motus gains a composition layer without becoming a GRC system.** Consumers can build an Evidence Matrix or gap view from deterministic machine-readable findings. Dashboard, dossier, workflow, legal conclusion, remediation, and continuous monitoring remain outside the kernel.

**Profiles can change faster than Motus.** A standards update or customer-specific mapping can produce a new profile revision without a runtime release.

**A bad profile can still be wrong about the world.** Motus can validate its shape, fingerprint it, and evaluate its declared mappings. It cannot certify that the profile author interpreted a law or standard correctly.

**Version 1 intentionally cannot express every policy.** If real integrations demonstrate a need for exact selectors, cardinality, temporal relationships, separation-of-duties rules, or compound predicates, those requirements must be measured and decided later.

## Alternatives rejected

### Add regulatory fields to existing evidence artifacts

Rejected. It couples neutral evidence identity to one framework and makes framework revision an evidence-format migration.

### Ship AI Act or ISO mappings in the first Motus implementation

Rejected. The mechanism must first prove that profiles can be external and versioned independently.

### Build a general policy or rules engine

Rejected. The current requirement is evidence mapping, not arbitrary business-rule execution.

### Use an LLM to interpret requirements or evidence

Rejected. Kernel assessment must be deterministic and independently reproducible.

### Let Orbis implement mapping semantics independently

Rejected. Orbis owns presentation, dossiers, workflow and UX, but evidence-state semantics must remain stable and portable beside the evidence contracts they compose.

### Put dashboards, gap analysis and reports in Motus

Rejected. Motus returns machine-readable findings. Orbis or another consumer decides how to present and operationalize them.
