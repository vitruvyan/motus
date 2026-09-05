# Report — #129

## RESULT
Implemented durable ordering guards for `CommitmentLog._append` and `seal()`.
A post-write window refusal, nonce bookkeeping failure, checkpoint
write/close failure, or next-window advance failure now poisons the live
instance instead of allowing another sequence or nonce to be issued.

## ARTIFACTS
- `src/vitruvyan_motus/commitlog.py`
- `tests/test_commitlog.py`
- `PLAN.md` (architect plan, pre-existing task artifact)

Tests cover the failed seal, failed `begin()` after its line is durable, the
handle-close site, and a parametrized sweep over the currently known post-write
steps. Reopening remains the recovery path and counts whatever durable lines
actually survived.

## TESTS (raw output)
Focused command:
```
.venv/bin/pytest -q tests/test_commitlog.py
..................................................                       [100%]
50 passed in 0.46s
```
Second verifier pass:
```
.venv/bin/pytest -q
........................................................................ [ 83%]
........................................................................ [ 89%]
........................................................................ [ 96%]
....ssss..................................                               [100%]
1118 passed, 5 skipped in 53.89s (0:00:53)
```
The literal frozen-path command has a repository-tool usage anomaly because
it requires two positional arguments:
```
.venv/bin/python tools/check_frozen_paths.py
usage: check_frozen_paths.py [-h] base head
check_frozen_paths.py: error: the following arguments are required: base, head
```
The valid invocation is green:
```
.venv/bin/python tools/check_frozen_paths.py demo/three-domains-anchored HEAD
Frozen contract paths: PASS
```

Adversarial attack scripts were re-run green:
```
.venv/bin/python .attack/129/run_all.py
# all 10 durability attacks passed
.venv/bin/python .attack/129/attack1_seal_close_real_ebadf.py
.venv/bin/python .attack/129/attack2_replay_truncate_fsync_fails.py
.venv/bin/python .attack/129/attack3_seal_replace_succeeds_then_fails.py
.venv/bin/python .attack/129/attack4_nonce_replay_after_poisoned_begin.py
.venv/bin/python .attack/129/attack5_remember_unguarded_after_window_advance.py
# all 15 preserved attack scripts passed after the final fix
```

## MUTATION CHECKS
The implementer temporarily replaced the source, ran focused tests, and
restored the exact fixed source from a backup after each mutation:

- Removing the `_append` post-write poison guard caused the failed-begin test
  and parametrized append case to fail (`2 failed, 47 deselected`).
- Removing the `seal()` guard caused the failed-seal and failed-close tests to
  fail (`2 failed, 47 deselected`).
- The adversaries independently reverted the fix and all seven new tests
  failed; the attacks also reproduced the duplicate/unpoisoned failure mode.
- Moving `_remember` back outside the post-write guard caused the new
  parametrized `remember` case to fail (`1 failed, 49 deselected`), and the
  independent attack reproduced the same-nonce retry.

No mutation was left in the source. The CTO runs `tools/mutation_probe.py`
after committing; it refuses uncommitted trees, so it was not run here.

## MUTATION TARGETS
The CTO's mutation probe should neuter these exact load-bearing regions:

- `src/vitruvyan_motus/commitlog.py:588-599`: the `try/except BaseException`
  around both `self._window.append(commitment)` and `_remember(commitment)`,
  plus its `_poison(...)` call.
- `src/vitruvyan_motus/commitlog.py:772-786`: the `try/except BaseException`
  covering checkpoint `_atomic_write`, handle close, next-window advance and
  nonce clearing, including its `_poison(...)` call.

Do not run the probe in this uncommitted worktree; the CTO runs it after the
human commits.

## FINDINGS
- **Fixed — durability lens (round #129):** PLAN.md §5 incorrectly claimed
  `_sync_directory` could not raise because it swallowed `OSError`. Corrected
  the sweep to distinguish `os.fsync` (swallowed) from `os.open` (outside the
  inner guard) and `os.close` in `finally`, and documented the resulting
  before/after-rename outcomes. The callers already fail closed correctly:
  `_write_identity`/`__init__` release the lock and return no object, while
  `seal()` poisons the live log.
- **Fixed — second-round class lens (round #129):**
  `.attack/129/attack5_remember_unguarded_after_window_advance.py` found that
  `_remember()` ran after the durable write and successful window advance but
  outside the guard. A `MemoryError` left the store usable, allowed a same-
  nonce retry, and produced duplicate nonces on disk. `_remember()` now runs
  inside the existing post-write poison guard; the parametrized sweep covers
  and mutation-checks it. The attack script is preserved under `.attack/129/`.
- **No finding — first-round class lens (round #129):** The adversary searched
  `_write_identity`, `_replay` truncation, `_open_handle`, `_append`, and seal
  cleanup/advance steps. No other source defect remained: live append and seal
  paths are guarded, and initialization-time failures cannot return a usable
  unpoisoned object.

Attack command/output for the confirmed pre-fix finding (the script exited
2 as expected):
```
.venv/bin/python .attack/129/attack5_remember_unguarded_after_window_advance.py
log._poisoned: None
CONFIRMED: the live store is NOT poisoned and remains usable after this failure -- the guard added for #129 does not cover this step.
CONFIRMED: retry with the SAME nonce on the SAME live instance SUCCEEDED: 1 shared-nonce-1
nonces now on disk: ['shared-nonce-1', 'shared-nonce-1']
CONFIRMED: the durable chain now holds two commitments sharing one nonce
RESULT: CONFIRMED FINDING
[exit 2]
```
After the fix, the same attack reports `log._poisoned` with the post-write
bookkeeping reason, refuses the retry, and exits 0.

## OUT OF SCOPE, NOTICED
`runtime.py`'s swallowing of a later `end()` failure, and `close()` resource
failure semantics, remain outside this task's permitted scope.

## AGENTS
Architect (`claude`) wrote PLAN.md; one implementer (`pi`,
`openai-codex/gpt-5.6-luna`) implemented the source/tests and focused mutation
checks; verifier (`pi`, same model) ran the full suite and frozen-path checks;
two independent `claude` adversaries ran durability and class lenses. No agent
committed, pushed, tagged or released.
