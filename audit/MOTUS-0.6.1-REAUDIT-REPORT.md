# Vitruvyan Motus 0.6.1 — Independent Adversarial Re-Audit of PR #23

**Reviewer:** independent adversarial reviewer (Claude Opus 5), acting against the
remediation, not for it.
**Scope:** does PR #23 close the release-blocking findings of the 0.6 audit
without introducing new CRITICAL or HIGH defects?
**Date:** 2026-08-05

---

## 1. Executive verdict

> # FAIL

| Severity | Count |
|---|---:|
| CRITICAL | **0** |
| HIGH | **1** |
| MEDIUM | **2** |
| LOW | **1** |
| NOTE | 4 |

**May PR #23 proceed toward ready-for-review?** Not in its current state. One
HIGH finding (`RA-001`) is a *regression introduced by the PR itself* — verified
absent at base `2a860a7` and present at head `746b91b`. It is reachable through
the documented public API with no threads, no timing and no private attributes.

**May Motus 0.6.1 be merged and released?** **No.** The stated release gate is
"cannot be merged or released if this audit finds any unresolved CRITICAL or
HIGH issue." `RA-001` is unresolved.

**What the remediation got right, stated plainly.** All three original
release-blocking defects are genuinely closed, and closed for the right reason
rather than by test adjustment:

- the evidence-free `Rejection` sentinel is now identity-stable through
  arbitrarily nested `copy`/`deepcopy`, and `evidence` is simply *absent* from
  every wire form;
- `max_transitions` now bounds routing-producing activations, verified
  exhaustively at limits 1–8 across both policies with zero runtime/validator
  disagreement;
- `to_dict()` genuinely re-materializes — 9 calls produce 9 encoder invocations
  — and the corrected 1.749x ratio is arithmetically possible where the retired
  0.788x was not.

Eight of the nine original findings are CLOSED. Every published performance
number recomputes **exactly** from the committed raw evidence. No target,
tolerance, frozen corpus or existing assertion was weakened. The single
contract narrowing (Listener) is properly ADR-backed rather than a test
rewrite to force a pass.

The failure is narrow and fixable: the fix for the *lowest*-severity
cancellation finding introduced a higher-severity one.

---

## 2. Environment and evidence

### 2.1 Provenance

```console
$ git rev-parse HEAD
746b91b9f6da5c8020ea1da9bfb90265a3b557c9        # == declared PR head

$ git log --oneline origin/main..HEAD
746b91b docs(motus): accept ADR-007 remediation      # == declared ADR acceptance commit
81fcb2d perf(motus): bind corrected v0.6.1 baseline  # == declared reviewed remediation SHA
1744ab6 perf(motus): bound state lookup without taxing each append
24bdf69 ci(motus): trigger v0.6.1 characterization
4f8a512 fix(motus): remediate v0.6 adversarial audit
3728fcb test(motus): preserve 0.6 adversarial audit evidence   # the 0.6 audit evidence
```

All four SHAs supplied in the brief exist and resolve as described. The prior
audit's evidence at `3728fcb` is an ancestor of this PR head, so the original
findings and their reproductions were inspected in place rather than
reconstructed.

`git diff --stat origin/main...HEAD`: 37 files, +5756/−136.

### 2.2 Environment

| | |
|---|---|
| OS | Linux 6.8.0-124-generic (Ubuntu), x86_64 |
| CPU | AMD EPYC Processor (with IBPB), 8 vCPU |
| Interpreter | **CPython 3.12.3** |
| Documented reference profile | **CPython 3.10.12, AMD EPYC 9V74 80-Core** |

**Declared deviation.** Python 3.10 is not installed on this host
(`which python3.10` → not found), so the documented reference environment could
not be used. Consequences, stated explicitly:

- Functional results are unaffected — the suite and every attack are
  interpreter-independent in the ranges exercised.
- **No new timing was substituted for the committed evidence.** Section 6
  verifies the performance claims by *recomputing the published aggregates from
  the committed raw runs*, which is host-independent, plus one arithmetic
  argument that needs no execution at all. Timings measured here are labelled as
  such and used only to establish shape (cold vs cached, bounded vs growing),
  never to assert or refute an SLO number.
- This is an **environment limitation, not a product failure**.

### 2.3 Dependency installation

```console
python3 -m venv .venv
.venv/bin/pip install -e ".[test]" -c constraints/test.txt
```

Resolved: `pytest==8.4.2`, `pytest-asyncio==0.24.0`, `jsonschema==4.26.0`,
`attrs==26.1.0`, `referencing==0.37.0`, `rpds-py==0.30.0`,
`jsonschema-specifications==2025.9.1`, `packaging==26.2`, `pluggy==1.6.0`,
`iniconfig==2.3.0`, `Pygments==2.20.0`, `typing_extensions==4.16.0`,
`vitruvyan-motus==0.6.1` (editable).

### 2.4 Gate results reproduced

| Gate | Command | Result |
|---|---|---|
| Full suite | `pytest tests/ -q` | **551 passed, 14 skipped, 0 failed** in 36.4 s |
| Frozen-path guard | `python tools/check_frozen_paths.py origin/main HEAD` | **PASS** (exit 0) |
| SLO gate | `python benchmarks/check_slo_baseline.py --candidate benchmarks/candidate-v0.6.1-epyc-py310.json` | **PASS**, all 10 rows |
| Compat + inherited corpus | `pytest tests/compat/ tests/contract/ -q` | 26 passed |
| Prior audit's own suite | `pytest tests/adversarial/ -q` | 92 passed |
| Wheel build + isolation | `python -m build --wheel`, install into a clean venv | **PASS** |

The independently reported "551 passed / 14 skipped / 0 failed" is **confirmed
exactly**. The 14 skips are infrastructure-gated (1 postgres extra, 5
PostgreSQL, 8 Qdrant); none is Motus-native. The 20 warnings are
`datetime.utcnow()` deprecations from the byte-preserved legacy `axis`-era
tests only.

CI claims were treated as claims: each was re-executed locally against the
checked-out head rather than read from the PR page.

### 2.5 Dirty/untracked state before attacks

```console
$ git status --short --branch
## HEAD (no branch)
```

Clean. The prior audit's uncommitted artifacts were removed from the working
tree first (they are tracked upstream at `3728fcb`, so nothing was lost).

---

## 3. Original finding closure matrix

| Original finding | Reproduced original failure | Remediation mechanism | Fresh attacks | Verdict |
|---|---:|---|---|---|
| **MOTUS-ADV-002** CRITICAL — evidence-free `Rejection` leaks a non-JSON sentinel into a "successful" run's trace | yes, at `2a860a7` | `trace.py`: `_MISSING` is now a `_Missing` singleton defining `__copy__`/`__deepcopy__` returning `self` | copy, deepcopy, deepcopy×3, pickle; 5 sentinel-lookalike user values; full run + `to_json`/`to_jsonl`/`to_dict`/snapshot/bundle/playback/explain; retry, exploration-continue and seeded-initial paths; real JSON sink under `synchronous`; JSON↔JSONL validator equivalence | **CLOSED** (residual `RA-003` on the pickle path) |
| **MOTUS-ADV-001** HIGH — cyclic EXPLORATION run unbounded, no terminal record | yes, at `2a860a7` | `runtime.py`: counter renamed to `routed_activations`, incremented on `commit` **and** on exploration `continue`; `validate.py` E11 now requires exactly that many activations; schema + ADR-007 §2 aligned | all-raising cycle; mixed commit/continue/retry; retry exhaustion; self-loop; multi-node SCC; limits 1,2,3,4 and 1–8 × both policies (16 runs); END selected exactly at the limit; forged activation counts; spec claiming 3/4/5 | **CLOSED** |
| **MOTUS-ADV-003** HIGH — published trace-preparation ratio measured a memoisation cache hit | yes, from the committed 0.6.0 evidence arithmetically | `trace.py`: `to_dict()` = `json.loads(json.dumps(self._view()))`, never reading `_json_cache`; `bench_motus.py` reports cached `to_json` as a separate named path; guarantees.md retires 0.788x as historical | encoder-invocation counting (9 calls → 9 encodes); mixed `to_json`/`to_dict` order (2 encodes + 3 materializations = 4); returned-document isolation; cross-order determinism; full recomputation of the new candidate | **CLOSED** |
| **MOTUS-ADV-004** MEDIUM — keyed state reads scanned the whole log | yes, at `2a860a7` | `state.py`: persistent `(collection, key)` index over completed chunks; partial chunk scanned directly; index copied only when a 64-chunk closes | sizes 1/63/64/65/127/128/129/200 × 3 probe positions; comparison counting; duplicate keys; identical values; fact/decision namespaces; 138 retained snapshots re-checked after later commits; branching from an old snapshot; scan/snapshot order; indexed-decision origin exactness | **CLOSED** |
| **MOTUS-ADV-009** MEDIUM — pre-run `cancel()` silently discarded | yes, at `2a860a7` | `runtime.py`: `cancel()` routes to `_pending_cancel_reason` when idle; `_start` consumes it once | queued cancel consumed exactly once; three stacked calls collapse to one; cancel from a node; cancel from a listener; cancel after a `BaseException` escape; **`StreamDriver.close()` / `with`-block exit after a completed stream**; **concurrent `cancel()` during run start-up, 300 rounds** | **PARTIALLY CLOSED** — original defect fixed, fix introduces `RA-001` (HIGH) |
| **MOTUS-ADV-005** LOW — defaulted second positional parameter received `RunContext` | yes, at `2a860a7` | `runtime.py`: `_accepts_context` additionally requires `positional[1].default is empty` | 16 signature shapes: plain/required/defaulted/keyword-only, callable instances, bound methods, decorated, two partial shapes, `*args`, `**kwargs`, zero-arg, three-arg, async | **CLOSED** |
| **MOTUS-ADV-006** LOW — stale `code_fingerprint` on a reused `Runtime` | yes, at `2a860a7` | `runtime.py`: config material recomputed at every `_start`; immutable source identity cached via `lru_cache`; digest recomputed only when material changed | unchanged config keeps the digest; changed config moves it; reverting restores it; in-place nested mutation detected; partial stability; per-run replay constraints | **CLOSED** (see `RA-004` for the new obligation this places on `motus_config()`) |
| **MOTUS-ADV-007** LOW — a supplied sink was silently discarded under `in-memory` | yes, at `2a860a7` | `observers.py`: `bind` gates on `sink is None`; `persist` forwards under `in-memory` when a run sink exists | sink opened under all three profiles; record count and causal order vs the trace; sink reuse across 3 runs; open-failure and write-failure under all three profiles | **CLOSED** (residual `RA-002`) |
| **MOTUS-ADV-008** LOW — "nothing a listener does can affect execution, by construction" was overstated | yes, at `2a860a7` | **Contract narrowed**, not code changed: guarantees.md §6 now promises data/failure isolation and states that synchronous delivery can delay the runner and that Runtime-holding code may cancel; ADR-007 §8 records the decision | delivered-record mutation; `SystemExit` from a listener; failure counting; the same listener registered twice; per-listener `seq` order; listener cancellation trace-visibility | **CLOSED** — legitimate ADR-backed narrowing. This is the one original reproduction whose assertions were rewritten; it is accompanied by the corresponding contract amendment, which is exactly the correction direction the 0.6 audit named. It is **not** a test weakened to force a pass |

---

## 4. New findings

### RA-001 — HIGH — a stale queued cancellation silently cancels a later, unrelated run

**Affected file and function.**
- `src/vitruvyan_motus/runtime.py` → `Runtime.cancel()` (lines 241–247): routes to
  `self._pending_cancel_reason` whenever `self._running` is false, with nothing
  that ever expires the request.
- `src/vitruvyan_motus/observers.py` → `StreamDriver.close()` (lines 264–274):
  unconditionally calls `self._cancel(reason)` even when the underlying
  generator is already exhausted; `StreamDriver.__exit__` calls it automatically.

**Violated contract or guarantee.** ADR-007 §4: *"The run emits `run_started`,
then `run_cancelled`, executes no node, and consumes the request. **A later run
is not cancelled unless another request is made.**"* Also guarantees.md §6,
which describes cancellation through a `StreamDriver` as landing "as the
trace-recorded `run_cancelled`" for *that* run.

**Minimal reproducible attack** (`.attack/r06_RA001_minimal.py` — public API
only, no threads, no timing, no private attributes):

```python
runtime = Runtime(SPEC, {"a": the_only_node})

with runtime.stream(State.empty("first")) as driver:   # documented usage
    for _ in driver:
        pass                                            # run 1 completes normally

second = runtime.run(State.empty("second"))             # run 2 on the same Runtime
```

```console
$ python .attack/r06_RA001_minimal.py
run 1 : status = completed (streamed) | nodes executed = 1
run 2 : status = cancelled | nodes executed = 0
        terminal record : run_cancelled
        reason attributed: 'stream context exited'
```

**Expected result.** Run 2 executes its nodes and completes. Nobody requested a
cancellation for it; run 1 was never cancelled either — it ran to completion.

**Actual result.** Run 2 is cancelled before the entry node is invoked, and its
trace attributes the cancellation to `'stream context exited'` — an event that
belongs to a different, already-finished run.

**This is a regression, verified directly.** The same script run against a
worktree at base `2a860a7`:

```console
run 2 : status = completed | nodes executed = 1
RESULT: clean
```

**Second, independent trigger — the concurrent-cancel race**
(`.attack/r04_phase4_new_defects.py`, N8). A supervisor thread calling
`runtime.cancel()` while `run()` is starting — the ordinary timeout/shutdown
pattern — usually finds `_running` still false:

```
300 rounds: {'cancelled': 4, 'completed': 296, 'leaked': 296, 'other': 0}
```

The cancellation the caller requested had **no effect** on the run in flight in
296/300 rounds, *and* remained queued for the next run in all 296. Both halves
are wrong: the intended run is not cancelled, and an unintended one is.

**User impact.** A `Runtime` is explicitly reusable — `_running` guards only
*overlapping* runs, and `bench_motus.py` itself reuses one `Runtime` across every
sample. Any consumer that streams a run inside the documented context manager
and then reuses the `Runtime` silently loses the next run: no exception, no
warning, no node executed, `RunResult.succeeded is False`. In a pipeline this
manifests as work that simply did not happen. The evidence produced is worse
than useless for an auditor — it is a well-formed, contract-valid
`run_started → run_cancelled` pair blaming a cause the operator never issued for
that run.

**Why existing tests missed it.** `tests/adversarial/test_adv_findings.py::test_adv_009`
asserts only that a *queued* cancellation takes effect on the *next* run — which
is precisely the leaked behaviour, so the leak looks like the feature. No test
in the repository (a) streams a run to exhaustion and then reuses the `Runtime`,
or (b) asserts that `StreamDriver.close()` is a no-op once the run has finished.
`tests/test_motus_runtime.py`'s stream tests all close mid-run, where the
behaviour is correct (`.attack/r01`, C5 — verified clean).

**Why the contract gate cannot catch it.** The leaked run's trace validates with
zero violations in both JSON and JSONL form (`.attack/r04`, N9). E1 admits
`run_cancelled` immediately after `run_started`, so the document is legitimately
well-formed. Only runtime semantics reveal the defect.

**Bounded remediation direction (not implemented).** Two independent changes,
either of which breaks the reproduction, both of which are wanted:
1. `StreamDriver.close()` should not request cancellation when the underlying
   iterator is already exhausted — track completion (the generator raising
   `StopIteration` during normal iteration) and make `close()` a no-op then.
2. `Runtime.cancel()` should not silently queue into an unbounded future.
   Either bind the pending request to the next `_start` under the same lock that
   sets `_running` (closing the race), or make a queued request expire when a
   run completes without consuming it, or have `cancel()` on an idle,
   already-used `Runtime` raise rather than queue. A regression test should
   assert that a `with`-block stream followed by `run()` executes its nodes.

---

### RA-002 — MEDIUM — under `in-memory`, an attached sink that fails to *open* is ignored but one that fails to *write* aborts the run

**Affected file and function.** `src/vitruvyan_motus/observers.py` →
`_ObservationHub.bind()` (lines 133–142) and `_ObservationHub.persist()`
(lines 187–195).

**Violated contract or guarantee.** guarantees.md invariant II: *"A run cannot
declare itself completed if **the sink required by its durability profile** has
not accepted the trace."* The `in-memory` profile requires no sink, so no
`in-memory` sink failure should prevent logical success. ADR-007 §7 says only
that "Sink failures retain the existing logical-failure rule", which is that
rule.

**Minimal reproducible attack** (`.attack/r04_phase4_new_defects.py`, N6):

```python
class Sink:
    def __init__(self, fail_open=False, fail_write=False): ...
    def open_run(self, header):
        if self.fail_open: raise OSError("cannot open")
        return self
    def write(self, records):
        if self.fail_write: raise OSError("cannot write")

Runtime(LINEAR, nodes, durability_profile="in-memory", sink=Sink(fail_open=True)).run(...)
Runtime(LINEAR, nodes, durability_profile="in-memory", sink=Sink(fail_write=True)).run(...)
```

**Expected result.** One consistent rule for the `in-memory` profile. Either
both failures are non-fatal (matching invariant II literally), or both are
fatal (a stricter reading, requiring an amendment).

**Actual result.**

```
in-memory, open fails  : status=completed          <- failure stored in _async_failure, never raised
in-memory, write fails : SinkFailed (cannot write) <- run aborted
```

`bind()` catches the open failure into `self._async_failure`; `persist()` then
short-circuits on `profile == "in-memory" and self._run_sink is None` and
returns *before* re-raising it. But when the sink opened successfully,
`persist()` falls into the `in ("in-memory", "synchronous")` branch and the
write exception propagates into `Runtime._store`, which raises `SinkFailed`.

**User impact.** The `in-memory` profile is the default. In 0.6.0 an attached
sink was inert; in 0.6.1 it is live and can abort a run that the profile
promises nothing about. A consumer who attaches an observation sink for
telemetry — the very use ADR-007 §7 enables — now has unrelated sink exceptions
convert successful runs into `SinkFailed`. Conversely, a sink that cannot open
at all is silently ignored, which is the failure mode ADR-007 §7 set out to
eliminate. The two behaviours are exact opposites for the same class of fault.

**Also observed (same root, no separate ID).** Under `in-memory` with a sink,
`chunk_records` and `flush_interval_ms` are ignored — every record is written
individually and synchronously — while the run header advertises no `sink`
object at all, because only `buffered` emits one (`.attack/r04`, N7). The
`in-memory`-plus-sink combination therefore has no declared delivery semantics
in the header an auditor reads.

**Why existing tests missed it.** `test_adv_007` asserts only that
`sink.runs` is non-empty; no test exercises a *failing* sink under `in-memory`,
and no test compares open-failure with write-failure behaviour within one
profile.

**Bounded remediation direction (not implemented).** Decide the rule and apply
it symmetrically. The reading most consistent with invariant II: under
`in-memory`, both open and write failures are non-fatal and are surfaced as
observation, not as `SinkFailed`. If instead an attached sink is to be treated
as required regardless of profile, that is a guarantees.md amendment plus an ADR,
and `bind()` must then propagate its stored failure. Either way, document the
delivery semantics of `in-memory`-plus-sink in the run header.

---

### RA-003 — MEDIUM — the `Rejection` absence sentinel is not identity-stable across `pickle`, re-opening the original CRITICAL failure mode

**Affected file and symbol.** `src/vitruvyan_motus/trace.py` → `class _Missing`
(lines 24–36). It defines `__copy__` and `__deepcopy__` but no `__reduce__`,
`__reduce_ex__` or `__getnewargs__`.

**Violated contract or guarantee.** `contract/node-protocol.md` §2.2: *"A value
that cannot be written down under these rules is refused at the boundary — the
runtime never carries what it cannot record."* ADR-007 §1 claims identity
stability "under shallow and deep copy" only, so this is a **scoping gap**, not
a false claim — but the consequence is the original CRITICAL symptom.

**Minimal reproducible attack** (`.attack/r02` A1, `.attack/r03` A1'):

```python
r = pickle.loads(pickle.dumps(Rejection("w", "r", NOW)))   # e.g. a cache, or multiprocessing

def declines(state):
    return state.with_rejection(r)

result = Runtime(SINGLE, {"a": declines}).run(State.empty("a1"))
result.trace.to_json()
```

**Expected result.** `evidence` remains absent, exactly as for the in-process
value.

**Actual result.**

```
pickle roundtrip     evidence is _MISSING: False  to_dict keys: ['evidence','reason','ts','what']
run status=completed   evidence in trace = _Missing
to_json: TypeError: Object of type _Missing is not JSON serializable
```

A logically successful run again carries a value its trace cannot serialize —
the precise shape of MOTUS-ADV-002.

**User impact.** Bounded but real: `Rejection` is a public, frozen dataclass in
`__all__`, and pickling public value objects into a cache, a queue or across
`multiprocessing` is an ordinary Python idiom. Motus itself never pickles, so
no purely in-process pipeline can reach this.

**Severity rationale (stated so it can be challenged).** MEDIUM, not HIGH,
because the trigger is an out-of-band serialization round trip on a value type
that Motus never round-trips itself, and because ADR-007 §1 does not claim
pickle stability. If the founder considers pickling public value types a
supported surface, this escalates to HIGH on the same evidence.

**Why existing tests missed it.** The remediation tests cover `copy.copy` and
`copy.deepcopy`, matching ADR-007 §1's wording exactly; no test exercises any
other isolation mechanism.

**Bounded remediation direction (not implemented).** Give `_Missing` a
`__reduce__` returning a module-level factory (or its qualified name) so
unpickling yields the singleton, and add the pickle round trip to the existing
copy-stability test. A defensive second net — validating rejection wire values
in `State._writes_wire()` or `_initial_wire()` — would close the whole class of
sentinel-escape defects rather than this one instance.

---

### RA-004 — LOW — `motus_config()` moved onto the per-run hot path with obligations the node protocol does not state

**Affected file and function.** `src/vitruvyan_motus/runtime.py` →
`Runtime._refresh_identity()` (lines 195–221), called from `_start()` (line 290),
via `_node_identity_parts` → `_config_material` (lines 55–72).

**Violated contract or guarantee.** `contract/node-protocol.md` §6.3 defines
`config_fingerprint` as "the fingerprint of its `motus_config()` return (a
strict JSON value the class opts into providing)" and states no obligation that
the method be pure, total, cheap, or safe to call repeatedly. In 0.6.0 it was
invoked once per `Runtime`; in 0.6.1 it is invoked once per node per run.

**Minimal reproducible attacks** (`.attack/r04`, N4/N5):

```python
class Angry:
    def motus_config(self):
        self.calls += 1
        if self.calls > 1: raise RuntimeError("config exploded on the second run")
        return {"ok": True}
```

```
run 1 -> RuntimeError config exploded on the second run
run 2 -> RuntimeError: config exploded on the second run
run 3 -> RuntimeError: config exploded on the second run
```

```python
class SideEffecting:
    def motus_config(self):
        self.n += 1
        return {"call": self.n}      # changes merely by being observed
```

```
self-incrementing config -> distinct fingerprints: 3 of 3
```

**Expected result.** Either the protocol states the obligation, or the runtime
degrades gracefully — e.g. a `MotusError` subclass, and a trace that records the
failure.

**Actual result.** A raising `motus_config()` propagates a bare `RuntimeError`
out of `Runtime.run()` from inside `_start`, **before `run_started` is emitted**,
so there is no trace of the run at all, and `except MotusError` does not catch
it. A config that changes when observed mints a new `code_fingerprint` on every
run, making otherwise identical runs look like different code.

Measured cost on this host (shape only): the per-run identity refresh is
**6.7%** of a 1,000-node run for the benchmark's own `motus_config()`-bearing
node (5.95 ms of 88.77 ms) and 2.9% for an opaque node.

**User impact.** Low. It requires a `motus_config()` that raises or is
non-deterministic — both already questionable — but the failure is late, untyped
and untraced, and the new per-run cost is part of the 43.5 → 51.2 µs/node
movement in the published profile.

**Why existing tests missed it.** The identity tests use well-behaved configs
returning constants.

**Bounded remediation direction (not implemented).** State in node-protocol §6.3
that `motus_config()` MUST be pure, total and cheap because it is evaluated at
every run start; wrap failures from `_refresh_identity` in a `MotusError`
subclass so the public error surface stays closed; and consider recomputing only
when the node object's identity or a cheap version marker changes.

---

## 5. Adversarial attack ledger

Every attack attempted, including those the implementation resisted.

| Subsystem | Attacks | Passed (resisted) | Findings |
|---|---:|---:|---|
| **Rejection / sentinel** — 4 copy mechanisms + pickle; 5 sentinel-lookalike user values; full-run surfaces (`to_json`, `to_jsonl`, `to_dict`, snapshot, bundle, playback, explain); retry, exploration-continue, seeded-initial paths; real JSON sink under `synchronous`; JSON↔JSONL equivalence | 23 | 22 | `RA-003` |
| **max_transitions** — all-raising cycle; mixed commit/continue/retry; retry exhaustion; self-loop; multi-node SCC; limits 1–4 explicit and 1–8 × 2 policies; END exactly at the limit; forged activation counts; spec claiming 3/4/5; runtime↔validator agreement sweep | 30 | 30 | none |
| **Trace materialization** — encoder-invocation counting; mixed `to_json`/`to_dict` order; returned-document mutation; cross-order determinism; cache-poisoning attempts | 9 | 9 | none |
| **State index** — sizes 1/63/64/65/127/128/129/200 × 3 probe positions; comparison-count bound; duplicate keys; identical values; namespace separation; 138 retained snapshots re-verified; branching from an old snapshot; scan and snapshot order; indexed-decision origin | 45 | 45 | none |
| **RunContext binding** — 16 signature shapes incl. keyword-only, callable instances, bound methods, decorated, two partial shapes, `*args`, `**kwargs`, zero-arg, three-arg, async | 16 | 16 | none |
| **Callable identity** — unchanged/changed/reverted config; in-place nested mutation; partial stability; opaque per-run constraints; raising config; self-observing config; non-JSON config; `lru_cache` key hashability across 4 unhashable-instance shapes | 15 | 13 | `RA-004` |
| **Sinks and durability** — sink opened under 3 profiles; causal order vs trace; open-failure and write-failure × 3 profiles; sink reuse across 3 runs; chunking under `in-memory` | 15 | 13 | `RA-002` |
| **Cancellation** — pre-run consumed once; stacked calls; cancel from node; cancel from listener; cancel after `BaseException` escape; mid-run stream close; exhausted-stream close; `with`-block exit; 300 concurrent-cancel rounds | 12 | 9 | `RA-001` |
| **Listener boundary** — record mutation; `SystemExit`; failure counting; duplicate registration; per-listener `seq` order; trace-visible cancellation | 6 | 6 | none |
| **Trace/validator agreement** — 5 changed-path executions validated in JSON and JSONL with verdict comparison | 10 | 10 | none |
| **Performance and evidence integrity** — 5 runs × 5 metrics recomputed; 4 ADR claims cross-checked; 5 environment identities; completeness per run; arithmetic possibility of the ratio; identity-refresh cost; read-path scaling at 4 sizes | 40 | 40 | none |
| **Packaging and compatibility** — wheel contents, `py.typed`, `axis` exclusion, runtime dependencies, clean-venv import outside the checkout, `__all__` diff vs 0.6.0, frozen-path diff, Terraveler + inherited corpus | 14 | 14 | none |
| **Totals** | **235** | **227** | **4** |

Notable **rejected hypotheses** — attacks that looked promising and failed:

- *The `lru_cache` on `_static_callable_identity` will crash on unhashable
  nodes.* It does not. CPython hashes bound methods by the identity of
  `__self__`, so bound methods of `@dataclass` (`eq=True` ⇒ `__hash__ = None`)
  and of classes defining `__eq__` remain hashable; callable instances are keyed
  by `type(x).__call__`, a function. All four shapes ran to completion.
- *E11's new exact-equality check will reject the runtime's own traces.* Across
  limits 1–8 × 2 policies, every `transition_limit_exceeded` trace carried
  exactly `max_transitions` routing-producing activations and validated with
  zero violations. Zero mismatches.
- *The state index will drift when an old snapshot is read after later commits.*
  138 snapshots spanning three chunk closures were re-read afterwards; drift 0.
- *`to_dict()` can be poisoned by a prior `to_json()`.* Mixed-order calls produce
  identical documents and independent objects.
- *An async node will be silently mistaken for a result.* It fails as
  `NodeFailed` with the coroutine rejected by `_committed`.
- *A `Rejection` sentinel lookalike can be smuggled in as user evidence.* A fresh
  `_Missing()` and a class mimicking `__deepcopy__` are both refused by
  `_strict_plain_json`.

---

## 6. Performance and evidence-integrity verdict

**Raw artifact, committed candidate and documentation agree — verified, not
assumed.** `benchmarks/candidate-v0.6.1-epyc-py310.json` hashes to
`07bcc8d6d8cfc900729b6723fb2a1c9c35d4b3cd5ae97bceeb948733e33ca3ca`, exactly the
SHA-256 recorded in ADR-007. Recomputing every aggregate independently from the
five raw runs reproduces ADR-007's numbers to five decimal places:

| Metric | ADR-007 | Recomputed here | Agree |
|---|---:|---:|:--:|
| per-node µs | 51.2301 | 51.23012 | ✓ |
| 100-node no-op ms | 3.43542 | 3.43542 | ✓ |
| cold materialization ratio | 1.749x | 1.74896x | ✓ |
| positive superlinearity | 3.47 % | 3.46932 % | ✓ |
| spreads | 2.88 / 4.40 / 10.30 / 13.68 % | identical | ✓ |

All five runs carry `vitruvyan-motus/0.6.1`, Python `3.10.12`, `AMD EPYC 9V74
80-Core Processor`, `gc_enabled_during_runs: true`, `repeats: 7`, and each
reports 3,002 records, 1,000 facts and **0** declaration violations. Five
independent runs satisfy the `MIN_RUNS = 5` requirement.

**The measurement is now methodologically valid.** The decisive check needs no
host: `to_dict()` is `json.loads(json.dumps(view))` and therefore performs
strictly more work than the `json.dumps` it is compared against, so a ratio
below 1.0 is arithmetically impossible. 0.6.0 published 0.788x — impossible, and
the proof that it timed a cache hit. 0.6.1 recomputes 1.749x, which is possible
and consistent with a dumps-plus-loads. The cached `to_json` path is now
reported separately (`cached_to_json_min_ms`, ~0.003 ms) and is not the gated
statistic. The encoder-invocation count confirms the code: 9 `to_dict()` calls →
9 encodes.

**Thresholds remain the accepted ADR-006 thresholds.** `MOTUS_CANDIDATE_TARGETS`
= `{per_node: 45.0, noop: 3.25, serialization: 1.5, superlinear: 10.0}` and
`MOTUS_CANDIDATE_TOLERANCE = 0.25` are byte-identical to base. The only changes
to `check_slo_baseline.py` are the default candidate path and the runtime
identity string it *requires*. **No hidden relaxation.** The gate still refuses a
different interpreter, CPU class, runtime version, incomplete trace or modified
aggregate.

**The candidate passes without relaxation** — all ten rows PASS — but three of
the four numeric rows now sit **above their target while inside the +25%
ceiling**: 51.2 vs ≤45 µs (ceiling 56.25), 3.44 vs ≤3.25 ms (ceiling 4.0625),
1.749x vs ≤1.5x (ceiling 1.875). guarantees.md publishes these honestly as
measured reality.

**Remaining "known debt" — is it historical or present?** Both, and the
distinction now matters:
- The Axis v0.4.0 debts (4.7x serialization, 25 % superlinearity) are
  **historical**, unchanged, correctly labelled.
- The Motus serialization row is **present debt in the 0.6.1 candidate**: 1.749x
  exceeds the 1.5x target. This is not a regression in the code — it is the true
  cost that 0.6.0's cache-hit measurement concealed, now stated honestly. That
  is an improvement in evidence quality even though the number got worse.
- The per-node and no-op rows moved from inside their targets to outside them
  (43.5 → 51.2 µs, 3.09 → 3.44 ms). Part is attributable to the remediation
  itself: the per-run identity refresh measures 6.7 % of a 1,000-node run here,
  and the index bookkeeping adds the rest. Both are disclosed, both stay inside
  the ceilings, and both are the price of correctness fixes. **NOTE-2** records
  that three of four rows exceeding target is a materially different posture from
  0.6.0's published table and deserves an explicit founder acknowledgement rather
  than passing quietly through a ceiling check.

**Read-path scaling.** The ADV-004 remediation is verified structurally — a keyed
lookup examines at most 63 log entries before one dictionary access, exactly as
ADR-007 §5 claims, confirmed at every chunk boundary. Measured here, a
write+read workload now costs 86 → 119 µs/node from n=100 to n=1000 (growth
1.38x) against 82 → 147 µs/node (growth 1.79x) at 0.6.0 on the same host. The
characterised benchmark node still only writes (**NOTE-3**), so the published
3.47 % superlinearity continues to describe a workload that does not exercise
the read surface — but the algorithmic defect behind the original finding is
gone, so the claim is no longer misleading.

---

## 7. Compatibility and packaging verdict

| Check | Result |
|---|---|
| `axis` excluded from the wheel | **PASS** — 12 members, all `vitruvyan_motus/`; `find_spec("axis") is None` from a clean venv outside the checkout |
| Zero runtime dependencies | **PASS** — `pip freeze` in the isolated venv lists only `vitruvyan-motus`; metadata declares dependencies solely under `extra == "test"` |
| Public API | **PASS** — `__all__` is **byte-identical** to 0.6.0 (41 names, all resolving). No public name added, removed or renamed |
| Version surfaces | **PASS** — `__version__ == "0.6.1"`, `TRACE_SCHEMA_VERSION == "1.1.0"`, distinct and correctly exposed; trace schema `x-current-version` unchanged |
| Frozen contract paths | **PASS** — `git diff origin/main...HEAD -- tests/contract/ tests/compat/` is empty; `tools/check_frozen_paths.py origin/main HEAD` exits 0 |
| Terraveler compatibility | **PASS** — `tests/compat/` + `tests/contract/`: 26 passed. `src/vitruvyan_motus/compat.py` is untouched by the PR |
| Contract schema versions | **PASS** — GraphSpec stays `1.0.0`, trace stays `1.1.0`; the amendments are description-level (R11's unit, E11's rule text) plus the E11 implementation, with no wire-shape change |
| Path traversal / unsafe filenames | **PASS** — unchanged surface; the native runtime writes no files, and the compat `FileTraceObserver` normalisation is untouched |

The contract amendments are properly constituted: `graphspec.v1.schema.json` and
`trace.v1.schema.json` descriptions, `guarantees.md` §3 and §6, and
`validate.py` E11 all move together with ADR-007, which states what changed and
why. The E11 change is a **tightening** (exact equality on routing-producing
activations, replacing a lower-bound check on committed transitions), verified
to catch both under- and over-claimed counts.

---

## 8. Residual risks and 0.7 deferrals

### Acceptable documented limitations

- **NOTE-1 — the cycle bound is per run segment.** `ReplayEngine.resume()`
  starts a new segment with a fresh activation counter, so a graph with
  `max_transitions = 2` can be advanced two activations at a time indefinitely by
  repeated resume (`.attack/r04`, N3). Each individual trace is valid and
  contract-conformant, and resume is an explicit, fail-closed, manually invoked
  operation. No contract statement says the bound is cumulative. Worth an
  explicit sentence in ADR-005 or guarantees.md rather than a code change.
- **NOTE-2 — three of four SLO rows now exceed target inside the ceiling.** See
  §6. Not a gate failure; a posture change that should be acknowledged
  deliberately, not absorbed silently.
- **NOTE-3 — the characterised workload still never reads state.** The read path
  is now bounded by construction, so this no longer conceals an algorithmic
  defect, but adding a read-performing row would make the profile describe the
  protocol's central mechanism.
- **NOTE-4 — `_static_callable_identity` holds strong references to callables**
  via `functools.lru_cache(maxsize=4096)`. Bounded and harmless for realistic
  registries; worth knowing for a process that constructs thousands of
  short-lived node classes.
- **Listener scheduling** remains able to delay execution, by construction. This
  is now correctly stated in guarantees.md §6 rather than denied. Moving delivery
  off the runner thread stays an explicitly-versioned future change.
- **Ambient nondeterminism detection** remains best-effort per node-protocol
  §6.2, unchanged by this PR and out of its scope.

### Defects (not limitations)

`RA-001` (HIGH), `RA-002` (MEDIUM), `RA-003` (MEDIUM), `RA-004` (LOW). `RA-001`
blocks the release. The other three are bounded and could reasonably be
scheduled — but `RA-002` and `RA-003` are both small, closed-form fixes in code
the PR already touches, and folding them into the same corrective push is
cheaper than carrying them to 0.7.

---

## 9. Release recommendation

`RELEASE GATE: FAIL`

Blocking finding IDs:

- **RA-001**

---

## 10. Repository integrity

```console
$ git status --short --branch
## HEAD (no branch)
?? .attack/r01_cancellation_lifecycle.py
?? .attack/r02_original_findings.py
?? .attack/r03_phase3.py
?? .attack/r04_phase4_new_defects.py
?? .attack/r05_evidence_integrity.py
?? .attack/r06_RA001_minimal.py

$ git diff --stat
(empty)

$ git stash create
(empty — Git cannot construct a stash commit because no tracked file differs)
```

**Tracked files changed by the audit:** none. Zero. Verified three ways — an
empty `git diff --stat`, an empty `git diff HEAD --stat`, and an empty
`git stash create`, which Git can only return when no tracked modification
exists at all.

**Temporary untracked files created:** six attack scripts under `.attack/`, plus
this report at `audit/MOTUS-0.6.1-REAUDIT-REPORT.md`. All uncommitted.

A temporary `git worktree` was created at base `2a860a7` to prove `RA-001` is a
regression and was removed afterwards (`git worktree list` shows only the main
checkout).

**Confirmation:** no commit, no push, no PR creation or update, no merge, no
tag, no publication, no issue mutation occurred. The repository is at detached
`746b91b9f6da5c8020ea1da9bfb90265a3b557c9` with an otherwise pristine tree. No
production code, contract, test, ADR, workflow, benchmark or documentation file
was modified, and no discovered defect was fixed.
