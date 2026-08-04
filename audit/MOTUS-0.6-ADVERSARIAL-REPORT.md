# Vitruvyan Motus 0.6 — Independent Adversarial Release Audit

**Auditor:** independent adversarial reviewer (Claude Opus 5), acting against the
implementation, not for it.
**Audited SHA:** `2a860a7c5c57f95dd471ccc1ff66bcf7bd10c7f6`
**Branch created:** `audit/0.6-adversarial-opus` (local only; nothing committed, pushed, merged or released)
**Date:** 2026-08-04

---

## A. Executive verdict

> ## FAIL
>
> One CRITICAL and two HIGH findings must be corrected before 0.7 may begin.

The 0.6 kernel is, on the whole, a genuinely hard target. Eighty distinct
adversarial probes — state aliasing, forged redaction, forged routes, forged
committed decisions, sink mutation, listener mutation, lineage substitution,
non-finite and non-JSON values, CRLF and truncated streams, cross-graph bundles,
effect-class escalation, resume without receipts — were **resisted correctly**,
and 22 of 23 structurally diverse real executions produce evidence that passes
`contract/validate.py` identically in the JSON and the JSONL form. The causal
provenance model in particular (routes naming the exact decision entry by
collection and index, recomputed against the GraphSpec at bundle time) survived
every attack aimed at it.

It nevertheless fails, for three reasons that are not stylistic:

1. **MOTUS-ADV-002 (CRITICAL).** A node that declines to act in exactly the way
   `node-protocol.md` §2.3 tells it to — `Rejection(what, reason, ts)` with no
   `evidence` — produces a run that reports `status == "completed"` and
   `succeeded is True` while writing a raw Python `object()` into the trace.
   That trace cannot be serialised, cannot be validated, cannot be bundled,
   cannot be replayed and cannot be persisted. For a runtime whose entire thesis
   is "the trace is part of the execution itself", a first-class, documented,
   contract-encouraged node behaviour that silently destroys the evidence while
   reporting success is a defeat of the core trust model. There is no existing
   test that writes a native `Rejection` through a `Runtime` and serialises the
   result.

2. **MOTUS-ADV-001 (HIGH).** `max_transitions` — which R11 makes *mandatory* for
   every cyclic spec precisely because it is that cycle's safety limit — does not
   bound a cycle in which no attempt commits. Under `Policy.EXPLORATION` a
   raised attempt routes onward without incrementing the counter, so a cyclic
   graph whose nodes are failing (an external dependency being down is the
   ordinary case) loops forever, accumulates records without bound, and emits no
   terminal outcome at all.

3. **MOTUS-ADV-003 (HIGH).** The published performance claim "0.788x trace
   preparation versus `json.dumps`" and the CI gate that enforces it measure a
   memoisation cache hit rather than trace preparation. This is provable from the
   committed evidence alone without re-running anything: `Trace.to_dict()` is
   `json.loads(self.to_json())` and `to_json()` memoises, so a ratio below 1.0
   is arithmetically impossible for genuine preparation — a `dumps` followed by a
   `loads` cannot be faster than the `dumps` alone. The genuine uncached ratio
   measured here is ~4.4x, i.e. above the 1.5x target and above the 1.875x
   ADR-006 ceiling, and roughly the same as the Axis 4.7x that guarantees.md
   records as an unmet historical debt.

Six further MEDIUM/LOW findings are listed below. None of the frozen corpora were
touched, no runtime file was modified, and the complete pre-existing suite still
passes unchanged.

---

## B. Baseline evidence

### B.1 Provenance

```console
$ git rev-parse HEAD
2a860a7c5c57f95dd471ccc1ff66bcf7bd10c7f6

$ git status --short --branch
## main...origin/main

$ git diff --stat
(empty)
```

The checked-out source matched the authoritative SHA exactly, with a clean
working tree. No discrepancy to report. The audit branch was then created at
that same commit:

```console
$ git checkout -b audit/0.6-adversarial-opus
$ git rev-parse HEAD
2a860a7c5c57f95dd471ccc1ff66bcf7bd10c7f6
```

### B.2 Environment

| | |
|---|---|
| OS | Linux 6.8.0-124-generic (Ubuntu), x86_64 |
| CPU | AMD EPYC Processor (with IBPB), 8 vCPU, 1 socket |
| Interpreter | **CPython 3.12.3** |
| Reference profile in ADR-006 | **CPython 3.10.12, AMD EPYC 9V74 80-Core** |
| Virtualenv | created fresh in the session scratchpad |

**Environmental limitation, stated up front.** The interpreter and CPU class do
not match the ADR-006 reference profile. `benchmarks/check_slo_baseline.py`
correctly refuses to gate on timings from an uncharacterised runner, so **no new
timing was substituted for the committed evidence**. Every timing quoted in this
report is either (a) recomputed by the committed gate from the committed raw
JSON, which is host-independent, or (b) explicitly labelled as an
on-this-host observation used only to establish a *shape* (linear vs
superlinear, cached vs uncached), never to assert or refute an absolute SLO
number. Finding MOTUS-ADV-003's core argument is arithmetic and needs no host at
all.

### B.3 Dependency installation

```console
python3 -m venv .venv
.venv/bin/pip install -e ".[test]" -c constraints/test.txt
```

Resolved (`pip freeze`):

```
Pygments==2.20.0            iniconfig==2.3.0            pluggy==1.6.0
attrs==26.1.0               jsonschema==4.26.0          pytest==8.4.2
jsonschema-specifications==2025.9.1                     pytest-asyncio==0.24.0
packaging==26.2             referencing==0.37.0         rpds-py==0.30.0
typing_extensions==4.16.0   vitruvyan-motus==0.6.0 (editable, from the checkout)
```

`exceptiongroup` and `tomli` in `constraints/test.txt` are 3.10-conditional and
correctly absent on 3.12.

### B.4 Original suite result (unmodified)

```console
$ .venv/bin/python -m pytest tests/ -q
455 passed, 14 skipped, 20 warnings in 33.94s
```

Re-run after adding `tests/adversarial/`, with the new directory excluded, to
prove nothing about the existing suite changed:

```console
$ .venv/bin/python -m pytest tests/ -q --ignore=tests/adversarial
455 passed, 14 skipped, 20 warnings in 32.64s
```

All 14 skips are infrastructure-gated, none is a Motus-native test:

| Count | Reason |
|---|---|
| 1 | `tests/test_audit.py` — needs the `[postgres]` extra |
| 5 | `tests/test_persistence.py` — PostgreSQL not available |
| 8 | `tests/test_qdrant_adapter.py` — Qdrant server not available |

The 20 warnings are all `DeprecationWarning: datetime.utcnow()` from the
byte-preserved legacy `axis`-era tests (`tests/test_synaptic_bus.py`,
`tests/test_e2e.py`), not from `vitruvyan_motus`.

### B.5 Frozen-path result

```console
$ python tools/check_frozen_paths.py 2a860a7c5c57f95dd471ccc1ff66bcf7bd10c7f6 2a860a7c5c57f95dd471ccc1ff66bcf7bd10c7f6
Frozen contract paths: PASS
(exit 0)
```

`tests/contract/kernel.py` hashes to
`113dbc24fafd35dbf24d92eae88f26692a70df59801b8c733cebe6c254e56ec8`, exactly the
founder-approved digest hard-coded in the checker.

Because the audit is forbidden to commit, the end-state proof is given directly
from Git rather than from a two-commit diff:

```console
$ git diff --stat                                     # -> empty
$ git stash create                                    # -> empty (no tracked modification exists at all)
$ git ls-files --others --exclude-standard | grep -E '^(tests/contract|tests/compat)/'
none
$ git status --porcelain -- src/ contract/ adr/ benchmarks/ README.md
none
```

`git stash create` returning the empty string is the strongest available proof:
Git could not construct a stash commit because **not one tracked file differs
from `2a860a7`**. Every audit artifact is a new untracked file.

### B.6 Wheel result

```console
$ python -m build --wheel --outdir <scratch>/wheel
Successfully built vitruvyan_motus-0.6.0-py3-none-any.whl
```

| Check | Result |
|---|---|
| Contains `vitruvyan_motus/` | PASS — 10 modules |
| Contains `py.typed` | PASS |
| Contains `axis` | PASS — absent |
| Contains `tests/`, `contract/`, `benchmarks/` | PASS — absent |
| Declares runtime dependencies | PASS — none; only `extra == "test"` |
| Installs in a clean venv | PASS |
| Imports from **outside** the checkout (`cwd=/tmp`) | PASS |
| `axis` importable from the installed wheel | PASS — `find_spec("axis") is None` |
| `__version__` / `TRACE_SCHEMA_VERSION` distinct | PASS — `0.6.0` vs `1.1.0` |
| Metadata describes the predecessor | PASS — no Axis reference; `Name: vitruvyan-motus` |
| `__all__` (41 names) all resolve | PASS |
| README's documented API present | PASS — every name in README §"Native package surface" is exported |
| Compatibility symbols leaking under native names | PASS — `LegacyDecision` absent from the native namespace; `Decision` absent from `compat.__all__`; `compat.Fact is not vitruvyan_motus.Fact` |

### B.7 Benchmark result

```console
$ python benchmarks/check_slo_baseline.py
SLO baseline gate: PASS
  PASS       Per-node overhead
  PASS       100-node no-op
  KNOWN DEBT Trace serialization
  KNOWN DEBT Superlinear accumulation
  PASS       Trace completeness

$ python benchmarks/check_slo_baseline.py --candidate benchmarks/candidate-v0.6.0-epyc-py310.json
SLO baseline gate: PASS
  ... 5 reference rows ...
  PASS       Candidate Per-node overhead
  PASS       Candidate 100-node no-op
  PASS       Candidate Trace serialization      <-- see MOTUS-ADV-003
  PASS       Candidate Superlinear accumulation <-- see MOTUS-ADV-004
  PASS       Candidate trace completeness
```

The gate mechanically does what it says: it recomputes the aggregates from the
committed raw runs, checks the runner identity, and refuses a different
interpreter or CPU class. Its **inputs** for two of the five rows do not measure
what their labels claim (MOTUS-ADV-003, MOTUS-ADV-004).

Independently reproduced on this host, exactly as claimed:
`3,002` trace records, `1,000` facts, `0` declaration violations for the
realistic 1,000-node run.

### B.8 Suite totals after the audit

| Suite | Passed | Failed | Skipped |
|---|---:|---:|---:|
| Pre-existing suite (`--ignore=tests/adversarial`) | **455** | **0** | 14 |
| `tests/adversarial/test_adv_resisted.py` (pinned resisted attacks) | **80** | **0** | 0 |
| `tests/adversarial/test_adv_findings.py` (finding reproductions + controls) | 2 | **10** | 0 |
| Everything together | 537 | 10 | 14 |

The ten failures are the findings. They are deterministic — no sleeps, no
threads, no wall-clock thresholds — and the whole findings file runs in 0.15 s.

---

## C. Attack coverage matrix

| # | Surface | Attacks attempted | Result | Permanent test | Exploratory script | Outcome |
|---|---|---|---|---|---|---|
| **A** | GraphSpec construction & validation | missing/dangling entry (R1), dangling targets (R2), unreachable nodes (R3), missing/extra transition keys (R4), duplicate names (R5), node named `END` (R8), trap regions and cycle-without-limit (R11), malformed `requires_motus` incl. empty clause / wildcard-with-local / `~=` single segment (R12), post-validation mutation of the caller's dict, `to_dict()` write-back, fingerprint sensitivity to entry / version / effect_class / omitted-default / `reads_declared` / `max_transitions`, key-order vs array-order, `8` vs `8.0`, opaque vs configured vs partial vs closure identity, mutable config after fingerprinting, 2,000-node deep and 2,000-branch wide graphs, 400-deep nested values | Resisted except node-identity staleness | `test_adv_resisted.py` (10 tests) | `.attack/a05_graph_fingerprint.py` | **MOTUS-ADV-006** |
| **B** | Execution state machine | complete without executing entry, skip a node, execute off-route, complete after retry exhaustion, turn abort/cancel/route-miss into success, repeat or skip attempt numbers, exceed `max_transitions`, commit writes from a raised attempt, route on a stale/overwritten Decision, two terminals, no terminal, records after a terminal, STRICT vs EXPLORATION divergence | Resisted except the unbounded cycle | `test_adv_resisted.py` (7 tests) | `.attack/a01`, `.attack/a10` | **MOTUS-ADV-001** |
| **C** | State isolation & causal provenance | nested mutable payload mutated after return, reader mutating a value it read, aliased dict/list/set/user object, double write of one key in one transition, duplicate seeded Decision keys, absent / scan / header reads, undeclared read & write sets, prefix preservation & foreign-state substitution, JSON `true` vs `1` vs `"true"` in routing, forged redacted values at three depths, NaN/Inf, tuple/set/datetime/object, non-string object keys | **Fully resisted** | `test_adv_resisted.py` (18 tests) | `.attack/a02_state_isolation.py` | none — see §E |
| **D** | Retry, failure, cancellation | 0/1/many retries, failure on the last allowed attempt, cancel before node start, cancel between records, cancel via `StreamDriver.close`, cancel from inside a listener, `NodeFailed.state` salvage, raised vs returned failure, `BaseException` from a node, cause pointing at the wrong transition, duplicate effects under retry | Resisted except pre-run cancel | `test_adv_resisted.py` (6 tests) | `.attack/a04_failure_cancel_sink.py` | **MOTUS-ADV-009** |
| **E** | Trace integrity & lifecycle | 23 structurally distinct real executions validated in JSON **and** JSONL and compared for verdict equality; seq gaps; records after a terminal; duplicated terminal line; CRLF; truncated final line; missing trailing newline; blank lines; malformed UTF-8 through the CLI; NaN/Infinity lines; caller mutation of `trace.run` / `trace.records`; replay-capability upgrade; vanishing constraints | **Fully resisted** — 22/23 clean, 0 JSON↔JSONL divergences; the 23rd fails at serialisation, not validation | `test_adv_resisted.py` (4 tests) | `.attack/a07_trace_conformance_sweep.py` | **MOTUS-ADV-002** (via the 23rd case) |
| **F** | Sinks & durability | refuse `open_run`; refuse the first, a middle, and the terminal record; buffered without flush; forced flush of failure evidence; real `SIGKILL`-equivalent (`os._exit`) at controlled records for both persistent profiles; empty destination; sink mutating records and the header; run IDs with traversal, separators and NUL; compat `FileTraceObserver` traversal | **Fully resisted**; every SinkFailed trace is itself contract-valid in both forms | `test_adv_resisted.py` (7 tests) | `.attack/a09`, `.attack/a10` | **MOTUS-ADV-007** (misconfiguration only) |
| **G** | Observers & effects | listener raising `SystemExit`; multiple listeners; listener mutating the delivered record; listener cancelling the run; pure node emitting an effect (both gates); `recorded_effect` node emitting an external effect; per-attempt effect attribution under retry; receipts and idempotency keys | Resisted except the listener-capability claim | `test_adv_resisted.py` (4 tests) | `.attack/a04`, `.attack/a11` | **MOTUS-ADV-008** |
| **H** | Replay & resume | replay with a different fingerprint; resume from terminal / completed; resume from an in-flight stream; forged route target; forged committed decision value; resume reusing the source run id; external effects without key or with `unknown` receipt; in-flight external effect; impure "pure" node under `verify()`; opaque / source-unavailable identity degradation; ambient clock and randomness escaping RunContext | **Fully resisted** at the contract level | `test_adv_resisted.py` (10 tests) | `.attack/a06_replay_resume.py` | none — see §E, NOTE-3 |
| **I** | Concurrency & scheduling | two threads on one `Runtime` (200 unsynchronised rounds); 500 `threading.Barrier`-synchronised rounds; 3 rounds with `sys.settrace` forcing an interleave at the exact guard line; 5 rounds with a widened in-flight overlap; abandoned `StreamDriver`; `StreamDriver` held open across a `run()` | Guard is a non-atomic check-then-set, **but no corruption reproduced in 708 rounds** | — (deliberately not pinned: timing-dependent) | `.attack/a06`, `.attack/a10`, `.attack/a11` | NOTE-1 (rejected as a finding) |
| **J** | Packaging & isolated consumer | wheel contents, `py.typed`, absence of `axis`, undeclared dependencies, clean-venv import from outside the checkout, version vs schema version, metadata, `__all__` vs README, compat-symbol leakage, third-party import fence in a pristine interpreter | **Fully resisted** | `test_adv_resisted.py` (3 tests) | `.attack/a11_misc_surface.py` | none |
| **K** | Performance & resources | documented method reproduced (2 warmups, 7 samples, `gc.collect()`); cached vs cold `to_dict`/`to_json`; per-node cost at n = 100/250/500/1000 for a write-only node and for a write+read node; memory retention over 200 runs; record-count and violation-count claims | Two methodology defects found | `test_adv_findings.py` (2 deterministic tests) | `.attack/a08_performance.py` | **MOTUS-ADV-003**, **MOTUS-ADV-004** |
| **L** | README vs reality | every material claim classified in §F; quick-start and replay snippets executed verbatim; HTML viewer attacked with hostile run ids and values; explanation determinism; bundle-fingerprint stability | 2 claims unsupported, the rest supported or honestly qualified | `test_adv_resisted.py` (2 tests) | `.attack/a11_misc_surface.py` | **MOTUS-ADV-003**, **MOTUS-ADV-004** |

---

## D. Findings

| ID | Severity | Affected guarantee | Deterministic | Blocks 0.7 |
|---|---|---|---|---|
| [MOTUS-ADV-002](#motus-adv-002) | **CRITICAL** | node-protocol §2.2/§2.3; invariant "execution and evidence are produced together" | yes | **YES** |
| [MOTUS-ADV-001](#motus-adv-001) | **HIGH** | graphspec R11 safety limit; "one terminal outcome per run" | yes | **YES** |
| [MOTUS-ADV-003](#motus-adv-003) | **HIGH** | guarantees.md §3 Motus SLO row 3; README performance claim; CI regression gate | yes | **YES** |
| [MOTUS-ADV-004](#motus-adv-004) | MEDIUM | guarantees.md §3 Motus SLO row 4; README "no positive superlinear term" | yes | no |
| [MOTUS-ADV-009](#motus-adv-009) | MEDIUM | `Runtime.cancel` public API; guarantees.md §6 cancellation semantics | yes | no |
| [MOTUS-ADV-005](#motus-adv-005) | LOW | node-protocol §1.1 node shape | yes | no |
| [MOTUS-ADV-006](#motus-adv-006) | LOW | node-protocol §6.3 `code_fingerprint` recipe | yes | no |
| [MOTUS-ADV-007](#motus-adv-007) | LOW | guarantees.md invariant II / ADR-004 run binding | yes | no |
| [MOTUS-ADV-008](#motus-adv-008) | LOW | guarantees.md §6 Listener — "by construction rather than by convention" | yes | no |

Common reproduction command for every finding:

```bash
.venv/bin/python -m pytest tests/adversarial/test_adv_findings.py -q
```

---

<a id="motus-adv-002"></a>
### MOTUS-ADV-002 — CRITICAL — an evidence-free `Rejection` makes a "successful" run's trace unserialisable

**Affected guarantee.**
`contract/node-protocol.md` §2.2: *"Every value written MUST be a strict RFC 8259
JSON value … A value that cannot be written down under these rules is refused at
the boundary — the runtime never carries what it cannot record."*
`contract/node-protocol.md` §2.3: *"A node that declines to act SHOULD say so: a
rejection with a reason is the difference between 'didn't' and 'wouldn't'."*
README: *"Execution and evidence are produced together."*

**Affected files and symbols.**
- `src/vitruvyan_motus/trace.py:23` — `_MISSING = object()`
- `src/vitruvyan_motus/trace.py:175-193` — `Rejection.__post_init__`, `Rejection.to_dict`
- `src/vitruvyan_motus/state.py:35-51` — `_isolate_item_value` (hand-copies `Fact` and `Decision`, falls through to `copy.deepcopy` for `Rejection`)
- `src/vitruvyan_motus/state.py:324-328` — `State.with_rejection`
- `src/vitruvyan_motus/state.py:132-150` — `State.new` initial-state path
- `src/vitruvyan_motus/trace.py:349-353` — `Trace._append_runtime` (deliberately skips `_strict_plain_json`)

**Minimal reproducible attack.**

```python
from datetime import datetime, timezone
from vitruvyan_motus import GraphSpec, Rejection, Runtime, State

NOW = datetime(2026, 8, 4, tzinfo=timezone.utc)
spec = GraphSpec.from_dict({
    "schema_version": "1.0.0", "name": "rej", "version": "1.0.0", "entry": "a",
    "nodes": [{"name": "a", "effect_class": "pure"}],
    "transitions": {"a": {"kind": "terminal"}},
})

def declines(state):                       # node-protocol §2.3, verbatim shape
    return state.with_rejection(Rejection("the thing", "not applicable", NOW))

result = Runtime(spec, {"a": declines}).run(State.empty("rej"))
print(result.status, result.succeeded)     # completed True
result.trace.to_json()                     # TypeError
```

**Exact command.**

```bash
.venv/bin/python .attack/a03_rejection_evidence.py
.venv/bin/python -m pytest tests/adversarial/test_adv_findings.py -q -k adv_002
```

**Expected behaviour.** Either the omitted `evidence` is absent from the wire
form (as `Rejection.to_dict` intends), or the value is refused at the write
boundary. Under no reading may the runtime carry a value it cannot record, and
under no reading may such a run report success.

**Observed behaviour.**

| Operation | Result |
|---|---|
| `result.status` / `result.succeeded` | `"completed"` / `True` |
| trace `writes.rejections[0]["evidence"]` | `<object object at 0x…>` — a bare Python `object` |
| `trace.to_json()` / `to_jsonl()` / `to_dict()` | `TypeError: Object of type object is not JSON serializable` |
| `TraceBundle(spec, trace)` | `TypeError: object is not a strict RFC 8259 JSON value` |
| `playback()` / `verify()` / `resume()` / `explain()` / `to_html()` | unreachable — all need the bundle |
| real JSON sink, `synchronous` profile | `SinkFailed` **after 2 records were already durably written** — a truncated, unusable file |
| `State.new(rejections=[Rejection(w, r, ts)])` (seeded) | identical defect, poisoning `run_started.initial_state` |
| `Rejection(..., evidence={"n": 1})` (control) | works correctly |

**Why the difference matters.** This is not a corner case reached by hostile
input: it is the *documented, encouraged* way for a node to decline, with no
argument supplied that would hint at danger. The failure mode is the worst
available shape — the caller is told the run succeeded, `RunResult.state` is
correct and usable, and the loss is only discovered later, at the moment someone
tries to persist, validate, replay or explain the evidence. Under `in-memory`
durability (the default) nothing raises at all. Under a persistent profile the
sink has already written a prefix before failing, so what remains on disk is a
truncated trace whose truncation has no recorded cause. Every downstream promise
in README §"What the evidence can establish" is void for such a run.

**Deterministic or intermittent.** Fully deterministic; 100% reproduction.

**Regression test.**
`tests/adversarial/test_adv_findings.py::test_adv_002_rejection_without_evidence_keeps_the_trace_serializable`
`tests/adversarial/test_adv_findings.py::test_adv_002b_seeded_rejection_without_evidence_keeps_the_trace_serializable`
`tests/adversarial/test_adv_findings.py::test_adv_002c_rejection_with_evidence_is_the_working_control` (passes — isolates the cause)

**Probable root cause.** `Rejection` marks "no evidence" with an *identity*
sentinel, `_MISSING = object()`, and `to_dict` tests `self.evidence is not
_MISSING`. `copy.deepcopy(object())` returns a **new** object, so the isolated
copy stored by `State.with_rejection` (and by `_isolate_item_value`, which
hand-copies `Fact` and `Decision` field by field but falls through to
`copy.deepcopy` for `Rejection` — the exact type that breaks) no longer holds the
sentinel. `Trace._append_runtime` then bypasses `_strict_plain_json` on the
documented grounds that runtime-built records are "already built from validated
runtime primitives", so nothing downstream catches it.

**Minimal correction direction** *(not implemented)*. Any one of:
(a) make the sentinel deepcopy-stable — a singleton class defining
`__deepcopy__`/`__copy__` returning `self`, or `__reduce__` returning its module
name; (b) extend `_isolate_item_value` to reconstruct `Rejection` field by field
the way it already does for `Fact` and `Decision`, preserving the sentinel by
identity, and route `State.with_rejection` through it; or (c) replace the
identity sentinel with an explicit boolean presence flag. (a) is the smallest and
fixes both the write path and the seeded path at once. A regression guard on
`Trace._append_runtime` (assert strict-JSON in a debug/dev profile) would have
caught this class of defect at the boundary the contract designates for it.

**Blocks 0.7.** **Yes.** Also warrants a 0.6.1 corrective release: any 0.6.0 run
that used a rejection without evidence has already lost its evidence.

---

<a id="motus-adv-001"></a>
### MOTUS-ADV-001 — HIGH — `max_transitions` does not bound a cycle in which nothing commits

**Affected guarantee.**
`contract/graphspec.v1.schema.json` R11 / `graph.py:578-584`: *"spec contains a
cycle but does not declare max_transitions; R11 requires the safety limit for any
cyclic spec."* The limit is mandatory precisely because it is the bound. Also the
whole E-rule family in `contract/validate.py`, which presumes every run reaches
exactly one terminal record.

**Affected files and symbols.**
- `src/vitruvyan_motus/runtime.py:564` — `committed_transitions += 1`, inside `if error is None:` only
- `src/vitruvyan_motus/runtime.py:607-619` — `if limit is not None and committed_transitions >= limit`
- `src/vitruvyan_motus/runtime.py:582-583` — exploration's `continue` disposition breaks out of the attempt loop and routes onward
- `contract/validate.py:1170-1190` — E11 defines the counter as committed transitions, so the contract currently *agrees* with the implementation's counting

**Minimal reproducible attack.**

```python
# a legal cyclic spec: a <-> b, with z as the mandatory exit; max_transitions = 4
SPEC = GraphSpec.from_dict({
    "schema_version": "1.0.0", "name": "cycle", "version": "1.0.0",
    "entry": "a", "max_transitions": 4,
    "nodes": [{"name": n, "effect_class": "pure"} for n in ("a", "b", "z")],
    "transitions": {"a": {"kind": "route", "on": "k", "map": {"loop": "b", "stop": "z"}},
                    "b": {"kind": "next", "to": "a"},
                    "z": {"kind": "terminal"}},
})
seed = State.new("x", decisions=[Decision("k", "loop", NOW)])

def down(state):
    raise RuntimeError("the dependency this node calls is down")

Runtime(SPEC, {"a": down, "b": down, "z": down},
        policy=Policy.EXPLORATION).run(seed)      # never returns
```

**Exact command.**

```bash
.venv/bin/python .attack/a01_transition_limit.py          # runs under a 5 s SIGALRM
.venv/bin/python -m pytest tests/adversarial/test_adv_findings.py -q -k adv_001
```

**Expected behaviour.** A cyclic spec that declared `max_transitions = 4`
terminates. Whatever the counter's definition, the run reaches exactly one
terminal record.

**Observed behaviour.** The run never returns. In 5 seconds it accumulated
**89,126 trace records** with no terminal record of any kind, growing without
bound in memory and — under a persistent durability profile — on the sink. The
two controls isolate the cause exactly:

| Registry | Policy | Result |
|---|---|---|
| all nodes commit | EXPLORATION | `failed` after 14 records, cause `transition_limit_exceeded` |
| all nodes commit | STRICT | `failed` after 14 records, cause `transition_limit_exceeded` |
| **all nodes raise** | **EXPLORATION** | **non-terminating, unbounded** |
| all nodes raise | STRICT | `failed` at the first abort (the raise ends the run) |

**Why the difference matters.** `Policy.EXPLORATION` is a first-class,
contract-defined policy (guarantees.md §5 item 6, §4's frozen Terraveler
surface) whose entire purpose is *record-and-continue past failures*. The
triggering condition — every node in a retry cycle failing — is the ordinary
consequence of a downstream dependency being unavailable, which is exactly the
situation `max_transitions` exists to survive. The consequences are a hung
process, unbounded memory, an unbounded write amplification against a
`synchronous` sink, and a run with **no terminal outcome at all**: the single
outcome the E-rules and `RunResult.status` both assume always exists.

**Deterministic or intermittent.** Fully deterministic.

**Regression test.**
`tests/adversarial/test_adv_findings.py::test_adv_001_exploration_cycle_reaches_a_terminal_within_the_declared_limit`
(bounded through `stream()` with a 200-record budget so the reproduction can
never hang the suite)
`tests/adversarial/test_adv_findings.py::test_adv_001b_control_committing_cycle_is_bounded` (passes)

**Probable root cause.** `committed_transitions` counts *commits*, but the loop
that must be bounded is *activations*. Under `disposition == "continue"` the
executor advances to the next node without a commit, so the counter is frozen
while the cycle turns.

**Minimal correction direction** *(not implemented)*. Count node **activations**
(or routing steps) against a bound in addition to the committed-transition count.
Note that this cannot be a pure runtime change: `contract/validate.py`'s E11
currently *refuses* a `transition_limit_exceeded` record unless at least
`max_transitions` committed transitions are on record, so emitting that cause
after 4 non-committing activations would be a contract violation. The minimal
coherent correction is therefore a versioned contract amendment plus an ADR —
either widening E11's admissibility to "committed transitions **or**
activations ≥ max_transitions", or introducing a distinct cause kind
(e.g. `activation_limit_exceeded`) with its own E-rule — and only then the
runtime counter. Until then, a defensive interim measure would be for
EXPLORATION to treat a full cycle with zero commits as non-progress.

**Blocks 0.7.** **Yes** — a runtime that can be made not to terminate, and to
produce no terminal outcome, by a node that merely keeps failing is not a stable
base for further contract work.

---

<a id="motus-adv-003"></a>
### MOTUS-ADV-003 — HIGH — the published trace-preparation SLO and its CI gate measure a memoisation cache hit

**Affected guarantee.**
`contract/guarantees.md` §3, Motus native SLO table row 3: *"Motus trace
preparation / `json.dumps` | ≤ 1.5x | **0.8x** | 5% / 16%"*.
`README.md` §"Performance profile": *"0.788x trace preparation versus
`json.dumps`"*.
`contract/README.md` §"Rules the contract imposes on itself" item 1: *"The
measured cost of per-event defensive validation in the predecessor kernel was
4.1× the pure serialization; that mistake is not repeated."*

**Affected files and symbols.**
- `benchmarks/bench_motus.py:161-165` — `timeit(result.trace.to_dict)` then `timeit(json.dumps(document))`, both on one already-serialised `Trace`
- `src/vitruvyan_motus/trace.py:355-367` — `to_dict()` = `json.loads(self.to_json())`; `to_json()` memoises into `_json_cache`
- `benchmarks/check_slo_baseline.py` — `serialization_ratio` derived from `to_dict_min_ms / json_dumps_min_ms`
- `benchmarks/candidate-v0.6.0-epyc-py310.json` — the committed evidence

**Minimal reproducible attack — no host required.** `Trace.to_dict()` is
`json.loads(json.dumps(view))`. It performs strictly *more* work than
`json.dumps(document)` alone. A measured ratio **below 1.0 is therefore
arithmetically impossible** for genuine preparation. The committed evidence
reports exactly that, in all five independent runs:

| committed run | `to_dict_min_ms` | `json_dumps_min_ms` | ratio |
|---|---:|---:|---:|
| 0 | 7.8633 | 11.4383 | 0.6875 |
| 1 | 7.6656 | 9.8733 | 0.7764 |
| 2 | 7.9786 | 10.0304 | 0.7954 |
| 3 | 8.0666 | 10.0699 | 0.8011 |
| 4 | 7.9352 | 11.2289 | 0.7067 |

The only way `dumps`+`loads` beats `dumps` is if the `dumps` half does not run.
It does not: `timeit` performs 2 warmups and 7 measured samples on the **same**
`Trace`, so the first warmup fills `_json_cache` and every measured sample times
a C-level `json.loads` of an already-built string.

**Exact command.**

```bash
.venv/bin/python .attack/a08_performance.py
.venv/bin/python -m pytest tests/adversarial/test_adv_findings.py -q -k adv_003
```

**Expected behaviour.** The quotient published as "trace preparation /
`json.dumps`" measures the cost of preparing a trace document, so a regression in
serialisation is visible to the gate.

**Observed behaviour.** The permanent test counts encoder invocations across the
benchmark's own call pattern and is fully host-independent:

> **9 `to_dict()` calls invoked the JSON encoder 1 time.**

Timings on this host (3.12.3 / EPYC — shape only, not an SLO assertion), 1,000-node realistic trace, 3,002 records:

| measurement | value | ratio vs `json.dumps` |
|---|---:|---:|
| `to_dict`, benchmark's warm method | 16.50 ms | **1.31x** |
| `to_dict`, cold trace per sample | 74.76 ms | **5.93x** |
| `to_json` alone, cold trace per sample | 55.52 ms | **4.40x** |
| `json.dumps(document)` | 12.62 ms | 1.00x |

**Why the difference matters.** Three consequences, in increasing severity.
(i) README publishes a number, to three decimal places, describing an operation
that was not measured. (ii) guarantees.md §3 is a *normative* surface with a
named enforcement point; its Motus row 3 records `0.8x` against a `≤ 1.5x`
target, converting what is in reality an inherited ~4.4x debt into a headline
achievement — and guarantees.md §3 itself insists that "the two Axis rows that
miss their targets remain historical debts recorded as measured reality, not
achievements". (iii) The gate cannot regress-detect: a change that doubled real
serialisation cost would leave the measured quotient untouched, because the
measured quotient is a cache read. Separately, `to_dict`/`to_json` are not on the
persist path at all — `_ObservationHub.persist` deep-copies records and hands
them to the sink — so the row's Axis-inherited label "Trace serialization
(persist path)" does not describe the Motus code being timed either.

**Deterministic or intermittent.** Deterministic. The encoder-count reproduction
is exact; the arithmetic argument from the committed evidence needs no execution.

**Regression test.**
`tests/adversarial/test_adv_findings.py::test_adv_003_repeated_to_dict_actually_re_prepares_the_document`

**Probable root cause.** `Trace` memoises `to_json` (a sound design decision for
the library) and the benchmark's repeat-on-one-object shape silently converts
that memoisation into the measurement.

**Minimal correction direction** *(not implemented)*. Build a **fresh** `Trace`
per measured sample (the runs are already produced in a loop; retain 9 results
and time one `to_dict()` on each), or time `Trace._view()` + a direct
`json.dumps` of the view. Then re-collect five runs on the ADR-006 reference
profile, republish the true ratio in guarantees.md §3, and — since the true ratio
will land above the 1.5x target — record it as a **measured debt** in the same
honest style as the two Axis rows, with an ADR. `check_slo_baseline.py` needs no
change once its input is correct.

**Blocks 0.7.** **Yes.** Not because the runtime is slow — 4.4x may well be
acceptable and is roughly the inherited Axis figure — but because a normative
published number and the executable gate that guards it are both measuring the
wrong operation. Correcting the measurement is cheap; shipping 0.7 on top of a
gate that cannot see the metric it names is not.

---

<a id="motus-adv-004"></a>
### MOTUS-ADV-004 — MEDIUM — state reads are linear in the committed log, and the SLO's node never reads

**Affected guarantee.**
`contract/guarantees.md` §3, Motus SLO row 4: *"Motus positive superlinear
accumulation at n = 1000 | < 10% | **0%**"*.
`README.md`: *"no positive superlinear term in the measured profile"*.
`contract/node-protocol.md` §3 — record-and-compare reads are the protocol's
central mechanism.

**Affected files and symbols.**
- `src/vitruvyan_motus/state.py:256-259` — `State._items` rebuilds a filtered list over the whole committed log on every call
- `src/vitruvyan_motus/state.py:288-304` — `State._lookup` calls `_items` per lookup
- `src/vitruvyan_motus/state.py:346-358` — `State._latest_decision` likewise (one per routed transition)
- `benchmarks/bench_motus.py:53-63` — `AppendFactNode` writes and never reads

**Minimal reproducible attack.** A `str` subclass that counts equality
comparisons makes the scan visible without any timing:

```python
class CountingKey(str):
    comparisons = 0
    def __eq__(self, other):
        type(self).comparisons += 1
        return str.__eq__(self, other)
    def __hash__(self): return str.__hash__(self)

state = State.new("x", facts=[Fact(f"k{i}", i, "s", NOW) for i in range(512)])
CountingKey.comparisons = 0
state.fact(CountingKey("k0"))          # -> 512 comparisons
```

**Exact command.**

```bash
.venv/bin/python .attack/a08_performance.py
.venv/bin/python -m pytest tests/adversarial/test_adv_findings.py -q -k adv_004
```

**Expected behaviour.** One state read is not proportional to the size of the
committed log, and the published superlinearity figure characterises a workload
that exercises the documented read surface.

**Observed behaviour.** Comparison counts scale exactly with log size (64 → 64,
512 → 512, for both the oldest key and an absent key), and the `_items()` list
rebuild is O(n) even for a newest-key hit that the counter cannot see. Timed on
this host (shape only):

| workload | n=100 | n=250 | n=500 | n=1000 | growth |
|---|---:|---:|---:|---:|---|
| write-only (the SLO's node) | 62.9 µs/node | 66.1 | 62.3 | **67.2** | flat — linear |
| write + **one** read | 82.2 µs/node | 97.1 | 124.5 | **147.2** | 1.8x — clearly superlinear |

**Why the difference matters.** The claim is textually defensible — the profile
*as measured* really has no positive superlinear term — but the measured profile
deliberately excludes the mechanism the node protocol is built around. A node
that reads one fact is not exotic; it is the normal shape of every routing,
validation and enrichment node in the README's own examples. A 1,000-node run of
such nodes costs 2.2x the 56.25 µs/node ADR-006 ceiling on this host and grows
quadratically thereafter. An operator reading README's bullet will not expect
that.

**Deterministic or intermittent.** The comparison-count reproduction is
deterministic; the timing table is a host observation quoted for shape only.

**Regression test.**
`tests/adversarial/test_adv_findings.py::test_adv_004_one_state_read_does_not_grow_with_the_committed_log`

**Probable root cause.** `_items()` materialises `[item for item in self._log if
item.collection == collection]` on every lookup instead of maintaining a
per-collection index, or per-key tail pointers, alongside the append-only log.

**Minimal correction direction** *(not implemented)*. Two independent steps.
(1) Add a persistent per-collection (and ideally per-key) index to `State`, built
incrementally on commit, so a lookup is O(1)/O(log n) without giving up the
append-only log or the origin metadata that provenance depends on.
(2) Add a read-performing node to `benchmarks/bench_motus.py` as an additional
characterised workload — the existing write-only row stays for continuity — and
publish its superlinearity figure beside the current one. Step (2) alone would
make the claim honest even before step (1) lands.

**Blocks 0.7.** No — bounded, with a practical workaround (read once into a local
variable; avoid re-reading in long runs). Recommended for the 0.7 backlog, with
the README/guarantees qualification tightened in the meantime.

---

<a id="motus-adv-009"></a>
### MOTUS-ADV-009 — MEDIUM — `cancel()` requested before `run()` is silently discarded

**Affected guarantee.** `Runtime.cancel` is a public, documented method
(guarantees.md §6: *"pause/cancel through the runner's API"*) with no stated
ordering constraint. guarantees.md §6 further requires that cancellation "lands
as the trace-recorded `run_cancelled`".

**Affected files and symbols.**
- `src/vitruvyan_motus/runtime.py:203-206` — `Runtime.cancel`
- `src/vitruvyan_motus/runtime.py:249` — `self._cancel_reason = None` inside `_start`

**Minimal reproducible attack.**

```python
runtime = Runtime(spec, {"a": node_with_external_effect})
runtime.cancel("shutdown signal arrived")     # before the run starts
result = runtime.run(State.empty("x"))
assert result.status == "completed"           # the node ran; the effect happened
```

**Exact command.**

```bash
.venv/bin/python -m pytest tests/adversarial/test_adv_findings.py -q -k adv_009
```

**Expected behaviour.** A cancellation the caller has already requested is either
honoured (a `run_cancelled` terminal with `active_attempt: null`, no node
invoked) or refused loudly. It is not silently dropped.

**Observed behaviour.** `_start` unconditionally clears `_cancel_reason`, so the
run executes in full — every node, and every external effect those nodes perform
— and reports `completed`. Nothing in the evidence records that a cancellation
was ever requested. Cancelling one record later (from a listener, or via
`StreamDriver`) works correctly.

**Why the difference matters.** The realistic caller is a supervisor that
receives SIGTERM between constructing the `Runtime` and starting it, or a
scheduler that cancels a queued job. The race window is small but the
consequence — performing external effects the operator explicitly asked to stop
— is the one cancellation exists to prevent. The trace stays truthful (the run
genuinely did complete), which is why this is MEDIUM and not higher.

**Deterministic or intermittent.** Fully deterministic.

**Regression test.**
`tests/adversarial/test_adv_findings.py::test_adv_009_cancellation_requested_before_the_run_starts_is_honoured`

**Probable root cause.** `_start` resets per-run state, and `_cancel_reason` is
treated as per-run state so a `Runtime` can be reused after a cancelled run. The
reset is correct in intent but is applied to a value the caller may legitimately
have set before the run.

**Minimal correction direction** *(not implemented)*. Distinguish "cancellation
requested for the next run" from "cancellation of the run in flight": consume the
pending reason at `_start` rather than discarding it (so it takes effect at the
first cancellation checkpoint, producing `run_cancelled` before the entry node),
and clear it only once consumed. Alternatively, make a pre-run `cancel()` raise
so the caller learns it had no effect. The first preserves the intent; the second
is smaller.

**Blocks 0.7.** No — workaround: cancel through `StreamDriver`, or after the
first yielded record.

---

<a id="motus-adv-005"></a>
### MOTUS-ADV-005 — LOW — a node with a defaulted second positional parameter silently receives `RunContext`

**Affected guarantee.** `contract/node-protocol.md` §1.1 defines exactly two node
shapes: `node(state)` and `node(state, ctx)`.

**Affected files and symbols.** `src/vitruvyan_motus/runtime.py:93-108` —
`_accepts_context` classifies on positional arity alone.

**Minimal reproducible attack.**

```python
def node(state, cache={}):        # the ordinary closure-by-default-arg idiom
    cache["seen"] = True          # -> TypeError: 'RunContext' does not support item assignment
    return state
```

**Exact command.**

```bash
.venv/bin/python -m pytest tests/adversarial/test_adv_findings.py -q -k adv_005
```

**Expected behaviour.** A parameter with a default is not a `ctx` parameter; such
a node is either called as `node(state)` or refused at construction, the way a
three-parameter node already is.

**Observed behaviour.** `_accepts_context` returns `True` and the executor passes
the `RunContext` as `cache`. The failure surfaces as an ordinary `NodeFailed`
with a confusing `TypeError` cause, or — worse — silently, if the node merely
stores or ignores the parameter. `Runtime.__init__` correctly refuses zero, three
and `*args` shapes, so this is the one ambiguous arity that slips through.

**Deterministic or intermittent.** Fully deterministic.

**Regression test.**
`tests/adversarial/test_adv_findings.py::test_adv_005_defaulted_second_parameter_is_not_treated_as_run_context`

**Probable root cause.** `_accepts_context` computes `required` but only uses it
in a check that can never fail (`len(required) > len(positional)`); it never asks
whether the *second* parameter is required.

**Minimal correction direction** *(not implemented)*. Treat a node as
ctx-accepting only when the second positional parameter has no default; refuse a
defaulted second positional parameter at construction with the existing
`must have signature (state) or (state, ctx)` message, so the ambiguity is
rejected rather than guessed. An explicit opt-in (annotation or marker) would be
an alternative but is a larger contract change.

**Blocks 0.7.** No.

---

<a id="motus-adv-006"></a>
### MOTUS-ADV-006 — LOW — `code_fingerprint` is captured once per `Runtime`, not per run

**Affected guarantee.** `contract/node-protocol.md` §6.3: `config_fingerprint`
"binds the callable's *configuration* — because source alone does not identify
behavior for partials, bound methods, class instances or closures".

**Affected files and symbols.** `src/vitruvyan_motus/runtime.py:143-157` —
identity rows and `_code_fingerprint` computed in `__init__`;
`runtime.py:54-74` — `_config_fingerprint` calls `motus_config()` once.

**Minimal reproducible attack.**

```python
node = ConfiguredNode(1)                       # exposes motus_config()
runtime = Runtime(spec, {"a": node})
first  = runtime.run(State.empty("a"))         # writes 1
node.value = 999
second = runtime.run(State.empty("b"))         # writes 999
first.trace.run["graph"]["code_fingerprint"] == second.trace.run["graph"]["code_fingerprint"]  # True
```

**Exact command.**

```bash
.venv/bin/python .attack/a05_graph_fingerprint.py
.venv/bin/python -m pytest tests/adversarial/test_adv_findings.py -q -k adv_006
```

**Expected behaviour.** Two runs whose node configuration differs — and which
demonstrably produce different writes — do not share one `code_fingerprint`.

**Observed behaviour.** They share it. The second run's trace attests a code
identity that does not describe the code that ran. `Runtime` is explicitly
reusable (the `_running` guard forbids only *overlapping* runs, and
`bench_motus.py` reuses one `Runtime` across every sample), so this is a
supported usage pattern.

**Deterministic or intermittent.** Fully deterministic.

**Regression test.**
`tests/adversarial/test_adv_findings.py::test_adv_006_code_fingerprint_tracks_the_configuration_that_actually_ran`

**Probable root cause.** Construction-time capture of a value that the contract
defines as a property of the callable's current configuration.

**Minimal correction direction** *(not implemented)*. Recompute the config
half of the identity rows at `_start` (source hashes can stay cached — source
cannot change mid-process), or document explicitly in `node-protocol.md` §6.3
that `config_fingerprint` binds the configuration **as of Runtime construction**
and that a node whose `motus_config()` varies across runs violates §8. The
documentation route is cheaper and arguably more honest, since a node that
mutates its own configuration between runs is already outside §8's rules.

**Blocks 0.7.** No.

---

<a id="motus-adv-007"></a>
### MOTUS-ADV-007 — LOW — a `TraceSink` attached to an `in-memory` run is silently discarded

**Affected guarantee.** ADR-004 / guarantees.md §6: *"the durable surface is
opened as `TraceSink.open_run(header) -> TraceRunSink`. The isolated header is
delivered exactly once before the first record."*

**Affected files and symbols.** `src/vitruvyan_motus/observers.py:106-113`
(validation accepts the combination), `observers.py:128-132` (`bind` returns
early), `observers.py:183-185` (`persist` returns early).

**Minimal reproducible attack.**

```python
sink = InMemoryTraceSink()
Runtime(spec, nodes, durability_profile=DurabilityProfile.IN_MEMORY, sink=sink).run(state)
assert sink.runs == ()        # open_run was never called; not one record written
```

**Exact command.**

```bash
.venv/bin/python -m pytest tests/adversarial/test_adv_findings.py -q -k adv_007
```

**Expected behaviour.** A configuration that can never do anything is refused —
`_ObservationHub` already refuses the mirror-image mistake (`buffered` or
`synchronous` with `sink=None`) with a clear `ValueError`.

**Observed behaviour.** Accepted and silently ignored: no `open_run`, no header
delivery, no records. An operator who attached durable evidence but left the
default profile discovers it only when the evidence is needed.

**Why this is LOW and not higher.** The run header truthfully declares
`durability_profile: "in-memory"`, so the *evidence* never overstates its own
guarantee; only the operator's expectation is defeated.

**Deterministic or intermittent.** Fully deterministic.

**Regression test.**
`tests/adversarial/test_adv_findings.py::test_adv_007_attaching_a_sink_to_an_in_memory_run_is_not_silent`

**Minimal correction direction** *(not implemented)*. Refuse `in-memory` +
non-`None` sink in `_ObservationHub.__init__` with a message naming both, exactly
as the inverse case is refused today. (A weaker alternative — opening the run and
delivering records without any persistence promise — would blur the profile's
meaning and is not recommended.)

**Blocks 0.7.** No.

---

<a id="motus-adv-008"></a>
### MOTUS-ADV-008 — LOW — the Listener surface is isolated by delivery, not by capability

**Affected guarantee.** `contract/guarantees.md` §6, **Listener**: *"Structurally
non-intervening: per-listener isolation in the dispatch layer; a listener's
exception is recorded (counted, logged) and swallowed; there is NO critical flag
on this surface — **nothing a listener does can affect execution, by construction
rather than by convention**."*

**Affected files and symbols.** `src/vitruvyan_motus/observers.py:212-222`
(`_ObservationHub.notify`, synchronous on the runner thread);
`src/vitruvyan_motus/runtime.py:203-206` (`Runtime.cancel` is public).

**Minimal reproducible attack.**

```python
class CancellingListener:
    def on_record(self, record):
        if record["kind"] == "run_started":
            runtime.cancel("cancelled from a listener")

result = runtime.run(state)
# status == "cancelled"; the entry node was never invoked
```

**Exact command.**

```bash
.venv/bin/python .attack/a04_failure_cancel_sink.py
.venv/bin/python -m pytest tests/adversarial/test_adv_findings.py -q -k adv_008
```

**Expected behaviour.** As written, the guarantee says a listener *cannot* affect
execution structurally.

**Observed behaviour.** A listener terminated the run before the entry node ran.
A second, purely structural route exists and needs no `Runtime` reference at all:
delivery is synchronous on the runner thread (correctly documented in the same
section), so a listener that blocks, sleeps or loops stalls the run indefinitely.

**Why this is LOW.** Everything the listener achieved is *recorded*: the run
terminated as `run_cancelled` with a reason, and no evidence is falsified. The
real isolation properties the section promises — per-listener exception
containment, deep-copied delivery, mutation affecting only copies — all hold
(verified in `test_adv_resisted.py`). What fails is the absoluteness of the
"by construction" clause.

**Deterministic or intermittent.** Fully deterministic.

**Regression test.**
`tests/adversarial/test_adv_findings.py::test_adv_008_listener_cannot_affect_execution`

**Minimal correction direction** *(not implemented)*. This is best resolved in
the **contract**, not the code: narrow guarantees.md §6 to what is actually
constructed — "a listener cannot alter the trace, the state, or any routing
decision; delivered records are isolated copies and listener exceptions are
counted and swallowed. Delivery is synchronous on the runner thread, so a
listener that blocks delays the run, and a listener that holds the `Runtime` can
use its public cancellation API like any other caller." If instead the absolute
reading is intended, listener dispatch would have to move off the runner thread —
a considerably larger, explicitly-versioned change that §6 already flags as "a
later, explicitly-versioned change".

**Blocks 0.7.** No.

---

## E. Rejected hypotheses

Attacks that were seriously attempted and that the implementation **correctly
resisted**. Each is pinned in `tests/adversarial/test_adv_resisted.py` (80 tests,
all passing) so the property cannot be lost silently.

### State isolation and provenance (surface C)

| # | Hypothesis | Why it was rejected |
|---|---|---|
| 1 | A node can mutate a value **after** writing it and change committed evidence | Values are deep-copied at the write boundary; the trace and the committed state both keep the pre-mutation value |
| 2 | A node can mutate a value it **read** and reach committed state through the alias | Reads return deep copies; the committed value is untouched |
| 3 | A node can substitute a foreign `State`, truncating or replacing history | `_committed` checks lineage identity **and** log identity; the attempt is recorded `raised` with structurally empty writes |
| 4 | A node can read its own uncommitted writes, or scan with pending writes, and hide it | Both raise; the read is captured before the refusal |
| 5 | Two writes to one key in one transition collapse or hide the overwrite | Both are recorded in order; the most recent wins on read, exactly as §2.1 requires |
| 6 | Duplicate seeded `Decision` keys make routing name an ambiguous origin | The route names `{"kind": "initial", "index": 1}` — the exact entry, not an equal payload |
| 7 | An overwritten `Decision` lets routing cite the stale entry | The route names the committing transition's seq and index |
| 8 | JSON `true` matches route key `"true"`, or `1` matches `"1"` | Routing requires `isinstance(value, str)`; every non-string value (`True`, `False`, `1`, `0`, `1.0`, `None`, list, dict) produces `miss`. Boolean/number conflation does not occur |
| 9 | `absent`, `scan` or `header` reads escape `reads_declared` | All four appear in `violations` (`later`, `nope`, `intent`, `facts`) — the cross-review v3 regression has not returned |
| 10 | A hand-forged `{"kind": "redacted", …}` can be written as a value | Refused at any depth — top level, nested in an object, inside an array — and also in run metadata |
| 11 | NaN / Infinity / tuple / set / `datetime` / non-string keys reach the trace | All refused at the write boundary with `ValueError`/`TypeError` |

### Execution, retry, failure (surfaces B, D)

| # | Hypothesis | Why it was rejected |
|---|---|---|
| 12 | A raised attempt can commit writes | Writes are structurally emptied; reads and context draws are preserved, exactly per §7.2 |
| 13 | Retry exhaustion loses attempts or reports success | All three attempts recorded `(1,raised,retry) (2,raised,retry) (3,raised,abort)`, then `run_failed` with cause `node_failure`; `NodeFailed.state` carries the salvaged state |
| 14 | A run can produce two terminals, or records after a terminal | Never observed; `Trace.from_dict` also refuses both on load |
| 15 | A route miss can be turned into success under STRICT | STRICT → `run_failed` cause `route_miss`; EXPLORATION → `run_completed`, which is the contract-defined divergence, not a defect |
| 16 | A declaration violation can commit under STRICT | Converted to `DeclarationViolation` before commit; writes empty; `run_failed` |
| 17 | A node raising `BaseException` erases evidence | The `attempt_started` is left unclosed and the exception propagates — precisely what §7.3 specifies as "visible, attributable evidence, never an erased gap" |
| 18 | `StreamDriver.close()` abandons the generator instead of recording cancellation | Lands `run_cancelled` with `active_attempt: {"node": "a", "attempt": 1}` |
| 19 | Attempt numbers can repeat or skip | Never observed across every retry, exploration and cancellation scenario in the sweep |

### Trace integrity (surface E)

| # | Hypothesis | Why it was rejected |
|---|---|---|
| 20 | The shipped runtime emits evidence its own validator rejects | **23-case sweep**: 22 of 23 executions — happy path, nested/unicode/float values, redaction, context draws, retry-then-success, retry exhaustion, exploration-past-failure, matched/default/miss routing, non-string decisions, transition-limit, external+recorded effects, declaration violations under both policies, mid-stream cancellation, seeded state, resume segments, buffered and synchronous profiles — produced **zero** violations. The 23rd is MOTUS-ADV-002, which fails before validation |
| 21 | JSON and JSONL validation diverge | **Zero divergences** across all 23 cases and all 5 sink-failure scenarios: identical rule, path and message sets |
| 22 | A caller can mutate `trace.run` or `trace.records` in place | Both properties deep-copy on access |
| 23 | Seq gaps or post-terminal records survive `Trace.from_dict` | Both raise `ValueError` |
| 24 | CRLF slips past as LF | `JSONL3`, reported on the correct line; the CLI reads bytes and decodes explicitly, so no laundering occurs |
| 25 | A truncated final line is treated as malformed rather than incomplete | `T3/INCOMPLETE`, never `JSONL2` — the documented distinction holds |
| 26 | Malformed UTF-8 is silently repaired | CLI exits 2 with an explicit decode error |
| 27 | A duplicated terminal line passes | `T1` + `T3` |
| 28 | Blank lines are a smuggling channel | Filtered deliberately and consistently by `validate_jsonl`; `Trace.to_jsonl` never emits them. A design choice, not a defect |
| 29 | Replay capability can be upgraded, or constraints can disappear | `downgrade` refuses any increase and unions constraints; header keeps the declaration, terminal carries the degraded value (`full` → `partial` + `node:a:opaque_config`) |

### Sinks and durability (surface F)

| # | Hypothesis | Why it was rejected |
|---|---|---|
| 30 | A sink failure can be swallowed into logical success | All five refusal points (`open_run`, first record, mid-trace, terminal, already-failed terminal) raise `SinkFailed`/`NodeFailed`; `run_completed` never appears |
| 31 | A sink-failure trace is itself malformed | All five produce **contract-valid** evidence in both JSON and JSONL |
| 32 | `buffered` hides failure evidence inside the loss window | With `chunk_records=1000, flush_interval_ms=600000`, the raised transition forced a flush and the terminal flushed separately — exactly invariant II's table |
| 33 | Killing the process contradicts the declared profile | `SIGKILL`-equivalent at a controlled record: `synchronous` had persisted every record up to the kill; `buffered` had persisted none, matching its declared window. Neither over-delivers or under-delivers |
| 34 | A sink can mutate records, the header, or the run | Deep-copied before delivery; `run_id`, record kinds and committed state all unaffected |
| 35 | The best-effort terminal after a sink failure fabricates a seq gap or dangling cause | The already-failed case keeps the primary terminal in memory, persists what it can, and validates clean (0 violations) |
| 36 | A run id with traversal components escapes the file sink | The native runtime writes no files. The compat `FileTraceObserver` normalised `../escape`, `/abs/path`, `a/b/c` and `..\win` into flat names inside its directory — inherited-corpus item 4 holds. Trace schema permits any 1..200 char run id, so consumer sinks own their own normalisation (NOTE-4) |

### Observers and effects (surface G)

| # | Hypothesis | Why it was rejected |
|---|---|---|
| 37 | A listener exception reaches the caller or aborts the run | A listener raising `SystemExit` is counted and swallowed; the run completes |
| 38 | A listener can mutate the trace through the delivered record | Delivery is a deep copy |
| 39 | A `pure` node can emit an effect | Double-gated: refused inside `RunContext.record_effect`, and re-checked after the attempt |
| 40 | A `recorded_effect` node can emit an `external_effect` | Refused at both gates; the external entry is stripped |
| 41 | Retries duplicate or leak effects between attempts | Per-attempt cursors: `[["POST #1"], ["POST #2"], ["POST #3"]]` — each attempt records only its own, and effects from raised attempts are preserved as evidence |

### Replay and resume (surface H)

| # | Hypothesis | Why it was rejected |
|---|---|---|
| 42 | A trace can be bundled with a different graph | Name, version, spec schema version **and** fingerprint are all checked |
| 43 | A forged route target that names another declared node redirects a resume | `_assert_bundle_semantics` recomputes routing from the GraphSpec and the replayed committed state — the forgery is refused. README's claim here is exact |
| 44 | Forging the committed decision value instead of the route evades that check | Also refused — the recomputation covers `on`, `value`, `origin`, `outcome`, `selected` and the full candidate list |
| 45 | A terminal run can be resumed | `UnsafeResume: a terminal trace cannot be resumed` |
| 46 | Resume can reuse the source run id | Refused at two independent layers |
| 47 | An external effect without an idempotency key, or with an `unknown` receipt, can be resumed | Both fail closed; only key + `completed` receipt is allowed. An in-flight `attempt_started` on an external-effect node is refused too |
| 48 | Playback silently re-executes node code | It does not; committed state is reconstructed from the recorded writes alone |
| 49 | `verify()` passes a node that only claims to be pure | `ReplayMismatch ... field writes` |
| 50 | Replay, resume and re-execution are conflated | Three distinct entry points with distinct results; resume creates a new run id, records `resume.source_run_id`/`start_node`/`bundle_fingerprint`, sets `metadata.causation_id`, and never rewrites persisted history |

### Graph, packaging, viewer (surfaces A, J, L)

| # | Hypothesis | Why it was rejected |
|---|---|---|
| 51 | An invalid graph can be constructed by a path other than `from_dict` | Validation is in `__post_init__`; every construction path is covered |
| 52 | Mutating the caller's dict after construction drifts the fingerprint | `_deep_freeze` at construction; `to_dict()` hands back a detached copy |
| 53 | An execution-relevant change leaves the fingerprint unchanged | Entry, version, effect class, omitted-vs-explicit default, `reads_declared` and `max_transitions` all move it |
| 54 | Two materially different specs share a fingerprint | None found. Conversely, semantically equivalent specs (node array order, `8` vs `8.0`) *do* differ — documented, conservative, fail-safe |
| 55 | The node registry can disagree with the topology | Refused with `missing=`/`extra=` |
| 56 | A malformed node signature is accepted | Zero-arg, three-arg and `*args` all refused at construction (the one gap is MOTUS-ADV-005) |
| 57 | Deep or wide graphs blow a limit | 2,000-node linear and 2,000-branch wide graphs validate and fingerprint in ~48 ms; 400-deep nested values accepted without `RecursionError` |
| 58 | The wheel leaks `axis`, tests, contract files or dependencies | None present; imports cleanly from `/tmp` outside the checkout |
| 59 | The compatibility layer leaks a second `Decision` under a native name | `Decision` is native-only, `LegacyDecision` compat-only, `compat.Fact is not vitruvyan_motus.Fact` |
| 60 | The offline viewer executes injected content or fetches remote assets | Hostile run ids and values are escaped; no `http://`/`https://` reference anywhere in the page |
| 61 | `explain()` or the bundle fingerprint is unstable | Byte-identical across independent bundle constructions |
| 62 | Memory grows unboundedly across runs on one `Runtime` | 200 × 100-node runs retained 369 KiB — one live trace, as expected |

### Concurrency (surface I) — reported honestly

| # | Hypothesis | Outcome |
|---|---|---|
| 63 | Two threads on one `Runtime` corrupt the trace | **Not reproduced.** `Runtime._start`'s `if self._running: … ; self._running = True` is a non-atomic check-then-set and the window is real: with `sys.settrace` forcing an interleave at the exact guard line, both threads entered `run()`. But across **200 unsynchronised rounds + 500 `threading.Barrier` rounds + 3 settrace-forced rounds + 5 widened-overlap rounds = 708 total**, every observed trace was coherent (single `run_started`, gapless seqs, one terminal) and every caller received its own run's trace. The barrier rounds fired the guard 9 times; the widened-overlap rounds fired it 5/5. guarantees.md §6 states the runner is single-threaded and fan-out has no representation in GraphSpec v1, so this is out-of-contract usage. Recorded as NOTE-1 rather than a finding, with the raw counts above so the claim can be checked |
| 64 | An abandoned `StreamDriver` permanently locks the `Runtime` | Generator finalisation runs `_managed_execute`'s `finally`; a subsequent `run()` succeeded |
| 65 | A held-open `StreamDriver` allows an overlapping `run()` | Correctly refused |

### Non-determinism (surface H) — allowed by contract

| # | Hypothesis | Outcome |
|---|---|---|
| 66 | A run declaring `replay: full` whose node uses ambient `random.random()` and `datetime.now()` keeps its `full` claim | **Confirmed behaviour, but contract-permitted.** `context_draws` is `[]` and the terminal still reports `{"capability": "full", "constraints": []}`. `node-protocol.md` §6.2 explicitly says detection is *best-effort in v1.0* and names §6.1 as the honest path; guarantees.md invariant IV(2) places the obligation on the node, not the runtime. The claim is falsifiable after the fact — `verify()` catches exactly this node. Recorded as NOTE-3, not a finding |
| 67 | Kernel timestamps, sequences or identifiers bypass the run's clock/identity source | They do not. Every record `ts`, the header `created_ts` and the generated `run_id` come from `_RunController`; `time.monotonic()` appears only in flush scheduling and is never recorded. Invariant IV(1) holds |

### Declarations — allowed by contract

| # | Hypothesis | Outcome |
|---|---|---|
| 68 | `writes_declared` can be evaded by writing a `Rejection` | **Confirmed behaviour, contract-consistent.** Neither `runtime._declaration_violations` nor `validate.py`'s SB4 considers rejections: both check `("facts", "decisions")` only. Runtime and oracle agree, so this is the contract's stated surface, not a divergence. Recorded as NOTE-2 |

---

## F. README claim matrix

Legend — **D**: directly demonstrated by code and tests · **C**: conditionally
true, important limitation · **F**: future direction, clearly labelled ·
**U**: unsupported · **X**: contradicted by runtime behaviour.

| # | README claim | Class | Evidence |
|---|---|:--:|---|
| 1 | "produces a structured, contract-validatable trace together with every result" | **C** | 22/23 executions validate clean in both forms; **MOTUS-ADV-002** makes this false for any run containing an evidence-free rejection |
| 2 | "The trace is not reconstructed from logs after execution. The trace is part of the execution itself." | **D** | Records are emitted inside `_execute` at each state-machine step; the sink stream *is* the trace log |
| 3 | "Apache-2.0 · dependency-free runtime · source release · not yet published on PyPI" | **D** | Wheel declares no runtime dependencies; `License-Expression: Apache-2.0`; import fence verified in a pristine interpreter |
| 4 | The 8-step "What Motus does" list (validate, compile, one semantics, isolate attempts, commit only successful writes, route from recorded decisions, capture reads/writes/retries/draws/effects/receipts, return state + trace) | **D** | Each step separately attacked and confirmed; see §E rows 1-19, 37-41 |
| 5 | "Motus records causal evidence while execution is happening. That evidence is a native runtime output." | **D** | `RunResult.trace` is produced by the executor, not derived |
| 6 | "which GraphSpec and graph version were used" | **C** | `graph_fingerprint` is exact and immune to source mutation; `code_fingerprint` can be stale on a reused `Runtime` (**MOTUS-ADV-006**) |
| 7 | "which nodes ran and in which causal order" | **D** | Executed order, `attempt_started` order and routing selections agreed in every probe |
| 8 | "which state values they read" | **D** | Every origin kind captured, including `absent`, `scan` and `header` |
| 9 | "which writes were committed" | **C** | True for facts and decisions; rejections are outside the declared-write surface by contract (NOTE-2), and **MOTUS-ADV-002** for evidence-free rejections |
| 10 | "which attempts failed, retried, or were cancelled" | **D** | Full retry chains, abort/continue dispositions and `run_cancelled` with `active_attempt` all verified |
| 11 | "which recorded decision selected each route" | **D** | The strongest verified property: origin names collection + index, distinguishes duplicate keys and stale entries, and is recomputed at bundle time. §E rows 6, 7, 43, 44 |
| 12 | "which context draws supplied time, randomness, or generated identifiers" | **C** | True for draws taken through `RunContext`; a node using ambient sources records none and keeps a `full` declaration (NOTE-3, contract-permitted by §6.2) |
| 13 | "which effects were observed and which adapter-supplied receipts were recorded" | **D** | Per-attempt attribution verified under retry; effects from raised attempts preserved |
| 14 | "how the final committed state emerged" | **D** | `playback()` reconstructs committed state from the recorded writes alone, without executing nodes |
| 15 | "Motus does not certify that external information is objectively true … A `Fact` is a recorded assertion" | **D** | No verification machinery exists; correctly scoped |
| 16 | "Motus certifies neither legal compliance nor exactly-once delivery" | **D** | Fail-closed resume; nothing infers exactly-once |
| 17 | "Immutable state. Writes create new state; committed history is append-only." | **D** | Persistent chunked log; lineage/prefix check; §E rows 1-3 |
| 18 | "Causal routing. A route names the exact recorded decision it observed." | **D** | See #11 |
| 19 | "Transactional attempts. Raised or cancelled attempts commit no writes." | **D** | §E row 12 |
| 20 | "Declared nondeterminism. Time, randomness, and generated identifiers **can be** recorded through `RunContext`." | **D** | The permissive wording ("can be") is accurate; see #12 for the limit |
| 21 | "One execution semantics. Compilation changes lookup cost, not behavior." | **D** | `CompiledPlan` is a pre-indexed projection of validated data; one interpreter, no second path |
| 22 | "Minimal core. No LLM SDK, database, or agent-framework dependency." | **D** | Import fence verified |
| 23 | Regulated-environment positioning (MiFID II, EU AI Act, GDPR, sector duties) with "Motus alone is not a compliance system" | **C** | Correctly framed as *technical evidence*, not compliance. Per the brief, the disclaimer is not itself a finding; what is auditable is whether the claimed evidence is generated and preserved per profile — it is (§E rows 30-36), **except** for **MOTUS-ADV-002**, which silently destroys it, and **MOTUS-ADV-001**, which can prevent a terminal record from ever existing |
| 24 | "The current release does not activate cryptographic hash-chain integrity." | **D** | `integrity: {payload_hash: null, prev_hash: null}` on every record; fixture 15 pins non-null as invalid. Honest |
| 25 | Install snippet `pip install -e ".[test]" -c constraints/test.txt` | **D** | Executed as written |
| 26 | "The wheel contains only `vitruvyan_motus`, includes `py.typed`, and declares no runtime dependencies." | **D** | §B.6 |
| 27 | Quick-start code block | **D** | Executed verbatim: prints `23.5`, `completed`, `True`, and a 1,854-char trace |
| 28 | "Nodes may have either `node(state)` or `node(state, ctx)` shape." | **C** | True, but a defaulted second positional parameter is silently classified as `ctx` (**MOTUS-ADV-005**) |
| 29 | "Before resume, persisted routing is recomputed against the bundled GraphSpec and committed state. An edited route cannot redirect execution merely because its forged target is another declared node." | **D** | Exactly true — attacked from both directions (§E rows 43, 44) |
| 30 | The three replay operations (`playback`, `verify`, `explain`/`to_html`) | **D** | All three executed; `verify` falsifies an impure "pure" node |
| 31 | "`ReplayEngine.resume(runtime)` starts a new, causally linked run segment … Persisted history is never rewritten. Resume fails closed for graph mismatches, inconsistent routing, ambiguous boundaries, and external effects without both a non-empty idempotency key and a completed adapter receipt." | **D** | Every clause independently attacked and upheld (§E rows 42-47, 50) |
| 32 | "Motus never claims exactly-once delivery." | **D** | Confirmed throughout |
| 33 | The three observation surfaces (`TraceSink`, `Listener`, `StreamDriver`) | **C** | Correct in substance; the Listener surface's "by construction" absoluteness is overstated (**MOTUS-ADV-008**) |
| 34 | The three durability profiles and their stated guarantees | **D** | Verified under real process termination (§E row 33); the `in-memory`-plus-sink misconfiguration is **MOTUS-ADV-007**, not a profile defect |
| 35 | "A required sink failure prevents logical success. If the required sink itself fails, the final failure record is necessarily best-effort…" | **D** | Five refusal points; the best-effort case behaves exactly as described and still validates |
| 36 | "43.5001 microseconds per node for a realistic 1,000-node full trace" | **C** | Reproduced in shape on a non-reference host (67 µs/node for the same write-only workload). The label "per node" is total per-node cost, not overhead over a bare loop — the no-op row does subtract the baseline, this one does not. Materially true for the workload measured; see **MOTUS-ADV-004** for the workload not measured |
| 37 | "3.08655 milliseconds overhead for a 100-node no-op run" | **D** | Gate recomputes it from committed raw runs; genuinely a baseline-subtracted overhead |
| 38 | **"0.788x trace preparation versus `json.dumps`"** | **U** | **MOTUS-ADV-003.** A ratio below 1.0 is arithmetically impossible for the operation named; the measured quantity is a memoisation cache hit. Genuine ratio here ≈ 4.4x |
| 39 | **"no positive superlinear term in the measured profile"** | **C** | **MOTUS-ADV-004.** Literally true of the measured profile; the measured profile's node never reads state, and adding one read per node makes the run clearly superlinear |
| 40 | "3,002 trace records and zero declaration violations, with no sampling" | **D** | Independently reproduced exactly: 3,002 records, 1,000 facts, 0 violations |
| 41 | "The CI gate recomputes aggregates from the committed raw evidence, verifies the runner identity, and rejects regressions beyond the accepted ADR-006 ceilings." | **C** | Mechanically true and verified. Two of its five inputs do not measure what their labels claim (#38, #39) |
| 42 | "The normative surfaces live in `contract/` … Run the complete suite and contract validator with: …" | **D** | All three commands executed as written |
| 43 | "The public API is explicitly listed in `vitruvyan_motus.__all__`" (7 groups) | **D** | All 41 names resolve; every README-listed name is exported |
| 44 | "The native and legacy decision types are deliberately unambiguous" | **D** | §E row 59 |
| 45 | "Shipped in 0.6" — immutable compiled topology; effect receipts and fail-closed external-effect resume; playback/verify/resume; deterministic explanation and portable bundles; standalone offline HTML viewer; strict read/write declaration enforcement; additive trace schema 1.1; executable performance regression gate | **D** (7 of 8) / **C** (1) | Seven independently confirmed. "Executable performance regression gate" exists and runs, but cannot regress-detect the serialisation row (**MOTUS-ADV-003**) |
| 46 | "Future direction … These are directions, not commitments." | **F** | Correctly labelled |
| 47 | "`axis/`, `orders/`, `poc/` … are excluded from the wheel." | **D** | Verified |

**Summary:** 30 claims directly demonstrated · 12 conditionally true with a
limitation now documented · 1 future direction · **1 unsupported (#38)** · 0
contradicted outright. Claim #1 is downgraded to conditional solely because of
MOTUS-ADV-002.

---

## G. 0.7 gate recommendation

### May 0.7 begin?

**No — not yet.** Three findings must be closed first.

### Which findings block it

| ID | Severity | Why it blocks |
|---|---|---|
| **MOTUS-ADV-002** | CRITICAL | A documented, encouraged node behaviour produces a successful run whose evidence cannot exist as a document. 0.7's stated direction (cryptographic trace integrity, capability enforcement) builds *on top of* the trace being reliably producible. Nothing should be layered on a trace that a plain `Rejection` can destroy |
| **MOTUS-ADV-001** | HIGH | A run that never terminates and never emits a terminal outcome invalidates the assumption every E-rule and every future execution-budget feature rests on. Its correction requires a contract amendment (E11), which is exactly the kind of work that belongs *before* a new minor line opens |
| **MOTUS-ADV-003** | HIGH | The performance gate is the mechanism meant to protect 0.7 from regressions. One of its five rows currently measures a cache read. Fixing it after 0.7 development starts means 0.7 accumulates changes under a gate known to be blind |

### Is a 0.6.1 corrective release required?

**Yes, for MOTUS-ADV-002 alone.** It is the only finding that silently destroys
evidence a consumer already believes they have. Any 0.6.0 deployment whose nodes
record rejections without evidence has been losing its traces since release, with
no error under the default durability profile. A 0.6.1 containing the sentinel
fix (and nothing else) is the smallest safe response.

MOTUS-ADV-001 and MOTUS-ADV-003 do not require a patch release — no shipped
evidence is wrong because of them — but both must be closed before the 0.7
branch opens. MOTUS-ADV-003's correction touches `benchmarks/` and
`guarantees.md` §3 and therefore needs its own ADR, since republishing the true
ratio will move a target row from "PASS" to "measured debt".

### Recommended 0.7 backlog (non-blocking)

| ID | Item |
|---|---|
| MOTUS-ADV-004 | Index `State` reads (or per-key tails) so lookups stop scanning the log; add a read-performing workload to the characterised benchmark set and publish its superlinearity beside the current row |
| MOTUS-ADV-009 | Make a pre-run `cancel()` either take effect at the first checkpoint or raise |
| MOTUS-ADV-005 | Refuse a defaulted second positional parameter instead of guessing it is `ctx` |
| MOTUS-ADV-006 | Recompute the config half of `code_fingerprint` per run, or narrow node-protocol §6.3's wording to "as of Runtime construction" |
| MOTUS-ADV-007 | Refuse `in-memory` + a non-`None` sink, mirroring the existing inverse check |
| MOTUS-ADV-008 | Narrow guarantees.md §6's Listener wording to the isolation actually constructed (or move dispatch off the runner thread as an explicitly-versioned change) |
| NOTE-1 | Consider a real lock, or explicit documentation that `Runtime` is not thread-safe, so the advisory guard is not mistaken for one |
| NOTE-2 | Decide deliberately whether `writes_declared` should cover rejections; if yes, it is a contract amendment (SB4) plus a runtime change, not a runtime change alone |
| NOTE-3 | Consider a cheap ambient-nondeterminism probe (e.g. comparing a monotonic reading across an attempt) so a `full` declaration is at least challenged, and/or tighten README #12's wording |
| NOTE-4 | Document that consumer-authored sinks must normalise `run_id` before using it in a path; the schema permits any 1..200-character string |
| NOTE-5 | Add native `Rejection` round-trip coverage to the ordinary suite — its absence is what let MOTUS-ADV-002 ship |

---

## H. Reproduction appendix

Every command below runs from a clean checkout and reproduces this audit
end to end. Nothing here commits, pushes or merges.

### H.1 Establish the base

```bash
git clone https://github.com/vitruvyan/motus.git
cd motus
git checkout -b audit/0.6-adversarial-opus 2a860a7c5c57f95dd471ccc1ff66bcf7bd10c7f6

git rev-parse HEAD            # must print 2a860a7c5c57f95dd471ccc1ff66bcf7bd10c7f6
git status --short --branch
git diff --stat               # must be empty
```

### H.2 Pinned environment

```bash
python3 -m venv .venv
.venv/bin/pip install -e ".[test]" -c constraints/test.txt
.venv/bin/pip freeze
.venv/bin/python -V
```

### H.3 Baseline gates

```bash
# Pre-existing suite, untouched:            455 passed, 14 skipped
.venv/bin/python -m pytest tests/ -q --ignore=tests/adversarial

# Frozen-path guard:                        Frozen contract paths: PASS
.venv/bin/python tools/check_frozen_paths.py \
    2a860a7c5c57f95dd471ccc1ff66bcf7bd10c7f6 2a860a7c5c57f95dd471ccc1ff66bcf7bd10c7f6

# Frozen corpora are untouched by the audit (both must print nothing):
git diff --stat
git ls-files --others --exclude-standard | grep -E '^(tests/contract|tests/compat)/'

# Strongest proof: Git cannot build a stash commit because no tracked file differs.
git stash create              # must print an empty line

# Benchmark gates:                          both PASS
.venv/bin/python benchmarks/check_slo_baseline.py
.venv/bin/python benchmarks/check_slo_baseline.py \
    --candidate benchmarks/candidate-v0.6.0-epyc-py310.json
```

### H.4 Wheel isolation

```bash
.venv/bin/pip install build
.venv/bin/python -m build --wheel --outdir /tmp/motus-wheel

python3 -c "
import zipfile
print('\n'.join(sorted(zipfile.ZipFile('/tmp/motus-wheel/vitruvyan_motus-0.6.0-py3-none-any.whl').namelist())))"

python3 -m venv /tmp/motus-iso
/tmp/motus-iso/bin/pip install /tmp/motus-wheel/vitruvyan_motus-0.6.0-py3-none-any.whl
/tmp/motus-iso/bin/pip freeze          # only vitruvyan-motus

cd /tmp && /tmp/motus-iso/bin/python -c "
import importlib.util, os, vitruvyan_motus as m
print(m.__version__, m.TRACE_SCHEMA_VERSION, len(m.__all__))
print('py.typed :', os.path.exists(os.path.join(os.path.dirname(m.__file__), 'py.typed')))
print('axis     :', importlib.util.find_spec('axis') is not None)"
cd -
```

### H.5 The adversarial suites

```bash
# Attacks the implementation resisted — all must pass:   80 passed
.venv/bin/python -m pytest tests/adversarial/test_adv_resisted.py -q

# The findings — 10 must fail, 2 controls must pass:     10 failed, 2 passed
.venv/bin/python -m pytest tests/adversarial/test_adv_findings.py -q

# Per finding:
.venv/bin/python -m pytest tests/adversarial/test_adv_findings.py -q -k adv_001   # unbounded cycle
.venv/bin/python -m pytest tests/adversarial/test_adv_findings.py -q -k adv_002   # rejection evidence
.venv/bin/python -m pytest tests/adversarial/test_adv_findings.py -q -k adv_003   # cached to_dict
.venv/bin/python -m pytest tests/adversarial/test_adv_findings.py -q -k adv_004   # linear reads
.venv/bin/python -m pytest tests/adversarial/test_adv_findings.py -q -k adv_005   # ambiguous signature
.venv/bin/python -m pytest tests/adversarial/test_adv_findings.py -q -k adv_006   # stale code_fingerprint
.venv/bin/python -m pytest tests/adversarial/test_adv_findings.py -q -k adv_007   # discarded sink
.venv/bin/python -m pytest tests/adversarial/test_adv_findings.py -q -k adv_008   # listener gating
.venv/bin/python -m pytest tests/adversarial/test_adv_findings.py -q -k adv_009   # pre-run cancel

# Everything together (the honest total):                10 failed, 537 passed, 14 skipped
.venv/bin/python -m pytest tests/ -q
```

### H.6 Exploratory attack scripts

Not part of the permanent suite — they print observations, use timing, threads
and subprocesses, and are deliberately quarantined under `.attack/`.

```bash
.venv/bin/python .attack/a01_transition_limit.py            # ADV-001 (5 s SIGALRM guard)
.venv/bin/python .attack/a02_state_isolation.py             # surface C battery
.venv/bin/python .attack/a03_rejection_evidence.py          # ADV-002, full blast radius
.venv/bin/python .attack/a04_failure_cancel_sink.py         # surfaces D, F, G
.venv/bin/python .attack/a05_graph_fingerprint.py           # surfaces A, J
.venv/bin/python .attack/a06_replay_resume.py               # surfaces H, I
.venv/bin/python .attack/a07_trace_conformance_sweep.py     # surface E: 23-case JSON/JSONL sweep
.venv/bin/python .attack/a08_performance.py                 # surface K (several minutes)
.venv/bin/python .attack/a09_durability_kill.py             # surface F: os._exit at controlled records
.venv/bin/python .attack/a10_sinkfail_conformance_race.py   # surfaces B, F, I
.venv/bin/python .attack/a11_misc_surface.py                # surfaces G, I, J, L
```

### H.7 Finding MOTUS-ADV-003 without running anything

```bash
python3 - <<'PY'
import json
runs = json.load(open("benchmarks/candidate-v0.6.0-epyc-py310.json"))["runs"]
for i, r in enumerate(runs):
    s = r["6_serialization"]["realistic_1000"]
    print(f"run {i}: to_dict={s['to_dict_min_ms']:.4f} ms  "
          f"json.dumps={s['json_dumps_min_ms']:.4f} ms  "
          f"ratio={s['to_dict_min_ms']/s['json_dumps_min_ms']:.4f}")
print("\nTrace.to_dict() == json.loads(json.dumps(view)).")
print("It cannot be FASTER than the json.dumps it contains unless that dumps is cached.")
PY
```

### H.8 Audit artifacts

| Path | Contents | Tracked? |
|---|---|---|
| `audit/MOTUS-0.6-ADVERSARIAL-REPORT.md` | this report | untracked, uncommitted |
| `tests/adversarial/test_adv_findings.py` | 12 tests — 10 finding reproductions + 2 controls | untracked, uncommitted |
| `tests/adversarial/test_adv_resisted.py` | 80 regression tests pinning resisted attacks | untracked, uncommitted |
| `tests/adversarial/__init__.py` | package marker | untracked, uncommitted |
| `.attack/a01…a11` | 11 exploratory scripts, deliberately outside the permanent suite | untracked, uncommitted |

**No runtime file (`src/vitruvyan_motus/`), contract file (`contract/`), ADR
(`adr/`), benchmark (`benchmarks/`), README, existing test, or frozen corpus
(`tests/contract/`, `tests/compat/`) was modified. No defect was fixed. Nothing
was committed, pushed, merged or released.**
