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


def test_application_exact_invalid_policy_is_not_a_valid_binding():
    invalid = policy()
    invalid["schema_version"] = "wrong"
    app = application()
    app["policy_fingerprint"] = validate.retention_policy_declaration_fingerprint(invalid)
    result = verify_retention_application_bindings(app, policy=invalid)
    assert result.supplied_violations
    assert any(item.path == "$.policy_fingerprint" and item.status == "not_verified"
               and item.expected == app["policy_fingerprint"]
               for item in result.findings)


@pytest.mark.parametrize("kind", ["hold", "snapshot"])
def test_application_exact_invalid_candidate_outranks_unrelated_valid(kind):
    app = application()
    current_hold = hold()
    if kind == "hold":
        invalid = copy.deepcopy(current_hold)
        invalid["schema_version"] = "wrong"
        unrelated = copy.deepcopy(current_hold)
        unrelated["hold_id"] = "H-unrelated-valid"
        expected = validate.legal_hold_declaration_fingerprint(invalid)
        app["hold_fingerprints"] = [expected]
        kwargs = lambda values: {"holds": values}
        path = "$.hold_fingerprints[0]"
    else:
        invalid = snapshot_for(current_hold, "legal_hold_declaration")
        invalid["schema_version"] = "wrong"
        unrelated = snapshot_for(current_hold, "legal_hold_declaration")
        unrelated["snapshot_id"] = "S-unrelated-valid"
        expected = validate.retention_scope_snapshot_fingerprint(invalid)
        app["scope_snapshot_fingerprints"] = [expected]
        kwargs = lambda values: {"snapshots": values}
        path = "$.scope_snapshot_fingerprints[0]"
    left = verify_retention_application_bindings(
        app, policy=policy(), **kwargs([invalid, unrelated]),
    )
    right = verify_retention_application_bindings(
        app, policy=policy(), **kwargs([unrelated, invalid]),
    )
    for result in (left, right):
        assert result.supplied_violations
        assert any(item.path == path and item.status == "not_verified" and
                   item.expected == expected for item in result.findings)
        assert not any(item.path == path and item.status == "matched"
                       for item in result.findings)
    assert left.findings == right.findings


def test_application_unfingerprintable_invalid_candidate_cannot_impersonate_digest():
    app = application()
    expected = "sha256:" + "d" * 64
    app["hold_fingerprints"] = [expected]
    invalid = hold()
    invalid["producer_ref"] = float("nan")
    unrelated = hold()
    unrelated["hold_id"] = "H-unrelated-valid"
    result = verify_retention_application_bindings(
        app, policy=policy(), holds=[invalid, unrelated],
    )
    assert result.supplied_violations
    assert any(item.path == "$.hold_fingerprints[0]" and
               item.status == "mismatched" for item in result.findings)
    assert not any(item.path == "$.hold_fingerprints[0]" and
                   item.status == "matched" for item in result.findings)


def test_application_duplicate_invalid_exact_identity_remains_conflicted():
    app = application()
    invalid = hold()
    invalid["schema_version"] = "wrong"
    expected = validate.legal_hold_declaration_fingerprint(invalid)
    app["hold_fingerprints"] = [expected]
    result = verify_retention_application_bindings(
        app, policy=policy(), holds=[invalid, copy.deepcopy(invalid)],
    )
    assert any(item.path == "$.hold_fingerprints[0]" and
               item.status == "not_verified" for item in result.findings)
    assert any(item.path == f"hold:{expected}" and item.status == "conflict"
               for item in result.findings)


def test_application_valid_invalid_digest_collision_never_matches(monkeypatch):
    app = application()
    forced = "sha256:" + "d" * 64
    app["hold_fingerprints"] = [forced]
    invalid = hold()
    invalid["schema_version"] = "wrong"
    valid = hold()
    valid["hold_id"] = "H-unrelated-valid"
    monkeypatch.setattr(validate, "legal_hold_declaration_fingerprint",
                        lambda _document: forced)
    result = verify_retention_application_bindings(
        app, policy=policy(), holds=[valid, invalid],
    )
    assert any(item.path == "$.hold_fingerprints[0]" and
               item.status == "not_verified" for item in result.findings)
    assert any(item.path == f"hold:{forced}" and item.status == "conflict"
               for item in result.findings)


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


def test_snapshot_for_omitted_hold_is_a_visible_missing_binding():
    omitted = fixture(371, "legal-hold-declaration")
    snapshot = snapshot_for(omitted, "legal_hold_declaration")
    result = evaluate_supplied_retention_blocker(
        ARTIFACT, snapshots=[snapshot],
    )
    assert result.status == "missing_binding"
    source = validate.legal_hold_declaration_fingerprint(omitted)
    assert any(item.path.endswith(".source") and item.status == "missing" and
               item.expected == source for item in result.findings)

    unrelated_artifact = copy.deepcopy(ARTIFACT)
    unrelated_artifact["fingerprint"] = "sha256:" + "e" * 64
    nonmatch = evaluate_supplied_retention_blocker(
        unrelated_artifact, snapshots=[snapshot],
    )
    assert nonmatch.status == "no_blocker_in_supplied_evidence"


def test_snapshot_for_omitted_hold_stays_visible_with_independent_blocker():
    omitted = fixture(371, "legal-hold-declaration")
    omitted["hold_id"] = "H-omitted"
    snapshot = snapshot_for(omitted, "legal_hold_declaration")
    left = evaluate_supplied_retention_blocker(
        ARTIFACT, holds=[hold()], snapshots=[snapshot],
    )
    right = evaluate_supplied_retention_blocker(
        ARTIFACT, snapshots=[snapshot], holds=[hold()],
    )
    assert left.status == right.status == "blocked_by_supplied_hold"
    assert left.findings == right.findings
    assert any(item.path.endswith(".source") and item.status == "missing"
               for item in left.findings)


def test_snapshot_for_supplied_invalid_hold_is_not_claimed_absent():
    invalid_hold = fixture(371, "legal-hold-declaration")
    invalid_hold["schema_version"] = "wrong"
    source = validate.legal_hold_declaration_fingerprint(invalid_hold)
    snapshot = snapshot_for(invalid_hold, "legal_hold_declaration")
    snapshot["source"]["fingerprint"] = source
    result = evaluate_supplied_retention_blocker(
        ARTIFACT, holds=[invalid_hold], snapshots=[snapshot],
    )
    assert result.status == "not_verified"
    assert result.violations
    assert any(item.path.endswith(".source") and
               item.status == "not_verified" and item.expected == source
               for item in result.findings)


def test_invalid_snapshot_named_to_selector_hold_is_not_missing_binding():
    selector = fixture(371, "legal-hold-declaration")
    invalid = snapshot_for(selector, "legal_hold_declaration")
    invalid["schema_version"] = "wrong"
    result = evaluate_supplied_retention_blocker(
        ARTIFACT, holds=[selector], snapshots=[invalid],
    )
    assert result.status == "not_verified"
    assert result.violations and result.violations[0][0] == "retention-scope-snapshot"
    fingerprint = validate.legal_hold_declaration_fingerprint(selector)
    assert any(item.path == f"hold:{fingerprint}.snapshots" and
               item.status == "not_verified" for item in result.findings)


def test_valid_and_invalid_snapshots_for_same_selector_do_not_prove_blocker():
    selector = fixture(371, "legal-hold-declaration")
    valid = snapshot_for(selector, "legal_hold_declaration")
    invalid = copy.deepcopy(valid)
    invalid["snapshot_id"] = "S-invalid"
    invalid["schema_version"] = "wrong"
    left = evaluate_supplied_retention_blocker(
        ARTIFACT, holds=[selector], snapshots=[valid, invalid],
    )
    right = evaluate_supplied_retention_blocker(
        ARTIFACT, holds=[selector], snapshots=[invalid, valid],
    )
    assert left.status == right.status == "not_verified"
    assert left.findings == right.findings
    assert any(item.path.endswith(".artifact") and item.status == "matched"
               for item in left.findings)
    assert left.violations and right.violations


def test_same_selector_snapshot_conflict_outranks_invalid_snapshot():
    selector = fixture(371, "legal-hold-declaration")
    first = snapshot_for(selector, "legal_hold_declaration")
    second = copy.deepcopy(first)
    second["snapshot_id"] = "S-competing"
    invalid = copy.deepcopy(first)
    invalid["schema_version"] = "wrong"
    result = evaluate_supplied_retention_blocker(
        ARTIFACT, holds=[selector], snapshots=[invalid, second, first],
    )
    assert result.status == "conflicting_supplied_hold"
    assert result.violations
    assert any(item.path.endswith(".snapshots") and item.status == "conflict"
               for item in result.findings)


def test_unrelated_invalid_snapshot_does_not_erase_independent_blocker():
    placed = hold()
    selector = fixture(371, "legal-hold-declaration")
    selector["hold_id"] = "H-unrelated-selector"
    invalid = snapshot_for(selector, "legal_hold_declaration")
    invalid["schema_version"] = "wrong"
    left = evaluate_supplied_retention_blocker(
        ARTIFACT, holds=[placed, selector], snapshots=[invalid],
    )
    right = evaluate_supplied_retention_blocker(
        ARTIFACT, holds=[selector, placed], snapshots=[invalid],
    )
    assert left.status == right.status == "blocked_by_supplied_hold"
    assert left.findings == right.findings
    assert left.violations


def test_malformed_snapshot_source_cannot_be_associated_with_selector_hold():
    selector = fixture(371, "legal-hold-declaration")
    selector["hold_id"] = "H-unrelated-selector"
    invalid = snapshot_for(selector, "legal_hold_declaration")
    invalid["source"] = ["legal_hold_declaration", "not-an-exact-digest"]
    result = evaluate_supplied_retention_blocker(
        ARTIFACT, holds=[selector], snapshots=[invalid],
    )
    assert result.status == "missing_binding"
    assert result.violations
    assert not any(item.path.endswith(".snapshots") and
                   item.status == "not_verified" for item in result.findings)
    independently_blocked = evaluate_supplied_retention_blocker(
        ARTIFACT, holds=[selector, hold()], snapshots=[invalid],
    )
    assert independently_blocked.status == "blocked_by_supplied_hold"
    assert independently_blocked.violations


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


@pytest.mark.parametrize("changed", ["producer_namespace", "hold_id"])
def test_cross_lineage_supplied_predecessor_is_mismatch_not_missing(changed):
    predecessor = hold()
    predecessor["scope"]["artifacts"] = [
        {"kind": "receipt", "fingerprint": "sha256:" + "e" * 64},
    ]
    child = copy.deepcopy(predecessor)
    child["action"] = "amended"
    child["supersedes"] = validate.legal_hold_declaration_fingerprint(predecessor)
    child["producer_ref"] = "child-producer"
    child["scope"]["artifacts"] = [ARTIFACT]
    child[changed] = "different-" + changed
    left = evaluate_supplied_retention_blocker(
        ARTIFACT, holds=[predecessor, child],
    )
    right = evaluate_supplied_retention_blocker(
        ARTIFACT, holds=[child, predecessor],
    )
    assert left.status == right.status == "not_verified"
    assert left.findings == right.findings
    assert any(item.path.endswith(".supersedes") and item.status == "mismatched"
               for item in left.findings)
    independent = hold()
    independent["hold_id"] = "H-independent-complete"
    complete = evaluate_supplied_retention_blocker(
        ARTIFACT, holds=[child, independent, predecessor],
    )
    assert complete.status == "blocked_by_supplied_hold"
    assert any(item.status == "mismatched" for item in complete.findings)


def test_ambiguous_cross_lineage_predecessor_remains_conflict(monkeypatch):
    first = hold()
    first["hold_id"] = "H-parent-one"
    first["producer_ref"] = "parent-one"
    first["scope"]["artifacts"] = [
        {"kind": "receipt", "fingerprint": "sha256:" + "e" * 64},
    ]
    second = copy.deepcopy(first)
    second["hold_id"] = "H-parent-two"
    second["producer_ref"] = "parent-two"
    child = hold()
    child["hold_id"] = "H-child"
    child["action"] = "amended"
    child["producer_ref"] = "child"
    forced = "sha256:" + "d" * 64
    child["supersedes"] = forced
    original = validate.legal_hold_declaration_fingerprint
    monkeypatch.setattr(
        validate, "legal_hold_declaration_fingerprint",
        lambda document: forced if document["producer_ref"].startswith("parent-")
        else original(document),
    )
    result = evaluate_supplied_retention_blocker(
        ARTIFACT, holds=[first, child, second],
    )
    assert result.status == "conflicting_supplied_hold"
    assert any(item.path.endswith(".supersedes") and item.status == "conflict"
               for item in result.findings)


def test_same_hold_fingerprint_across_chains_cannot_bind_snapshot(monkeypatch):
    first = fixture(371, "legal-hold-declaration")
    first["hold_id"] = "H-first"
    second = copy.deepcopy(first)
    second["hold_id"] = "H-second"
    second["producer_ref"] = "other-producer"
    forced = "sha256:" + "d" * 64
    original = validate.legal_hold_declaration_fingerprint
    monkeypatch.setattr(
        validate, "legal_hold_declaration_fingerprint",
        lambda document: forced if document["hold_id"] in ("H-first", "H-second")
        else original(document),
    )
    snapshot = snapshot_for(first, "legal_hold_declaration")
    left = evaluate_supplied_retention_blocker(
        ARTIFACT, holds=[first, second], snapshots=[snapshot],
    )
    right = evaluate_supplied_retention_blocker(
        ARTIFACT, holds=[second, first], snapshots=[snapshot],
    )
    assert left.status == right.status == "conflicting_supplied_hold"
    assert left.findings == right.findings
    assert any(item.path == f"lineage:{forced}" and item.status == "conflict"
               for item in left.findings)
    independent = hold()
    independent["hold_id"] = "H-independent-complete"
    complete = evaluate_supplied_retention_blocker(
        ARTIFACT, holds=[second, independent, first], snapshots=[snapshot],
    )
    assert complete.status == "blocked_by_supplied_hold"
    assert any(item.path == f"lineage:{forced}" and item.status == "conflict"
               for item in complete.findings)


def test_invalid_hold_colliding_with_snapshot_source_is_visible_conflict(monkeypatch):
    valid = fixture(371, "legal-hold-declaration")
    valid["hold_id"] = "H-valid"
    invalid = copy.deepcopy(valid)
    invalid["hold_id"] = "H-invalid"
    invalid["schema_version"] = "wrong"
    forced = "sha256:" + "d" * 64
    original = validate.legal_hold_declaration_fingerprint
    monkeypatch.setattr(
        validate, "legal_hold_declaration_fingerprint",
        lambda document: forced if document["hold_id"] in ("H-valid", "H-invalid")
        else original(document),
    )
    snapshot = snapshot_for(valid, "legal_hold_declaration")
    result = evaluate_supplied_retention_blocker(
        ARTIFACT, holds=[invalid, valid], snapshots=[snapshot],
    )
    assert result.status == "conflicting_supplied_hold"
    assert result.violations
    assert any(item.path == f"lineage:{forced}" and item.status == "conflict"
               for item in result.findings)


@pytest.mark.parametrize("difference", ["producer_namespace", "hold_id", "both"])
def test_exact_invalid_predecessor_is_supplied_but_unverified(difference):
    invalid_parent = hold()
    invalid_parent["schema_version"] = "wrong"
    if difference in ("producer_namespace", "both"):
        invalid_parent["producer_namespace"] = "other-namespace"
    if difference in ("hold_id", "both"):
        invalid_parent["hold_id"] = "H-other-parent"
    predecessor = validate.legal_hold_declaration_fingerprint(invalid_parent)
    child = hold()
    child["action"] = "amended"
    child["producer_ref"] = "child-producer"
    child["supersedes"] = predecessor
    left = evaluate_supplied_retention_blocker(
        ARTIFACT, holds=[invalid_parent, child],
    )
    right = evaluate_supplied_retention_blocker(
        ARTIFACT, holds=[child, invalid_parent],
    )
    assert left.status == right.status == "not_verified"
    assert left.findings == right.findings
    assert left.violations and right.violations
    assert any(item.path.endswith(".supersedes") and item.status == "not_verified"
               and item.expected == predecessor and item.observed == predecessor
               for item in left.findings)
    independent = hold()
    independent["hold_id"] = "H-independent-complete"
    complete = evaluate_supplied_retention_blocker(
        ARTIFACT, holds=[child, invalid_parent, independent],
    )
    assert complete.status == "blocked_by_supplied_hold"
    assert complete.violations


def test_duplicate_invalid_exact_predecessor_is_conflict():
    invalid_parent = hold()
    invalid_parent["schema_version"] = "wrong"
    invalid_parent["hold_id"] = "H-other-parent"
    child = hold()
    child["action"] = "amended"
    child["producer_ref"] = "child-producer"
    child["supersedes"] = validate.legal_hold_declaration_fingerprint(invalid_parent)
    result = evaluate_supplied_retention_blocker(
        ARTIFACT, holds=[invalid_parent, child, copy.deepcopy(invalid_parent)],
    )
    reversed_result = evaluate_supplied_retention_blocker(
        ARTIFACT, holds=[child, copy.deepcopy(invalid_parent), invalid_parent],
    )
    assert result.status == reversed_result.status == "conflicting_supplied_hold"
    assert result.findings == reversed_result.findings
    assert len(result.violations) == 2
    assert any(item.path.endswith(".supersedes") and item.status == "conflict"
               for item in result.findings)
    independent = hold()
    independent["hold_id"] = "H-independent-complete"
    complete = evaluate_supplied_retention_blocker(
        ARTIFACT, holds=[invalid_parent, child, independent,
                         copy.deepcopy(invalid_parent)],
    )
    assert complete.status == "blocked_by_supplied_hold"
    assert any(item.path.endswith(".supersedes") and item.status == "conflict"
               for item in complete.findings)


def test_valid_invalid_exact_predecessor_collision_is_conflict(monkeypatch):
    invalid_parent = hold()
    invalid_parent["schema_version"] = "wrong"
    invalid_parent["hold_id"] = "H-invalid-parent"
    invalid_parent["producer_ref"] = "invalid-parent"
    valid_parent = hold()
    valid_parent["hold_id"] = "H-valid-parent"
    valid_parent["producer_ref"] = "valid-parent"
    valid_parent["scope"]["artifacts"] = [
        {"kind": "receipt", "fingerprint": "sha256:" + "e" * 64},
    ]
    child = hold()
    child["action"] = "amended"
    child["producer_ref"] = "child-producer"
    forced = "sha256:" + "d" * 64
    child["supersedes"] = forced
    original = validate.legal_hold_declaration_fingerprint
    monkeypatch.setattr(
        validate, "legal_hold_declaration_fingerprint",
        lambda document: forced if document["producer_ref"] in
        ("invalid-parent", "valid-parent") else original(document),
    )
    left = evaluate_supplied_retention_blocker(
        ARTIFACT, holds=[invalid_parent, valid_parent, child],
    )
    right = evaluate_supplied_retention_blocker(
        ARTIFACT, holds=[child, valid_parent, invalid_parent],
    )
    assert left.status == right.status == "conflicting_supplied_hold"
    assert left.findings == right.findings
    assert any(item.path.endswith(".supersedes") and item.status == "conflict"
               for item in left.findings)


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
