# Task 004 / #118 — `CommitmentLog.receipt_for`

## RESULT
Implemented and verified. The tree is intentionally uncommitted for human review.

## ARTIFACTS
- `contract/receipt.v1.schema.json`: optional ADR-027 `execution` object (`ref`, `fingerprint`, `run_id`).
- `contract/validate.py`: P7 execution-reference binding, P8 fingerprint/unfinished-state checks, weakest-mode validation, continuation omission notes.
- `src/vitruvyan_motus/commitlog.py`: read-only `receipt_for`, sealed proof assembly, resumed-chain backward/forward traversal, anchors, ambiguity refusals, schema-boundary checks.
- `contract/fixtures/223-receipt-p7-execution-ref-names-a-different-begin.json`
- `contract/fixtures/224-receipt-p8-unfinished-has-fingerprint.json`
- `contract/README.md`, `README.md`: contract/API documentation.
- `tests/test_verifier.py`, `tests/test_contract_fixtures.py`: producer, CLI/verifier, tamper, continuation, refusal, anchor, mode, and boundary regressions.

## TESTS (raw output)
- `.venv/bin/pytest -q` → `1146 passed, 5 skipped in 53.02s`
- `.venv/bin/python tools/check_frozen_paths.py HEAD~1 HEAD` → `Frozen contract paths: PASS`
- `git diff --check` → clean
- Final verifier round: `1145 passed, 5 skipped`; targeted robustness tests: `11 passed`; contract P8 fixture: `1 passed, 162 deselected`.
- CLI verification of a freshly produced receipt with trace: exit `0`; `INTEGRITY ESTABLISHED`, pending anchor correctly `EXISTENCE NOT YET`.
- Produced finished, unfinished, resumed (original and continuation refs), unresolved-predecessor, anchored, and refusal cases were schema-validated. `demo/out` contains no receipt.v1 artifact; its anchor/report JSON is correctly rejected as non-receipt input.

## FINDINGS
All findings from architect, verifier, and both adversarial lenses were processed:

- **Fixed:** receipt could not produce a real receipt for unfinished/resumed/anchor/refusal paths; added coverage and mutation targets.
- **Fixed:** continuation refs silently omitted local predecessors; now walks backward then forward and canonicalizes to the earliest available BEGIN.
- **Fixed:** unresolved cross-log predecessor was not reported; verifier notes the omitted predecessor.
- **Fixed:** P8 falsely flagged a null fingerprint on an unfinished receipt paired with an unrelated complete trace; P8 now applies to trace comparison only when the receipt has an END.
- **Fixed:** non-null fingerprint on an unfinished receipt was unchecked; contract P8 rejects it and fixture 224 covers it.
- **Fixed:** P8 had no contract fixture/advertised-rule coverage; added `P8` and fixture 224.
- **Fixed:** oversized run IDs and other receipt `Identifier` fields could produce schema-invalid receipts; all included commitment/checkpoint/witness/anchor identifiers are checked before return.
- **Fixed:** forked continuations and multiple ENDs were silently resolved by first-match selection; `CommitmentLogFork` refuses ambiguity.
- **Fixed:** resumed top-level mode could overstate assurance; mode is the weakest achieved mode across included BEGINs and P3 checks all segments.
- **Fixed:** unbounded decimal refs could escape as an integer-conversion exception; they become P7/`ValueError`.
- **Fixed:** slash-containing tenant/writer identities cannot be encoded in `execution_ref`; `receipt_for` refuses them clearly.
- **Fixed:** unsealed BEGIN refs were indistinguishable from unknown refs; the API now reports `not yet sealed`.
- **Open none.** Low-priority inherited notes about the ADR wording “required from 1.0.0” versus the fixed `1.0.0` schema const, and ADR status on the stale branch, are out of scope and were not changed.

## OUT OF SCOPE, NOTICED
- No changes to `src/` beyond `commitlog.py`, no contract frozen tests, compat tests, workflows, or `demo/out`.
- No new dependencies, network calls, commits, pushes, tags, releases, or mutation probe on the dirty tree.
- `demo/out/anchor_receipt.json` is an `AnchorReceipt`, not a receipt.v1 document; it remains unchanged.

## AGENTS
- Architect: `claude` (`architect-118`), plan at `/tmp/architect-118.md`.
- Implementer: `openai-codex/gpt-5.6-luna` (`implementer-118`), two correction rounds.
- Verifier: `openai-codex/gpt-5.6-luna` (`verifier-118`), final report `/tmp/verifier-118-final.md`.
- Adversaries: `claude` (`adversary-protocol-118`, public protocol lens; `/tmp/adversary-protocol-118-round2.md`) and `claude` (`adversary-identity-118`, positions/identity lens; `/tmp/adversary-identity-118-round2.md`).
