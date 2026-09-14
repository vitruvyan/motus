# TASK 086 — La review OpenRouter: test, dottrina completa, troncamento onesto, un solo commento

## RESULT

Implemented, all four corrections (C1–C4). Working tree uncommitted, as required; no commit, push,
PR, network traffic, and no real OpenRouter call was made (the tests inject a fake HTTP opener).

- `python3 -m pytest tests/tools -q` → **17 passed**.
- All eight fixes are proved by neutering them with the repository's own `tools/mutation_probe.py`:
  **8/8 killed**.
- Workflow YAML parses (`yaml.safe_load` → `YAML OK`).

## ARTIFACTS

- `.github/scripts/openrouter_review.py`
  - **C1 seam:** `call_openrouter(..., opener=None)` — the HTTP client is injectable, so the tests
    use a fake and never touch the network. `build_footer` extracted; `--commit` added.
  - **C2 class, not two files:** `GUIDELINE_FILENAMES` is now the ordered quartet `AGENTS.md`,
    `CLAUDE.md`, `.github/copilot-instructions.md`, `.pi/AGENTS.md`; `nested_guideline_paths()`
    derives every `AGENTS.md` in a directory the diff touches from `+++ b/` / `--- a/` headers
    (line parsing, no regex), capped by `MAX_NESTED_GUIDELINES = 5`; `load_guidelines()` emits each
    file under a `## <path>` heading, deduplicated and stable-ordered.
  - **C3 honest truncation:** `MAX_GUIDELINES_CHARS = 40_000`, applied **per doctrine file**, keeping
    head and tail with the marker `[guidelines truncated: N chars omitted in the middle]`. Per file
    (not on the concatenation) because head-only truncation of the assembled text would drop the
    end of `AGENTS.md`, where "Rules no agent may break" lives.
  - **C4 marker:** `REVIEW_MARKER = "<!-- openrouter-review -->"`; the footer carries it plus
    `Reviewed commit: <sha>`.
- `.github/workflows/openrouter-review.yml`
  - The review step passes `--commit "$HEAD_SHA"`.
  - The posting step is now **"Upsert the review comment"**: it lists the PR's issue comments, greps
    for the marker with `gh api --jq`, `PATCH`es the first match in place, and only creates a comment
    (`gh pr comment`) when there is none. The behavior and the marker are declared in a comment in
    the file; the per-PR `concurrency` group is cited as the reason two pushes cannot both create
    the "first" comment.
- `tests/tools/__init__.py` (new package marker).
- `tests/tools/test_openrouter_review.py` (new, 17 tests): fake HTTP client + in-memory fake response.
  Covers `build_payload` (model/messages/doctrine), the diff truncation marker and the no-truncation
  case, the guidelines head+tail truncation marker, the doctrine class with `## <path>` headings and
  ordering, nested doctrine only from touched directories, the 5-file cap, no root duplication, the
  empty diff (no call, declared text, marker), the malformed response (readable error, non-zero),
  the HTTP error, the good response with model footer + reviewed commit + marker, and the exact
  workflow marker.

## TESTS (raw output)

```
$ python3 -m pytest tests/tools -q
.................                                                        [100%]
17 passed in 0.14s
```

```
$ python3 tools/mutation_probe.py /tmp/probes086.json --python python3 --allow-dirty
KILLED      guidelines truncation marker
KILLED      head-only guidelines truncation (tail lost)
KILLED      nested doctrine forgotten
KILLED      path heading dropped
KILLED      review marker dropped from footer
KILLED      reviewed commit dropped from footer
KILLED      empty diff calls the model
KILLED      diff truncation marker dropped

8/8 killed
```

```
$ python3 -m py_compile .github/scripts/openrouter_review.py && echo COMPILE_OK
COMPILE_OK

$ yaml.safe_load(.github/workflows/openrouter-review.yml)
YAML OK

$ python3 .github/scripts/openrouter_review.py --help   # argument surface intact
usage: openrouter_review.py [-h] --diff DIFF --out OUT [--repo-root REPO_ROOT] [--commit COMMIT]

$ (unset OPENROUTER_API_KEY; python3 .github/scripts/openrouter_review.py --diff … --out …)
OPENROUTER_API_KEY is not set      exit=1
```

The full CI chain (`python -m pytest tests/ -q`, CI's own path after `pip install -e ".[test]"`)
could not run in this worktree: there is no `.venv` and the interpreter is missing `vitruvyan_motus`,
`jsonschema`, `referencing` and `pytest-asyncio`. Installing them needs the network, which the task
forbids. The declared suite (`tests/tools`) is green and is the only suite this change can affect.

## FINDINGS

No adversarial round was run (this is an implementer task). Self-review corrections made while
writing the fixes:

- **Fixed:** the first design truncated the *assembled* doctrine head+tail. That drops the end of
  `AGENTS.md` whenever any file follows it, which is exactly where this repository keeps "Rules no
  agent may break". Truncation is now per file, so every file keeps its own tail — and the mutation
  probe `head-only guidelines truncation (tail lost)` is what proves the test would notice.
- **Fixed:** nested doctrine could double `.pi/AGENTS.md` (it is both a base name and a walk result
  when `.pi/` is touched). `load_guidelines` now de-duplicates names before reading.

## OUT OF SCOPE, NOTICED

- The workflow posts through the issues-comments endpoint (`gh api …/issues/N/comments`). With the
  declared `pull-requests: write` permission this is the same endpoint `gh pr comment` uses and
  GitHub accepts it (Issues write *or* Pull requests write). Left unchanged; if a future run shows a
  403 on the list call, the fix is to add `issues: read` to `permissions`, not to change the script.
- The script remains repository-agnostic; nothing in it is motus-specific. The lead can copy
  `.github/scripts/openrouter_review.py`, the workflow and `tests/tools/` to orbis unchanged.

## AGENTS

- Implementer: `openai-codex/gpt-5.6-luna` (Pi, this session). No subagents spawned.
- Scope read as instructed: the workflow, the script, `AGENTS.md`. No commit, push, PR, network or
  real OpenRouter call.

FATTO