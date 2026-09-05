# TASK 006b — #132 window-line key

## RESULT

PASS. H1 passed before the writer change: renaming the envelope key from `c` to
`commitment` preserved every leaf, the sealed window root, and the byte-identical
checkpoint. The reader now accepts `commitment` and legacy `c`; `_append` now
writes only `commitment`. The strict commitment validator and frozen evidence
were not changed.

## ARTIFACTS

- `src/vitruvyan_motus/commitlog.py` — reader compatibility, one-time log
  diagnostics, executable 1.0.0 fallback guard, and new writer key.
- `tests/test_issue132_window_key.py` — H1, 18/18 CLI validator checks, old-log
  chain/receipt/diagnostic, mixed-window verification, and removal guard.
- `tests/test_commitlog.py` and `tests/test_the_digest_precedes_the_write.py` —
  existing writer-shape assertions updated from `c` to `commitment`.
- `contract/README.md` — names `commitment` as the on-disk envelope key.
- `README.md` — records that releases through 0.13.0 wrote `c` and 0.14.0
  writes `commitment`.
- `demo/README.md` — records frozen `demo/out` legacy evidence and forbids
  rewriting it. `demo/out` itself is untouched.

## TESTS (raw output)

H1, before changing `_append`:

```
.venv/bin/pytest -q tests/test_issue132_window_key.py
.                                                                        [100%]
1 passed in 0.24s
```

After the writer change, focused commitment/contract tests:

```
222 passed in 6.11s
```

Migration tests, including 18 invocations of `motus-validate commitment`:

```
5 passed in 4.92s
```

Full suite:

```
1188 passed, 5 skipped, 1 warning in 66.21s (0:01:06)
```

Post-adversarial correction focused suite: `59 passed`.

The warning is the pre-existing duplicate ZIP member warning in
`tests/test_evidence_package.py`.

## FINDINGS

- **FIXED — H1 reader could only read `c`.** Reader selection now prefers
  `commitment` and falls back to `c`; H1 proves the envelope key is outside the
  leaf/root/checkpoint digests.
- **FIXED — new lines failed the published strict schema.** `_append` writes
  `{"commitment": ..., "witness": ...}` and validator code/schema remain
  strict.
- **FIXED — legacy use was silent/repeated.** A `c` line adds exactly one
  diagnostic to `CommitmentLog.diagnostics`: `written by a Motus before 0.14.0`.
- **FIXED — deprecation could be forgotten.** The fallback raises an explicit
  removal error if either `__version__` or `TRACE_SCHEMA_VERSION` is `1.0.0`,
  covered by tests for both version constants.
- **OPEN — none confirmed.** No correction round or mutation probe was run, as explicitly prohibited by the task.
- **Fixed after adversarial review:** `demo/README.md` now states that the repository's frozen `demo/out` contains no commitment-log window files; external pre-0.14.0 evidence may use `c`.
- **Fixed after adversarial review:** a line containing both `commitment` and `c` is rejected as ambiguous rather than silently preferring one spelling.
- **Open, unconfirmed:** diagnostics use a list de-duplication check without an explicit read lock. Sequential repeated reads remain exactly once and no concurrent duplicate was reproduced; this is outside ADR-028's behavior decision.

## MUTATION TARGETS

The tests are intended to fail for these mutations:

1. Change `_append` back to `c`: strict 18/18 validation and the new-key shape
   fail.
2. Remove the `commitment` reader branch: H1, mixed spelling, and new-log
   reopen fail.
3. Remove the `c` fallback: H1, old-log chain/receipt, and mixed spelling fail.
4. Remove diagnostics or its once-only guard: old-log diagnostic assertions
   fail.
5. Remove either 1.0.0 guard: the corresponding version test fails.
6. Make the envelope part of the leaf input: H1 root/leaf/checkpoint equality
   fails.

## OUT OF SCOPE, NOTICED

No validator relaxation, schema change, rewrite tool, demo output rewrite,
commit, push, tag, release, or `mutation_probe` was performed.

## ADVERSARIAL CORRECTION

- **Fixed:** the adversary found that the new `demo/README.md` falsely claimed commitment windows existed under `demo/out/`; it now states that `demo/out` contains no commitment-log window files and documents external legacy evidence accurately.
- **Fixed:** a line carrying both `commitment` and `c` is now rejected as ambiguous instead of silently preferring one spelling.
- **Open, unconfirmed:** diagnostic de-duplication uses a list check without an explicit read lock. Repeated sequential reads are covered and remain exactly once; no concurrent duplicate was reproduced. This remains a future concurrency-hardening question, outside the accepted ADR behavior.

## AGENTS

- Implementer: `openai-codex/gpt-5.6-luna` (`implementer-006b`).
- Verifier: `openai-codex/gpt-5.6-luna` (`verifier-006b`), `/tmp/verifier-006b.md`.
- Adversary: `claude` (`adversary-006-window-spellings`), `/tmp/adversary-006-window-spellings.md`.

## 006c

## RESULT

PASS. Removed the production 1.0.0 runtime guard and the fallback's package/version imports. Added the explicit `LEGACY_WINDOW_KEY` marker and freeze-guard test while preserving 006b behavior.

## ARTIFACTS

- `src/vitruvyan_motus/commitlog.py` — explicit legacy key marker; no runtime version guard or `TRACE_SCHEMA_VERSION` fallback import.
- `tests/test_issue132_window_key.py` — freeze test detects the marker using `__version__` only.

## TESTS (raw output)

Focused 006c and 006b commitment tests:

```
.venv/bin/pytest -q tests/test_issue132_window_key.py tests/test_commitlog.py tests/test_the_digest_precedes_the_write.py
...........................................................              [100%]
59 passed in 6.34s
```

Full suite:

```
1188 passed, 5 skipped, 1 warning in 63.45s (0:01:03)
```

Frozen paths:

```
.venv/bin/python tools/check_frozen_paths.py HEAD HEAD
Frozen contract paths: PASS
```

The warning is the pre-existing duplicate ZIP member warning in
`tests/test_evidence_package.py`.

## FINDINGS

- **FIXED** — removed the obsolete runtime guard and both version dependencies from the fallback.
- **OPEN** — none known.

## MUTATION TARGETS

1. Restore the fallback's 1.0.0 runtime guard or `TRACE_SCHEMA_VERSION` import: the requested source shape is violated.
2. Remove `LEGACY_WINDOW_KEY` while `__version__` starts with `1.`: the freeze test passes only when the marker is absent at the contract freeze.

## OUT OF SCOPE

No commit, push, tag, release, or `mutation_probe` was performed. All 006b changes and frozen artifacts remain otherwise untouched.

## AGENTS

- Implementer: `openai-codex/gpt-5.6-luna` (`implementer-006c`).
- Verifier: `openai-codex/gpt-5.6-luna` (`verifier-006c`), `/tmp/verifier-006c.md`.

## 006d

## RESULT

PASS. On construction, after recovery, sealed windows are cheaply inspected at
their first non-blank line; a legacy `c` envelope now records the existing
 diagnostic before any later operation. Diagnostic de-duplication and mixed
window read behavior remain intact.

## ARTIFACTS

- `src/vitruvyan_motus/commitlog.py` — construction-time sealed-window key scan,
  with the same diagnostic channel and once-only behavior.
- `tests/test_issue132_window_key.py` — fresh legacy-open diagnostic assertion
  and fresh all-`commitment` diagnostic-free assertion.

## TESTS (raw output)

Focused:

```
.venv/bin/pytest -q tests/test_issue132_window_key.py tests/test_commitlog.py tests/test_the_digest_precedes_the_write.py
............................................................             [100%]
60 passed in 5.57s
```

Full suite:

```
1189 passed, 5 skipped, 1 warning in 78.00s (0:01:18)
```

Frozen paths:

```
.venv/bin/python tools/check_frozen_paths.py HEAD HEAD
Frozen contract paths: PASS
```

The warning is the pre-existing duplicate ZIP member warning in
`tests/test_evidence_package.py`.

## FINDINGS

- **FIXED** — legacy sealed windows were silent until a later read; construction
  now scans the first non-blank line of every sealed window and records the
  existing diagnostic immediately.
- **FIXED** — fresh all-new-key instances remain diagnostic-free.
- **OPEN** — none known.

## MUTATION TARGETS

1. Remove `_scan_sealed_window_keys()` from construction: the fresh legacy-open
   assertion fails before any other operation.
2. Scan only the open window or omit sealed checkpoint indices: the same test
   fails.
3. Remove the diagnostic once-only guard: existing repeated-read assertions
   fail.
4. Change the scan to inspect every line: behavior remains correct but violates
   the intended cheap first-line scan; the implementation comment documents the
   invariant and mixed-window handoff.

## OUT OF SCOPE

No validator/schema/demo/out/frozen changes, rewrite tool, commit, push, tag,
release, or `mutation_probe` was performed.

## AGENTS

- Implementer: `openai-codex/gpt-5.6-luna` (`implementer-006d`).
- Verifier: `openai-codex/gpt-5.6-luna` (`verifier-006d`), `/tmp/verifier-006d.md`.

## 006e

## RESULT

PASS. Added a load-bearing regression test for a legacy `c` envelope in an
open, unsealed window with no sealed windows. Fresh construction therefore
recovers through `_recover` → `_replay`; the sealed-window scan has no windows
to inspect. The diagnostic is exactly `("written by a Motus before 0.14.0",)`.

## TESTS (raw output)

Focused test before mutation:

```
.venv/bin/pytest -q tests/test_issue132_window_key.py
........                                                                 [100%]
8 passed in 6.58s
```

Mutation proof (source saved byte-for-byte to
`/tmp/commitlog.py.006e.saved`, then the diagnostic append in
`_commitment_from` was hand-neutered):

```
.venv/bin/pytest -q tests/test_issue132_window_key.py::test_legacy_c_in_open_window_reports_diagnostic_during_recovery
F                                                                        [100%]
E           AssertionError: assert () == ('written by ...fore 0.14.0',)
E             Right contains one more item: 'written by a Motus before 0.14.0'
1 failed in 0.29s
```

Restored from the saved bytes (not git checkout); SHA-256 before mutation and
after restoration matched:

```
96b244d07e05034cd7775ecccb52ed36ceabef1310c64d45cc1c589b4d0d0c9a  src/vitruvyan_motus/commitlog.py
96b244d07e05034cd7775ecccb52ed36ceabef1310c64d45cc1c589b4d0d0c9a  /tmp/commitlog.py.006e.saved
```

Focused suite after restoration:

```
.venv/bin/pytest -q tests/test_issue132_window_key.py tests/test_commitlog.py tests/test_the_digest_precedes_the_write.py
.............................................................            [100%]
61 passed in 6.96s
```

Full suite:

```
1190 passed, 5 skipped, 1 warning in 77.06s (0:01:17)
```

Frozen paths:

```
Frozen contract paths: PASS
```

The warning is the pre-existing duplicate ZIP member warning in
`tests/test_evidence_package.py`.

## MUTATION TARGETS

1. Removing or neutering the diagnostic append in `_commitment_from` fails
   `test_legacy_c_in_open_window_reports_diagnostic_during_recovery`.
2. Routing the test through sealed-window scanning instead of `_recover` →
   `_replay` would violate the no-sealed-windows setup and its stated purpose.
3. Removing the `c` fallback prevents fresh recovery of the hand-rewritten
   open window.

## AGENTS

- Implementer: `openai-codex/gpt-5.6-luna` (`implementer-006e`).
- Verifier: `openai-codex/gpt-5.6-luna` (`verifier-006e`), `/tmp/verifier-006e.md`.
- No `tools/mutation_probe.py` run.
