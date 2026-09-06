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

## Round 3 (Codex threads)

Two review findings, both valid, fixed on top of the seal reorder. Neither
changed contract or frozen corpora; both closed by making the implementation
match the property the fix already claimed to have.

### Finding 1 — `_sync_directory` swallowed the OSError it exists to surface

`_commit_atomic_write`'s post-rename phase called `_sync_directory(path.parent)`
inside `except BaseException as exc: raise _AtomicWriteFailure(...)`, but
`_sync_directory` itself caught every `OSError` from the directory fsync and
discarded it (`except OSError: pass`). A real EIO/ENOSPC on the directory
never reached that `except` clause at all, so `seal()` could return a
checkpoint and report `durable == True` while the checkpoint's directory
entry might not survive a crash — the `after_replace=True` classification
this wrapper exists for was reachable only when `os.open` itself failed, per
issue #151's own note.

Fix (general, not the instance): `_sync_directory` gained a `strict: bool =
False` parameter — `src/vitruvyan_motus/commitlog.py:149`. Non-strict is the
historical swallow, kept as the default so `_open_handle`
(`src/vitruvyan_motus/commitlog.py:543`) is untouched — whether a refused
directory fsync there should poison the log is issue #151's still-open
question, not this fix's to settle, and a comment there says so.
`_commit_atomic_write` now calls `_sync_directory(path.parent, strict=True)`
(`src/vitruvyan_motus/commitlog.py:1443`), so the OSError propagates into the
`except BaseException` right below it and is wrapped as
`_AtomicWriteFailure(exc, after_replace=True)` — the poison path `seal()`
already had, now actually reachable through the real syscall.

Two existing tests monkeypatched `_sync_directory` wholesale with a
`lambda *a: ...`; since the new call site passes `strict=True` as a keyword,
both lambdas needed `**k` added (`tests/test_commitlog.py:242`,
`tests/test_commitlog.py:401`) to keep raising regardless of the keyword —
mechanical compatibility, not a weakened assertion; both still assert the
same `OSError`.

Test: `test_seal_reports_a_real_directory_fsync_failure_after_replace`
(`tests/test_commitlog.py:315`). It does not monkeypatch `_sync_directory`
(the sweep's `dir-sync` case already does that, and stays); it monkeypatches
`os.fsync` itself, raising `OSError(errno.EIO)` only when
`stat.S_ISDIR(os.fstat(fd).st_mode)` is true, so the file's own fsync in
`_prepare_atomic_write` is untouched and only the directory descriptor
fails. It asserts `seal()` raises `OSError`, the checkpoint file IS on disk
(the rename already completed), `log._poisoned is not None`, every
subsequent operation is refused with `CommitmentLogFork`, and a reopen after
`close()` recovers the sealed window and verifies the chain.

**Killing line, proved by neutering**: reverted
`_sync_directory(path.parent, strict=True)` back to
`_sync_directory(path.parent)` at `src/vitruvyan_motus/commitlog.py:1443` (no
other change). Result:
```
.venv/bin/pytest -q tests/test_commitlog.py -k directory_fsync_failure
F
...
>       with pytest.raises(OSError):
E       Failed: DID NOT RAISE <class 'OSError'>
tests/test_commitlog.py:340: Failed
1 failed, 77 deselected in 0.25s
```
Restored, then `tests/test_commitlog.py`: `78 passed`.

### Finding 2 — `mark_sealed` accepted a checkpoint from another window with the same shape

`_mark_sealed_locked`'s four O(1) checks (not-yet-sealed, count, index,
first/last sequence) are functions of *how many* commitments a window holds
and *where* they sit in the sequence — none of them is a function of *which*
commitments those are. Two windows for the same writer with identical
dimensions but different actual commitments passed every check, and
`_sealed` was set to a digest whose Merkle root does not describe the window
it was set on.

Fix: bind the mark to the exact snapshot rather than recomputing the root.
`CommitmentWindow` gained one slot, `_snapshot_digest`
(`src/vitruvyan_motus/commitments.py:766`). `_checkpoint_locked` records
`checkpoint.digest` into it right after building the checkpoint
(`src/vitruvyan_motus/commitments.py:879`; replaced on each call, so only the
most recent snapshot is live). `append` clears it back to `None` on every
successful append (`src/vitruvyan_motus/commitments.py:827`), so a stale
snapshot can never be marked even in a hypothetical where the O(1) checks
alone would not have caught the drift. `_mark_sealed_locked` now requires
`checkpoint.digest == self._snapshot_digest` in addition to (not instead of)
the four O(1) checks (`src/vitruvyan_motus/commitments.py:908`). The comment
block above the checks was rewritten to say why the four facts do not
suffice on their own — they classify by shape, not by content
(`src/vitruvyan_motus/commitments.py:887-901`).

Tests, both in `tests/test_commitments.py`:
- `test_mark_sealed_refuses_a_checkpoint_snapshotted_from_a_different_window`
  (`tests/test_commitments.py:473`): two windows, same tenant/writer, same
  index/count/sequence range, different `run_id`s (so different digests).
  `checkpoint_at` on window A, `mark_sealed` on window B with A's checkpoint
  → refused with "not snapshotted from this window"; B then seals normally
  against its own snapshot, proving the binding refuses a foreign checkpoint
  and nothing else.
- `test_mark_sealed_refuses_after_a_real_append_between_snapshot_and_mark`
  (`tests/test_commitments.py:504`): a real `append()` (not poking `_leaves`
  directly, unlike the pre-existing count test) between `checkpoint_at` and
  `mark_sealed` still refuses — pins that the digest-clearing mechanism the
  fix depends on did not accidentally become the *only* thing standing
  between a stale snapshot and a seal.

**Killing line, proved by neutering**: removed the
`if checkpoint.digest != self._snapshot_digest: raise ValueError(...)` block
at `src/vitruvyan_motus/commitments.py:908-912`, leaving only
`self._sealed = checkpoint.digest`. Result:
```
.venv/bin/pytest -q tests/test_commitments.py -k "different_window or real_append"
F.
...
>       with pytest.raises(ValueError, match="not snapshotted from this window"):
E       Failed: DID NOT RAISE <class 'ValueError'>
1 failed, 1 passed, 50 deselected in 0.15s
```
(The append-between-snapshot-and-mark test still passed on its own, exactly
as expected — the O(1) checks already cover that reachable case; the digest
binding is what closes the *different-window* case those checks cannot.)
Restored, then `tests/test_commitments.py`: `52 passed`.

### Full verification after both fixes

```
.venv/bin/pytest -q tests/
1264 passed, 5 skipped, 1 warning in 57.52s

.venv/bin/python tools/check_frozen_paths.py origin/main HEAD
Frozen contract paths: PASS

.venv/bin/python .attack/129/round2/d13_failed_seal_pins_the_writer_lock.py
source                      : /home/vitruvyan/motus-factory/src
CommitmentLog still reachable after the caller dropped it: 0
reopen in the same process  : OK
reopen after gc.collect()   : OK  <- freed only by the cyclic collector

.venv/bin/python .attack/129/round2-class/a2_baseline_vs_head.py src 3000
src=src  trials=3000  seal() produced nothing=0  rate=0.00%
```

Files touched: `src/vitruvyan_motus/commitlog.py`,
`src/vitruvyan_motus/commitments.py`, `tests/test_commitlog.py`,
`tests/test_commitments.py`. No contract, frozen corpus, or dependency
changes; nothing committed (per instruction — worktree left as-is for
review).
