# REPORT — 010 / #129 seal reorder

## RESULT
Implemented the decided two-state seal protocol. A failure before `os.replace` leaves the live window open and retryable; a failure at or after the rename poisons the live instance. Existing append poison behavior and handle-close coverage remain intact.

## ARTIFACTS
- `src/vitruvyan_motus/commitments.py`: added pure `CommitmentWindow.checkpoint_at()` and checked `mark_sealed()`; retained `seal()` as snapshot+mark composition.
- `src/vitruvyan_motus/commitlog.py`: split atomic write into prepare/commit phases with rename-boundary metadata, temporary cleanup, and seal poison boundary handling; updated stale ordering comment.
- `tests/test_commitlog.py`: replaced the old failed-seal test with a 9-point × 2-fsync parametrized sweep and snapshot/mark regression; retained handle-close test.

## TESTS
Raw output:
```
.venv/bin/pytest -q tests/test_commitments.py tests/test_commitlog.py
109 passed in 2.18s

.venv/bin/pytest -q
1243 passed, 5 skipped, 1 warning in 87.14s (0:01:27)

.venv/bin/python tools/check_frozen_paths.py origin/main HEAD
Frozen contract paths: PASS

Final raw correction output:
```
.venv/bin/pytest -q tests/test_commitlog.py tests/test_commitments.py
119 passed in 2.28s

.venv/bin/pytest -q
1253 passed, 5 skipped, 1 warning in 82.46s (0:01:22)

.venv/bin/python tools/check_frozen_paths.py origin/main HEAD
Frozen contract paths: PASS

14 permitted .attack/129 scripts: PASS
attack_10_mutation_probe_via_monkeypatch.py: SKIP (prohibited)
```

Correction rerun attack accounting (raw result):
- Updated ignored harness assumptions only: `attack_01_seal_checkpoint_write.py` now injects prepare-phase failure and checks retryability; `attack_04_append_window_refuses.py`, `attack_05...`, and `attack_08...` use the current `commitment` envelope and cleanup contract.
- PASS: all 14 non-mutation-probe attack scripts present under `.attack/129` (including the legacy `attack1`–`attack5` scripts).
- Explicitly not run: `attack_10_mutation_probe_via_monkeypatch.py`, per the no-mutation-probe instruction.

The requested GitHub issue fetch was unavailable in this environment (`gh issue view 129 --comments` returned no issue data); the supplied task brief and repository source/plan/attack corpus were read in full.

## FINDINGS
Correction round evidence: focused seal/commitment suite is `119 passed`; full suite is `1253 passed, 5 skipped, 1 warning`; frozen guard is `PASS`; all 14 permitted attack scripts are green. No source boundary defect remains.

Adversarial findings processed:
- Fixed (critical durability): cleanup and `path.exists()` could mask the poison decision. Phase state is now decided before cleanup; replace observation is conservative and cleanup catches `BaseException`.
- Fixed (medium exception fidelity): `_atomic_write()` now unwraps its phase wrapper, preserving identity-write exception type and errno.
- Fixed (low/medium coverage): restored `_remember()` post-write poison coverage.
- Fixed (medium API concurrency): `CommitmentWindow.seal()` uses private locked helpers, preserving one-lock snapshot/mark atomicity.
- Fixed (verifier evidence): the sweep now checks continuation, reseal, reopen, sealed-window recovery, and same-instance refusal for every relevant phase.
- Fixed (report accuracy): the sweep is 9 fault points, and agent/attack accounting is current.

One verifier orchestration mistake briefly included ignored `run_all.py`, which invoked the in-process monkeypatch attack 10; no source was mutated and `tools/mutation_probe.py` was never invoked. All final verification deliberately excluded `run_all.py` and attack 10.

Final explicit-evidence correction: the parametrized post-rename branch now asserts `begin()`, `end()`, and `seal()` each raise `CommitmentLogFork` while the poisoned live log is still open, before close/reopen verification. The replace-after case also injects a `.tmp` `Path.unlink` failure after the real rename, proving cleanup cannot mask poison.

Raw final focused output:
```
.venv/bin/pytest -q tests/test_commitlog.py tests/test_commitments.py
119 passed in 2.28s

.venv/bin/python tools/check_frozen_paths.py origin/main HEAD
Frozen contract paths: PASS
```

- Fixed: seal mutated the window before durable checkpoint publication. Prepare is now non-mutating; mark follows successful rename.
- Fixed: replace completion observes both entries with `Path.stat()` before cleanup: target absent+temp present and both present are pre-replace; target present+temp absent is post-replace; both absent or any non-FileNotFound stat error conservatively poisons. Poison is decided before best-effort cleanup (which catches `BaseException`); seal no longer uses `path.exists()` as its post-fault classifier. Unit coverage asserts the matrix and the replace-before sweep asserts temp cleanup.
- Fixed: `_atomic_write` unwraps its private phase marker and preserves the original identity-write exception type and errno; PermissionError/errno fidelity is regression-tested.
- Fixed: restored independent `_remember`-failure poison coverage.
- Fixed: `CommitmentWindow.seal()` uses private locked helpers so snapshot+mark remains one atomic lock-held operation.
- Fixed: temporary artifacts from pre-rename failures are removed.
- Preserved: failures in mark, handle close, following, or directory sync after rename poison the instance.
- Fixed in correction round: all stale attack harness assumptions were updated without changing source/property behavior; the full non-mutation attack rerun is green.

## MUTATION TARGETS
- Phase split: mutate seal to call one undifferentiated atomic write / mutate prepare to rename; the pre-rename sweep must remain retryable and the post-rename sweep must poison.
- Mark check: remove `mark_sealed()`'s live snapshot comparison or permit count/last-leaf drift; the snapshot/mark invariant test must fail.
- Poison boundary: poison before rename or fail to poison after rename; the parametrized fault sweep and reopen/refusal checks must fail.

## OUT OF SCOPE
- No contract or compatibility corpus changes.
- No runtime dependency changes.
- Only ignored `.attack/129` harness assumptions were updated; source and committed properties were not weakened.
- No changes to runtime end-failure handling or general `close()` resource semantics.
- No commit, push, tag, release, or mutation probe was run in the correction round.

## AGENTS
- Lead: current Pi session.
- Implementer: `implementer-010` (Pi, GPT-5.6 Luna).
- Verifier: `verifier-010` (Pi, GPT-5.6 Luna).
- Adversarial durability review: `adversary-010-durability` (Claude).
- Adversarial class-sweep review: `adversary-010-class` (Claude).
- Corrections were processed by the lead with the implementer; no agent committed, pushed, tagged, released, or ran mutation tooling.
