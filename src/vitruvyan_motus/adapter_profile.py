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
EVIDENCE_INTERFACE_VERSION = "1.0.0"
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
MAX_PACKAGE_BYTES = 160 * 1024 * 1024

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
    return operation if isinstance(operation, str) and operation in OPERATIONS else None


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
        f"{issue.rule} {issue.path}: "
        + (
            "message does not satisfy the adapter profile schema"
            if issue.rule == "SCHEMA"
            else issue.message
        )
        for issue in issues[:16]
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


def _resource_bound_was_exceeded(issues: list[Any]) -> bool:
    return any(
        issue.rule == "AP2" and "exceeds" in issue.message
        for issue in issues
    )


def _correlation_failure(request: object, result: object) -> str | None:
    """Return why an ADR-043 result cannot answer its adapter request."""
    if not isinstance(request, dict) or not isinstance(result, dict):
        return None
    if request.get("operation") != "evidence.execute":
        return None
    if result.get("outcome") != "completed":
        return None
    embedded_request = request.get("evidence_request")
    embedded_result = result.get("evidence_result")
    if not isinstance(embedded_request, dict) or not isinstance(embedded_result, dict):
        return None
    if embedded_result.get("interface_version") != embedded_request.get("interface_version"):
        return "embedded ADR-043 result interface_version differs from its request"
    if embedded_result.get("operation") != embedded_request.get("operation"):
        return "embedded ADR-043 result operation differs from its request"
    embedded_operation = embedded_request.get("operation")
    if embedded_operation in ("inspect", "verify"):
        artifact = embedded_request.get("artifact")
        if not isinstance(artifact, dict):
            return None
        expected_ids = [artifact.get("input_id")]
        if embedded_operation == "verify":
            companions = embedded_request.get("companions")
            if isinstance(companions, list):
                expected_ids.extend(
                    item.get("input_id") for item in companions if isinstance(item, dict)
                )
        subject = embedded_result.get("subject")
        if not isinstance(subject, dict):
            return "embedded ADR-043 result subject is missing"
        if (
            subject.get("input_id") != artifact.get("input_id")
            or subject.get("kind") != artifact.get("kind")
        ):
            return "embedded ADR-043 result subject differs from its request"
    elif embedded_operation == "query":
        artifacts = embedded_request.get("artifacts")
        if not isinstance(artifacts, list):
            return None
        expected_ids = [
            item.get("input_id") for item in artifacts if isinstance(item, dict)
        ]
        if embedded_result.get("subject") is not None:
            return "embedded ADR-043 query result unexpectedly carries a subject"
    else:
        return None
    scope = embedded_result.get("scope")
    if not isinstance(scope, dict) or scope.get("input_ids") != expected_ids:
        return "embedded ADR-043 result scope differs from its request inputs"
    return None


def _same_json_value(left: object, right: object) -> bool:
    """Type-sensitive equality for JSON values (where False is not 0)."""
    if type(left) is not type(right):
        return False
    if isinstance(left, dict):
        return left.keys() == right.keys() and all(
            _same_json_value(left[key], right[key]) for key in left
        )
    if isinstance(left, list):
        return len(left) == len(right) and all(
            _same_json_value(a, b) for a, b in zip(left, right)
        )
    return left == right


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
        if not isinstance(request, dict):
            return _failure(None, "invalid_request", "adapter request must be an object")
        operation = _known_operation(request)
        profile_version = request.get("profile_version")
        if not isinstance(profile_version, str):
            return _failure(operation, "invalid_request", "profile_version must be a string")
        if profile_version != PROFILE_VERSION:
            return _failure(
                operation,
                "unsupported_version",
                "adapter profile version is not supported",
            )
        if "operation" in request and not isinstance(request["operation"], str):
            return _failure(None, "invalid_request", "operation must be a string")
        if operation == "evidence.execute":
            embedded = request.get("evidence_request")
            if (
                isinstance(embedded, dict)
                and isinstance(embedded.get("interface_version"), str)
                and embedded["interface_version"] != EVIDENCE_INTERFACE_VERSION
            ):
                return _failure(
                    operation,
                    "unsupported_version",
                    "Motus evidence interface version is not supported",
                )

        issues = _validate(request)
        if issues:
            kind = "resource_exhausted" if _resource_bound_was_exceeded(issues) else "invalid_request"
            return _failure(operation, kind, _validation_detail(issues))

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
                if len(package) > MAX_PACKAGE_BYTES:
                    raise AdapterProfileFailure(
                        "resource_exhausted",
                        "retrieved package exceeds the 160 MiB profile limit",
                    )
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
            if _resource_bound_was_exceeded(result_issues):
                result = _failure(
                    operation, "resource_exhausted", _validation_detail(result_issues)
                )
            else:
                raise RuntimeError(
                    "reference adapter produced an invalid result: "
                    + _validation_detail(result_issues)
                )
        correlation_failure = _correlation_failure(request, result)
        if correlation_failure is not None:
            raise RuntimeError(correlation_failure)
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
        expected_hook_error = case.get("expected_hook_error")
        has_expected_result = "expected_result" in case
        if has_expected_result == (expected_hook_error is not None):
            failures.append(ConformanceFailure(
                case_id, "case must contain exactly one expected_result or expected_hook_error"
            ))
            continue
        if expected_hook_error not in (None, "operational_exception"):
            failures.append(ConformanceFailure(case_id, "expected_hook_error is invalid"))
            continue
        try:
            request = copy.deepcopy(case["request"])
            setup = copy.deepcopy(case["setup"])
        except (KeyError, TypeError):
            failures.append(ConformanceFailure(case_id, "case shape is invalid"))
            continue
        expected = copy.deepcopy(case.get("expected_result"))

        request_before = copy.deepcopy(request)
        setup_before = copy.deepcopy(setup)
        expected_issues = [] if expected_hook_error else _validate(expected)
        if expected_issues:
            failures.append(ConformanceFailure(
                case_id, "corpus expected result is invalid: " + _validation_detail(expected_issues)
            ))
            continue
        expected_correlation = _correlation_failure(request, expected)
        if expected_correlation is not None:
            failures.append(ConformanceFailure(case_id, "corpus mismatch: " + expected_correlation))
            continue

        try:
            actual = invoke(request, setup)
        except Exception as exc:  # the report records the host failure verbatim by type
            if not _same_json_value(request, request_before) or not _same_json_value(setup, setup_before):
                failures.append(ConformanceFailure(case_id, "hook mutated request or setup"))
                continue
            if expected_hook_error == "operational_exception":
                passed += 1
                continue
            failures.append(ConformanceFailure(
                case_id, f"hook raised {type(exc).__name__}: {str(exc)[:512]}"
            ))
            continue
        if expected_hook_error is not None:
            failures.append(ConformanceFailure(
                case_id, "hook returned a result instead of raising an operational exception"
            ))
            continue
        if not _same_json_value(request, request_before) or not _same_json_value(setup, setup_before):
            failures.append(ConformanceFailure(case_id, "hook mutated request or setup"))
            continue
        actual_issues = _validate(actual)
        if actual_issues:
            failures.append(ConformanceFailure(
                case_id, "adapter result is invalid: " + _validation_detail(actual_issues)
            ))
            continue
        actual_correlation = _correlation_failure(request, actual)
        if actual_correlation is not None:
            failures.append(ConformanceFailure(case_id, actual_correlation))
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
