# ADR-039 — Incident and CAPA records are append-only claims, not verdicts of blame, effectiveness, or closure

- **Status:** ACCEPTED
- **Date:** 2026-09-23
- **Accepted:** 2026-09-23, by the founder.
- **Authority:** CTO proposes; the founder accepts.
- **Depends on:** ADR-001 (contract authority), ADR-020 (trust model), ADR-027 (execution identity), ADR-034 (Motus owns evidence; bridges only expose it), ADR-035 (system declaration != execution proof), ADR-036 (control declaration != ControlApplication evidence), ADR-037 (oversight event != compliance verdict), ADR-038 (profiles map evidence; they do not decide compliance).
- **Amends on acceptance:** permits canonical, jurisdiction-neutral IncidentDeclaration, CAPAAction, and LedgerEntry contract surfaces plus deterministic structural and binding verification. It does **not** amend trace, GraphSpec, commitment, checkpoint, receipt, System Manifest, Risk & Control Registry, ControlApplication, HumanOversightReceipt, Regulatory Evidence Profile, or evidence-package wire formats unless a later ADR explicitly does so.

## Context

Motus can identify an execution, preserve its evidence, describe the system, declare risks and controls, record one control application, record one claimed human oversight event, and map external requirements to existing evidence. None of those artifacts records that an operator declared an incident, what corrective or preventive work was proposed or performed, or how later corrections preserve the original account.

An incident-management system usually combines mutable workflow state, assignments, discussion, notifications, legal classification, root-cause analysis, remediation and closure. Importing that model into Motus would turn the evidence kernel into a case-management or GRC product. At the other extreme, one mutable incident document would let a correction erase the statement that preceded it and would make historical CAPA evidence depend on the current database row.

The missing surface is narrower: portable immutable declarations, linked explicitly to each other and to independently verifiable Motus evidence. Storage, workflow and regulatory conclusions remain outside the kernel.

The required invariant is:

**Motus records what a producer declared about an incident and its corrective or preventive actions; it does not infer blame, reportability, root cause, action effectiveness, compliance, or closure from the presence, order, or wording of those records.**

## Decision

1. **An incident declaration, a CAPA action and supporting evidence remain separate facts.**
   - IncidentDeclaration records one producer's bounded account of an observed or suspected incident.
   - CAPAAction records one corrective, preventive, or combined action linked to that incident.
   - Existing Motus evidence is referenced by canonical identity and is not embedded, copied, or reinterpreted as incident truth.
   - A valid declaration does not prove that the incident occurred as described; a valid action does not prove that the action was performed or effective.

2. **The ledger is a verifiable collection, not a workflow engine or mutable database.**
   - Version 1 defines immutable entries and deterministic relationships between them.
   - A storage system may index or project a current view, but that projection is not canonical evidence.
   - The core performs no assignment, notification, scheduling, escalation, approval, ticketing, or case-management transition.

3. **Stable references and exact-document identities are distinct.**
   - incident_id and action_id are bounded operator-assigned references within a declared producer namespace.
   - Every exact IncidentDeclaration, CAPAAction, or amendment has a derived sha256 fingerprint over canonical JSON; the fingerprint is not embedded in the document.
   - A stable identifier locates a declared subject. A fingerprint identifies the exact bytes-as-value revision being verified. Neither may substitute for the other.

4. **History is append-only; corrections are new records.**
   - A published canonical record is never updated in place or deleted by the contract.
   - A correction names exactly one prior fingerprint through supersedes, preserves the same stable incident_id or action_id, and receives a new fingerprint.
   - The superseded record remains valid historical evidence of what was declared earlier.
   - Supersession is not erasure and does not make the earlier claim false; it records a later producer claim about which revision should be considered.

5. **Amendment chains are explicit and mechanically constrained.**
   - A record may supersede only a record of the same kind, stable identifier, and producer namespace.
   - One amendment names one immediate predecessor; skipped predecessors, self-reference and cycles are invalid.
   - Forks are preserved and reported as conflicts. Motus does not select a winner by timestamp, ingestion order, actor role, or last-write-wins.
   - Missing predecessor material is reported as not_verified, not accepted as a complete chain.

6. **Incident classification remains neutral and declared.**
   - Version 1 may carry bounded title, description, observed_at, discovered_at, producer_ref, affected-system references and a closed neutral event class.
   - Severity, impact, suspected cause and containment may be present only as producer-declared values with their scale or vocabulary identified.
   - Motus does not derive legal reportability, safety significance, personal-data-breach status, regulatory category, fault, negligence, intent, or liability.

7. **CAPAAction describes an action claim, not effectiveness.**
   - action_kind is corrective, preventive, or corrective_and_preventive.
   - The record carries a bounded action description, producer_ref and the incident fingerprint it was created against.
   - It may declare lifecycle observations such as proposed, started, completed, cancelled, or blocked only as producer claims.
   - completed means that the producer declared completion of the described work. It does not mean the action was adequate, successful, effective, independently verified, approved, or legally sufficient.

8. **Incident closure is never inferred.**
   - No number or combination of CAPAAction records closes an incident automatically.
   - A producer may add an explicit closure declaration as a new incident revision, with bounded rationale and references.
   - closed is a declared lifecycle state, not a Motus verdict that remediation succeeded or obligations ended.
   - Reopening is another amendment; it does not delete the closure declaration.

9. **Execution and regulatory bindings use identities Motus already owns.**
   - An incident or action may reference zero or more ADR-027 execution_ref values and exact fingerprints for System Manifest, registry, ControlApplication, HumanOversightReceipt, receipt, package, or other accepted Motus evidence.
   - It must not invent a second execution identity or weaken an existing verifier.
   - Structural validity of the incident/CAPA record does not verify any referenced artifact.
   - Binding verification distinguishes matched, mismatched, missing and not_verified using existing Motus semantics where available.

10. **Time remains a producer claim unless separately assured.**
    - observed_at, discovered_at, declared_at, due_at, started_at and completed_at are RFC 3339 UTC instants when present.
    - Their ordering is structurally checked only where the contract can do so without guessing business meaning.
    - A timestamp alone proves neither contemporaneity, causal order, timeliness, retention, nor ADR-020 LEGAL_TIME.

11. **Actor, producer, assignee and authority are separate references.**
    - producer_ref identifies who is claimed to have emitted a record.
    - Optional actor_ref, assignee_ref and authority_ref describe claimed roles without proving natural-person status, credential control, competence, independence or authorization.
    - Motus does not turn authentication metadata into blame or organizational standing.

12. **Stop conditions are explicit.**
    Implementation stops and requires a later founder-accepted ADR if it would need to:
    - decide whether an event is legally reportable or which deadline applies;
    - infer blame, root cause, negligence, liability or regulatory classification;
    - decide that a CAPA action is effective, adequate or compliant;
    - choose a winning amendment fork or silently discard history;
    - require mutable canonical rows, hidden server state, a network call, an LLM, or a policy engine for deterministic verification;
    - modify an existing wire format or assurance-level meaning;
    - add jurisdiction-specific fields to the neutral core.

13. **The core remains jurisdiction- and product-agnostic.**
    - No AI Act article, NIS2 category, GDPR breach conclusion, ISO nonconformity class, national reporting authority, statutory deadline or customer workflow status belongs in the v1 canonical vocabulary.
    - Regulatory Evidence Profiles may map neutral incident/CAPA evidence to external requirements without changing its identity.

14. **The first implementation is additive, local and dependency-free.**
    - It may add schemas, executable validation, fingerprint derivation, amendment-chain and binding verification, public read-only helpers, neutral fixtures and focused tests.
    - It adds no runtime dependency, database, HTTP endpoint, MCP tool, UI, scheduler, notification service, workflow engine, legal rules, automatic root-cause analysis or evidence-package change.
    - Mutation proof, independent verification, adversarial closure and green Jenkins evidence remain release gates for v0.18.0.

## Initial conceptual shape

The exact field-by-field schemas are implementation work after acceptance. The decision constrains an incident declaration toward a document containing schema_version, producer namespace, incident_id, declared lifecycle state, bounded description and declared classifications, relevant timestamps, evidence bindings, and supersedes.

A CAPA action is constrained toward a document containing schema_version, producer namespace, action_id, incident_fingerprint, action_kind, bounded description, declared lifecycle observation, relevant timestamps, evidence bindings, and supersedes.

A ledger view is a deterministic ordered collection of exact records plus validation findings. Ordering must be based on explicit relationships and canonical tie-breaking defined by the contract; ingestion order is never evidence.

## Consequences

**History remains inspectable.** A correction adds a verifiable link instead of replacing the earlier account.

**The same neutral evidence can support different regulatory analyses.** Framework-specific reportability and deadlines remain profile or consumer concerns.

**The cost is explicit joins and visible conflict.** A complete dossier may need the incident chain, action chains, execution evidence and external profile. Forks cannot be hidden behind a current-row projection.

**A valid ledger may contain false or contradictory claims.** Motus can verify shape, identity, lineage and supported bindings; it cannot certify the producer's narrative or organizational process.

**Version 1 is deliberately not operational case management.** Orbis or another system may provide workflow and UI, but must preserve the Motus distinctions between declaration, exact revision, binding status and external conclusion.

## Alternatives rejected

### Put incident state and CAPA into the Risk & Control Registry

Rejected. The registry declares governance design; incidents and actions have their own event and amendment lifecycle. Combining them would make a registry revision rewrite operational history.

### Model the ledger as one mutable incident object

Rejected. Last-write-wins erases prior claims and makes exact historical verification impossible.

### Treat a completed action as proof of remediation effectiveness

Rejected. Completion is a producer claim about work status. Effectiveness requires separate evidence and an evaluation rule that version 1 does not own.

### Close an incident automatically when all actions are completed

Rejected. It manufactures a governance conclusion from record presence and cannot account for conflicting, inadequate, cancelled or newly required actions.

### Embed jurisdiction-specific reporting fields in the core

Rejected. Applicability, deadlines and reportability vary by framework and context; they belong in external profiles or consumers.

### Let an LLM classify incidents or infer root cause

Rejected. The kernel contract must remain deterministic, reproducible and independently verifiable.

### Put incident and CAPA records directly into trace.v1

Rejected for v1. It would change the execution wire format before the new semantics and amendment rules have been exercised independently.
