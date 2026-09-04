"""Run-scoped time, identity and mediated node nondeterminism.

Nodes receive :class:`RunContext`, a deliberately small capability exposing
only ``now()``, ``rand()`` and ``uuid()``.  The executor owns
``_RunController``: sequence allocation, kernel time/identity, draw cursors
and replay degradation never cross the node boundary.
"""

from __future__ import annotations

import math
import random
import uuid as uuid_module
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Callable, Iterable, Literal

from vitruvyan_motus.effects import EffectDescriptor

__all__ = ["ContextDraw", "ReplayStatus", "RunContext"]

DrawSource = Literal["now", "rand", "uuid"]
ReplayCapability = Literal["full", "partial", "none"]
_CAPABILITY_RANK = {"none": 0, "partial": 1, "full": 2}


def _timestamp(value: datetime) -> str:
    if not isinstance(value, datetime):
        raise TypeError("the run clock must return datetime instances")
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("the run clock must return timezone-aware datetimes")
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


@dataclass(frozen=True, slots=True)
class ContextDraw:
    """One node-visible nondeterministic value captured by the trace."""

    source: DrawSource
    value: str | float

    def __post_init__(self) -> None:
        if self.source not in ("now", "rand", "uuid"):
            raise ValueError(f"unsupported context draw source {self.source!r}")
        if self.source == "rand":
            if isinstance(self.value, bool) or not isinstance(self.value, (int, float)):
                raise TypeError("a rand draw must be a JSON number")
            numeric = float(self.value)
            if not math.isfinite(numeric) or not 0.0 <= numeric < 1.0:
                raise ValueError("a rand draw must be finite and in [0, 1)")
        elif not isinstance(self.value, str):
            raise TypeError(f"a {self.source} draw must be a string")

    def to_dict(self) -> dict[str, str | float]:
        return {"source": self.source, "value": self.value}


@dataclass(frozen=True, slots=True)
class ReplayStatus:
    """The explicit replay promise carried by a run.

    ``full`` has no constraints. ``partial`` and ``none`` always explain why.
    Constraints are canonicalized so trace output is stable.
    """

    capability: ReplayCapability
    constraints: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if self.capability not in _CAPABILITY_RANK:
            raise ValueError(f"unsupported replay capability {self.capability!r}")
        if isinstance(self.constraints, (str, bytes)):
            raise TypeError("replay constraints must be an iterable of strings")
        normalized: list[str] = []
        for constraint in self.constraints:
            if not isinstance(constraint, str):
                raise TypeError("every replay constraint must be a string")
            if not constraint:
                raise ValueError("replay constraints must not be empty")
            if constraint not in normalized:
                normalized.append(constraint)
        normalized.sort()
        object.__setattr__(self, "constraints", tuple(normalized))
        if self.capability == "full" and normalized:
            raise ValueError("full replay capability cannot carry constraints")
        if self.capability != "full" and not normalized:
            raise ValueError(f"{self.capability} replay capability requires a constraint")

    @classmethod
    def declared(
        cls, capability: ReplayCapability = "none", constraints: Iterable[str] | None = None
    ) -> "ReplayStatus":
        if constraints is None:
            constraints = () if capability == "full" else ("undeclared",)
        return cls(capability, tuple(constraints))

    def to_dict(self) -> dict[str, object]:
        return {"capability": self.capability, "constraints": list(self.constraints)}


class RunContext:
    """The complete node-facing control surface.

    It intentionally has no sequence, kernel-clock, replay or draw-inspection
    methods.  A node can request nondeterminism; only the executor can account
    for and classify it.
    """

    __slots__ = ("__now", "__rand", "__uuid", "__record_effect")

    def __init__(
        self,
        now: Callable[[], datetime],
        rand: Callable[[], float],
        uuid: Callable[[], str],
        record_effect: Callable[[EffectDescriptor], None],
    ) -> None:
        self.__now = now
        self.__rand = rand
        self.__uuid = uuid
        self.__record_effect = record_effect

    def now(self) -> datetime:
        return self.__now()

    def rand(self) -> float:
        return self.__rand()

    def uuid(self) -> str:
        return self.__uuid()

    def record_effect(self, effect: EffectDescriptor) -> None:
        """Attach one adapter-supplied effect descriptor to this attempt."""
        self.__record_effect(effect)


class _RunController:
    """Executor-owned half of a run context (not a public package surface)."""

    __slots__ = (
        "_clock", "_identity", "_random", "_draws", "_effects", "_next_sequence",
        "_declared_replay", "_replay", "_node_context", "_effect_class",
    )

    def __init__(
        self,
        *,
        clock: Callable[[], datetime] | None = None,
        identity: Callable[[], str] | None = None,
        random_source: Callable[[], float] | None = None,
        replay: ReplayStatus | None = None,
    ) -> None:
        self._clock = clock or (lambda: datetime.now(timezone.utc))
        self._identity = identity or (lambda: str(uuid_module.uuid4()))
        self._random = random_source or random.random
        self._draws: list[ContextDraw] = []
        self._effects: list[EffectDescriptor] = []
        self._effect_class = "external_effect"
        self._next_sequence = 1
        self._declared_replay = replay or ReplayStatus.declared()
        self._replay = self._declared_replay
        self._node_context = RunContext(
            self._node_now, self._node_rand, self._node_uuid, self._record_effect
        )

    @property
    def node_context(self) -> RunContext:
        return self._node_context

    @property
    def declared_replay(self) -> ReplayStatus:
        return self._declared_replay

    @property
    def replay(self) -> ReplayStatus:
        return self._replay

    def kernel_now(self) -> datetime:
        value = self._clock()
        _timestamp(value)
        return value.astimezone(timezone.utc)

    def timestamp(self) -> str:
        return _timestamp(self._clock())

    def kernel_uuid(self) -> str:
        value = self._identity()
        if not isinstance(value, str) or not value:
            raise ValueError("the run identity source must return a non-empty string")
        return value

    def next_seq(self) -> int:
        value = self._next_sequence
        self._next_sequence += 1
        return value

    def draw_cursor(self) -> int:
        return len(self._draws)

    def draws_since(self, cursor: int) -> tuple[ContextDraw, ...]:
        if isinstance(cursor, bool) or not isinstance(cursor, int):
            raise TypeError("draw cursor must be an integer")
        if cursor < 0 or cursor > len(self._draws):
            raise ValueError("draw cursor is outside the current draw log")
        return tuple(self._draws[cursor:])

    def effect_cursor(self) -> int:
        return len(self._effects)

    def effects_since(self, cursor: int) -> tuple[EffectDescriptor, ...]:
        if isinstance(cursor, bool) or not isinstance(cursor, int):
            raise TypeError("effect cursor must be an integer")
        if cursor < 0 or cursor > len(self._effects):
            raise ValueError("effect cursor is outside the current effect log")
        return tuple(self._effects[cursor:])

    def _record_effect(self, effect: EffectDescriptor) -> None:
        if not isinstance(effect, EffectDescriptor):
            raise TypeError("record_effect requires EffectDescriptor")
        if self._effect_class == "pure":
            raise ValueError("a pure node cannot record effects")
        if (
            self._effect_class == "recorded_effect"
            and effect.effect_class.value == "external_effect"
        ):
            raise ValueError("a recorded_effect node cannot record external effects")
        self._effects.append(effect)

    def begin_effect_scope(self, effect_class: str) -> None:
        if effect_class not in ("pure", "recorded_effect", "external_effect"):
            raise ValueError("unsupported node effect class")
        self._effect_class = effect_class

    def downgrade(self, capability: ReplayCapability, constraint: str) -> ReplayStatus:
        if capability not in _CAPABILITY_RANK:
            raise ValueError(f"unsupported replay capability {capability!r}")
        if not isinstance(constraint, str):
            raise TypeError("replay constraint must be a string")
        if not constraint:
            raise ValueError("replay constraint must not be empty")
        if _CAPABILITY_RANK[capability] > _CAPABILITY_RANK[self._replay.capability]:
            raise ValueError("replay capability may only degrade")
        constraints = set(self._replay.constraints)
        constraints.add(constraint)
        self._replay = ReplayStatus(capability, tuple(constraints))
        return self._replay

    def downgrade_many(
        self, degradations: Iterable[tuple[ReplayCapability, str]]
    ) -> ReplayStatus:
        """Apply construction-time constraints in one canonicalization pass."""
        capability = self._replay.capability
        constraints = set(self._replay.constraints)
        for proposed, constraint in degradations:
            if proposed not in _CAPABILITY_RANK:
                raise ValueError(f"unsupported replay capability {proposed!r}")
            if not isinstance(constraint, str):
                raise TypeError("replay constraint must be a string")
            if not constraint:
                raise ValueError("replay constraint must not be empty")
            if _CAPABILITY_RANK[proposed] < _CAPABILITY_RANK[capability]:
                capability = proposed
            constraints.add(constraint)
        if constraints:
            self._replay = ReplayStatus(capability, tuple(constraints))
        return self._replay

    def _node_now(self) -> datetime:
        value = self.kernel_now()
        self._draws.append(ContextDraw("now", value.isoformat().replace("+00:00", "Z")))
        return value

    def _node_rand(self) -> float:
        value = self._random()
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise TypeError("the run random source must return a number")
        numeric = float(value)
        draw = ContextDraw("rand", numeric)
        self._draws.append(draw)
        return numeric

    def _node_uuid(self) -> str:
        value = self.kernel_uuid()
        self._draws.append(ContextDraw("uuid", value))
        return value
