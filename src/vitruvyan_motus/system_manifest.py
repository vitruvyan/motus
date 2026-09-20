"""ADR-035 System Manifest binding verification.

Document validation and binding verification are distinct operations. A valid
manifest says what an operator declared; this module compares selected bindings
against Motus artifacts without making compliance or certification claims.

The contract validator is imported lazily so importing vitruvyan_motus still
pulls no third-party module.
"""
from __future__ import annotations

import importlib
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Iterable

from vitruvyan_motus import TRACE_SCHEMA_VERSION, __version__
from vitruvyan_motus.graph import GraphSpec
from vitruvyan_motus.trace import Trace

if TYPE_CHECKING:
    from vitruvyan_motus.contract.validate import Violation

__all__ = [
    "MATCHED", "MISMATCHED", "NOT_VERIFIED",
    "SystemManifestBindingFinding", "SystemManifestBindingVerdict",
    "verify_system_manifest_bindings",
]

MATCHED = "matched"
MISMATCHED = "mismatched"
NOT_VERIFIED = "not verified"


@dataclass(frozen=True, slots=True)
class SystemManifestBindingFinding:
    path: str
    status: str
    expected: str | None
    observed: str | None
    reason: str


@dataclass(frozen=True, slots=True)
class SystemManifestBindingVerdict:
    """Binding result; complete is not a compliance or certification verdict."""

    manifest_fingerprint: str | None
    manifest_violations: tuple["Violation", ...]
    findings: tuple[SystemManifestBindingFinding, ...]

    @property
    def complete(self) -> bool:
        return (
            not self.manifest_violations
            and bool(self.findings)
            and all(item.status == MATCHED for item in self.findings)
        )

    @property
    def has_mismatch(self) -> bool:
        return any(item.status == MISMATCHED for item in self.findings)

    @property
    def has_unverified(self) -> bool:
        return any(item.status == NOT_VERIFIED for item in self.findings)


def _contract_validate():
    return importlib.import_module("vitruvyan_motus.contract.validate")


def _finding(path: str, expected: str | None, observed: str | None, *, source: str, missing: bool = False) -> SystemManifestBindingFinding:
    if missing:
        return SystemManifestBindingFinding(
            path, NOT_VERIFIED, expected, None,
            f"no {source} was supplied that can settle this binding",
        )
    status = MATCHED if expected == observed else MISMATCHED
    verb = "matches" if status == MATCHED else "does not match"
    return SystemManifestBindingFinding(
        path, status, expected, observed, f"manifest value {verb} {source}",
    )


def _spec_index(specs: Iterable[GraphSpec]) -> dict[tuple[str, str], list[GraphSpec]]:
    out: dict[tuple[str, str], list[GraphSpec]] = {}
    for spec in specs:
        if not isinstance(spec, GraphSpec):
            raise TypeError("graph_specs entries must be validated GraphSpec objects")
        out.setdefault((spec.name, spec.version), []).append(spec)
    return out


def _trace_graph(trace: Trace) -> dict[str, Any]:
    if not isinstance(trace, Trace):
        raise TypeError("traces entries must be validated Trace objects")
    graph = trace.run.get("graph")
    return graph if isinstance(graph, dict) else {}


def _matching_trace_code_fingerprints(traces: Iterable[Trace], graph_binding: dict[str, Any]) -> tuple[str, ...]:
    observed: set[str] = set()
    for trace in traces:
        graph = _trace_graph(trace)
        if (
            graph.get("name") == graph_binding["name"]
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

    fingerprint = validate.system_manifest_fingerprint(manifest)
    specs = tuple(graph_specs)
    trace_items = tuple(traces)
    spec_by_identity = _spec_index(specs)

    # A Trace instance alone is not proof of contract validity: Trace.from_dict
    # intentionally performs only the dependency-free reader checks and does
    # not reimplement the full executable contract. Refuse invalid evidence
    # here before any of its graph fields can produce a successful binding.
    for trace in trace_items:
        if not isinstance(trace, Trace):
            raise TypeError("traces entries must be validated Trace objects")
        graph = _trace_graph(trace)
        candidates = spec_by_identity.get(
            (graph.get("name"), graph.get("version")), []
        )
        spec_document = candidates[0].to_dict() if len(candidates) == 1 else None
        trace_violations = validate.validate_trace(
            trace.to_dict(), spec=spec_document
        )
        if trace_violations:
            detail = "; ".join(
                f"{item.rule} {item.path}: {item.message}"
                for item in trace_violations[:3]
            )
            raise ValueError(
                "trace evidence does not satisfy the Motus contract: " + detail
            )

    findings: list[SystemManifestBindingFinding] = []
    motus = manifest["bindings"]["motus"]
    findings.append(_finding(
        "$.bindings.motus.runtime_version", motus["runtime_version"], __version__,
        source="the Motus distribution executing this verifier",
    ))
    findings.append(_finding(
        "$.bindings.motus.trace_schema_version", motus["trace_schema_version"], TRACE_SCHEMA_VERSION,
        source="this Motus distribution's TRACE_SCHEMA_VERSION",
    ))

    for index, graph in enumerate(manifest["bindings"]["graphs"]):
        prefix = f"$.bindings.graphs[{index}]"
        candidates = spec_by_identity.get((graph["name"], graph["version"]), [])
        if len(candidates) == 1:
            spec = candidates[0]
            findings.extend([
                _finding(f"{prefix}.name", graph["name"], spec.name, source="the supplied GraphSpec name"),
                _finding(f"{prefix}.version", graph["version"], spec.version, source="the supplied GraphSpec version"),
                _finding(f"{prefix}.spec_schema_version", graph["spec_schema_version"], spec.schema_version, source="the supplied GraphSpec schema_version"),
                _finding(f"{prefix}.graph_fingerprint", graph["graph_fingerprint"], spec.graph_fingerprint, source="the fingerprint recomputed from the supplied GraphSpec"),
            ])
        elif not candidates:
            for field in ("name", "version", "spec_schema_version", "graph_fingerprint"):
                findings.append(_finding(
                    f"{prefix}.{field}", graph[field], None,
                    source=f"GraphSpec with identity {graph['name']!r}/{graph['version']!r}",
                    missing=True,
                ))
        else:
            for field in ("name", "version", "spec_schema_version", "graph_fingerprint"):
                findings.append(SystemManifestBindingFinding(
                    f"{prefix}.{field}", NOT_VERIFIED, graph[field], None,
                    f"{len(candidates)} supplied GraphSpecs share identity {graph['name']!r}/{graph['version']!r}; verifier refuses to choose one",
                ))

        code_values = _matching_trace_code_fingerprints(trace_items, graph)
        if len(code_values) == 1:
            findings.append(_finding(
                f"{prefix}.code_fingerprint", graph["code_fingerprint"], code_values[0],
                source="the code_fingerprint carried by matching validated Motus execution trace evidence",
            ))
        elif not code_values:
            findings.append(_finding(
                f"{prefix}.code_fingerprint", graph["code_fingerprint"], None,
                source="matching validated Motus execution trace evidence", missing=True,
            ))
        else:
            findings.append(SystemManifestBindingFinding(
                f"{prefix}.code_fingerprint", NOT_VERIFIED, graph["code_fingerprint"], None,
                "matching supplied traces carry multiple distinct code_fingerprint values; verifier refuses to choose one",
            ))

    return SystemManifestBindingVerdict(fingerprint, (), tuple(findings))
