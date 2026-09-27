# Orbis mapping to Motus adapter profile v1

This mapping records how the already-qualified Orbis backend bridge relates to
ADR-044. It does not claim that the v0.22.0 Orbis routes implement the v1 JSON
adapter envelope byte for byte, and it does not qualify the Orbis browser UI.

Observed revisions and live evidence remain in
`.factory/tasks/done/013-orbis-evidence-bridge-qualification/REPORT.md`.

| Orbis v0.22 bridge route | Adapter-profile semantic operation | Mapping |
|---|---|---|
| `GET /motus/evidence/{tenant}/{writer_id}/{begin_sequence}/receipt` | `receipt.retrieve` | The three path components form the ADR-027 `execution_ref`; the Motus receipt is returned after the Orbis service-token and writer boundary. |
| `POST /motus/evidence/{tenant}/{writer_id}/{begin_sequence}/verify` | `evidence.execute` with embedded ADR-043 `verify` | Orbis retrieves the package for the same BEGIN-located reference, invokes the Motus verification/query interface and preserves its structured result without adding a boolean verdict. |

Orbis does not currently expose profile operations `package.retrieve` or
`package.verify` as public bridge routes. The latter is the lower-level
`EvidenceAPI.verify(execution_ref, package=...)` result (`PackageVerdict`), while
the qualified Orbis verify route returns the ADR-043 verification result.

The observed transport mapping is deliberately host-owned:

- missing `X-Motus-Token` produced HTTP 401 and corresponds to
  `unauthorized`;
- an authorized lookup with the wrong writer produced HTTP 409
  `other_writer` and corresponds to `conflict`;
- a missing BEGIN-located execution produced HTTP 404 and corresponds to
  `not_found`.

HTTP status codes, service-token authentication, writer authorization and route
shapes remain Orbis concerns. They do not alter the Motus receipt, package,
`PackageVerdict` or ADR-043 result, and passing the neutral corpus does not
certify those deployment controls.
