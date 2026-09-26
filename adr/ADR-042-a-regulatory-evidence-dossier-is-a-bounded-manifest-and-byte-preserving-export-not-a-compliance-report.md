# ADR-042 — A Regulatory Evidence Dossier is a bounded manifest and byte-preserving export, not a compliance report

- **Status:** PROPOSED
- **Date:** 2026-09-26
- **Authority:** CTO proposes; the founder accepts.
- **Depends on:** ADR-001 (contract authority), ADR-020 (trust and assurance levels), ADR-027 (execution identity), ADR-034 (Motus owns evidence; bridges expose it), ADR-035 through ADR-041 (the neutral regulatory evidence surfaces and their stop conditions).
- **Amends on acceptance:** permits one canonical, jurisdiction-neutral Regulatory Evidence Dossier manifest plus deterministic local pack and verify helpers for a byte-preserving dossier export. It does **not** amend the meaning or wire format of trace, GraphSpec, commitment, checkpoint, receipt, the existing evidence package, System Manifest, Risk & Control Registry, ControlApplication, HumanOversightReceipt, Regulatory Evidence Profile, Incident / CAPA, Retention / Legal Hold, or AI System Registry artifacts.

## Context

Motus now owns independently verifiable evidence for executions and eight regulatory-core capabilities. ADR-038 can map opaque external requirement references to Motus evidence kinds and deterministically report `missing`, `not_verified`, `mismatched`, or `matched` over exactly the artifacts supplied by a caller.

Those artifacts are still exchanged one at a time. A consumer that needs a portable review set must currently invent:

1. which exact profile and evidence artifacts belong to the set;
2. how a member's semantic identity differs from the digest of its transported bytes;
3. how member bytes are named and protected from replacement;
4. whether verification was performed or merely asserted; and
5. whether an archive is a bounded supplied set or a claim of global completeness.

The existing evidence package solves a narrower execution problem: it carries one execution's trace, graph, receipt and related proof material. Expanding that wire format to become a regulatory dossier would couple execution evidence to every later regulatory surface and would make old packages acquire new meaning.

At the other extreme, exporting a PDF, mutable folder or dashboard would create a presentation artifact whose contents cannot be independently recomputed from exact Motus identities. A signed archive would authenticate some bytes but still would not establish legal sufficiency, authority, completeness or compliance.

The missing surface is a bounded manifest over exact Motus artifacts and a deterministic transport archive that carries those original bytes without rewriting them.

The required invariant is:

**A Regulatory Evidence Dossier states which exact Motus artifacts one producer included in one bounded export and lets a stranger verify those bytes with existing Motus validators; it does not decide that the set is complete, legally sufficient, submitted, accepted or compliant.**

## Decision

1. **The dossier manifest and its export archive are different objects.**
   - A Regulatory Evidence Dossier is an immutable canonical JSON manifest.
   - A dossier export is a deterministic archive containing that manifest and the exact member bytes it names.
   - The archive is transport, not a new source of regulatory facts.
   - The manifest can be validated and fingerprinted without constructing an archive.

2. **Every dossier is explicitly bounded.**
   - It names one producer namespace, one stable `dossier_id`, one subject reference, one declared observation time and one exact Regulatory Evidence Profile fingerprint.
   - The subject reference locates the producer-declared review subject; it does not prove legal scope, ownership, deployment or authority.
   - A dossier covers only its listed members and the requirements declared by its exact profile.
   - No field or verifier result may call the dossier globally complete, authoritative, official or legally sufficient.

3. **Semantic identity and transported-byte integrity remain separate.**
   - Every member records a SHA-256 digest of the exact bytes carried by the export.
   - A recognized canonical JSON artifact also records its derived Motus artifact fingerprint.
   - The verifier recomputes both where both apply and reports which boundary failed.
   - Re-serializing semantically equivalent JSON changes the transported-byte digest even when its canonical artifact fingerprint remains unchanged.
   - Paths, timestamps, labels and media types never substitute for either digest.

4. **The v1 member vocabulary is closed and Motus-owned.**
   - Version 1 may carry only artifact kinds that the accepted Motus contracts through ADR-041 can identify and verify, plus the exact Regulatory Evidence Profile.
   - The dossier manifest occupies one fixed archive path and is not listed as its own member; this avoids a self-referential digest.
   - A self-contained export carries the exact profile whose fingerprint the manifest names.
   - Opaque third-party attachments, legal opinions, full prompts, model outputs, personal dossiers, certificates and regulator forms are outside the canonical v1 export.
   - Adding a new Motus evidence kind requires an accepted ADR and an explicit dossier-kind mapping; unknown kinds fail closed.
   - A consumer may distribute companion files separately, but Motus does not label them verified dossier evidence.

5. **The export preserves member bytes and has one deterministic representation.**
   - The packer never normalizes, redacts, repairs or re-serializes supplied member bytes.
   - Member ordering, archive paths, metadata, permissions, timestamps and compression settings are fixed by the v1 export contract so identical inputs produce identical archive bytes.
   - The exact archive has its own SHA-256 transport digest; that digest is not the dossier fingerprint.
   - The implementation uses the standard library and adds no runtime dependency.

6. **Archive safety is part of verification, before extraction.**
   - Duplicate names, unsafe paths, absolute paths, traversal, links, undeclared members and missing declared members are refused.
   - Compressed size, uncompressed size, member count, path length and nesting are bounded before materialization.
   - Exact limits are contract work after acceptance and must be derived from the existing Motus artifact corpus plus an explicit operating margin; they must not be selected to accommodate a failing implementation.
   - Verification does not require writing member bytes to the filesystem.

7. **Verification composes existing Motus authorities.**
   - Structural dossier validation, manifest fingerprinting, archive integrity, member dispatch and profile assessment are separate result dimensions.
   - Recognized members are passed to their existing strict structural, semantic, lineage and binding verifiers where the required supplied context is present.
   - The dossier layer must not reimplement a weaker validator or upgrade `not_verified` to `matched`.
   - Invalid, mismatched, absent, duplicated and unsupported material remain distinguishable findings tied to exact entries.

8. **Profile assessment remains evidence-state assessment.**
   - The verifier assesses the exact profile carried by the dossier against the exact supplied members.
   - It may reproduce ADR-038 evidence states and its narrowly named `evidence_complete` aggregate.
   - It may not emit `compliant`, `conformant`, `certified`, `approved`, `audit-ready`, `submission-ready`, `passed`, `safe` or synonyms.
   - A bad or incomplete profile can yield internally consistent results without becoming correct about law or the world.

9. **Corrections are append-only and forks remain visible.**
   - A corrected dossier manifest receives a new fingerprint and may name one immediate predecessor through `supersedes`.
   - It preserves producer namespace and stable `dossier_id`.
   - Self-reference, cycles, skipped predecessors and cross-dossier supersession are invalid; forks are conflicts.
   - Time, archive order and last-write-wins never select an authoritative dossier revision.

10. **Omission and redaction cannot be hidden as preservation.**
    - The packer does not redact a recognized artifact in place because that would create different bytes and potentially a different semantic identity.
    - If required evidence is not supplied, profile assessment reports it as missing or not verified under existing semantics.
    - A producer may create a separate valid artifact under its governing contract, but Motus does not claim it is equivalent to an omitted original.
    - Secrets and personal data are not required merely to make a dossier structurally valid.

11. **The builder and verifier are local, deterministic and read-only with respect to evidence.**
    - Inputs are explicit manifest data and caller-supplied member bytes.
    - No network call, database lookup, clock read, mutable cache, filesystem discovery, LLM, regulator service or hidden policy state is required.
    - The builder writes only the requested export output; it does not discover, fetch, mutate, submit or publish evidence.
    - A deployment may index exports for retrieval, but the index is not canonical truth.

12. **An export is not a filing, report or approval.**
    - Archive creation proves only that specified bytes were assembled under this contract.
    - A signature, anchor or attestation over the export retains the assurance meaning defined by its own contract and does not establish authority or legal acceptance.
    - Motus does not choose recipients, submission channels, access rules, retention periods or disclosure policy.
    - Rendering PDF, HTML, spreadsheets, dashboards and regulator-specific forms remains a consumer concern.

13. **Stop conditions are explicit.**
    Implementation stops and requires a later founder-accepted ADR if it would need to:
    - change an existing evidence or evidence-package wire format;
    - infer global completeness, legal sufficiency, official submission, acceptance or compliance;
    - introduce arbitrary selection predicates, legal rules or a policy DSL;
    - fetch evidence from a network, database, registry or hidden filesystem scope;
    - rewrite, redact or repair member bytes while preserving their claimed identity;
    - accept unknown member kinds as verified Motus evidence;
    - add encryption, key management, recipient authorization or regulator delivery to the neutral core;
    - require a runtime dependency, mutable service or UI for deterministic verification.

14. **The first implementation is additive and contract-first.**
    - It may add one dossier-manifest schema, strict semantic validation, canonical fingerprinting, deterministic pack/verify helpers, neutral fixtures, focused tests and a permanent mutation probe.
    - It may add validator dispatch for the new manifest, but not the broad verification/query CLI reserved for roadmap point 10 / v0.22.0.
    - It does not add a database, HTTP or MCP endpoint, UI, workflow engine, legal template library, PDF renderer, signing service, encryption system or publication action.
    - Frozen `tests/contract/` and `tests/compat/` remain untouched.
    - Independent verification, adversarial closure, relative characterization, real-workload evidence where required, and green Jenkins remain release gates for v0.21.0.

## Initial conceptual shape

The exact field-by-field schema is implementation work after acceptance. The decision constrains the dossier manifest toward:

- `schema_version`;
- producer namespace and stable `dossier_id`;
- one producer-declared subject reference and observation time;
- exact `profile_fingerprint`;
- an optional immediate `supersedes` dossier fingerprint;
- a bounded entries list whose members carry a stable entry identifier, closed artifact kind, safe archive path, media type, exact-byte SHA-256 digest and, where applicable, canonical Motus artifact fingerprint.

The export contains the canonical dossier manifest at one fixed path and each declared member exactly once at its declared path. Deterministic verification returns dossier identity, export digest, manifest integrity, per-entry identity and verification findings, correction-lineage findings, and the ADR-038 assessment for the supplied profile. Reporting order follows manifest order with canonical fingerprint tie-breaking only where a stable diagnostic order is needed; archive ingestion order is never evidence.

## Consequences

**A stranger can verify one portable review set without trusting the exporter.** Exact bytes, semantic identities and existing artifact verifiers remain independently checkable.

**The dossier does not become a second evidence package format.** The execution evidence package can be carried and verified as one member without changing its wire contract.

**Equivalent JSON can have two intentionally different identities.** Canonical artifact identity answers “same Motus document”; raw-byte digest answers “same transported bytes”. Consumers must retain both instead of pretending one replaces the other.

**The cost is a stricter, less convenient export.** Unknown attachments, silent redaction, mutable folders and arbitrary reports are refused. Product layers must manage companion documents and presentation separately.

**Completeness remains bounded and defeasible.** A dossier can be complete against one supplied profile while omitting facts that the profile author did not request or that the producer did not supply.

**Deterministic archives require explicit metadata rules and resource limits.** That work is accepted so archive bytes do not vary by operating system, clock or tool defaults, and so verification does not become an extraction vulnerability.

## Alternatives rejected

### Extend the existing execution evidence package

Rejected. Its contract is execution-scoped. Adding every regulatory surface would change old package meaning and couple execution verification to a growing governance bundle.

### Export a PDF or dashboard as the canonical dossier

Rejected. Presentation is not a stable machine-verifiable evidence boundary and cannot preserve independent member semantics.

### Treat a ZIP signature as proof of compliance or official filing

Rejected. A signature can authenticate bytes under its own assurance model; it does not establish authority, applicability, completeness, acceptance or legal sufficiency.

### Allow arbitrary attachments and report them as dossier evidence

Rejected for v1. Raw-byte integrity alone cannot turn an unknown file into Motus-verified evidence. Companion-file support can be reconsidered with explicit semantics and threat evidence.

### Normalize JSON before placing it in the archive

Rejected. It would silently replace producer bytes and collapse transport integrity into canonical semantic identity.

### Fetch all evidence for a subject automatically

Rejected. Discovery depends on hidden storage, authorization and completeness assumptions. The neutral kernel verifies exactly what the caller supplies.

### Add encryption, recipient policy and regulator submission

Rejected. Those are deployment and governance concerns with key, identity and legal-authority semantics the Motus core does not own.
