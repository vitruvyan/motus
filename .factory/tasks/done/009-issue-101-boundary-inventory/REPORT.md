# Task 009 — #101 boundary inventory

## RESULT
Implemented the inventory-only regression coverage. No runtime behavior,
dependencies, or frozen paths were changed.

## ARTIFACTS
- `tests/test_stand_ins.py`: added `BOUNDARY_INVENTORY` for every keyword-only
  parameter in `Runtime.__init__`, `CommitmentLog.begin`, and the
  `CommitmentLog.receipt_for(anchors=...)` value boundary, with protocol/value
  classification and verdict/reason fields; added inspect-derived coverage.
- `REPORT.md`: this report.
- `contract/guarantees.md`: confirmed unchanged and not part of the diff.

## TESTS
Focused command:

```text
PYTHONPATH=src pytest -q tests/test_stand_ins.py tests/test_commitlog.py tests/test_commit_lifecycle.py
........................................................................ [ 80%]
.................                                                        [100%]
89 passed in 2.34s
```

Frozen-path check:

```text
python3 tools/check_frozen_paths.py origin/main HEAD
Frozen contract paths: PASS
```

Full suite command:

```text
.venv/bin/pytest -q
```

Result:

```text
1200 passed, 5 skipped, 1 warning in 96.51s (0:01:36)
```

The warning is the existing duplicate-zip-member warning in
`tests/test_evidence_package.py`.

## FINDINGS
- Verifier initially found stale report text claiming the full suite could not
  collect because of `referencing`; the required `.venv` suite is green and the
  report was corrected.
- Verifier noted that listener callbacks can affect scheduling/cancellation
  while remaining non-authoritative under `guarantees.md` §6; the inventory
  reason now says a stand-in cannot alter the authoritative evidence verdict.

## MUTATION TARGETS
- Remove or omit any row from `BOUNDARY_INVENTORY`: the inspect-derived set
  equality must fail.
- Add a keyword-only parameter to any inspected signature without adding a
  row: the same regression must fail.
- Change a protocol row's kind to `value`, or remove its verdict/reason: the
  inventory assertions must fail.
- Replace the `ask` boundary decision with an unconditional acknowledgement:
  existing commitment lifecycle coverage should catch the stand-in no longer
  degrading to `AssuranceMode.LOCAL`.

## OUT OF SCOPE
No runtime validation or behavior change; no dependency changes; no contract
or frozen-path edits; no restoration of a withdrawn stand-in rejection rule;
no mutation probe.

## AGENTS
- Implementer: Pi `openai-codex/gpt-5.6-luna`.
- Verifier: Pi `openai-codex/gpt-5.6-luna`, `/tmp/verifier-009.md`.
- No architect or adversary; no correction round beyond report/test wording.
- No commit, push, tag, release, or mutation probe.

## 009b

## RESULT
Implemented the inventory-test correction only. The inventory now derives
both `POSITIONAL_OR_KEYWORD` and `KEYWORD_ONLY` parameters with `inspect`,
excluding `self`; `Runtime.nodes` is recorded as an authoritative caller-
supplied node registry, and protocol-row key completeness is asserted before
verdict comparison. No runtime or frozen files changed.

## ARTIFACTS
- `tests/test_stand_ins.py`: added positional-or-keyword inventory rows,
  `Runtime.nodes` classification, and exact protocol-row key-set assertion.
- `.factory/tasks/done/009-issue-101-boundary-inventory/REPORT.md`: this
  section and raw verification outputs.

## TESTS
Focused (`.venv/bin/pytest -q tests/test_stand_ins.py tests/test_commitlog.py tests/test_commit_lifecycle.py`):

```text
........................................................................ [ 80%]
.................                                                        [100%]
89 passed in 2.90s
```

Full (`.venv/bin/pytest -q`):

```text
=============================== warnings summary ===============================
tests/test_evidence_package.py::test_duplicate_physical_member_names_are_refused_before_lookup
  /usr/lib/python3.12/zipfile/__init__.py:1620: UserWarning: Duplicate name: 'manifest.json'
    return self._open_to_write(zinfo, force_zip64=force_zip64)

1200 passed, 5 skipped, 1 warning in 114.59s (0:01:54)
```

Frozen-path check (`python3 tools/check_frozen_paths.py origin/main HEAD`):

```text
Frozen contract paths: PASS
```

Diff check (`git diff --check`):

```text
[no output; exit 0]
```

Diff stat (`git diff --stat`):

```text
 tests/test_stand_ins.py | 16 +++++++++++++++-
 1 file changed, 15 insertions(+), 1 deletion(-)
```

Verifier rerun (Pi `openai-codex/gpt-5.6-luna`):

```text
.venv/bin/pytest -q tests/test_stand_ins.py tests/test_commitlog.py tests/test_commit_lifecycle.py
89 passed in 1.95s

.venv/bin/pytest -q
1200 passed, 5 skipped, 1 warning in 99.31s

python3 tools/check_frozen_paths.py origin/main HEAD
Frozen contract paths: PASS

git diff --check
[no output; exit 0]
```

## FINDINGS
- None. The requested inventory-test changes are complete; runtime behavior is
  unchanged.

## MUTATION TARGETS
- Remove a positional parameter row or revert the inspect kind filter: the
  inspected-name equality fails.
- Add or remove a protocol row, or reclassify `Runtime.nodes` as a value: the
  protocol-row key-set/verdict assertions fail.
- No mutation probe was run, per task instructions.

## OUT OF SCOPE
No runtime changes, contract/frozen-path changes, dependency changes, commit,
push, tag, release, or mutation probe.

## AGENTS
- Lead: current Pi session.
- Implementer: Pi `openai-codex/gpt-5.6-luna`.
- Verifier: Pi `openai-codex/gpt-5.6-luna`, `/tmp/verifier-009b.md`; independently confirmed inventory coverage and reran focused/full pytest, frozen-path, and diff checks.
- No architect or adversary; no correction round.
- No commit, push, tag, release, or mutation probe.
