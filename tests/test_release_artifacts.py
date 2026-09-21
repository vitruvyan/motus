import json
from pathlib import Path

import pytest

from tools.release_artifacts import (
    compare_distributions,
    manifest,
    pypi_verdict,
    verify_tag,
)

REPO_ROOT = Path(__file__).resolve().parent.parent


def test_the_accepted_motus_publication_path_is_committed():
    """ADR-033 names these artifacts; a release must not invent them later."""
    assert (REPO_ROOT / ".github" / "workflows" / "publish-motus.yml").is_file()
    assert (REPO_ROOT / "constraints" / "release-build.txt").is_file()
    assert (REPO_ROOT / "docs" / "releases" / "v0.15.0.md").is_file()


def _dist(directory: Path, wheel: bytes = b"wheel", sdist: bytes = b"sdist") -> Path:
    directory.mkdir()
    (directory / "vitruvyan_motus-0.15.0-py3-none-any.whl").write_bytes(wheel)
    (directory / "vitruvyan_motus-0.15.0.tar.gz").write_bytes(sdist)
    return directory


def test_release_assets_must_be_byte_identical(tmp_path):
    carried = _dist(tmp_path / "carried")
    released = _dist(tmp_path / "released")
    compare_distributions(carried, released)

    (released / "vitruvyan_motus-0.15.0.tar.gz").write_bytes(b"swapped")
    with pytest.raises(ValueError, match="differ"):
        compare_distributions(carried, released)


def test_pypi_retries_are_only_idempotent_for_the_same_bytes(tmp_path):
    local = manifest(_dist(tmp_path / "dist"))
    remote = {
        "urls": [
            {"filename": item["filename"], "digests": {"sha256": item["sha256"]}}
            for item in local["files"]
        ]
    }
    assert pypi_verdict(local, None) == "publish"
    assert pypi_verdict(local, remote) == "already-published"
    remote["urls"][0]["digests"]["sha256"] = "0" * 64
    with pytest.raises(ValueError, match="different or incomplete"):
        pypi_verdict(local, remote)


def test_release_tag_is_coupled_to_version_and_evidence(tmp_path):
    repo = tmp_path
    package = repo / "src" / "vitruvyan_motus"
    package.mkdir(parents=True)
    (package / "__init__.py").write_text('__version__ = "0.15.0"\n', encoding="utf-8")
    (repo / "benchmarks" / "relative-0.15.0").mkdir(parents=True)
    (repo / "benchmarks" / "candidate-v0.15.0-epyc-py310.json").write_text(
        json.dumps({}), encoding="utf-8"
    )
    (repo / "docs" / "releases").mkdir(parents=True)
    (repo / "docs" / "releases" / "v0.15.0.md").write_text("notes", encoding="utf-8")

    assert verify_tag(repo, "v0.15.0") == "0.15.0"
    with pytest.raises(ValueError, match="does not exactly match"):
        verify_tag(repo, "v0.15")
