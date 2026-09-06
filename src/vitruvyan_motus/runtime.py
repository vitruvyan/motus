"""The single Motus executor and the trace-v1 execution state machine."""

from __future__ import annotations

import copy
import functools
import hashlib
import inspect
import json
import sys
import threading
import weakref
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

__all__ = ["Policy", "DurabilityProfile", "EvidenceStatus", "RunResult", "Runtime"]


class Policy(str, Enum):
    STRICT = "strict"
    EXPLORATION = "exploration"


class DurabilityProfile(str, Enum):
    """How this RUNTIME hands records to a sink. Not a claim about the sink.

    The name invites the wrong reading and an integrator took it (#108): they
    attached a `JsonlTraceSink` writing to disk, read `durability_profile:
    "in-memory"` in the header, and concluded the evidence was never durable —
    while the same run's `RunResult` said `evidence: "persisted"` and the file
    was on disk. Both were correct. They answer different questions.

    - `IN_MEMORY`, written `"in-memory"` — no sink is required. One may still
      be attached, and then it receives every record as the run proceeds. The
      default, because a run with no sink at all has to say something.
    - `BUFFERED`, written `"buffered"` — requires a sink, and batches. The only
      profile with a loss window, declared in the header's `sink` object
      (`flush_interval_ms`, `chunk_records`) and required there by rule H1.
    - `SYNCHRONOUS`, written `"synchronous"` — requires a sink, delivers record
      by record.

    The value is what a header carries, so both spellings are here: somebody
    arriving from `"in-memory"` in a trace is not holding the member name.

    So `IN_MEMORY` and `SYNCHRONOUS` deliver identically; what separates them
    is whether a sink is REQUIRED. Read the header this way: `sink` present
    means a sink was attached, `durability_profile` says how it was fed, and
    `RunResult.evidence` says whether it accepted everything. Crash guarantees
    belong to the profile alone — `contract/guarantees.md` invariant II.
    """

    IN_MEMORY = "in-memory"
    BUFFERED = "buffered"
    SYNCHRONOUS = "synchronous"


class EvidenceStatus(str, Enum):
    """What a run can say about its own durable evidence (#42).

    Three states and not a boolean, because "no sink was configured" and "a sink
    was configured and did not receive the terminal" are different facts, and a
    caller that treats the first as a durability failure will refuse to act on
    perfectly ordinary in-memory runs.

    A `str` Enum, following `Policy` and `DurabilityProfile`, so a caller
    comparing against the plain string keeps working.
    """

    PERSISTED = "persisted"
    """A required sink accepted every record, terminal included."""

    INCOMPLETE = "incomplete"
    """A required sink refused something. What is stored is an honest prefix,
    and a validator reading it reports T3/INCOMPLETE — but the run may still
    have returned an ordinary `failed` or `cancelled` status, because the
    primary cause is preserved rather than replaced."""

    NOT_REQUIRED = "not-required"
    """No sink was configured, so no durable evidence was promised."""


@dataclass(frozen=True, slots=True)
class RunResult:
    state: State
    trace: Trace
    evidence: str = EvidenceStatus.NOT_REQUIRED.value
    """Whether the durable evidence for this run is whole — see EvidenceStatus.

    A plain `str`, never an enum member, and the same on `NodeFailed`,
    `SinkFailed` and both stream drivers. `EvidenceStatus` is the vocabulary; the
    value carried is data, exactly as `run.policy` in the trace header is
    `"strict"` and not a `Policy`. An adversarial round found the enum on one
    carrier and a plain string on the others, so `f"{x.evidence}"` printed
    `EvidenceStatus.INCOMPLETE` or `incomplete` depending on which object the
    caller happened to be holding.

    Comparison against the enum still reads naturally, because `EvidenceStatus`
    is a `str` Enum: `result.evidence == EvidenceStatus.PERSISTED` is True.

    Defaulted so that constructing a `RunResult` by hand keeps working, and
    defaulted to `not-required` because a result nobody's sink produced promised
    nobody anything. A run driven by this module always carries the real value.
    """

    _TERMINALS = ("run_completed", "run_failed", "run_cancelled")

    @property
    def status(self) -> str:
        """The explicit terminal status: completed, failed or cancelled.

        Guarded, because the last record of a trace is not always a terminal.
        A driver abandoned mid-run leaves one ending in ``transition`` or
        ``routing``, and stripping the ``run_`` prefix off those quietly
        answered ``"transition"`` — a value outside the three this contract
        names, which callers compare against and branch on.
        """
        records = self.trace.records
        terminal = records[-1]["kind"] if records else None
        if terminal not in self._TERMINALS:
            raise ValueError(
                f"this trace has no terminal record: it ends with {terminal!r}. "
                "A run that was abandoned before finishing has no status; its "
                "evidence is incomplete by construction."
            )
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
    if inspect.isfunction(target) and target.__closure__ is None:
        # A function's configuration lives in exactly two places it can carry
        # without a closure: its default arguments. `def node(state, *,
        # threshold=x)` stores x in `__kwdefaults__` while `__closure__` stays
        # None, so a factory can produce two nodes that behave differently and
        # capture nothing.
        #
        # This branch first required "<locals>" not in __qualname__, which was
        # a rule stricter than the contract — it marked a nested `def` capturing
        # NOTHING as opaque — and I removed it. That opened this hole: two nodes
        # with different thresholds both reported `none` and shared one
        # configuration fingerprint. A false constraint is noise; a false
        # IDENTITY is the class of defect this project exists to prevent, so the
        # narrower rule is not restored — defaults are read instead, which is
        # what the qualname check was accidentally standing in for.
        #
        # They are fingerprinted exactly as a partial's arguments are, for the
        # same reason: they are configuration, and configuration that reduces
        # to strict JSON is identifiable. Defaults that do not reduce are
        # honestly opaque.
        defaults = list(target.__defaults__ or ())
        keyword_defaults = target.__kwdefaults__ or {}
        if not defaults and not keyword_defaults:
            return ("marker", "none"), False
        try:
            material = _strict_plain_json(
                {"defaults": defaults, "kwdefaults": keyword_defaults}
            )
        except (TypeError, ValueError):
            return ("marker", "opaque"), True
        return ("value", material), False
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


def _is_async_node(node: Callable[..., Any]) -> bool:
    """Whether this callable is statically known to produce an awaitable.

    Every candidate is examined, not just the fully unwrapped one:
    ``inspect.unwrap`` follows ``__wrapped__`` straight past the coroutine
    function in the standard "asyncify a sync function" decorator, so asking
    only the innermost target answers about the wrong callable. Async
    generator functions count too — ``iscoroutinefunction`` is False for them,
    while replay's own predicate refuses them, and the two must agree.

    Statically unknowable shapes — a plain ``def`` that returns a coroutine —
    are caught at the moment the runtime observes the awaitable instead.
    """
    candidates = [node]
    inner = node.func if isinstance(node, functools.partial) else node
    candidates.append(inner)
    target = inspect.unwrap(inner)
    candidates.append(target)
    if not inspect.isfunction(target) and not inspect.ismethod(target) and callable(target):
        call = type(target).__call__
        candidates.extend((call, inspect.unwrap(call)))
    return any(
        inspect.iscoroutinefunction(candidate) or inspect.isasyncgenfunction(candidate)
        for candidate in candidates
    )


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


def _discard_awaitable(value: Any) -> None:
    """Close an awaitable the runtime refuses, without letting it complain.

    A coroutine that is never awaited emits an interpreter-level warning that
    no consumer can act on, so the runtime closes what it declines. Cleanup
    failures are the caller's to see; they surface through the node-failure
    boundary that encloses every call site.
    """
    closer = getattr(value, "close", None)
    if not callable(closer):
        return
    result = closer()
    if inspect.isawaitable(result):
        # `async def close()` is the ordinary shape for async resources, and
        # it hands back a coroutine this synchronous path cannot await.
        # Closing that coroutine is what stops it complaining in turn.
        inner = getattr(result, "close", None)
        if callable(inner):
            inner()


@dataclass(slots=True)
class _RunHandle:
    """One run's identity, and the evidence that run will answer with.

    A run is not over when its generator unwinds — it is over when its terminal
    record is written *and* its caller has read its result.  The lifecycle claim
    is released at the first of those events, so anything the caller still needs
    afterwards cannot live in a ``Runtime`` attribute: a second run admitted in
    that window rebinds ``self._trace`` and ``self._state`` before the first
    caller reads them, and hands one caller another caller's evidence.

    Identity was already run-scoped, because run-scoped cancellation needed it
    (see :meth:`Runtime._run_scoped_cancel`).  The result travels on the same
    handle for the same reason: a handle belongs to exactly one run and no later
    run can reach it.
    """

    trace: "Trace | None" = None
    state: State | None = None
    hub: "_ObservationHub | None" = None
    evidence: str = "not-required"
    started: bool = False
    finished: bool = False


def _release_abandoned_run(runtime_ref: "weakref.ref[Runtime]", handle: _RunHandle) -> None:
    """Finaliser body for an abandoned driver — deliberately not a method.

    ``weakref.finalize`` keeps its callback and arguments in a module-global
    registry, and that registry is a strong root. A bound method here would put
    the Runtime under that root, and the Runtime reaches the driver in the very
    shape this finaliser exists for — a listener that holds the Runtime and
    closes its own driver (guarantees.md §6 blesses exactly that). The driver
    would then never become unreachable, the finaliser would never fire, and the
    registry entry would never leave: an unbounded leak dragging the Trace,
    State, observation hub and the caller's sink with every run.

    Holding the Runtime weakly keeps the registry out of that path. A dead
    Runtime has no claim left to release.
    """
    runtime = runtime_ref()
    if runtime is not None:
        runtime._release_if_never_started(handle)


def _result_of(handle: _RunHandle) -> RunResult:
    """The result of the run this handle names, and of no other run."""
    if not handle.finished or handle.state is None or handle.trace is None:
        raise AssertionError("a finished run must publish its state and trace")
    return RunResult(handle.state, handle.trace, handle.evidence)


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
        if inspect.isawaitable(returned):
            # Inside the boundary on purpose: closing an awaitable can itself
            # raise (a coroutine that swallows GeneratorExit raises
            # RuntimeError), and that must be an ordinary node failure, not an
            # escape that leaves the attempt unclosed as if the run had
            # crashed (node-protocol §7.3).
            _discard_awaitable(returned)
            raise TypeError(
                f"node {request.name!r} is asynchronous; drive it with "
                "Runtime.arun() or Runtime.astream()"
            )
    except Exception as exc:  # noqa: BLE001 - this IS the node failure boundary
        return None, exc
    return returned, None


def _drive(
    machine: Iterator[Any], invoke: Callable[[_Invoke], tuple[Any, BaseException | None]]
) -> Iterator[dict[str, Any]]:
    """Advance the state machine, servicing its invocation requests.

    Yields only trace records, so every consumer above this layer — including
    ``StreamDriver`` — sees exactly the stream it always saw.
    """
    reply: Any = None
    try:
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
    finally:
        # Closing this driver must release the run it is driving. The async
        # twin gets that from the loop's asyncgen finalisation; the
        # synchronous one has to say so, or a caller that closes the driver
        # strands the machine — and with it `_running` — forever.
        machine.close()


def _async_invoker(
    observe: Callable[[str], None],
) -> Callable[[_Invoke], Awaitable[tuple[Any, BaseException | None]]]:
    """Bind the async invoker to the run that must record what it observes.

    A plain ``def`` that returns a coroutine is async in every way that
    matters and in no way a static predicate can see. The runtime does see
    it — at the instant it decides to await — and invariant IV requires that
    observation to reach the recorded replay capability rather than be
    discarded.
    """

    async def invoke(request: _Invoke) -> tuple[Any, BaseException | None]:
        try:
            returned = request.node(*request.args)
            if inspect.isawaitable(returned):
                observe(request.name)
                returned = await returned
        except Exception as exc:  # noqa: BLE001 - the node failure boundary
            return None, exc
        return returned, None

    return invoke


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
        commitments: Any = None,
        witness: Any = None,
    ) -> None:
        if not isinstance(spec, GraphSpec):
            raise TypeError("spec must be GraphSpec")
        # ADR-021 decision 1: unconfigured, this is bit-for-bit 0.8.1. Both are
        # `Any` and both are inert when None — the module is not even imported
        # unless an embedder passes one, which is what keeps Motus a library
        # rather than a service that happens to be importable.
        if witness is not None and commitments is None:
            raise ValueError(
                "a witness has nothing to acknowledge without a commitment "
                "log: pass commitments= as well, or neither")
        # Refused HERE rather than at the first run. A deadline missing from
        # the log is a configuration mistake, and finding it out on the hot
        # path of the first real decision is the worst possible moment.
        if witness is not None and getattr(commitments, "witness_deadline", 0) is None:
            raise ValueError(
                "this commitment log has no witness_deadline, so a slow "
                "witness would stop its writer for as long as it likes. Open "
                "the log with witness_deadline=<seconds>")
        self._commitments = commitments
        self._witness = witness
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
        # Computed once, beside `_uses_context`, and for the same reason: this
        # is a property of the callable, and the callable does not change
        # between runs. `_refresh_identity` re-derives node config on every
        # `_start` because `motus_config()` legitimately varies — but whether a
        # node is `async def` cannot. Asking per node per run cost 4.8 us of
        # `inspect` work each time, which on a 1000-node graph is 72% of a 24%
        # per-node regression the SLO gate refused. Measured, not guessed.
        self._async_nodes = frozenset(
            name for name, node in self._nodes.items() if _is_async_node(node)
        )
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
        self._run: _RunHandle | None = None

    def _refresh_identity(self) -> None:
        """Refresh only config rows whose observable material changed."""
        rows: list[list[str]] = []
        constraints: list[tuple[str, str]] = []
        changed = not self._identity_cache
        # Staged, not written through. `_node_identity_parts` runs consumer
        # code and a node that raises aborts the loop before the fingerprint
        # below is recomputed. Committing rows as they are computed would leave
        # the nodes already visited cached against the OLD fingerprint — and on
        # the next start they compare equal to their own cache, `changed` stays
        # False, and the fingerprint is never recomputed again. The header would
        # then record configuration the run did not use, which node-protocol.md
        # §"motus_config" forbids in the same words it uses to require this
        # method. A refresh that raises must leave identity as it found it.
        staged: dict[str, tuple[Any, Any, list[str], bool]] = {}
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
                staged[name] = (static, material, row, opaque)
                changed = True
            rows.append(row)
            if opaque:
                constraints.append(("partial", f"node:{name}:opaque_config"))
            if unavailable:
                constraints.append(("partial", f"node:{name}:source_unavailable"))
            if name in self._async_nodes:
                # An async node is executable but not verify-replayable:
                # `ReplayEngine.verify` and `resume` drive nodes
                # synchronously. Invariant IV requires replay capability to be
                # an explicit recorded property, so a run containing one may
                # not silently keep a `full` claim it cannot honour.
                constraints.append(("partial", f"node:{name}:async"))
        # Past the last statement that can raise: commit the whole refresh.
        current_constraints = tuple(constraints)
        self._identity_cache.update(staged)
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
        handle, machine = self._start(
            state, run_id=run_id, replay=replay, copy_yields=False,
            start_node=self._plan.entry, resume_info=None,
        )
        for _ in _drive(machine, _invoke_sync):
            pass
        return _result_of(handle)

    def stream(
        self,
        state: State | None = None,
        *,
        run_id: str | None = None,
        replay: ReplayStatus | None = None,
    ) -> StreamDriver:
        handle, machine = self._start(
            state, run_id=run_id, replay=replay, copy_yields=True,
            start_node=self._plan.entry, resume_info=None,
        )
        driver = StreamDriver(
            _drive(machine, _invoke_sync),
            self._run_scoped_cancel(handle),
            lambda: handle.trace,
            lambda: handle.evidence if handle.finished else None,
        )
        weakref.finalize(driver, _release_abandoned_run, weakref.ref(self), handle)
        return driver

    def _async_invoker(self):
        """The async invoker for the run in flight, wired to its controller."""
        control = self._control
        assert control is not None

        def observe(name: str) -> None:
            control.downgrade_many((("partial", f"node:{name}:async"),))

        return _async_invoker(observe)

    def _release_if_never_started(self, handle: _RunHandle) -> None:
        """Release a claim whose driver was dropped before it was ever advanced.

        ``stream()`` claims the run up front, and has to: a second ``stream()``
        must be refused. The claim is normally released by
        :meth:`_managed_execute`'s ``finally`` — but a generator that was never
        started does not run one, so a driver created and dropped left the
        Runtime claiming a run that had not executed a single node, for good.

        Deliberately narrow. A driver dropped *mid-run* is already handled: its
        generator is live, closing it raises ``GeneratorExit`` inside, and the
        ``finally`` releases the claim and publishes the result. Releasing that
        case here too would open a window where the claim is free while the
        generator is still unwinding, and the unwind would then publish another
        run's trace onto this handle — trading a wedge for a corruption.

        Scoped to this handle for the same reason cancellation is: a finaliser
        runs at an arbitrary later moment, and a Runtime that has since started
        another run must not be disarmed by it.
        """
        if handle.started or handle.finished:
            return
        with self._lifecycle_lock:
            if self._run is handle and self._running:
                self._running = False
                self._cancel_reason = None
        # The durable session was opened by `_start`, before the consumer asked
        # for anything, so an abandoned driver leaves it open and — worse —
        # never told. `_ObservationHub.close()` is what signals the session,
        # and it lives in `_managed_execute`'s `finally`, which a generator
        # that never started does not run. Closed through the handle, never
        # `self._hub`: by now that may name a later run's hub.
        hub, handle.hub = handle.hub, None
        if hub is not None:
            hub.close()

    def _run_scoped_cancel(self, handle: _RunHandle) -> Callable[[str], bool]:
        """A cancellation that can only ever reach the run it was made for.

        A driver outlives its run — an event loop finalising an abandoned
        async generator ends the run without the driver noticing — and a bare
        ``self.cancel`` would then bind to whatever run happens to be live.
        ADR-008 §1 forbids exactly that: "an idle call ... cannot cancel a
        later unrelated run." Each run gets a fresh handle, so identity of that
        handle is the run's identity.
        """

        def cancel(reason: str) -> bool:
            if self._run is not handle:
                return False
            return self.cancel(reason)

        return cancel

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
        handle, machine = self._start(
            state, run_id=run_id, replay=replay, copy_yields=False,
            start_node=self._plan.entry, resume_info=None,
        )
        async for _ in _adrive(machine, self._async_invoker()):
            pass
        return _result_of(handle)

    def astream(
        self,
        state: State | None = None,
        *,
        run_id: str | None = None,
        replay: ReplayStatus | None = None,
    ) -> AsyncStreamDriver:
        """Consumer-paced asynchronous execution, record by record."""
        handle, machine = self._start(
            state, run_id=run_id, replay=replay, copy_yields=True,
            start_node=self._plan.entry, resume_info=None,
        )
        driver = AsyncStreamDriver(
            _adrive(machine, self._async_invoker()),
            self._run_scoped_cancel(handle),
            lambda: handle.trace,
            lambda: handle.evidence if handle.finished else None,
        )
        weakref.finalize(driver, _release_abandoned_run, weakref.ref(self), handle)
        return driver

    def _start(
        self, state: State | None, *, run_id: str | None, replay: ReplayStatus | None,
        copy_yields: bool, start_node: str, resume_info: dict[str, Any] | None,
    ) -> tuple[_RunHandle, Iterator[dict[str, Any]]]:
        with self._lifecycle_lock:
            if self._running:
                raise RuntimeError("a Runtime instance cannot execute overlapping runs")
            # `_has_started` is the latch ADR-008 §1 uses to refuse an idle
            # cancellation on a Runtime that has already executed, and the
            # queued reason is spent here. Both are taken before the checks
            # below can still reject the call, so both must be recoverable if
            # this start never becomes a run. `queued` is non-None only when
            # `was_started` is False — `cancel()` queues nothing once the latch
            # is up — which is why the rollback needs no condition.
            was_started = self._has_started
            queued = self._pending_cancel_reason
            self._running = True
            self._has_started = True
            self._cancel_reason = queued
            self._pending_cancel_reason = None
            # The new run's identity is published in the same critical section
            # that claims `_running`. Assigning it later — after the controller,
            # the hub, identity refresh and header construction, all of which
            # run user code — would leave a window where `_running` describes
            # this run while `_run` still names the previous one, and a stale
            # driver's run-scoped cancel would pass its identity test.
            handle = _RunHandle()
            self._run = handle
        try:
            self._control = _RunController(replay=replay, **self._source_args)
            self._hub = _ObservationHub(**self._hub_args)
            handle.hub = self._hub
            self._refresh_identity()
            self._active_attempt = None
            initial = State.empty() if state is None else state
            if not isinstance(initial, State):
                raise TypeError("run state must be State")
            self._state = initial
            self._control.downgrade_many(self._identity_constraints)
            # `or` would treat "" as "not supplied" and silently generate a
            # UUID, so the stated 1..200 check below never saw it. A caller
            # who passes an empty run_id has made a mistake and should be told.
            actual_run_id = (
                self._control.kernel_uuid() if run_id is None else run_id
            )
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
            handle.trace = self._trace
            # ADR-011: the sink is handed the document's TraceHeader, not the
            # run object one level inside it. `run` alone is not a valid header
            # and a sink given only that can conform only by importing the
            # schema version from the writer — the out-of-band coupling ADR-004
            # exists to remove.
            self._hub.bind(self._trace.header)
            return handle, self._managed_execute(
                copy_yields=copy_yields, start_node=start_node, handle=handle,
            )
        except BaseException:
            # Ordered as `_managed_execute`'s `finally` orders it: the attempt
            # is cleared while the claim still holds. Written after the release
            # it is an unsynchronised cross-run store — it lands in whichever
            # run has since begun, and blanks the `active_attempt` that a
            # `run_cancelled` must name, which the validator refuses under T6.
            self._active_attempt = None
            with self._lifecycle_lock:
                self._running = False
                # A start that raised wrote no header, ran no node and handed
                # the caller an exception instead of a result. Leaving the
                # latch up would spend the Runtime's one queued cancellation on
                # a run that never existed, and — because ADR-008 §1 then reads
                # the Runtime as used — refuse to let the caller lodge it again.
                # Restore what was found rather than clearing: a Runtime that
                # HAD executed must still refuse to queue, or the failed start
                # reopens the leak into a later unrelated run that §1 closed.
                self._has_started = was_started
                # Exactly what the claim took, and nothing else. By now
                # `_cancel_reason` may instead hold a request a concurrent
                # caller bound to THIS start; re-queueing that one would aim it
                # at a later, unrelated run and record its reason as that run's
                # own — and would displace the reason the operator had queued.
                self._pending_cancel_reason = queued
                self._cancel_reason = None
            raise

    def _managed_execute(
        self, *, copy_yields: bool, start_node: str, handle: _RunHandle
    ) -> Iterator[dict[str, Any]]:
        handle.started = True
        try:
            # BEGIN and END live in ONE function on purpose. `_start` would be
            # the obvious home for the first, but the generator it returns may
            # never be advanced — a `stream()` driver created and dropped — and
            # a BEGIN written there would stand alone forever for a run that
            # did not execute a single node.
            #
            # INSIDE the try, and that placement is the whole of a defect this
            # file had already fixed once by another route. Above it, a BEGIN
            # that could not be written raised from a point where nothing
            # cleaned up: `_start` had already returned, so its own except
            # clause was long gone; this `finally` had not been entered, so the
            # claim was never released; and `_release_if_never_started` refuses
            # to help because `handle.started` is already True. The run
            # correctly did not execute, and the Runtime was then wedged
            # forever — every later run(), stream(), arun() and resume() on the
            # instance raising "cannot execute overlapping runs" — with a
            # required sink's session left open and never finished.
            #
            # It raises, and the run still does not execute: that is the point
            # of the first phase, and a run whose BEGIN can vanish has no
            # execution continuity to prove. What changes is that the failure
            # now costs one run rather than the object.
            self._commit_begin(handle)
            yield from self._execute(copy_yields=copy_yields, start_node=start_node)
        finally:
            # Publish onto the run's own handle BEFORE releasing the claim.
            # Afterwards `self._state` and `self._trace` describe whichever run
            # the Runtime is executing, which need not be this one any more.
            handle.trace = self._trace
            handle.state = self._state
            handle.hub = None
            if self._hub is not None:
                self._hub.close()
                # Read after close, so the caller is handed exactly what
                # `finish(complete=...)` told the session — one fact, read
                # twice, unable to disagree.
                #
                # Today the two points are equivalent and no test can tell them
                # apart: a terminal record persists with force=True and so has
                # always flushed by the time close() runs, and close() skips its
                # flush entirely once a refusal is latched. A mutation probe that
                # moved this line above close() stayed green, which is the honest
                # status of this ordering — defensive, not load-bearing. It is
                # written this way so it remains correct if terminals ever stop
                # forcing their own flush.
                handle.evidence = self._hub.evidence
            handle.finished = True
            self._active_attempt = None
            with self._lifecycle_lock:
                self._running = False
                self._cancel_reason = None
            # AFTER the claim is released, and that ordering is the second half
            # of the same defect. `_commit_end` re-raises when nothing else is
            # in flight — a clean run whose END could not be written is a
            # failure the caller must hear about — and from above the release
            # that exception escaped before `_running` was ever set back to
            # False. The instance was then wedged forever, through run(),
            # stream(), arun(), astream() and cooperative cancellation alike,
            # while the persisted account stayed perfectly honest: a BEGIN with
            # no END, which is what it should say.
            #
            # Nothing below this line may touch the run's lifecycle state, so a
            # raise here costs the caller their result and costs the Runtime
            # nothing.
            self._commit_end(handle)

    def _commit_begin(self, handle: _RunHandle) -> None:
        """Durably commit that this run is about to execute, or do nothing."""
        if self._commitments is None:
            return
        assert self._control is not None and self._trace is not None
        # ADR-023 decision 3. A resumed segment takes a new run_id and starts a
        # fresh trace chain, so without this the durable account of a
        # crash-and-resume is an unpaired BEGIN followed by an unrelated pair
        # with nothing connecting them -- and an unpaired BEGIN is exactly the
        # signal ADR-020 needs to stay rare. The trace documents carry the
        # link; the log, which is what an auditor walks and what an anchor
        # covers, did not.
        resume = self._trace.run.get("resume")
        link: dict[str, Any] = {}
        if resume:
            link = {
                "continues": resume["source_run_id"],
                "continues_fingerprint": resume["bundle_fingerprint"],
            }
        committed = self._commitments.begin(
            self._trace.run["run_id"],
            at=self._control.timestamp(),
            nonce=self._control.kernel_uuid(),
            ask=self._witness,
            **link,
        )
        # A log must hand back the commitment it wrote. Nothing else here can
        # tell a working implementation from a leftover test double: a `Mock()`
        # has every attribute, satisfies any Protocol, accepts every call and
        # returns another Mock — and an adversarial round found a run reporting
        # `completed` with an audit trail that was never written,
        # indistinguishable from one that was. The import is deliberately here
        # and not at module scope, so an unconfigured Runtime still loads none
        # of this (ADR-021 decision 1).
        from vitruvyan_motus.commitments import Commitment

        if not isinstance(committed, Commitment):
            raise TypeError(
                "a commitment log must return the Commitment it wrote; "
                f"{type(committed).__name__} is not one. A double that accepts "
                "every call and records nothing produces a run that reports "
                "success with no evidence behind it")

    def _commit_end(self, handle: _RunHandle) -> None:
        """Bind this run's outcome to the evidence that produced it.

        Written on every terminal path, because a BEGIN without an END is only
        a signal if it is rare — and if ordinary failures produced the same
        shape as suppression, it would be noise (ADR-020 decision 3).

        A trace with no derived root gets no END, and that is not an omission:
        `Trace.root` answers None when the document has not earned one, and an
        END exists to bind an outcome TO evidence. There is nothing to bind to,
        so the run is reported by its absence — as an execution that left no
        completion, never as suppression.

        A failure here does not mask the run's own exception. The missing END
        is itself the record of what happened, and raising over an in-flight
        error would replace a diagnosis with a symptom.
        """
        if self._commitments is None:
            return
        trace = handle.trace
        if trace is None:
            return
        root = trace.root
        if root is None:
            return
        records = trace.records
        outcome = records[-1]["kind"] if records else "unknown"
        # Read BEFORE the try. Inside an `except` block `sys.exc_info()` names
        # the exception being handled, so asking there always answers "one is
        # in flight" and the raise below never fires. This ran green until a
        # test asked for the other half of the rule.
        in_flight = sys.exc_info()[0] is not None
        try:
            committed = self._commitments.end(
                trace.run["run_id"], root=root, outcome=outcome,
                at=self._control.timestamp() if self._control else "",
                nonce=self._control.kernel_uuid() if self._control else "",
            )
            # Symmetric with `_commit_begin`, and it was missing here. A store
            # whose `end()` returns None without persisting anything let a run
            # report `completed` while the durable account held only its BEGIN
            # -- the exact silent no-op the BEGIN side already refused, and the
            # asymmetry survived a round because both halves were read
            # separately.
            from vitruvyan_motus.commitments import Commitment
            if not isinstance(committed, Commitment):
                raise TypeError(
                    "the commitment log's end() returned "
                    f"{type(committed).__name__}, not a Commitment. A run that "
                    "cannot show its END has no completion on record, and "
                    "reporting one would describe evidence that does not exist")
        except BaseException:
            if not in_flight:
                raise

    def _replace_trace(self, trace: Trace) -> None:
        self._trace = trace
        if self._run is not None:
            self._run.trace = trace

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
        # Seal FIRST, so the sink and the trace receive the identical dict. The
        # sink is handed the record before the trace appends it, so sealing at
        # append time wrote real hashes to memory and null ones to the artifact.
        record = self._trace._seal(record)
        if terminal:
            try:
                self._hub.persist(record, force=True)
            except BaseException as exc:
                if record["kind"] == "run_completed":
                    failure = self._trace._seal(
                        self._sink_failure_record(record["seq"], None, exc)
                    )
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
            failure = self._trace._seal(
                self._sink_failure_record(
                    self._control.next_seq(), record.get("seq"), exc  # type: ignore[union-attr]
                )
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
    ) -> list[dict[str, str]] | None:
        if declaration.reads_declared is None and declaration.writes_declared is None:
            return None
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
        # **Sorted by map key, not by insertion order.** `graph_fingerprint` is
        # computed over key-sorted canonical JSON, so two specs whose route
        # maps differ only in the order they were written are, by the
        # contract's own identity, THE SAME GRAPH. Emitting candidates in
        # insertion order made them produce different traces with different
        # roots — the fingerprint and the trace answering the same question
        # differently (#124, found by the Orbis integration).
        #
        # Order carries no semantics here and cannot: the schema admits three
        # condition kinds — `map`, `default`, `static` — and none is a
        # predicate, so a route is a key lookup and never a first-match scan.
        # `contract/validate.py` already agreed, comparing candidates as a
        # `Counter`; this line is what makes the runtime agree too.
        candidates = [
            {"condition": {"kind": "map", "key": key}, "target": step.map[key], "taken": False}
            for key in sorted(step.map)
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
                        # ADR-030 decision 1/2: `State.with_fact` no longer
                        # gates rule J4 — a State does not know the trace
                        # version it will end up in. This trace does, and
                        # checking the write HERE, inside the node's own
                        # try/except, makes a refusal indistinguishable from
                        # any other exception the node raised: it becomes this
                        # attempt's failure, not an exception out of `.run()`.
                        self._trace._refuse_j4(writes, "$.writes")
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
                    raise NodeFailed(
                        current_node, self._state, self._trace, error,
                        evidence=self._hub.evidence if self._hub is not None else "not-required",
                    ) from error
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
        handle, machine = self._start(
            state, run_id=run_id, replay=replay, copy_yields=False,
            start_node=start_node, resume_info=resume_info,
        )
        for _ in _drive(machine, _invoke_sync):
            pass
        return _result_of(handle)
