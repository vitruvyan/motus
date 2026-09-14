# Task 092 — the five residual findings of motus #170 on the review script

## RESULT

All five findings closed. Only the three files in scope were touched; no
commit, push, PR or network access. `tests/tools` is green (28 passed), and
15/15 mutation probes are KILLED, including the two new ones the brief asked
for (per-file cap vs. total budget; author filter in the comment lookup) plus
two more written for the fixes that also needed a neutralisation proof
(pipefail scoped to the lookup step; `realpath` in `_stays_inside`).

Findings and what changed:

1. **Total budget did not hold two files at the per-file cap.**
   `load_guidelines` counted only the text of each file, so two files at
   `MAX_GUIDELINES_CHARS` (40 000) assembled to 80 000 + two headings + a
   separator = 80 030 > `MAX_TOTAL_GUIDELINES_CHARS` (80 000) while claiming to
   be within it. The per-file cap now covers the whole *section* —
   `cap = MAX_GUIDELINES_CHARS - len("## <name>\n\n") - len(separator)` — and
   the running total counts the heading, the separator and the truncated
   text. `_truncate_guidelines` takes the cap as a parameter and now counts
   its own truncation marker against it (solving the marker's length fixed
   point, so the returned string is never longer than the cap). Two files at
   exactly 40 000 both enter and the assembled doctrine is 79 998 ≤ 80 000;
   a third is omitted under `[guidelines omitted: <name> (total budget)]`, as
   the existing five-file test still requires.

2. **The workflow test looked at the file, not the step.**
   `test_the_workflow_fails_loudly_when_the_lookup_fails` asserted
   `"set -euo pipefail" in workflow`, which a pipefail moved into any other
   step would satisfy. PyYAML is **not** a declared test dependency
   (`constraints/test.txt` pins pytest, pytest-asyncio and jsonschema only),
   so the test file carries `_workflow_steps`, a small line-based parser for
   the one shape the workflow uses (`steps:` → `- name:` with a literal
   `run: |`). The test finds the single step whose run performs the comment
   lookup (`issues/$PR_NUMBER/comments`), then asserts the pipefail is a
   non-comment line of *that* step's run and that `head -n1` is absent from
   it. Neutralisation: move the pipefail out of the lookup step → red.

3. **The lookup did not filter by author.**
   The jq filter now reads
   `select(.user.login == "github-actions[bot]" and (.body | contains("<!-- openrouter-review -->")))`,
   so a pull request cannot plant a comment carrying the marker and have the
   job PATCH it (the job runs on `pull_request_target`, where `github.token`
   can write comments; the bot's login is the discriminator). Test
   `test_the_comment_lookup_is_restricted_to_the_bot_and_the_marker` pins the
   exact expression on the workflow text, and
   `test_the_marker_is_exactly_the_workflow_marker` still pins the marker
   contract from both ends. Neutralisation: drop the author filter → red.
   Verified with `jq` that the new filter yields only the bot's id where the
   old one yielded two.

4. **Empty assertion** `assert len("x" * 29_990) > 0` removed; the
   `MAX_TOTAL_GUIDELINES_CHARS == 80_000` assertion beside it was kept.

5. **`_stays_inside` followed no symlinks.** It used `abspath`, so a diff
   path through a symlink that leaves the root (`base/sub` → `outside`) was
   accepted because its spelling started with the root, and the doctrine was
   read from outside the base checkout. It now uses `realpath` on both the
   root and the candidate; the docstring says what that means (a `..` that
   leaves and comes back to a real file inside is allowed; where the bytes
   live is the property). Two tests: a symlink inside the root pointing
   outside is refused by `nested_guideline_paths` and its content never enters
   the doctrine; a symlink inside the root pointing inside is followed.
   Neutralisation: revert to `abspath` → red.

## ARTIFACTS

- `.github/scripts/openrouter_review.py` — findings 1 and 5 (`load_guidelines`,
  `_truncate_guidelines`, `_stays_inside`, docstrings).
- `.github/workflows/openrouter-review.yml` — finding 3 (jq `select`).
- `tests/tools/test_openrouter_review.py` — findings 2, 3, 4, 5 and the two
  budget tests for finding 1; `_workflow_steps` parser; `import os`.
- This REPORT. The probe file was written to `/tmp/probes092.json` and its 15
  probes are listed under TESTS; it is not committed.

No frozen path was touched (`tests/contract/`, `tests/compat/` untouched). No
assertion was weakened or removed except the empty one finding 4 names.

## TESTS (raw output)

```
$ python3 -m pytest tests/tools -q
............................                                             [100%]
28 passed in 0.16s
```

```
$ python3 tools/mutation_probe.py /tmp/probes092.json --python python3 --allow-dirty
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
KILLED      workflow marker renamed without the script
KILLED      per-file cap ignores the heading (two files at the cap overrun the total)
KILLED      comment lookup loses the author filter
KILLED      pipefail moved out of the lookup step
KILLED      _stays_inside uses abspath, so a symlink out is trusted

15/15 killed
```

The eleven probes before the four new ones are the existing set from task 088
(reconstructed into `/tmp/probes092.json`): guidelines truncation marker;
head-only truncation; nested doctrine forgotten; path heading dropped; review
marker dropped from footer; reviewed commit dropped from footer; empty diff
calls the model; diff truncation marker dropped; nested doctrine ordered
alphabetically; total guidelines budget dropped; workflow marker renamed.
The four new probes are the last four lines above.

```
$ python3 -m py_compile .github/scripts/openrouter_review.py && echo COMPILE_OK
COMPILE_OK
$ python3 -c "import yaml; yaml.safe_load(open('.github/workflows/openrouter-review.yml')); print('YAML_OK')"
YAML_OK
$ bash -n <extracted run block of the comment-lookup step>   → BASH_OK
$ jq filter on one bot comment and one forged comment         → only the bot id (7), not 9
```

## FINDINGS

No adversarial round was run: this is an implementer task and the brief names
no reviewer. Self-review findings, recorded:

- **A retained class, deliberately bounded (finding 1).** Under the chosen
  branch of #170 — *reduce the per-file cap and count the headings* — the
  omission marker itself is not charged to `MAX_TOTAL_GUIDELINES_CHARS`. So
  for three files at the cap the assembled text can exceed the budget by the
  omitted files' markers (the two entering sections are 79 998, plus the third
  file's marker). The test therefore asserts `len(doctrine) <= 80 000` for the
  **two**-file case (where there is no marker), and for the three-file case
  only that the third is omitted with a marker. Charging markers too would
  mean reserving a marker allowance up front and shrinking the content budget
  further; the finding offered that as the *other* branch ("riserva lo slack …
  dei marcatori"), and the brief's two-file/two-ascertained assertions are met
  without it. Left open and named here rather than silently.
- **`_truncate_guidelines` is only budget-true for a sane cap.** The marker is
  itself ~58 chars; if `cap` were smaller than the marker the returned string
  cannot be ≤ `cap` and the function returns the marker alone. Every call site
  passes a cap near 40 000 (the per-file cap minus a heading and a separator),
  so this is unreachable today; the docstring says so instead of asserting it.
  A property check over caps 1000–40 000 and sizes up to 10× cap finds no
  violation; caps 10 and 50 do, as expected.
- **The workflow test now depends on the workflow's textual shape.** It is a
  parser for the shape this file uses (literal `run:` blocks under `- name:`),
  not a YAML parser, because PyYAML is not a declared dependency. If the step
  ever uses a folded `run: >` or an inline `run:` the parser returns an empty
  run and the test **fails loudly** (the pipefail assertion fails), which is
  the correct direction for a test guarding a security step.

## OUT OF SCOPE, NOTICED

- Nothing about the OpenRouter request/response path, the model default, or
  the `pull_request_target` security model was opened; those were #168/#169
  territory.
- The workflow's own YAML was validated with `yaml.safe_load` here, but the
  test suite still does not depend on PyYAML; adding it would be a new test
  dependency and is not done.

## AGENTS

One implementer (pi, `openai-codex/gpt-5.6-luna`), no architect, no reviewer,
no verifier — the brief named none. No model other than the session default
was used; no network access.