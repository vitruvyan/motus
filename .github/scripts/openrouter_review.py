#!/usr/bin/env python3
"""Ask an OpenRouter-hosted model to review a pull request diff.

Standalone (stdlib only, no new dependency for this repo's zero-dependency
guarantee to worry about) so it can run in CI with nothing but the pinned
Python interpreter.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.request

OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"

# Trimmed from AGENTS.md so the reviewer applies this repository's actual
# review doctrine instead of a generic checklist.
SYSTEM_PROMPT = """\
You are reviewing a pull request against Vitruvyan Motus, a contract-first \
Python runtime. Apply these rules, which are non-negotiable in this repository:

- Authority order: ADR-001 -> contract/ -> frozen corpora (tests/contract/, \
tests/compat/) -> implementation. When implementation and contract disagree, \
the implementation is wrong.
- tests/contract/ and tests/compat/ must never be edited by a PR. Flag it hard \
if the diff touches them.
- No assertion may be weakened, skipped, or deleted to make something pass.
- A finding names an instance; the fix should repair the class. If a diff \
fixes one occurrence of a bug shape, check whether the same shape exists \
elsewhere in the changed files and say so if it does.
- Regular expressions must not be used to parse structured text (JSON, source \
code, etc.) that already has a real parser available; a regex over an opaque \
token with a grammar the repo owns (a hash, an identifier shape) is fine.
- No new runtime dependencies may be introduced; the package declares zero.
- Every behavior fix should carry a test that would fail without it.
- Prefer flagging real correctness bugs, contract violations, and missing \
tests over style opinions.

Write a concise code review of the diff below. Structure it as:
1. A one-line verdict (approve / request changes / comment).
2. Findings, most severe first, each with file:line, what is wrong, and why \
it matters. Skip this section if there is nothing worth flagging.
3. Anything the diff does well, in one line, if genuinely notable. Skip \
if not.

Keep it terse. Do not restate the whole diff back. If the diff is truncated, \
say so and review only what you can see.
"""

MAX_DIFF_CHARS = 120_000


def build_payload(diff: str, model: str) -> dict:
    truncated = len(diff) > MAX_DIFF_CHARS
    if truncated:
        diff = diff[:MAX_DIFF_CHARS]
    user_content = diff + ("\n\n[diff truncated for length]" if truncated else "")
    return {
        "model": model,
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": user_content},
        ],
    }


def call_openrouter(api_key: str, payload: dict) -> str:
    body = json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(
        OPENROUTER_URL,
        data=body,
        method="POST",
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
            "HTTP-Referer": "https://github.com/vitruvyan/motus",
            "X-Title": "Motus PR review",
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
        payload = build_payload(diff, model)
        review = call_openrouter(api_key, payload)

    footer = f"\n\n---\n*Automated review via OpenRouter (`{model}`).*\n"
    with open(args.out, "w", encoding="utf-8") as f:
        f.write(review.strip() + footer)

    return 0


if __name__ == "__main__":
    sys.exit(main())
