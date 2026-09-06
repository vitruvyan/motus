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