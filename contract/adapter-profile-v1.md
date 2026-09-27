# Motus adapter profile v1

Authority: ADR-044. Schema: `adapter-profile.v1.schema.json`. Profile version:
`1.0.0`.

This profile is an ephemeral integration protocol. Its requests and results are
not evidence, attestations, receipts or compliance decisions. It names how a
host transports existing Motus operations without becoming another verifier.

## Responsibilities

Motus owns execution identity, artifact validation, package verification and
the ADR-043 verification/query result. The host owns authentication, tenant
authorization, storage retrieval, rate limits, network security and deployment.
The profile does not standardize any of those host concerns.

An adapter MUST authorize before retrieving evidence. A conformance result does
not prove that it did so correctly. Unexpected implementation exceptions MUST
remain operational failures; they MUST NOT be rewritten as a Motus verification
result.

## Messages

Every message carries `profile_version`, `message_type` and one closed
`operation`. Unknown members and unsupported operations are refused.

Version 1 defines exactly four operations:

| Operation | Request input | Completed result |
|---|---|---|
| `receipt.retrieve` | canonical ADR-027 `execution_ref` | exact contract-valid Motus receipt in `receipt` |
| `package.retrieve` | canonical ADR-027 `execution_ref` | exact package bytes as strict padded base64 in `package_base64` |
| `package.verify` | canonical ADR-027 `execution_ref` plus exact package bytes | lossless JSON projection of Motus `PackageVerdict` in `package_verdict` |
| `evidence.execute` | exact ADR-043 request in `evidence_request` | exact ADR-043 result in `evidence_result` |

The execution locator is `tenant/writer_id/BEGIN-sequence`. A `run_id`, END
sequence, trace root, filename or UI identifier is not an execution reference.

Base64 is a transport encoding only. Decoding it MUST reproduce the supplied
bytes exactly; an adapter MUST NOT unpack, normalize, rebuild or repair a
package while transporting it.

`package_verdict` is a mechanical JSON projection of the public frozen
dataclasses: tuples become arrays, nested `Finding` and `Violation` fields keep
their public names, and no value is interpreted. `verdict: null` remains
distinct from an empty or successful verdict.

## Retrieval failure is not verification

A failed adapter operation has `outcome: failed` and exactly one `failure`:

| Kind | Meaning |
|---|---|
| `invalid_request` | the profile message is malformed or an embedded ADR-043 request/result is invalid |
| `unsupported_version` | the profile or embedded Motus interface major is not supported; no operation ran |
| `unauthorized` | no authenticated principal was established |
| `forbidden` | the established principal is not allowed to access the requested evidence |
| `not_found` | the authorized retrieval boundary found no requested artifact |
| `conflict` | stored material or writer selection cannot coherently satisfy the request |
| `resource_exhausted` | a declared message, byte or host resource bound refused the operation |
| `unavailable` | an expected retrieval dependency was temporarily unavailable |

The adapter MUST NOT use `failed` merely because a Motus verifier returned
`not_verified`, `mismatched`, refused, damaged, incomplete or conflict. In that
case the operation completed and the unchanged Motus result is the payload.

The failure vocabulary is operational and carries no evidence finding. A host
MAY map it to transport-specific status codes, but that mapping is outside this
profile and MUST NOT change the embedded Motus values.

## Bounds and validation

- Profile JSON is limited to 128 levels of nesting.
- An adapter envelope is limited to 224 MiB of canonical JSON so a complete
  160 MiB package can survive base64 expansion without contradicting the
  binary limit.
- A decoded evidence package is limited to 160 MiB, matching ADR-043's binary
  request ceiling.
- Failure detail is limited to 4096 characters and is diagnostic text, never
  evidence.
- Embedded `evidence_request` and `evidence_result` values MUST satisfy the
  ADR-043 `verification-query.v1` contract independently.
- An embedded ADR-043 result MUST preserve the request interface version and
  operation. Its scope input identifiers MUST equal the request inputs; an
  `inspect` or `verify` subject MUST identify the primary request artifact,
  while a `query` result MUST have no subject.
- Validation and conformance execution are read-only and MUST NOT mutate the
  supplied messages, stored receipt, package bytes or Motus result.

## Conformance boundary

`adapter-profile-conformance.v1.json` supplies neutral setup, request and exact
expected-result cases. The shipped runner calls a caller-supplied hook with a
deep copy of each request and setup, validates both sides, checks exact equality
and reports mutation separately.

Negative cases deliberately carry malformed or unsupported requests and a
contract-valid failed result. A hostile upstream-result case instead carries
`expected_hook_error: operational_exception`: returning a profile result for
that case is non-conformant, because an unrelated or malformed Motus result
must not be dressed as successful adapter output.

Passing the corpus establishes only that these values survived the adapter
boundary for the supplied cases. It does not establish authentication quality,
tenant isolation, storage durability, availability, security, retention,
publication, legal identity, legal time or compliance.
