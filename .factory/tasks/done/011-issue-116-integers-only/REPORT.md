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
## Round 4 (CTO decisions on round 3's report, all six implemented)

### 1. `Trace.__init__` walks supplied `records` too

`src/vitruvyan_motus/trace.py:670-699` (`__init__`): after `self._records` is
set, a new loop (`:695-696`) walks every supplied record with `_refuse_j4`
at `$.records[{index}]`, exactly the document-absolute path `append` and
`_append_runtime` use — gated by the same version check, so a 3.1.0
constructor call over the same records stays silent. Before this, a caller
handing `records` straight to the constructor (the reseal pattern
`Trace._seal`'s own docstring assumes exists) reached the wire completely
unchecked; `append`'s refusal only ever saw records added one at a time
through it.

Test: `tests/test_integer_trace.py:819`
(`test_the_constructor_is_a_producing_boundary_too_for_supplied_records`) —
the r01c shape: a genuinely produced 3.2.0 run, one fact value doctored back
to the float it started as, resealed and handed to `Trace(run, records,
schema_version=...)`. Refused with `J4` and
`$.records[{transition_index}].writes.facts[0].value`; the SAME records at
`schema_version="3.1.0"` are accepted and the float round-trips unchanged.

Mutation probe: removed the `for`/`_refuse_j4` loop at `:695-696` → the new
test fails `DID NOT RAISE`; restored, full suite green (raw output below).

Repro re-run:
```
$ .venv/bin/python .attack/116/round3/r01_ctor_records.py
=== A. records= as a _ChunkedLog (the declared type) ===
NonIntegerNumber: rule J4 (ADR-030): the number -14.0 at
  $.records[0].initial_state.facts[0].value is not a JSON integer ...

$ .venv/bin/python .attack/116/round3/r01c_ctor_real_run.py
NonIntegerNumber: rule J4 (ADR-030): the number -14.0 at
  $.records[2].writes.facts[0].value is not a JSON integer ...
```
Both refuse; before this round both constructed a sealed, root-deriving 3.2.0
artifact with a float inside it.

### 2. Cost: two fixes, measured, still over budget — STOPPING here per instruction

**Cause A — the double walk.** `Runtime._execute` pre-checks a node's
`writes` under J4 (`src/vitruvyan_motus/runtime.py:1363`,
`self._trace._refuse_j4(writes, "$.writes")`, unchanged) so a violation
becomes that node's own failure (`NodeFailed`) rather than a bare exception —
kept, because removing it is exactly the mutation
`test_the_runtime_threads_the_checked_writes_object_into_append_runtime`
already guarded and the report's own note says the wrapping is load-bearing.
`_append_runtime`'s walk of the finished transition record used to walk the
SAME `writes` subtree a second time. Fixed by **naming the object, not
re-checking it**: `checked_writes` (`runtime.py:1351`) is set to the exact
`writes` object right after its own pre-check passes (`:1364`); rebuilt as
`skip_writes = writes if writes is checked_writes else None` (`:1401`)
immediately before the transition dict is finalised, so a LATER violation
(pure-node effects, a declaration violation) that replaces `writes` with a
fresh empty dict is never mistaken for the checked one; threaded through
`_store(transition, force=force, skip_j4=skip_writes)` (`:1428`) →
`_store`'s new `skip_j4` parameter (`:1150`) → `Trace._append_runtime(record,
skip=skip_j4)` (`:1183`). `_integer_only` (`trace.py:230`) takes the new
`skip` and, at the top of its pop loop, does `if skip is not None and item is
skip: continue` — object identity, never equality, because the object is
held alive by the very record being walked so there is no window for a
different dict at a different address to be mistaken for it. Chosen over the
alternative (dropping the runtime's own pre-check and letting
`_append_runtime`'s walk report node failures) because the latter would make
a refused write a bare `NonIntegerNumber` out of `.run()` instead of
`NodeFailed`, which `test_with_fact_does_not_refuse_but_the_node_write_does`
and the node-write half of
`test_every_producing_position_is_covered_and_not_only_facts` already pin.

Measured (deterministic, not timing): `.attack/116/round3/r05_walk_cost.py`
patched to accept the new `skip` keyword — total values visited by a 20-node
realistic run, with the runtime's real `skip_j4` wiring forced off versus
left on:
```
skip disabled (pre-fix double walk): 1739 values
skip enabled  (this round's fix)   : 1339 values      (-23.0%)
```
matching the reported 22%.

**Cause B — eager path strings.** `_integer_only` built an f-string JSON path
for every child visited and discarded almost all of them. Fixed by
`_render_j4_path` (`trace.py:209-224`): the walk now pushes a breadcrumb
cons-cell, `(parent, is_list, key_or_index)` — one small tuple per level,
never a formatted string — and the path is rendered into text only for the
single node that is actually refused (`trace.py:274`, `:282`, the two
`NonIntegerNumber(...)` call sites). Measured against the exact pre-fix
implementation reconstructed inline (not the round-3 script's own zero-path
reference, which never tracks a path at all and so overstates the achievable
saving): **~12%** per record on a realistic transition record, well short of
the 38% figure, because SOME bookkeeping is unavoidable if a path is to be
named on refusal at all.

**Re-measured, `benchmarks/collect_relative_baseline.py --baseline-src
.attack/116/compat/mainsrc/src --baseline-ref v0.14.0 --candidate-src src
--candidate-ref factory/116 --pairs 5`, three separate runs, gated metric
`realistic_1000_us_per_node_min`:**
```
run 1: 1.3525372834611076   (+35.25%)
run 2: 1.2960995422903518   (+29.61%)
run 3: 1.3132076055401174   (+31.32%)
median (canonical): 1.3132076055401174  →  +31.3%
```
For comparison, the SAME benchmark against the unoptimised round-2 tree (no
skip, eager paths — reconstructed via `git archive HEAD src/vitruvyan_motus`
into a scratch tree, never touching this worktree) measured **+40.0%** in one
run. The two fixes bought a real but modest reduction (roughly 5-9 points);
**the median is still far above the ADR-012 +10% budget. Per instruction,
stopping here — the CTO decides between further optimisation (a third cause
would have to be found; the walk itself is no longer the dominant cost once
these two are fixed) and a declared exception.**

Tests: `tests/test_integer_trace.py:852`
(`test_the_runtime_threads_the_checked_writes_object_into_append_runtime`) —
white-box property test: `Trace._append_runtime` must be called for a
transition record with `skip is record["writes"]` (the live object, not a
copy or a lookalike). Mutation probe: reverted
`runtime.py:1428` to `self._store(transition, force=force)` (dropping
`skip_j4=skip_writes`) → test fails `assert None is not None`; restored,
green.

### 3. Two surviving mutants, both closed

**(a) M11 — header walk narrowed to `$.run.metadata`.**
`tests/test_integer_trace.py:221`
(`test_the_header_walk_covers_the_kernels_own_sink_numbers_not_only_metadata`):
a `Runtime(..., sink=InMemoryTraceSink(), durability_profile=BUFFERED,
flush_interval_ms=2**60, chunk_records=2**60)` run must raise `NonIntegerNumber`
naming `$.run.sink.` before `_hub.bind` opens a session — reproduces b03/M11.
Mutation probe: narrowed `Trace.__init__`'s header check
(`trace.py:684`, `self._refuse_j4(self._run, "$.run")`) to
`self._refuse_j4(self._run.get("metadata", {}), "$.run.metadata")` → test
fails `DID NOT RAISE` (and a background flush thread with a `2**60` ms
timeout actually spins up, itself evidence the refusal should have fired
first — visible in the failure output as an `OverflowError` from a stray
thread); restored, green.

**(b) M12 — the writer's version gate deleted.**
`tests/test_integer_trace.py:241`
(`test_the_writers_version_gate_still_governs_which_documents_j4_sees`): a
hand-built `Trace({"run_id": "r", "metadata": {"temperature": -14.0}},
schema_version="3.1.0")` is constructed and a float value is appended and
accepted; the SAME header at `schema_version="3.2.0"` is refused at
construction. Mutation probe: removed the `if self._schema_version in
_INTEGER_ONLY_GOVERNED:` guard in `_refuse_j4` (`trace.py:719-720`, always
calling `_integer_only`) → test fails (the 3.1.0 construction itself now
raises); restored, green.

### 4. Wording: §6.1's formula is `floor`, not "nearest"

`contract/node-protocol.md:282-289`: "accepted and recorded at the nearest
point on the grid" → "accepted and recorded **rounded down to the grid point
below (`n = floor(v * 2^53)`)**", with the r04 counterexample inline: `0.42`'s
exact product with `2^53` lands precisely on the halfway mark between two
grid points, so `floor` and "nearest" are not the same rule — `floor` always
takes the lower point (`n = 3783023686991216`, node receives
`0.41999999999999993`), never `0.42000000000000004`, which is the point
"nearest" would suggest.

Test: `tests/test_motus_context.py:103`
(`test_the_grid_point_recorded_is_the_one_below_the_value_not_the_nearest`) —
pins BOTH the r04 example (`0.42`, where `floor` and Python's own
round-half-even happen to coincide, so it alone would not catch a `floor`→
`round` regression) AND `0.123456789` (fractional part `0.875`, no tie, where
`floor` gives `n=1111999897873515` and `round` would give `...516`) so the
test is genuinely mutation-effective. Mutation probe: changed
`context.py:312` from `math.floor(numeric * 2 ** 53)` to `round(numeric * 2
** 53)` → test fails on the `0.123456789` assertion (`assert
1111999897873516 == 1111999897873515`); the `0.42` assertion alone would NOT
have caught this mutant. Restored, green.

### 5. Stale claims corrected, marked as review corrections

- `README.md:969-972` — "The refusal lives at the producing boundary
  (`with_fact`, `with_decision`, `with_rejection`, metadata seeding) *before
  anything is written*" → names the writer boundary instead:
  `Trace.__init__`/`append`/`_append_runtime`, the runtime's in-node writes
  check (kept per item 2), and the CLI's document parser; states plainly that
  `State` does not raise this any more.
- `src/vitruvyan_motus/trace.py:186-205` (`NonIntegerNumber.__doc__`) —
  same correction, listing all five current raise sites by name.
- `adr/ADR-030-a-number-in-the-trace-is-an-integer-a-browser-reads-back-unchanged.md:23`
  (decision 2) — same correction, marked "(2026-09-06 review correction)".
- `src/vitruvyan_motus/trace.py:701-718` (`Trace._refuse_j4`'s docstring) —
  added a "**'Once' means once**" paragraph naming the round-3 finding (the
  pre-check and the record walk used to visit the same values twice) and
  stating that `skip` is what makes the docstring's claim true now, so a
  later reader is told why the parameter exists rather than only that it
  does.

No dedicated test: these are prose corrections, not a checkable behaviour: the
existing J4 test suite (`tests/test_integer_trace.py`) already proves `State`
does not raise and the writer boundary does, which is what the corrected text
now says.

### 6. F6 — untouched

`replay.py` not modified, per instruction; filed by the CTO.

### Full verification, this round

```
$ .venv/bin/python -m pytest -q tests/
1332 passed, 5 skipped, 1 warning in ~58s

$ .venv/bin/python tools/check_frozen_paths.py origin/main HEAD
Frozen contract paths: PASS
```

Re-ran (all pass/refuse as expected, no regressions against round 1/2's own
recorded outputs): `.attack/116/round3/r01_ctor_records.py`,
`r01b_ctor_sealed.py`, `r01c_ctor_real_run.py`, `r02_resume_sink.py`,
`r03_verify_migration.py` (F6, unchanged, not this round's job),
`r04_rand_quantisation.py`, `r05_walk_cost.py` (patched to accept the new
`skip` keyword — a diagnostic script, not a frozen path), `r07_surviving_mutants.py`,
`r08_remaining.py`, `r09_doc_claims.py`; `.attack/116/boundary/b01-b09`;
`.attack/116/compat/a1-a8` (a1/a2/a6 need CLI args and error with
`IndexError` exactly as they did before this round, unrelated to it).

Files touched this round: `src/vitruvyan_motus/trace.py`,
`src/vitruvyan_motus/runtime.py`, `contract/node-protocol.md`, `README.md`,
`adr/ADR-030-a-number-in-the-trace-is-an-integer-a-browser-reads-back-unchanged.md`,
`tests/test_integer_trace.py`, `tests/test_motus_context.py`,
`.attack/116/round3/r05_walk_cost.py` (signature compat only, not a fix).

## Round 5 (CTO decision: fold J4 into `_strict_plain_json`; still over budget — STOPPING, reporting)

### What was built, exactly as decided

`src/vitruvyan_motus/trace.py:151` — `_strict_plain_json` gains `integers_only:
bool = False`, `path_root: str = "$"` and `skip: Any = None`. The int branch
(`:203-217`) reuses the SAME `operator.index(value)` call the function already
made to normalise every accepted int (round 2 decision 3) to also read J4's
magnitude bound — one dispatch, one bit-read, not two. The float branch
(`:217-229`) raises `NonIntegerNumber` when `integers_only`, after the
existing NaN/Infinity check. Path rendering is round 4's lazy cons-cell
technique (`_render_j4_path`, moved to `:129-148`, now built for
`_strict_plain_json`'s OWN recursion via a private `_crumb` parameter,
`:236`/`:259`, populated only when `integers_only` so the non-J4 callers —
`redact()`, node-config fingerprinting, `commitments.py` — pay nothing extra).
`skip` (`:189-190`, identity via `is`, checked before any dispatch) is round
4's identity-skip, ported onto this function so it still exists exactly once.

The standalone `_integer_only` function is deleted. Every call site that used
to walk twice now calls `_strict_plain_json` once:

- `Trace.__init__`'s header (`:665-667`) — was `_strict_plain_json(run,
  ...)` immediately followed by `self._refuse_j4(self._run, "$.run")`; now one
  call, `integers_only=schema_version in _INTEGER_ONLY_GOVERNED`. A genuine
  fold: these two calls were adjacent, over the identical value, with nothing
  between them, so merging changes no error precedence.
- `Trace.append` (`:877-880`) — same fold, the public producing boundary.
- `Trace.from_dict` (`:812-817`) — kept as a SEPARATE second pass over the
  already-isolated `plain`, deliberately: the existing comment there (and
  ADR-030 decision 3's test coverage) requires J4 to run AFTER every
  structural check, so a malformed document is told its structural defect
  first. Folding this into the FIRST `_strict_plain_json(document, ...)`
  call would move J4 ahead of those checks and change what a caller sees
  first for a document broken two ways at once — not measured on the write
  path this round targets, so not worth that behaviour change. The two
  `_integer_only(...)` calls here became `_strict_plain_json(...,
  integers_only=True, ...)` calls, mechanically, same order, same messages.
- `Trace._refuse_j4` (`:684-719`) — the entry point for callers NOT already
  isolating the same value at that call site (the ctor's `records` loop,
  `append`/`_append_runtime`, the runtime's `_execute` writes pre-check). Its
  body is now `_strict_plain_json(value, reserve_redacted=False,
  integers_only=True, path_root=path, skip=skip)` behind the unchanged
  version guard. `_append_runtime` (`:947-981`) is UNCHANGED beyond this:
  it still calls `self._refuse_j4(record, path, skip=skip)` and discards the
  return, storing the original `record` object — not the isolated copy
  `_strict_plain_json` builds — because `_seal`'s own invariant ("the SAME
  sealed dict goes to the sink and to the trace") would break if the trace's
  own log held a structurally-equal-but-distinct object.
- `runtime.py` — **zero changes.** `_refuse_j4`'s contract from the caller's
  side (raise-or-not, honour `skip`) is unchanged, so `_execute`'s
  `checked_writes`/`skip_writes` plumbing and `_store`'s `skip_j4` parameter
  needed no edits.

### `skip_j4` plumbing: kept, not removed

The brief's condition for removing it — "if the writes are isolated once" —
is not met by this design: `_execute`'s pre-check of a node's `writes` still
has to happen inside the node's own `try`/`except` so a refusal becomes
`NodeFailed` rather than a bare exception (round 4's finding, re-confirmed:
`_append_runtime`'s later, full-record check cannot retroactively provide
that wrapping). That constraint is architectural, not something folding the
WALK into `_strict_plain_json` changes, so `writes` is still checked once at
the pre-check and excluded by identity from the later record-wide check —
same shape as round 4, same reason. Nothing removed here; said so per
instruction.

### Mutation probes (each neutered, watched fail, restored; full suite green after each restore)

- `trace.py:208` (int magnitude check folded onto `plain`) → neutered to
  `if False and integers_only and ...` → `test_an_int_subclass_with_a_lying_abs_cannot_smuggle_a_magnitude_past_j4`
  and `test_the_walks_own_bound_check_reads_the_same_bits_as_normalisation` fail.
- `trace.py:220` (float refusal) → neutered → 15 tests fail across every
  writer AND reader site (header, append, ctor-records, `_execute`'s
  writes-precheck, `from_dict`), proving the fold did not silently narrow
  coverage at any of them.
- `trace.py:665-667` (header fold) → `integers_only=False` hard-coded →
  `test_every_producing_position_is_covered_and_not_only_facts`,
  `test_the_metadata_refusal_happens_before_the_run_starts`,
  `test_the_header_walk_covers_the_kernels_own_sink_numbers_not_only_metadata`,
  `test_the_writers_version_gate_still_governs_which_documents_j4_sees` fail.
- `trace.py:877-880` (`append` fold) → `integers_only=False` hard-coded →
  `test_the_public_append_is_a_producing_boundary_too` fails.
- `trace.py:812` (`from_dict`'s read-side gate) → `if False:` →  7 tests
  fail, every reader-side J4 fixture/case.
- `trace.py:716-718` (`_refuse_j4`'s body) → replaced with `pass` → 10 tests
  fail: node-write refusal (all parametrised cases),
  `test_every_producing_position_is_covered_and_not_only_facts`, the
  int-subclass test, and the ctor-records reseal test — confirming this ONE
  routing point is what the writes-precheck, `_append_runtime` and the ctor's
  records loop all still go through.
- `trace.py:189` (`skip`'s identity check) → `if False and skip is not None
  ...` → **no pytest test failed** (matches round 4's own finding: this is a
  performance property, invisible to ordinary assertions) but a direct
  functional probe shows the real defect it would otherwise reintroduce: a
  poisoned subtree named by `skip` gets walked and refused when it should be
  skipped entirely (`.venv/bin/python -c` repro in session, reproduced then
  restored). Added `tests/test_integer_trace.py::test_strict_plain_json_skip_is_identity_not_a_performance_footnote`
  to close this gap — a genuinely new test, since round 4 only ever tested
  that `_append_runtime` receives `skip is record["writes"]` (wiring), never
  that `_strict_plain_json`/`_integer_only` itself honours identity over
  equality. Neutered the same line → the new test fails with the exact
  `NonIntegerNumber` a lookalike-but-distinct `skip` object should have
  prevented; restored, passes, full suite green (1333 passed).

### Round 5 measurement — the fold made it WORSE, not better

`r05_walk_cost.py` (adapted: it now monkeypatches `_strict_plain_json`
instead of the deleted `_integer_only`, counting only calls where
`integers_only=True` so the count answers the same question as before and
does not also count every ordinary isolation call in the codebase; the
wrapper unpatches itself before delegating to the real function and
re-patches after, because `_strict_plain_json` recurses on its own bare
module-level name — unlike the iterative `_integer_only` it replaces — so
leaving the patch active during the real call would have every recursive
step re-enter the wrapper and inflate the count):

```
records: 62
J4 walks: 83
values visited in total: 1339
per record: 1.3387096774193548 walks; 21.596774193548388 values
```

Unchanged from round 4's post-fix number (1339) — the SET of values J4 looks
at is identical, as it should be: this round changed WHICH function does the
looking, not what it looks at.

`benchmarks/collect_relative_baseline.py --baseline-src
.attack/116/compat/mainsrc/src --baseline-ref v0.14.0 --candidate-src src
--candidate-ref factory/116-round5 --pairs 5`, three separate runs, one at a
time (a stray concurrent run from an earlier attempt was killed — see
process note below — before any of these three; running two benchmark
processes at once would invalidate the machine-exclusivity the harness
depends on), full JSON captured per run in `.attack/116/round5_run{1,2,3}.json`:

```
                                  run 1      run 2      run 3      median
realistic_1000_us_per_node_min   1.530382   1.542408   1.660127   1.542408   (+54.2%)
noop_100_overhead_ms             1.985905   1.785571   1.712265   1.785571   (+78.6%)
to_dict_min_ms_realistic         0.947370   0.936664   0.997068   0.947370   (−5.3%)
```

**Median per-node ratio: +54.2%.** Round 4 measured +31.3% for the same
metric on the same benchmark. This round's fold made the gated metric
WORSE, not better — by a wide margin — and `noop_100_overhead_ms` moved even
further (+78.6% median), while the one metric that improved
(`to_dict_min_ms_realistic`, ~−5%) is a read-path number this round never
touched for speed and its movement is noise, not a result of anything here.

**Why, in one sentence:** the fold's real saving (reusing one `operator.index`
call and one isinstance dispatch instead of two) is genuine but small, and it
only applies at the THREE sites where `_strict_plain_json` was ALREADY being
called on the value a moment before the J4 check (the header, `append`,
`from_dict`) — none of which are on the per-node hot path a runtime actually
exercises (the header is built once per `Trace`; `append` is the public API,
never called by `Runtime`, which uses `_append_runtime`; `from_dict` is a
read). The two sites that ARE on the hot path — `_execute`'s writes
pre-check and `_append_runtime`'s walk of the rest of the transition record —
had NO prior `_strict_plain_json` call to fold with: through round 4 they
called the lean, copy-free `_integer_only` (isinstance dispatch + `abs`,
nothing allocated). Routing them through `_strict_plain_json` instead means
every node now pays that function's FULL isolation contract at those two
sites — a fresh dict/list copy of `writes` and of the rest of the transition
record, plus a redundant `_encodable()` re-check of every string in them
(`key`, `source`, `ts`, dict keys) that was already checked once when each
`Fact`/`Decision`/`Rejection` was constructed. That is real, unavoidable extra
work with the signature as specified — not a bug in the fold, but the fold's
architecture costs more at exactly the two sites that dominate a node's own
J4 bill, and costs less only at three sites that barely register in it.

Per instruction: **STOPPING here.** The change is implemented precisely as
decided, is behaviourally correct (full suite green, every existing
exception/path/wrapping expectation preserved, frozen-paths guard PASS,
`r07`'s two mutants still closed), and is measurably worse for the ADR-012
budget than what it replaced. The CTO decides: revert round 5's fold (keeping
round 4's tree, +31.3%, still over budget) and accept a declared exception at
some ratio, or pursue a THIRD design (this report does not propose one — a
no-copy "check without isolating" mode at `_strict_plain_json`'s two hot call
sites would need a new keyword this task's signature does not have and is
itself an architecture decision, not an implementer's to make).

### Full verification, this round

```
$ .venv/bin/python -m pytest -q tests/
1333 passed, 5 skipped, 1 warning in 54.17s

$ .venv/bin/python tools/check_frozen_paths.py origin/main HEAD
Frozen contract paths: PASS
```

Re-ran: `.attack/116/round3/r05_walk_cost.py` (adapted, see above — deleted
`_integer_only` was the thing it instrumented), `r07_surviving_mutants.py`
(both M11 and M12 still closed, output unchanged from round 4).

Files touched this round: `src/vitruvyan_motus/trace.py` (the fold),
`tests/test_integer_trace.py` (two tests adapted off the deleted
`_integer_only`, one new test for `skip`'s identity property),
`.attack/116/round3/r05_walk_cost.py` (adapted to the new signature — a
diagnostic script, not a frozen path), `.attack/116/round5_run{1,2,3}.json`
(new — raw benchmark evidence for the numbers above).
`src/vitruvyan_motus/runtime.py`: **not touched this round.**

### A process note

An earlier attempt at the three-run measurement left a background `until`
polling loop alive that kept re-launching benchmark runs; a stray
`collect_relative_baseline.py` process ended up running concurrently with a
freshly started one for about a minute before it was noticed and killed (PID
recorded in the session, not in this repo). No file in this repository was
touched by that mistake and no measurement used in the numbers above overlaps
with it — the three runs reported were each run alone, verified by process
listing before starting the next — but recording it here rather than leaving
it silent, per this file's own convention (see the round 2 "note on
process").

## Round 6 (CTO decision: undo round 5's fold; remove the backstop walk instead of narrowing it — still over budget, STOPPING, reporting)

### 1. Round 5's fold undone by hand

`src/vitruvyan_motus/trace.py`:

- `_strict_plain_json` (`:106-172`) back to isolation only — `reserve_redacted`,
  `scalars_required`, `path`, nothing else. `integers_only`, `path_root`,
  `skip` and `_crumb` are gone from its signature and body.
- `_MAX_SAFE_INTEGER`, `NonIntegerNumber`, `_render_j4_path` and a restored
  standalone `_integer_only` (`:211-319`) sit back between `_canonical_number`
  and `_RepeatedMember`, where they lived through round 3. `_integer_only`
  keeps round 4's lazy-path technique (a breadcrumb cons cell per level,
  rendered into text only on refusal) but drops `skip`: round 2 below removes
  the only caller that ever passed one, so carrying unused identity-skip
  machinery through this walk would be exactly the kind of mechanism the
  suite could never exercise.
- `Trace.__init__` (`:675-689`), `Trace.append` (`:878-884`), `Trace.from_dict`
  (`:767-825`) and `Trace._refuse_j4` (`:706-732`) all call `_integer_only`
  again, in round 4's shape (isolate with `_strict_plain_json`, then check
  with `_integer_only`, two calls where they used to be one fold).

Kept: `test_strict_plain_json_skip_is_identity_not_a_performance_footnote`'s
subject does **not** survive — `_strict_plain_json` no longer takes `skip` or
`integers_only` at all — so it is **deleted**
(`tests/test_integer_trace.py`, was at `:777-799`), and
`test_the_walks_own_bound_check_reads_the_same_bits_as_normalisation` is
reverted to round 3's `test_the_walks_own_bound_check_is_not_only_saved_by_isolation`,
calling `_integer_only` directly again (`tests/test_integer_trace.py:763-772`).

### 2. `_append_runtime` no longer walks a kernel-built record — at all

**The instance the brief named:** `_append_runtime`'s per-record backstop
walk (round 4: a standalone `_integer_only` pass over the whole transition
record minus `writes`; round 5: the same pass folded into `_strict_plain_json`)
was the runtime's hot-path cost, because it ran once per record regardless of
whether anything in that record could possibly be a caller's number.

**The class:** every record `_append_runtime` ever receives is built by
`Runtime._base`/`_store` from primitives the runtime itself computed — `seq`,
`attempt`, timings, routing `candidates`, `error`/`cause` (`type(...).__name__`
and fixed strings), `context_draws[].value` (an integer `_node_rand` computes,
never a caller's) — with exactly two exceptions: a node's own `writes`, and
`run_started.initial_state`. Both are caller-controlled, and both were already
being walked somewhere else in the codebase (or, for `initial_state`, needed
to be, see below) — so re-walking the WHOLE record to find them again, on
every record, was checking an invariant instead of ever making the invariant
hold.

**The repair:** `Trace._append_runtime` (`src/vitruvyan_motus/trace.py:950-988`)
no longer calls `_refuse_j4`/`_integer_only` on anything. Its docstring states
the invariant by name — `writes` is checked once, in-node, in
`Runtime._execute`, before the record exists; `metadata`/the sink's numbers
are checked once, in `Trace.__init__`, because they live in the header, not
in a record this method ever sees; `initial_state` is checked once, also in
`_execute`, right before `run_started` is built (new this round, see below);
everything else is kernel-built. `Runtime._store`'s `skip_j4` parameter and
`Runtime._execute`'s `checked_writes`/`skip_writes` local variables
(`runtime.py`, round 4's cost repair) are removed entirely
(`runtime.py:1148-1150`, `1178`, `1337-1387`, `1417`) — they existed only to
avoid re-walking `writes` inside a walk that no longer happens.

**The hole this closes that decision 2's own wording almost left open:**
`initial_state` is the seed a caller hands `State.new` — facts, decisions,
rejections no node in THIS run ever touched, most visibly on a resumed
segment, where it is the ENTIRE state a prior segment committed
(`ReplayEngine.resume`'s `State.from_snapshot(playback.state.snapshot(), ...)`).
It is not part of the header (`self._run`), so `Trace.__init__`'s check does
NOT see it, and — after ADR-030's own review correction (`cf81175`) —
`State.new` itself does not raise J4 either (a `State` does not know what
trace version it will end up in). Through round 5, the ONLY thing that ever
caught a bad `initial_state` was the very backstop walk this round removes;
removing it with nothing in its place would have silently reopened the
producing-boundary hole ADR-030 exists to close, on exactly the position
decision 2's own test (`test_a_3_2_0_document_with_a_float_is_refused_at_every_reader`,
reading a document with a bad `initial_state`) and decision 1's own test
(`test_every_producing_position_is_covered_and_not_only_facts`, seeding
`State.new` with a float and running it) already probe on the READ and
WRITE sides respectively. Found by re-deriving decision 2's own invariant
from the code rather than taking "checked once, in `Trace.__init__`" at face
value — the header and `initial_state` are two different objects the
runtime builds at two different times, and only one of them goes through
`Trace.__init__`.

**Fix:** `Runtime._execute` (`runtime.py:1301-1311`) computes `initial_state`
and calls `self._trace._refuse_j4(initial_state, f"$.records[{len(self._trace._runtime_log)}].initial_state")`
BEFORE `run_started` is built — mirroring exactly how `writes` is checked
before its own record is built a few lines below, for the same reason: a
refusal here is a bare `NonIntegerNumber` out of `.run()` (no node has run
yet to blame it on), which is what `test_every_producing_position_is_covered_and_not_only_facts`
already required and continues to pass unmodified.

Mutation probe: removed the `self._trace._refuse_j4(initial_state, ...)` call
in `_execute` (leaving `initial_state = self._state._initial_wire()` in
place) → `test_every_producing_position_is_covered_and_not_only_facts` fails
(`DID NOT RAISE`) on the three `State.new(...)` seeds; the new round-6 test
below (item 3) does NOT fail from this neutering alone, because none of its
own seeded values happen to violate J4 — it is a structural canary for a
NEW field, not a substitute for this regression test. Both are kept.
Restored, full suite green.

### 3. The backstop moves to a test

`tests/test_integer_trace.py::test_every_record_kind_the_runtime_can_emit_is_j4_clean`
runs five real runtimes covering every record kind the runtime can emit at
3.2.0 — `run_started`/`attempt_started`/`transition`/`routing`/`run_completed`
(a retry: one attempt raises, the second returns, under a `BUFFERED` sink so
the header carries the sink's own numbers), `run_failed` (STRICT abort),
`run_cancelled` (lodged before the run starts), and a resumed segment whose
`run_started.initial_state` carries the source segment's committed facts
(`ReplayEngine.resume`) — and for every resulting document asserts BOTH
`contract/validate.py`'s `validate_trace(...) == []` (no violations at all,
J4 included) AND a from-scratch structural walk
(`_numeric_offenders`, independent of anything `trace.py` exports) finds no
float and no integer with `|n| >= 2**31` anywhere in the document. The
second assertion is the one a re-check would have given for free and a mere
"no violations" report would not: a NEW kernel field that started carrying a
caller's number — in range, so J4 itself would stay silent about it, but
nowhere near the small counts and bounded configuration values every
genuine kernel field actually holds — would fail this test on its VALUE,
without needing anyone to have also remembered to widen a walk.

Mutation probe: temporarily reverted `_execute`'s `initial_state` check (item
2's own probe) — `test_every_record_kind_the_runtime_can_emit_is_j4_clean`
did NOT fail (recorded above, and repeated here because it is the fact that
motivates keeping BOTH tests: this one is a canary for shape, not a
regression test for enforcement). Full suite green after restoring.

### 4. Cost, measured exactly as rounds 4/5 did

`.venv/bin/python benchmarks/collect_relative_baseline.py --baseline-src
.attack/116/compat/mainsrc/src --baseline-ref v0.14.0 --candidate-src src
--candidate-ref factory/116-round6 --pairs 5`, three separate runs, one at a
time (no other `collect_relative_baseline.py` process running before each,
checked by `ps aux`), raw JSON in `.attack/116/round6_run{1,2,3}.json`:

```
                                  run 1      run 2      run 3      median
noop_100_overhead_ms             1.028533   1.019016   1.023980   1.023980   (+2.4%)
realistic_1000_us_per_node_min   1.090260   1.176875   1.139035   1.139035   (+13.9%)
to_dict_min_ms_realistic         0.938442   1.217653   1.078836   1.078836   (+7.9%)
```

**Median per-node ratio: +13.9%**, against round 4's +31.3% and round 5's
+54.2% for the identical metric — a large, genuine improvement (removing the
backstop walk entirely rather than narrowing what it re-checked), but still
above the ADR-012 +10% budget.

This host is not exclusive during this session — `ps aux` at measurement time
showed roughly two dozen unrelated long-running services (uvicorn workers,
stream listeners, an embedding process at 39% CPU) sharing the machine, and
the harness's own within-subject spread reflects it: 15-24% for the baseline,
19-37% for the candidate, well above what an idle host produces in rounds
4/5's own numbers. `run 1` alone (+9.0%) would have cleared the budget;
`run 2` (+17.7%) would not have by a wide margin. Recorded honestly because
the instruction is to report the median of three runs, not to pick the
favourable one or discard the noise — but the CTO should weigh this
budget-boundary result knowing the machine was shared, not idle, and that a
re-run on a quieter host is a cheap way to narrow the range before deciding
between further optimisation and a declared exception.

### 5. Full verification, this round

```
$ .venv/bin/python -m pytest -q tests/
1332 passed, 5 skipped, 1 warning in ~55s

$ .venv/bin/python tools/check_frozen_paths.py origin/main HEAD
Frozen contract paths: PASS
```

Re-ran: `.attack/116/round3/r01_ctor_records.py`, `r01b_ctor_sealed.py`,
`r01c_ctor_real_run.py`, `r02_resume_sink.py`, `r03_verify_migration.py` (F6,
unchanged, not this round's job), `r04_rand_quantisation.py`,
`r05_walk_cost.py` (adapted: instruments the restored `_integer_only` again,
not `_strict_plain_json` — shows 400 values visited per 62-record run this
round, down from round 4/5's 1339, because nothing walks a transition,
routing or terminal record any more), `r07_surviving_mutants.py` (M11 and M12
both still closed), `r08_remaining.py`, `r09_doc_claims.py` — all refuse/accept
exactly as round 4 recorded, with the two pre-existing script issues
unrelated to this round (`r08` items 6/7 hit `from_dict`'s
"records must begin with run_started" check, which predates ADR-030
entirely — confirmed against `632bfcd`).
`.attack/116/boundary/b01`-`b09` and `.attack/116/compat/a1`-`a8`: all
fixed/unchanged from round 4's own recorded outputs, `a1_verify_new`,
`a2_no_node_verifies` and `a6_resume_old` still erroring with `IndexError`
for the same missing-CLI-argument reason round 4 noted, unrelated to this
round.

### Files touched this round

`src/vitruvyan_motus/trace.py` (undo round 5's fold; remove
`_append_runtime`'s walk), `src/vitruvyan_motus/runtime.py` (the
`initial_state` check; remove `skip_j4`/`checked_writes`/`skip_writes`),
`tests/test_integer_trace.py` (restored `test_the_walks_own_bound_check_is_not_only_saved_by_isolation`;
deleted `test_strict_plain_json_skip_is_identity_not_a_performance_footnote`
and `test_the_runtime_threads_the_checked_writes_object_into_append_runtime`,
both subjects removed by this round; added
`test_every_record_kind_the_runtime_can_emit_is_j4_clean` and
`_numeric_offenders`), `.attack/116/round3/r05_walk_cost.py` (re-adapted to
instrument `_integer_only` again), `.attack/116/round6_run{1,2,3}.json` (new
— raw benchmark evidence).
