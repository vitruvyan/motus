# TASK 021 — Regulatory Evidence Dossier / export v1

Parent: ADR-042, accepted by the founder on 2026-09-26.

Branch: `feat/regulatory-evidence-dossier-v1`.

Target release: **0.21.0**.

## Goal after acceptance

Add the smallest jurisdiction-neutral mechanism that describes one bounded set of exact Motus artifacts and exports those original bytes in one deterministic, independently verifiable archive.

The dossier is a manifest and transport boundary. It is not a compliance report, official filing, mutable evidence store, discovery service or presentation format.

## Gate

1. ADR-042 was accepted by the founder on 2026-09-26.
2. Contract-first implementation then proceeds in independently verifiable micro-steps:
   - dossier manifest schema and semantic invariants;
   - canonical dossier fingerprint and append-only correction lineage;
   - exact member-byte digests kept separate from semantic artifact fingerprints;
   - deterministic, safe, byte-preserving export packer;
   - read-only export verifier composing existing Motus validators and ADR-038 assessment;
   - neutral fixtures and focused tests;
   - permanent mutation probes;
   - independent verification and adversarial closure;
   - release characterization and green Jenkins evidence.
3. No edits to `tests/contract/` or `tests/compat/`.
4. No new runtime dependencies.
5. No legal, compliance, conformity, certification, approval, filing or completeness verdict.
6. No change to existing evidence or evidence-package wire formats.
7. No arbitrary attachments, hidden discovery, network fetch, database, HTTP/MCP endpoint, UI, PDF renderer, encryption, signing service or regulator submission.
8. Broad verification/query CLI work remains roadmap point 10 / v0.22.0.

## Proposed implementation envelope after ADR acceptance

Expected new surfaces, subject to contract review:

- `contract/regulatory-evidence-dossier.v1.schema.json`;
- strict additions to `contract/validate.py`;
- `src/vitruvyan_motus/regulatory_dossier.py`;
- neutral fixtures in the next free fixture range;
- focused manifest, archive-safety, deterministic-export, lineage, dispatch and profile-composition tests;
- public read-only pack/verify helpers only after the contract is independently verified;
- one permanent mutation-probe definition.

The v1 verifier must report separate structural, archive-integrity, member-identity, member-verification, lineage and profile-assessment dimensions. It must preserve ADR-038's `missing`, `not_verified`, `mismatched` and `matched` meanings and may not upgrade them.

## Stop conditions

Stop and return to ADR review if implementation needs to infer global completeness or legal sufficiency, accept unknown files as verified evidence, rewrite member bytes, discover evidence from hidden state, add a policy language, change an existing artifact contract, or add encryption, recipient authorization, submission workflow or a runtime dependency.

## Definition of done

Point 9 / 0.21.0 mechanism work is complete only when:

1. accepted ADR and contract agree;
2. identical inputs produce byte-identical exports;
3. semantic artifact fingerprints and raw member-byte digests remain distinct and independently checked;
4. archive traversal, links, duplicates, undeclared members, missing members and resource-bound violations fail closed before extraction;
5. existing Motus validators remain authoritative for every recognized member;
6. dossier correction forks and incomplete lineage remain visible;
7. profile assessment is scoped to the exact included profile and members;
8. no compliance, official-submission or global-completeness verdict can be emitted;
9. focused and full tests, packaging and frozen-path guards pass;
10. mutation probes kill every targeted invariant;
11. independent and adversarial review have no unresolved verified finding;
12. Jenkins is green on the exact reviewed head and on `main` after merge;
13. release `v0.21.0` is characterized and closed under the repository release discipline.
