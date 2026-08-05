# Vitruvyan Motus 0.6.1 — Final Independent Re-Audit of PR #23

**Reviewer:** independent adversarial reviewer (Claude Opus 5).
**Scope:** does `81db21c` close RA-001…RA-004 without introducing new
CRITICAL or HIGH defects?
**Date:** 2026-08-05

---

## 1. Executive verdict

> # PASS WITH NON-BLOCKING FINDINGS

| Severity | Count |
|---|---:|
| CRITICAL | **0** |
| HIGH | **0** |
| MEDIUM | **0** |
| LOW | **2** |
| NOTE | 5 |

All four re-audit findings are **CLOSED**, reproduced independently rather than
taken on the new tests' word. RA-001 — the HIGH regression that failed the
previous gate — was attacked through eight distinct routes plus 700 forced
races and did not reproduce once.

The two remaining findings are LOW: a contract-named error type missing from
the package's public surface, and a queued cancellation silently dropped when
the run it was queued for fails to start. Neither is release-blocking; both are
one-line fixes that can be scheduled without gating 0.6.1.

---

## 2. Environment and evidence

### 2.1 Provenance

```console
$ git rev-parse HEAD
81db21c39c972addd886a0aba91912dad96beaba        # CONFIRMED == declared PR head

$ git log --oneline 746b91b..HEAD
81db21c fix(motus): close v0.6.1 re-audit findings

$ git diff --stat 746b91b..HEAD
 README.md                                 |   3 +-
 adr/ADR-008-v0.6.1-reaudit-corrections.md |  75 +++++++++++
 contract/guarantees.md                    |   9 +-
 contract/node-protocol.md                 |   7 +
 contract/trace.v1.schema.json             |   2 +-
 src/vitruvyan_motus/errors.py             |  10 ++
 src/vitruvyan_motus/observers.py          |  13 +-
 src/vitruvyan_motus/runtime.py            |  69 ++++++++---
 src/vitruvyan_motus/trace.py              |   8 +
 tests/test_reaudit_findings.py            | 140 ++++++++++++++++++++
 10 files changed, 311 insertions(+), 25 deletions(-)

$ git diff --stat origin/main...HEAD | tail -1
 41 files changed, 6059 insertions(+), 153 deletions(-)
```

### 2.2 Environment

| | |
|---|---|
| OS | Linux 6.8.0-124-generic (Ubuntu), x86_64 |
| CPU | AMD EPYC Processor (with IBPB), 8 vCPU |
| Interpreter | **CPython 3.12.3** |
| CI reference (run 30991900678) | CPython 3.10.12, Ubuntu |

**Declared deviation.** Python 3.10 is not installed on this host. Functional
results are interpreter-independent in the ranges exercised. **No new timing was
substituted for the committed evidence**: §6 re-derives the published SLO
aggregates from the committed raw runs, which is host-independent. This is an
environment limitation, not a product failure.

### 2.3 Commands and gate results

```console
python3 -m venv .venv
.venv/bin/pip install -e ".[test]" -c constraints/test.txt
```

| Gate | Command | Result |
|---|---|---|
| Full suite | `pytest tests/ -q` | **560 passed, 14 skipped, 0 failed** (31.4 s) |
| New regressions | `pytest tests/test_reaudit_findings.py -q` | **9 passed** |
| Prior audit corpus | `pytest tests/adversarial/ -q` | 92 passed |
| Compat + inherited | `pytest tests/compat/ tests/contract/ -q` | 26 passed |
| Frozen-path guard | `python tools/check_frozen_paths.py origin/main HEAD` | **PASS** (exit 0) |
| SLO gate | `python benchmarks/check_slo_baseline.py --candidate benchmarks/candidate-v0.6.1-epyc-py310.json` | **PASS**, all 10 rows |
| Wheel + isolation | `python -m build --wheel`; install into a clean venv | **PASS** |

The CI-reported `560 passed / 14 skipped / 0 failed` is **confirmed exactly**.
The 14 skips are infrastructure-gated (1 postgres extra, 5 PostgreSQL, 8
Qdrant). The 20 warnings are `datetime.utcnow()` deprecations from the
byte-preserved legacy `axis`-era tests only. Every CI claim was re-executed
locally rather than read from the PR page.

### 2.4 Working tree before attacks

```console
$ git status --short --branch
## HEAD (no branch)
```

Clean detached checkout at the exact PR head.

---

## 3. Closure matrix — RA-001 … RA-004

| Finding | Independent reproduction attempt | Mechanism inspected | Fresh attacks | Verdict |
|---|---|---|---|---|
| **RA-001** HIGH — a stale queued cancellation silently cancels a later run | **Did not reproduce.** The exact minimal repro that failed at `746b91b` (`with runtime.stream(...)` exhausted, then `runtime.run(...)`) now yields `status=completed, nodes=2` | `StreamDriver.__next__` wraps `next(self._iterator)` and sets `_closed = True` on **any** `BaseException`, so normal exhaustion and node failure both finish the driver and make `close()`/`__exit__` a no-op. `Runtime.cancel()` is now guarded by an `RLock`, returns `bool`, and refuses to queue once `_has_started` is set. `_start` consumes the pending reason under the same lock; both the `except BaseException` path and `_managed_execute`'s `finally` clear `_cancel_reason` | with-block exhaustion; explicit `close()` after exhaustion; exceptional exhaustion via `NodeFailed`; with-block over a raising node; mid-stream break + `close()` (must still cancel *that* run — it does); double `close()`; `next()` after `close()`; abandoned driver + GC finalisation; two concurrent drivers; 50 sequential runs; **500 barrier-forced cancel/start races**; **200 cancel/terminal races**; 4 threads × 188,145 concurrent `cancel()` calls during one run | **CLOSED** |
| **RA-002** MEDIUM — `in-memory` sink open-failure ignored while write-failure aborted the run | **Did not reproduce.** Both failure modes now behave identically | `_ObservationHub.persist` moves the `_async_failure` re-raise **above** the in-memory short-circuit, so a stored open failure surfaces at the first record. `guarantees.md` invariant II amended: "A sink is required when the profile requires one **or when the caller explicitly supplies one**." The header now discloses `{flush_interval_ms: 0, chunk_records: 1}` for non-buffered attached sinks, and the trace schema description was amended to permit and explain it | open-failure × 3 profiles; write-failure × 3 profiles; header disclosure × 3 profiles + the no-sink control; delivered-record count and causal order vs the trace; sink-failure evidence re-validated in JSON and JSONL; `chunk_records=1000` under `in-memory` | **CLOSED** |
| **RA-003** MEDIUM — the rejection-absence sentinel was lost across `pickle` | **Did not reproduce.** `evidence` stays absent through every mechanism tried | `_Missing.__reduce__` returns `(_restore_missing, ())`; `_restore_missing()` returns the module singleton, so unpickling in any process yields that process's `_MISSING` | `copy`, `deepcopy`, `deepcopy×3`; **pickle protocols 2, 3, 4 and 5**; the bare sentinel through each protocol; `Rejection` nested inside dict/list/tuple; **multiprocessing `spawn`, `fork` and `forkserver`** round trips; an end-to-end run writing an unpickled `Rejection`, validated in both encodings; a user-constructed `_Missing()` still refused | **CLOSED** |
| **RA-004** LOW — `motus_config()` obligations unstated; failures untyped and untraced | **Did not reproduce as an untyped error.** Failures are now `NodeConfigurationError`, a `MotusError` | `_refresh_identity` wraps any `_node_identity_parts` exception as `NodeConfigurationError(name, exc)`. `contract/node-protocol.md` §6.3 now states `motus_config()` MUST be pure, total, cheap and strict-JSON-valued, and that failure is reported before `run_started` | raising config (construction **and** per-run); non-JSON config; side-effecting config; mutable config changed and reverted across runs; `issubclass(..., MotusError)`; `runtime.trace is None` after the failure | **CLOSED** (see FA-001 for its public-surface gap) |

**On the one rewritten reproduction.** The prior audit noted that
`test_adv_008` had had its assertions inverted between `3728fcb` and `746b91b`.
That remains the only rewritten reproduction, it is accompanied by the
corresponding `guarantees.md` §6 amendment and ADR-007 §8, and it is exactly the
correction direction the 0.6 audit itself named. No further test was weakened in
`81db21c`: the nine new tests in `tests/test_reaudit_findings.py` all assert the
*expected* behaviour (`status == "completed"`, `cancel() is False`,
`"evidence" not in ...`), not the observed defect.

---

## 4. New findings

### FA-001 — LOW — `NodeConfigurationError` is named by the contract but is not in the public API

**Affected files.** `src/vitruvyan_motus/__init__.py` (imports nine names from
`errors`, not this one; `__all__` unchanged at 41 entries) versus
`src/vitruvyan_motus/errors.py:101` and `errors.__all__`.

**Violated guarantee.** `contract/node-protocol.md` §6.3 — a **normative**
surface — states: *"Failure is reported as `NodeConfigurationError` before
`run_started`."* ADR-008 §4 repeats it. `README.md` §"Native package surface"
states: *"The public API is explicitly listed in `vitruvyan_motus.__all__`"* and
lists the failure types. A type a consumer is normatively told to handle is not
on that list.

**Minimal reproduction.**

```console
$ python -c "from vitruvyan_motus import NodeConfigurationError"
ImportError: cannot import name 'NodeConfigurationError' from 'vitruvyan_motus'
```

```
issubclass(NodeConfigurationError, MotusError): True
'NodeConfigurationError' in vitruvyan_motus.__all__: False
hasattr(vitruvyan_motus, 'NodeConfigurationError'): False
contract/node-protocol.md names it: True
```

**Expected.** `from vitruvyan_motus import NodeConfigurationError` succeeds, as
it does for every other error the contract names.

**Actual.** `ImportError`. The type is reachable only as
`from vitruvyan_motus.errors import NodeConfigurationError`, or by catching the
`MotusError` base.

**User impact.** Low and fail-fast: a consumer following node-protocol §6.3 gets
an immediate `ImportError` at their integration point, with two obvious
workarounds. No runtime behaviour is affected.

**Why existing tests missed it.** `tests/test_motus_packaging.py` verifies wheel
contents, metadata, zero dependencies and the `axis` boundary — it does **not**
pin `__all__`. The PR's own `tests/test_reaudit_findings.py` imports the type
from `vitruvyan_motus.errors`, the private path, so it never exercises the
public one.

**Bounded remediation direction (not implemented).** Add
`NodeConfigurationError` to the `from vitruvyan_motus.errors import (...)` block
and to `__all__` in `src/vitruvyan_motus/__init__.py`, add it to README's
failures list, and add a test asserting that every error type named in
`contract/` resolves from the package root.

---

### FA-002 — LOW — a queued cancellation is silently dropped, and cannot be re-queued, when the run it was queued for fails to start

**Affected file and function.** `src/vitruvyan_motus/runtime.py` → `_start()`
(lines 302–312 set `_has_started = True` and consume `_pending_cancel_reason`
*before* the `try`; lines 364–369 clear `_cancel_reason` on `BaseException` but
never restore the pending request or roll back `_has_started`) together with
`Runtime.cancel()` (lines 252–268).

**Violated guarantee.** ADR-008 §1: *"`Runtime.cancel()` returns whether it
bound the request … it may queue cancellation before the Runtime's first run."*
Here `cancel()` returned `True`, nothing was cancelled, no run occurred, and the
request cannot be reinstated.

**Minimal reproduction** (`.attack/f02_sinks_rejection_config.py`, G2) — an
ordinary caller mistake, no threads:

```python
runtime = Runtime(SPEC, nodes)
assert runtime.cancel("operator shutdown") is True     # queued
try:
    runtime.run(State.empty("x"), run_id="x" * 201)    # 201 chars -> ValueError
except ValueError:
    pass
runtime.cancel("retry shutdown")                       # -> False, cannot re-queue
runtime.run(State.empty("y")).status                   # -> "completed", uncancelled
```

```
run_id too long        queued=True start=ValueError  requeue=False next_run=completed
state is not a State   queued=True start=TypeError   requeue=False next_run=completed
```

The same happens when `_refresh_identity` raises `NodeConfigurationError`
(`.attack/f01_cancellation_lifecycle.py`, F12).

**Expected.** Either the queued request survives a start that never produced a
run, or `cancel()` remains able to queue because no run has actually begun.

**Actual.** `_has_started` is latched by an attempt that produced no
`run_started`, the pending reason is consumed and then cleared, and every later
idle `cancel()` returns `False`.

**User impact.** Low. The start failure raises loudly, so the caller knows the
run did not happen; cancelling an *active* run still works; and constructing a
new `Runtime` restores queueing. The residual risk is a supervisor that queues a
shutdown, hits a validation error, retries, and runs uncancelled.

**Why existing tests missed it.** `test_cancel_before_first_run_is_bound_once`
only covers a start that succeeds; no test makes `_start` raise after the
lifecycle prologue.

**Bounded remediation direction (not implemented).** In `_start`'s
`except BaseException` handler, restore `_pending_cancel_reason` and roll back
`_has_started` when the failure occurred before `run_started` was emitted — or,
equivalently, latch `_has_started` only once the header exists. Add a regression
test that queues a cancellation, forces a `ValueError` from `run_id`, and
asserts the request is still queueable.

---

### Notes (no demonstrated defect)

- **NOTE-1 — `cancel()` can return `True` for a run that then completes.** A
  listener firing on `run_completed` calls `cancel()` while `_running` is still
  true, so the request binds — but no checkpoint remains and
  `_managed_execute`'s `finally` clears it. The docstring's wording ("bound to
  the active run") is literally satisfied, nothing leaks, and the trace honestly
  reports completion. Observed only from inside the terminal notification; 200
  routing-gated races produced `False` every time, correctly.
- **NOTE-2 — pickling any `_Missing` instance yields the singleton.** A
  user-constructed `_Missing()` is correctly refused at the write boundary, but
  the *same object round-tripped through pickle* becomes `_MISSING` and is read
  as "absent" instead of refused. `_Missing` is private and no public path
  constructs it.
- **NOTE-3 — `motus_config()` is evaluated at every run start**, so a
  side-effecting implementation mints a new `code_fingerprint` per run
  (confirmed: 3 distinct digests in 3 runs). This is now *explicitly forbidden*
  by node-protocol §6.3, so it is documented behaviour rather than a defect.
- **NOTE-4 — three of four Motus SLO rows remain above target inside the +25 %
  ceiling** (51.2 vs ≤45 µs; 3.44 vs ≤3.25 ms; 1.749 vs ≤1.5x). Carried
  unchanged from ADR-007; `81db21c` touches no benchmark.
- **NOTE-5 — the cyclic-execution bound remains per run segment.**
  `ReplayEngine.resume()` starts a fresh activation counter, so repeated resume
  can advance a cycle beyond `max_transitions` in total. Carried from the prior
  re-audit; each individual trace is valid and resume is explicit and
  fail-closed.

---

## 5. Fresh attacks attempted

Grouped totals. Every attack listed was executed; "passed" means the
implementation resisted.

| Area | Attacks | Passed | Outcome |
|---|---:|---:|---|
| 1–2. StreamDriver exhaustion (normal, exceptional, with-block, explicit close, double close, `next()` after close, abandoned + GC, two live drivers) | 12 | 12 | RA-001 closed |
| 3. Cancellation before first run / during a run / after a completed run / from a node / from a listener at all five record kinds / return-value semantics | 16 | 15 | NOTE-1 |
| 4. Concurrent races: 500 barrier-forced cancel-vs-start, 200 cancel-vs-terminal, 4 threads × 188,145 concurrent `cancel()`, reentrant cancel from a listener | 704 rounds | all | no leak, no deadlock, no false binding |
| 5. Runtime reuse: 50 sequential runs; reuse after stream, after cancellation, after `NodeFailed`, after a failed `_start` | 12 | 11 | FA-002 |
| 6. Sink open/write failure × `in-memory`/`buffered`/`synchronous` | 6 | 6 | RA-002 closed, symmetric |
| 7. Header disclosure × 3 profiles + no-sink control; schema and H1 conformance of `flush_interval_ms: 0` | 8 | 8 | closed |
| 8–9. Rejection through copy, deepcopy×3, pickle protocols 2–5, bare sentinel, nested containers, `spawn`/`fork`/`forkserver`, end-to-end run, user-built sentinel | 18 | 17 | RA-003 closed; NOTE-2 |
| 10–11. `motus_config`: raising, non-JSON, side-effecting, mutable, reverted; fingerprint stability and refresh; `NodeConfigurationError` type, base class and public reachability | 14 | 13 | RA-004 closed; FA-001 |
| 12. Trace/schema/validator agreement over 12 changed-path executions, JSON **and** JSONL, verdicts compared | 24 | 24 | zero violations, zero divergences |
| 13. Packaging: wheel contents, `py.typed`, `axis` exclusion, zero dependencies, clean-venv import outside the checkout, `__all__` diff vs `origin/main`, frozen-corpora diff | 12 | 12 | clean |
| 14. SLO evidence: artifact SHA-256, 5-run recomputation of 4 aggregates, completeness, environment identity, ceiling checks, benchmark-immutability since `746b91b` | 16 | 16 | clean |
| 15. Fresh-defect sweep on `81db21c`: RLock reentrancy, GC finalisation under the lock, listener-cancel + sink ordering, replay/resume after the changes | 10 | 10 | clean |
| **Totals** | **~850 executions across 148 distinct attacks** | | **2 LOW findings** |

Reproductions live in the untracked scripts
`.attack/f01_cancellation_lifecycle.py`,
`.attack/f02_sinks_rejection_config.py`, `.attack/f03_mp_rejection.py` and
`.attack/f04_evidence_packaging_fresh.py`.

---

## 6. Performance and evidence integrity

`benchmarks/` is **byte-identical to the reviewed `746b91b`** — `81db21c`
changes no benchmark, no candidate document and no gate threshold.

| Check | Result |
|---|---|
| Committed candidate SHA-256 | `07bcc8d6…3ca3ca` — matches ADR-007 exactly |
| Independent recomputation from the 5 raw runs | per-node **51.2301 µs**, no-op **3.43542 ms**, cold ratio **1.7490x**, superlinearity **3.4693 %** — identical to the published values |
| Inside every ADR-006 ceiling (target +25 %) | **True** (56.25 / 4.0625 / 1.875 / 12.5) |
| Trace completeness across all runs | 3,002 records, 0 declaration violations, every run |
| Runner identity across all runs | `vitruvyan-motus/0.6.1`, Python `3.10.12`, AMD EPYC 9V74, gc enabled, repeats 7 |
| `MOTUS_CANDIDATE_TARGETS` / `MOTUS_CANDIDATE_TOLERANCE` | unchanged from `origin/main` — **no hidden relaxation** |
| Arithmetic sanity of the ratio | 1.749x > 1.0, consistent with a `dumps`+`loads`; the retired 0.788x was arithmetically impossible |

---

## 7. Compatibility and packaging

| Check | Result |
|---|---|
| `axis` excluded from the wheel | **PASS** — 12 members, all `vitruvyan_motus/`; `find_spec("axis") is None` from a clean venv outside the checkout |
| Zero runtime dependencies | **PASS** — the isolated venv lists only `vitruvyan-motus` |
| Public API | **PASS for stability** — `__all__` is 41 names, **byte-identical to `origin/main`**; nothing added or removed; all resolve. (FA-001 is that a *newly contract-named* type was not added.) |
| Version surfaces | **PASS** — `__version__ == "0.6.1"`, `TRACE_SCHEMA_VERSION == "1.1.0"`, distinct |
| Frozen contract paths | **PASS** — `git diff origin/main...HEAD -- tests/contract/ tests/compat/` is empty; guard exits 0 |
| Terraveler + inherited corpus | **PASS** — 26 passed; `compat.py` untouched by the whole PR |
| Schema versions | **PASS** — GraphSpec 1.0.0, trace 1.1.0; the amendments are description-level plus the already-permitted optional `run.sink` disclosure |

---

## 8. Repository integrity

```console
$ git rev-parse HEAD
81db21c39c972addd886a0aba91912dad96beaba

$ git status --short --branch
## HEAD (no branch)
?? .attack/f01_cancellation_lifecycle.py
?? .attack/f02_sinks_rejection_config.py
?? .attack/f03_mp_rejection.py
?? .attack/f04_evidence_packaging_fresh.py
?? audit/MOTUS-0.6.1-FINAL-GATE-REPORT.md

$ git diff --stat
(empty)

$ git stash create
(empty — Git cannot build a stash commit because no tracked file differs)
```

**Tracked files changed by this audit:** none.
**Temporary untracked files created:** four attack scripts plus this report.
**Confirmation:** no commit, push, PR update, merge, tag or publication
occurred. No production code, contract, test, ADR, workflow, benchmark or
documentation file was modified, and no finding was fixed in this session.

---

## 9. Release recommendation

Zero CRITICAL and zero HIGH findings. The four re-audit findings are closed and
independently verified; the two residual findings are LOW, fail-fast, and
individually one-line corrections.

`RELEASE GATE: PASS`
