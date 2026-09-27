"""ADR-044 reference adapter and conformance runner tests."""
from __future__ import annotations

import copy
from dataclasses import dataclass

import pytest

import vitruvyan_motus.adapter_profile as adapter_profile
from vitruvyan_motus.contract import validate as contract_validate
from vitruvyan_motus.adapter_profile import (
    AdapterProfileFailure,
    InProcessAdapter,
    load_adapter_conformance_cases,
    run_adapter_conformance,
)
from vitruvyan_motus.evidence import PackageVerdict


@dataclass(frozen=True)
class Finding:
    level: str
    status: str
    reason: str


@dataclass(frozen=True)
class Violation:
    rule: str
    path: str
    message: str


@dataclass(frozen=True)
class ReceiptVerdict:
    findings: tuple[Finding, ...]
    violations: tuple[Violation, ...]
    notes: tuple[str, ...]


class FakeEvidence:
    def __init__(self, setup: dict, *, fail: AdapterProfileFailure | None = None):
        self.setup = setup
        self.fail = fail
        self.verified_package = None

    def _fail(self):
        if self.fail is not None:
            raise self.fail

    def receipt_for(self, execution_ref):
        self._fail()
        return copy.deepcopy(self.setup["receipt"])

    def package_for(self, execution_ref):
        self._fail()
        import base64

        return base64.b64decode(self.setup["package_base64"], validate=True)

    def verify(self, execution_ref, *, package=None):
        self._fail()
        self.verified_package = package
        verdict = self.setup["package_verdict"]
        nested = verdict["verdict"]
        receipt_verdict = None if nested is None else ReceiptVerdict(
            findings=tuple(Finding(**item) for item in nested["findings"]),
            violations=tuple(Violation(**item) for item in nested["violations"]),
            notes=tuple(nested["notes"]),
        )
        return PackageVerdict(
            receipt_verdict,
            verdict["transport_ok"],
            tuple(verdict["damaged"]),
            tuple(verdict["trace_violations"]),
        )


def _invoke_case(request: dict, setup: dict) -> dict:
    if "failure" in setup:
        failure = AdapterProfileFailure(**setup["failure"])
        evidence = FakeEvidence(setup, fail=failure)
    else:
        evidence = FakeEvidence(setup)

    def execute(_request):
        if "failure" in setup:
            raise AdapterProfileFailure(**setup["failure"])
        return copy.deepcopy(setup["evidence_result"])

    return InProcessAdapter(evidence, execute_evidence=execute).invoke(request)


def test_shipped_corpus_passes_the_reference_in_process_adapter():
    report = run_adapter_conformance(_invoke_case)
    assert report.conformant
    assert report.total >= 6
    assert report.passed == report.total
    assert report.failures == ()


def test_package_verdict_is_projected_without_collapsing_refusal_to_failure():
    setup = {
        "package_verdict": {
            "verdict": {
                "findings": [{"level": "INTEGRITY", "status": "MATCHED", "reason": "root matched"}],
                "violations": [{"rule": "P7", "path": "$.execution", "message": "different ref"}],
                "notes": ["bounded supplied evidence"],
            },
            "transport_ok": False,
            "damaged": ["core/trace.json"],
            "trace_violations": ["T11 broken chain"],
        }
    }
    evidence = FakeEvidence(setup)
    adapter = InProcessAdapter(evidence, execute_evidence=lambda request: {})
    request = {
        "profile_version": "1.0.0",
        "message_type": "request",
        "operation": "package.verify",
        "execution_ref": "tenant/writer/0",
        "package_base64": "bm90LWEtemlw",
    }
    result = adapter.invoke(request)
    assert result["outcome"] == "completed"
    assert "verified" not in result
    assert result["package_verdict"] == setup["package_verdict"]
    assert evidence.verified_package == b"not-a-zip"


def test_expected_host_failure_is_transport_data_but_bug_propagates():
    expected = InProcessAdapter(
        FakeEvidence({}, fail=AdapterProfileFailure("not_found", "missing")),
        execute_evidence=lambda request: {},
    )
    request = {
        "profile_version": "1.0.0",
        "message_type": "request",
        "operation": "receipt.retrieve",
        "execution_ref": "tenant/writer/0",
    }
    assert expected.invoke(request)["failure"] == {
        "kind": "not_found", "detail": "missing"
    }

    class Buggy(FakeEvidence):
        def receipt_for(self, execution_ref):
            raise RuntimeError("programmer bug")

    with pytest.raises(RuntimeError, match="programmer bug"):
        InProcessAdapter(Buggy({}), execute_evidence=lambda request: {}).invoke(request)

    with pytest.raises(ValueError, match="Unicode scalar"):
        AdapterProfileFailure("not_found", "bad\ud800text")


def test_malformed_and_unsupported_requests_fail_before_an_operation_runs():
    class MustNotRun(FakeEvidence):
        def receipt_for(self, execution_ref):
            raise AssertionError("operation ran")

    adapter = InProcessAdapter(MustNotRun({}), execute_evidence=lambda request: {})
    malformed = adapter.invoke({"profile_version": "1.0.0", "operation": "search"})
    assert malformed["operation"] is None
    assert malformed["failure"]["kind"] == "invalid_request"

    future = adapter.invoke({
        "profile_version": "2.0.0",
        "message_type": "request",
        "operation": "receipt.retrieve",
        "execution_ref": "tenant/writer/0",
    })
    assert future["operation"] == "receipt.retrieve"
    assert future["failure"]["kind"] == "unsupported_version"

    malformed_operation = adapter.invoke({
        "profile_version": "1.0.0",
        "message_type": "request",
        "operation": [],
    })
    assert malformed_operation["operation"] is None
    assert malformed_operation["failure"] == {
        "kind": "invalid_request", "detail": "operation must be a string"
    }

    malformed_version = adapter.invoke({
        "profile_version": [],
        "message_type": "request",
        "operation": "receipt.retrieve",
        "execution_ref": "tenant/writer/0",
    })
    assert malformed_version["failure"] == {
        "kind": "invalid_request", "detail": "profile_version must be a string"
    }


def test_interface_version_and_resource_bounds_have_distinct_failures(monkeypatch):
    adapter = InProcessAdapter(FakeEvidence({}), execute_evidence=lambda request: {})
    unsupported_interface = adapter.invoke({
        "profile_version": "1.0.0",
        "message_type": "request",
        "operation": "evidence.execute",
        "evidence_request": {
            "interface_version": "2.0.0",
            "message_type": "request",
            "operation": "inspect",
            "artifact": {
                "input_id": "manifest",
                "kind": "system_manifest",
                "media_type": "application/json",
                "document": {"schema_version": "1.0.0"},
            },
        },
    })
    assert unsupported_interface["failure"]["kind"] == "unsupported_version"

    monkeypatch.setattr(contract_validate, "_VQ_MAX_BINARY_TOTAL_BYTES", 4)
    oversized_request = adapter.invoke({
        "profile_version": "1.0.0",
        "message_type": "request",
        "operation": "package.verify",
        "execution_ref": "tenant/writer/0",
        "package_base64": "bm90LWEtemlw",
    })
    assert oversized_request["failure"]["kind"] == "resource_exhausted"


def test_retrieved_package_bound_is_checked_before_base64_encoding(monkeypatch):
    class OversizedEvidence(FakeEvidence):
        def package_for(self, execution_ref):
            return b"12345"

    monkeypatch.setattr(adapter_profile, "MAX_PACKAGE_BYTES", 4)
    adapter = InProcessAdapter(OversizedEvidence({}), execute_evidence=lambda request: {})
    result = adapter.invoke({
        "profile_version": "1.0.0",
        "message_type": "request",
        "operation": "package.retrieve",
        "execution_ref": "tenant/writer/0",
    })
    assert result["failure"] == {
        "kind": "resource_exhausted",
        "detail": "retrieved package exceeds the 160 MiB profile limit",
    }


def test_oversized_structured_result_is_resource_exhausted(monkeypatch):
    cases = load_adapter_conformance_cases()
    receipt_case = next(case for case in cases if case["case_id"].startswith("receipt-retrieve"))
    request_size = len(str(receipt_case["request"]))
    monkeypatch.setattr(contract_validate, "_AP_MAX_JSON_TOTAL_BYTES", request_size + 50)
    adapter = InProcessAdapter(FakeEvidence(receipt_case["setup"]), execute_evidence=lambda request: {})
    result = adapter.invoke(copy.deepcopy(receipt_case["request"]))
    assert result["failure"]["kind"] == "resource_exhausted"


def test_corpus_loader_returns_independent_copies():
    first = load_adapter_conformance_cases()
    second = load_adapter_conformance_cases()
    first[0]["request"]["operation"] = "changed"
    assert second[0]["request"]["operation"] == "receipt.retrieve"


def test_conformance_runner_detects_mismatch_mutation_and_exception():
    one = load_adapter_conformance_cases()[:1]

    def valid_but_different(request, setup):
        return {
            "profile_version": "1.0.0",
            "message_type": "result",
            "operation": "receipt.retrieve",
            "outcome": "failed",
            "failure": {"kind": "unavailable", "detail": "different result"},
        }

    mismatch = run_adapter_conformance(valid_but_different, cases=one)
    assert not mismatch.conformant
    assert "differs" in mismatch.failures[0].reason

    def mutate(request, setup):
        request["execution_ref"] = "changed/writer/0"
        return copy.deepcopy(one[0]["expected_result"])

    mutation = run_adapter_conformance(mutate, cases=one)
    assert "mutated" in mutation.failures[0].reason

    def explode(request, setup):
        raise OSError("offline")

    exception = run_adapter_conformance(explode, cases=one)
    assert "OSError" in exception.failures[0].reason

    def mutate_then_explode(request, setup):
        setup["receipt"]["segments"][0]["begin"]["commitment"]["sequence"] = True
        raise OSError("offline after mutation")

    exceptional_mutation = run_adapter_conformance(mutate_then_explode, cases=one)
    assert "mutated" in exceptional_mutation.failures[0].reason


def test_conformance_result_comparison_distinguishes_boolean_from_integer():
    case = copy.deepcopy(next(
        item for item in load_adapter_conformance_cases()
        if item["case_id"] == "evidence-inspect-result-is-preserved"
    ))
    finding = {
        "path": "TYPE",
        "status": "not_verified",
        "reason": "type preservation probe",
        "expected": 0,
    }
    case["expected_result"]["evidence_result"]["findings"] = [finding]

    def boolean_substitution(request, setup):
        actual = copy.deepcopy(case["expected_result"])
        actual["evidence_result"]["findings"][0]["expected"] = False
        return actual

    report = run_adapter_conformance(boolean_substitution, cases=(case,))
    assert not report.conformant
    assert "differs" in report.failures[0].reason


def test_evidence_result_must_correlate_with_the_request_operation():
    cases = load_adapter_conformance_cases()
    hostile = tuple(
        case for case in cases
        if case["case_id"] == "unrelated-evidence-result-remains-an-operational-error"
    )
    assert run_adapter_conformance(_invoke_case, cases=hostile).conformant

    def return_unrelated(request, setup):
        return {
            "profile_version": "1.0.0",
            "message_type": "result",
            "operation": "evidence.execute",
            "outcome": "completed",
            "evidence_result": copy.deepcopy(setup["evidence_result"]),
        }

    report = run_adapter_conformance(return_unrelated, cases=hostile)
    assert not report.conformant
    assert "instead of raising" in report.failures[0].reason


def test_evidence_result_scope_and_subject_must_correlate_with_inputs():
    case = next(
        item for item in load_adapter_conformance_cases()
        if item["case_id"] == "evidence-inspect-result-is-preserved"
    )
    swapped_subject = copy.deepcopy(case["setup"]["evidence_result"])
    swapped_subject["subject"]["kind"] = "risk_control_registry"
    adapter = InProcessAdapter(
        FakeEvidence({}), execute_evidence=lambda request: copy.deepcopy(swapped_subject)
    )
    with pytest.raises(RuntimeError, match="authoritative ADR-043"):
        adapter.invoke(copy.deepcopy(case["request"]))

    forged_fingerprint = copy.deepcopy(case["setup"]["evidence_result"])
    forged_fingerprint["subject"]["fingerprint"] = "sha256:" + "0" * 64
    adapter = InProcessAdapter(
        FakeEvidence({}),
        execute_evidence=lambda request: copy.deepcopy(forged_fingerprint),
    )
    with pytest.raises(RuntimeError, match="authoritative ADR-043"):
        adapter.invoke(copy.deepcopy(case["request"]))

def test_evidence_correlation_covers_verify_companions_and_query_inputs():
    inspect_request = {
        "operation": "evidence.execute",
        "evidence_request": {
            "interface_version": "1.0.0",
            "operation": "inspect",
            "artifact": {"input_id": "manifest", "kind": "system_manifest"},
        },
    }
    wrong_operation = {
        "outcome": "completed",
        "evidence_result": {
            "interface_version": "1.0.0",
            "operation": "verify",
            "scope": {"input_ids": ["manifest"]},
            "subject": {"input_id": "manifest", "kind": "system_manifest"},
        },
    }
    assert "operation differs" in adapter_profile._correlation_failure(
        inspect_request, wrong_operation
    )
    wrong_subject = {
        "outcome": "completed",
        "evidence_result": {
            "interface_version": "1.0.0",
            "operation": "inspect",
            "scope": {"input_ids": ["manifest"]},
            "subject": {"input_id": "manifest", "kind": "risk_control_registry"},
        },
    }
    assert "subject differs" in adapter_profile._correlation_failure(
        inspect_request, wrong_subject
    )

    verify_request = {
        "operation": "evidence.execute",
        "evidence_request": {
            "interface_version": "1.0.0",
            "operation": "verify",
            "artifact": {"input_id": "package", "kind": "execution_evidence_package"},
            "companions": [{"input_id": "anchor", "kind": "anchor_receipt"}],
        },
    }
    verify_result = {
        "outcome": "completed",
        "evidence_result": {
            "interface_version": "1.0.0",
            "operation": "verify",
            "scope": {"input_ids": ["package"]},
            "subject": {"input_id": "package", "kind": "execution_evidence_package"},
        },
    }
    assert "scope differs" in adapter_profile._correlation_failure(
        verify_request, verify_result
    )

    query_request = {
        "operation": "evidence.execute",
        "evidence_request": {
            "interface_version": "1.0.0",
            "operation": "query",
            "artifacts": [
                {"input_id": "a", "kind": "system_manifest"},
                {"input_id": "b", "kind": "risk_control_registry"},
            ],
        },
    }
    query_result = {
        "outcome": "completed",
        "evidence_result": {
            "interface_version": "1.0.0",
            "operation": "query",
            "scope": {"input_ids": ["b", "a"]},
            "subject": None,
        },
    }
    assert "scope differs" in adapter_profile._correlation_failure(
        query_request, query_result
    )


def test_exceptional_hook_mutation_distinguishes_negative_zero():
    case = copy.deepcopy(load_adapter_conformance_cases()[0])
    case["setup"]["probe"] = -0.0

    def mutate_then_explode(request, setup):
        setup["probe"] = 0.0
        raise RuntimeError("after mutation")

    report = run_adapter_conformance(mutate_then_explode, cases=(case,))
    assert not report.conformant
    assert "mutated" in report.failures[0].reason


def test_conformance_runner_bounds_caller_supplied_case_collections():
    with pytest.raises(ValueError, match="1..1000"):
        run_adapter_conformance(_invoke_case, cases=())
    with pytest.raises(ValueError, match="1..1000"):
        run_adapter_conformance(_invoke_case, cases=tuple({} for _ in range(1001)))
