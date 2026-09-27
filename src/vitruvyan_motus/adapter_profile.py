"""ADR-044 adapter profile and conformance runner.

The adapter transports exact Motus values. It does not authorize callers,
retrieve hidden evidence, repair artifacts, or interpret verifier outcomes.
"""
from __future__ import annotations

import base64
import binascii
import copy
import json
from dataclasses import dataclass, fields, is_dataclass
from importlib import resources
from typing import Any, Callable, Mapping, Protocol

PROFILE_VERSION = "1.0.0"
CONFORMANCE_CORPUS_VERSION = "1.0.0"
OPERATIONS = frozenset({
    "receipt.retrieve",
    "package.retrieve",
    "package.verify",
    "evidence.execute",
})
FAILURE_KINDS = frozenset({
    "invalid_request",
    "unsupported_version",
    "unauthorized",
    "forbidden",
    "not_found",
    "conflict",
    "resource_exhausted",
    "unavailable",
})

__all__ = [
    "PROFILE_VERSION",
    "CONFORMANCE_CORPUS_VERSION",
    "OPERATIONS",
    "FAILURE_KINDS",
    "AdapterProfileFailure",
    "InProcessAdapter",
    "ConformanceFailure",
    "ConformanceReport",
    "load_adapter_conformance_cases",
    "run_adapter_conformance",
]


class EvidenceBoundary(Protocol):
    """The exact public EvidenceAPI surface the reference adapter uses."""

    def receipt_for(self, execution_ref: str) -> dict[str, Any]: ...

    def package_for(self, execution_ref: str) -> bytes: ...

    def verify(self, execution_ref: str, *, package: bytes | None = None) -> Any: ...


class AdapterProfileFailure(Exception):
    """An expected host/retrieval failure the adapter may transport.

    Hosts raise this only after their own authentication, authorization and
    storage boundary has classified the failure. Unexpected exceptions remain
    exceptions: the reference adapter never dresses a bug as evidence.
    """

    def __init__(self, kind: str, detail: str) -> None:
        if kind not in FAILURE_KINDS - {"invalid_request", "unsupported_version"}:
            raise ValueError(f"host failure kind is not permitted: {kind!r}")
        if not isinstance(detail, str) or not detail or len(detail) > 4096:
            raise ValueError("host failure detail must be 1..4096 characters")
        try:
            detail.encode("utf-8")
        except UnicodeEncodeError:
            raise ValueError("host failure detail must be Unicode scalar text") from None
        super().__init__(detail)
        self.kind = kind
        self.detail = detail


def _json_value(value: Any) -> Any:
    """Mechanical JSON projection of frozen public verdict dataclasses."""
    if is_dataclass(value) and not isinstance(value, type):
        return {field.name: _json_value(getattr(value, field.name)) for field in fields(value)}
    if isinstance(value, tuple):
        return [_json_value(item) for item in value]
    if isinstance(value, list):
        return [_json_value(item) for item in value]
    if isinstance(value, Mapping):
        if not all(isinstance(key, str) for key in value):
            raise TypeError("adapter result mappings must have string keys")
        return {key: _json_value(item) for key, item in value.items()}
    if value is None or isinstance(value, (str, int, bool)):
        return value
    raise TypeError(f"adapter cannot project value of type {type(value).__name__}")


def _known_operation(message: object) -> str | None:
    if not isinstance(message, dict):
        return None
    operation = message.get("operation")
    return operation if operation in OPERATIONS else None


def _failure(operation: str | None, kind: str, detail: str) -> dict[str, Any]:
    detail = detail[:4096] or kind
    return {
        "profile_version": PROFILE_VERSION,
        "message_type": "result",
        "operation": operation,
        "outcome": "failed",
        "failure": {"kind": kind, "detail": detail},
    }


def _validation_detail(issues: list[Any]) -> str:
    detail = "; ".join(
        f"{issue.rule} {issue.path}: {issue.message}" for issue in issues[:16]
    )
    if len(issues) > 16:
        detail += f"; {len(issues) - 16} additional violations"
    return detail[:4096]


def _validate(message: object) -> list[Any]:
    if not isinstance(message, dict):
        return []
    # Lazy by design: importing the kernel must not import jsonschema.
    from vitruvyan_motus.contract.validate import validate_adapter_profile_message

    return validate_adapter_profile_message(message)


class InProcessAdapter:
    """Reference in-process mapping over existing Motus public interfaces.

    Authentication and tenant authorization happen before ``invoke``. Expected
    host failures may cross the boundary only as ``AdapterProfileFailure``.
    """

    def __init__(
        self,
        evidence: EvidenceBoundary,
        *,
        execute_evidence: Callable[[dict[str, Any]], dict[str, Any]] | None = None,
    ) -> None:
        self._evidence = evidence
        if execute_evidence is None:
            from vitruvyan_motus.verification_query import execute_verification_query

            execute_evidence = execute_verification_query
        self._execute_evidence = execute_evidence

    def invoke(self, request: object) -> dict[str, Any]:
        operation = _known_operation(request)
        if not isinstance(request, dict):
            return _failure(None, "invalid_request", "adapter request must be an object")
        if request.get("profile_version") != PROFILE_VERSION:
            return _failure(
                operation,
                "unsupported_version",
                f"adapter profile {request.get('profile_version')!r} is not supported",
            )

        issues = _validate(request)
        if issues:
            return _failure(operation, "invalid_request", _validation_detail(issues))

        stable_request = copy.deepcopy(request)
        try:
            if operation == "receipt.retrieve":
                result = {
                    "profile_version": PROFILE_VERSION,
                    "message_type": "result",
                    "operation": operation,
                    "outcome": "completed",
                    "receipt": copy.deepcopy(
                        self._evidence.receipt_for(request["execution_ref"])
                    ),
                }
            elif operation == "package.retrieve":
                package = self._evidence.package_for(request["execution_ref"])
                if not isinstance(package, bytes):
                    raise TypeError("EvidenceAPI package_for must return bytes")
                result = {
                    "profile_version": PROFILE_VERSION,
                    "message_type": "result",
                    "operation": operation,
                    "outcome": "completed",
                    "package_base64": base64.b64encode(package).decode("ascii"),
                }
            elif operation == "package.verify":
                try:
                    package = base64.b64decode(
                        request["package_base64"].encode("ascii"), validate=True
                    )
                except (UnicodeEncodeError, binascii.Error):
                    # Contract validation should already have caught this.
                    raise AssertionError("validated package_base64 did not decode") from None
                verdict = self._evidence.verify(
                    request["execution_ref"], package=package
                )
                result = {
                    "profile_version": PROFILE_VERSION,
                    "message_type": "result",
                    "operation": operation,
                    "outcome": "completed",
                    "package_verdict": _json_value(verdict),
                }
            else:
                evidence_result = self._execute_evidence(
                    copy.deepcopy(request["evidence_request"])
                )
                result = {
                    "profile_version": PROFILE_VERSION,
                    "message_type": "result",
                    "operation": operation,
                    "outcome": "completed",
                    "evidence_result": copy.deepcopy(evidence_result),
                }
        except AdapterProfileFailure as exc:
            result = _failure(operation, exc.kind, exc.detail)

        if request != stable_request:
            raise RuntimeError("adapter mutated its request")
        result_issues = _validate(result)
        if result_issues:
            raise RuntimeError(
                "reference adapter produced an invalid result: "
                + _validation_detail(result_issues)
            )
        return result


@dataclass(frozen=True, slots=True)
class ConformanceFailure:
    case_id: str
    reason: str


@dataclass(frozen=True, slots=True)
class ConformanceReport:
    corpus_version: str
    total: int
    passed: int
    failures: tuple[ConformanceFailure, ...]

    @property
    def conformant(self) -> bool:
        return not self.failures and self.total == self.passed


def load_adapter_conformance_cases() -> tuple[dict[str, Any], ...]:
    """Load a fresh copy of the version-matched shipped conformance corpus."""
    path = resources.files("vitruvyan_motus.contract").joinpath(
        "adapter-profile-conformance.v1.json"
    )
    corpus = json.loads(path.read_text(encoding="utf-8"))
    if corpus.get("corpus_version") != CONFORMANCE_CORPUS_VERSION:
        raise RuntimeError("adapter conformance corpus version disagrees with runtime")
    if corpus.get("profile_version") != PROFILE_VERSION:
        raise RuntimeError("adapter conformance profile version disagrees with runtime")
    cases = corpus.get("cases")
    if not isinstance(cases, list) or not cases or len(cases) > 1000:
        raise RuntimeError("adapter conformance corpus must contain 1..1000 cases")
    return tuple(copy.deepcopy(cases))


def run_adapter_conformance(
    invoke: Callable[[dict[str, Any], dict[str, Any]], dict[str, Any]],
    *,
    cases: tuple[dict[str, Any], ...] | None = None,
) -> ConformanceReport:
    """Run exact neutral cases through one caller-supplied adapter hook.

    The hook receives independent copies of ``request`` and ``setup``. Setup is
    test material used by the host harness to seed or fake storage; it is never
    part of an adapter profile message.
    """
    selected = load_adapter_conformance_cases() if cases is None else copy.deepcopy(cases)
    if not isinstance(selected, tuple) or not selected or len(selected) > 1000:
        raise ValueError("adapter conformance run requires a tuple of 1..1000 cases")
    failures: list[ConformanceFailure] = []
    passed = 0
    for index, case in enumerate(selected):
        case_id = case.get("case_id") if isinstance(case, dict) else None
        if not isinstance(case_id, str) or not case_id:
            failures.append(ConformanceFailure(f"case-{index}", "case_id is missing"))
            continue
        try:
            request = copy.deepcopy(case["request"])
            setup = copy.deepcopy(case["setup"])
            expected = copy.deepcopy(case["expected_result"])
        except (KeyError, TypeError):
            failures.append(ConformanceFailure(case_id, "case shape is invalid"))
            continue

        request_before = copy.deepcopy(request)
        setup_before = copy.deepcopy(setup)
        request_issues = _validate(request)
        expected_issues = _validate(expected)
        if request_issues or expected_issues:
            issues = request_issues + expected_issues
            failures.append(ConformanceFailure(
                case_id, "corpus message is invalid: " + _validation_detail(issues)
            ))
            continue

        try:
            actual = invoke(request, setup)
        except Exception as exc:  # the report records the host failure verbatim by type
            failures.append(ConformanceFailure(
                case_id, f"hook raised {type(exc).__name__}: {str(exc)[:512]}"
            ))
            continue
        if request != request_before or setup != setup_before:
            failures.append(ConformanceFailure(case_id, "hook mutated request or setup"))
            continue
        actual_issues = _validate(actual)
        if actual_issues:
            failures.append(ConformanceFailure(
                case_id, "adapter result is invalid: " + _validation_detail(actual_issues)
            ))
            continue
        if actual != expected:
            failures.append(ConformanceFailure(case_id, "adapter result differs from corpus"))
            continue
        passed += 1

    return ConformanceReport(
        corpus_version=CONFORMANCE_CORPUS_VERSION,
        total=len(selected),
        passed=passed,
        failures=tuple(failures),
    )
