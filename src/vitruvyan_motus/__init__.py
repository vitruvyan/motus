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
__version__ = "0.5.0"

#: Single-sourced trace schema version. Pinned equal to
#: ``contract/trace.v1.schema.json``'s ``properties.schema_version.const``
#: by ``tests/test_schema_version.py`` (contract/README.md, "One source per
#: fact"; ADR-003 §Decision 2).
TRACE_SCHEMA_VERSION = "1.0.0"

__all__ = ["__version__", "TRACE_SCHEMA_VERSION"]
