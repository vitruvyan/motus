# TASK 088 — The six findings of the DeepSeek review on motus #169

## RESULT

Implemented, all six corrections (F1–F6), in the same worktree/branch as task 086. Working tree
uncommitted as required: no commit, push, PR, tag, deploy, and no network — the OpenRouter call is
still behind the injected HTTP seam and no test touches it.

- `python3 -m pytest tests/tools -q` → **23 passed** (was 17; +6 tests).
- Mutation probes: the **8 existing** probes plus the **2 new** ones (proximity order; total budget)
  → **10/10 killed** (`python3 tools/mutation_probe.py /tmp/probes088.json --python python3 --allow-dirty`).
- The F5 neutralization was verified separately (see FINDINGS): renaming the marker **in the workflow's
  lookup** now turns the suite red; the first version of the test survived that change and was repaired.

## ARTIFACTS

Only the three files in scope were touched (`git status --short` lists exactly these):

- `.github/scripts/openrouter_review.py`
  - **F1 — doctrine is the base's.** `nested_guideline_paths` and `load_guidelines` docstrings now state
    that the workflow runs on `pull_request_target`, checks out the BASE commit, and therefore reads
    doctrine from `repo_root` only — an `AGENTS.md` that exists only on the PR head does not enter.
    A new `_stays_inside(repo_root, rel)` guard rejects any candidate that resolves outside
    `repo_root` (a diff path like `../mod.py`), so no read escapes the root.
  - **F3 — total doctrine budget.** New `MAX_TOTAL_GUIDELINES_CHARS = 80_000`. `load_guidelines`
    keeps a running total of admitted file text; a file that would cross the ceiling is not read into
    the prompt but replaced by `## <path>` + `[guidelines omitted: <path> (total budget)]`. The budget
    is the doctrine's alone: the diff has its own `MAX_DIFF_CHARS` and is never shortened for it.
  - **F4 — order by proximity.** `nested_guideline_paths` now sorts candidates by
    `(-touched_paths_under_dir, -depth, path)`: nearest first, ties by deeper directory, ties by
    alphabet. The cap of 5 therefore keeps the five *nearest* files, not the five alphabetically first.
  - **F6 — honest `diff_paths` note.** Its docstring now names the two limits of prefix parsing: a
    content line beginning `-- a/` / `++ b/` can be mistaken for a header, and git's quoted paths are
    not unquoted. Neither can raise — a path invented that way is only ever a candidate `AGENTS.md`
    lookup and is skipped when absent — so the worst case is one extra doctrine lookup.
  - Final newline added (the file lacked one).
- `.github/workflows/openrouter-review.yml`
  - **F2 — the upsert fails loudly.** The `Upsert the review comment` step now runs
    `set -euo pipefail`, so a failed `gh api` lookup (permission or network) aborts the step instead of
    yielding an empty id and posting a duplicate comment; the step comment declares this explicitly.
    The lookup's `| head -n1` was replaced with `| sed -n '1p'`: `head` closes the pipe early and,
    under `pipefail`, a `gh api` killed by SIGPIPE would be mistaken for a failed lookup; `sed`
    consumes the stream and exits 0.
- `tests/tools/test_openrouter_review.py`
  - **F1:** `test_a_guideline_present_only_in_the_pull_request_does_not_enter`,
    `test_a_diff_path_cannot_escape_the_repo_root`.
  - **F2:** `test_the_workflow_fails_loudly_when_the_lookup_fails` (reads the workflow, asserts
    `set -euo pipefail` and no `head -n1`).
  - **F3:** `test_total_guidelines_budget_omits_files_beyond_it` (five ~30k files → exactly three
    `[guidelines omitted: … (total budget)]` markers, the two that fit keep their content).
  - **F4:** `test_nested_doctrine_is_ordered_by_proximity_not_alphabet` (a 1-file `aaa/` and a 3-file
    `zzz/`; `zzz` must come first), `test_nested_doctrine_breaks_proximity_ties_by_depth`.
  - **F5:** `test_the_marker_is_exactly_the_workflow_marker` now opens the workflow and asserts the
    exact lookup expression `contains("<!-- openrouter-review -->")`, not a bare substring.
  - Final newline added (the file lacked one).
  - Contract tests were not touched (never allowed), and no assertion was weakened or removed.

## TESTS (raw output)

```
$ python3 -m pytest tests/tools -q
.......................                                                  [100%]
23 passed in 0.13s
```

```
$ python3 tools/mutation_probe.py /tmp/probes088.json --python python3 --allow-dirty
KILLED      guidelines truncation marker
KILLED      head-only guidelines truncation (tail lost)
KILLED      nested doctrine forgotten
KILLED      path heading dropped
KILLED      review marker dropped from footer
KILLED      reviewed commit dropped from footer
KILLED      empty diff calls the model
KILLED      diff truncation marker dropped
KILLED      nested doctrine ordered alphabetically, not by proximity
KILLED      total guidelines budget dropped

10/10 killed
```

```
$ python3 tools/mutation_probe.py /tmp/probe_marker.json --python python3 --allow-dirty
KILLED      workflow marker renamed without the script

1/1 killed
```

```
$ python3 -m py_compile .github/scripts/openrouter_review.py && echo COMPILE_OK
COMPILE_OK

$ yaml.safe_load(.github/workflows/openrouter-review.yml)  →  YAML OK
$ bash -n <extracted Upsert run block>                     →  BASH_OK
$ python3 .github/scripts/openrouter_review.py --help
usage: openrouter_review.py [-h] --diff DIFF --out OUT [--repo-root REPO_ROOT]
                            [--commit COMMIT]
```

Trailing byte of each edited file is `\n` (checked with `tail -c1 | od`), for all three.

## FINDINGS

No adversarial round was run (implementer task). Corrections made on self-review:

- **Fixed (F5, real defect).** The first version of `test_the_marker_is_exactly_the_workflow_marker`
  only asserted `REVIEW_MARKER in workflow_text`. The workflow *prose comment* above the step also
  contains `<!-- openrouter-review -->`, so renaming the actual `contains(...)` lookup literal left the
  test green — the marker probe **SURVIVED**. The test now pins the lookup expression itself
  (`contains("<marker>")`); the same probe is **KILLED**. Recorded because a test that passes for the
  wrong reason is exactly what this project's probe discipline exists to catch.
- **Fixed (F2, self-review of the fix).** `set -euo pipefail` added to a pipeline ending in `head -n1`
  would have aborted successful lookups on SIGPIPE (`gh api` exit 141). Replaced with `sed -n '1p'`,
  which consumes all input and exits 0; a genuine `gh api` failure still fails the pipeline and the
  assignment, and `set -e` aborts the step.
- **F3 semantics recorded.** Files are omitted individually when they no longer fit; a later small file
  that still fits would be admitted. With the task's five-30k-file case all three over-budget files are
  omitted either way.
- **F4 semantics recorded.** "Nearer" counts every touched path under the candidate's directory
  (including subdirectories), so an ancestor's count is never smaller than a descendant's and the
  depth tie-break decides between them when equal.

## OUT OF SCOPE, NOTICED

- `_stays_inside` is used by the nested walk; `load_guidelines` then reads the returned names, which
  are already root-contained, plus the fixed `GUIDELINE_FILENAMES`. No other read path exists.
- F2's `set -euo pipefail` is asserted by a test that greps the workflow text. A future reader who
  moves the lookup into a separate step must move the `set -euo pipefail` with it; the test would
  catch its absence, not the move.
- Still open, unchanged and not part of this task: the workflow's `--jq` lookup is the only place the
  marker string appears in a command; if the marker ever needs a regex-special character, `contains()`
  remains a literal test and is the right primitive.

## AGENTS

- Implementer: `openai-codex/gpt-5.6-luna` (Pi, this session). No subagents spawned.
- Scope read as instructed: the task brief plus only the three named files. No commit, push, PR,
  network traffic, or real OpenRouter call.

FATTO