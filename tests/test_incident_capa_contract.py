"""ADR-039 IncidentDeclaration, CAPAAction and ledger contract tests."""

from __future__ import annotations

import copy
import json
import subprocess
import sys
from pathlib import Path

import pytest

from vitruvyan_motus.contract import validate

REPO_ROOT = Path(__file__).resolve().parent.parent


def _incident(**changes) -> dict:
    document = {
        "schema_version": "1.0.0",
        "producer_namespace": "acme/incidents",
        "producer_ref": "orbis-operator",
        "incident_id": "INC-001",
        "lifecycle": "open",
        "event_class": "reliability",
        "title": "Execution did not reach its declared terminal",
        "description": "The producer observed an incomplete execution.",
        "declared_at": "2026-09-23T10:00:00Z",
    }
    document.update(changes)
    return document


def _action(incident: dict, **changes) -> dict:
    document = {
        "schema_version": "1.0.0",
        "producer_namespace": incident["producer_namespace"],
        "producer_ref": "orbis-operator",
        "action_id": "CAPA-001",
        "incident_id": incident["incident_id"],
        "incident_fingerprint": validate.incident_declaration_fingerprint(incident),
        "action_kind": "corrective",
        "description": "Add an independently reviewed retry guard.",
        "lifecycle": "proposed",
        "declared_at": "2026-09-23T10:05:00Z",
    }
    document.update(changes)
    return document


def _ledger(*entries: tuple[str, dict]) -> dict:
    return {
        "schema_version": "1.0.0",
        "entries": [
            {"kind": kind, "document": document} for kind, document in entries
        ],
    }


def _rules(violations) -> set[str]:
    return {item.rule for item in violations}


def test_three_documents_are_valid_and_have_derived_exact_identity():
    incident = _incident()
    action = _action(incident)
    ledger = _ledger(
        ("incident_declaration", incident),
        ("capa_action", action),
    )

    assert validate.validate_incident_declaration(incident) == []
    assert validate.validate_capa_action(action) == []
    assert validate.validate_incident_capa_ledger(ledger) == []
    assert validate.incident_declaration_fingerprint(incident).startswith("sha256:")
    assert validate.capa_action_fingerprint(action).startswith("sha256:")


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("compliant", True),
        ("reportable", True),
        ("blame", "operator"),
        ("effective", True),
    ],
)
def test_schema_forbids_verdict_fields(field, value):
    incident = _incident(**{field: value})
    action = _action(_incident(), **{field: value})

    assert "SCHEMA" in _rules(validate.validate_incident_declaration(incident))
    assert "SCHEMA" in _rules(validate.validate_capa_action(action))


def test_declared_closed_and_completed_states_require_their_bounded_detail():
    closed = _incident(lifecycle="closed")
    completed = _action(_incident(), lifecycle="completed")
    assert "SCHEMA" in _rules(validate.validate_incident_declaration(closed))
    assert "SCHEMA" in _rules(validate.validate_capa_action(completed))

    closed["closure_rationale"] = "Producer declares handling complete."
    completed["completed_at"] = "2026-09-23T11:00:00Z"
    assert validate.validate_incident_declaration(closed) == []
    assert validate.validate_capa_action(completed) == []


def test_calendar_and_execution_locator_checks_are_semantic_rules():
    incident = _incident(
        declared_at="2026-02-30T10:00:00Z",
        evidence=[{"kind": "execution", "execution_ref": "not/a/coordinate/x"}],
    )
    action = _action(
        _incident(),
        due_at="2026-02-30T10:00:00Z",
        evidence=[{"kind": "execution", "execution_ref": "acme/writer/-1"}],
    )

    assert _rules(validate.validate_incident_declaration(incident)) == {"INC1", "INC2"}
    assert _rules(validate.validate_capa_action(action)) == {"CAPA1", "CAPA2"}


def test_ledger_rejects_duplicate_exact_records_and_wrong_present_predecessor():
    incident = _incident()
    duplicate = _ledger(
        ("incident_declaration", incident),
        ("incident_declaration", copy.deepcopy(incident)),
    )
    assert "LEDGER1" in _rules(validate.validate_incident_capa_ledger(duplicate))

    action = _action(
        incident,
        supersedes=validate.incident_declaration_fingerprint(incident),
    )
    wrong_kind = _ledger(
        ("incident_declaration", incident),
        ("capa_action", action),
    )
    assert "LEDGER2" in _rules(validate.validate_incident_capa_ledger(wrong_kind))

    prior_action = _action(incident)
    changed_identity = _action(
        incident,
        action_id="CAPA-002",
        supersedes=validate.capa_action_fingerprint(prior_action),
    )
    wrong_identity = _ledger(
        ("capa_action", prior_action),
        ("capa_action", changed_identity),
    )
    assert "LEDGER2" in _rules(validate.validate_incident_capa_ledger(wrong_identity))


def test_missing_predecessor_is_a_valid_partial_view():
    incident = _incident(supersedes="sha256:" + "a" * 64)
    assert validate.validate_incident_capa_ledger(
        _ledger(("incident_declaration", incident))
    ) == []


def test_cycle_check_fails_closed_even_under_a_digest_collision(monkeypatch):
    """A real cycle needs a SHA-256 fixed point; inject identities to test it."""
    first_fingerprint = "sha256:" + "a" * 64
    second_fingerprint = "sha256:" + "b" * 64
    first = _incident(
        title="first",
        supersedes=second_fingerprint,
    )
    second = _incident(
        title="second",
        supersedes=first_fingerprint,
    )

    monkeypatch.setattr(
        validate,
        "_ledger_document_fingerprint",
        lambda _kind, document: (
            first_fingerprint if document["title"] == "first" else second_fingerprint
        ),
    )

    violations = validate.validate_incident_capa_ledger(
        _ledger(
            ("incident_declaration", first),
            ("incident_declaration", second),
        )
    )
    assert "LEDGER3" in _rules(violations)


def test_ledger_fingerprint_ignores_transport_order_but_binds_content():
    incident = _incident()
    action = _action(incident)
    forward = _ledger(
        ("incident_declaration", incident),
        ("capa_action", action),
    )
    reverse = copy.deepcopy(forward)
    reverse["entries"].reverse()
    changed = copy.deepcopy(forward)
    changed["entries"][1]["document"]["description"] = "Different action claim."

    assert (
        validate.incident_capa_ledger_fingerprint(forward)
        == validate.incident_capa_ledger_fingerprint(reverse)
    )
    assert (
        validate.incident_capa_ledger_fingerprint(forward)
        != validate.incident_capa_ledger_fingerprint(changed)
    )


@pytest.mark.parametrize(
    ("kind", "document"),
    [
        ("incident-declaration", _incident()),
        ("capa-action", _action(_incident())),
        (
            "incident-capa-ledger",
            _ledger(("incident_declaration", _incident())),
        ),
    ],
)
def test_all_three_surfaces_are_reachable_from_motus_validate(tmp_path, kind, document):
    path = tmp_path / f"{kind}.json"
    path.write_text(json.dumps(document), encoding="utf-8")

    result = subprocess.run(
        [sys.executable, str(REPO_ROOT / "contract" / "validate.py"), kind, str(path)],
        capture_output=True,
        text=True,
        timeout=30,
    )

    assert result.returncode == 0, result.stdout + result.stderr
    assert result.stdout == ""
