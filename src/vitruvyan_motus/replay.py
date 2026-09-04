"""Portable bundles plus playback, pure verification and safe resume."""

from __future__ import annotations

import hashlib
import html
import inspect
import json
from dataclasses import dataclass
from typing import Any, Callable, Mapping

from vitruvyan_motus.context import ReplayStatus, RunContext
from vitruvyan_motus.errors import ReplayMismatch, ReplayUnsupported, UnsafeResume
from vitruvyan_motus.graph import GraphSpec, TransitionKind
from vitruvyan_motus.runtime import RunResult, Runtime
from vitruvyan_motus.state import State
from vitruvyan_motus.trace import _loads_canonical, Trace, _canonical_bytes

__all__ = ["TraceBundle", "ReplayResult", "ReplayEngine"]


def _is_asynchronous(value: Any) -> bool:
    """Whether a node handed back something this synchronous path cannot drive.

    ``inspect.isawaitable`` is False for an async *generator* object, so a
    node written ``async def … yield`` would otherwise fall through to the
    ``isinstance(returned, State)`` check and be reported as a divergence —
    the contract's signal that the code changed — against code that did not.
    """
    return inspect.isawaitable(value) or inspect.isasyncgen(value)


def _discard_unreplayable(value: Any) -> None:
    """Close what verify declines, including an asynchronous ``close``."""
    closer = getattr(value, "close", None)
    if not callable(closer):
        return
    result = closer()
    if inspect.isawaitable(result):
        # `async def close()` hands back a coroutine we cannot await here;
        # closing it is what stops it complaining about never being awaited.
        inner = getattr(result, "close", None)
        if callable(inner):
            inner()


def _assert_bundle_semantics(spec: GraphSpec, trace: Trace) -> None:
    """Dependency-free import fence for the semantics replay relies on."""
    run = trace.run
    graph = run.get("graph") or {}
    if (
        graph.get("name") != spec.name
        or graph.get("version") != spec.version
        or graph.get("spec_schema_version") != spec.schema_version
        or graph.get("graph_fingerprint") != spec.graph_fingerprint
    ):
        raise ValueError("trace graph identity does not match the bundled GraphSpec")
    resume = run.get("resume") or {}
    if resume and resume.get("source_run_id") == run.get("run_id"):
        raise ValueError("a resumed segment must have a new run_id")
    declarations = spec.compiled.declarations
    expected_start = resume.get("start_node", spec.entry)
    if expected_start not in declarations:
        raise ValueError("trace resume start node is not declared")
    records = trace.records
    state = _initial_state(trace)
    if len(records) > 1:
        first = records[1]
        if first.get("kind") == "attempt_started" and first.get("node") != expected_start:
            raise ValueError("trace does not start at its declared entry/resume node")
    for index, record in enumerate(records):
        kind = record.get("kind")
        if kind in ("attempt_started", "transition"):
            node = record.get("node")
            if node not in declarations:
                raise ValueError(f"trace names undeclared node {node!r}")
        if kind == "transition":
            declaration = declarations[record["node"]]
            if record.get("effect_class") != declaration.effect_class.value:
                raise ValueError("trace effect_class disagrees with the GraphSpec")
            if index == 0:
                raise ValueError("transition has no opening attempt")
            opener = records[index - 1]
            if not (
                opener.get("kind") == "attempt_started"
                and opener.get("node") == record.get("node")
                and opener.get("attempt") == record.get("attempt")
            ):
                raise ValueError("transition does not match its opening attempt")
            if (
                record.get("outcome") == "returned"
                and record.get("disposition") == "commit"
            ):
                state = state._replay_commit(record["writes"], record["seq"])
        elif kind == "routing":
            if record.get("after") not in declarations:
                raise ValueError("routing names an undeclared predecessor")
            selected = record.get("selected")
            if selected != "END" and selected not in declarations:
                raise ValueError("routing selects an undeclared node")
            if index == 0 or not (
                records[index - 1].get("kind") == "transition"
                and records[index - 1].get("node") == record.get("after")
            ):
                raise ValueError("routing does not follow its transition")
            step = spec.compiled.transitions[record["after"]]
            expected = _expected_routing_semantics(step, state)
            observed = {
                key: record.get(key)
                for key in (
                    "on",
                    "value",
                    "origin",
                    "outcome",
                    "selected",
                    "candidates",
                )
            }
            if _canonical_bytes(_ordered(observed)) != _canonical_bytes(
                _ordered(expected)
            ):
                raise ValueError(
                    "routing semantics disagree with the GraphSpec and recorded state"
                )


def _ordered(semantics: dict[str, Any]) -> dict[str, Any]:
    """The same routing evidence with its candidates in a canonical order.

    **Comparing candidates as a sequence was order-sensitive, and order carries
    no meaning here.** A route is a key lookup — the schema admits `map`,
    `default` and `static` conditions and none of them is a predicate — so two
    candidate lists holding the same entries in a different order describe the
    same routing. `contract/validate.py` already knew this and compares them as
    a `Counter`; this function is what makes replay agree.

    It is also what keeps #124's fix backward-compatible. The runtime now emits
    candidates sorted, but every trace written before that change carries them
    in the order its GraphSpec happened to be written in. Sorting only the
    expectation would have refused those traces — turning a fix for evidence
    that could not be compared into a reason that older evidence could not be
    verified, which is a worse defect than the one being fixed.

    Nothing is weakened by this, and the reason is NOT the one an earlier draft
    of this docstring gave. It said "tampering is caught by the chain, which is
    its job" — but rule T11 is explicit that an editor who alters a record "can
    recompute a well-formed hash, and under 3.0.0 can reseal the entire chain".
    The catcher is the **anchored root**, not the chain, and the re-sealing
    helper in this fix's own test is exactly what such an editor would run.

    What actually makes this safe is narrower and checkable: `_ordered` sorts
    the whole candidate dict, `taken` included, so the comparison is multiset
    equality over `(condition, target, taken)` triples — strictly STRONGER than
    `contract/validate.py`'s T8, which compares a `Counter` and drops `taken`.
    Multiplicity, length, the taken-assignment and the target pairing all
    survive. An adversarial round tried candidate deletion, duplication,
    invention, target redirection at all four positions and a moved `taken`
    flag, each also permuted, and every one is still refused. Reordering alone
    changes nothing a reader reads, so it is the one edit an attacker gains
    nothing from.
    """
    candidates = semantics.get("candidates")
    if not isinstance(candidates, list):
        return semantics
    return {
        **semantics,
        "candidates": sorted(candidates, key=lambda c: _canonical_bytes(c)),
    }


def _expected_routing_semantics(step: Any, state: State) -> dict[str, Any]:
    """Recompute the exact routing evidence without trusting persisted fields."""
    if step.kind == TransitionKind.TERMINAL:
        return {
            "on": None,
            "value": None,
            "origin": None,
            "outcome": "static",
            "selected": "END",
            "candidates": [
                {
                    "condition": {"kind": "static"},
                    "target": "END",
                    "taken": True,
                }
            ],
        }
    if step.kind == TransitionKind.NEXT:
        return {
            "on": None,
            "value": None,
            "origin": None,
            "outcome": "static",
            "selected": step.to,
            "candidates": [
                {
                    "condition": {"kind": "static"},
                    "target": step.to,
                    "taken": True,
                }
            ],
        }

    latest = state._latest_decision(step.on)
    if latest is None:
        value, origin = None, {"kind": "absent"}
    else:
        value, origin = latest
    # Sorted, exactly as the runtime emits them — see runtime.py's note and
    # #124. Two copies of one ordering rule is one copy too many, and the
    # duplication is why the defect could exist at all; it stays only because
    # this module recomputes rather than imports, deliberately.
    #
    # **A mutation probe reverting this sort SURVIVES, and it is an equivalent
    # mutant rather than a coverage gap.** `_expected_routing_semantics` has
    # exactly one caller, and that caller passes both sides through `_ordered`
    # before comparing — so the order this list is built in is provably
    # unobservable. The sort stays anyway: an expectation that agreed with the
    # runtime only by accident of a normaliser downstream is an expectation
    # waiting for somebody to remove the normaliser. Do not invent a contrived
    # test to kill this mutant; there is nothing to observe.
    candidates = [
        {
            "condition": {"kind": "map", "key": key},
            "target": step.map[key],
            "taken": False,
        }
        for key in sorted(step.map)
    ]
    if step.default is not None:
        candidates.append(
            {
                "condition": {"kind": "default"},
                "target": step.default,
                "taken": False,
            }
        )
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
    return {
        "on": step.on,
        "value": value,
        "origin": origin,
        "outcome": outcome,
        "selected": selected,
        "candidates": candidates,
    }


def _initial_state(trace: Trace) -> State:
    started = next(
        (record for record in trace.records if record.get("kind") == "run_started"),
        None,
    )
    if started is None:
        raise ValueError("trace has no run_started record")
    return State.from_snapshot(
        started["initial_state"], intent=started.get("intent", ""),
        metadata=trace.run.get("metadata", {}),
    )


def _apply_commits(state: State, records: tuple[dict[str, Any], ...]) -> State:
    current = state
    for record in records:
        if (
            record.get("kind") == "transition"
            and record.get("outcome") == "returned"
            and record.get("disposition") == "commit"
        ):
            current = current._replay_commit(record["writes"], record["seq"])
    return current


@dataclass(frozen=True, slots=True)
class ReplayResult:
    mode: str
    state: State
    trace: Trace
    verified: tuple[tuple[str, int], ...] = ()


@dataclass(frozen=True, slots=True)
class TraceBundle:
    """The exact graph declaration bound to one trace document."""

    spec: GraphSpec
    trace: Trace

    def __post_init__(self) -> None:
        if not isinstance(self.spec, GraphSpec) or not isinstance(self.trace, Trace):
            raise TypeError("TraceBundle requires GraphSpec and Trace")
        _assert_bundle_semantics(self.spec, self.trace)

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> "TraceBundle":
        if (
            not isinstance(value, dict)
            or set(value) != {"bundle_version", "graph_spec", "trace"}
            or value.get("bundle_version") != "1.0.0"
        ):
            raise ValueError("unsupported trace bundle")
        return cls(
            GraphSpec.from_dict(value["graph_spec"]), Trace.from_dict(value["trace"])
        )

    @classmethod
    def from_json(cls, value: str) -> "TraceBundle":
        """A loader that holds the document TEXT, so it applies the text rules.

        It did not, and ADR-024 decision 3 enumerates where the guarantee lives
        and did not name this one — which is the single thing that decision
        exists to prevent. The #74 tamper and a repeated member both reached a
        genuine root and a genuine bundle fingerprint through the public replay
        and verify entry point.
        """
        return cls.from_dict(_loads_canonical(value))

    def to_dict(self) -> dict[str, Any]:
        return {
            "bundle_version": "1.0.0",
            "graph_spec": self.spec.to_dict(),
            "trace": self.trace.to_dict(),
        }

    def to_json(self) -> str:
        return json.dumps(
            self.to_dict(), ensure_ascii=False, separators=(",", ":"), allow_nan=False
        )

    @property
    def fingerprint(self) -> str:
        return "bundle:sha256:" + hashlib.sha256(
            _canonical_bytes(self.to_dict())
        ).hexdigest()

    def explain(self) -> dict[str, Any]:
        """Return a deterministic, machine-readable causal explanation."""
        steps: list[dict[str, Any]] = []
        for record in self.trace.records:
            kind = record["kind"]
            if kind == "transition":
                steps.append({
                    "seq": record["seq"], "kind": kind, "node": record["node"],
                    "attempt": record["attempt"], "outcome": record["outcome"],
                    "disposition": record["disposition"],
                    "read_keys": [item["key"] for item in record["reads"]],
                    "write_keys": [
                        item.get("key", item.get("what"))
                        for collection in record["writes"].values()
                        for item in collection
                    ],
                    "effects": len(record["effects"]),
                })
            elif kind == "routing":
                steps.append({
                    "seq": record["seq"], "kind": kind, "after": record["after"],
                    "on": record["on"], "value": record["value"],
                    "outcome": record["outcome"], "selected": record["selected"],
                    "origin": record["origin"],
                })
            elif kind.startswith("run_") and kind != "run_started":
                steps.append({"seq": record["seq"], "kind": kind})
        last_kind = self.trace.records[-1]["kind"] if self.trace.records else None
        terminal = (
            last_kind
            if last_kind in ("run_completed", "run_failed", "run_cancelled")
            else "incomplete"
        )
        return {
            "bundle_fingerprint": self.fingerprint,
            "run_id": self.trace.run["run_id"],
            "graph": self.trace.run["graph"],
            "terminal": terminal,
            "steps": steps,
        }

    def to_html(self) -> str:
        """Render a deterministic standalone viewer with no remote assets."""
        explanation = self.explain()
        rows = []
        for step in explanation["steps"]:
            detail = html.escape(json.dumps(step, ensure_ascii=False, sort_keys=True))
            rows.append(
                f"<tr><td>{step['seq']}</td><td>{html.escape(step['kind'])}</td>"
                f"<td><code>{detail}</code></td></tr>"
            )
        return (
            "<!doctype html><html><head><meta charset=\"utf-8\"><title>Motus trace "
            + html.escape(str(explanation["run_id"]))
            + "</title><style>body{font:14px system-ui;margin:2rem;color:#17202a}"
              "table{border-collapse:collapse;width:100%}th,td{border:1px solid #ccd1d1;"
              "padding:.5rem;text-align:left;vertical-align:top}code{white-space:pre-wrap}"
              "</style></head><body><h1>Vitruvyan Motus trace</h1><p><b>Run:</b> "
            + html.escape(str(explanation["run_id"]))
            + "</p><p><b>Bundle:</b> <code>"
            + html.escape(self.fingerprint)
            + "</code></p><table><thead><tr><th>Seq</th><th>Kind</th><th>Evidence</th>"
              "</tr></thead><tbody>" + "".join(rows) + "</tbody></table></body></html>"
        )


@dataclass(frozen=True, slots=True)
class _ReplayInvoke:
    """One request from the comparison machine to re-execute one pure node."""

    node: Callable[..., Any]
    attempt: Any
    ctx: Any
    wants_context: bool


def _replay_invoke_sync(request: _ReplayInvoke) -> tuple[Any, BaseException | None, bool]:
    """Answer a re-execution request on the calling thread.

    An awaitable here cannot be driven, so it is closed and refused. Cleanup
    runs INSIDE the boundary: closing what we decline can itself raise, and
    that must be an ordinary re-execution failure rather than an arbitrary
    exception escaping verify(). Only the refusal is raised outside, because
    ReplayMismatch and UnsafeResume are both ReplayError subclasses — catching
    ReplayError here would swallow a node's own failure as success.
    """
    returned: Any = None
    unreplayable = False
    try:
        returned = request.node(request.attempt, request.ctx) if request.wants_context \
            else request.node(request.attempt)
        if _is_asynchronous(returned):
            _discard_unreplayable(returned)
            unreplayable = True
    except ReplayMismatch:
        raise
    except BaseException as exc:
        return None, exc, False
    return returned, None, unreplayable


async def _replay_invoke_async(request: _ReplayInvoke) -> tuple[Any, BaseException | None, bool]:
    """Answer a re-execution request by awaiting what is awaitable.

    An async generator is still refused: it produces a stream, not the single
    State a transition recorded, so there is nothing to compare against.
    """
    returned: Any = None
    try:
        returned = request.node(request.attempt, request.ctx) if request.wants_context \
            else request.node(request.attempt)
        if inspect.isasyncgen(returned):
            _discard_unreplayable(returned)
            return None, None, True
        if inspect.isawaitable(returned):
            returned = await returned
    except ReplayMismatch:
        raise
    except BaseException as exc:
        return None, exc, False
    return returned, None, False


def _drive_verify(machine: Any, invoke: Callable[[_ReplayInvoke], Any]) -> "ReplayResult":
    reply: Any = None
    try:
        while True:
            request = machine.send(reply)
            reply = invoke(request)
    except StopIteration as stop:
        return stop.value
    finally:
        machine.close()


async def _adrive_verify(machine: Any, invoke: Callable[[_ReplayInvoke], Any]) -> "ReplayResult":
    reply: Any = None
    try:
        while True:
            request = machine.send(reply)
            reply = await invoke(request)
    except StopIteration as stop:
        return stop.value
    finally:
        machine.close()


class ReplayEngine:
    """Replay one immutable bundle under explicit, bounded modes."""

    def __init__(self, bundle: TraceBundle) -> None:
        if not isinstance(bundle, TraceBundle):
            raise TypeError("ReplayEngine requires TraceBundle")
        self.bundle = bundle

    def playback(self) -> ReplayResult:
        initial = _initial_state(self.bundle.trace)
        state = _apply_commits(initial, self.bundle.trace.records)
        return ReplayResult("playback", state, self.bundle.trace)

    def verify(self, nodes: Mapping[str, Callable[..., State]]) -> ReplayResult:
        """Re-execute the pure nodes and check they still agree with the record.

        Drives :meth:`_verify` synchronously. An ``async def`` node cannot be
        driven here and is refused with :class:`ReplayUnsupported`; use
        :meth:`averify`.
        """
        return _drive_verify(self._verify(nodes), _replay_invoke_sync)

    async def averify(self, nodes: Mapping[str, Callable[..., Any]]) -> ReplayResult:
        """The asynchronous twin of :meth:`verify`.

        The same comparison state machine, awaited rather than called — the
        same inversion the runtime applies to execution, and for the same
        reason: a second copy of the comparison logic could drift from the
        first, and guarantees.md invariant I exists to forbid exactly that.
        A node that is both ``async def`` and ``pure`` is verifiable here.
        """
        return await _adrive_verify(self._verify(nodes), _replay_invoke_async)

    def _verify(self, nodes: Mapping[str, Callable[..., Any]]) -> Any:
        registry = dict(nodes)
        state = _initial_state(self.bundle.trace)
        declarations = self.bundle.spec.compiled.declarations
        verified: list[tuple[str, int]] = []
        for record in self.bundle.trace.records:
            if record.get("kind") != "transition":
                continue
            declaration = declarations[record["node"]]
            if declaration.effect_class.value == "pure":
                node = registry.get(record["node"])
                if node is None:
                    raise ValueError(f"missing pure node {record['node']!r} for verify replay")
                attempt = state._attempt_view(self.bundle.trace._runtime_log)
                draws = list(record["context_draws"])
                cursor = 0

                def take(source: str) -> Any:
                    nonlocal cursor
                    if cursor >= len(draws) or draws[cursor]["source"] != source:
                        raise ReplayMismatch(record["node"], record["seq"], "context_draws")
                    value = draws[cursor]["value"]
                    cursor += 1
                    return value

                def now():
                    from datetime import datetime
                    return datetime.fromisoformat(str(take("now")).replace("Z", "+00:00"))

                def reject_effect(_effect):
                    raise ReplayMismatch(record["node"], record["seq"], "effects")

                ctx = RunContext(now, lambda: float(take("rand")), lambda: str(take("uuid")), reject_effect)
                positional = [
                    p for p in inspect.signature(node).parameters.values()
                    if p.kind in (p.POSITIONAL_ONLY, p.POSITIONAL_OR_KEYWORD)
                ]
                # The comparison never calls the node itself: it asks, and a
                # driver answers. One state machine, two thin drivers -- the
                # same inversion `Runtime._execute` uses, for the same reason.
                returned, raised, unreplayable = yield _ReplayInvoke(
                    node, attempt, ctx, len(positional) == 2
                )
                if unreplayable and raised is None:
                    raise ReplayUnsupported(
                        f"pure node {record['node']!r} is asynchronous; "
                        "verify() drives nodes synchronously and cannot "
                        "re-execute it -- use ReplayEngine.averify()"
                    )
                if record["outcome"] == "returned":
                    if raised is not None or not isinstance(returned, State):
                        raise ReplayMismatch(record["node"], record["seq"], "outcome") from raised
                    try:
                        attempt._committed(returned, record["seq"])
                    except BaseException as exc:
                        raise ReplayMismatch(record["node"], record["seq"], "lineage") from exc
                    if returned._writes_wire() != record["writes"]:
                        raise ReplayMismatch(record["node"], record["seq"], "writes")
                else:
                    expected_type = (record.get("error") or {}).get("type")
                    if expected_type == "DeclarationViolation":
                        # Transactional failure intentionally discarded the
                        # attempted write values. Their keys remain auditable,
                        # but value-level verify would have to invent evidence.
                        raise ReplayMismatch(
                            record["node"], record["seq"],
                            "attempted_writes_not_recorded",
                        )
                    elif raised is None or type(raised).__name__ != expected_type:
                        raise ReplayMismatch(record["node"], record["seq"], "outcome") from raised
                if attempt._reads_wire() != record["reads"]:
                    raise ReplayMismatch(record["node"], record["seq"], "reads")
                if cursor != len(draws):
                    raise ReplayMismatch(record["node"], record["seq"], "context_draws")
                verified.append((record["node"], record["seq"]))
            if record.get("outcome") == "returned" and record.get("disposition") == "commit":
                state = state._replay_commit(record["writes"], record["seq"])
        return ReplayResult("verify", state, self.bundle.trace, tuple(verified))

    def _next_node(self, state: State) -> str:
        records = self.bundle.trace.records
        if records and records[-1]["kind"] in ("run_completed", "run_failed", "run_cancelled"):
            raise UnsafeResume("a terminal trace cannot be resumed")
        if not records or records[-1]["kind"] == "run_started":
            return self.bundle.spec.entry
        record = records[-1]
        if record["kind"] == "routing":
            if record["selected"] == "END":
                raise UnsafeResume("the persisted route already selected END")
            return record["selected"]
        if record["kind"] == "attempt_started":
            return record["node"]
        if record["kind"] != "transition":
            raise UnsafeResume(f"cannot derive a resume point after {record['kind']!r}")
        if record["disposition"] == "retry":
            return record["node"]
        if record["disposition"] == "abort":
            raise UnsafeResume("an aborted attempt must be closed, not resumed")
        if record["disposition"] not in ("commit", "continue"):
            raise UnsafeResume("unsupported transition disposition at resume boundary")
        step = self.bundle.spec.compiled.transitions[record["node"]]
        if step.kind == TransitionKind.NEXT:
            assert step.to is not None
            return step.to
        if step.kind == TransitionKind.TERMINAL:
            raise UnsafeResume("the last transition routes to terminal completion")
        assert step.on is not None
        value = state.decision(step.on)
        if isinstance(value, str) and value in step.map:
            return step.map[value]
        if isinstance(value, str) and step.default is not None:
            return step.default
        raise UnsafeResume("the last transition's routing decision is a miss")

    def _assert_effect_safe(self) -> None:
        records = self.bundle.trace.records
        if records and records[-1].get("kind") == "attempt_started":
            node = records[-1]["node"]
            if self.bundle.spec.compiled.declarations[node].effect_class.value == "external_effect":
                raise UnsafeResume(
                    "an in-flight external effect has unknown outcome and cannot be resumed"
                )
        for record in records:
            if record.get("kind") != "transition":
                continue
            declaration = self.bundle.spec.compiled.declarations[record["node"]]
            effects = record.get("effects", [])
            if declaration.effect_class.value != "external_effect":
                continue
            if not effects:
                raise UnsafeResume(
                    "an external-effect attempt has no effect evidence; resume is unsafe"
                )
            external = [
                effect for effect in effects
                if effect.get("class") == "external_effect"
            ]
            if not external:
                raise UnsafeResume(
                    "an external-effect attempt has no external-effect receipt"
                )
            for effect in external:
                receipt = effect.get("receipt") or {}
                if not effect.get("idempotency_key") or receipt.get("status") != "completed":
                    raise UnsafeResume(
                        "external effects require an idempotency key and completed receipt"
                    )

    def resume(
        self, runtime: Runtime, *, run_id: str | None = None
    ) -> RunResult:
        if not isinstance(runtime, Runtime):
            raise TypeError("resume requires Runtime")
        if runtime.spec.graph_fingerprint != self.bundle.spec.graph_fingerprint:
            raise UnsafeResume("runtime graph does not match the trace bundle")
        if run_id == self.bundle.trace.run["run_id"]:
            raise UnsafeResume("a resumed segment must have a new run_id")
        self._assert_effect_safe()
        playback = self.playback()
        start_node = self._next_node(playback.state)
        source = self.bundle.trace
        state = State.from_snapshot(
            playback.state.snapshot(),
            intent=next(r for r in source.records if r["kind"] == "run_started").get("intent", ""),
            metadata={**source.run.get("metadata", {}), "causation_id": source.run["run_id"]},
        )
        resume_info = {
            "source_run_id": source.run["run_id"],
            "source_seq": source.records[-1]["seq"] if source.records else 0,
            "start_node": start_node,
            "bundle_fingerprint": self.bundle.fingerprint,
        }
        try:
            return runtime._run_from(
                state, start_node=start_node, resume_info=resume_info, run_id=run_id,
                replay=ReplayStatus.declared(
                    "partial", (f"resume:{source.run['run_id']}",)
                ),
            )
        except ValueError as exc:
            if str(exc) == "a resumed segment must have a new run_id":
                raise UnsafeResume(str(exc)) from exc
            raise
