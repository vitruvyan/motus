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
    """
    paths: list[str] = []
    for line in diff.splitlines():
        for prefix in ("+++ b/", "--- a/"):
            if line.startswith(prefix):
                path = line[len(prefix):].split("\t", 1)[0].strip()
                if path and path != "/dev/null":
                    paths.append(path)
    return paths


def nested_guideline_paths(repo_root: str, diff: str) -> list[str]:
    """``AGENTS.md`` files in directories the diff touched, at most five.

    ``AGENTS.md`` is a tree convention: a component may carry its own rules
    under its directory, and a change to that directory is reviewed against
    them too. Every ancestor directory of every touched path is checked, the
    result is deduplicated and sorted so the same diff always yields the same
    doctrine, and it is capped at ``MAX_NESTED_GUIDELINES``.
    """
    found: set[str] = set()
    for path in diff_paths(diff):
        directory = os.path.dirname(path)
        while directory:
            rel = os.path.join(directory, "AGENTS.md")
            if os.path.isfile(os.path.join(repo_root, rel)):
                found.add(rel.replace(os.sep, "/"))
            parent = os.path.dirname(directory)
            if parent == directory:
                break
            directory = parent
    return sorted(found)[:MAX_NESTED_GUIDELINES]


def _truncate_guidelines(text: str) -> str:
    """Keep head and tail of one doctrine file.

    Head-only truncation drops the end of ``AGENTS.md``, which is where a
    repository tends to put the rules that matter most (Motus keeps its
    "Rules no agent may break" there), so the omitted middle is what is
    thrown away and both ends are kept. Applied per file so one oversized
    file cannot evict every file that follows it.
    """
    if len(text) <= MAX_GUIDELINES_CHARS:
        return text
    head = MAX_GUIDELINES_CHARS // 2
    tail = MAX_GUIDELINES_CHARS - head
    omitted = len(text) - head - tail
    marker = f"\n\n[guidelines truncated: {omitted} chars omitted in the middle]\n\n"
    return text[:head] + marker + text[len(text) - tail:]


def load_guidelines(repo_root: str, diff: str = "") -> str:
    """Assemble the repository's doctrine for the model.

    Every file that exists enters the doctrine under a ``## <path>`` heading,
    in the order ``GUIDELINE_FILENAMES`` declares and then the nested files the
    diff names. A file that is not there is simply not doctrine.
    """
    names: list[str] = []
    seen: set[str] = set()
    for name in list(GUIDELINE_FILENAMES) + nested_guideline_paths(repo_root, diff):
        if name not in seen:
            seen.add(name)
            names.append(name)

    sections: list[str] = []
    for name in names:
        path = os.path.join(repo_root, name)
        if not os.path.isfile(path):
            continue
        with open(path, encoding="utf-8") as f:
            text = _truncate_guidelines(f.read())
        sections.append(f"## {name}\n\n{text}")

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