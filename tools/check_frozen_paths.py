"""Reject changes to the frozen contract corpora.

This program is intentionally stdlib-only.  In CI it is executed from the
base branch by a ``pull_request_target`` workflow; code in the pull request
cannot weaken the check that judges that same pull request.
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import PurePosixPath


FROZEN_ROOTS = (
    PurePosixPath("tests/contract"),
    PurePosixPath("tests/compat"),
)
EDITABLE_PATHS = {
    PurePosixPath("tests/contract/kernel.py"),
}


def normalize_path(raw_path: str) -> PurePosixPath:
    """Return a repository-relative path with platform-neutral separators."""
    return PurePosixPath(raw_path.replace("\\", "/").lstrip("./"))


def is_frozen_path(raw_path: str) -> bool:
    """Whether ``raw_path`` belongs to a frozen corpus and is not exempt."""
    path = normalize_path(raw_path)
    if path in EDITABLE_PATHS:
        return False
    return any(path == root or root in path.parents for root in FROZEN_ROOTS)


def changed_paths(base: str, head: str) -> list[str]:
    """List paths changed between two exact Git objects."""
    command = [
        "git",
        "diff",
        "--name-only",
        "--no-renames",
        "--diff-filter=ACDMRTUXB",
        "-z",
        base,
        head,
        "--",
    ]
    completed = subprocess.run(command, check=True, capture_output=True)
    return [
        item.decode("utf-8")
        for item in completed.stdout.split(b"\0")
        if item
    ]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Fail when a diff edits the Motus frozen contract corpora."
    )
    parser.add_argument("base", help="base commit SHA")
    parser.add_argument("head", help="head commit SHA")
    args = parser.parse_args(argv)

    frozen = sorted(path for path in changed_paths(args.base, args.head) if is_frozen_path(path))
    if not frozen:
        print("Frozen contract paths: PASS")
        return 0

    print("Frozen contract paths: FAIL", file=sys.stderr)
    print(
        "The following files are contract evidence and cannot be edited by an "
        "implementation PR:",
        file=sys.stderr,
    )
    for path in frozen:
        print(f"  - {path}", file=sys.stderr)
    print(
        "Only tests/contract/kernel.py may change for the one-time import move. "
        "Any other change requires a prior, founder-approved contract amendment.",
        file=sys.stderr,
    )
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
