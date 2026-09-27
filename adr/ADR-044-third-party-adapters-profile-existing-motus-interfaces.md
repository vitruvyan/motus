# ADR-044 — Third-party adapters profile existing Motus interfaces; they do not create a second verifier

- **Status:** ACCEPTED
- **Date:** 2026-09-27
- **Accepted:** 2026-09-27, by the founder.
- **Authority:** CTO proposes; the founder accepts.
- **Depends on:** ADR-020, ADR-027, ADR-032 through ADR-034, ADR-043.
- **Amends on acceptance:** permits one versioned, transport-neutral adapter profile and conformance kit over the existing Evidence API and verification/query interface. It does not amend any evidence artifact, receipt, package, dossier, verification-result, or query-result format.

## Context

Motus already ships the two semantic boundaries an adapter needs:

1. `EvidenceAPI` retrieves an execution receipt or evidence package by the
   ADR-027 `execution_ref` and verifies exact package bytes; and
2. the ADR-043 interface accepts an explicit artifact kind, operation and exact
   supplied material, then returns a structured inspect, verify, or query
   result without discovering hidden evidence.

The Python distribution is therefore already the Python SDK. The missing
roadmap item is not another set of validators. It is a precise integration
profile that tells a non-Motus host which existing calls it may expose, which
bytes and result envelopes it must preserve, and how another implementation
can prove it did not strengthen or erase Motus semantics.

The first deployed reference is Orbis PR #249. Its bridge needed two read-only
routes and one storage-aware `EvidenceSource`; it did not need a new evidence
kind. Live qualification on 2026-09-27 exercised four requests against Motus
0.22.0: receipt retrieval and verification returned 200, missing credentials
returned 401, and a different writer returned 409. `INTEGRITY` was `matched`
while six unsupplied assurance properties and the overall outcome remained
`not_verified`. A manifest over 371 commitment and trace files was identical
before and after the requests.

That implementation also shows what cannot be standardized inside the Motus
kernel. The host knows its storage layout, tenant authorization, credentials,
rate limits, network framework, deployment topology and error observability.
Motus knows the execution identity, artifact bytes, validators and findings.
An “SDK” that mixes those responsibilities would either import one product's
deployment into Motus or let each client silently reinterpret results.

The required invariant is:

**An adapter may locate, authorize and transport exact Motus inputs and outputs;
only Motus derives evidence identity or verification meaning.**

## Decision

1. **The shipped Python package is the Python SDK.**
   - `EvidenceAPI`, `EvidenceSource`, `LiveEvidenceSource` and the ADR-043
     facade remain the authoritative embeddable surface.
   - No second package copies their validators, result classes, dispatch table
     or artifact vocabulary.
   - The adapter milestone may add integration helpers only where they preserve
     those public calls exactly and add no competing semantics.

2. **Version 1 adds a transport-neutral adapter profile, not a server.**
   - The profile specifies the allowed operation names, required inputs,
     returned media types, Motus result envelopes and failure categories.
   - It does not require HTTP, MCP, gRPC, GraphQL, WebSocket, a particular URL
     layout, or a particular language.
   - A host may map the profile to its own transport, but the mapping is outside
     Motus evidence semantics.

3. **The profiled operations are closed.**
   - retrieve one receipt by canonical `execution_ref`;
   - retrieve one evidence package by the same reference;
   - verify exact evidence-package bytes for that reference through
     `EvidenceAPI.verify`;
   - execute one exact ADR-043 inspect, verify, or query request.
   - Adding another operation requires profile review; a host-specific search,
     “latest” lookup, mutation, workflow action or policy decision is not an
     extension of these operations.

4. **Retrieval and verification remain different outcomes.**
   - Authorization failure, missing storage, another writer, unreadable bytes,
     malformed request and resource exhaustion are adapter or retrieval
     failures.
   - A Motus verifier result such as `not_verified`, `mismatched`, damaged,
     incomplete or conflict means the verifier ran. The adapter returns that
     result as data and must not rewrite it as a transport failure.
   - Receipt or package presence is never emitted as a successful verification.

5. **Every execution operation uses `execution_ref`.**
   - The canonical locator is `tenant/writer_id/BEGIN-sequence` under ADR-027.
   - `run_id`, END sequence, trace root, filename, UI route, database key and
     provider request ID may be correlation metadata but never substitute for
     that locator.
   - A host must not guess the BEGIN sequence from an ambiguous record set.

6. **The host owns authorization, tenancy and retrieval.**
   - It resolves the authenticated principal and checks access before exposing
     stored evidence.
   - It implements `EvidenceSource` or the equivalent storage boundary for its
     language and deployment.
   - The profile does not define users, roles, bearer tokens, sessions, tenant
     membership, object-store credentials, database schema or key management.
   - An adapter must fail closed when those checks cannot be made.

7. **Exact Motus values cross the boundary without semantic translation.**
   - Receipt JSON remains the contract-valid Motus receipt.
   - Package transport preserves the exact package bytes.
   - ADR-043 request/result values preserve interface version, operation,
     subject, scope, findings, violations, matches and records.
   - A host may add a namespaced transport envelope for correlation or tracing,
     but it may not rename, remove, coalesce or strengthen Motus fields.
   - In particular, no adapter-level `verified: true`, `compliant`, `approved`,
     `safe` or equivalent verdict is permitted.

8. **The adapter profile has explicit version negotiation.**
   - Profile version and Motus interface version are distinct fields.
   - Unsupported major versions fail closed before an operation runs.
   - A transport route version, package distribution version or application
     release is not accepted as a substitute for either field.

9. **A conformance kit tests preservation, not business workflow.**
   - It contains neutral requests, exact expected Motus results, package-byte
     preservation cases, execution-ref cases, retrieval/verification
     separation, hostile result-shape cases and resource-bound cases.
   - It runs against an adapter through a small caller-supplied invocation hook;
     Motus does not need to import the host's framework.
   - It tests that the adapter preserves Motus semantics. It does not certify
     deployment security, tenant policy, availability, storage durability,
     legal compliance or UI wording.

10. **Reference adapters are examples, never hidden authorities.**
    - Motus may ship a standard-library-only in-process example and mappings
      for the already qualified Orbis bridge.
    - Framework-specific examples belong in their integration repository or
      optional example area and are not runtime dependencies.
    - Limen and future stacks use the same profile and conformance cases rather
      than importing Orbis code.

11. **The first implementation remains bounded.**
    - one normative adapter-profile document;
    - one versioned set of neutral conformance fixtures;
    - one standard-library conformance runner or helper;
    - one in-process reference example;
    - focused tests, packaging checks, mutation proof, independent review,
      adversarial closure and Jenkins evidence;
    - no new runtime dependency and no edits to frozen contract or
      compatibility corpora.

## Consequences

**There is no new validator to learn.** Python integrations import Motus;
non-Python or network integrations preserve the existing JSON and byte
boundaries. A third-party adapter cannot become an alternate evidence authority
by calling itself an SDK.

**Hosts still have real work.** They must implement authentication, tenant
authorization, storage access, rate limits and operational error handling. The
profile makes those responsibilities visible; it does not pretend they are
portable evidence semantics.

**A result can be successful transport and unsuccessful assurance.** Integrators
must carry structured `not_verified`, mismatch, conflict and incomplete results
to their users. This is more work than returning a boolean and is the cost of
not lying about the evidence.

**Non-Python adoption starts with a protocol and tests, not generated clients.**
The first release does not promise JavaScript, Java, Go or Rust packages.
Measured demand may justify generated or hand-written clients later, but only
after the profile is stable and without moving verification outside Motus.

**The adapter kit is not deployment certification.** Passing conformance says
that the Motus values survived the boundary. It says nothing about whether the
deployment protects credentials, isolates tenants, retains bytes, stays
available or satisfies a law.

## Alternatives rejected

### Build a second `motus-sdk` Python distribution

Rejected. `vitruvyan-motus` already exports the embeddable API. A second package
would either be an empty rename or duplicate versioning and verifier semantics.

### Make the Orbis routes the universal API

Rejected. They are a qualified product bridge with Orbis authentication,
storage and error mapping. Treating that deployment as the standard would make
Orbis the de facto owner of the Motus integration boundary.

### Ship a Motus HTTP service in core

Rejected for version 1. It would add a network framework, authorization and
deployment policy to a zero-dependency embeddable runtime. A separately
deployed service may implement the profile later.

### Generate clients for several languages immediately

Rejected. There is no measured consumer demand or stable network binding from
which to generate them. Generated types would look authoritative while leaving
retrieval, exact bytes and verifier execution unspecified.

### Standardize only success responses

Rejected. The dangerous integration behavior lives in the distinctions among
retrieval failure, verifier refusal, mismatch, incomplete scope and unsupported
assurance. A profile without those cases standardizes the easy half and leaves
the semantic bugs portable.

### Let each adapter simplify results for its UI

Rejected. Presentation may summarize after preserving the full Motus result,
but replacing structured findings with `verified`, `compliant` or a green badge
recreates the second verifier this decision forbids.

## Hypotheses and falsification

**H1 — the existing v0.22.0 interfaces are sufficient for the first adapter
profile.** Falsified if the Orbis conformance mapping or one independently
implemented adapter cannot express a required operation without reading a
Motus private module or reconstructing a finding. In that case the missing
public semantic surface returns to ADR review; the profile does not work around
it.

**H2 — a transport-neutral conformance hook can test preservation across
languages.** Falsified if a real non-Python adapter cannot submit and return
the fixture bytes and JSON without transport-specific interpretation. Then the
profile must separate a smaller byte/JSON corpus from language-specific runner
bindings rather than declaring the runner portable.

**H3 — generated language clients are premature.** Revisit when two independent
non-Python integrations need the same binding and measure repeated code or
incompatible interpretations. Until then the accepted cost is that those hosts
write a thin transport client against the profile.
