"""ADR-035 System Manifest binding verification.

Document validation and binding verification are distinct operations. A valid
manifest says what an operator declared; this module compares selected bindings
against Motus artifacts without making compliance or certification claims.

The contract validator is imported lazily so importing vitruvyan_motus still
pulls no third-party module.
"""
from __future__ import annotations

import importlib
import json
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Iterable, Literal

from vitruvyan_motus import TRACE_SCHEMA_VERSION, __version__
from vitruvyan_motus.graph import GraphSpec
from vitruvyan_motus.trace import Trace

if TYPE_CHECKING:
    from vitruvyan_motus.contract.validate import Violation

__all__ = [
    "SystemManifestBindingFinding", "SystemManifestBindingVerdict",
    "verify_system_manifest_bindings",
]

_MATCHED = "matched"
_MISMATCHED = "mismatched"
_NOT_VERIFIED = "not verified"
_TRACE_JOIN_LIMIT = 1_000_000


@dataclass(frozen=True, slots=True)
class SystemManifestBindingFinding:
    path: str
    status: Literal["matched", "mismatched", "not verified"]
    expected: str | None
    observed: str | tuple[str, ...] | None
    reason: str


@dataclass(frozen=True, slots=True)
class SystemManifestBindingVerdict:
    """Binding result; bindings_complete is not a compliance or certification verdict."""

    manifest_fingerprint: str | None
    manifest_violations: tuple["Violation", ...]
    findings: tuple[SystemManifestBindingFinding, ...]

    @property
    def bindings_complete(self) -> bool:
        return (
            not self.manifest_violations
            and bool(self.findings)
            and all(item.status == _MATCHED for item in self.findings)
        )

    @property
    def has_mismatch(self) -> bool:
        return any(item.status == _MISMATCHED for item in self.findings)

    @property
    def has_unverified(self) -> bool:
        return any(item.status == _NOT_VERIFIED for item in self.findings)


def _contract_validate():
    return importlib.import_module("vitruvyan_motus.contract.validate")


def _plain_snapshot(validate, document: dict[str, Any]) -> dict[str, Any]:
    """Isolate one JSON value from the canonical bytes fingerprints use.

    Caller-provided iterables may execute arbitrary Python while they are
    consumed. Reading the caller's mutable manifest again after that point
    could otherwise pair the fingerprint of document A with findings about
    document B.
    """
    canonical = validate.canonical_json(document)
    return json.loads(canonical.decode("utf-8"))


def _finding(path: str, expected: str | None, observed: str | None, *, source: str, missing: bool = False) -> SystemManifestBindingFinding:
    if missing:
        return SystemManifestBindingFinding(
            path, _NOT_VERIFIED, expected, None,
            f"no {source} was supplied that can settle this binding",
        )
    status = _MATCHED if expected == observed else _MISMATCHED
    verb = "matches" if status == _MATCHED else "does not match"
    return SystemManifestBindingFinding(
        path, status, expected, observed, f"manifest value {verb} {source}",
    )


def _spec_index(
    specs: Iterable[GraphSpec],
    validate,
) -> dict[tuple[str, str], list[dict[str, Any]]]:
    """Snapshot and independently validate every supplied GraphSpec once."""
    out: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for spec in specs:
        if not isinstance(spec, GraphSpec):
            raise TypeError("graph_specs entries must be validated GraphSpec objects")
        document = spec.to_dict()
        violations = validate.validate_graphspec(document)
        if violations:
            detail = "; ".join(
                f"{item.rule} {item.path}: {item.message}"
                for item in violations[:3]
            )
            raise ValueError(
                "GraphSpec evidence does not satisfy the Motus contract: " + detail
            )
        snapshot = _plain_snapshot(validate, document)
        out.setdefault((snapshot["name"], snapshot["version"]), []).append(snapshot)
    return out


def _trace_snapshots(
    traces: Iterable[Trace],
    validate,
) -> tuple[dict[str, Any], ...]:
    """Snapshot and contract-validate every supplied Trace exactly once.

    A trace may legitimately be in flight or crash-recovered. Completion is
    unrelated to the graph identity used for a System Manifest binding, so the
    validator accepts incomplete evidence while retaining every other trace
    contract check.
    """
    out: list[dict[str, Any]] = []
    for trace in traces:
        if not isinstance(trace, Trace):
            raise TypeError("traces entries must be validated Trace objects")
        document = trace.to_dict()
        violations = validate.validate_trace(document, expect_complete=False)
        if violations:
            detail = "; ".join(
                f"{item.rule} {item.path}: {item.message}"
                for item in violations[:3]
            )
            raise ValueError(
                "trace evidence does not satisfy the Motus contract: " + detail
            )
        out.append(_plain_snapshot(validate, document))
    return tuple(out)


def _matching_trace_code_fingerprints(
    traces: Iterable[dict[str, Any]], graph_binding: dict[str, Any], trace_schema_version: str,
) -> tuple[str, ...]:
    observed: set[str] = set()
    for document in traces:
        run = document.get("run")
        graph = run.get("graph") if isinstance(run, dict) else None
        if not isinstance(graph, dict):
            continue
        if (
            document.get("schema_version") == trace_schema_version
            and graph.get("name") == graph_binding["name"]
            and graph.get("version") == graph_binding["version"]
            and graph.get("spec_schema_version") == graph_binding["spec_schema_version"]
            and graph.get("graph_fingerprint") == graph_binding["graph_fingerprint"]
        ):
            value = graph.get("code_fingerprint")
            if isinstance(value, str):
                observed.add(value)
    return tuple(sorted(observed))


def verify_system_manifest_bindings(
    manifest: dict[str, Any],
    *,
    graph_specs: Iterable[GraphSpec] = (),
    traces: Iterable[Trace] = (),
) -> SystemManifestBindingVerdict:
    """Compare a manifest with this distribution and supplied Motus artifacts.

    The manifest is contract-validated first. GraphSpecs and Traces must be
    validated Motus objects. code_fingerprint matching establishes agreement
    with trace-carried execution evidence; it does not independently recompute
    node code identity.
    """
    if not isinstance(manifest, dict):
        raise TypeError("manifest must be a dict")

    validate = _contract_validate()
    violations = tuple(validate.validate_system_manifest(manifest))
    if violations:
        return SystemManifestBindingVerdict(None, violations, ())

    # Freeze the exact declaration before consuming caller-provided iterables.
    # The fingerprint and every expected value below therefore refer to one
    # snapshot even if those iterables mutate the original manifest.
    manifest_document = _plain_snapshot(validate, manifest)
    fingerprint = validate.system_manifest_fingerprint(manifest_document)

    spec_by_identity = _spec_index(tuple(graph_specs), validate)
    trace_documents = _trace_snapshots(tuple(traces), validate)

    trace_join_work = (
        len(manifest_document["bindings"]["graphs"])
        * len(trace_documents)
    )
    if trace_join_work > _TRACE_JOIN_LIMIT:
        return SystemManifestBindingVerdict(
            fingerprint, (), (SystemManifestBindingFinding(
                "$.bindings.graphs", _NOT_VERIFIED,
                f"at most {_TRACE_JOIN_LIMIT} manifest/trace comparisons",
                str(trace_join_work),
                "binding verification exceeded the bounded trace join budget",
            ),),
        )

    findings: list[SystemManifestBindingFinding] = []
    motus = manifest_document["bindings"]["motus"]
    findings.append(_finding(
        "$.bindings.motus.runtime_version", motus["runtime_version"], __version__,
        source="the Motus distribution executing this verifier",
    ))
    findings.append(_finding(
        "$.bindings.motus.trace_schema_version", motus["trace_schema_version"], TRACE_SCHEMA_VERSION,
        source="this Motus distribution's TRACE_SCHEMA_VERSION",
    ))

    for index, graph in enumerate(manifest_document["bindings"]["graphs"]):
        prefix = f"$.bindings.graphs[{index}]"
        candidates = spec_by_identity.get((graph["name"], graph["version"]), [])
        if len(candidates) == 1:
            spec_document = candidates[0]
            recomputed_graph_fingerprint = validate.fingerprint(
                "graph", spec_document
            )
            findings.extend([
                _finding(
                    f"{prefix}.name", graph["name"], spec_document["name"],
                    source="the supplied GraphSpec name",
                ),
                _finding(
                    f"{prefix}.version", graph["version"], spec_document["version"],
                    source="the supplied GraphSpec version",
                ),
                _finding(
                    f"{prefix}.spec_schema_version",
                    graph["spec_schema_version"],
                    spec_document["schema_version"],
                    source="the supplied GraphSpec schema_version",
                ),
                _finding(
                    f"{prefix}.graph_fingerprint",
                    graph["graph_fingerprint"],
                    recomputed_graph_fingerprint,
                    source=(
                        "the fingerprint independently recomputed by the "
                        "contract from the supplied GraphSpec"
                    ),
                ),
            ])
        elif not candidates:
            for field in ("name", "version", "spec_schema_version", "graph_fingerprint"):
                findings.append(_finding(
                    f"{prefix}.{field}", graph[field], None,
                    source=f"GraphSpec with identity {graph['name']!r}/{graph['version']!r}",
                    missing=True,
                ))
        else:
            for field in ("name", "version", "spec_schema_version"):
                findings.append(SystemManifestBindingFinding(
                    f"{prefix}.{field}", _NOT_VERIFIED, graph[field], None,
                    f"{len(candidates)} supplied GraphSpecs share identity {graph['name']!r}/{graph['version']!r}; verifier refuses to choose one",
                ))
            graph_fingerprints = tuple(sorted({
                validate.fingerprint("graph", candidate)
                for candidate in candidates
            }))
            if len(graph_fingerprints) == 1:
                findings.append(SystemManifestBindingFinding(
                    f"{prefix}.graph_fingerprint", _NOT_VERIFIED,
                    graph["graph_fingerprint"], None,
                    f"{len(candidates)} supplied GraphSpecs are duplicate evidence "
                    f"for identity {graph['name']!r}/{graph['version']!r}; "
                    "verifier refuses to choose one",
                ))
            else:
                findings.append(SystemManifestBindingFinding(
                    f"{prefix}.graph_fingerprint", _MISMATCHED,
                    graph["graph_fingerprint"], graph_fingerprints,
                    "supplied GraphSpecs with the same name/version carry "
                    "conflicting canonical fingerprints; at least one "
                    "contradicts the manifest declaration",
                ))

        code_values = _matching_trace_code_fingerprints(
            trace_documents, graph, motus["trace_schema_version"]
        )
        if len(code_values) == 1:
            findings.append(_finding(
                f"{prefix}.code_fingerprint", graph["code_fingerprint"], code_values[0],
                source="the code_fingerprint carried by matching contract-valid Motus execution trace evidence",
            ))
        elif not code_values:
            findings.append(_finding(
                f"{prefix}.code_fingerprint", graph["code_fingerprint"], None,
                source="matching contract-valid Motus execution trace evidence", missing=True,
            ))
        else:
            findings.append(SystemManifestBindingFinding(
                f"{prefix}.code_fingerprint", _MISMATCHED,
                graph["code_fingerprint"], code_values,
                "matching supplied traces carry conflicting code_fingerprint values; "
                "at least one contradicts the manifest declaration, so the binding "
                "is a mismatch rather than missing evidence",
            ))

    return SystemManifestBindingVerdict(fingerprint, (), tuple(findings))
