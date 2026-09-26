# ADR-043 — Verification and query are explicit read-only views over supplied evidence

- **Status:** ACCEPTED
- **Date:** 2026-09-26
- **Accepted:** 2026-09-26, by the founder.
- **Authority:** CTO proposes; the founder accepts.
- **Depends on:** ADR-001, ADR-020, ADR-027, ADR-034 through ADR-042.
- **Amends on acceptance:** permits one transport-neutral public verification/query facade and one CLI over existing Motus contracts. It does not amend any existing artifact, trace, evidence-package, or dossier wire format.

## Context

Motus now owns a canonical Evidence API and neutral contracts for execution
evidence, System Manifests, risk/control declarations, ControlApplication,
HumanOversightReceipt, Regulatory Evidence Profiles, incident/CAPA records,
retention/legal-hold records, AI System Registry records, and Regulatory
Evidence Dossiers.

Each surface has its own structural validator, fingerprint rules, and — where
applicable — binding, lineage, projection, assessment, package, or export
verifier. Those narrow functions are the semantic authority. Consumers should
not need to know their module layout or reproduce dispatch rules, but a generic
interface must not flatten their deliberately different results into one
boolean.

The existing `motus-validate` command is a structural and narrowly scoped
semantic validator. The Evidence API is execution-ref oriented. Neither is a
stable, uniform boundary through which a bridge, operator, or UI can explicitly
ask which supported artifact it holds, obtain its exact identity, run the
appropriate existing verifier with explicitly supplied companions, or inspect
a bounded supplied collection.

The required invariant is:

**Verification reports only what Motus can establish from exact declared input;
query returns only deterministic views over an exact supplied set. Neither may
discover hidden evidence, select global current state, or issue a compliance
verdict.**

## Decision

1. **Version 1 adds a transport-neutral Python facade and a separate CLI.**
   - Both expose the same closed operations and result semantics.
   - The facade is the semantic boundary; the CLI is an adapter over it.
   - HTTP, MCP, UI, storage, database, and product-specific endpoints remain
     outside this release.

2. **Artifact kind and operation are explicit.**
   - A caller declares the supported artifact kind it supplies.
   - Motus does not infer kind from filenames, field coincidences, archive
     contents, media sniffing, or a best-effort parse.
   - Unsupported kinds and invalid operation/kind combinations fail closed.

3. **The v1 operation vocabulary is closed and deliberately small.**
   - `inspect` validates one declared artifact and, when valid, derives its
     canonical identity and bounded metadata.
   - `verify` composes the existing authoritative verifier for one declared
     artifact with only the explicitly supplied companion material.
   - `query` produces deterministic projections over an exact bounded
     collection supplied by the caller.
   - Adding an operation or changing its semantics requires contract review;
     v1 has no expression language, arbitrary predicates, scripts, plugins, or
     user-defined code.

4. **Existing validators and domain verifiers remain authoritative.**
   - The facade dispatches to them; it does not reimplement weaker variants.
   - Structural validation precedes identity derivation and composition.
   - A generic layer cannot upgrade a domain verifier's `missing`,
     `not_verified`, `mismatched`, `conflict`, damaged, incomplete, or analogous
     outcome to success.
   - Existing evidence-package and dossier transport verification remain
     distinct from semantic member verification.

5. **Input scope is exact, visible, and caller-owned.**
   - Every result identifies the operation, declared kind, exact input
     identity when derivable, companions used, and supplied-set scope.
   - Missing companion material remains missing or not verified; it is never
     fetched or reconstructed.
   - Duplicate identities, ambiguous candidates, competing revisions, forks,
     and incomplete lineage remain visible and are never resolved by input
     order.

6. **Query is projection, not retrieval or discovery.**
   - It may filter, relate, or project only validated artifacts present in the
     supplied collection using fields and relationships defined by their
     existing contracts.
   - It does not search a filesystem, object store, database, network service,
     running writer, or environment.
   - It cannot claim inventory completeness, latest/current global state,
     applicability, or absence beyond the supplied set.

7. **Results use a stable machine-readable envelope without erasing domain detail.**
   - The envelope distinguishes usage/input errors, structural violations,
     derived identity, verification findings, query matches, ambiguity, and
     scope limitations.
   - Domain-specific findings are preserved in deterministic structured form.
   - Human-readable CLI output is a rendering of that result; JSON output is
     available for bridges and automation.
   - No field is named or described as `compliant`, `certified`, `approved`,
     `safe`, `legally_sufficient`, or an equivalent legal or organizational
     verdict.

8. **The interface is deterministic, read-only, local, and bounded.**
   - The same exact inputs and operation yield the same result.
   - Verification and query do not append, amend, seal, repair, normalize,
     migrate, regenerate, publish, or delete evidence.
   - No clock read, network call, database lookup, LLM call, hidden mutable
     state, or external policy service is part of core semantics.
   - The contract defines resource bounds for document size, collection size,
     nesting, and result cardinality; exceeding a bound fails closed.

9. **Existing public interfaces remain compatible.**
   - `motus-validate`, the execution-oriented Evidence API, and existing
     domain helpers retain their established behavior.
   - The new CLI receives a distinct entry point rather than silently changing
     the grammar or output contract of `motus-validate`.
   - Existing artifact and archive identities remain unchanged.

10. **Version 1 does not add a new evidence artifact merely to describe a query.**
    - Requests and results are ephemeral API/CLI protocol values, not producer
      claims, evidence, attestations, receipts, or dossier members.
    - Their schema and compatibility rules may be specified as an interface
      contract, but no result fingerprint implies evidentiary authority.

11. **The first implementation is intentionally narrow.**
    - One public facade, one distinct CLI entry point, one closed kind/operation
      registry, stable JSON results, focused fixtures/tests, mutation proof,
      independent verification, adversarial review, and release
      characterization.
    - No new runtime dependency and no edits to frozen contract or
      compatibility tests.

## Initial conceptual shape

Exact field names are contract-first implementation work after acceptance. The
interface is constrained toward an explicit request containing:

- operation;
- artifact kind and exact artifact bytes/document;
- zero or more explicitly typed companion artifacts for `verify`; or
- an exact bounded typed collection plus one closed projection for `query`.

The result is constrained toward:

- interface version, operation, and declared kind;
- deterministic outcome category and structural violations;
- canonical artifact identity where validation permits deriving one;
- domain verifier findings without semantic translation;
- exact supplied scope, ambiguity/conflict information, and limitations;
- deterministic matches/projections for query operations.

The closed v1 query projections should prove cross-artifact usefulness without
becoming a general query language. The contract review must select the minimum
set from relationships Motus already defines, such as exact identity lookup,
execution-reference association, risk/control relationships, correction or
supersession lineage, registry subset projection, and dossier membership.

## Consequences

**Consumers gain one dependable boundary.** A bridge or UI can call a stable
Motus interface instead of importing validator internals or recreating evidence
semantics.

**Callers must be explicit.** They must know the artifact kind and supply the
documents needed to verify a binding. This is intentional: convenience cannot
come from guessing or hidden discovery.

**Results remain richer than a boolean.** Integrators must preserve distinct
transport, structure, identity, binding, lineage, conflict, and evidence-state
findings.

**Query scope remains bounded.** A useful projection over supplied evidence
cannot answer whether other evidence exists elsewhere or which revision is
globally current.

**Transports still require adapters.** A service or MCP surface may later wrap
the facade, but authorization, tenancy, retrieval, pagination, and network
security are separate decisions.

## Alternatives rejected

### Extend `motus-validate` until it becomes the new interface

Rejected. Its positional grammar and text/exit-code behavior are already a
public compatibility surface. A new automation contract should not silently
change it.

### Return one generic `verified: true|false`

Rejected. It collapses distinctions that ADR-020 and the regulatory contracts
make load-bearing and encourages consumers to display stronger claims than the
evidence supports.

### Auto-detect artifact kind and required companions

Rejected. Similar document shapes, missing context, and ambiguous candidates
would make behavior heuristic and could silently select the wrong authority.

### Make query read Motus storage directly

Rejected. It would expose deployment layout as semantic API, conflate retrieval
with verification, and create unjustified completeness/current-state claims.

### Add SQL, JSONPath, GraphQL, or a policy DSL

Rejected. Version 1 needs portable evidence projections, not an extensible
execution language or a new authorization surface.

### Put the interface in Orbis or the UI

Rejected. Presentation and transport belong in integrations, but evidence
verification semantics remain owned by Motus and must be portable to third-party
consumers.
