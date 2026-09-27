"""ADR-044 reference adapter and conformance runner tests."""
from __future__ import annotations

import copy
from dataclasses import dataclass

import pytest

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


def test_corpus_loader_returns_independent_copies():
    first = load_adapter_conformance_cases()
    second = load_adapter_conformance_cases()
    first[0]["request"]["operation"] = "changed"
    assert second[0]["request"]["operation"] == "receipt.retrieve"


def test_conformance_runner_detects_mismatch_mutation_and_exception():
    one = load_adapter_conformance_cases()[:1]

    mismatch = run_adapter_conformance(lambda request, setup: {}, cases=one)
    assert not mismatch.conformant
    assert "invalid" in mismatch.failures[0].reason

    def mutate(request, setup):
        request["execution_ref"] = "changed/writer/0"
        return copy.deepcopy(one[0]["expected_result"])

    mutation = run_adapter_conformance(mutate, cases=one)
    assert "mutated" in mutation.failures[0].reason

    def explode(request, setup):
        raise OSError("offline")

    exception = run_adapter_conformance(explode, cases=one)
    assert "OSError" in exception.failures[0].reason
