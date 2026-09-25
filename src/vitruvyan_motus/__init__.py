"""Vitruvyan Motus — an embeddable, trace-first graph execution runtime.

Every run records what it read, what it decided, what it rejected and what
it caused, so that a run can be explained deterministically and, later,
reproduced. The runtime is semantically neutral: it interprets nothing.

The full contract lives in ``contract/`` at the repository root — this
package is implemented inside it, never the other way around.
"""

# Two version numbers, deliberately never derived from one another: the
# distribution version below answers "which release of the runtime is
# this", the trace schema version answers "which version of the wire
# format did this trace commit to" (contract/trace.v1.schema.json). They
# will diverge the day the schema goes a release without changing while
# the runtime does, or the reverse — collapsing them into one constant is
# exactly the version confusion the contract exists to make impossible.
__version__ = "0.19.0"

#: Single-sourced trace schema version. Pinned equal to
#: ``contract/trace.v1.schema.json``'s ``properties.schema_version.const``
#: by ``tests/test_schema_version.py`` (contract/README.md, "One source per
#: fact"; ADR-003 §Decision 2).
#: 3.2.0 is ADR-030's rule J4: every number in a trace is a JSON integer
#: with |n| <= 2^53 - 1.
TRACE_SCHEMA_VERSION = "3.2.0"

from vitruvyan_motus.context import ContextDraw, ReplayStatus, RunContext
from vitruvyan_motus.effects import EffectClass, EffectDescriptor, EffectReceipt
from vitruvyan_motus.errors import (
    GraphSpecValidationError,
    GraphSpecViolation,
    MotusError,
    NodeConfigurationError,
    NodeFailed,
    SinkFailed,
    ReplayError,
    ReplayMismatch,
    ReplayUnsupported,
    UnsafeResume,
    DeclarationViolation,
)
from vitruvyan_motus.graph import CompiledPlan, GraphSpec, NodeDecl, Transition, TransitionKind
from vitruvyan_motus.observers import (
    AsyncStreamDriver,
    InMemoryTraceSink,
    Listener,
    StreamDriver,
    TraceRunSink,
    TraceSink,
)
from vitruvyan_motus.runtime import (
    DurabilityProfile, EvidenceStatus, Policy, RunResult, Runtime,
)
from vitruvyan_motus.sinks import JsonlTraceSink
from vitruvyan_motus.replay import ReplayEngine, ReplayResult, TraceBundle
from vitruvyan_motus.evidence import (
    PackageVerdict, evidence_package_fingerprint, pack, verify_package,
)
from vitruvyan_motus.evidence_api import EvidenceAPI, EvidenceSource, LiveEvidenceSource
from vitruvyan_motus.system_manifest import (
    SystemManifestBindingFinding, SystemManifestBindingVerdict,
    verify_system_manifest_bindings,
)
from vitruvyan_motus.risk_control import (
    ControlApplicationBindingFinding, ControlApplicationBindingVerdict,
    controls_for_risk, risks_for_control, verify_control_application_bindings,
)
from vitruvyan_motus.human_oversight import (
    HumanOversightBindingFinding, HumanOversightBindingVerdict,
    verify_human_oversight_bindings,
)
from vitruvyan_motus.regulatory_profile import (
    RegulatoryEvidenceAssessment, RegulatoryEvidenceFinding,
    assess_evidence_profile,
)
from vitruvyan_motus.incident_capa import (
    IncidentCAPAFinding, IncidentCAPAVerdict,
    order_incident_capa_entries, verify_incident_capa_ledger,
)
from vitruvyan_motus.retention import (
    RetentionFinding, RetentionArtifactIdentity, RetentionLineageVerdict,
    RetentionScopeVerdict, RetentionApplicationBindingVerdict,
    RetentionBlockerVerdict, verify_retention_lineage,
    resolve_supplied_retention_scope, verify_retention_application_bindings,
    evaluate_supplied_retention_blocker,
)
from vitruvyan_motus.state import State
from vitruvyan_motus.trace import (
    Decision, Fact, NonCanonicalNumber, NonIntegerNumber, RedactedValue,
    Rejection, Trace, redact,
)

__all__ = [
    "__version__", "TRACE_SCHEMA_VERSION",
    "ContextDraw", "ReplayStatus", "RunContext",
    "EffectClass", "EffectDescriptor", "EffectReceipt",
    "MotusError", "GraphSpecViolation", "GraphSpecValidationError",
    "NodeConfigurationError", "NodeFailed", "SinkFailed",
    "ReplayError", "ReplayMismatch", "ReplayUnsupported", "UnsafeResume",
    "DeclarationViolation",
    "GraphSpec", "NodeDecl", "Transition", "TransitionKind", "CompiledPlan",
    "TraceSink", "TraceRunSink", "Listener", "InMemoryTraceSink", "JsonlTraceSink",
    "StreamDriver",
    "AsyncStreamDriver",
    "Policy", "DurabilityProfile", "EvidenceStatus", "RunResult", "Runtime",
    "TraceBundle", "ReplayResult", "ReplayEngine",
    "pack", "verify_package", "evidence_package_fingerprint", "PackageVerdict",
    "EvidenceAPI", "EvidenceSource", "LiveEvidenceSource",
    "SystemManifestBindingFinding", "SystemManifestBindingVerdict",
    "verify_system_manifest_bindings",
    "ControlApplicationBindingFinding", "ControlApplicationBindingVerdict",
    "controls_for_risk", "risks_for_control",
    "verify_control_application_bindings",
    "HumanOversightBindingFinding", "HumanOversightBindingVerdict",
    "verify_human_oversight_bindings",
    "RegulatoryEvidenceAssessment", "RegulatoryEvidenceFinding",
    "assess_evidence_profile",
    "IncidentCAPAFinding", "IncidentCAPAVerdict",
    "order_incident_capa_entries", "verify_incident_capa_ledger",
    "RetentionFinding", "RetentionArtifactIdentity", "RetentionLineageVerdict",
    "RetentionScopeVerdict", "RetentionApplicationBindingVerdict",
    "RetentionBlockerVerdict", "verify_retention_lineage",
    "resolve_supplied_retention_scope", "verify_retention_application_bindings",
    "evaluate_supplied_retention_blocker",
    "State", "Trace", "Fact", "Decision", "Rejection", "RedactedValue", "redact",
    "NonCanonicalNumber", "NonIntegerNumber",
]
