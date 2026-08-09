# FA-002 adversarial round — attack scripts

The scripts three independent adversarial agents used against the FA-002 fix
(issue #25) during the 0.8 cycle, on 2026-08-09.

Preserved on a branch rather than on main, as the 0.6 and 0.7 evidence was
(`3728fcb`, `5844b27`): they are not tests, CI does not run them, several
reproduce only probabilistically, and two of them mutate a copy of the runtime.
Their value is that they reproduce findings the suite could not.

Run them from the repository root with the project venv:

    .venv/bin/python audit/0.8-fa002-attacks/<script>.py

## The rounds

| prefix | lens | what it was pointed at |
|---|---|---|
| `r8_` | the public protocol and the contract | whether the normative corpus now says something false, and whether the new tests pass for the right reason |
| `fa002_` | the new code itself | every failure path inside `_start`, and whether a rejected start is observable |
| `conc` | concurrency and contention | the window between the claim and the rollback, at `setswitchinterval(5e-6)` |

## What the round found

Three defects **in the fix** (`4010210`), all corrected in `db0ceb7`:

- `fa002_03`, `conc01` — the rollback re-queued `_cancel_reason` rather than
  what the claim consumed, so a cancellation bound to a start that never ran
  was aimed at a later, unrelated run, and displaced a reason the operator had
  already queued. **Regression, introduced by the fix.** Both scripts now print
  `DID NOT REPRODUCE` / `LEAKED FORWARD: False`.
- `r8_untested_half`, `r8_mutate_full`, `r8_mutate` — two of the fix's three
  changed lines were pinned by no test: the regression test re-lodged the
  cancellation itself, so its final assertion held under either implementation.
- `conc05`, `conc06`, `conc06b` — `_active_attempt` was cleared *after* the
  lifecycle claim was freed, an unsynchronised cross-run store that blanks the
  attempt a `run_cancelled` must name. `conc06` drove it into a trace the
  project's own validator refuses under **T6**; `conc06b` is its control.
  Observed 1 time in 2000 rounds at `setswitchinterval(5e-6)`, 0 at the CPython
  default. Pre-existing. `conc06` now prints `contract validator: []`.

Three defects **left open**, each with an issue:

- `r8_abandoned_stream`, `r8_abandoned_astream`, `fa002_02`, `conc02`, `conc03`
  — `_release_if_never_started` destroys a queued cancellation and leaves
  `_has_started` up. FA-002's shape at a second site. Deterministic, no
  concurrency. Found by all three agents.
- `fa002_01`, `conc07`, `r8_concurrent_cancel` — `cancel()` returns `True` for a
  request that binds to nothing and is never reported as missed. The window is
  `fa002_04`-measured at 35 µs (3 nodes) to 1.1 ms (400 nodes). Pre-existing.
- `r8_stale_fingerprint` — a refused start leaves `_identity_cache` half
  committed, and `_code_fingerprint` is then never recomputed: the header
  records configuration the run did not use, and nothing downstream can see it.

## Attacked and found clean

`fa002_05`, `r8_evidence_surface` — no sink header and no listener record is
emitted from a refused start, so calling it "a run that never began" is truthful
on the observation surfaces. `conc08` — the overlapping-run guard raises before
the claim and cannot reach the rollback (50 re-entrant refusals, thousands
cross-thread).

`conc04_stress.py` is a general harness: it runs the contract validator over
every trace produced under a hostile switch interval. It found `conc06`'s
violation without being aimed at it, and is the reason issue #66 proposes it as
a CI job rather than an attack script.
