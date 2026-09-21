# ADR-037 — Human oversight is an execution event, not a compliance verdict

- **Status:** ACCEPTED
- **Date:** 2026-09-21
- **Accepted:** 2026-09-21, by the founder.
- **Authority:** CTO proposes; the founder accepts.
- **Depends on:** ADR-001 (contract authority), ADR-020 (trust model), ADR-027 (execution identity), ADR-034 (Motus owns evidence; bridges only expose it), ADR-035 (system declaration != execution proof), ADR-036 (control declaration != ControlApplication evidence).
- **Amends on acceptance:** adds a canonical, jurisdiction-agnostic `HumanOversightReceipt` contract surface for recording one claimed human oversight event bound to Motus execution evidence. It does **not** amend trace.v1, graphspec.v1, commitment.v1, checkpoint.v1, receipt.v1, System Manifest v1, Risk & Control Registry v1, ControlApplication v1, or the evidence-package wire format unless a later ADR explicitly does so.

## Context

Motus can now distinguish three facts that regulatory systems routinely collapse:

1. a System Manifest declares which system configuration an operator says is under review;
2. a Risk & Control Registry declares risks and controls;
3. a ControlApplication records that one declared control was evaluated or applied at one evidence boundary.

None of those facts says that a human examined a particular execution or decision, what intervention the human made, or what disposition the human recorded.

Adding `human_oversight: true` to a manifest or registry would only create another declaration. Treating every action made through a user interface as oversight would infer intent from transport. Treating an authenticated account as proof of human judgment would confuse identity with conduct. Embedding a mutable approval state in an execution record would erase which decision was original and which event happened later.

The missing object is therefore an event receipt: a portable record that one producer claims a human actor performed one named oversight action over one named Motus execution boundary. It must preserve the distinction between what the receipt records and what independent evidence can verify.

The required invariant is:

**a recorded human intervention is evidence that the intervention was recorded; it is not proof that the actor was human, that the review was adequate, or that any legal requirement was satisfied.**

## Decision

1. **HumanOversightReceipt is execution evidence, not organizational policy or system identity.**
   - One receipt records one oversight event concerning one execution-scoped subject.
   - It is not embedded in the System Manifest, Risk & Control Registry, or ControlApplication.
   - A policy that requires human oversight remains a declaration; a HumanOversightReceipt records a claimed event under that policy.

2. **Every v1 receipt is bound to the canonical Motus execution identity.**
   - `execution_ref` is required and uses ADR-027's existing identity; no second run identity is invented.
   - A receipt may additionally bind the exact `manifest_fingerprint`, `registry_fingerprint`, and `control_application_fingerprint` relevant to the intervention.
   - Optional bindings do not become verified merely because they are schema-valid. Independent binding verification must report missing source material as unverified, never as a match.

3. **The subject of oversight is explicit.**
   - The receipt identifies whether the human acted on the execution as a whole, a named decision point, or one ControlApplication.
   - A decision-point subject carries a bounded operator-assigned `decision_ref`; it does not claim that arbitrary prose is a Motus execution identity.
   - A ControlApplication subject carries the derived application fingerprint and the same `execution_ref` as that application.

4. **Actor, role and authority are separate facts.**
   - `actor_ref` is an operator-assigned reference to the claimed actor.
   - An optional `role` records the role under which the actor is said to have acted.
   - An optional `authority_ref` may point to the policy or delegation said to authorise the act.
   - None of these fields proves that the actor is a natural person, that the actor controlled the credential, or that the role or authority was valid. Identity and authority assurance require independent evidence.

5. **The oversight action uses a closed, neutral vocabulary.**
   The v1 action is exactly one of:
   - `reviewed` — inspection was recorded without a directional disposition;
   - `approved` — the actor recorded approval of the subject;
   - `rejected` — the actor recorded rejection of the subject;
   - `overridden` — the actor recorded a decision different from the automated or prior disposition;
   - `escalated` — the actor referred the subject to another decision boundary;
   - `abstained` — the actor recorded that no substantive disposition was made.

   These values describe the recorded event. `approved` does not mean compliant, safe, correct or legally authorised; `overridden` does not mean the replacement decision was better.

6. **An override cannot erase the decision it overrides.**
   - An `overridden` receipt identifies the prior decision or ControlApplication by its canonical binding and records the replacement disposition separately.
   - The original execution evidence remains unchanged and independently verifiable.
   - A consumer must be able to display both the original and human-recorded dispositions; it must not rewrite history into one final state.

7. **Time is recorded without manufacturing legal time or causal order.**
   - `observed_at` is a required RFC 3339 UTC instant supplied by the producer.
   - An optional `recorded_at` may distinguish when Motus received the event from when the producer says it occurred.
   - Either value is a claim until supported by existing assurance mechanisms. The receipt alone establishes neither contemporaneity nor ADR-020 `LEGAL_TIME`.
   - A timestamp after execution does not prove that the human could intervene before an effect occurred.

8. **The receipt carries bounded explanation, not an unbounded second record system.**
   - An optional rationale is human-authored context with an explicit size bound.
   - Optional references are bounded opaque identifiers or URIs; their contents are not imported into the receipt's truth.
   - Secrets, full prompts, model outputs, personal dossiers and uploaded documents are not required by the core contract.

9. **One exact receipt has a derived identity.**
   - `oversight_fingerprint = sha256:<64 lowercase hex>` is derived over the canonical receipt document.
   - The fingerprint is not embedded in the document, avoiding self-reference.
   - Corrections produce a new receipt and may name the prior fingerprint through `supersedes`; they never mutate the historical receipt.

10. **Multiple receipts do not collapse into one hidden state machine.**
    - Zero, one or many HumanOversightReceipts may refer to the same execution or subject.
    - Ordering and supersession are explicit evidence relationships.
    - Motus does not infer a single current approval state when receipts conflict, arrive late or come from different actors.

11. **A receipt does not elevate ADR-020 assurance levels.**
    - Structural validity says only that the record has the canonical shape.
    - Binding verification says only which referenced Motus artifacts agree.
    - Actor identity, credential control, authority, natural-person status, completeness of review, independence, competence, timeliness and legal sufficiency remain unestablished unless separate evidence establishes them.

12. **The core remains jurisdiction-agnostic.**
    - The receipt contains no AI Act article, ISO clause, NIS2 reference, national-law role, conformity status or statutory adequacy verdict.
    - Later Regulatory Evidence Profiles may map the neutral event to framework-specific requirements without changing its identity.

13. **Producers do not become authority by emitting the receipt.**
    - A UI, gateway, identity provider, workflow engine, model, agent or external service may supply the event.
    - Motus owns the canonical evidence shape and binding semantics; it records the producer's claim without certifying the producer's interpretation.

14. **The first implementation is additive and dependency-free.**
    - It may add a new schema, executable contract validation, fingerprint derivation, binding verification, public query helpers, fixtures and focused tests.
    - It adds no runtime dependency, rewrites no frozen corpus, and changes no existing wire format.
    - Storage, HTTP, MCP, UI, authentication and regulatory mapping remain outside this first surface.

## Initial conceptual shape

The exact schema is implementation work after acceptance. The decision constrains it toward:

```json
{
  "schema_version": "1.0.0",
  "execution_ref": "tenant/writer/sequence",
  "subject": {
    "kind": "decision_point",
    "decision_ref": "tool-dispatch-4"
  },
  "actor": {
    "actor_ref": "operator-user-42",
    "role": "reviewer",
    "authority_ref": "policy:human-review-v3"
  },
  "action": "overridden",
  "prior_disposition": "allowed",
  "recorded_disposition": "rejected",
  "observed_at": "RFC3339 UTC instant",
  "recorded_at": "RFC3339 UTC instant",
  "bindings": {
    "manifest_fingerprint": "sha256:...",
    "registry_fingerprint": "sha256:...",
    "control_application_fingerprint": "sha256:..."
  },
  "rationale": "Bounded human-authored context",
  "supersedes": null
}
```

This example is explanatory, not an accepted field-by-field contract.

## Consequences

**Human intervention becomes independently addressable.** A consumer can locate the execution, subject, claimed actor, action and disposition without turning a UI audit row into a new source of truth.

**Original automated evidence remains visible.** An override adds evidence instead of rewriting the decision it replaces.

**The cost is another explicit join.** A complete account of oversight may require the execution evidence, System Manifest, Registry, ControlApplication and one or more HumanOversightReceipts. This is deliberate: each artifact owns a different fact.

**A valid receipt may still contain a false claim.** Without independent identity, authority and time evidence, an operator can misstate who acted or when. The contract exposes that boundary rather than hiding it behind a field named `human`.

**The core cannot answer whether oversight was sufficient.** Number of reviewers, separation of duties, competence, response time, mandatory escalation and legal adequacy belong to later profiles or organizational policy. Different frameworks may evaluate the same receipt differently without changing it.

**Conflicting receipts remain visible.** Consumers must not silently choose the last timestamp or highest-privilege role as truth. A later dossier/profile may define evaluation rules, but the evidence layer preserves the conflict.

## Alternatives rejected

### Add `human_oversight: true` to the System Manifest or Registry

Rejected. That records a policy or configuration declaration, not an intervention concerning a concrete execution.

### Treat every authenticated UI action as human oversight

Rejected. Authentication identifies a credential under some assurance model; it does not prove natural-person control, meaningful review, authority or intent.

### Put the approval directly into ControlApplication

Rejected. A control event and a human intervention have different producers, timing and identity semantics. Combining them would make the control appear human-reviewed whenever the application record exists.

### Rewrite the automated decision with the human's final decision

Rejected. It destroys the evidence necessary to show that an override occurred and prevents independent review of both decisions.

### Embed HumanOversightReceipt in trace.v1 immediately

Rejected for v1. It would revise the core execution format before the event semantics and binding rules have been exercised independently. A later ADR may integrate production if evidence shows that a separate receipt cannot preserve required ordering.

### Use arbitrary strings for actions and outcomes

Rejected. Free-form statuses make equivalent events incomparable and let jurisdiction- or product-specific conclusions enter the canonical evidence vocabulary silently.

### Treat a signature as proof that adequate human review occurred

Rejected. A signature can bind a key to bytes under an identity policy. It cannot prove what the signer saw, understood, was authorised to decide, or whether the intervention was timely and sufficient.
