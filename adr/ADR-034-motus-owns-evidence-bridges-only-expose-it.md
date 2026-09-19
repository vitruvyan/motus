# ADR-034 — Motus owns evidence; bridges only expose it

- **Status:** PROPOSED
- **Date:** 2026-09-19
- **Authority:** CTO proposes; the founder accepts.
- **Depends on:** ADR-020 (trust model), ADR-021 (witness/anchor), ADR-027 (execution identity), ADR-032/033 (distribution boundaries).
- **Amends:** no wire contract yet. This ADR adds a public integration boundary for evidence consumers without changing trace, commitment, checkpoint, receipt, or evidence-package formats.

## Context

Motus already produces and verifies the core evidence primitives needed by an AI execution: trace, graph binding, execution fingerprint, BEGIN/END commitments, checkpoints, receipts, attestations/anchors, and evidence packages. `vitruvyan_motus.evidence.pack` and `verify_package` are public; a sealed `CommitmentLog` can already return a receipt for an `execution_ref`.

The product family now has a clear boundary:

- **Motus** is the auditable execution runtime and evidence authority.
- **Limen** is the AI gateway and access/routing/policy plane.
- **Orbis** is the cognitive operating system and uses Motus as its native orchestrator.

Orbis, Limen, or a third-party application may need to display Motus evidence. That display requirement must not move ownership of evidence into those products. A consumer-specific bridge may translate Motus evidence into a UI or transport shape, but the underlying receipt and verification result remain Motus artifacts.

The current public surface has the pieces but no single boundary that a bridge can depend on. If each consumer reaches directly into `CommitmentLog`, `TraceBundle`, package internals, and storage layout, every integration will invent a different meaning for "evidence" and Motus will lose the product boundary this architecture is intended to create.

## Decision

1. **Motus is the evidence authority.** A bridge may retrieve and render Motus evidence, but must not construct, repair, reinterpret, upgrade, or certify it.
2. **The canonical Evidence API is transport-neutral.** The Motus kernel exposes a Python public API. HTTP, MCP, WebSocket, GraphQL, UI-specific JSON, and product-specific bridges are adapters over that API, not part of its semantics.
3. **Every execution lookup is keyed by `execution_ref`.** ADR-027 remains authoritative. `run_id` may be returned as correlation metadata but is never the primary key.
4. **The API exposes existing artifacts; it invents no new proof.** The first surface is limited to receipt retrieval, evidence-package retrieval/building where source artifacts are available, independent package verification, and execution identity exactly as carried by the receipt/package.
5. **Read-only means read-only.** Retrieval and verification never append commitments, seal windows, rewrite traces, regenerate a missing artifact, or fix a failed verification.
6. **No product vocabulary enters Motus.** No Orbis, Limen, vertical, customer, UI, AI-provider, or domain-specific type becomes part of the Evidence API.
7. **Storage is separate behind the boundary.** The API must not make a filesystem layout, worker process, database, or object store part of the public semantic contract.
8. **Verification is explicit.** Retrieving a receipt or package is not verification; a verification result exists only after the shipped verifier runs on the actual artifact.
9. **The UI is outside the trust boundary.** Where a UI says "verified", it must be displaying a Motus verification result, not inferring one from `authority`, a fingerprint, or receipt presence.
10. **Regulatory extensions attach later.** System manifests, risk/control evidence, human-oversight receipts, incident/CAPA evidence, retention/legal-hold metadata, and regulatory dossiers are later Motus evidence types.

## Consequences

**The good boundary costs an adapter.** Orbis cannot simply read Motus private files and call that integration complete; it needs a bridge against the public Evidence API. The same is true for a third-party stack.

**Motus stays embeddable.** No HTTP server is added to the kernel and the default dependency surface does not grow.

**A storage abstraction becomes necessary.** Today the writer-facing `CommitmentLog` and trace sink are sufficient to produce evidence, but a consumer-facing Evidence API must also support retrieval without assuming that the caller owns the live writer object.

**No compliance claim follows automatically.** The API makes technical evidence retrievable and verifiable. Whether an organization meets a legal or management-system requirement remains a separate assessment.

## Alternatives rejected

**Put the receipt bridge in Orbis.** That solves one UI and makes Orbis the de facto owner of Motus evidence.

**Make Motus an HTTP service.** Useful for some deployments, but it changes an embeddable runtime into a service. A network adapter can be built later.

**Use `run_id` because UIs already have it.** Rejected by ADR-027: it may repeat and is not the execution key.

**Let consumers read trace/receipt files directly.** That exposes storage layout as public API and bypasses verifier discipline.

**Return a boolean `verified`.** Too weak: transport, trace, receipt and trust-model outcomes are deliberately distinct.