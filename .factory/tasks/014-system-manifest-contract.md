# TASK 014 — System Manifest contract, regulatory-roadmap point 2

Branch: `feat/system-manifest-v1`.

Status: contract work after founder acceptance of ADR-035.

Read first: `AGENTS.md`, ADR-001, ADR-020, ADR-027, ADR-034, accepted ADR-035, `contract/README.md`, `contract/graphspec.v1.schema.json`, `contract/trace.v1.schema.json`, and `contract/validate.py`.

## Goal

Define the first versioned System Manifest contract without implementing storage, registries, HTTP, consumer bridges, or runtime integration.

The manifest is a system-scoped declaration with independently verifiable bindings. It is never proof that a declared control executed and never a compliance verdict.

## Scope

1. Add `contract/system-manifest.v1.schema.json`.
2. Add contract-side validation for schema plus only the semantic invariants the document itself can prove.
3. Define the derived `manifest_fingerprint` recipe over canonical JSON; do not store the fingerprint inside the manifest.
4. Extend the standalone contract CLI with a `system-manifest` artifact validator.
5. Extend non-frozen conformance tests and contract fixtures for the new surface.
6. Update `contract/README.md` to state the boundary between document validation and future binding verification.

## Initial semantic rules

- **SM1** — identifiers that must be unique inside one manifest are unique: graph identity `(name, version)`, `component_id`, `policy_id`, and `control_id`.
- **SM2** — when a control names `policy_ref`, that id exists in this manifest's declared policies.
- **SM3** — `created_at` is a calendar-valid RFC 3339 UTC instant, not merely text matching the timestamp shape.

Everything else that the schema can express remains `SCHEMA`, not a duplicate semantic rule.

## Explicitly out of scope

- verifying manifest bindings against a runtime, GraphSpec, trace, receipt, or evidence package;
- System Manifest storage or registry;
- public runtime API;
- AI System Registry;
- Risk & Control Registry / ControlApplication evidence;
- Human Oversight Receipt;
- Incident/CAPA;
- Retention/Legal Hold;
- regulatory mappings or compliance verdicts;
- Orbis, Limen, UI, HTTP, MCP changes;
- trace, graphspec, commitment, checkpoint, receipt, or evidence-package wire changes.

## Acceptance

- ADR-035 remains the authority and is already founder-accepted.
- Draft 2020-12 metaschema validation passes.
- Positive and isolated-negative fixtures exercise the new surface.
- The manifest fingerprint is deterministic over canonical JSON and is not a field in the document.
- No existing frozen path is touched.
- Full Jenkins contract suite remains green before implementation work starts.
