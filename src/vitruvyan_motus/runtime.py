"""The single Motus executor and the trace-v1 execution state machine."""

from __future__ import annotations

import copy
import functools
import hashlib
import inspect
import json
import threading
from dataclasses import dataclass
from enum import Enum
from types import MappingProxyType
from typing import Any, AsyncIterator, Awaitable, Callable, Iterator, Mapping

from vitruvyan_motus import TRACE_SCHEMA_VERSION, __version__
from vitruvyan_motus.context import ReplayStatus, _RunController
from vitruvyan_motus.effects import EffectClass
from vitruvyan_motus.errors import (
    DeclarationViolation,
    NodeConfigurationError,
    NodeFailed,
    SinkFailed,
)
from vitruvyan_motus.graph import GraphSpec, NodeDecl, TransitionKind
from vitruvyan_motus.observers import (
    AsyncStreamDriver,
    Listener,
    StreamDriver,
    TraceSink,
    _ObservationHub,
)
from vitruvyan_motus.state import State
from vitruvyan_motus.trace import Trace, _canonical_bytes, _strict_plain_json

__all__ = ["Policy", "DurabilityProfile", "RunResult", "Runtime"]


class Policy(str, Enum):
    STRICT = "strict"
    EXPLORATION = "exploration"


class DurabilityProfile(str, Enum):
    IN_MEMORY = "in-memory"
    BUFFERED = "buffered"
    SYNCHRONOUS = "synchronous"


@dataclass(frozen=True, slots=True)
class RunResult:
    state: State
    trace: Trace

    @property
    def status(self) -> str:
        """The explicit terminal status: completed, failed or cancelled."""
        terminal = self.trace.records[-1]["kind"]
        return terminal.removeprefix("run_")

    @property
    def succeeded(self) -> bool:
        return self.status == "completed"


def _config_material(node: Callable[..., Any]) -> tuple[tuple[str, Any], bool]:
    """Return isolated comparable config material and whether it is opaque."""
    if isinstance(node, functools.partial):
        value = {"args": list(node.args), "keywords": node.keywords or {}}
        return ("value", _strict_plain_json(value)), False
    if not inspect.isfunction(node) and not inspect.ismethod(node):
        config = getattr(node, "motus_config", None)
        if callable(config):
            return ("value", _strict_plain_json(config())), False
        return ("marker", "opaque"), True
    target = inspect.unwrap(node)
    if inspect.isfunction(target) and target.__closure__ is None and "<locals>" not in target.__qualname__:
        return ("marker", "none"), False
    if inspect.ismethod(target) and target.__self__ is not None:
        config = getattr(target.__self__, "motus_config", None)
        if callable(config):
            return ("value", _strict_plain_json(config())), False
    return ("marker", "opaque"), True


def _config_fingerprint(material: tuple[str, Any]) -> str:
    kind, value = material
    if kind == "marker":
        return value
    return "config:sha256:" + hashlib.sha256(_canonical_bytes(value)).hexdigest()


@functools.lru_cache(maxsize=4096)
def _static_callable_identity(target: Callable[..., Any]) -> tuple[str, str, bool]:
    """Cache immutable source identity; mutable config is never cached."""
    qualified = f"{getattr(target, '__module__', type(target).__module__)}.{getattr(target, '__qualname__', type(target).__qualname__)}"
    unavailable = False
    try:
        source = inspect.getsource(target).encode("utf-8")
        source_hash = hashlib.sha256(source).hexdigest()
    except (OSError, TypeError):
        source_hash = "unavailable"
        unavailable = True
    return qualified, source_hash, unavailable


def _node_identity_parts(
    node: Callable[..., Any],
) -> tuple[tuple[str, str, bool], tuple[str, Any], bool]:
    target = inspect.unwrap(node.func if isinstance(node, functools.partial) else node)
    if not inspect.isfunction(target) and not inspect.ismethod(target) and callable(target):
        target = inspect.unwrap(type(target).__call__)
    static = _static_callable_identity(target)
    material, opaque = _config_material(node)
    return static, material, opaque


def _accepts_context(node: Callable[..., Any]) -> bool:
    signature = inspect.signature(node)
    positional = [
        parameter for parameter in signature.parameters.values()
        if parameter.kind in (parameter.POSITIONAL_ONLY, parameter.POSITIONAL_OR_KEYWORD)
    ]
    required = [parameter for parameter in positional if parameter.default is parameter.empty]
    has_varargs = any(
        parameter.kind == parameter.VAR_POSITIONAL
        for parameter in signature.parameters.values()
    )
    if has_varargs or len(positional) not in (1, 2) or len(required) > len(positional):
        raise TypeError(
            f"node {node!r} must have signature (state) or (state, ctx)"
        )
    return (
        len(positional) == 2
        and positional[1].default is positional[1].empty
    )


@dataclass(frozen=True, slots=True)
class _Invoke:
    """One request from the state machine to run one node.

    The executor never calls a node itself: it asks, and a *driver* answers.
    That inversion is what keeps guarantees.md invariant I true by
    construction rather than by test — there is a single state machine, and
    the synchronous and asynchronous drivers differ only in how they obtain
    the node's result. No second engine exists to diverge from the first.
    """

    node: Callable[..., Any]
    args: tuple[Any, ...]
    name: str


def _invoke_sync(request: _Invoke) -> tuple[Any, BaseException | None]:
    """Answer one invocation request on the calling thread.

    Only ``Exception`` is captured as node failure, exactly as a direct call
    did: a ``BaseException`` must still tear the run down and leave the
    ``attempt_started`` unclosed (node-protocol.md §7.3).
    """
    try:
        returned = request.node(*request.args)
    except Exception as exc:  # noqa: BLE001 - this IS the node failure boundary
        return None, exc
    if inspect.isawaitable(returned):
        if hasattr(returned, "close"):
            returned.close()
        return None, TypeError(
            f"node {request.name!r} is asynchronous; drive it with "
            "Runtime.arun() or Runtime.astream()"
        )
    return returned, None


def _drive(
    machine: Iterator[Any], invoke: Callable[[_Invoke], tuple[Any, BaseException | None]]
) -> Iterator[dict[str, Any]]:
    """Advance the state machine, servicing its invocation requests.

    Yields only trace records, so every consumer above this layer — including
    ``StreamDriver`` — sees exactly the stream it always saw.
    """
    reply: Any = None
    while True:
        try:
            item = machine.send(reply)
        except StopIteration:
            return
        if type(item) is _Invoke:
            try:
                reply = invoke(item)
            except BaseException as exc:
                # Re-raise inside the machine so its `finally` runs at the
                # same point a direct call would have raised.
                machine.throw(exc)
                raise
            continue
        reply = None
        yield item


async def _invoke_async(request: _Invoke) -> tuple[Any, BaseException | None]:
    """Answer one invocation request, awaiting the node when it is awaitable.

    A graph may mix shapes freely: an ordinary ``def`` node is called, an
    ``async def`` node is awaited, and both produce the same transition
    record. Only ``Exception`` is captured as node failure, matching
    :func:`_invoke_sync`; ``asyncio.CancelledError`` is a ``BaseException``
    and therefore tears the run down rather than becoming a raised attempt.
    """
    try:
        returned = request.node(*request.args)
        if inspect.isawaitable(returned):
            returned = await returned
    except Exception as exc:  # noqa: BLE001 - this IS the node failure boundary
        return None, exc
    return returned, None


async def _adrive(
    machine: Iterator[Any],
    invoke: Callable[[_Invoke], Awaitable[tuple[Any, BaseException | None]]],
) -> AsyncIterator[dict[str, Any]]:
    """The asynchronous driver of the same synchronous state machine.

    ``machine`` is the very generator :meth:`Runtime._execute` returns — not a
    second implementation of it. Nothing here decides anything about
    execution; it only awaits what the machine asked for and hands the answer
    back, so the emitted record stream is the interpreter's, unchanged.
    """
    reply: Any = None
    while True:
        try:
            item = machine.send(reply)
        except StopIteration:
            return
        if type(item) is _Invoke:
            try:
                reply = await invoke(item)
            except BaseException as exc:
                machine.throw(exc)
                raise
            continue
        reply = None
        yield item


class Runtime:
    """Interpret one GraphSpec with exactly one execution semantics."""

    def __init__(
        self,
        spec: GraphSpec,
        nodes: Mapping[str, Callable[..., State]],
        *,
        policy: Policy | str = Policy.STRICT,
        durability_profile: DurabilityProfile | str = DurabilityProfile.IN_MEMORY,
        sink: TraceSink | None = None,
        listeners: tuple[Listener | Callable[[dict[str, Any]], None], ...] = (),
        max_attempts: int | Mapping[str, int] = 1,
        chunk_records: int = 64,
        flush_interval_ms: int = 1000,
        clock: Callable[[], Any] | None = None,
        identity: Callable[[], str] | None = None,
        random_source: Callable[[], float] | None = None,
    ) -> None:
        if not isinstance(spec, GraphSpec):
            raise TypeError("spec must be GraphSpec")
        self.spec = spec
        self._plan = spec.compiled
        self._graph_fingerprint = spec.graph_fingerprint
        self._nodes = dict(nodes)
        declared = {node.name for node in spec.nodes}
        if set(self._nodes) != declared:
            missing = sorted(declared - set(self._nodes))
            extra = sorted(set(self._nodes) - declared)
            raise ValueError(f"node registry must match GraphSpec; missing={missing}, extra={extra}")
        self._uses_context = {name: _accepts_context(node) for name, node in self._nodes.items()}
        self._declarations = self._plan.declarations
        self._identity_cache: dict[
            str,
            tuple[tuple[str, str, bool], tuple[str, Any], list[str], bool],
        ] = {}
        self._code_fingerprint = ""
        self._identity_constraints: tuple[tuple[str, str], ...] = ()
        self._refresh_identity()
        self._policy = Policy(policy)
        self._durability_profile = DurabilityProfile(durability_profile)
        if isinstance(max_attempts, Mapping):
            attempts = dict(max_attempts)
        else:
            attempts = {name: max_attempts for name in declared}
        for name in declared:
            value = attempts.get(name, 1)
            if isinstance(value, bool) or not isinstance(value, int) or value < 1:
                raise ValueError("max_attempts values must be integers >= 1")
        self._max_attempts = {name: attempts.get(name, 1) for name in declared}
        self._hub_args = dict(
            profile=self._durability_profile.value,
            sink=sink,
            listeners=tuple(listeners),
            chunk_records=chunk_records,
            flush_interval_ms=flush_interval_ms,
        )
        self._source_args = dict(clock=clock, identity=identity, random_source=random_source)
        self._trace: Trace | None = None
        self._state: State | None = None
        self._control: _RunController | None = None
        self._hub: _ObservationHub | None = None
        self._cancel_reason: str | None = None
        self._pending_cancel_reason: str | None = None
        self._active_attempt: tuple[str, int] | None = None
        self._lifecycle_lock = threading.RLock()
        self._running = False
        self._has_started = False
        self._trace_ref: list[Trace | None] | None = None

    def _refresh_identity(self) -> None:
        """Refresh only config rows whose observable material changed."""
        rows: list[list[str]] = []
        constraints: list[tuple[str, str]] = []
        changed = not self._identity_cache
        for declaration in self._declarations.values():
            name = declaration.name
            try:
                static, material, opaque = _node_identity_parts(self._nodes[name])
            except Exception as exc:
                raise NodeConfigurationError(name, exc) from exc
            unavailable = static[2]
            cached = self._identity_cache.get(name)
            if cached is not None and cached[0] == static and cached[1] == material:
                row = cached[2]
            else:
                qualified, source_hash, _ = static
                row = [name, qualified, source_hash, _config_fingerprint(material)]
                self._identity_cache[name] = (static, material, row, opaque)
                changed = True
            rows.append(row)
            if opaque:
                constraints.append(("partial", f"node:{name}:opaque_config"))
            if unavailable:
                constraints.append(("partial", f"node:{name}:source_unavailable"))
        current_constraints = tuple(constraints)
        if changed or current_constraints != self._identity_constraints:
            self._code_fingerprint = (
                "code:sha256:" + hashlib.sha256(_canonical_bytes(rows)).hexdigest()
            )
            self._identity_constraints = current_constraints

    @property
    def nodes(self) -> Mapping[str, Callable[..., State]]:
        """The frozen registry whose contents produced the code fingerprint."""
        return MappingProxyType(self._nodes)

    @property
    def policy(self) -> Policy:
        return self._policy

    @property
    def durability_profile(self) -> DurabilityProfile:
        return self._durability_profile

    @property
    def trace(self) -> Trace | None:
        return self._trace

    def cancel(self, reason: str = "cancelled by caller") -> bool:
        """Cancel the active run, or queue cancellation before first use.

        Once this Runtime has executed, an idle cancellation returns ``False``
        instead of leaking into an unrelated future run.  ``True`` means the
        request was bound to the active run or to the first run not yet begun.
        """
        if not isinstance(reason, str):
            raise TypeError("cancellation reason must be a string")
        with self._lifecycle_lock:
            if self._running:
                self._cancel_reason = reason
                return True
            if not self._has_started:
                self._pending_cancel_reason = reason
                return True
            return False

    def run(
        self,
        state: State | None = None,
        *,
        run_id: str | None = None,
        replay: ReplayStatus | None = None,
    ) -> RunResult:
        machine = self._start(
            state, run_id=run_id, replay=replay, copy_yields=False,
            start_node=self._plan.entry, resume_info=None,
        )
        for _ in _drive(machine, _invoke_sync):
            pass
        assert self._state is not None and self._trace is not None
        return RunResult(self._state, self._trace)

    def stream(
        self,
        state: State | None = None,
        *,
        run_id: str | None = None,
        replay: ReplayStatus | None = None,
    ) -> StreamDriver:
        machine = self._start(
            state, run_id=run_id, replay=replay, copy_yields=True,
            start_node=self._plan.entry, resume_info=None,
        )
        assert self._trace_ref is not None
        trace_ref = self._trace_ref
        return StreamDriver(_drive(machine, _invoke_sync), self.cancel, lambda: trace_ref[0])

    async def arun(
        self,
        state: State | None = None,
        *,
        run_id: str | None = None,
        replay: ReplayStatus | None = None,
    ) -> RunResult:
        """Execute the graph, awaiting nodes that are awaitable.

        The same state machine as :meth:`run`, driven asynchronously. Nodes may
        be ``def`` or ``async def`` in any mixture; the resulting trace is the
        one the interpreter produces either way. Execution remains single-lane
        — one node at a time — because fan-out has no representation in
        GraphSpec v1 (guarantees.md §5).
        """
        machine = self._start(
            state, run_id=run_id, replay=replay, copy_yields=False,
            start_node=self._plan.entry, resume_info=None,
        )
        async for _ in _adrive(machine, _invoke_async):
            pass
        assert self._state is not None and self._trace is not None
        return RunResult(self._state, self._trace)

    def astream(
        self,
        state: State | None = None,
        *,
        run_id: str | None = None,
        replay: ReplayStatus | None = None,
    ) -> AsyncStreamDriver:
        """Consumer-paced asynchronous execution, record by record."""
        machine = self._start(
            state, run_id=run_id, replay=replay, copy_yields=True,
            start_node=self._plan.entry, resume_info=None,
        )
        assert self._trace_ref is not None
        trace_ref = self._trace_ref
        return AsyncStreamDriver(
            _adrive(machine, _invoke_async), self.cancel, lambda: trace_ref[0]
        )

    def _start(
        self, state: State | None, *, run_id: str | None, replay: ReplayStatus | None,
        copy_yields: bool, start_node: str, resume_info: dict[str, Any] | None,
    ) -> Iterator[dict[str, Any]]:
        with self._lifecycle_lock:
            if self._running:
                raise RuntimeError("a Runtime instance cannot execute overlapping runs")
            self._running = True
            self._has_started = True
            self._cancel_reason = self._pending_cancel_reason
            self._pending_cancel_reason = None
        try:
            self._control = _RunController(replay=replay, **self._source_args)
            self._hub = _ObservationHub(**self._hub_args)
            self._refresh_identity()
            self._active_attempt = None
            initial = State.empty() if state is None else state
            if not isinstance(initial, State):
                raise TypeError("run state must be State")
            self._state = initial
            self._control.downgrade_many(self._identity_constraints)
            actual_run_id = run_id or self._control.kernel_uuid()
            if not isinstance(actual_run_id, str) or not 1 <= len(actual_run_id) <= 200:
                raise ValueError("run_id must be a string of length 1..200")
            if (
                resume_info is not None
                and actual_run_id == resume_info.get("source_run_id")
            ):
                raise ValueError("a resumed segment must have a new run_id")
            header: dict[str, Any] = {
                "run_id": actual_run_id,
                "graph": {
                    "name": self.spec.name,
                    "version": self.spec.version,
                    "graph_fingerprint": self._graph_fingerprint,
                    "code_fingerprint": self._code_fingerprint,
                    "spec_schema_version": self.spec.schema_version,
                },
                "policy": self.policy.value,
                "durability_profile": self.durability_profile.value,
                "replay": self._control.declared_replay.to_dict(),
                "metadata": copy.deepcopy(initial._metadata),
                "created_ts": self._control.timestamp(),
            }
            if resume_info is not None:
                header["resume"] = _strict_plain_json(resume_info)
            if self._hub.sink is not None:
                header["sink"] = {
                    "flush_interval_ms": (
                        self._hub.flush_interval_ms
                        if self.durability_profile is DurabilityProfile.BUFFERED
                        else 0
                    ),
                    "chunk_records": (
                        self._hub.chunk_records
                        if self.durability_profile is DurabilityProfile.BUFFERED
                        else 1
                    ),
                }
            self._trace = Trace(header)
            self._trace_ref = [self._trace]
            self._hub.bind(self._trace.run)
            return self._managed_execute(copy_yields=copy_yields, start_node=start_node)
        except BaseException:
            with self._lifecycle_lock:
                self._running = False
                self._cancel_reason = None
            self._active_attempt = None
            raise

    def _managed_execute(
        self, *, copy_yields: bool, start_node: str
    ) -> Iterator[dict[str, Any]]:
        try:
            yield from self._execute(copy_yields=copy_yields, start_node=start_node)
        finally:
            if self._hub is not None:
                self._hub.close()
            self._active_attempt = None
            with self._lifecycle_lock:
                self._running = False
                self._cancel_reason = None

    def _replace_trace(self, trace: Trace) -> None:
        self._trace = trace
        if self._trace_ref is not None:
            self._trace_ref[0] = trace

    def _base(self, kind: str, *, seq: int | None = None) -> dict[str, Any]:
        assert self._control is not None
        return {
            "seq": self._control.next_seq() if seq is None else seq,
            "ts": self._control.timestamp(),
            "kind": kind,
            "integrity": {"payload_hash": None, "prev_hash": None},
        }

    def _sink_failure_record(self, seq: int, record_seq: int | None, cause: BaseException) -> dict[str, Any]:
        assert self._control is not None
        record = self._base("run_failed", seq=seq)
        record.update({
            "cause": {
                "kind": "sink_failure",
                "message": "required TraceSink rejected trace evidence",
                "record_seq": record_seq,
            },
            "failed_node": None,
            "replay": self._control.replay.to_dict(),
        })
        return record

    def _store(self, record: dict[str, Any], *, terminal: bool = False, force: bool = False) -> dict[str, Any]:
        assert self._trace is not None and self._hub is not None
        if terminal:
            try:
                self._hub.persist(record, force=True)
            except BaseException as exc:
                if record["kind"] == "run_completed":
                    failure = self._sink_failure_record(record["seq"], None, exc)
                    self._hub.best_effort(failure)
                    self._replace_trace(self._trace._append_runtime(failure))
                    self._hub.notify(failure)
                    raise SinkFailed(self._trace, exc) from exc
                # The run was already failed/cancelled. Preserve that primary
                # terminal in memory; persistence is honestly best-effort.
                self._hub.best_effort(record)
            self._replace_trace(self._trace._append_runtime(record))
            self._hub.notify(record)
            return record
        self._replace_trace(self._trace._append_runtime(record))
        try:
            self._hub.persist(record, force=force)
        except BaseException as exc:
            failure = self._sink_failure_record(
                self._control.next_seq(), record.get("seq"), exc  # type: ignore[union-attr]
            )
            # Once a record was refused, the persisted stream remains the
            # last valid prefix.  Appending a later best-effort terminal would
            # manufacture a seq gap and a dangling cause.record_seq.
            self._replace_trace(self._trace._append_runtime(failure))
            self._hub.notify(record)
            self._hub.notify(failure)
            raise SinkFailed(self._trace, exc) from exc
        self._hub.notify(record)
        return record

    def _terminal(self, kind: str, **fields: Any) -> dict[str, Any]:
        record = self._base(kind)
        record.update(fields)
        return self._store(record, terminal=True)

    def _cancelled(self) -> dict[str, Any]:
        assert self._control is not None and self._cancel_reason is not None
        active = None
        if self._active_attempt is not None:
            active = {"node": self._active_attempt[0], "attempt": self._active_attempt[1]}
        return self._terminal(
            "run_cancelled", reason=self._cancel_reason,
            active_attempt=active, replay=self._control.replay.to_dict(),
        )

    def _declaration_violations(
        self, declaration: NodeDecl, reads: list[dict[str, Any]], writes: dict[str, list[dict[str, Any]]]
    ) -> list[dict[str, str]]:
        out: list[dict[str, str]] = []
        if declaration.reads_declared is not None:
            allowed = set(declaration.reads_declared)
            out.extend(
                {"kind": "undeclared_read", "key": read["key"]}
                for read in reads if read["key"] not in allowed
            )
        if declaration.writes_declared is not None:
            allowed = set(declaration.writes_declared)
            for collection in ("facts", "decisions"):
                out.extend(
                    {"kind": "undeclared_write", "key": item["key"]}
                    for item in writes[collection] if item["key"] not in allowed
                )
        return out

    def _routing(self, node: str, state: State) -> tuple[dict[str, Any], str, bool]:
        step = self._plan.transitions[node]
        record = self._base("routing")
        if step.kind == TransitionKind.TERMINAL:
            selected = "END"
            record.update({
                "after": node, "on": None, "value": None, "origin": None,
                "outcome": "static", "selected": selected,
                "candidates": [{"condition": {"kind": "static"}, "target": selected, "taken": True}],
            })
            return record, selected, False
        if step.kind == TransitionKind.NEXT:
            assert step.to is not None
            record.update({
                "after": node, "on": None, "value": None, "origin": None,
                "outcome": "static", "selected": step.to,
                "candidates": [{"condition": {"kind": "static"}, "target": step.to, "taken": True}],
            })
            return record, step.to, False
        assert step.on is not None
        latest = state._latest_decision(step.on)
        if latest is None:
            value, origin = None, {"kind": "absent"}
        else:
            value, origin = latest
        candidates = [
            {"condition": {"kind": "map", "key": key}, "target": target, "taken": False}
            for key, target in step.map.items()
        ]
        if step.default is not None:
            candidates.append({"condition": {"kind": "default"}, "target": step.default, "taken": False})
        if isinstance(value, str) and value in step.map:
            outcome, selected = "matched", step.map[value]
            for candidate in candidates:
                if candidate["condition"] == {"kind": "map", "key": value}:
                    candidate["taken"] = True
        elif isinstance(value, str) and step.default is not None:
            outcome, selected = "default", step.default
            for candidate in candidates:
                if candidate["condition"] == {"kind": "default"}:
                    candidate["taken"] = True
        else:
            outcome, selected = "miss", "END"
        record.update({
            "after": node, "on": step.on, "value": value, "origin": origin,
            "outcome": outcome, "selected": selected, "candidates": candidates,
        })
        return record, selected, outcome == "miss"

    def _execute(
        self, *, copy_yields: bool, start_node: str
    ) -> Iterator[dict[str, Any]]:
        assert self._control is not None and self._trace is not None and self._state is not None
        def exposed(record: dict[str, Any]) -> dict[str, Any]:
            return copy.deepcopy(record) if copy_yields else record
        started = self._base("run_started")
        started.update({"intent": self._state._intent, "initial_state": self._state._initial_wire()})
        yield exposed(self._store(started))
        current_node = start_node
        routed_activations = 0
        while True:
            if self._cancel_reason is not None:
                terminal = self._cancelled()
                yield exposed(terminal)
                return

            declaration = self._declarations[current_node]
            attempt_number = 1
            while True:
                opener = self._base("attempt_started")
                opener.update({"node": current_node, "attempt": attempt_number})
                self._active_attempt = (current_node, attempt_number)
                yield exposed(self._store(opener))
                if self._cancel_reason is not None:
                    terminal = self._cancelled()
                    yield exposed(terminal)
                    self._active_attempt = None
                    return

                attempt_state = self._state._attempt_view(self._trace._runtime_log)
                draw_cursor = self._control.draw_cursor()
                effect_cursor = self._control.effect_cursor()
                self._control.begin_effect_scope(declaration.effect_class.value)
                node = self._nodes[current_node]
                arguments = (
                    (attempt_state, self._control.node_context)
                    if self._uses_context[current_node]
                    else (attempt_state,)
                )
                returned, error = yield _Invoke(node, arguments, current_node)
                transition_seq = self._control.next_seq()
                writes = {"facts": [], "decisions": [], "rejections": []}
                if error is None:
                    try:
                        assert returned is not None
                        writes = returned._writes_wire()
                        committed = attempt_state._committed(returned, transition_seq)
                    except Exception as exc:
                        error = exc
                        writes = {"facts": [], "decisions": [], "rejections": []}
                reads = attempt_state._reads_wire()
                draws = [draw.to_dict() for draw in self._control.draws_since(draw_cursor)]
                effects = [
                    effect.to_dict()
                    for effect in self._control.effects_since(effect_cursor)
                ]
                if declaration.effect_class is EffectClass.PURE and effects:
                    error = ValueError("a pure node cannot record effects")
                    effects = []
                elif declaration.effect_class is EffectClass.RECORDED_EFFECT and any(
                    effect["class"] == EffectClass.EXTERNAL_EFFECT.value
                    for effect in effects
                ):
                    error = ValueError(
                        "a recorded_effect node cannot record external effects"
                    )
                    effects = [
                        effect for effect in effects
                        if effect["class"] == EffectClass.RECORDED_EFFECT.value
                    ]
                violations = self._declaration_violations(declaration, reads, writes)
                if error is None and violations and self.policy is Policy.STRICT:
                    error = DeclarationViolation(violations)
                if error is not None:
                    writes = {"facts": [], "decisions": [], "rejections": []}
                transition = self._base("transition", seq=transition_seq)
                if error is None:
                    outcome, disposition, error_wire = "returned", "commit", None
                else:
                    outcome = "raised"
                    if attempt_number < self._max_attempts[current_node]:
                        disposition = "retry"
                    elif self.policy is Policy.STRICT:
                        disposition = "abort"
                    else:
                        disposition = "continue"
                    error_wire = {
                        "type": type(error).__name__,
                        "message": f"node raised {type(error).__name__}",
                    }
                transition.update({
                    "node": current_node, "attempt": attempt_number,
                    "outcome": outcome, "disposition": disposition,
                    "effect_class": declaration.effect_class.value,
                    "reads": reads, "writes": writes,
                    "effects": effects, "context_draws": draws,
                    "violations": violations,
                    "error": error_wire,
                })
                self._active_attempt = None
                force = error is not None
                yield exposed(self._store(transition, force=force))
                if self._cancel_reason is not None:
                    terminal = self._cancelled()
                    yield exposed(terminal)
                    return
                if error is None:
                    self._state = committed
                    routed_activations += 1
                    break
                if disposition == "retry":
                    attempt_number += 1
                    continue
                if disposition == "abort":
                    terminal = self._terminal(
                        "run_failed",
                        cause={
                            "kind": "node_failure",
                            "message": f"node raised {type(error).__name__}",
                            "record_seq": transition_seq,
                        },
                        failed_node=current_node,
                        replay=self._control.replay.to_dict(),
                    )
                    yield exposed(terminal)
                    raise NodeFailed(current_node, self._state, self._trace, error) from error
                # exploration: route using the unchanged committed state.
                routed_activations += 1
                break

            routing, selected, missed = self._routing(current_node, self._state)
            yield exposed(self._store(routing))
            routing_seq = routing["seq"]
            if self._cancel_reason is not None:
                terminal = self._cancelled()
                yield exposed(terminal)
                return
            if missed:
                if self.policy is Policy.STRICT:
                    terminal = self._terminal(
                        "run_failed",
                        cause={"kind": "route_miss", "message": f"no route for {routing['on']!r}", "record_seq": routing_seq},
                        failed_node=None, replay=self._control.replay.to_dict(),
                    )
                else:
                    terminal = self._terminal("run_completed", replay=self._control.replay.to_dict())
                yield exposed(terminal)
                return
            if selected == "END":
                terminal = self._terminal("run_completed", replay=self._control.replay.to_dict())
                yield exposed(terminal)
                return
            limit = self._plan.max_transitions
            if limit is not None and routed_activations >= limit:
                terminal = self._terminal(
                    "run_failed",
                    cause={
                        "kind": "transition_limit_exceeded",
                        "message": f"max_transitions {limit} reached",
                        "record_seq": routing_seq,
                    },
                    failed_node=None, replay=self._control.replay.to_dict(),
                )
                yield exposed(terminal)
                return
            current_node = selected

    def _run_from(
        self,
        state: State,
        *,
        start_node: str,
        resume_info: dict[str, Any],
        run_id: str | None = None,
        replay: ReplayStatus | None = None,
    ) -> RunResult:
        """Execute a causally linked resume segment (used by ReplayEngine)."""
        if start_node not in self._plan.declarations:
            raise ValueError(f"resume start node {start_node!r} is not declared")
        machine = self._start(
            state, run_id=run_id, replay=replay, copy_yields=False,
            start_node=start_node, resume_info=resume_info,
        )
        for _ in _drive(machine, _invoke_sync):
            pass
        assert self._state is not None and self._trace is not None
        return RunResult(self._state, self._trace)
