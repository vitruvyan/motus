"""Axis - Minimal cognitive graph kernel.

Provides:
    - Immutable GraphState (Primary Memory)
    - Node protocol (pure functions)
    - Runner (execution engine)
    - SynapticBus (Dual Memory observer)
    - Policy (STRICT, EXPLORATION)
    - Epistemic types and protocols

Phase status:
    Phase 1: Core kernel (FROZEN)
    Phase 2.1: Synaptic Bus (FROZEN)
    Phase 2.2: Epistemic types (COMPLETE)
    Phase 2.3: Production features (IN PROGRESS)
"""

__version__ = "0.3.0-dev"

# Core kernel
from axis.state import GraphState, Fact, Decision, Rejection, Event
from axis.events import EventType, now
from axis.node import Node
from axis.runner import Runner, NodeFailed
from axis.policy import Policy

# Synaptic Bus (Phase 2.1)
from axis.synaptic_bus import SynapticBus

# Epistemic types (Phase 2.2)
from axis.epistemic_types import (
    Category,
    Relation,
    Intent,
    Implication,
    Pattern,
    Constraint,
    Violation,
    EpistemicState,
)

# Epistemic protocols (Phase 2.2)
from axis.epistemic_protocols import (
    OntologyProvider,
    SemanticInterpreter,
    PatternDetector,
    ConstraintChecker,
    EpistemicMemory,
)

# Streaming (Phase 2.3)
from axis.streaming import (
    AsyncRunner,
    ConcurrentRunner,
)

# Audit Layer (Phase 1 Step 2)
from axis.audit import (
    SentinelAgent,
    AuditConfig,
    BackupMode,
    VaultStatus,
    AuditEvent,
)

__all__ = [
    # Core
    "GraphState",
    "Fact",
    "Decision",
    "Rejection",
    "Event",
    "EventType",
    "now",
    "Node",
    "Runner",
    "NodeFailed",
    "Policy",
    # Synaptic Bus
    "SynapticBus",
    # Epistemic types
    "Category",
    "Relation",
    "Intent",
    "Implication",
    "Pattern",
    "Constraint",
    "Violation",
    "EpistemicState",
    # Epistemic protocols
    "OntologyProvider",
    "SemanticInterpreter",
    "PatternDetector",
    "ConstraintChecker",
    "EpistemicMemory",
    # Streaming
    "AsyncRunner",
    "ConcurrentRunner",
    # Audit Layer
    "SentinelAgent",
    "AuditConfig",
    "BackupMode",
    "VaultStatus",
    "AuditEvent",
]
