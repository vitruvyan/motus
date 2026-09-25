"""Supplied ADR-040 lineage and scope claims stay bounded to exact inputs."""
from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

from vitruvyan_motus.contract import validate
from vitruvyan_motus.retention import (
    resolve_supplied_retention_scope,
    verify_retention_lineage,
)

ROOT = Path(__file__).resolve().parent.parent
KINDS = (
    "retention-policy-declaration", "legal-hold-declaration",
    "retention-scope-snapshot", "retention-trigger-occurrence",
    "retention-application", "custody-observation",
)
STABLE = ("policy_id", "hold_id", "snapshot_id", "occurrence_id",
          "application_id", "observation_id")


def fixture(kind: str) -> dict:
    number = 370 + KINDS.index(kind)
    return json.loads((ROOT / "contract" / "retention-fixtures" /
                       f"{number}-{kind}-valid.json").read_text(encoding="utf-8"))


def fingerprint(kind: str, document: dict) -> str:
    return getattr(validate, kind.replace("-", "_") + "_fingerprint")(document)


def child(kind: str, parent: dict) -> dict:
    document = copy.deepcopy(parent)
    document["supersedes"] = fingerprint(kind, parent)
    document["producer_ref"] = "reviser"
    if kind == "legal-hold-declaration":
        document["action"] = "amended"
    return document


@pytest.mark.parametrize("kind,stable", zip(KINDS, STABLE))
def test_each_stable_identity_links_one_immediate_predecessor(kind, stable):
    root = fixture(kind)
    descendant = child(kind, root)
    verdict = verify_retention_lineage(kind, [descendant, root])
    assert verdict.violations == ()
    assert verdict.ordered_fingerprints == (fingerprint(kind, root),
                                            fingerprint(kind, descendant))
    assert any(f.status == "matched" for f in verdict.findings)
    wrong = copy.deepcopy(descendant)
    wrong[stable] = "different-stable-id"
    verdict = verify_retention_lineage(kind, [root, wrong])
    assert any(f.status == "mismatched" for f in verdict.findings)


def test_same_stable_id_in_another_namespace_does_not_link():
    kind = KINDS[0]
    root = fixture(kind)
    descendant = child(kind, root)
    descendant["producer_namespace"] = "other/records"
    verdict = verify_retention_lineage(kind, [root, descendant])
    assert any(f.status == "mismatched" for f in verdict.findings)


def test_missing_wrong_kind_and_invalid_roots_never_pretend_to_link():
    kind = KINDS[0]
    root = fixture(kind)
    descendant = child(kind, root)
    assert any(f.status == "not_verified" for f in
               verify_retention_lineage(kind, [descendant]).findings)
    other_kind = fixture(KINDS[1])
    verdict = verify_retention_lineage(kind, [other_kind, descendant])
    assert verdict.violations
    assert any(f.status == "not_verified" for f in verdict.findings)
    invalid_root = copy.deepcopy(root)
    invalid_root["schema_version"] = "wrong"
    verdict = verify_retention_lineage(kind, [invalid_root, descendant])
    assert verdict.violations and len(verdict.ordered_fingerprints) == 1
    assert not any(f.status == "matched" for f in verdict.findings)


def test_self_fork_and_cycle_keep_every_branch_visible(monkeypatch):
    kind = KINDS[0]
    root = fixture(kind)
    self_ref = child(kind, root)
    # A synthetic fingerprint collision makes a self-reference constructible.
    monkeypatch.setattr(validate, "retention_policy_declaration_fingerprint",
                        lambda document: document.get("supersedes", "sha256:" + "1" * 64))
    verdict = verify_retention_lineage(kind, [self_ref])
    assert any(f.status == "conflict" and "own" in f.reason for f in verdict.findings)
    monkeypatch.undo()

    first = child(kind, root)
    second = child(kind, root)
    second["producer_ref"] = "another-reviser"
    verdict = verify_retention_lineage(kind, [second, first, root])
    assert len(verdict.ordered_fingerprints) == 3
    assert verdict.ordered_fingerprints[0] == fingerprint(kind, root)
    assert any(f.status == "conflict" and "branches" in f.reason for f in verdict.findings)

    a = fixture(kind)
    b = copy.deepcopy(a)
    a["supersedes"] = "sha256:" + "a" * 64
    b["supersedes"] = "sha256:" + "b" * 64
    b["producer_ref"] = "other"
    monkeypatch.setattr(validate, "retention_policy_declaration_fingerprint",
                        lambda document: ("sha256:" + "a" * 64) if
                        document["producer_ref"] == "other" else "sha256:" + "b" * 64)
    verdict = verify_retention_lineage(kind, [a, b])
    assert any(f.path == "lineage:cycle" and f.status == "conflict"
               for f in verdict.findings)
    assert len(verdict.ordered_fingerprints) == 2


def test_digest_collision_is_reported_and_not_used_as_a_predecessor(monkeypatch):
    kind = KINDS[0]
    root = fixture(kind)
    sibling = copy.deepcopy(root)
    sibling["producer_ref"] = "other"
    descendant = child(kind, root)
    descendant["supersedes"] = "sha256:" + "c" * 64
    monkeypatch.setattr(validate, "retention_policy_declaration_fingerprint",
                        lambda document: "sha256:" + "c" * 64 if
                        "supersedes" not in document else "sha256:" + "d" * 64)
    verdict = verify_retention_lineage(kind, [root, sibling, descendant])
    assert any(f.status == "conflict" and "ambiguous" in f.reason
               for f in verdict.findings)
    assert len(verdict.ordered_fingerprints) == 3


def test_permutation_and_reversed_timestamps_do_not_choose_a_branch():
    kind = KINDS[0]
    root = fixture(kind)
    one = child(kind, root)
    two = child(kind, root)
    one["declared_at"] = "2027-01-01T00:00:00Z"
    two["declared_at"] = "2025-01-01T00:00:00Z"
    two["producer_ref"] = "other"
    left = verify_retention_lineage(kind, [root, one, two])
    right = verify_retention_lineage(kind, [two, root, one])
    assert left.ordered_fingerprints == right.ordered_fingerprints
    assert left.ordered_fingerprints[1:] == tuple(sorted((fingerprint(kind, one),
                                                           fingerprint(kind, two))))
    assert left.findings == right.findings
    assert any(f.status == "conflict" for f in left.findings)


def test_outputs_are_detached_from_mutable_inputs():
    kind = KINDS[0]
    root = fixture(kind)
    verdict = verify_retention_lineage(kind, [root])
    original = verdict.ordered_fingerprints
    root["policy_id"] = "changed"
    assert verdict.ordered_fingerprints == original
    declaration = fixture(kind)
    result = resolve_supplied_retention_scope(declaration)
    original_artifacts = result.artifacts
    declaration["scope"]["artifacts"][0]["kind"] = "trace"
    assert result.artifacts == original_artifacts


def test_exact_scope_needs_no_snapshot_and_returns_typed_identity():
    declaration = fixture(KINDS[0])
    result = resolve_supplied_retention_scope(declaration)
    assert result.artifacts[0].kind == "receipt"
    assert result.artifacts[0].fingerprint == declaration["scope"]["artifacts"][0]["fingerprint"]
    assert result.snapshot_fingerprint is None


@pytest.mark.parametrize("selector", ["execution_refs", "tenant_writer"])
def test_selector_without_snapshot_reports_missing_binding(selector):
    declaration = fixture(KINDS[0])
    declaration["scope"] = ({"kind": "execution_refs", "execution_refs": ["tenant/writer/1"]}
                            if selector == "execution_refs" else
                            {"kind": "tenant_writer", "tenant": "tenant"})
    result = resolve_supplied_retention_scope(declaration)
    assert result.artifacts == ()
    assert any(f.status == "missing" for f in result.findings)


def test_snapshot_requires_exact_source_kind_fingerprint_and_namespace():
    declaration = fixture(KINDS[0])
    declaration["scope"] = {"kind": "tenant_writer", "tenant": "tenant"}
    scope = fixture(KINDS[2])
    scope["source"] = {"kind": "retention_policy_declaration",
                       "fingerprint": fingerprint(KINDS[0], declaration)}
    scope["artifacts"] = [{"kind": "receipt", "fingerprint": "sha256:" + "f" * 64}]
    result = resolve_supplied_retention_scope(declaration, snapshot=scope)
    assert len(result.artifacts) == 1
    assert result.findings[0].status == "matched"
    assert "completeness and custody are unverified" in result.findings[0].reason
    for field, change in (("source_kind", "legal_hold_declaration"),
                          ("source_fingerprint", "sha256:" + "e" * 64),
                          ("producer_namespace", "another/namespace")):
        wrong = copy.deepcopy(scope)
        if field.startswith("source_"):
            wrong["source"][field.removeprefix("source_")] = change
        else:
            wrong[field] = change
        result = resolve_supplied_retention_scope(declaration, snapshot=wrong)
        assert result.artifacts == ()
        assert any(f.status == "mismatched" for f in result.findings)


def test_empty_snapshot_and_later_artifact_are_not_silently_expanded():
    declaration = fixture(KINDS[0])
    declaration["scope"] = {"kind": "tenant_writer", "tenant": "tenant"}
    scope = fixture(KINDS[2])
    scope["source"] = {"kind": "retention_policy_declaration",
                       "fingerprint": fingerprint(KINDS[0], declaration)}
    result = resolve_supplied_retention_scope(declaration, snapshot=scope)
    assert result.artifacts == ()
    assert result.findings[0].status == "matched"
    later_artifact = {"kind": "receipt", "fingerprint": "sha256:" + "f" * 64}
    assert later_artifact not in scope["artifacts"]
    assert (later_artifact["kind"], later_artifact["fingerprint"]) not in {
        (item.kind, item.fingerprint) for item in result.artifacts
    }
    assert all(not hasattr(result, name) for name in
               ("clear", "eligible", "disposable", "custody_complete"))


def test_invalid_snapshot_has_violations_and_no_identities():
    declaration = fixture(KINDS[0])
    declaration["scope"] = {"kind": "tenant_writer", "tenant": "tenant"}
    scope = fixture(KINDS[2])
    scope["source"]["kind"] = "receipt"
    result = resolve_supplied_retention_scope(declaration, snapshot=scope)
    assert result.snapshot_violations and result.artifacts == ()
    assert result.findings[0].status == "not_verified"
