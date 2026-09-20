# ADR-036 — Risks and controls are declared separately from evidence that a control was applied

- **Status:** ACCEPTED
- **Date:** 2026-09-20
- **Accepted:** 2026-09-21, by the founder.
- **Authority:** CTO proposes; the founder accepts.
- **Depends on:** ADR-001 (contract authority), ADR-020 (trust model), ADR-027 (execution identity), ADR-034 (Motus owns evidence; bridges only expose it), ADR-035 (System Manifest declaration != execution proof).
- **Amends on acceptance:** adds canonical, jurisdiction-agnostic contract surfaces for a Risk & Control Registry and ControlApplication evidence. It does **not** amend trace.v1, graphspec.v1, commitment.v1, checkpoint.v1, receipt.v1, System Manifest v1, or the evidence-package wire format unless a later ADR explicitly does so.

## Context

ADR-035 deliberately keeps policies and controls inside the System Manifest as declarations. A manifest may say that a control exists and where the operator says it is enforced; it does not prove that the control was evaluated or applied in any execution.

That boundary now creates the next missing layer.

Motus needs to represent two different facts without collapsing them:

1. **governance intent** — which risks an operator has identified, which controls are intended to address them, and which policy/system revision those declarations belong to;
2. **execution evidence** — whether a named control was actually evaluated at a named execution/enforcement point, what decision it produced, and which Motus evidence binds that event.

The first is registry data. The second is evidence.

If both are placed in one mutable record, a dashboard could turn "control exists" into "control ran". If ControlApplication is placed in the System Manifest, an execution event becomes part of system identity. If regulation-specific classifications are embedded in the core registry, the same risk/control identity moves when jurisdiction or certification framework changes.

The required invariant is therefore the same one ADR-035 established at the system level:

**declaration is not proof.**

## Decision

1. **Risk & Control Registry is declarative governance data, not execution evidence.**
   - A registry revision describes risks, controls, their relationships, and neutral metadata.
   - Registry validity proves only that the declaration is well formed.
   - It does not prove that a risk exists in fact, that a control is effective, that a control ran, or that any compliance obligation is satisfied.

2. **ControlApplication is a separate evidence type.**
   - One ControlApplication records one evaluation/application of one declared control at one concrete evidence boundary.
   - It is never embedded inside the registry document and never embedded inside the System Manifest identity.
   - A ControlApplication may bind to an execution by ADR-027 `execution_ref` when the application is execution-scoped.
   - Non-execution-scoped organizational evidence is outside this first Motus surface.

3. **The registry and application have separate identities.**
   - One registry has a stable operator-assigned `registry_id`.
   - One registry revision has an operator-assigned `registry_version`.
   - One exact registry revision has a derived `registry_fingerprint = sha256:<64 lowercase hex>` over canonical JSON.
   - Each risk has a stable `risk_id` within the registry namespace.
   - Each control has a stable `control_id` within the registry namespace.
   - A ControlApplication carries its own derived fingerprint; the fingerprint is not self-embedded in the application document.

4. **Risk-to-control relationships are explicit and many-to-many.**
   - A risk may be addressed by zero, one, or many controls.
   - A control may address zero, one, or many risks.
   - The registry stores identifiers/edges, not effectiveness claims.
   - Missing controls for a risk are valid declarations; later regulatory profiles may evaluate whether that is acceptable for a particular framework.

5. **The v1 risk vocabulary remains neutral.**
   A risk declaration may carry generic fields such as:
   - `risk_id`
   - `title`
   - `description`
   - optional `category`
   - optional operator-declared `likelihood`
   - optional operator-declared `impact`
   - optional operator-declared `treatment`
   - optional references

   These are declarations. Numeric or categorical scales are operator-defined metadata unless a later profile supplies a mapping. Motus does not infer a legal risk class from them.

6. **The v1 control vocabulary remains neutral.**
   A control declaration may carry generic fields such as:
   - `control_id`
   - `title`
   - optional `description`
   - optional `policy_ref`
   - optional `enforcement_point`
   - optional `control_type`
   - optional references
   - zero or more linked `risk_id` values

   No registry field may assert `effective: true`, `passed: true`, `compliant: true`, or equivalent operational/legal verdicts.

7. **ControlApplication reports what happened, not whether the organization is compliant.**
   The application records at minimum:
   - the exact registry revision/fingerprint that defined the control;
   - `control_id`;
   - the application/evaluation timestamp;
   - the enforcement point or evaluation point;
   - an outcome from a closed neutral vocabulary;
   - evidence bindings sufficient to locate the Motus execution/evidence involved.

   Initial outcome vocabulary:
   - `applied`
   - `blocked`
   - `allowed`
   - `not_applicable`
   - `error`

   These terms describe the control event. They are not compliance or effectiveness verdicts.

8. **A ControlApplication must be evidence-bound, not free-floating.**
   - For execution-scoped controls, the application carries canonical `execution_ref`.
   - Where available, it also binds to the exact `manifest_fingerprint` and registry fingerprint under which the control was evaluated.
   - The application may reference receipt/trace-derived identifiers only by the canonical identities Motus already owns; it must not invent a second execution identity.
   - Retrieval of a ControlApplication is not verification of the referenced execution evidence.

9. **ControlApplication cannot elevate ADR-020 assurance levels.**
   A structurally valid application says that Motus recorded a control event. It does not by itself establish EXISTENCE, RETENTION, IDENTITY, LEGAL_TIME, or any other ADR-020 level. Those levels remain governed by their existing mechanisms.

10. **Registry revisions are immutable declarations.**
    - A new governance state is represented by a new registry revision/fingerprint, optionally declaring `supersedes`.
    - Historical ControlApplications continue to bind the registry revision that existed when they were recorded.
    - Updating a risk or control never rewrites historical evidence.

11. **System Manifest and Risk & Control Registry are related but not collapsed.**
    - The System Manifest remains system configuration identity.
    - The registry remains governance declaration.
    - The first implementation may allow the registry to declare which `system_id` / manifest fingerprint it applies to, but the registry does not become the System Manifest and the manifest does not absorb risk analysis.
    - Cross-document binding semantics must be explicit and one-directional enough to avoid circular fingerprints.

12. **The core remains jurisdiction-agnostic.**
    The registry and ControlApplication contain no AI Act article numbers, NIS2 clauses, ISO control numbers, D.Lgs. references, certification status, conformity verdict, or national-law risk class. Regulatory Evidence Profiles later map neutral risk/control declarations and applications to external frameworks.

13. **No control detector or policy engine becomes authority merely by producing an application.**
    A model, rule engine, gateway, human reviewer, or external service may supply an observation/result, but Motus owns the evidence record and its binding semantics. The producer's claim is recorded; authority is determined by the Motus contract and any later attestation/assurance evidence.

14. **The first implementation adds no runtime dependency and changes no existing wire format.**
    New schemas, executable contract validation, public verification/query helpers, and focused tests may be added. Existing trace/receipt corpora are not rewritten.

## Initial conceptual shape

The exact schemas are implementation work after acceptance. The decision constrains the registry toward:

```json
{
  "schema_version": "1.0.0",
  "registry": {
    "id": "operator-risk-control-registry",
    "version": "2026.09.20-1"
  },
  "system": {
    "system_id": "operator-system-id",
    "manifest_fingerprint": "sha256:..."
  },
  "risks": [
    {
      "risk_id": "R-001",
      "title": "Untrusted instructions may influence model output",
      "category": "security"
    }
  ],
  "controls": [
    {
      "control_id": "C-001",
      "title": "Tool capability gate",
      "risk_refs": ["R-001"],
      "enforcement_point": "tool_dispatch"
    }
  ],
  "created_at": "RFC3339 UTC instant",
  "supersedes": null
}
```

and ControlApplication toward:

```json
{
  "schema_version": "1.0.0",
  "registry_fingerprint": "sha256:...",
  "control_id": "C-001",
  "execution_ref": "tenant/writer/sequence",
  "manifest_fingerprint": "sha256:...",
  "enforcement_point": "tool_dispatch",
  "outcome": "blocked",
  "observed_at": "RFC3339 UTC instant",
  "evidence": {
    "kind": "motus_execution"
  }
}
```

These examples are explanatory, not accepted field-by-field contracts.

## Consequences

**Governance state becomes addressable without pretending it happened.** Auditors and regulatory profiles can refer to one exact registry revision while execution evidence independently shows which controls were evaluated.

**Historical evidence stays stable.** A later risk reassessment or control redesign creates a new registry revision rather than mutating the context of old executions.

**The cost is another explicit join.** A complete regulatory dossier must join System Manifest, registry revision, ControlApplication, and core execution evidence. That is deliberate: combining them would erase which source owns which fact.

**Control effectiveness remains outside v1.** One successful application proves one recorded control event, not that the control is globally effective. Statistical effectiveness, control testing, organizational approval, audit conclusions and certification remain later layers.

**Profiles can disagree without changing evidence.** AI Act, ISO, NIS2 or national-law mappings may classify the same neutral risk/control evidence differently while the Motus evidence stays unchanged.

## Alternatives rejected

### Put risks and controls directly into the System Manifest

Rejected. The manifest identifies system configuration. Risk assessments and governance controls change on a different lifecycle and may be revised without changing executable configuration.

### Treat a declared control as evidence that it ran

Rejected. That repeats the exact declaration-versus-proof collapse ADR-035 prohibits.

### Put ControlApplication into trace.v1 records immediately

Rejected for v1. It would revise the core execution wire format before the evidence semantics are exercised independently. A later ADR may integrate a compact binding into trace production if experience proves it necessary.

### Use regulation-specific risk/control fields in the core schema

Rejected. It would make Motus evidence identity depend on jurisdiction and external standards revisions.

### Use one mutable registry row per risk/control

Rejected as the canonical evidence model. Mutable storage may exist behind an API, but canonical evidence must preserve revision identity so historical applications remain interpretable.

### Let ControlApplication say `effective` or `compliant`

Rejected. One event cannot establish organizational effectiveness or compliance, and Motus must not manufacture that conclusion from a runtime observation.
