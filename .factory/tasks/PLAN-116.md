# PLAN-116 — ADR-030: every number in a 3.2.0+ trace is a JSON integer (|n| ≤ 2^53−1)

Written by the implementer (sole agent; the FLOW's architect step is folded in per the
brief). Grep of the producing/value surfaces: `with_fact|with_decision|with_rejection|metadata|initial_state|context_draws|parse_`.

## Every producing boundary found by grep, and where the check goes

| # | Boundary | File:line (current) | J4 gate |
|---|---|---|---|
| B1 | `State.with_fact` | `src/vitruvyan_motus/state.py:397` | walk `fact.value` (deep) |
| B2 | `State.with_decision` | `state.py:403` | walk `decision.value` (deep) |
| B3 | `State.with_rejection` | `state.py:409` | walk `rejection.evidence` when present (deep) |
| B4 | `State.new` initial seed (facts/decisions/rejections → `run_started.initial_state`) | `state.py:173-194` | walk each item — **but `from_snapshot` calls `new`**, so the walk is behind a private `_check_integers` flag that `from_snapshot` clears (BOUNDARY vs READ, exactly ADR-030 decision 2) |
| B5 | metadata seeding — `State.empty/new/__init__` `_value(metadata)` | `state.py:125`; the runtime copies it into the header at `runtime.py:895` | walk each metadata value; same `_check_integers` flag |
| B6 | `ctx.rand()` draw record → `context_draws[].value` | `context.py:278` (`_node_rand`) | ADR-030 decision 5: record the 53-bit integer **n**, hand the node **n / 2^53** |
| B7 | CLI's document parser — `motus-validate`'s `_loads_strict` / `validate_trace` / `validate_jsonl` (`contract/validate.py:335-367`, `main` at 3915; the MCP diagnose tool routes through the same strict reader) | `contract/validate.py` | J4 violations at ≥ 3.2.0, never below |
| B8 | `Trace.from_dict` / `Trace.from_json` (loader the runtime's replay/evidence/bundle paths all descend through) | `trace.py:577-622` | doc-level J4 walk at ≥ 3.2.0 (covers initial_state, metadata, fact values, routing values, context_draws) |
| B9 | replay draw reproduction | `replay.py:559` (`lambda: float(take("rand"))`) | read n (int) ⇒ n / 2^53; read float ⇒ as-is (old traces) |
| B10 | `redact()` fingerprint of a caller value | `trace.py` `redact` | **NOT gated** — the redacted value never enters the trace (only a `redacted:sha256:` string does), so J4 ("every number IN A TRACE") does not reach it. Recorded for the round; see REPORT OUT OF SCOPE. |
| B11 | `Runtime._start` header `resume` (`runtime.py:899`) | int/str fields only, already `_strict_plain_json` | nothing to gate; `source_seq` is an int |
| B12 | GraphSpec numbers (`max_transitions: 8.0`, #124) | graphspec schema | **NOT in scope** — the spec is not a trace and T11 never hashes it (#124 is its own defect, already fixed) |

## Reader gates (never refuse below 3.2.0)

- `State.from_snapshot` (used by `replay._initial_state` at `replay.py:267/688`, `resume`) — clears `_check_integers`; a 3.1.0/3.0.0 initial_state with `-14.0` keeps loading exactly as today. A 3.2.0 document can only reach it through a `Trace` already loaded by B8, which refused it.
- `State._replay_commit` (`state.py:354`) — constructs `Fact(**wire)` / `Decision(**wire)` / `Rejection(**wire)`; constructors stay lenient (they are shared with from_snapshot).
- Fact/Decision/Rejection `__post_init__` / `_value` — **stay lenient** (this is exactly what 506faec got wrong: `_value` is a read path too).
- Validator: J4 only ≥ 3.2.0 (below, "it says nothing" — 0.12.0/3.0.0 `-14.0` validates clean).

## Version plumbing (3.1.0 → 3.2.0)

- `src/vitruvyan_motus/__init__.py:24` `TRACE_SCHEMA_VERSION = "3.2.0"`
- `contract/trace.v1.schema.json`: `x-current-version`, the two `schema_version` enums, rule J4 sentence in the description
- `trace.py`: `_CHAIN_BINDS_PREV` + `_LEXICALLY_GOVERNED` + `_SCALAR_GOVERNED` + `from_dict` supported versions gain `"3.2.0"`; new `_INTEGER_ONLY_GOVERNED = {"3.2.0"}`
- `contract/validate.py`: `_SCALAR_GOVERNED`, `_LEXICALLY_GOVERNED`, `_VIOLATIONS_NULL_ADMITTED`, the T11 recipe tuple (1803), T12 tuple (1901), `derived_root` gate (3570) gain `"3.2.0"`
- `demo/bundle_hiring.py:86` `derived_root` version tuple gains `"3.2.0"` (test_standalone_hiring_reader pins "current trace")
- Tests pinning the old version: `test_motus_integrity_chain.py:97-98`, `test_motus_runtime.py:234` (→ 3.2.0), `test_motus_runtime.py:247` (3.2.0 no longer "unsupported" — relabel the assertion to a future version)

## The J4 walk (never a regex)

`_integer_only(value, path)` — iterative; `isinstance(v, int) and not isinstance(v, bool)` and `abs(v) <= 2**53 - 1` ⇒ ok; any `float` (integral ones included) ⇒ refuse; containers descend; bool/str/null pass. New public exception `NonIntegerNumber` (rule id J4 in the message, path included), exported and added to the README public-surface list. Two independent copies (trace.py raises; validate.py collects `Violation("J4", path, msg)`) — same pattern as the existing J1 walk duplication.

## Draws (decision 5)

`_node_rand`: `value = float(self._random())` (source unchanged → same floats as 0.13.0 for the same seed, since `random.random()` outputs are exactly k/2^53); `n = round(value * 2**53)`; record `ContextDraw("rand", n)`; return `n / 2**53` (== value exactly for k/2^53 forms). Refuse n outside [0, 2^53). `ContextDraw.__post_init__` accepts int n in [0, 2^53) or float in [0,1). Replay (replay.py:559) reads int ⇒ `n / 2**53`, float ⇒ as-is.

## Docs

- `contract/README.md` — storage section: the third thing a store does + CPython-canonical requirement on a verifier in another language below 3.2.0, with the three disagreement classes (integral floats; floats outside [1e-4, 1e16); integers beyond 2^53); validator fence list gains J4; the J2 "may not renumber" paragraph gains the 3.2.0 neighbor rule.
- `contract/node-protocol.md` — §2.2: values are integers from 3.2.0 (J4); §6.1: the draw formula (n recorded, node receives n/2^53, replay reproduces it).
- `README.md` — public-surface list + J4/NonIntegerNumber paragraph.

## Fixtures (after 305)

- `306-trace-3-2-j4-float` — 3.2.0, fact value `-14.0` → J4 negative
- `307-trace-3-2-j4-2-53` — 3.2.0, fact value `2**53` → J4 negative
- `308-trace-3-2-j4-2-53-minus-1` — 3.2.0, `2**53 - 1` → valid
- `309-trace-3-1-float-loads` — 3.1.0, `-14.0` → valid, silent (no J4)
- `310-trace-3-2-j4-draw-integer` — 3.2.0 with integer context draw → valid; replay pinned by python test
- `ADVERTISED_RULES` gains "J4"

## Tests (each fix carries one that fails without it)

New `tests/test_integer_trace.py`: producing refusals (all four positions + nested + bool/int boundaries + exact message path); readers (3.1.0 `-14.0` loads/validates/playbacks, 3.2.0 float refused at from_json/from_dict/validator); CLI exit codes for 3.2.0-float (J4) vs 3.1.0-float (0); JSONL==JSON verdict; draws recorded as int, node receives the float, replay reproduces same float, seeded-equality with the plain float source; the Node/JS round-trip witness (skip w/ reason when node absent from PATH). Updated: test_motus_context (draw int, ctx.rand() unchanged), test_motus_replay (float-draw facts scaled to ints; old float-draw trace still verifies), test_motus_runtime (draws fact ints; schema 3.2.0; unsupported=3.3.0), test_number_lexeme (floats injected + resealed, per 506faec's proven pattern), tests/adversarial/test_adv_resisted.py (2.5 → int), test_motus_integrity_chain (3.2.0), demo/bundle_hiring.

## Explicitly NOT gated (the implementer must not widen)

redact() input (B10), GraphSpec numbers (B12), commitment/receipt/checkpoint documents (their own schemas; J4 governs only trace documents), the compat `GraphState` legacy view (JSON-serializable in-memory only, never enters trace.v1).