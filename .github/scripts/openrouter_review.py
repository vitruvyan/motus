#!/usr/bin/env python3
"""Ask an OpenRouter-hosted model to review a pull request diff.

Standalone (stdlib only) so it runs in CI with nothing but the interpreter.
Repository-agnostic by design: it reads the repo's own AGENTS.md/CLAUDE.md at
runtime and hands it to the model as review doctrine, instead of encoding one
project's rules here. That means the same script is reused, unmodified,
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

GUIDELINE_FILENAMES = ("AGENTS.md", "CLAUDE.md")
MAX_GUIDELINES_CHARS = 20_000
MAX_DIFF_CHARS = 120_000

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


def load_guidelines(repo_root: str) -> str:
    for name in GUIDELINE_FILENAMES:
        path = os.path.join(repo_root, name)
        if os.path.isfile(path):
            with open(path, encoding="utf-8") as f:
                text = f.read()
            if len(text) > MAX_GUIDELINES_CHARS:
                text = text[:MAX_GUIDELINES_CHARS] + "\n[guidelines truncated for length]"
            return text
    return "(no AGENTS.md or CLAUDE.md found at the repository root; apply general code-review judgement.)"


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


def call_openrouter(api_key: str, payload: dict) -> str:
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
    try:
        with urllib.request.urlopen(request, timeout=120) as response:
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


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--diff", required=True, help="Path to a unified diff file")
    parser.add_argument("--out", required=True, help="Path to write the review markdown to")
    parser.add_argument("--repo-root", default=".", help="Repository root to read AGENTS.md/CLAUDE.md from")
    args = parser.parse_args()

    api_key = os.environ.get("OPENROUTER_API_KEY")
    if not api_key:
        raise SystemExit("OPENROUTER_API_KEY is not set")
    model = os.environ.get("OPENROUTER_MODEL") or "z-ai/glm-5.3-flash"

    with open(args.diff, encoding="utf-8") as f:
        diff = f.read()

    if not diff.strip():
        review = "No diff content to review (empty diff)."
    else:
        guidelines = load_guidelines(args.repo_root)
        payload = build_payload(diff, model, guidelines)
        review = call_openrouter(api_key, payload)

    footer = f"\n\n---\n*Automated review via OpenRouter (`{model}`).*\n"
    with open(args.out, "w", encoding="utf-8") as f:
        f.write(review.strip() + footer)

    return 0


if __name__ == "__main__":
    sys.exit(main())
