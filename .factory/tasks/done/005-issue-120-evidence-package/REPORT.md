# TASK 005 / #120 — Evidence Package

## RESULT
Implemented and adversarially verified. Working tree remains uncommitted as required.

## ARTIFACTS
- `src/vitruvyan_motus/evidence.py`: deterministic evidence envelope, `pack`, `verify_package`, `PackageVerdict`.
- `src/vitruvyan_motus/commitlog.py`: `find_execution_ref` for uniquely locating sealed BEGINs.
- `src/vitruvyan_motus/__init__.py`: public exports.
- `contract/validate.py`: binary `motus-validate package` CLI path with lazy evidence import.
- `README.md`: package verification and public API documentation.
- `demo/bundle_hiring.py`, `demo/bundle_scenarios.py`: shared deterministic legacy-demo ZIP writer; `demo/out` not regenerated.
- `tests/test_evidence_package.py`: round trips, receipts, tampering, manifest accounting, resumed/unfinished runs, zip-slip, malformed inputs, CLI and metadata.
- `.factory/tasks/PLAN.md`: approved architect plan.

## TESTS (raw output)
- `.venv/bin/pytest -q` → `1160 passed, 5 skipped in 176.98s`
- `.venv/bin/pytest -q tests/test_evidence_package.py` → `15 passed` (includes trace validation and GraphSpec-binding regressions)
- import guard: `1 passed` for `test_importing_motus_pulls_no_third_party_module`.
- public surface/README tests: `2 passed`.
- `.venv/bin/python tools/check_frozen_paths.py HEAD~1 HEAD` → `Frozen contract paths: PASS`.
- `git diff --check` → clean.
- CLI good/no-receipt package exits 0 and prints INTEGRITY CLEAN plus the explicit EXISTENCE NOT ESTABLISHED honest-limit explanation; damaged and trace-violation packages exit 1.
- Fresh-byte manual checks: completed logged, no-log, unfinished, resumed, proofs, attachments and anchors; all five tamper targets; forged trace with recomputed manifest; malformed ZIP/manifest; absolute, traversal and Windows-drive zip-slip; no filesystem writes.

## FINDINGS
Every concrete finding from the verifier and both adversarial lenses was processed:

- **Fixed:** package CLI emitted literal `\\n`; now emits real newline separators and tests assert `splitlines()`.
- **Fixed:** no-log manifest used `trace.to_dict().get("root")`, which is absent; now uses `bundle.trace.root` and has a regression assertion.
- **Fixed:** evidence tests initially covered only four paths; expanded to 13 tests covering the task/plan acceptance matrix.
- **Fixed:** non-dict `core/trace.json` could raise `AttributeError`; `verify_package` now always runs `validate_trace` (lazily), records string trace violations, passes a valid GraphSpec for SB checks, and returns a result for hostile JSON shapes. CLI reports INTEGRITY CLEAN/VIOLATION and the no-receipt existence limit.
- **Fixed:** manifest file-list omissions and undeclared physical members were silently accepted; physical members other than `manifest.json` must be accounted for and are reported damaged when omitted.
- **Fixed:** `find_execution_ref` ambiguity/missing/sequence-selected behavior is covered.
- **Fixed:** deterministic ZIP metadata, fixed layout, proofs/attachments, zip-slip and no-write behavior are covered.
- **Open, non-blocking:** a highly compressed ZIP can expand to very large memory use because verification reads members fully in memory. The CTO acceptance criteria specify bytes-only verification and tamper/zip-slip behavior but no package-size ceiling; adding an arbitrary cap would reject legitimate caller attachments. This remains a follow-up hardening decision, not silently forgotten.
- **Open, out of scope:** inherited demo archives retain their prior layout because `demo/out` is frozen; the approved plan resolves the brief's “call pack” wording by centralizing deterministic ZIP writing rather than forcing legacy OpenTimestamps members into the fixed evidence-package layout.

## OUT OF SCOPE, NOTICED
- No contract schema or verifier semantics changed; only the package CLI was added.
- No frozen `tests/contract/`, `tests/compat/`, workflows, SLO/benchmark files, or `demo/out` artifacts changed.
- No runtime dependency was added; `jsonschema` remains lazy and bare `import vitruvyan_motus` stays third-party-free.
- No commits, pushes, releases, or mutation probe on the dirty tree.
- Pre-existing untracked task briefs for later tasks (`.factory/tasks/006-adr-028-window-line-key.md`, `.factory/tasks/007-adr-029-violations-null.md`) were not modified.

## MUTATION TARGETS
- Remove `pack`/`verify_package`: evidence-package tests must fail.
- Revert `bundle.trace.root` to `trace_dict.get("root")`: no-log fingerprint test must fail.
- Remove `find_execution_ref` ambiguity checks: its missing/ambiguous tests must fail.
- Remove fixed-path trace/receipt verification or trust manifest execution fields: forged-manifest integrity test must fail.
- Remove manifest completeness sweep: omitted-entry and injected-member tests must fail.
- Remove non-dict trace guard: hostile trace regression must raise/fail.
- Change CLI `"\n"` to literal `"\\n"`: CLI `splitlines()` assertion must fail.
- Remove ZIP metadata normalization: fixed timestamp/system/permissions tests must fail.

## AGENTS
- Architect: `claude`, plan copied to `.factory/tasks/PLAN.md`.
- Implementer: `openai-codex/gpt-5.6-luna`, one correction round.
- Verifier: `openai-codex/gpt-5.6-luna`, reports `/tmp/verifier-120.md` and `/tmp/verifier-120-round2.md`.
- Adversaries: `claude`, lens 1 second machine (`/tmp/adversary-second-machine-120-round2.md`); lens 2 manifest identity (`/tmp/adversary-manifest-120-round2.md`).

## 005b

### RESULT
Closed the no-receipt manifest-identity gap. `verify_package` now always validates the fixed-path trace and optionally correlates it with the fixed-path GraphSpec, independently of `manifest.json`. `PackageVerdict.trace_violations` carries rule/path/message strings. No commit or push was made.

### ARTIFACTS
- `src/vitruvyan_motus/evidence.py`: trace validation for every package, SB0 reporting for missing/invalid GraphSpecs, and separate trace findings.
- `contract/validate.py`: no-receipt package output for `INTEGRITY` and honest `EXISTENCE NOT ESTABLISHED` limits; exit 1 on trace violations.
- `tests/test_evidence_package.py`: no-receipt unresealed/resealed traces, receipt-vs-resealed-trace protection, wrong/invalid/missing GraphSpec cases.

### TESTS (raw output)
- `.venv/bin/pytest -q tests/test_evidence_package.py` → `18 passed in 7.33s`.
- `.venv/bin/pytest -q` → `1164 passed, 5 skipped in 183.98s`.
- `.venv/bin/python tools/check_frozen_paths.py HEAD~1 HEAD` → `Frozen contract paths: PASS`.
- `git diff --check` → clean.
- 005b verifier: full suite `1161 passed, 5 skipped`; focused compatibility/evidence suite `46 passed`; required no-receipt, resealed receipt, GraphSpec, malformed trace, import and CLI cases passed.

### FINDINGS
- **Fixed:** no-receipt trace edits with an attacker-recomputed manifest now produce `trace_violations` (including T11) and CLI exit 1.
- **Fixed:** no-receipt traces that are internally resealed produce no trace violation but explicitly report `EXISTENCE NOT ESTABLISHED`; the docstring records that anyone holding the recipe can reseal an edited trace.
- **Fixed:** receipt-bearing packages still pass the edited/resealed trace through `verify()`, where the receipt root/P8 catches it.
- **Fixed:** valid-but-wrong GraphSpecs produce SB violations; missing or schema-invalid GraphSpecs produce SB0 plus their GraphSpec validation finding instead of silently disabling spec-correlated checks.
- **Fixed:** mutation targets were exercised for skipping `validate_trace`, dropping the existence block, and ignoring GraphSpec correlation.
- **Open, non-blocking:** decompression bombs can consume substantial memory because package members are read fully; no size ceiling is specified by #120, and imposing an arbitrary cap requires a separate decision.

### MUTATION TARGETS
- Remove the unconditional `validate_trace` call: the no-receipt unsealed-edit test must lose T11 and fail.
- Restore the old receipt-only trace path: no-receipt trace tests must fail.
- Remove the `EXISTENCE NOT ESTABLISHED` CLI block: exact no-receipt CLI output tests must fail.
- Remove `bool(package.trace_violations)` from the CLI exit calculation: the unresealed no-receipt CLI test must incorrectly pass and therefore fail.
- Pass `spec=None` unconditionally or ignore `validate_graphspec`: wrong-GraphSpec SB tests must fail.
- Remove SB0 handling for missing/invalid GraphSpec: the missing/invalid GraphSpec regression must fail.
- Remove receipt verification/P8 path: the resealed trace with receipt test must fail.

### OUT OF SCOPE, NOTICED
- The package manifest remains transport accounting only; its execution block is not trusted for validity.
- No frozen paths, `demo/out`, schema, runtime, or SLO files changed.

### AGENTS
- Implementer: `openai-codex/gpt-5.6-luna` (`implementer-120b`).
- Verifier: `openai-codex/gpt-5.6-luna` (`verifier-120b`), `/tmp/verifier-120b.md`.
- Adversary: `claude` (`adversary-manifest-120-round3`), `/tmp/adversary-manifest-120-round3.md`.

## 005c

### RESULT
Kept the non-dict trace guard. It is semantically distinct from the generic validator path: it produces the precise `TRACE core/trace.json: not valid JSON object` refusal instead of a generic schema/exception-derived message. The guard now documents that distinction, and tests assert the exact tuple for both list-shaped and string-shaped traces.

### TESTS
- Focused evidence tests: `19 passed`.
- Hand-neutered guard by direct byte edit: both exact-shape tests failed as expected; the neutered source was restored byte-for-byte without `git checkout`.
- Restored targeted tests: `2 passed`.
- Full suite: `1165 passed, 5 skipped`.
- Frozen-path guard: `PASS`.
- Bare-import/third-party isolation: passed; direct module-delta probe was `[]`.
- `git diff --check`: clean.

### FINDINGS
- **Fixed:** the mutation survivor was not equivalent; exact user-facing trace diagnostics distinguish the guarded path from the generic fall-through.
- **Open:** none.

### MUTATION TARGETS
- Replace `elif not isinstance(trace, dict):` with `elif False:`: the parametrized list/string exact-refusal tests must fail.
- Remove or alter the precise refusal string: exact `trace_violations` assertions must fail.
- Remove the guard comment: review must identify the undocumented coverage distinction.

### OUT OF SCOPE, NOTICED
- No `mutation_probe.py`, commit, push, or checkout was used.

### AGENTS
- Implementer: `openai-codex/gpt-5.6-luna` (`implementer-120c`).
- Verifier: `openai-codex/gpt-5.6-luna` (`verifier-120c`), `/tmp/verifier-120c.md`.
