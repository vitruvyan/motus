"""Reject changes to the frozen contract corpora.

This program is intentionally stdlib-only.  In CI it is executed from the
base commit by the Jenkins trusted frozen-contract stage; pull-request code
cannot weaken the check that judges that same pull request.
"""

from __future__ import annotations

import argparse
import hashlib
import subprocess
import sys
from pathlib import PurePosixPath


FROZEN_ROOTS = (
    PurePosixPath("tests/contract"),
    PurePosixPath("tests/compat"),
)
KERNEL_PATH = PurePosixPath("tests/contract/kernel.py")
# One-time Axis -> Motus compatibility switch approved by ADR-003.  Future
# edits to the adapter binding are frozen just like every assertion around it.
APPROVED_KERNEL_SHA256 = (
    "113dbc24fafd35dbf24d92eae88f26692a70df59801b8c733cebe6c254e56ec8"
)


def normalize_path(raw_path: str) -> PurePosixPath:
    """Return a repository-relative path with platform-neutral separators."""
    return PurePosixPath(raw_path.replace("\\", "/").lstrip("./"))


def is_frozen_path(raw_path: str) -> bool:
    """Whether ``raw_path`` belongs to a frozen corpus."""
    path = normalize_path(raw_path)
    return any(path == root or root in path.parents for root in FROZEN_ROOTS)


def approved_kernel_switch(head: str) -> bool:
    """Whether ``head`` contains the exact founder-approved adapter switch."""
    completed = subprocess.run(
        ["git", "show", f"{head}:{KERNEL_PATH.as_posix()}"],
        check=False,
        capture_output=True,
    )
    return (
        completed.returncode == 0
        and hashlib.sha256(completed.stdout).hexdigest() == APPROVED_KERNEL_SHA256
    )


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

    frozen = []
    for path in changed_paths(args.base, args.head):
        normalized = normalize_path(path)
        if normalized == KERNEL_PATH and approved_kernel_switch(args.head):
            continue
        if is_frozen_path(path):
            frozen.append(path)
    frozen.sort()
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
        "The one-time tests/contract/kernel.py import move is accepted only at "
        "its founder-approved digest. Any other change requires a prior, "
        "founder-approved contract amendment.",
        file=sys.stderr,
    )
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
