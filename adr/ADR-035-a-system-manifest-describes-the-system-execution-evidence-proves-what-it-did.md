# ADR-035 — A system manifest describes the system; execution evidence proves what it did

- **Status:** PROPOSED
- **Date:** 2026-09-20
- **Authority:** CTO proposes; the founder accepts.
- **Depends on:** ADR-001 (contract authority), ADR-020 (trust model), ADR-027 (execution identity), ADR-034 (Motus owns evidence; bridges only expose it).
- **Amends on acceptance:** adds a new `contract/system-manifest.v1.schema.json` contract surface and the corresponding contract documentation/validator rules. It does **not** amend `trace.v1`, `graphspec.v1`, `commitment.v1`, `checkpoint.v1`, `receipt.v1`, or the evidence-package wire format.

## Context

Motus can already identify and verify one execution far more precisely than it can identify the AI system that execution belongs to.

The repository already carries independently checkable execution-side identities:

- the Motus distribution version and trace schema version;
- GraphSpec `name`, `version`, `spec_schema_version`, `graph_fingerprint`, and the trace-carried `code_fingerprint`;
- node names, effect classes, and declared read/write surfaces;
- ADR-027 `execution_ref`, with `run_id` explicitly correlation metadata rather than identity;
- the derived execution fingerprint (`Trace.root`);
- commitment coordinates `tenant / writer_id / sequence`;
- receipt segments, anchors, attestations, and achieved assurance mode;
- evidence packages that carry the exact trace, graph spec and receipt without becoming a fifth identity.

What does not exist is a canonical document that says which AI system a deployment claims to be: which runtime, graphs, models, tools, services, data sources, policies and controls form the system configuration under review.

Without that document, every future regulatory profile or compliance bridge has to invent its own system inventory. That would make the same deployment acquire different identities depending on whether the consumer is an auditor, a dashboard, a regulator-specific exporter, or a customer application.

The obvious alternative is also wrong: putting fields such as `compliant: true`, `human_oversight: true`, or `prompt_injection_protected: true` in a manifest would turn configuration claims into evidence. ADR-020 exists to prevent exactly that collapse. A declaration can be useful without being proof.

## Decision

1. **The System Manifest is a versioned declaration of system identity and configuration, not evidence that the declared system behaved as stated.** A valid manifest can say what the operator declares the system to be. It cannot by itself establish that a control executed, that a human reviewed an outcome, that an incident was handled, that retention occurred, or that any legal or certification requirement is satisfied.

2. **One system has a stable `system_id`; one configuration has a `manifest_version`; one exact manifest has a derived `manifest_fingerprint`.**
   - `system_id` is operator-assigned and stable across revisions.
   - `manifest_version` is operator-assigned revision metadata.
   - `manifest_fingerprint` is `sha256:<64 lowercase hex>` over the contract's canonical JSON form of the complete manifest.
   - The fingerprint is derived and is **not embedded inside the manifest**, so the document never hashes a field that claims to be its own hash.

3. **The schema separates `bindings` from `declarations`.**
   - A **binding** is a field for which Motus defines an independent verification recipe against an authoritative artifact. Initial bindings are limited to Motus/runtime and GraphSpec/trace identities already present in the repository.
   - A **declaration** is operator-supplied context whose truth Motus cannot establish from the manifest alone: operator identity, external components, models, tools, data sources, policies, controls and similar system inventory.
   - Parsing or validating a declaration never promotes it to a binding.

4. **A binding is not considered verified merely because its value is schema-valid.** Binding verification is an explicit operation over the manifest plus the artifact that owns the fact. For example, a declared graph fingerprint is checked by recomputing it from the supplied GraphSpec; a runtime version is checked against the runtime/artifact being evaluated. A verifier reports absent verification material as unverified, never as a match.

5. **The initial manifest is system-scoped, not execution-scoped.** No `execution_ref`, `run_id`, receipt, trace root, human decision, incident or CAPA event is stored as part of the manifest's identity. Those are evidence about events. Later evidence profiles may bind one manifest fingerprint to one or more execution references.

6. **Existing execution evidence remains independently valid.** A trace, receipt or evidence package must continue to validate without a System Manifest. The manifest is additional system context, never a prerequisite for verifying core Motus evidence.

7. **The manifest cannot elevate ADR-020 trust levels.** No field in a System Manifest establishes `INTEGRITY`, `EXISTENCE`, `RETENTION`, `EXECUTION_CONTINUITY`, `PROVENANCE`, `IDENTITY`, or `LEGAL_TIME`. Those remain established, claimed, unchecked or not established only by the mechanisms ADR-020 assigns to them.

8. **The canonical component vocabulary is generic and closed at schema version 1.** A component may be declared as one of:
   - `runtime`
   - `graph`
   - `model`
   - `tool`
   - `service`
   - `data_source`
   - `other`

   Product names, providers, vendors, domains and customer vocabulary are values, never new Motus types.

9. **Policies and controls are references, not verdicts.** A manifest may declare identifiers such as `policy_id`, `control_id`, `reference`, and a generic `enforcement_point`. It must not carry fields whose semantics assert operational success, such as `effective: true`, `passed: true`, `compliant: true`, or equivalent synonyms. Whether a control was actually applied belongs to later ControlApplication evidence.

10. **The core manifest is jurisdiction-agnostic.** It contains no AI Act, NIS2, ISO, national-law, risk-class, conformity, certification or regulatory-article fields. Regulatory mappings consume the same neutral evidence later through a Regulatory Evidence Profile. Adding a jurisdiction must not require changing the System Manifest schema.

11. **Revision lineage is explicit but narrow.** A manifest may carry one optional `supersedes` value naming the prior `manifest_fingerprint`. This proves only what the newer document declares about its predecessor. It does not let Motus assert that the newer manifest is currently deployed, approved, or authoritative.

12. **The manifest is plain portable evidence data, not a service object.** The canonical representation is JSON under a versioned schema. Storage, HTTP, databases, registries, UI and product-specific bridges remain outside the semantic contract, following ADR-034.

13. **The first implementation adds no runtime dependency and no existing wire-format change.** Contract validation remains on the existing validation boundary. Existing frozen trace/receipt corpora are not rewritten to mention the new type.

## Initial contract shape

The exact schema is implementation work after this ADR is accepted, but the accepted decisions constrain it to this conceptual form:

```json
{
  "schema_version": "1.0.0",
  "system": {
    "id": "operator-assigned-id",
    "manifest_version": "operator-assigned-revision",
    "name": "optional human-readable name"
  },
  "bindings": {
    "motus": {
      "runtime_version": "0.x.y",
      "trace_schema_version": "x.y.z"
    },
    "graphs": [
      {
        "name": "graph-name",
        "version": "graph-version",
        "spec_schema_version": "1.0.0",
        "graph_fingerprint": "sha256:...",
        "code_fingerprint": "sha256:..."
      }
    ]
  },
  "declarations": {
    "operator": {
      "id": "..."
    },
    "components": [
      {
        "component_id": "...",
        "kind": "model",
        "name": "...",
        "version": "..."
      }
    ],
    "policies": [
      {
        "policy_id": "...",
        "reference": "..."
      }
    ],
    "controls": [
      {
        "control_id": "...",
        "policy_ref": "...",
        "enforcement_point": "..."
      }
    ]
  },
  "created_at": "RFC3339 UTC instant",
  "supersedes": null
}
```

This example is explanatory. The schema, field optionality and size bounds are contract work and are not accepted merely because they appear here.

## Consequences

**The manifest creates a system identity without weakening execution identity.** A regulator, auditor or customer can refer to one exact declared system configuration while receipts keep naming exact executions by ADR-027.

**The cost is a second validation mode.** Schema validity answers whether a manifest is a well-formed declaration. Binding verification answers whether selected facts agree with authoritative Motus artifacts. Calling both operations "validation" would recreate the presence-versus-verification confusion ADR-034 forbids, so the API and verdict types must keep them distinct.

**Some apparently useful fields are deliberately absent.** Risk classifications, certification state, jurisdictional status, human-review outcomes, incident state, CAPA state, retention state and legal-hold state belong to later evidence types or regulatory profiles. That makes the first manifest smaller and prevents it becoming a catch-all governance database.

**An operator can lie in declarations.** That is not a defect hidden by the design; it is the design's explicit boundary. Later attestations or evidence can make selected declarations externally supportable, but Motus will not turn an unsigned statement into a fact by naming the file "manifest".

**No execution format is forced to change.** The price is that the initial manifest-to-execution relationship is external and must later be expressed by the Regulatory Evidence Profile rather than by adding `system_id` to every trace and receipt immediately.

## Alternatives rejected

### Put `system_id` and `manifest_fingerprint` directly into trace.v1 now

Rejected. It would make every existing execution format depend on a regulatory layer before the relationship has been exercised. Core evidence already has stable execution identity. A later profile can bind that identity to a manifest without making old evidence unverifiable or forcing a trace schema revision.

### Treat every manifest field as verified because the document is signed or hashed

Rejected. A signature can prove who signed a declaration and a hash can prove which bytes were signed. Neither proves that the declared model, policy, control or operator state was true.

### Put regulation-specific fields in the manifest

Rejected. It would create one manifest shape per jurisdiction and make the runtime's system identity move when legislation or certification frameworks change. Regulations map to neutral evidence; they do not define Motus's core evidence vocabulary.

### Make the manifest the AI System Registry

Rejected. A registry is a collection, lifecycle and governance surface over many systems/manifests. The manifest is one immutable declaration of one system revision. Collapsing them would make storage and lifecycle semantics part of the evidence document.

### Let consumers define arbitrary component kinds

Rejected for schema v1. Free-form kinds would make two consumers describe the same system with incompatible taxonomies and prevent deterministic downstream mapping. `other` carries genuinely novel cases without silently expanding the canonical vocabulary.

### Store `manifest_fingerprint` inside the manifest

Rejected. That either creates self-reference or requires a special "hash everything except this field" rule. Motus already avoids duplicate identity fields where a value can be derived; the manifest follows the same discipline.
