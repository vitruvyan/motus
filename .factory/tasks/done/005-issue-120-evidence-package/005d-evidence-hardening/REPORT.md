# TASK 005d — hostile evidence inputs and CI portability

## RESULT
Implemented the fixed-package strict parsing, ZIP read refusal, duplicate physical-name, and portable CLI test fixes. No commit, push, or mutation probe.

## ARTIFACTS
- `src/vitruvyan_motus/evidence.py`
- `tests/test_evidence_package.py`
- `.factory/tasks/005d-evidence-hardening/REPORT.md`

## TESTS (raw output)
- `.venv/bin/pytest -q tests/test_evidence_package.py` → `26 passed, 1 warning`
- `.venv/bin/pytest -q` → `1180 passed, 5 skipped, 1 warning`
- `git diff --check` → clean

## FINDINGS
- **Fixed:** `manifest.json`, `core/trace.json`, `core/graphspec.json`, and `core/receipt.json` now use `contract.validate._loads_strict` lazily. Duplicate members are reported as J1 and noncanonical numeric lexemes as J2 rather than being laundered by `json.loads`.
- **Fixed:** all production `archive.read` calls in evidence verification go through `_read_member`, which catches unsupported, encrypted/refused, oversized, corrupt/truncated, and OS read failures and retains the member name in the result.
- **Fixed:** duplicate physical ZIP names are rejected immediately after `namelist()`, before any fixed-path lookup, as `<duplicate member>: name`.
- **Fixed:** package CLI subprocess tests use `[sys.executable, "-m", "vitruvyan_motus.contract.validate", ...]`, not a repository-local `.venv` path.

## JSON LOAD SWEEP
- `evidence.py`: fixed package members are contract input and now use the strict contract loader; no direct `json.loads` remains.
- `trace.py` `_loads_canonical`: runtime trace contract loader with its own lexical/version behavior; `to_dict` uses `json.loads(json.dumps(...))` only as an internal isolated materialization, not external input.
- `commitlog.py`: `identity.json`, checkpoints, and commitment-window lines are private store records, classified as internal persistence input; unchanged in this package-boundary task.
- `compat.py` `GraphState.from_json`: public model serialization, not a fixed evidence/contract artifact; unchanged.
- `mcp/__main__.py` and `mcp/diagnose.py`: user/tool input and trusted schema/heuristic inspection paths, not fixed evidence package members; unchanged.

## CORRECTION ROUND
- **Fixed:** `_read_member` now catches `zlib.error`, including real corrupted-DEFLATE reads.
- **Fixed:** the shared strict-loader boundary converts `RecursionError` from deeply nested JSON into a named J1 refusal for package verification and the contract CLI.
- **Fixed:** fixed JSON members are bounded at 28 MiB during decompression, including their manifest-digest pass; arbitrary supplementary members retain size freedom and are hashed incrementally. This accommodates the measured 27.4 MiB medium trace while staying below the tested astral-string failure threshold.
- **Fixed:** strict loading rejects excessive nesting before parsing, while the fixed-member byte ceiling prevents parsed-object amplification without imposing the abandoned token-count cliff on ordinary traces; duplicate manifest entries are reported and hashed only once.
- **Fixed:** package parse diagnostics distinguish J1/J2 strict refusals from ordinary invalid JSON or UTF-8 errors using `isinstance` against lazily imported exception classes.
- Added a tiny nesting-budget mutation test and explicit ordinary malformed/UTF-8 manifest diagnostics.
- The fixed-member bound was measured down from 32 MiB after 30 MiB astral input still exhausted a 400 MiB process; 28 MiB accepts the measured medium trace and stays below that tested hostile threshold. This does not claim 32 MiB is safe.
- Added real corrupted-DEFLATE and deep-nesting regressions for manifest and trace, including a no-traceback CLI assertion.
- Focused correction tests: `.venv/bin/pytest -q tests/test_evidence_package.py tests/test_number_lexeme.py tests/test_contract_fixtures.py` → `270 passed, 1 warning`.
- The size-bound regression monkeypatches the limit to 32 bytes and exercises both manifest and trace without allocating large inputs.
- Strict-loader regressions: `.venv/bin/pytest -q tests/test_number_lexeme.py tests/test_contract_fixtures.py` → `237 passed`.

## OUT OF SCOPE, NOTICED
No new dependencies, frozen paths, schemas, or lazy-import boundaries were changed. The duplicate-name regression emits the standard `zipfile` warning while constructing the intentionally malformed fixture.

## AGENTS
- Initial verifier reconnaissance: `motus-verifier-005d`.
- Final verifier dispatch was started; local focused and full suites were run to completion by the lead.
