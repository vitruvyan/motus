"""Effect classification (contract/node-protocol.md §4).

Milestone B scope only: the node-level classification GraphSpec carries.
``EffectDescriptor`` — the per-effect record attached to a *trace* transition
— appears nowhere in ``contract/graphspec.v1.schema.json``; it belongs to the
trace envelope built in a later milestone, once there is a transition record
for a descriptor to describe. Adding it here now, with no caller, would be
exactly the unused extension hook the contract forbids.
"""

from __future__ import annotations

from enum import Enum

__all__ = ["EffectClass"]


class EffectClass(str, Enum):
    """A node's declared (or defaulted) effect class.

    ``PURE``: output depends only on captured reads and context draws — no
    observable outside effect.
    ``RECORDED_EFFECT``: reads the outside world or calls a model/tool whose
    *result* is the effect.
    ``EXTERNAL_EFFECT``: mutates something outside the run. The conservative
    default for any node that declares no class at all.

    String-backed so a value compares equal to, and serializes as, its JSON
    form (``EffectClass.PURE == "pure"``) — the same convention the wire
    schemas use throughout the contract.
    """

    PURE = "pure"
    RECORDED_EFFECT = "recorded_effect"
    EXTERNAL_EFFECT = "external_effect"
