"""The additive ADR-040 read-only helpers are part of the native surface."""
from __future__ import annotations

from dataclasses import FrozenInstanceError, is_dataclass
from pathlib import Path

import pytest

import vitruvyan_motus as motus
from vitruvyan_motus import retention


PUBLIC = (
    "RetentionFinding",
    "RetentionArtifactIdentity",
    "RetentionLineageVerdict",
    "RetentionScopeVerdict",
    "RetentionApplicationBindingVerdict",
    "RetentionBlockerVerdict",
    "verify_retention_lineage",
    "resolve_supplied_retention_scope",
    "verify_retention_application_bindings",
    "evaluate_supplied_retention_blocker",
)


def test_every_retention_symbol_is_exported_from_the_native_package():
    for name in PUBLIC:
        assert name in motus.__all__
        assert getattr(motus, name) is getattr(retention, name)
    assert len(motus.__all__) == len(set(motus.__all__))


def test_readme_public_surface_names_every_exported_retention_symbol():
    readme = (Path(__file__).resolve().parent.parent / "README.md").read_text(
        encoding="utf-8"
    )
    listing = readme.split("## Native package surface", 1)[1].split(
        "### System Manifest binding verification", 1
    )[0]
    for name in PUBLIC:
        assert f"`{name}`" in listing


def test_public_retention_results_are_frozen_and_have_no_disposal_permission():
    for name in PUBLIC[:6]:
        assert is_dataclass(getattr(motus, name))
        assert getattr(motus, name).__dataclass_params__.frozen
    finding = motus.RetentionFinding(
        "$.scope", "matched", "sha256:" + "a" * 64,
        "sha256:" + "a" * 64, "exact identity compared",
    )
    identity = motus.RetentionArtifactIdentity(
        "receipt", "sha256:" + "a" * 64,
    )
    with pytest.raises(FrozenInstanceError):
        finding.status = "mismatched"
    with pytest.raises(FrozenInstanceError):
        identity.fingerprint = "sha256:" + "b" * 64
    for value in (finding, identity):
        assert all(not hasattr(value, name) for name in
                   ("clear", "eligible", "disposable"))
