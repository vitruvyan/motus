"""Release-artifact checks for ADR-032/ADR-033.

The workflow builds once.  These helpers bind that build to the tag and
committed evidence, record its hashes, compare the authenticated workflow
artifact with the editable GitHub Release assets, and refuse a conflicting
PyPI version before Trusted Publishing is allowed to run.
"""

from __future__ import annotations

import argparse
import ast
import hashlib
import json
import os
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path


PROJECT = "vitruvyan-motus"


def distribution_files(directory: Path) -> list[Path]:
    files = sorted(
        path for path in directory.iterdir()
        if path.is_file() and (path.suffix == ".whl" or path.name.endswith(".tar.gz"))
    )
    wheels = [path for path in files if path.suffix == ".whl"]
    sdists = [path for path in files if path.name.endswith(".tar.gz")]
    if len(wheels) != 1 or len(sdists) != 1 or len(files) != 2:
        raise ValueError(
            f"{directory}: expected exactly one wheel and one sdist, found "
            f"{[path.name for path in files]}"
        )
    return files


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def manifest(directory: Path) -> dict[str, object]:
    return {
        "algorithm": "sha256",
        "files": [
            {"filename": path.name, "sha256": sha256(path), "size": path.stat().st_size}
            for path in distribution_files(directory)
        ],
    }


def compare_distributions(authenticated: Path, release_assets: Path) -> None:
    expected = manifest(authenticated)
    actual = manifest(release_assets)
    if expected != actual:
        raise ValueError(
            "GitHub Release assets differ from the authenticated workflow artifact\n"
            f"authenticated={json.dumps(expected, sort_keys=True)}\n"
            f"release={json.dumps(actual, sort_keys=True)}"
        )


def read_version(repo: Path) -> str:
    source = (repo / "src" / "vitruvyan_motus" / "__init__.py").read_text(
        encoding="utf-8"
    )
    module = ast.parse(source)
    for statement in module.body:
        if (
            isinstance(statement, ast.Assign)
            and any(isinstance(target, ast.Name) and target.id == "__version__"
                    for target in statement.targets)
        ):
            value = ast.literal_eval(statement.value)
            if isinstance(value, str):
                return value
    raise ValueError("src/vitruvyan_motus/__init__.py has no literal __version__")


def verify_tag(repo: Path, tag: str) -> str:
    version = read_version(repo)
    if tag != f"v{version}":
        raise ValueError(f"tag {tag!r} does not exactly match runtime version {version!r}")
    required = (
        repo / "benchmarks" / f"relative-{version}",
        repo / "benchmarks" / f"candidate-v{version}-epyc-py310.json",
        repo / "docs" / "releases" / f"v{version}.md",
    )
    missing = [str(path.relative_to(repo)) for path in required if not path.exists()]
    if missing:
        raise ValueError(f"release evidence is incomplete: {missing}")
    return version


def pypi_verdict(local: dict[str, object], remote: dict[str, object] | None) -> str:
    """Return ``publish`` or ``already-published``; raise on any byte conflict."""
    if remote is None:
        return "publish"
    remote_files = {
        item["filename"]: item["digests"]["sha256"]
        for item in remote.get("urls", [])
    }
    local_files = {
        item["filename"]: item["sha256"]
        for item in local["files"]
    }
    if remote_files == local_files:
        return "already-published"
    raise ValueError(
        "PyPI already contains different or incomplete files for this version\n"
        f"local={json.dumps(local_files, sort_keys=True)}\n"
        f"pypi={json.dumps(remote_files, sort_keys=True)}"
    )


def fetch_pypi_version(project: str, version: str) -> dict[str, object] | None:
    url = f"https://pypi.org/pypi/{project}/{version}/json"
    try:
        with urllib.request.urlopen(url, timeout=30) as response:
            return json.load(response)
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            return None
        raise


def _write_github_output(key: str, value: str) -> None:
    output = os.environ.get("GITHUB_OUTPUT")
    if output:
        with Path(output).open("a", encoding="utf-8") as destination:
            destination.write(f"{key}={value}\n")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    commands = parser.add_subparsers(dest="command", required=True)

    verify = commands.add_parser("verify-tag")
    verify.add_argument("tag")
    verify.add_argument("--repo", type=Path, default=Path.cwd())

    write = commands.add_parser("write-manifest")
    write.add_argument("directory", type=Path)
    write.add_argument("output", type=Path)

    markdown = commands.add_parser("markdown")
    markdown.add_argument("manifest", type=Path)

    compare = commands.add_parser("compare")
    compare.add_argument("authenticated", type=Path)
    compare.add_argument("release_assets", type=Path)

    pypi = commands.add_parser("check-pypi")
    pypi.add_argument("directory", type=Path)
    pypi.add_argument("version")
    pypi.add_argument("--expect-present", action="store_true")
    pypi.add_argument("--attempts", type=int, default=1)

    args = parser.parse_args(argv)
    try:
        if args.command == "verify-tag":
            version = verify_tag(args.repo, args.tag)
            print(f"release tag/evidence: PASS ({version})")
        elif args.command == "write-manifest":
            args.output.write_text(
                json.dumps(manifest(args.directory), indent=2, sort_keys=True) + "\n",
                encoding="utf-8",
            )
        elif args.command == "markdown":
            document = json.loads(args.manifest.read_text(encoding="utf-8"))
            for item in document["files"]:
                print(f"- `{item['filename']}`: `sha256:{item['sha256']}`")
        elif args.command == "compare":
            compare_distributions(args.authenticated, args.release_assets)
            print("release assets: PASS (byte-identical to workflow artifact)")
        elif args.command == "check-pypi":
            local = manifest(args.directory)
            verdict = "publish"
            for attempt in range(args.attempts):
                verdict = pypi_verdict(local, fetch_pypi_version(PROJECT, args.version))
                if verdict == "already-published" or not args.expect_present:
                    break
                if attempt + 1 < args.attempts:
                    time.sleep(10)
            if args.expect_present and verdict != "already-published":
                raise ValueError("PyPI did not expose the uploaded files before timeout")
            _write_github_output("publish", "true" if verdict == "publish" else "false")
            print(f"PyPI state: {verdict}")
    except (OSError, ValueError, urllib.error.URLError) as exc:
        print(f"release artifact check: FAIL: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
