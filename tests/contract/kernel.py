"""The kernel under test — the ONE file the implementing agent may edit here.

Everything else under ``tests/contract/`` and ``tests/compat/`` is frozen:
those files state what the runtime must do, and a runtime that cannot satisfy
them is wrong, not the tests.  But the corpus has to name *some* package, and
that name changes exactly once, when the runtime moves from ``axis`` to
``vitruvyan_motus``.  Rather than let that rename touch a hundred assertions,
it touches this file.

When Motus 0.5 lands, the compatibility view is what these names resolve to
(guarantees.md §4) — the legacy surface Terraveler is pinned against, not the
Motus-native one:

    from vitruvyan_motus.compat import (
        GraphState, Runner, Policy, NodeFailed, Fact, Decision, Rejection,
    )

If a name below cannot be provided by the new runtime's compatibility view,
that is a contract violation to be resolved by amendment (ADR), never by
editing the corpus that noticed it.
"""

from axis import GraphState, NodeFailed, Policy, Runner  # noqa: F401
from axis.persistence import FileTraceObserver  # noqa: F401
from axis.recovery import retry  # noqa: F401
from axis.state import Decision, Fact, Rejection  # noqa: F401
from axis.streaming import ConcurrentRunner  # noqa: F401

#: Which kernel this corpus ran against, for the record in failure output.
KERNEL_UNDER_TEST = "axis (pre-rename)"

__all__ = [
    "ConcurrentRunner",
    "Decision",
    "Fact",
    "FileTraceObserver",
    "GraphState",
    "KERNEL_UNDER_TEST",
    "NodeFailed",
    "Policy",
    "Rejection",
    "Runner",
    "retry",
]
