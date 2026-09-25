"""ADR-040 exact supplied bindings and subset-scoped hold findings."""
from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

from vitruvyan_motus.contract import validate
from vitruvyan_motus.retention import (
    evaluate_supplied_retention_blocker,
    verify_retention_application_bindings,
)

ROOT = Path(__file__).resolve().parent.parent
ARTIFACT = {"kind": "receipt", "fingerprint": "sha256:" + "a" * 64}


def fixture(number: int, kind: str) -> dict:
    path = ROOT / "contract" / "retention-fixtures" / f"{number}-{kind}-valid.json"
    return json.loads(path.read_text(encoding="utf-8"))


def policy() -> dict:
    return fixture(370, "retention-policy-declaration")


def hold() -> dict:
    document = fixture(371, "legal-hold-declaration")
    document["scope"] = {"kind": "exact_artifacts", "artifacts": [ARTIFACT]}
    return document


def application() -> dict:
    document = fixture(374, "retention-application")
    document["policy_fingerprint"] = validate.retention_policy_declaration_fingerprint(policy())
    return document


def snapshot_for(document: dict, kind: str) -> dict:
    snapshot = fixture(372, "retention-scope-snapshot")
    snapshot["source"] = {
        "kind": kind,
        "fingerprint": (
            validate.legal_hold_declaration_fingerprint(document)
            if kind == "legal_hold_declaration" else
            validate.retention_policy_declaration_fingerprint(document)
        ),
    }
    snapshot["artifacts"] = [ARTIFACT]
    return snapshot


def test_application_binds_exact_policy_hold_and_snapshot_revisions():
    current_policy = policy()
    current_hold = hold()
    current_snapshot = snapshot_for(current_hold, "legal_hold_declaration")
    app = application()
    app["hold_fingerprints"] = [validate.legal_hold_declaration_fingerprint(current_hold)]
    app["scope_snapshot_fingerprints"] = [
        validate.retention_scope_snapshot_fingerprint(current_snapshot)
    ]
    result = verify_retention_application_bindings(
        app, policy=current_policy, holds=[current_hold], snapshots=[current_snapshot],
    )
    assert not result.application_violations and not result.supplied_violations
    assert result.application_fingerprint == validate.retention_application_fingerprint(app)
    assert result.findings and all(item.status == "matched" for item in result.findings)
    current_policy["producer_ref"] = "mutated-after-call"
    assert result.findings and all(item.status == "matched" for item in result.findings)


def test_application_distinguishes_missing_mismatched_and_invalid_references():
    app = application()
    missing = verify_retention_application_bindings(app)
    assert any(item.path == "$.policy_fingerprint" and item.status == "missing"
               for item in missing.findings)
    other = policy()
    other["producer_ref"] = "other"
    mismatch = verify_retention_application_bindings(app, policy=other)
    assert any(item.path == "$.policy_fingerprint" and item.status == "mismatched"
               for item in mismatch.findings)
    invalid = policy()
    invalid["schema_version"] = "2.0.0"
    unverified = verify_retention_application_bindings(app, policy=invalid)
    assert unverified.supplied_violations
    assert any(item.path == "$.policy_fingerprint" and item.status == "not_verified"
               for item in unverified.findings)


def test_application_refuses_wrong_snapshot_source_and_unreferenced_hold_claim():
    current_policy = policy()
    current_hold = hold()
    current_snapshot = snapshot_for(current_hold, "legal_hold_declaration")
    app = application()
    app["scope_snapshot_fingerprints"] = [
        validate.retention_scope_snapshot_fingerprint(current_snapshot)
    ]
    result = verify_retention_application_bindings(
        app, policy=current_policy, holds=[current_hold], snapshots=[current_snapshot],
    )
    assert any(item.path.startswith("scope:") and item.status == "not_verified"
               for item in result.findings)
    assert any(item.path == "$.hold_fingerprints" and item.status == "not_verified"
               for item in result.findings)


def test_application_checks_each_hold_and_snapshot_digest_independently():
    app = application()
    app["hold_fingerprints"] = ["sha256:" + "d" * 64]
    app["scope_snapshot_fingerprints"] = ["sha256:" + "e" * 64]
    result = verify_retention_application_bindings(
        app, policy=policy(), holds=[hold()],
        snapshots=[snapshot_for(hold(), "legal_hold_declaration")],
    )
    assert any(item.path == "$.hold_fingerprints[0]" and item.status == "mismatched"
               for item in result.findings)
    assert any(item.path == "$.scope_snapshot_fingerprints[0]" and
               item.status == "mismatched" for item in result.findings)


def test_invalid_application_has_no_derived_binding_identity():
    app = application()
    app["schema_version"] = "wrong"
    result = verify_retention_application_bindings(app, policy=policy())
    assert result.application_fingerprint is None
    assert result.application_violations
    assert result.findings[0].status == "not_verified"


def test_matching_placed_hold_is_only_a_supplied_producer_blocker():
    result = evaluate_supplied_retention_blocker(ARTIFACT, holds=[hold()])
    assert result.status == "blocked_by_supplied_hold"
    assert any(item.status == "matched" and "producer" in item.reason
               for item in result.findings)
    assert all(not hasattr(result, name) for name in
               ("clear", "eligible", "disposable", "custody_verified"))


def test_redundant_source_bound_snapshot_does_not_erase_direct_hold_membership():
    placed = hold()
    redundant = snapshot_for(placed, "legal_hold_declaration")
    result = evaluate_supplied_retention_blocker(
        ARTIFACT, holds=[placed], snapshots=[redundant],
    )
    assert result.status == "blocked_by_supplied_hold"
    assert any(item.status == "mismatched" and "snapshot" in item.path
               for item in result.findings)
    assert any(item.status == "matched" and item.path.endswith(".artifact")
               for item in result.findings)


def test_independent_missing_selector_and_release_do_not_erase_proven_blocker():
    placed = hold()
    unrelated_selector = fixture(371, "legal-hold-declaration")
    unrelated_selector["hold_id"] = "H-unrelated-selector"
    unrelated_release = copy.deepcopy(unrelated_selector)
    unrelated_release["hold_id"] = "H-unrelated-release"
    unrelated_release["action"] = "released"
    unrelated_release["supersedes"] = "sha256:" + "f" * 64
    unrelated_release["rationale"] = "Producer claims a release."
    del unrelated_release["scope"]
    left = evaluate_supplied_retention_blocker(
        ARTIFACT, holds=[placed, unrelated_selector, unrelated_release],
    )
    right = evaluate_supplied_retention_blocker(
        ARTIFACT, holds=[unrelated_release, unrelated_selector, placed],
    )
    assert left.status == right.status == "blocked_by_supplied_hold"
    assert left.findings == right.findings
    assert any(item.status == "missing" for item in left.findings)
    assert any(item.status == "not_verified" and "release" in item.reason
               for item in left.findings)


def test_invalid_unrelated_record_remains_visible_with_a_matching_hold():
    bad = hold()
    bad["hold_id"] = "H-unrelated-invalid"
    bad["schema_version"] = "invalid"
    result = evaluate_supplied_retention_blocker(ARTIFACT, holds=[hold(), bad])
    assert result.status == "blocked_by_supplied_hold"
    assert result.violations
    assert any(item.status == "not_verified" for item in result.findings)
    bad["hold_id"] = []
    malformed_identity = evaluate_supplied_retention_blocker(
        ARTIFACT, holds=[hold(), bad],
    )
    assert malformed_identity.status == "blocked_by_supplied_hold"
    assert malformed_identity.violations


def test_invalid_record_claiming_the_matching_chain_stays_unverified():
    bad = hold()
    bad["schema_version"] = "invalid"
    result = evaluate_supplied_retention_blocker(ARTIFACT, holds=[hold(), bad])
    assert result.status == "not_verified"
    assert result.violations


def test_missing_scope_in_the_matching_chain_still_prevents_a_blocker_verdict():
    placed = hold()
    amended = copy.deepcopy(placed)
    amended["action"] = "amended"
    amended["supersedes"] = validate.legal_hold_declaration_fingerprint(placed)
    amended["scope"] = {"kind": "tenant_writer", "tenant": "acme"}
    result = evaluate_supplied_retention_blocker(ARTIFACT, holds=[placed, amended])
    assert result.status == "missing_binding"
    assert any(item.status == "matched" and item.path.endswith(".artifact")
               for item in result.findings)


def test_conflict_in_an_independent_lineage_remains_visible_without_erasing_blocker():
    placed = hold()
    unrelated = hold()
    unrelated["hold_id"] = "H-other"
    unrelated["scope"] = {"kind": "exact_artifacts", "artifacts": [
        {"kind": "receipt", "fingerprint": "sha256:" + "e" * 64},
    ]}
    competing = copy.deepcopy(unrelated)
    competing["producer_ref"] = "second-producer"
    result = evaluate_supplied_retention_blocker(
        ARTIFACT, holds=[competing, placed, unrelated],
    )
    assert result.status == "blocked_by_supplied_hold"
    assert any(item.status == "conflict" for item in result.findings)


def test_empty_or_nonmatching_subset_has_only_subset_verdict():
    empty = evaluate_supplied_retention_blocker(ARTIFACT)
    assert empty.status == "no_blocker_in_supplied_evidence"
    different = copy.deepcopy(ARTIFACT)
    different["fingerprint"] = "sha256:" + "e" * 64
    nonmatch = evaluate_supplied_retention_blocker(different, holds=[hold()])
    assert nonmatch.status == "no_blocker_in_supplied_evidence"


def test_selector_requires_one_exact_snapshot_and_does_not_expand_empty_one():
    current_hold = fixture(371, "legal-hold-declaration")
    missing = evaluate_supplied_retention_blocker(ARTIFACT, holds=[current_hold])
    assert missing.status == "missing_binding"
    snapshot = snapshot_for(current_hold, "legal_hold_declaration")
    matched = evaluate_supplied_retention_blocker(
        ARTIFACT, holds=[current_hold], snapshots=[snapshot],
    )
    assert matched.status == "blocked_by_supplied_hold"
    snapshot["artifacts"] = []
    nonmatch = evaluate_supplied_retention_blocker(
        ARTIFACT, holds=[current_hold], snapshots=[snapshot],
    )
    assert nonmatch.status == "no_blocker_in_supplied_evidence"


def test_missing_predecessor_cannot_verify_its_chain_but_an_independent_hold_can():
    placed = hold()
    amended = copy.deepcopy(placed)
    amended["action"] = "amended"
    amended["supersedes"] = validate.legal_hold_declaration_fingerprint(placed)
    amended["producer_ref"] = "amender"
    missing = evaluate_supplied_retention_blocker(ARTIFACT, holds=[amended])
    assert missing.status == "missing_binding"
    amended["hold_id"] = "wrong-stable-id"
    wrong = evaluate_supplied_retention_blocker(ARTIFACT, holds=[placed, amended])
    assert wrong.status == "blocked_by_supplied_hold"
    assert any(item.status == "mismatched" for item in wrong.findings)


@pytest.mark.parametrize("action", ["released", "cancelled"])
def test_release_or_cancel_claim_never_erases_prior_placement(action):
    placed = hold()
    terminal = copy.deepcopy(placed)
    terminal["action"] = action
    terminal["supersedes"] = validate.legal_hold_declaration_fingerprint(placed)
    terminal["rationale"] = "Producer states the hold ended."
    terminal["authority_ref"] = "claimed/authority"
    del terminal["scope"]
    result = evaluate_supplied_retention_blocker(ARTIFACT, holds=[placed, terminal])
    assert result.status == "not_verified"
    assert any(item.status == "not_verified" and "authority" in item.reason
               for item in result.findings)
    terminal_only = evaluate_supplied_retention_blocker(ARTIFACT, holds=[terminal])
    assert terminal_only.status == "not_verified"


def test_fork_competing_root_and_multiple_snapshots_remain_conflicts():
    placed = hold()
    other_root = copy.deepcopy(placed)
    other_root["producer_ref"] = "other"
    roots = evaluate_supplied_retention_blocker(ARTIFACT, holds=[placed, other_root])
    assert roots.status == "conflicting_supplied_hold"
    amended = copy.deepcopy(placed)
    amended["action"] = "amended"
    amended["supersedes"] = validate.legal_hold_declaration_fingerprint(placed)
    amended["producer_ref"] = "amender-1"
    other_amendment = copy.deepcopy(amended)
    other_amendment["producer_ref"] = "amender-2"
    forks = evaluate_supplied_retention_blocker(
        ARTIFACT, holds=[placed, amended, other_amendment],
    )
    assert forks.status == "conflicting_supplied_hold"
    selector = fixture(371, "legal-hold-declaration")
    first = snapshot_for(selector, "legal_hold_declaration")
    second = copy.deepcopy(first)
    second["snapshot_id"] = "S-002"
    snapshots = evaluate_supplied_retention_blocker(
        ARTIFACT, holds=[selector], snapshots=[first, second],
    )
    assert snapshots.status == "conflicting_supplied_hold"


def test_invalid_supplied_hold_or_artifact_is_not_verified():
    bad_hold = hold()
    bad_hold["schema_version"] = "wrong"
    result = evaluate_supplied_retention_blocker(ARTIFACT, holds=[bad_hold])
    assert result.status == "not_verified" and result.violations
    bad_artifact = {"kind": "receipt", "fingerprint": "wrong"}
    result = evaluate_supplied_retention_blocker(bad_artifact, holds=[hold()])
    assert result.status == "not_verified" and result.artifact is None


def test_custody_observation_does_not_change_subset_hold_verdict():
    observation = fixture(375, "custody-observation")
    assert observation["result"] == "retrievable"
    assert validate.validate_custody_observation(observation) == []
    result = evaluate_supplied_retention_blocker(observation["artifact"])
    assert result.status == "no_blocker_in_supplied_evidence"
