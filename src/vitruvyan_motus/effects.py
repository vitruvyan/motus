"""Node effect classification and trace effect descriptors.

The vocabulary is fixed by ``contract/node-protocol.md`` section 4 and the
trace schema. Motus 0.6 adds adapter-supplied receipts and fail-closed resume
without claiming exactly-once delivery.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any

__all__ = ["EffectClass", "EffectReceipt", "EffectDescriptor"]


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
class EffectReceipt:
    """Opaque evidence returned by an effect adapter.

    ``completed`` means the adapter reports a terminal outcome; ``unknown``
    preserves uncertainty. Motus records the assertion and never upgrades it
    into an exactly-once claim.
    """

    receipt_id: str
    status: str = "completed"
    result_fingerprint: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.receipt_id, str) or not self.receipt_id:
            raise ValueError("receipt_id must be a non-empty string")
        if self.status not in ("completed", "unknown"):
            raise ValueError("receipt status must be 'completed' or 'unknown'")
        if self.result_fingerprint is not None:
            value = self.result_fingerprint
            prefix = "effect:sha256:"
            if not isinstance(value, str) or not value.startswith(prefix):
                raise ValueError("result_fingerprint must use effect:sha256:<hex>")
            digest = value[len(prefix):]
            if len(digest) != 64 or any(c not in "0123456789abcdef" for c in digest):
                raise ValueError("result_fingerprint must contain a lowercase SHA-256 digest")

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {"receipt_id": self.receipt_id, "status": self.status}
        if self.result_fingerprint is not None:
            out["result_fingerprint"] = self.result_fingerprint
        return out


@dataclass(frozen=True, slots=True)
class EffectDescriptor:
    """One recorded or external effect described by a transition."""

    effect_class: EffectClass
    description: str
    idempotency_key: str | None | object = _MISSING
    receipt: EffectReceipt | None = None

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
        if self.receipt is not None and not isinstance(self.receipt, EffectReceipt):
            raise TypeError("receipt must be EffectReceipt or null")

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {
            "class": self.effect_class.value,
            "description": self.description,
        }
        if self.idempotency_key is not _MISSING:
            out["idempotency_key"] = self.idempotency_key
        if self.receipt is not None:
            out["receipt"] = self.receipt.to_dict()
        return out
