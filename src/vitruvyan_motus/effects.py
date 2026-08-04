"""Node effect classification and trace effect descriptors.

The vocabulary is fixed by ``contract/node-protocol.md`` section 4 and the
trace schema. Classification is descriptive in 0.5; effect receipts and
delivery enforcement are deliberately out of scope.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any

__all__ = ["EffectClass", "EffectDescriptor"]


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


_MISSING = object()


@dataclass(frozen=True, slots=True)
class EffectDescriptor:
    """One recorded or external effect described by a transition."""

    effect_class: EffectClass
    description: str
    idempotency_key: str | None | object = _MISSING

    def __post_init__(self) -> None:
        if self.effect_class not in (
            EffectClass.RECORDED_EFFECT, EffectClass.EXTERNAL_EFFECT
        ):
            raise ValueError("an effect descriptor cannot have class pure")
        if not isinstance(self.description, str):
            raise TypeError("effect description must be a string")
        if (
            self.idempotency_key is not _MISSING
            and self.idempotency_key is not None
            and not isinstance(self.idempotency_key, str)
        ):
            raise TypeError("idempotency_key must be a string or null")

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {
            "class": self.effect_class.value,
            "description": self.description,
        }
        if self.idempotency_key is not _MISSING:
            out["idempotency_key"] = self.idempotency_key
        return out
