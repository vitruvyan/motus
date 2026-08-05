# ADR-009 — Predecessor removal, and the undeclared async regression

- **Status:** PROPOSED
- **Date:** 2026-08-05
- **Authority:** founder direction opening the 0.7 architecture phase
- **Depends on:** ADR-001 (§Decision 2, the Axis→Motus rename-in-place),
  ADR-003 (§Decision, the one-time `tests/contract/kernel.py` switch)
- **Supersedes:** nothing. ADR-001…008 stand unedited; this ADR records a new
  decision, it does not rewrite an old one.

## Context

Two facts, established by inventory at `v0.6.1` (`4d9370a`).

**The predecessor is inert.** `axis/` was kept on disk because the frozen
conformance corpora ran against it. They no longer do: `tests/contract/
kernel.py:25` binds to `vitruvyan_motus.compat`, and `kernel.py:39` records
`KERNEL_UNDER_TEST = "vitruvyan_motus.compat (Motus 0.5)"`. The compatibility
switch ADR-001 §Decision 2 anticipated, and ADR-003 authorised, has already
been executed. What remains is 5,727 lines of runtime plus 11 test modules
that exercise it — **21.6 % of the passing suite and 0 % of the shipped
product** — together with the whole of its planning and documentation era.

**Motus lost asynchronous execution in the rewrite, and nothing recorded it.**
`axis/` contains 38 `async def` across six modules, including a 493-line
`AsyncRunner`, an SSE event stream and a WebSocket surface with
pause/resume/cancel. `src/vitruvyan_motus/` contains none outside
`compat.py`'s legacy `ConcurrentRunner`. No ADR, no contract file and no
release note states this.

The nearest thing to a record is `guarantees.md:189-195`, which defers
**fan-out**: *"Fan-out has no representation in GraphSpec v1 or trace schema
v1 — deliberately deferred, trigger: the first real consumer that needs
declared concurrent topology."* That deferral has been read as covering
asynchrony. **It does not.** Fan-out is parallelism across branches and is
retrofit-hostile: it would require a new transition kind, a lane identity on a
sealed `RecordBase`, a join record, and a rewrite of T6 (which asserts
*physical* record adjacency) and of the entire E1–E11 successor machine.
Single-lane asynchrony is none of that: one node at a time, awaited, produces
a byte-identical record sequence. The two were conflated, and the conflation
hid a capability regression behind a legitimate deferral.

## Decision

### 1. The predecessor runtime leaves the working tree

Removed: `axis/`, `poc/`, `orders/`, `examples/`, the four `*_PROMPTS.md`
planning scripts and `PHASE_2_1_SUMMARY.md`, the eleven Axis-era test modules,
and the eleven Axis-era files in `docs/`.

**`benchmarks/` is kept whole, deliberately.** An earlier draft of this
decision removed the two Axis collectors; running the gates refused it, and the
refusal was right twice over. Mechanically, `check_slo_baseline.py:25` imports
`TRACKED` from `collect_baseline` — the shared specification of metric paths,
not an Axis artifact — so its removal broke both the SLO gate and
`tests/test_ci_foundation.py`. Substantively, `baseline-v0.4.0-epyc-py310.json`
is still validated by CI, and deleting the producer of evidence one continues
to gate on is exactly the asymmetry this project rejects elsewhere.
`bench_kernel.py` is therefore retained as that document's provenance. It is
not runnable in this tree, because the runtime it measures is archived at
`v0.6.1`; that is a stated consequence, not a silent breakage.

Preservation is unaffected and is stated precisely, because "byte-preserved"
was a real commitment:

- the tag **`v0.6.1`** holds every removed file byte-identical and is
  permanent;
- the **`vitruvyan-axis` 0.4.0 distribution remains independently pinnable**
  by consumers who have not migrated — deleting a directory here does not
  unpublish a wheel;
- the surface those consumers actually depend on is
  `src/vitruvyan_motus/compat.py`, which is **untouched**, and it is exercised
  by `tests/compat/` and `tests/contract/`, which are **frozen and untouched**.

The committed Axis SLO baseline `benchmarks/baseline-v0.4.0-epyc-py310.json`
**stays**: `check_slo_baseline.py` still validates guarantees.md §3's Axis rows
against it, and those rows are recorded measured reality, not a live target.
Its collector is archived with the rest at `v0.6.1`.

Two amendments follow mechanically and are the whole cost:

- `tests/test_motus_packaging.py` — `test_axis_stays_importable_from_the_
  checkout` asserted that the predecessor stays on disk. It is replaced by
  `test_the_predecessor_runtime_is_absent_from_the_working_tree`. Its sibling,
  which asserts `axis` is **not** importable from a venv holding only the Motus
  wheel, is kept: that was always the real packaging guarantee, and it now
  holds for the stronger reason.
- `pyproject.toml`'s header comment stated that `axis/` stays "until the
  compatibility switch". The switch happened; the comment is corrected.

### 2. The async regression is recorded as a debt, not a deferral

Asynchronous node execution is **absent by regression, not by design**. No
contract text ever deferred it. This ADR states the debt so that no future
reader mistakes `guarantees.md`'s fan-out deferral for a decision about
asynchrony.

Two properties are asserted now so the 0.7 design is bounded before it starts:

- **Single-lane asynchrony is contract-neutral.** One node at a time, awaited,
  emits the same seven record kinds in the same total `seq` order; T1's
  gaplessness, T6's adjacency and the E1–E11 successor machine are all
  preserved. Restoring it requires **no schema amendment and no fixture
  regeneration**.
- **It is not fan-out.** Concurrent branch execution remains deferred under
  `guarantees.md:189-195` with its existing trigger, unchanged by this ADR.

### 3. Invariant I is satisfied by construction, not by a harness

`guarantees.md:9-12` requires that *"any future alternative path must prove
trace-equivalence against the interpreter per release, in CI."* No such
harness exists, and with one executor there is nothing to compare.

The decision recorded here is to **keep it that way**: an asynchronous path
must not be a second state machine. The intended shape is one `_execute`
owning the whole state machine, with node invocation inverted so that a
synchronous driver calls the node and an asynchronous driver awaits it. If
that shape proves unworkable in prototype, the fallback is a second executor
**plus** the differential harness invariant I demands — and that cost is the
reason the inversion is tried first.

This clause binds the 0.7 design discussion. It does not authorise
implementation, which is a separate scope decision.

## Consequences

**Measured, not projected.** The suite goes from 560 passed / 14 skipped to
**439 passed / 0 skipped**, and from 33.9 s to 19.6 s. Every one of the 14
skips was Axis-era and two of the three skip gates required a live server. The
Motus-native share of the suite rises from 51 % to 66 %. Both frozen corpora,
all 125 contract-fixture tests and all 92 adversarial tests are unaffected.

**Repository surface** drops by 19,152 lines across 83 files. `docs/` keeps
exactly the two files that describe the shipped product; the eleven removed
included `api.md`, which documented a `GraphRunner` class that exists nowhere
in the repository, and `docs/README.md`, titled "Vitruvyan Axis".

**What this does not do.** It changes no contract surface, no schema, no
fixture, no frozen corpus, and no public API symbol. `vitruvyan_motus.__all__`
is unchanged. The wheel is unchanged — `where = ["src"]` already excluded
everything removed here, so no consumer sees any difference.

**Outstanding, deliberately not decided here.** The legacy `SynapticBus`
interoperates with `vitruvyan_motus.compat` only by coincidence — it compares
an `axis.state.EventType` against a `compat._EventType` and passes because
both are `(str, Enum)` with equal values, and `_derive_events` has no `else`
branch, so unmatched events vanish silently. No test covers this. It is a
**migration-time** question for the first consumer that moves off the pinned
Axis wheel, not a removal-time one, and it needs its own decision before that
migration begins.
