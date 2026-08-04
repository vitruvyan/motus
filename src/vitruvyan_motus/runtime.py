"""The single Motus executor and the trace-v1 execution state machine."""

from __future__ import annotations

import copy
import functools
import hashlib
import inspect
import json
from dataclasses import dataclass
from enum import Enum
from types import MappingProxyType
from typing import Any, Callable, Iterator, Mapping

from vitruvyan_motus import TRACE_SCHEMA_VERSION, __version__
from vitruvyan_motus.context import ReplayStatus, _RunController
from vitruvyan_motus.errors import NodeFailed, SinkFailed
from vitruvyan_motus.graph import GraphSpec, NodeDecl, TransitionKind
from vitruvyan_motus.observers import Listener, StreamDriver, TraceSink, _ObservationHub
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


def _config_fingerprint(node: Callable[..., Any]) -> tuple[str, bool]:
    """Return the node's config marker/fingerprint and whether it is opaque."""
    if isinstance(node, functools.partial):
        value = {"args": list(node.args), "keywords": node.keywords or {}}
        canonical = _strict_plain_json(value)
        return "config:sha256:" + hashlib.sha256(_canonical_bytes(canonical)).hexdigest(), False
    if not inspect.isfunction(node) and not inspect.ismethod(node):
        config = getattr(node, "motus_config", None)
        if callable(config):
            canonical = _strict_plain_json(config())
            return "config:sha256:" + hashlib.sha256(_canonical_bytes(canonical)).hexdigest(), False
        return "opaque", True
    target = inspect.unwrap(node)
    if inspect.isfunction(target) and target.__closure__ is None and "<locals>" not in target.__qualname__:
        return "none", False
    if inspect.ismethod(target) and target.__self__ is not None:
        config = getattr(target.__self__, "motus_config", None)
        if callable(config):
            canonical = _strict_plain_json(config())
            return "config:sha256:" + hashlib.sha256(_canonical_bytes(canonical)).hexdigest(), False
    return "opaque", True


def _node_identity(name: str, node: Callable[..., Any]) -> tuple[list[str], bool, bool]:
    target = inspect.unwrap(node.func if isinstance(node, functools.partial) else node)
    if not inspect.isfunction(target) and not inspect.ismethod(target) and callable(target):
        target = inspect.unwrap(type(target).__call__)
    qualified = f"{getattr(target, '__module__', type(target).__module__)}.{getattr(target, '__qualname__', type(target).__qualname__)}"
    unavailable = False
    try:
        source = inspect.getsource(target).encode("utf-8")
        source_hash = hashlib.sha256(source).hexdigest()
    except (OSError, TypeError):
        source_hash = "unavailable"
        unavailable = True
    config, opaque = _config_fingerprint(node)
    return [name, qualified, source_hash, config], opaque, unavailable


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
    return len(positional) == 2


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
        self._graph_fingerprint = spec.graph_fingerprint
        self._nodes = dict(nodes)
        declared = {node.name for node in spec.nodes}
        if set(self._nodes) != declared:
            missing = sorted(declared - set(self._nodes))
            extra = sorted(set(self._nodes) - declared)
            raise ValueError(f"node registry must match GraphSpec; missing={missing}, extra={extra}")
        self._uses_context = {name: _accepts_context(node) for name, node in self._nodes.items()}
        self._declarations = {node.name: node for node in spec.nodes}
        identity_rows = []
        identity_constraints: list[tuple[str, str]] = []
        for declaration in spec.nodes:
            row, opaque, unavailable = _node_identity(
                declaration.name, self._nodes[declaration.name]
            )
            identity_rows.append(row)
            if opaque:
                identity_constraints.append(("partial", f"node:{declaration.name}:opaque_config"))
            if unavailable:
                identity_constraints.append(("partial", f"node:{declaration.name}:source_unavailable"))
        self._identity_constraints = tuple(identity_constraints)
        self._code_fingerprint = (
            "code:sha256:" + hashlib.sha256(_canonical_bytes(identity_rows)).hexdigest()
        )
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
        self._active_attempt: tuple[str, int] | None = None
        self._running = False
        self._trace_ref: list[Trace | None] | None = None

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

    def cancel(self, reason: str = "cancelled by caller") -> None:
        if not isinstance(reason, str):
            raise TypeError("cancellation reason must be a string")
        self._cancel_reason = reason

    def run(
        self,
        state: State | None = None,
        *,
        run_id: str | None = None,
        replay: ReplayStatus | None = None,
    ) -> RunResult:
        iterator = self._start(state, run_id=run_id, replay=replay, copy_yields=False)
        for _ in iterator:
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
        iterator = self._start(state, run_id=run_id, replay=replay, copy_yields=True)
        assert self._trace_ref is not None
        trace_ref = self._trace_ref
        return StreamDriver(iterator, self.cancel, lambda: trace_ref[0])

    def _start(
        self, state: State | None, *, run_id: str | None, replay: ReplayStatus | None,
        copy_yields: bool,
    ) -> Iterator[dict[str, Any]]:
        if self._running:
            raise RuntimeError("a Runtime instance cannot execute overlapping runs")
        self._running = True
        try:
            self._control = _RunController(replay=replay, **self._source_args)
            self._hub = _ObservationHub(**self._hub_args)
            self._cancel_reason = None
            self._active_attempt = None
            initial = State.empty() if state is None else state
            if not isinstance(initial, State):
                raise TypeError("run state must be State")
            self._state = initial
            self._control.downgrade_many(self._identity_constraints)
            actual_run_id = run_id or self._control.kernel_uuid()
            if not isinstance(actual_run_id, str) or not 1 <= len(actual_run_id) <= 200:
                raise ValueError("run_id must be a string of length 1..200")
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
            if self.durability_profile is DurabilityProfile.BUFFERED:
                header["sink"] = {
                    "flush_interval_ms": self._hub.flush_interval_ms,
                    "chunk_records": self._hub.chunk_records,
                }
            self._trace = Trace(header)
            self._trace_ref = [self._trace]
            self._hub.bind(self._trace.run)
            return self._managed_execute(copy_yields=copy_yields)
        except BaseException:
            self._running = False
            self._active_attempt = None
            raise

    def _managed_execute(self, *, copy_yields: bool) -> Iterator[dict[str, Any]]:
        try:
            yield from self._execute(copy_yields=copy_yields)
        finally:
            if self._hub is not None:
                self._hub.close()
            self._active_attempt = None
            self._running = False

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
        step = self.spec.transitions[node]
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

    def _execute(self, *, copy_yields: bool) -> Iterator[dict[str, Any]]:
        assert self._control is not None and self._trace is not None and self._state is not None
        def exposed(record: dict[str, Any]) -> dict[str, Any]:
            return copy.deepcopy(record) if copy_yields else record
        started = self._base("run_started")
        started.update({"intent": self._state._intent, "initial_state": self._state._initial_wire()})
        yield exposed(self._store(started))
        current_node = self.spec.entry
        committed_transitions = 0
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

                attempt_state = self._state._attempt_view(self._trace.log)
                draw_cursor = self._control.draw_cursor()
                error: BaseException | None = None
                returned: State | None = None
                try:
                    node = self._nodes[current_node]
                    if self._uses_context[current_node]:
                        returned = node(attempt_state, self._control.node_context)
                    else:
                        returned = node(attempt_state)
                except Exception as exc:
                    error = exc
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
                    "effects": [], "context_draws": draws,
                    "violations": self._declaration_violations(declaration, reads, writes),
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
                    committed_transitions += 1
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
            limit = self.spec.max_transitions
            if limit is not None and committed_transitions >= limit:
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
