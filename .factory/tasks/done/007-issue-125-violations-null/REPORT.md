# TASK 007 / #125 — violations null

## RESULT
Implemented ADR-029. Trace schema 3.1.0 now admits `Transition.violations: null` only for a fully undeclared node; `[]` remains the checked-and-clean result, and half-declared nodes retain lists for their declared side. T13 rejects null below 3.1.0, while SB4 enforces the 3.1.0 iff rule when a spec is supplied.

## ARTIFACTS
- Updated `contract/trace.v1.schema.json`, `contract/validate.py`, contract README, and node protocol.
- Updated runtime declaration capture, `TRACE_SCHEMA_VERSION`, trace reader/writer version scopes, MCP diagnosis, and benchmark nullable length guard.
- Added six fixture wrappers: `contract/fixtures/300-*` through `305-*` (existing fixtures untouched).
- Added direct runtime/null and current-version coverage in `tests/`; added `ADVERTISED_RULES` T13.
- Added root README migration note.
- Corrected `demo/bundle_hiring.py` standalone `derive_root` to accept 3.1.0 and added `tests/test_demo_bundle_hiring.py` proving a current runtime trace derives the same root without Motus imports.

Architect plan followed: version-site sweep included chain binding, lexical/scalar governance, T11/T12, derived-root, trace loading, and MCP diagnostics; SLO gate and frozen directories were untouched.

## TESTS
Focused:
```
.venv/bin/pytest -q tests/test_contract_fixtures.py tests/test_motus_runtime.py tests/test_motus_integrity_chain.py tests/test_schema_version.py tests/test_mcp_diagnose.py
266 passed in 10.29s
```

Standalone demo regression:
```
.venv/bin/pytest -q tests/test_demo_bundle_hiring.py tests/test_motus_integrity_chain.py tests/test_schema_version.py
30 passed in 2.18s
```
(The first focused run exposed stale fixture hashes after intentional negative mutations; hashes were resealed and the focused suite then passed: `169 passed` for the fixture suite.)

Full:
```
.venv/bin/pytest -q
1198 passed, 5 skipped, 1 warning in 70.50s
```
`git diff --check` passed. No frozen test or fixture path was modified.

## FINDINGS
- Initial SB4 negative fixtures also produced T11 because changing `violations` invalidated the chain. Recomputed the 3.1.0 chain hashes so each negative isolates SB4.
- A current-version assumption in `tests/test_number_lexeme.py` still searched for literal 3.0.0; made it use `TRACE_SCHEMA_VERSION`.
- Verifier finding F-1: `demo/bundle_hiring.py` embedded standalone reader hard-coded 3.0.0 and rejected current 3.1.0 traces. **Fixed:** allow 3.0.0 and 3.1.0, update unsupported-version wording, and add a regression test comparing standalone-derived root with `Trace.root`.
- Adversary old/new: no findings after independently checking all version sites, old fixtures, JSON/JSONL readers, chain/root derivation, and standalone demo readers.
- Adversary declaration-shape: no findings after attacking fully undeclared, half-declared, explicit-empty declarations, SB4 directions, and strict transactional abort behavior.
- No open reviewer findings.

## MUTATION TARGETS
Not run (per instruction not to run `mutation_probe`). Targets recorded for later proof:
- Remove runtime `_declaration_violations` early `None` return: direct undeclared runtime assertion fails.
- Disable T13 or empty `_VIOLATIONS_NULL_ADMITTED`: T13 negative or null-positive fixture fails.
- Revert SB4 raw/null distinction: both 3.1.0 SB4 direction fixtures fail.
- Omit 3.1.0 from trace chain binding: direct non-null root assertion fails.
- Omit 3.1.0 from trace loading/version scopes: current-version round-trip or governance tests fail.

## OUT OF SCOPE
No release notes/changelog, ADR edits, GraphSpec changes, SLO gate changes, frozen corpus changes, commit, push, tag, release, or mutation probe.

## AGENTS
- Lead: session implementation following `/tmp/architect-007.md`.
- Architect: Claude, plan in `/tmp/architect-007.md`.
- Implementer: Pi `openai-codex/gpt-5.6-luna`; corrected verifier finding F-1.
- Verifier: Pi `openai-codex/gpt-5.6-luna`, `/tmp/verifier-007.md`.
- Adversaries: Claude old/new reader lens (`/tmp/adversary-007-old-new.md`) and declaration-shape lens (`/tmp/adversary-007-half.md`).
- No commit, push, tag, release, or mutation probe.
