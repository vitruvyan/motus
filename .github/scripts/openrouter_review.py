#!/usr/bin/env python3
"""Ask an OpenRouter-hosted model to review a pull request diff.

Standalone (stdlib only) so it runs in CI with nothing but the interpreter.
Repository-agnostic by design: it reads the repository's own doctrine files at
runtime and hands them to the model as review doctrine, instead of encoding
one project's rules here. That means the same script is reused, unmodified,
across every repository that wants OpenRouter-based PR review.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.request

OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"

# The doctrine is a *class* of files, not two fixed names. Read in this order:
# the root instructions first, then the per-agent entry points, then any
# AGENTS.md sitting in a directory the diff touches (nested doctrine). The
# order is the reading order the model gets, so it is stable and explicit.
GUIDELINE_FILENAMES = (
    "AGENTS.md",
    "CLAUDE.md",
    ".github/copilot-instructions.md",
    ".pi/AGENTS.md",
)
# A change touches a directory; a directory may carry its own AGENTS.md. Only
# the directories the diff names are walked, and only this many nested files
# are admitted, so a huge tree cannot flood the prompt.
MAX_NESTED_GUIDELINES = 5
MAX_GUIDELINES_CHARS = 40_000
# Per-file truncation alone is not a budget: five files at the per-file cap are
# 200k of prompt. This is the ceiling for the doctrine as a whole; past it the
# files that no longer fit are omitted by name. The diff has its own budget
# (MAX_DIFF_CHARS) and is never shortened to make room here.
MAX_TOTAL_GUIDELINES_CHARS = 80_000
MAX_DIFF_CHARS = 120_000

# The marker a workflow greps for to update one review comment in place
# instead of stacking a new comment on every push (see openrouter-review.yml).
REVIEW_MARKER = "<!-- openrouter-review -->"

REVIEW_INSTRUCTIONS = """\
You are reviewing a pull request diff for this repository. Below, wrapped in \
<repo-guidelines>, are the repository's own instructions for how code should \
be written and reviewed here. Honor them as this repository's actual review \
doctrine, not generic best practice - a rule stated there beats a generic \
style opinion.

<repo-guidelines>
{guidelines}
</repo-guidelines>

Write a concise code review of the diff below. Structure it as:
1. A one-line verdict (approve / request changes / comment).
2. Findings, most severe first, each with file:line, what is wrong, and why \
it matters. Skip this section if there is nothing worth flagging.
3. Anything the diff does well, in one line, if genuinely notable. Skip \
if not.

Keep it terse. Do not restate the whole diff back. If the diff is truncated, \
say so and review only what you can see.
"""


def diff_paths(diff: str) -> list[str]:
    """Repository-relative paths a unified diff touches.

    Read line by line off the ``+++ b/<path>`` and ``--- a/<path>`` headers,
    which is what a diff actually says; no pattern is matched against the
    raw bytes. Both sides are kept so a rename contributes two directories.
    ``/dev/null`` is one side of an add or a delete, not a path, and is
    skipped.

    This is prefix parsing, not a diff parser, and two limits are worth
    naming. A *content* line can begin with ``-- a/`` or ``++ b/`` (a deleted
    line whose text was ``-- a/foo`` restates itself as ``--- a/foo``), and is
    then taken for a header; and git's quoted paths (special characters,
    ``core.quotePath``) are not unquoted. Neither can raise: a path invented
    this way is only ever looked up as a candidate ``AGENTS.md`` and skipped
    when the base checkout has no such file, so the worst case is one extra
    doctrine lookup, never a wrong result and never an error.
    """
    paths: list[str] = []
    for line in diff.splitlines():
        for prefix in ("+++ b/", "--- a/"):
            if line.startswith(prefix):
                path = line[len(prefix):].split("\t", 1)[0].strip()
                if path and path != "/dev/null":
                    paths.append(path)
    return paths


def _stays_inside(repo_root: str, rel: str) -> bool:
    """True when ``rel`` resolves inside ``repo_root``.

    Resolution uses ``realpath``, so symlinks are followed: a link inside the
    tree that points outside it is rejected, not trusted because its name is
    inside. ``..`` segments are resolved the same way, so a path that climbs
    out of the root is refused while one that leaves and comes back to a real
    file inside is allowed. The property is about where the bytes live, not
    how the path is spelled.
    """
    root = os.path.realpath(repo_root)
    candidate = os.path.realpath(os.path.join(root, rel))
    return candidate == root or candidate.startswith(root + os.sep)


def nested_guideline_paths(repo_root: str, diff: str) -> list[str]:
    """``AGENTS.md`` files in directories the diff touched, at most five.

    ``AGENTS.md`` is a tree convention: a component may carry its own rules
    under its directory, and a change to that directory is reviewed against
    them too. Every ancestor directory of every touched path is checked and the
    result is deduplicated.

    Order is by proximity to the change, not by name: more touched paths under
    a directory first, then the deeper directory, then alphabetical so the same
    diff always yields the same doctrine. The cap at ``MAX_NESTED_GUIDELINES``
    therefore keeps the five *nearest* files, not the five alphabetically first.

    **The doctrine is the base revision's, never the pull request's.** The
    workflow runs on ``pull_request_target`` and checks out the BASE commit, so
    every candidate is tested for existence under ``repo_root`` and therefore a
    path the diff names whose ``AGENTS.md`` exists only on the PR head simply
    does not enter. Nothing is read outside ``repo_root``: paths are resolved
    with ``realpath``, so a diff path that tries to climb out with ``..``, or
    one that reaches the outside through a symlink, is discarded rather than
    followed.
    """
    touched = diff_paths(diff)
    found: set[str] = set()
    for path in touched:
        directory = os.path.dirname(path)
        while directory:
            rel = os.path.join(directory, "AGENTS.md")
            if _stays_inside(repo_root, rel) and os.path.isfile(
                os.path.join(repo_root, rel)
            ):
                found.add(rel.replace(os.sep, "/"))
            parent = os.path.dirname(directory)
            if parent == directory:
                break
            directory = parent

    def proximity(rel: str) -> tuple[int, int, str]:
        directory = os.path.dirname(rel).replace(os.sep, "/")
        prefix = directory + "/"
        nearer = sum(1 for path in touched if path.startswith(prefix))
        depth = len([part for part in directory.split("/") if part])
        return (-nearer, -depth, rel)

    return sorted(found, key=proximity)[:MAX_NESTED_GUIDELINES]


def _truncate_guidelines(text: str, cap: int = MAX_GUIDELINES_CHARS) -> str:
    """Keep head and tail of one doctrine file, in at most ``cap`` characters.

    Head-only truncation drops the end of ``AGENTS.md``, which is where a
    repository tends to put the rules that matter most (Motus keeps its
    "Rules no agent may break" there), so the omitted middle is what is
    thrown away and both ends are kept. Applied per file so one oversized
    file cannot evict every file that follows it.

    The truncation marker counts against ``cap``, because the cap is prompt
    the model actually receives, not just the payload it was cut from. The
    marker reports its own omission count, so its length depends on the
    number it carries; the loop below solves that small fixed point before
    slicing, so the result is never longer than ``cap`` whenever ``cap`` is at
    least the marker's own length (every call site passes a cap near 40k).
    """
    if len(text) <= cap:
        return text
    template = "\n\n[guidelines truncated: {} chars omitted in the middle]\n\n"
    omitted = len(text) - cap
    for _ in range(8):
        marker = template.format(omitted)
        keep = cap - len(marker)
        new_omitted = len(text) - keep
        if new_omitted == omitted:
            break
        omitted = new_omitted
    keep = max(cap - len(marker), 0)
    head = keep // 2
    tail = keep - head
    return text[:head] + marker + text[len(text) - tail:]


def load_guidelines(repo_root: str, diff: str = "") -> str:
    """Assemble the repository's doctrine for the model.

    Every file that exists enters the doctrine under a ``## <path>`` heading,
    in the order ``GUIDELINE_FILENAMES`` declares and then the nested files the
    diff names. A file that is not there is simply not doctrine. Each file is
    truncated on its own so that its whole section (heading included) fits
    ``MAX_GUIDELINES_CHARS``, and once the combined size of the sections
    reaches ``MAX_TOTAL_GUIDELINES_CHARS`` a file that would not fit is
    omitted under a marker naming it; the diff is never shortened for this.

    The per-file cap covers the whole section, heading included, so two files
    at ``MAX_GUIDELINES_CHARS`` fit ``MAX_TOTAL_GUIDELINES_CHARS`` exactly
    rather than overrunning it by their headings. The separator between
    sections is likewise taken out of each file's allowance.

    All of it is read from ``repo_root``, which the workflow has checked out at
    the BASE revision: the pull request contributes a diff to read, not files
    to read from.
    """
    names: list[str] = []
    seen: set[str] = set()
    for name in list(GUIDELINE_FILENAMES) + nested_guideline_paths(repo_root, diff):
        if name not in seen:
            seen.add(name)
            names.append(name)

    separator = "\n\n"
    sections: list[str] = []
    total = 0
    for name in names:
        path = os.path.join(repo_root, name)
        if not os.path.isfile(path):
            continue
        with open(path, encoding="utf-8") as f:
            raw = f.read()
        prefix = f"## {name}\n\n"
        cap = MAX_GUIDELINES_CHARS - len(prefix) - len(separator)
        section = prefix + _truncate_guidelines(raw, max(cap, 0))
        extra = len(separator) if sections else 0
        if total + extra + len(section) > MAX_TOTAL_GUIDELINES_CHARS:
            sections.append(
                f"## {name}\n\n[guidelines omitted: {name} (total budget)]"
            )
            continue
        total += extra + len(section)
        sections.append(section)

    if not sections:
        return (
            "(no repository guidelines found at the repository root or next "
            "to the changed files; apply general code-review judgement.)"
        )
    return "\n\n".join(sections)


def build_payload(diff: str, model: str, guidelines: str) -> dict:
    truncated = len(diff) > MAX_DIFF_CHARS
    if truncated:
        diff = diff[:MAX_DIFF_CHARS]
    user_content = diff + ("\n\n[diff truncated for length]" if truncated else "")
    system_prompt = REVIEW_INSTRUCTIONS.format(guidelines=guidelines)
    return {
        "model": model,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_content},
        ],
    }


def call_openrouter(api_key: str, payload: dict, opener=None) -> str:
    """POST the payload and return the model's text.

    ``opener`` is the HTTP seam: it defaults to ``urllib.request.urlopen`` and
    exists so the tests can hand in a fake client instead of a network.
    """
    repository = os.environ.get("GITHUB_REPOSITORY", "openrouter-review")
    body = json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(
        OPENROUTER_URL,
        data=body,
        method="POST",
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
            "HTTP-Referer": f"https://github.com/{repository}",
            "X-Title": f"{repository} PR review",
        },
    )
    open_url = opener or urllib.request.urlopen
    try:
        with open_url(request, timeout=120) as response:
            data = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise SystemExit(f"OpenRouter request failed: {exc.code} {exc.reason}\n{detail}")
    except urllib.error.URLError as exc:
        raise SystemExit(f"OpenRouter request failed: {exc.reason}")

    try:
        return data["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError):
        raise SystemExit(f"Unexpected OpenRouter response shape: {json.dumps(data)[:2000]}")


def build_footer(model: str, commit: str) -> str:
    """The attribution block, carrying the marker and the reviewed commit.

    The marker is what lets the workflow find and PATCH a single existing
    review comment rather than post a new one on every push.
    """
    lines = [f"*Automated review via OpenRouter (`{model}`).*"]
    if commit:
        lines.append(f"Reviewed commit: {commit}")
    lines.append(REVIEW_MARKER)
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--diff", required=True, help="Path to a unified diff file")
    parser.add_argument("--out", required=True, help="Path to write the review markdown to")
    parser.add_argument("--repo-root", default=".", help="Repository root to read doctrine from")
    parser.add_argument(
        "--commit",
        default="",
        help="PR head commit the review is about, recorded in the footer",
    )
    args = parser.parse_args()

    api_key = os.environ.get("OPENROUTER_API_KEY")
    if not api_key:
        raise SystemExit("OPENROUTER_API_KEY is not set")
    # Founder, 14/09/2026: DeepSeek flash for tests, code and reviews — not GLM.
    # The repository variable OPENROUTER_MODEL still overrides this default.
    model = os.environ.get("OPENROUTER_MODEL") or "deepseek/deepseek-v4.1-flash"

    with open(args.diff, encoding="utf-8") as f:
        diff = f.read()

    if not diff.strip():
        review = "No diff content to review (empty diff)."
    else:
        guidelines = load_guidelines(args.repo_root, diff)
        payload = build_payload(diff, model, guidelines)
        review = call_openrouter(api_key, payload)

    body = review.strip() + "\n\n---\n" + build_footer(model, args.commit) + "\n"
    with open(args.out, "w", encoding="utf-8") as f:
        f.write(body)

    return 0


if __name__ == "__main__":
    sys.exit(main())
