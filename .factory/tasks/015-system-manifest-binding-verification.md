# TASK 015 — System Manifest binding verification

Branch: `feat/system-manifest-v1`.

Status: implementation work after ADR-035 acceptance and green System Manifest contract/schema gate.

Read first: `AGENTS.md`, ADR-035, `contract/system-manifest.v1.schema.json`, `contract/README.md`, `src/vitruvyan_motus/graph.py`, `src/vitruvyan_motus/trace.py`, and `src/vitruvyan_motus/__init__.py`.

## Goal

Add an explicit public binding-verification operation that compares a schema-valid System Manifest against the Motus distribution and supplied authoritative Motus artifacts without turning declaration validity into proof.

## Required behavior

1. Add a public `verify_system_manifest_bindings(...)` surface plus typed findings/verdict.
2. The verifier validates the manifest first. Invalid manifests never receive binding findings that look successful.
3. Runtime bindings compare against the Motus distribution actually executing the verifier:
   - `bindings.motus.runtime_version` vs `vitruvyan_motus.__version__`;
   - `bindings.motus.trace_schema_version` vs `TRACE_SCHEMA_VERSION`.
4. Graph bindings compare against supplied validated `GraphSpec` objects:
   - `name`, `version`, `spec_schema_version`;
   - recomputed `graph_fingerprint` from the GraphSpec's canonical source.
5. `code_fingerprint` is compared only against supplied valid Motus traces whose graph identity matches the same manifest graph. The result must say explicitly that this establishes agreement with trace-carried execution evidence; it does not independently recompute node code identity.
6. Missing GraphSpec or trace evidence produces an explicit `not verified` finding. It is never silently treated as a match.
7. A mismatch is explicit and makes the overall binding verdict incomplete.
8. Do not mutate the manifest, GraphSpec, or Trace.
9. No network, storage, UI, HTTP, MCP, Orbis or Limen work.
10. No new runtime dependencies. Importing `vitruvyan_motus` alone must still pull no third-party module; any contract-validator import must be lazy.

## Status vocabulary

Use exactly:
- `matched`
- `mismatched`
- `not verified`

The overall verdict may expose convenience properties, but must not call a valid manifest "compliant", "certified", or equivalent.

## Acceptance

- top-level public exports are documented in README;
- tests cover exact match, runtime mismatch, missing GraphSpec, graph mismatch, missing trace, code-fingerprint mismatch, invalid manifest, multiple graphs, input immutability;
- mutation proof: neutering each comparison causes a dedicated test to fail;
- full Jenkins suite green;
- adversarial review before merge.
