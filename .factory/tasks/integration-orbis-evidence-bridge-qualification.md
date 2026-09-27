# Orbis ↔ Motus receipt/evidence bridge qualification

Date: 2026-09-27.

Scope: backend bridge only. This evidence does not qualify the authenticated
browser flow or claim that the Orbis or Motus UI showed these results.

## Exact revisions observed

- Motus source tag loaded by the running Orbis graph container: `v0.22.0`.
- Orbis `main`: `7d32a5e8d685e2e05320208721b46559401d3138`.
- Orbis bridge merge: `08996314ab103f5a8d3cb204c30f548d8ac00660`
  (PR #249; feature commit
  `997f66feb0a930f6fdca5ebd7efc651dde8d74ff`).
- Running route files matched the checkout bytes:
  - `services/api_graph/adapters/motus_evidence.py`:
    `sha256:063bbddfdd8e0c45d0d526f001c429c7192ee777877cf3b558d77b2dcf1d7e81`;
  - `services/api_graph/api/routes_evidence.py`:
    `sha256:7f5d8f21635969ada64e79e3c05d1f4a8bb53d9e88b48a2ffd7680ca607e4f73`.

## Live probe

The probe ran inside the deployed `orbis_graph` container against its loopback
listener. It read the configured service token from the container environment,
used it only as the `X-Motus-Token` request header, and never printed or stored
the token.

Execution reference: `orbis/api_graph/116`. This is the BEGIN sequence for
run `c92595d1c5944e3fb547d63007546b08`; its paired END is sequence 117. The
distinction is material: ADR-027 locates an execution by BEGIN. A preliminary
request using the END sequence correctly returned `404 not_found` and was not
accepted as positive qualification evidence.

| Probe | Observed |
|---|---|
| `GET /motus/evidence/orbis/api_graph/116/receipt` | HTTP 200; `execution.ref` exactly `orbis/api_graph/116` |
| `POST /motus/evidence/orbis/api_graph/116/verify` | HTTP 200; ADR-043 result envelope preserved |
| receipt without `X-Motus-Token` | HTTP 401 |
| receipt for `orbis/not-api-graph/116` | HTTP 409; `error: other_writer` |

The verification result reported:

- `operation: verify`;
- subject kind `execution_evidence_package`;
- scope kind `supplied_inputs`, `global_complete: false`;
- `INTEGRITY: matched`;
- `EXISTENCE`, `RETENTION`, `EXECUTION_CONTINUITY`, `PROVENANCE`, `IDENTITY`
  and `LEGAL_TIME`: `not_verified`;
- overall `outcome: not_verified`;
- no synthetic top-level `verified` boolean.

The result is therefore evidence that the stored trace chain recomputes to the
committed END root and that the proof reaches the sealed window. It is not
evidence of publication, durable retention, prior acknowledgement, signature,
legal identity, qualified time, compliance, or global completeness.

## Read-only evidence

Before and after all four requests, the probe built a manifest over every file
under the configured commitment and trace directories, including relative
path, byte length, modification time and SHA-256 digest.

- files observed: 371;
- manifest before:
  `sha256:d6fd98ed9ce06b1359b29c14e5164a70f431ac367e23217a65f599d66fda5889`;
- manifest after:
  `sha256:d6fd98ed9ce06b1359b29c14e5164a70f431ac367e23217a65f599d66fda5889`.

The receipt and verification operations made no observed change to the stored
commitment or trace artifacts.

## Qualification boundary

This closes the Motus roadmap item “Orbis ↔ Motus receipt/evidence bridge” for
the deployed backend and current v0.22.0 contract. It deliberately leaves these
items open:

1. authenticated browser/UI qualification in Orbis;
2. Motus UI regulatory wiring;
3. the Limen bridge;
4. SDK/adapters for third-party stacks;
5. any external assurance layer required for the six `not_verified` findings.
