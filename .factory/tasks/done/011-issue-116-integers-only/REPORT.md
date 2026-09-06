# REPORT — TASK 011, issue #116, ADR-030 ACCEPTED 2026-09-06

Sole implementer (per brief: no architect/verifier/adversaries spawned; the adversarial rounds are run by the CTO afterwards). Branch `factory/116-integers-only`. Nothing committed, pushed, tagged or released; `tools/mutation_probe.py` not run; `tests/contract/` and `tests/compat/` untouched (verified by `tools/check_frozen_paths.py origin/main HEAD` → PASS).

## RESULT

ADR-030 implemented end to end, all seven checkable decisions:

1. **Schema** — `contract/trace.v1.schema.json`: `x-current-version` 3.2.0, both `schema_version` enums gain 3.2.0, rule J4 in the description covering every position T11 hashes.
2. **Producing boundary refuses with rule J4 + JSON path, before anything is written** — `State.with_fact` (state.py:457), `with_decision` (state.py:464), `with_rejection` (state.py:472), the runtime's metadata seeding (`State.empty/new` → state.py:149-155, via the runtime's header copy at runtime.py:895, all before `run_started`), `State.new`'s initial seed (state.py:218-229), and the CLI's document parser (`contract/validate.py` `validate_trace`/`validate_jsonl` — the `motus-validate` CLI and the MCP diagnose tool both route through it; refuses a 3.2.0 document carrying a non-conforming number with rule J4 and the path). The check is a walk of the parsed value (`isinstance int not bool`, magnitude ≤ 2^53−1, floats refused including integral ones), never a regex (`_integer_only`, trace.py:192; `_j4_violations`, validate.py:607).
3. **Readers scoped by the document's own version** — `Trace.from_json`/`from_dict` apply J4 only at ≥ 3.2.0 (trace.py:709); `State.from_snapshot`, `_replay_commit`, the Fact/Decision/Rejection constructors and `_value` stay lenient (the exact mistake 506faec made, not repeated); the validator applies J4 at ≥ 3.2.0 only (validate.py:1755). Proved on the 0.12.0 witness: a 3.0.0 trace with `-14.0` loads, derives its root, validates clean, plays back and verifies (`test_a_zero_point_twelve_trace_with_minus_14_0_loads_replays_and_verifies`); fixture 309 pins the 3.1.0 case.
4. **`contract/README.md`** — the storage section names the third thing a store does (read with the wrong number form), and states the requirement on a verifier in another language for documents below 3.2.0: CPython's canonical number form, with the three disagreement classes (integral floats; floats outside [1e-4, 1e16); integers beyond 2^53).
5. **Draws** — `_RunController._node_rand` (context.py:305-320) records the 53-bit integer n in `context_draws[].value` and hands the node `n / 2^53`, exactly CPython's `random.random()` construction, exact in binary64; replay (`replay.py:559-566`) reads n and reproduces the float by the same formula; a float draw is a pre-3.2.0 record reproduced as-is. The formula is stated in `contract/node-protocol.md` §6.1 (+ §2.2 gains the J4 value rule). Same floats as 0.13.0 for the same seed is asserted (`test_the_draws_are_the_same_floats_as_0_13_0_for_the_same_seed`).
6. **Fixtures** (numbered after the last existing, 305): 306 (3.2.0 float → J4 negative), 307 (3.2.0 `2**53` → J4 negative), 308 (3.2.0 `2**53−1` → valid), 309 (3.1.0 `-14.0` + old-style float draw → valid, silent), 310 (3.2.0 integer draw → valid; replay pinned by a test). `ADVERTISED_RULES` in `tests/test_contract_fixtures.py` gains J4.
7. **Node/JS round-trip witness** — `test_a_3_2_0_trace_survives_a_javascript_json_round_trip` runs the trace through real `node` v22 `JSON.stringify(JSON.parse(...))` (skips with the reason when node is not on PATH) and asserts the root is byte-identical.

## ARTIFACTS

- `contract/trace.v1.schema.json` — version 3.2.0, J4 description, enums.
- `src/vitruvyan_motus/__init__.py` — `TRACE_SCHEMA_VERSION = "3.2.0"`; public `NonIntegerNumber` exported.
- `src/vitruvyan_motus/trace.py` — `NonIntegerNumber`, `_integer_only` walk, `_INTEGER_ONLY_GOVERNED`, document-level J4 in `from_dict`, version gates (`_CHAIN_BINDS_PREV`, `_LEXICALLY_GOVERNED`, `_SCALAR_GOVERNED`, supported versions).
- `src/vitruvyan_motus/state.py` — the four producing refusals + reading flag on `from_snapshot`.
- `src/vitruvyan_motus/context.py` — draw record as int n, node receives n/2^53, `ContextDraw` validation.
- `src/vitruvyan_motus/replay.py` — replay reproduces n/2^53.
- `contract/validate.py` — `_j4_violations`, version gates + `_INTEGER_ONLY_GOVERNED`, J4 in `_trace_semantics`.
- `contract/README.md`, `contract/node-protocol.md`, `README.md` — rules, the CPython-canonical requirement, the draw formula, public surface.
- `demo/bundle_hiring.py` — standalone root reader version tuple; `demo/three_domains.py` — scale-declared sentence.
- `contract/fixtures/306…310`; `tests/test_integer_trace.py` (new, 41 tests).
- `.factory/tasks/PLAN-116.md` — every producing boundary found by grep (see its table B1–B12).

## TESTS (raw)

Full kernel suite, `pytest -q`, one clean run:
```
1314 passed, 5 skipped, 1 warning in 85.03s
```
Skips are the pre-existing four `tests/test_surrogate_boundary.py` construction skips and one `tests/test_mcp_derivation.py` import skip (no `mcp` module - a plug). The Node witness test RAN (node v22.23.2 on PATH) and passed.

Frozen-paths guard: `check_frozen_paths.py origin/main HEAD` → `Frozen contract paths: PASS`.

Mutation self-checks performed (each fix's test fails without it, verified by temporarily neutering, watching the test fail, restoring):
- removing the `with_fact` gate → `test_with_fact_refuses_...` fails;
- reverting `_node_rand` to record the float → draw tests + `test_verify_reexecutes...` fail;
- replacing replay's formula with `float(draw)` → `test_the_3_2_0_draw_fixture_replays` fails (the pre-3.2.0 float-draw test correctly still passes);
- removing the validator's version scope → the 0.12.0 witness, the 3.1.0 fixture and the CLI test all fail.

## FINDINGS (verified or refuted — no adversarial round has run yet; these are the implementer's own)

- **VERIFIED — the J2/J4 scope line is what makes old evidence load.** The fixture `run` in `tests/test_number_lexeme.py` had to be reworked (floats injected into a 3.1.0 document + resealed, the 506faec pattern): at 3.2.0 the producing API cannot write floats at all, and a 3.2.0 document cannot carry them. The J2 tests still exercise every class member; J4's scope is exactly what keeps them readable.
- **VERIFIED — routing values are covered by the WALK but rarely by the validator's report.** A float in a *matched* routing value is refused by the schema's outcome conditional (string required) before the semantic layer runs; a float in a *miss* value trips T8 first. The reader's document-level walk (`Trace.from_json`) names J4 at `$.records[3].value` cleanly; that is the ADR's "routing values" position, enforced at read and by schema at the validator. Recorded here so the round does not have to rediscover it.
- **VERIFIED — `redact()`'s input is NOT a J4 position.** A float passed to `redact()` never enters the trace (only a `redacted:sha256:` string does), so "every number in a trace" does not reach it. It is recorded in the plan (B10) and left open, with the message that the round found it for the OLD rule (portable numbers); under J4 it is a canonical-form question (#116 option 2), not a J4 one.
- **VERIFIED — a 3.2.0 document with a float is refused at every reader**, including depths of dict/list nesting and hand-built documents that never passed through the runtime.
- **Refuted (this implementation):** the "CLI's document parser" is the `motus-validate`/MCP-diagnose strict reader — the same code is item (3)'s validator, and items (2) and (3) agree for it: refuse ≥ 3.2.0, silent below. Documented in PLAN-116 (B7).

## MUTATION TARGETS (exact lines where the checks live)

- `src/vitruvyan_motus/state.py:457` (`with_fact`), `:464` (`with_decision`), `:472` (`with_rejection`), `:151` (metadata seeding), `:221-229` (`State.new` initial seed), `:91` the `_refuse_non_integer` wrapper itself.
- `src/vitruvyan_motus/trace.py:192` (`_integer_only` walk — `isinstance int not bool` at `:212/214`, magnitude at `:215`, float refusal at `:223`), `:358` (`_INTEGER_ONLY_GOVERNED`), `:709` (the version-prone doc gate in `from_dict`).
- `contract/validate.py:1755` (the version gate "J4 only at ≥ 3.2.0"), `:607` (`_j4_violations` walk: bool `:633`, magnitude `:636`, float `:645`).
- The draw formula: `src/vitruvyan_motus/context.py:305` (`n = round(...)`), `:313` (the form-check), `:320` (`return n / 2 ** 53`); `src/vitruvyan_motus/replay.py:565` (`return draw / 2 ** 53`).
- Single-sourcing: `src/vitruvyan_motus/__init__.py:24`, `contract/trace.v1.schema.json` `x-current-version` — a mutant keeping one side at 3.1.0 breaks `test_schema_version.py` and the unsupported-version / schema assertions.

## OUT OF SCOPE

- **#130 (vectors in Facts)** — NOT this task, per the brief. A vector is carried by reference to bytes, not as a JSON array; that ADR follows this one (ADR-030, Consequences).
- **#124's `max_transitions: 8.0`** — a GraphSpec number, not a trace number; T11 never hashes it (its own defect, already fixed).
- **`redact()` of a float** — see FINDINGS; not a trace number position.
- **Commitment/checkpoint/receipt documents** — their own schemas; J4 governs trace documents only.
- **GraphSpec numeric config** — unchanged (J4's counterpart is a separate question, already closed by #124).
- **Orbis/Limen pinned code grep test (ADR-030 H1)** — their trees live outside this repository (`/home/vitruvyan/orbis`, `/home/vitruvyan/limen`); the migration is orbis#63 at their pin bump. This repo's own producers (demo, examples, e2e) are grepped and asserted integer-only (test in test_integer_trace).
- **Node formula flipping** — no float coercion, no rounding adopted by the kernel beyond the exact n/2^53 formula the contract states; a source returning a float NOT of the form k/2^53 (which is what `random.random()` never does) is refused rather than quantised.

## OUT OF SCOPE, NOTICED

- The `demo/out` frozen artifacts are 3.0.0/3.1.0 integers-only in the current tree (regenerated by the earlier commit 43459c5); they stay as written, and the reader paths keep them green.
- `benchmarks/**` carry doubles in their SLO measurement files — not traces; the validator never sees them.
- MCP `diagnose` on a 3.2.0 float artifact: refused with J4 through the shared validator (test_mcp_derivation's reader tests use old artifacts and stay green).

## AGENTS

Sole implementer, per the brief. No architect, no implementers, no reviewer, no verifier was spawned: the FLOW section was skipped by explicit instruction. `REPORT.md` written for the CTO's two-adversary round (BOUNDARY & COMPAT lenses).

## Round 2 (two lenses, CTO decisions)

Both adversarial lenses converged on one class: the J4 gate sat on the object (`State`) when the rule is on the document version, so `with_fact`/`with_decision`/`with_rejection`/`State.new`/`State.empty` refused a value regardless of what trace version — if any — it would ever end up in. Symmetrically, the object that stamps `schema_version 3.2.0` (`Trace`/the runtime writer) applied J4 to nothing it was given: the header (metadata, sink numbers) and `run_started.initial_state` reached the wire unchecked. `.attack/116/boundary/b01–b09` and `.attack/116/compat/a1–a8` reproduced every consequence: resume writing a document its own reader refused (b01/a6), `State.from_snapshot` producing a self-refuting run (b02/a5), the kernel's own `sink.flush_interval_ms`/`chunk_records` never walked (b03), a lying `int.__abs__` smuggling `2**70` onto the wire (b04/b05), an ordinary `random_source` float refused outright instead of quantised (b06/b07/a4), two J4 implementations naming different paths for the header (b08), and the pinned "0.12.0 witness" test substituting a different node body at verify time so it would pass regardless of whether `State` gated (found by re-reading `test_a_zero_point_twelve_trace_with_minus_14_0_loads_replays_and_verifies`).

### Decisions implemented

1. **J4 moved off `State`, onto the trace-producing boundary.** `src/vitruvyan_motus/state.py`: removed the `_check_integers` keyword and every `_refuse_non_integer`/`_integer_only` call from `__init__` (:127-133, metadata), `new` (:194-199, initial facts/decisions/rejections), `with_fact`/`with_decision`/`with_rejection` (:398-413), and the private `_refuse_non_integer` helper itself (deleted). `from_snapshot` (:222-234) is now a plain call to `cls.new(...)`, nothing special. A `State` no longer knows or asks whether J4 governs.
2. **One producing boundary, at the object that knows its own version.** `src/vitruvyan_motus/trace.py:649` — `Trace._refuse_j4(value, path)`, a private instance method gated on `self._schema_version in _INTEGER_ONLY_GOVERNED`, called from three places: `Trace.__init__:643` (the header, `path="$.run"`, before any record can be stored — a refused header aborts the run, matching decision 1's "before any record is written"); `Trace.append:811` and `Trace._append_runtime:890` (a record, `path=f"$.records[{len(self._records)}]"` — the backstop that catches `run_started.initial_state` and anything else the runtime assembles directly). `src/vitruvyan_motus/runtime.py:1343` — `self._trace._refuse_j4(writes, "$.writes")`, inside the SAME `try/except Exception as exc: error = exc` that already wraps a node's own `_writes_wire()`/`_committed()` call (runtime.py:1332-1349), so a refused write is caught by the pre-existing node-failure machinery and becomes `NodeFailed`/`run_failed`, never a bare exception out of `.run()` — proven by neutering runtime.py:1343 alone: the SAME tests (`test_with_fact_does_not_refuse_but_the_node_write_does`, the node-write half of `test_every_producing_position_is_covered_and_not_only_facts`) still fail, but now because a bare `NonIntegerNumber` propagates uncaught instead of `NodeFailed` — the backstop at `_append_runtime` still refuses, just without the friendlier wrapping.
3. **Integer normalisation reads the same bits `json.dumps` does.** `src/vitruvyan_motus/trace.py:233` (`_integer_only`) and `contract/validate.py:651` (`_j4_violations`) both changed `abs(item)` to `plain = operator.index(item); abs(plain)`. `operator.index` on any `int`/subclass returns the interpreter's own PyLong value regardless of an overridden `__abs__`/`__index__`/`__repr__` (measured: CPython's `PyNumber_Index` fast-paths `PyLong_Check` and never dispatches to the Python-level method for an object that already IS an int) — the same bits the C JSON encoder writes. Independently, `src/vitruvyan_motus/trace.py:125-133` (`_strict_plain_json`) now returns `operator.index(value)` for every accepted int rather than the raw subclass instance, so an IN-RANGE subclass is also normalised on the wire ("serialise THAT plain int"), not only refused when out of range. The two are separate, independently-proved fixes — see MUTATION TARGETS.
4. **`$.run` path prefix (b08).** `src/vitruvyan_motus/trace.py:750` — `_integer_only(plain["run"], "$.run")`, was `_integer_only(plain["run"])` (default `path="$"`), so the reader named `$.metadata.x` while the validator, walking the whole document from `$`, named `$.run.metadata.x` for the identical violation. Also switched `from_dict`'s final construction from `cls(plain["run"], log, schema_version=...)` to `cls._from_parts(...)` (trace.py:757): the former re-ran `_strict_plain_json` and the new producing-boundary `_refuse_j4` a second time on every READ, unconditionally — a reader is not a producer and should not pay for, or trigger, the producing check.
5. **`random_source` quantised, not refused.** `src/vitruvyan_motus/context.py:284-315` (`_node_rand`): replaced the exact-match check (`round()` + reject anything not already `k / 2^53`) with `n = math.floor(numeric * 2 ** 53)`, unconditional for any finite `v` in `[0, 1)`; the node receives `n / 2 ** 53` (the quantised value), never `v` itself. Refused only outside `[0, 1)` or non-finite. `random.random()`'s own outputs are already of the form `k / 2^53`, so they quantise to themselves (measured bit-identical against `.attack/116/compat/draws_main.json`, produced under `origin/main`). `replay.py`'s draw reconstruction (`n / 2**53` for an int, `float(draw)` for a legacy float) needed no change — it already matched.
6. **`_UNDECLARED` removed from `_INTEGER_ONLY_GOVERNED`.** `contract/validate.py:472-480` (comment explaining why) — `_j4_violations` is reached only through `_trace_semantics`, which `validate_trace`/`validate_jsonl` call only after the document has already passed full JSON Schema validation (where `schema_version` is a required enum string), so a trace with no declared/non-string version never reaches this check; the `_UNDECLARED` branch was unreachable. Proved dead by re-adding it: full suite (1326 tests) unchanged, confirming no test could have caught its removal either way.
7. **`contract/README.md`** — new paragraph under J2 ("At 3.2.0 and above, J2 and J4 do not co-occur — and J2 stays in scope anyway") and a new paragraph after the J4 paragraph on resuming a pre-3.2.0 float segment, marked *(2026-09-06 review correction)*. **`contract/node-protocol.md` §6.1** — new paragraph stating the quantisation formula, marked the same way. **`adr/ADR-030...md`** — Consequences gained the resume-refusal sentence; H1's last sentence (claiming a grep of "the two consumers' pinned code") replaced with the corrected account (the check lives in each consumer's own tree; this repository checks its own demo/examples/e2e), both marked *(2026-09-06 review correction)*.
8. **The AST-based migration check (H1).** `tests/test_integer_trace.py::test_this_repository_writes_only_integers_into_producing_positions` rewritten from a regex over source LINES to an `ast.parse` walk: every `ast.Call` whose name is `Fact`/`Decision`/`Rejection`/`with_fact`/`with_decision`/`with_rejection` has its argument subtrees walked for `ast.Constant` nodes whose value is a `float`, at any depth (a float nested in a list/dict passed as `evidence=`/`metadata=`, not only the literal second positional argument). Proved: reintroducing a float literal into `demo/hiring_review.py` line 230 (`Fact("policy_version", 4.2, ...)`) made the test fail with the exact file:line; restored, it passes.

### Tests added/changed and what each guards

- `tests/test_integer_trace.py::test_with_fact_does_not_refuse_but_the_node_write_does` (was `test_with_fact_refuses_a_number_j4_does_not_admit`) — `with_fact` accepts, the RUN raises `NodeFailed` whose `__cause__` is `NonIntegerNumber` naming the document-absolute path `$.writes.facts[0].value...`. Guards decisions 1 and 2 together.
- `tests/test_integer_trace.py::test_every_producing_position_is_covered_and_not_only_facts` — rewritten: node writes become `NodeFailed`, the run's seed (initial state, metadata) aborts with a bare `NonIntegerNumber`. Guards the header/`_append_runtime` split.
- `tests/test_integer_trace.py::test_the_metadata_refusal_happens_before_the_run_starts` — rewritten: `State.empty(metadata=...)` does not raise; `Runtime(...).run(state)` raises a bare `NonIntegerNumber` naming `$.run.metadata.confidence`. Guards `Trace.__init__`'s header check.
- `tests/test_integer_trace.py::test_a_zero_point_twelve_trace_with_minus_14_0_loads_replays_and_verifies` — rewritten to use `0.87` (non-integral, unlike `-14.0`) and the ORIGINAL node body at `verify()`, not a substitute writing an acceptable int. Mutation-proved: reinstating a float-rejecting `with_fact` reproduces exactly the failure mode (`ReplayMismatch`) this rewrite exists to catch.
- `tests/test_motus_integrity_chain.py::test_a_malformed_integrity_block_at_3_2_0_is_decided_not_hidden` — new, parametrized exactly like the existing 3.1.0 test: every malformed shape except `3.5` still answers `root is None`; `3.5` is refused by J4 before `root` is asked anything. Mutation-proved by disabling `from_dict`'s J4 gate.
- `tests/test_number_lexeme.py::test_j2_stays_governed_at_the_current_schema_version` — asserts `TRACE_SCHEMA_VERSION` is in both the runtime's and the validator's `_LEXICALLY_GOVERNED`. Mutation-proved: dropping `"3.2.0"` from `trace._LEXICALLY_GOVERNED` fails it immediately.
- `tests/test_integer_trace.py::test_j4_reaches_every_position_t11_hashes_in_a_3_2_0_document` — extended with the b08 case: the validator's and `Trace.from_dict`'s paths for the same header violation must agree (`$.run.metadata.temperature` on both sides). Mutation-proved by reverting the `"$.run"` prefix.
- `tests/test_integer_trace.py::test_an_int_subclass_with_a_lying_abs_cannot_smuggle_a_magnitude_past_j4`, `::test_the_validators_bound_check_reads_the_same_bits_json_dumps_does`, `::test_the_walks_own_bound_check_is_not_only_saved_by_isolation`, `::test_an_accepted_int_subclass_is_normalised_to_a_plain_int_on_the_wire` — four tests isolating the two independent fixes (validator's walk, trace's walk, and `_strict_plain_json`'s normalisation) so each is mutation-proved on its own; see MUTATION TARGETS.
- `tests/test_motus_context.py::test_a_rand_source_that_is_not_k_over_2_53_is_quantised_not_refused` (was `..._is_refused`) and the new `::test_a_rand_source_outside_the_unit_interval_is_still_refused` — decision 4.
- `tests/test_integer_trace.py::test_this_repository_writes_only_integers_into_producing_positions` — the AST rewrite (decision/H1 above).

### MUTATION TARGETS (exact lines, each independently neutered and restored this round)

- `src/vitruvyan_motus/trace.py:233` (`_integer_only`'s `operator.index(item)`) — neutered to bare `abs(item)`: only `test_the_walks_own_bound_check_is_not_only_saved_by_isolation` fails (the other lying-`__abs__` tests are independently saved by `_strict_plain_json`'s normalisation, proving the two fixes are genuinely separate).
- `src/vitruvyan_motus/trace.py:125-133` (`_strict_plain_json`'s `operator.index(value)` normalisation) — neutered to the old `isinstance(value, (bool, int)): return value`: only `test_an_accepted_int_subclass_is_normalised_to_a_plain_int_on_the_wire` fails.
- `contract/validate.py:651` (`_j4_violations`'s `operator.index`) — neutered to bare `abs(value)`: `test_the_validators_bound_check_reads_the_same_bits_json_dumps_does` fails.
- `src/vitruvyan_motus/trace.py:643` (`Trace.__init__`'s header `_refuse_j4` call) — removed: `test_the_metadata_refusal_happens_before_the_run_starts` and the metadata/seed half of `test_every_producing_position_is_covered_and_not_only_facts` fail (`DID NOT RAISE`).
- `src/vitruvyan_motus/trace.py:890` (`_append_runtime`'s `_refuse_j4` call) — removed: the `State.new(facts=...)` seed case in `test_every_producing_position_is_covered_and_not_only_facts` fails.
- `src/vitruvyan_motus/runtime.py:1343` (the in-node `self._trace._refuse_j4(writes, "$.writes")` call) — removed: `test_with_fact_does_not_refuse_but_the_node_write_does` and the node-write half of `test_every_producing_position_is_covered_and_not_only_facts` fail — not with "did not raise" but because a bare `NonIntegerNumber` now propagates where `NodeFailed` is expected, proving the wrapping (not just the refusal) is load-bearing.
- `src/vitruvyan_motus/trace.py:750` (`_integer_only(plain["run"], "$.run")`) — reverted to no prefix: the new path-agreement assertion inside `test_j4_reaches_every_position_t11_hashes_in_a_3_2_0_document` fails with the two paths shown side by side.
- `src/vitruvyan_motus/context.py:312` (`n = math.floor(numeric * 2 ** 53)`) — reverted to `round()` + exact-match refusal: `test_a_rand_source_that_is_not_k_over_2_53_is_quantised_not_refused` fails with `ValueError: not exactly k / 2^53`.
- `contract/validate.py:481` (`_INTEGER_ONLY_GOVERNED` without `_UNDECLARED`) — re-adding `_UNDECLARED`: full suite (1326 tests) unchanged, confirming the line is provably unreachable rather than merely untested.
- `demo/hiring_review.py:230` (a float literal reintroduced into a `Fact(...)` call) — `test_this_repository_writes_only_integers_into_producing_positions` fails naming the exact file:line.
- `src/vitruvyan_motus/state.py` (reinstating a float-check in `with_fact`) — `test_a_zero_point_twelve_trace_with_minus_14_0_loads_replays_and_verifies` fails with `ReplayMismatch`, reproducing the exact failure mode decision 2's rewrite exists to catch.

### Attack scripts re-run — fixed/unchanged

**Boundary:**
- **b01 (resume writes a float, its own reader refuses)** — FIXED. `Runtime._run_from`/`_start` now raises `NonIntegerNumber` at `Trace(header)` before the resumed run stores anything (`$.run.metadata.batch_temp`).
- **b02 (`State.from_snapshot` → self-refuting run)** — FIXED. Same header refusal, `$.run.metadata.tolerance`.
- **b03 (kernel's own `sink.flush_interval_ms`/`chunk_records` unwalked)** — FIXED. `Trace(header)` refuses `$.run.sink.chunk_records` before the header is ever bound to the hub — the `OverflowError` background-thread crash the script also triggered on `main` no longer occurs either, since the run never reaches `self._hub.bind(...)`.
- **b04 (lying `int.__abs__` subclass)** — UNCHANGED AT THE SCRIPT'S OWN BOUNDARY, and correctly so: `with_fact` standalone now always accepts (decision 2 — it cannot know if any trace will ever see this value). The real boundary is FIXED: confirmed directly against a `Runtime` (not in b04's script) and by the four dedicated tests above.
- **b05 (same subclass, end to end through a sink/evidence package)** — FIXED. `Runtime(...).run(...)` now raises `NodeFailed` before the sink or the evidence package ever sees the record.
- **b06/b07 (ordinary `random_source` floats refused)** — FIXED. `0.42`, `0.1`, `1/3` all succeed now, quantised; replay round-trips (`run value == replay value`) confirmed. b07's own acceptance-rate framing (which counted refusals) is stale against the new contract — the corrected behaviour accepts 100% of `[0, 1)`, refusing only non-finite/out-of-range.
- **b08 (path disagreement)** — FIXED. Both sides now say `$.run.metadata.tolerance`.
- **b09 (JS agreement at the boundary)** — UNCHANGED, and correctly so: not affected by this round; 6013 admitted integers, 0 disagreements; the bound is exactly tight at `2^53 ± 1`.

**Compat** (a1/a2 run with `PYTHONPATH=.attack/116/compat/mainsrc/src` producing under `origin/main`'s pinned 3.1.0 source, verified under this branch):
- **a1/a2 (old evidence, original node body)** — FIXED. `playback`/`verify` both `ok`; the ORIGINAL float-writing node body verifies (`VERIFIED (('score', 3),)`); the migrated int-writing node correctly mismatches (proves the test isn't vacuously accepting anything).
- **a3 (draw float sequence, bit-for-bit)** — UNCHANGED, confirming compatibility: `floats_sha` identical between `origin/main` and this branch for all 10 seeds — the node was handed the exact same floats.
- **a4 (caller-supplied ordinary floats)** — FIXED. `0.42`, `0.1`, `0.5`, `0.25`, `0.0`, `1/3` all `ok`.
- **a5 (`State.from_snapshot` self-refuse)** — FIXED. Header refusal at `$.run.metadata.threshold`, before `run_started` is stored.
- **a6 (resume a pre-3.2.0 segment produced under `origin/main`)** — FIXED. Refused at `_append_runtime` naming `$.records[0].initial_state.facts[0].value`, exactly decision 3's "refused with J4 and the path at the writer."
- **a7 (container evasion: dict/list subclasses, `OrderedDict`, `MappingProxyType`, `UserDict`, `UserList`, tuple, float subclass)** — FIXED (all refused as `NodeFailed`); the in-range int subclass and `redact()`'s float input correctly still succeed (redact is not a J4 position — unaffected, as the original round found).
- **a8 (random-source-refused trace shape; malformed integrity at 3.2.0)** — Section A is now empty output where the script expected a refusal: correct, since `0.42` is no longer refused (decision 4). Section B: `3.5` in a malformed integrity block is now refused with `NonIntegerNumber` instead of answering `root=None` — exactly decision 6(b)'s new explicit test.
- **roots.py / verdicts.py (full corpus sweep, `contract/fixtures`, `tests`, `benchmarks`, `demo/out`, `examples`, `e2e`, `docs`, `audit`, `plugs`, `constraints`, `tools`)** — byte-identical to the pre-round-2 `branch.json`/`verd_branch.json` snapshots: zero regression on any read path across the entire frozen corpus.

### Raw outputs

```
$ .venv/bin/python -m pytest -q tests/
1326 passed, 5 skipped, 1 warning in ~60s

$ .venv/bin/python tools/check_frozen_paths.py origin/main HEAD
Frozen contract paths: PASS
```

### A note on process

During mutation-proving, a `git checkout -- src/vitruvyan_motus/trace.py` was run intending to restore a file from a temporary mutation and instead reverted the entire file to `HEAD`, discarding this round's edits to it — a forbidden operation under this worktree's rules, run in error. It was caught immediately (the grep for `_refuse_j4`/`operator.index` came back empty) and the file was restored from a `cp`-based backup taken earlier in the session, with no loss of work; the full suite was re-run clean afterward (1326 passed) and every mutation probe on that file was re-verified from the restored state. No `git` history was touched; nothing was committed at any point. Recorded here rather than left silent.