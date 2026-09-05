# PLAN — #129: a failed `seal()` plus two ordinary runs opens no chain again

Architect's plan only. No source or test file is touched by this document.
Scope is `src/vitruvyan_motus/commitlog.py` and `tests/test_commitlog.py`, per
the issue's "what is owed" list. `contract/`, `tests/contract/`,
`tests/compat/` and `commitments.py`'s public shape are unchanged — see
§6.

## 1. Root cause, restated precisely

`_append` (commitlog.py:483-590) already has the rule the module's docstring
states: **the write happens before the memory does**, and a failure of the
write poisons the store (lines 573-586) before the exception reaches the
caller. `seal()` (commitlog.py:743-768) does not have that rule: it mutates
memory (`CommitmentWindow.seal()` sets the window's `_sealed` digest,
irreversibly — commitments.py:719-747) *before* the durable write
(`_atomic_write`, line 761) that can fail. When it fails, nothing poisons the
log, so:

- `seal()` can never be retried on this instance (`CommitmentWindow.seal()`
  raises `"this window is already sealed"` on a second call — commitments.py
  :675-681);
- every subsequent `begin()`/`end()` reaches `_append`'s write, durably writes
  the line, flushes, fsyncs — succeeds completely — and only then calls
  `self._window.append(commitment)` (line 588), which raises `"this window is
  sealed"` because of the marking above. That raise sits **outside** the
  try/except that guards the write (lines 574-586), so the store is not
  poisoned and `self._window`'s next-sequence counter is not advanced;
- the next call repeats exactly this, durably writing the **same** sequence
  again. Two ordinary calls now hold `[0, 1, 2, 2]` on disk under a checkpoint
  that sealed a shorter run, and `_replay` raises `CommitmentLogFork` from
  `__init__` on every future open — unrecoverable, per the issue's repro.

Two ordering defects, not one: `seal()`'s memory-before-disk mutation, and
`_append`'s unguarded write-then-mutate step that turns *any* post-write
exception (not only the seal race) into the same class of damage.

## 2. Exact ordering change — Site 1: `seal()`

Current order (commitlog.py:759-768):

```
checkpoint = self._window.seal(at)          # (a) MEMORY: irreversible
_atomic_write(path, ..., fsync=self._fsync) # (b) DISK: can raise
if self._handle is not None:
    self._handle.close()                    # (c) can raise
    self._handle = None
self._window = CommitmentWindow.following(checkpoint)  # (d)
self._nonces.clear()                        # (e)
return checkpoint
```

**Decision: guard, not reorder.** The issue offers two acceptable shapes —
"writes the checkpoint durably before mutating `_window`, **or** poisons on
failure." True write-before-mutate is not available without changing
`commitments.py`: `CommitmentWindow.seal()` is the only place that both
*computes* the `Checkpoint` (from `self._commitments[0]`/`[-1]`.sequence and
`self._leaves`) and *marks* `_sealed`, in one locked block, and it cannot be
called twice. Building the checkpoint fields a second way inside
`CommitmentLog.seal()` to defer the marking would mean two independent
encoders of one checkpoint — exactly the shape #112 already burned this file
on ("two encoders... meet... with a durable write between them"). Recomputing
correctly requires `commitments.py` API surgery (e.g. splitting `seal()` into
`build_checkpoint(at)` / `_mark_sealed(digest)`), which is out of the read
scope for this task and not needed: (a) is already irreversible and
unavoidable given the current API, so the established precedent in this same
file for an *unavoidable* irreversible step is `_append`'s own answer —
poison on failure. Applying it here is the general form, not a narrower one.

**The fix wraps everything from (b) through (e) in one guard**, not only
(b). (c), (d) and (e) are unguarded today and reproduce the identical bug
independently of (b): if `_atomic_write` succeeds but `self._handle.close()`
then raises (e.g. a delayed I/O error surfaced on close), the checkpoint IS
durably on disk, `self._window` is still the *old*, sealed-in-memory window
(steps d/e never ran), and the next `begin()`/`end()` reproduces the exact
`[n, n]`-duplicate-sequence failure through the very same unguarded
`self._window.append` in `_append` — a second site for one class, exactly
the shape AGENTS.md's "repair the class" rule warns about, and it survives a
fix that only wraps `_atomic_write`.

New order:

```python
def seal(self, at: str) -> Checkpoint:
    with self._lock:
        if self._closed:
            raise ValueError("this commitment log is closed")
        self._refuse_if_poisoned()
        path = self._dir / _CHECKPOINT.format(index=self._window.index)
        if path.exists():
            raise CommitmentLogFork(...)                 # unchanged
        checkpoint = self._window.seal(at)                # (a) irreversible
        try:
            _atomic_write(path, json.dumps(checkpoint.to_dict(), indent=2) + "\n",
                          fsync=self._fsync)               # (b)
            if self._handle is not None:
                self._handle.close()                       # (c)
                self._handle = None
        except BaseException as exc:
            self._poison(
                f"the window was marked sealed and the checkpoint then "
                f"failed to become durable ({type(exc).__name__}: {exc}); "
                "whether it reached disk is unknown, so this instance must "
                "not be trusted to seal or append again")
            raise
        self._window = CommitmentWindow.following(checkpoint)  # (d)
        self._nonces.clear()                                    # (e)
        return checkpoint
```

(d) and (e) have no known raise path today (`CommitmentWindow.following`
only re-validates already-valid strings; `set.clear()` cannot raise), so
leaving them outside the `try` is defensible — but if the implementer finds
it free to move them inside too (it is: three more lines, same indentation),
do it, so the invariant reads as "nothing between the memory mutation and
the return can fail silently" rather than "these two happen not to fail
today."

**Recovery after this poison is exactly the path `_append`'s equivalent
poison already relies on**: `_atomic_write` is whole-or-absent with respect
to its target before `os.replace` (commitlog.py:988-998), but its final
`_sync_directory` call occurs after the rename. That call swallows `OSError`
from `os.fsync`, but `os.open` is outside its inner `try` and `os.close` in
`finally` can also raise. Thus an exception from `_atomic_write` can mean
that the target is absent (failure before rename) or that it is already a
valid, fully renamed file (failure while syncing the directory). The seal
caller catches either case and poisons; `__init__` catches identity/recovery
failures, releases the lock, and returns no object. Recovery reads what is
actually on disk: no checkpoint means the window is still open and `_replay`
resumes it from the window file's actual contents; a checkpoint means the
window closes cleanly and the next one opens at the correct index. No code
path re-issues a sequence twice.

## 3. Exact ordering change — Site 2: `_append`

Current order (commitlog.py:573-590):

```python
handle = self._open_handle()
try:
    handle.write(line)
    handle.flush()
    if self._fsync:
        os.fsync(handle.fileno())
except BaseException as exc:
    self._poison(f"a durable write failed after the line reached the "
                 f"file ({type(exc).__name__}: {exc})")
    raise

self._window.append(commitment)      # <-- OUTSIDE the guard
self._remember(commitment)
return commitment
```

`self._window.append(commitment)` runs after the write has fully succeeded
(written, flushed, fsynced) and is not covered by the `except`. Any
exception it raises — the seal race above, or a future bug that produces an
out-of-order sequence — leaves a durably-written line whose sequence the
in-memory window never advanced past, so the *next* call reuses it.

New order — a second, separately worded guard immediately after the first
(kept separate rather than merged, because the two failure meanings are
different: the first block's message is specifically about the write; a
second block whose write *succeeded* needs to say so):

```python
handle = self._open_handle()
try:
    handle.write(line)
    handle.flush()
    if self._fsync:
        os.fsync(handle.fileno())
except BaseException as exc:
    self._poison(f"a durable write failed after the line reached the "
                 f"file ({type(exc).__name__}: {exc})")
    raise

try:
    self._window.append(commitment)
    self._remember(commitment)
except BaseException as exc:
    self._poison(
        f"the line reached the file and post-write in-memory "
        f"bookkeeping then failed ({type(exc).__name__}: {exc}); "
        "what is on disk may already hold this sequence and issuing it "
        "again would duplicate it")
    raise

return commitment
```

`self._remember` is inside the same guard because even an in-memory
`set.add` can raise (for example, `MemoryError`) after the durable line and
window sequence have advanced. Poisoning prevents a retry from silently
reusing the already-durable nonce.

This closes both the seal-race manifestation and the general class the
issue names: *any* future reason `CommitmentWindow.append` might refuse a
commitment after its bytes are already durable now poisons the store instead
of leaving it silently one sequence behind.

## 4. The `begin()` failure consequence

Requested explicitly, and it does **not** fully disappear — by design,
matching the module's own stated tradeoff:

A `begin()` call whose line reaches the file and is then refused by
`self._window.append` (site 2) still returns the exception to the caller —
the caller is told the run did not start. The line is nonetheless durably on
disk. On the next open, `_replay` treats it as a legitimate commitment (its
JSON is well-formed and it continues the chain from what is actually on the
window file), so it becomes a real, provable, eventually-sealed BEGIN for a
run the caller was told never started.

This is not a new defect introduced by the site-2 fix — it is the same
"the file is the ground truth, not the caller's exception" tradeoff `_append`
already made for I/O failures (commitlog.py's own recovery doc at
lines 439-445: *"Reopening the log is the recovery... a decision made from
evidence rather than a hopeful in-memory picture"*). What the fix changes is
that this window is now bounded and detectable: the store poisons
immediately, so it cannot happen more than once per process lifetime without
an operator restart, and `verify_chain()` / a reopen will show the extra
BEGIN rather than silently corrupting further sequences.

The issue also names a second-order effect of this: `runtime.py:1114-1117`
swallows the corresponding `end()` failure whenever the run is already
failing, making the unpaired BEGIN invisible to the caller too. That file is
outside this task's read scope (commitlog.py only) and is not touched by
this plan. It is flagged here as a follow-up worth its own issue once #129
lands, not folded into this fix — the two are independent surfaces
(commitlog.py's honesty about what is durable vs. runtime.py's exception
handling around it) and conflating them would widen this change's blast
radius past what it can be measured against.

## 5. The sweep (issue point 4) — every durable write in commitlog.py

"Durable write" here means: any statement that writes, flushes, fsyncs,
renames or truncates persistent storage, or that mutates in-memory state
which a later durable write's correctness depends on.

| Function | Durable write | What can raise immediately after (before the function/lock returns) | Poisoned today? | Verdict |
|---|---|---|---|---|
| `_atomic_write` (988-998, shared by #2 and #6) | temp-file write/flush/fsync, then `os.replace`, followed by directory sync | `_sync_directory`'s `os.open` is outside its inner `try/except`, and `os.close` in `finally` can raise; `os.fsync` itself is swallowed there. Before `os.replace`, a failure leaves `path` untouched; after it, the target is valid even if directory sync reports failure. | N/A — no poison flag exists at this level | **Safe by construction for callers that handle the exception.** The helper is whole-or-absent before rename, while a post-rename exception means a valid target may already exist. `_write_identity`/`__init__` fail closed, and `seal()` poisons the live log. |
| `_write_identity` (273-290) | `_atomic_write` of `identity.json` | The same post-rename `_sync_directory` exceptions as `_atomic_write`; nothing follows inside `_write_identity`. Its caller, `__init__`, then runs `_recover()` in the same guarded block. | No (no live object exists to poison — see next row) | **Safe.** A post-rename exception may leave a valid identity file, but `__init__` releases the lock and returns no object. On a later attempt the matching identity is idempotently accepted (lines 278-281 short-circuit). |
| `__init__`'s try around `_write_identity` + `_recover` (225-231) | orchestrates the two writes above and `_replay`'s truncate | Either raises ⇒ `except BaseException: self._release(); raise` (229-231) | N/A (object never returned) | **Safe.** A raised `__init__` can never hand the caller a usable, unpoisoned handle — the equivalent of poisoning is "the object doesn't exist." No gap here. |
| `_replay`'s truncate branch (421-426) | `handle.truncate` / `flush` / `os.fsync` on the torn-line window file | Nothing — last statement in `_replay`, called only from `_recover`, called only from `__init__`. | No, same reasoning as above | **Safe by the same construction-failure argument.** Note for the implementer: `truncate()` takes effect at the OS level immediately, before `flush`/`fsync` — if `fsync` alone raises, the file is already shortened. This is idempotent (re-running `_replay` on an already-clean file is a no-op branch, since `raw.endswith(b"\n")` will then be true) and not a correctness gap, only worth a one-line comment if touched. |
| `_open_handle` (474-481) | `path.open("a", ...)` (creates the window file when new) + directory sync | `path.open()` can raise before returning; after creation, `_sync_directory` can raise from `os.open` or `os.close` even though its `os.fsync` error is swallowed. This is before the commitment line write and before `_append`'s write guard. | No | **Safe for chain integrity.** No commitment has been handed to the file at this point; the caller sees the directory-sync exception rather than a phantom commitment. The created empty window file is harmless and recovery can reuse it. |
| `_append` (483-590) — the line write (573-579) | `handle.write` / `flush` / `os.fsync` | Guarded: `except BaseException: self._poison(...); raise` (580-586) | **Yes — already fixed by #112/dd2140a.** | **Verified safe** (existing test `test_a_failed_write_leaves_nothing_in_memory`, `tests/test_commitlog.py:76`, and `test_an_uncertain_durable_write_makes_the_log_unusable`, line 581). |
| `_append` — `self._window.append(commitment)` and `_remember(commitment)` (588-599, pre-fix) | Not themselves disk writes, but post-write in-memory steps a **completed** durable write's correctness depends on (advancing the sequence and recording nonce use) | **Unguarded today.** Either step can raise after the line is durable; the store was not poisoned. | **No — this is Site 2, §3.** | **Defect. Fixed by §3: both steps share the post-write poison guard.** |
| `seal()` — `self._window.seal(at)` (760, pre-fix) | Not a disk write; marks the window irreversibly sealed in memory, ahead of the durable write that must justify it | N/A itself; the *next* three steps depend on it | — | See next three rows — this is the memory mutation that (b)/(c)/(d)/(e) must not be allowed to outlive if any of them fails. |
| `seal()` — `_atomic_write` of the checkpoint (761-762, pre-fix) | checkpoint file write | **Unguarded today.** | **No — this is Site 1, §2.** | **Defect. Fixed by §2.** |
| `seal()` — `self._handle.close()` (763-765, pre-fix) | Closes the window file handle (no pending buffered bytes at the Python level, since every `_append` already flushed; still a syscall that can raise) | **Unguarded today**, and unguarded even by a fix that only wraps `_atomic_write` — this is the "second site, and the worse one" called out in §2. | **No.** | **Defect. Fixed by §2 (same guard, extended to cover (c) as well as (b)).** |
| `seal()` — `self._window = CommitmentWindow.following(checkpoint)` / `self._nonces.clear()` (766-767, pre-fix) | In-memory advance that all future `_append` calls trust | No known raise path (see §2) | N/A | **No known gap; recommended to bring inside the same guard for §2's fix as a zero-cost robustness measure, not because a raise is currently reachable.** |
| `close()` (900-906) | `self._handle.close()` at shutdown | If it raises, `self._closed` is never set and `self._release()` (the flock) never runs; the exception reaches the caller. | No | **Out of scope for #129.** This risks a stuck OS lock / an object that answers `closed is False` after a failed close — an availability/resource-leak concern, not a chain-integrity one, since every `_append` already fsyncs before this point and no memory-vs-disk divergence is created here. Worth its own issue if it ever bites; not folded into this fix (see §4's reasoning about not widening blast radius past what can be measured). |

## 6. Contract impact — none

- No change to `contract/guarantees.md`. §2 of the guarantees ("Replay and
  delivery semantics") and the crash-guarantee table in §II are about the
  runtime's `TraceSink`, not the embedder-configured `CommitmentLog`
  (ADR-021 decision 1: "nothing here is reachable from the runtime unless an
  embedder configures it"). This fix changes only when and how
  `CommitmentLog` refuses to continue after a failed durable write — it adds
  no new public method, no new parameter, and no new observable success
  path; it only closes a gap where a failure was *not* refusing.
- `tests/contract/` and `tests/compat/` are not touched — this bug and its
  fix live entirely below the compatibility surface and the conformance
  corpus; `CommitmentLog` is not part of either.
- `commitments.py` is not touched (§2's rejected alternative would have
  touched it; the guard-based fix does not).
- Existing tests that already assert current poison behavior
  (`test_an_uncertain_durable_write_makes_the_log_unusable`, line 581;
  `test_sealing_publishes_a_checkpoint_and_opens_the_next_window`, line 156;
  `test_numbering_survives_a_restart_across_a_seal`, line 169) exercise only
  the *successful* path of `seal()` and the *write-guard* path of `_append`
  — neither reaches the new guards, so they are expected to pass unchanged
  and serve as the regression backstop that the happy path is undisturbed.

## 7. Test and property plan (issue point 3, plus the sweep as a property)

All new tests go in `tests/test_commitlog.py` (not a frozen file) and follow
the file's existing convention: `monkeypatch.setattr("vitruvyan_motus.
commitlog.<name>", boom)` for module-level functions (`os.fsync`, as at line
600), or a bound-method override on an already-opened handle (as at line
90-94 for `handle.write`), whichever reaches the exact call being targeted.
`fsync=True` where the scenario is about `os.fsync`/`os.replace`; `fsync=
False` where it is about the write/close call itself.

**7.1 — Direct regression test for the issue's own repro** (closes issue
point 3 literally):

`test_a_failed_seal_poisons_rather_than_leaving_a_stale_window`
- open a log, `_run` once so the window is non-empty;
- monkeypatch `vitruvyan_motus.commitlog._atomic_write` (or, more precisely,
  `os.replace` inside it, to land the failure at the latest possible point
  and prove even a near-complete write is treated as untrusted) to raise
  `OSError`;
- call `log.seal(AT)`, assert it raises `OSError`;
- assert the log is now poisoned: `log.begin(...)`, `log.end(...)` and
  `log.seal(...)` each raise `CommitmentLogFork` matching `"stopped being
  writable"`;
- close, reopen on the same directory: assert no checkpoint file exists
  (`_atomic_write` never reached `os.replace`), the window is still open at
  index 0 with the original run's commitments intact, and a fresh
  `begin()`/`seal()` on the reopened instance produces contiguous sequences
  with no duplicates (mirror the assertions at lines 614-621 of the existing
  fsync test).

**7.2 — The "worse" second site named in §2**, proving the fix is not
merely `_atomic_write`-shaped:

`test_a_failed_handle_close_during_seal_also_poisons`
- same setup; let `_atomic_write` succeed genuinely (checkpoint lands on
  disk);
- monkeypatch the *open window handle's* `.close` to raise `OSError` (grab
  it via `log._open_handle()` before calling `seal`, same pattern as line
  84-94's `handle.write` override);
- call `seal(AT)`, assert it raises, assert the log is poisoned exactly as
  in 7.1;
- reopen: assert the checkpoint IS present and `_check_links` accepts it,
  the store opens window index 1 fresh, and `verify_chain()` returns 1 with
  no duplicate-sequence evidence anywhere. This is the case that a fix
  scoped only to `_atomic_write` would silently fail — it must fail loudly
  against the unfixed code (see 7.4).

**7.3 — Site 2 in isolation**, independent of how the window came to be
sealed underneath a live `_append` (so the test does not depend on 7.1/7.2's
mechanism, only on the guard itself):

`test_append_after_the_window_refuses_it_poisons_not_just_raises`
- open a log, `begin("r1", ...)` once;
- force the *next* append's in-memory step to fail without touching disk:
  monkeypatch `CommitmentWindow.append` (via the instance, `log._window.
  append`, or the class) to raise `ValueError("forced")` on the next call
  only (delegate to the original afterward is not needed — the call under
  test should fail and nothing after it runs);
- call `log.begin("r2", ...)`, assert it raises;
- **critically**, read `window-000000.jsonl` directly off disk and assert
  the `r2` line IS present (the write succeeded — this is what makes the
  scenario real rather than a restatement of the existing write-guard test);
- assert the log is poisoned (`"stopped being writable"`);
- reopen: assert the reopened `open_window.next_sequence` accounts for the
  `r2` line already on disk (i.e., equals `on-disk line count`, not
  `on-disk line count - 1`), and that issuing `begin("r3", ...)` on the
  reopened instance does **not** collide with `r2`'s sequence. This is the
  assertion that would fail hardest under the pre-fix code, since pre-fix
  the *caller* sees `r2` as failed while the file holds it as sequence N —
  reopening then must still show it counted once, not lost and not
  duplicated.

**7.4 — Mutation-probe, per AGENTS.md**: for each of 7.1–7.3, the
implementer neuters the corresponding guard (temporarily removes the specific
`try/except` this test targets, or reverts to the pre-fix ordering) and
confirms the test fails, with the specific failure being "no
`CommitmentLogFork`/wrong sequence on reopen" rather than an unrelated
error — then restores the fix and confirms it passes. This is not optional
per AGENTS.md ("Five tests in the 0.7 cycle passed for the wrong reason and
only this caught them").

**7.5 — The sweep, expressed as one property test** (issue point 4's "as a
property rather than a site"): parametrize a single test function over the
five "unguarded step" rows identified in §5 that are not already covered by
an existing test —

```
("seal: _atomic_write",        patches _atomic_write to raise),
("seal: handle.close",         patches the open handle's .close to raise),
("seal: window advance",       patches CommitmentWindow.following to raise),
("append: window.append",      patches CommitmentWindow.append to raise once),
("append: _remember",           patches _remember to raise once),
```

For each: perform one `begin()`/`end()`/`seal()` sequence up to the
injection point, trigger the failure, and assert the single shared
invariant — *either the object never came into existence (init-time
injection points, none remain unguarded per §5) or `log._poisoned is not
None` and a fresh reopen on the same directory produces a chain with
`sorted(sequences) == list(range(len(sequences)))` (no duplicate, no gap
beyond the deliberately-refused tail)*. This is the regression guard for
"a new unguarded step appears between the memory mutation and the return" —
per AGENTS.md's "leave a test that fails when a new member appears," a
future edit to `seal()` or `_append` that adds a statement after the memory
mutation without extending the `try` will fail this parametrized test only
if that statement is added to the parametrize list, which is the plan's
acknowledged limit: this closes the *currently known* members of the class
generally, not automatically every future one. State that limit in the test
docstring rather than implying more than the property proves, following the
project's own rule about not overclaiming closure (`_append`'s digest-fix
comment at commitlog.py:546-566 is the model for how to phrase this
honestly).

## 8. Order of implementation for `motus-implementer`

1. Site 2 first (`_append`'s second guard) — it is the smaller, more
   isolated change and several Site-1 tests (7.1, 7.2) depend on it being in
   place to observe "poisoned" rather than "silently desynced" once seal's
   own guard also fires.
2. Site 1 (`seal()`'s extended guard).
3. Tests 7.1–7.3, each proved against the pre-fix code per 7.4.
4. Property test 7.5.
5. Full suite + `verify_chain`/frozen-path guard via `motus-verifier`.
6. Adversarial round before merge, per the skill and per this issue's own
   comment thread ("under the Motus rules — adversarial round + mutation
   probe on the fix"). Do not merge while it is running.
