# Task 008 report

## RESULT
Implemented the table-driven schema-bound validation for commitment/receipt dataclasses. Runtime construction now refuses Identifier values over 200 characters, malformed Digest values, and validates all mapped fields (including pending anchor optional values and `WitnessAck.algorithm`). `receipt_for` ad-hoc Identifier checks were removed. The a08 attack now refuses at runtime construction.

The brief's RFC3339 UTC Timestamp claim conflicts with the authoritative current `contract/commitment.v1.schema.json` and ADR-021 context: commitment Timestamp is only non-blank text (`minLength: 1`, `pattern: \\S`). I therefore did **not** add offset rejection. A positive offset-timestamp test and schema-parity assertion document and pin the current contract.

## ARTIFACTS
- `src/vitruvyan_motus/commitments.py`: schema type validators, `_SCHEMA_FIELDS`, `_NON_SCHEMA_FIELDS`, dispatcher, and dataclass routing.
- `src/vitruvyan_motus/commitlog.py`: removed receipt-time ad-hoc checks.
- `tests/test_commitments_schema_fields.py`: classification, schema parity, boundary, digest, pending-anchor, algorithm, and timestamp tests.
- `tests/test_verifier.py`: updated obsolete receipt-time oversized-input expectations to construction-time expectations.

## TESTS
Raw output:
- `.venv/bin/python -m pytest -q` — `1216 passed, 5 skipped, 1 warning`.
- `.venv/bin/python .attack/pr131/a08_huge_height_reference_exceeds_identifier_length.py` — `upgrade() refused the absurd height outright: ValueError`; script reports `NOT REPRODUCED` for the later validator path, as expected because construction now refuses first.
- `git diff --check` — passed.

Construction timing (200,000 iterations, minimum of three repeats): before `1.2537947450036881s`; after `2.305545365001308s`; ratio `1.838x` (+83.8%). This exceeds the requested +10% threshold. Per the task instruction, no micro-optimization was attempted and this is reported rather than hidden.

## FINDINGS
- Resolved: the architect identified that commitment Timestamp is not RFC3339 in the current contract. Followed contract authority; added explicit parity/positive-offset coverage rather than silently strengthening semantics.
- Resolved: the previously unvalidated `WitnessAck.algorithm` and pending-anchor optional fields are covered by the same class-level table.
- Open: construction overhead is above the requested +10% threshold; left unoptimized as instructed.
- Adversary finding 1 (open, no correction due stop condition): `Commitment.kind` is not type-checked; a foreign enum can construct and serialize an END without its required `root`/`outcome`, which the validator refuses.
- Adversary finding 2 (open, no correction due stop condition): `Checkpoint.count` does not reject `bool`; a corrupted checkpoint with `count: true` can reopen and flow through `receipt_for`, while the validator refuses the JSON boolean as an integer.
- Adversary finding 3 (open, no correction due stop condition): `AnchorReceipt.proof` is not checked to be a dict; `proof: 42` constructs and the receipt validator refuses it as not an object.
- Adversary confirmed Identifier/Digest/BundleFingerprint coverage, pending anchors, Unicode length parity, a08 runtime refusal, and the Timestamp contract reconciliation.

## MUTATION TARGETS
- Remove `_IDENTIFIER_MAX_LENGTH` enforcement from `_require_identifier`: the 201-character Identifier and a08 runtime-refusal tests must fail.
- Remove `_require_digest` routing from `_SCHEMA_FIELDS`: malformed Digest construction tests must fail.
- Remove pending-anchor validation through `_validate_schema_fields`: oversized pending reference/timestamp tests must fail.
- Remove one class or field from `_SCHEMA_FIELDS`/`_NON_SCHEMA_FIELDS`: the bidirectional dataclass enumeration test must fail.
- Restore either `receipt_for` ad-hoc loop: the construction-boundary tests and source-scope inspection should expose the obsolete duplicate check. No mutation probe was run because the task stopped at the measured performance threshold.

## OUT OF SCOPE
- No contract or ADR changes; no RFC3339 Timestamp semantics.
- `TenantCheckpoint` remains outside the table because no contract schema exists for it.
- No edits to frozen contract/compat paths, plug code, attack script, SLO gate, dependencies, mutation probe, or release artifacts.
- No commit, push, tag, or release performed.

## NOTICED
- Existing receipt tests assumed oversized values could survive commitment construction and be rejected only by `receipt_for`; they were updated to reflect the requested runtime boundary.
- The a08 script prints both its runtime-refusal line and its historical “NOT REPRODUCED” explanatory line; the refusal branch is the one executed.

## AGENTS
- Lead: motus-lead.
- Architect: Claude, `/tmp/architect-008.md`.
- Implementer: Pi `openai-codex/gpt-5.6-luna`.
- Verifier: Pi `openai-codex/gpt-5.6-luna`, `/tmp/verifier-008.md`.
- Adversary: Claude, runtime-accepted / validator-refused lens, `/tmp/adversary-008.md`.
- No correction round was run: the measured +83.8% construction-cost stop condition was honored.
- No commit, push, tag, release, or optimization after the stop.

## 008b

## RESULT
Fixed all three findings from `/tmp/adversary-008.md`. `Commitment.kind` now requires the actual `CommitmentKind`; `Checkpoint.count` rejects bool at the type boundary; and `AnchorReceipt.proof` requires a dict. The existing checkpoint invariant remains authoritative: although the requested type boundary is non-negative (`>= 0`), the current checkpoint schema requires minimum 1 and the constructor's sealed-window invariant requires at least one commitment, so zero remains refused. Timestamp remains nonblank under the current commitment schema; RFC3339 commitment timestamps require a separate contract ADR.

## ARTIFACTS
- `src/vitruvyan_motus/commitments.py`: corrected the three boundaries, updated classifications, and precomputed per-class schema validator tuples once at import.
- `tests/test_commitments_schema_fields.py`: one regression test for each finding.

## TESTS
Raw output:
- `.venv/bin/python -m pytest -q tests/test_commitments_schema_fields.py tests/test_commitments.py` — `68 passed in 0.77s`
- `.venv/bin/python -m pytest -q` — `1219 passed, 5 skipped, 1 warning in 101.75s (0:01:41)`
- `.venv/bin/python tools/check_frozen_paths.py "$(git merge-base HEAD main)" HEAD` — `Frozen contract paths: PASS`
- `.venv/bin/python .attack/pr131/a08_huge_height_reference_exceeds_identifier_length.py` — `upgrade() refused the absurd height outright: ValueError`; `NOT REPRODUCED` at the later validator path.
- `git diff --check` — passed.
- Timing, same `timeit` method as 008 (200,000 constructions, 3 repeats, minimum): before `1.2537947450036881s`; after `2.148755053000059s`; ratio `1.713x` (`+71.3%`). No optimization beyond the required import-time tuple precompute.

## FINDINGS
- Fixed: foreign `CommitmentKind` values can no longer diverge construction and serialization branches.
- Fixed: `Checkpoint.count=True` can no longer pass arithmetic checks or reopen from corrupted JSON; the non-negative type check is followed by the unchanged `>= 1` sealed-window invariant.
- Fixed: non-object anchor proofs are refused while proof contents remain opaque.
- Reconciled: the user-requested `>=0` type boundary is preserved as `_require_index`; schema minimum 1 and the existing checkpoint invariant still reject zero. No contract/schema weakening was made.

## MUTATION TARGETS
- Remove the `Commitment.kind` `isinstance` guard: the foreign-enum regression test must fail.
- Remove `_require_index(self.count, "checkpoint count")`: the bool boundary regression test must fail.
- Remove the `AnchorReceipt.proof` dict guard: the proof regression test must fail.
- Replace the precomputed `_SCHEMA_VALIDATORS` walk with per-construction table iteration: source review should detect failure to meet the required one-time precompute.

## OUT OF SCOPE
- No contract or ADR changes; no RFC3339 strengthening of Timestamp.
- No changes to frozen paths, `commitlog.py`, SLO gate, dependencies, or a08.
- No `mutation_probe`, commit, push, tag, or release.

## NOTICED
- The full suite's one warning is the existing duplicate-zip-member warning in `tests/test_evidence_package.py`.
- The standalone frozen-check command requires base/head arguments; it passed against `git merge-base HEAD main` and `HEAD`.

## AGENTS
- Lead: current Pi session.
- Source finding: `/tmp/adversary-008.md` (read-only; findings 1–3).
- No additional agent panes were used; no correction-round adversary or mutation probe was run.

### Correction round 1

## FINDINGS
- Fixed B1: null skipping is now explicit through `_NULLABLE_SCHEMA_FIELDS`; all required schema-bound fields reject `None`. The nullable set is exhaustively asserted and public `begin()`/`seal()` paths are covered.
- Fixed B2: failures rebuilding commitments in `_sealed_window` are translated to `CommitmentLogFork`; the historical ambiguous dual-envelope diagnostic remains a direct `ValueError` as required by its existing test.
- Fixed B3: `Commitment.kind` now requires exact `type(...) is CommitmentKind`, closing `Mock` and `__class__` spoofing.
- Fixed B4: `witness` and `continues` now require the exact expected dataclass types before nested validation is claimed.

## TESTS
Raw correction-round output:
- `.venv/bin/python -m pytest -q tests/test_commitments_schema_fields.py tests/test_issue132_window_key.py` — `32 passed in 9.76s`
- Initial `.venv/bin/python -m pytest -q` — `1222 passed, 1 failed, 5 skipped, 1 warning in 102.22s (0:01:42)`; the failure exposed an existing ambiguous-envelope diagnostic expectation, which was preserved.
- Final `.venv/bin/python -m pytest -q` — `1223 passed, 5 skipped, 1 warning in 113.32s (0:01:53)`.
- `.venv/bin/python tools/check_frozen_paths.py "$(git merge-base HEAD main)" HEAD` — `Frozen contract paths: PASS`
- `.venv/bin/python .attack/pr131/a08_huge_height_reference_exceeds_identifier_length.py` — runtime `ValueError` refusal; `NOT REPRODUCED` at validator path.
- `git diff --check` — passed.
- Timing report preserved: before `1.2537947450036881s`, after `2.148755053000059s`, ratio `1.713x` (`+71.3%`); no further optimization.

## MUTATION TARGETS
- Remove the nullable-set membership guard: exhaustive required-`None` tests must fail.
- Remove `_sealed_window` conversion of commitment reconstruction errors: legacy overlong replay must fail with raw `ValueError`, not `CommitmentLogFork`.
- Change exact `type` checks for `kind`, `witness`, or `continues` back to duck-accepting behavior: spoof/double regression tests must fail.
- Remove or bypass the public begin/seal checks: their required-`None` tests must fail.

## AGENTS
- Lead: current Pi session.
- Correction source: `/tmp/adversary-008b.md`.
- Correction implementer: Pi `openai-codex/gpt-5.6-luna`; fixed B1–B4.
- Correction verifier: Pi `openai-codex/gpt-5.6-luna`, `/tmp/verifier-008b-correction.md`.
- Same adversary lens was rerun once in `/tmp/adversary-008b.md`; its B1–B4 findings were processed in correction round 1.
- No further adversary round, optimization, mutation probe, commit, push, tag, or release.

## 008c

## RESULT
Implemented recursive strict JSON validation for `AnchorReceipt.proof` by reusing `_strict_plain_json`. The proof remains required to be an outer `dict`; invalid nested values are refused at construction with their proof path and rule J1. The schema-bound dict-field sweep found only `AnchorReceipt.proof`, which is routed through the explicit `_JSON_FIELDS` allowlist.

## ARTIFACTS
- `src/vitruvyan_motus/trace.py`: optional path context for `_strict_plain_json`; default behavior remains unchanged.
- `src/vitruvyan_motus/commitments.py`: schema-bound JSON-field allowlist and recursive proof validation.
- `tests/test_commitments_schema_fields.py`: bytes, set, non-string key, NaN, three-level nested failures, arbitrary-depth valid proof, and path/J1 assertions.

## TESTS
Raw output:
- `.venv/bin/python -m pytest -q` — `1228 passed, 5 skipped, 1 warning in 100.22s (0:01:40)`.
- `.venv/bin/python tools/check_frozen_paths.py "$(git merge-base HEAD main)" HEAD` — `Frozen contract paths: PASS`.
- `.venv/bin/python .attack/pr131/a08_huge_height_reference_exceeds_identifier_length.py` — runtime `ValueError` refusal; `NOT REPRODUCED` at the later validator path.
- `git diff --check` — passed.
- Verifier focused suites — `77 passed`, `100 passed, 4 skipped`, and `55 passed`; no findings.

## FINDINGS
- No findings from verification. The implementation preserves the outer proof-object requirement, accepts arbitrary-depth valid JSON, and applies the shared validator without a second recursive implementation.

## MUTATION TARGETS
- Remove the `_strict_plain_json` call from `_validate_schema_fields`: each nested invalid-proof regression must fail.
- Remove path propagation from `_strict_plain_json`: nested path/J1 assertions must fail.
- Remove `AnchorReceipt` from `_JSON_FIELDS`: invalid proof values must become constructible.
- Remove the outer `dict` guard in the JSON-field route: the non-object proof regression must fail.

## OUT OF SCOPE
- No contract/schema or frozen-path changes.
- No changes to proof semantics beyond strict JSON representability.
- No mutation probe, commit, push, tag, release, or dependency changes.

## AGENTS
- Lead: current Pi session.
- Implementer: Pi `openai-codex/gpt-5.6-luna`, `implementer-008c`; applied the correction round.
- Verifier: Pi `openai-codex/gpt-5.6-luna`, `verifier-008c`; initial report and `/tmp/verifier-008c-correction.md`.
- Adversary: Claude, same runtime-accepted / validator-refused lens, `/tmp/adversary-008c.md`; identified the dict-subclass and aliasing gaps.
- No further adversary round, mutation probe, commit, push, tag, or release.

### Correction round 1

## FINDINGS
- Fixed: `_strict_plain_json` now snapshots one `.items()` result and uses it for key validation, reserved-redacted detection, and recursive isolation, closing dict-subclass view discrepancies.
- Fixed: `AnchorReceipt.proof` stores the isolated plain JSON result at construction. `to_dict()` revalidates and returns a fresh isolated copy, so caller or accessor mutation cannot bypass path-aware J1 refusal.

## TESTS
Raw correction-round output:
- `.venv/bin/python -m pytest -q tests/test_commitments_schema_fields.py tests/test_surrogate_boundary.py tests/test_commitments.py` — `180 passed, 4 skipped in 2.82s`.
- `.venv/bin/python -m pytest -q` — `1231 passed, 5 skipped, 1 warning in 95.83s (0:01:35)`.
- `.venv/bin/python tools/check_frozen_paths.py "$(git merge-base HEAD main)" HEAD` — `Frozen contract paths: PASS`.
- `.venv/bin/python .attack/pr131/a08_huge_height_reference_exceeds_identifier_length.py` — runtime `ValueError` refusal; `NOT REPRODUCED` at the later validator path.
- `git diff --check` — passed.

## MUTATION TARGETS
- Revert the single `.items()` snapshot and restore separate iteration: the sneaky dict-subclass regression must fail.
- Discard the construction-time `_strict_plain_json` result: source-alias isolation regression must fail.
- Remove serialization-time shared validation/isolation: accessor mutation must serialize no longer, or lose path/J1 refusal.
- Remove reserved-redacted checking from the snapshot: the sneaky reserved-proof regression must fail.

## AGENTS
- Lead: current Pi session.
- Adversary: `/tmp/adversary-008c.md`, same runtime-accepted/validator-refused lens; identified the two blocking gaps.
- Correction implementer: current Pi session; no separate implementer agent used.
- Verifier: prior `verifier-008c` focused review plus current focused/full/frozen/a08 verification.
- No mutation probe, commit, push, tag, or release.
