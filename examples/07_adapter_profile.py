"""Run ADR-044's shipped neutral corpus through the in-process adapter.

This is deliberately a corpus-backed example, not a server. A real host owns
authentication, authorization and storage before calling ``adapter.invoke``.
"""
from __future__ import annotations

import base64
import copy
import json

from vitruvyan_motus import (
    AdapterProfileFailure,
    InProcessAdapter,
    PackageVerdict,
    run_adapter_conformance,
)


class CorpusEvidence:
    def __init__(self, setup: dict) -> None:
        self._setup = setup

    def _failure(self) -> None:
        failure = self._setup.get("failure")
        if failure is not None:
            raise AdapterProfileFailure(failure["kind"], failure["detail"])

    def receipt_for(self, execution_ref: str) -> dict:
        self._failure()
        return copy.deepcopy(self._setup["receipt"])

    def package_for(self, execution_ref: str) -> bytes:
        self._failure()
        return base64.b64decode(self._setup["package_base64"], validate=True)

    def verify(self, execution_ref: str, *, package: bytes | None = None):
        self._failure()
        verdict = self._setup["package_verdict"]
        # This corpus case deliberately exercises a verifier refusal. A real
        # EvidenceAPI returns the same public PackageVerdict type.
        return PackageVerdict(
            None,
            verdict["transport_ok"],
            tuple(verdict["damaged"]),
            tuple(verdict["trace_violations"]),
        )


def invoke(request: dict, setup: dict) -> dict:
    def execute_evidence(_request: dict) -> dict:
        failure = setup.get("failure")
        if failure is not None:
            raise AdapterProfileFailure(failure["kind"], failure["detail"])
        return copy.deepcopy(setup["evidence_result"])

    return InProcessAdapter(
        CorpusEvidence(setup), execute_evidence=execute_evidence
    ).invoke(request)


if __name__ == "__main__":
    report = run_adapter_conformance(invoke)
    print(json.dumps({
        "corpus_version": report.corpus_version,
        "total": report.total,
        "passed": report.passed,
        "conformant": report.conformant,
        "failures": [
            {"case_id": failure.case_id, "reason": failure.reason}
            for failure in report.failures
        ],
    }, indent=2, sort_keys=True))
