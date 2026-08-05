from __future__ import annotations

import pickle
import threading

import pytest

from vitruvyan_motus.errors import NodeConfigurationError, SinkFailed
from vitruvyan_motus.graph import GraphSpec
from vitruvyan_motus.runtime import Runtime
from vitruvyan_motus.state import State
from vitruvyan_motus.trace import Rejection


def _single_spec() -> GraphSpec:
    return GraphSpec.from_dict({
        "schema_version": "1.0.0",
        "name": "reaudit-regression",
        "version": "1.0.0",
        "entry": "node",
        "nodes": [{"name": "node", "effect_class": "pure"}],
        "transitions": {"node": {"kind": "terminal"}},
    })


def test_exhausted_stream_context_does_not_cancel_the_next_run() -> None:
    calls: list[str] = []

    def node(state: State) -> State:
        calls.append(state.intent or "")
        return state

    runtime = Runtime(_single_spec(), {"node": node})
    with runtime.stream(State.empty("first"), run_id="first") as driver:
        assert list(driver)[-1]["kind"] == "run_completed"

    second = runtime.run(State.empty("second"), run_id="second")
    assert second.status == "completed"
    assert calls == ["first", "second"]


def test_idle_cancel_after_a_completed_run_cannot_leak_forward() -> None:
    runtime = Runtime(_single_spec(), {"node": lambda state: state})
    assert runtime.run(run_id="first").status == "completed"
    assert runtime.cancel("too late") is False
    assert runtime.run(run_id="second").status == "completed"


def test_concurrent_cancel_never_leaks_into_the_following_run() -> None:
    spec = _single_spec()
    for index in range(100):
        runtime = Runtime(spec, {"node": lambda state: state})
        first = []
        thread = threading.Thread(
            target=lambda: first.append(runtime.run(run_id=f"first-{index}"))
        )
        thread.start()
        runtime.cancel("concurrent request")
        thread.join(timeout=2)
        assert not thread.is_alive()
        assert len(first) == 1
        assert runtime.run(run_id=f"second-{index}").status == "completed"


def test_cancel_before_first_run_is_bound_once() -> None:
    runtime = Runtime(_single_spec(), {"node": lambda state: state})
    assert runtime.cancel("shutdown before start") is True
    assert runtime.run(run_id="first").status == "cancelled"
    assert runtime.run(run_id="second").status == "completed"


class _RunSink:
    def __init__(self, *, fail_write: bool = False) -> None:
        self.fail_write = fail_write
        self.records: list[dict] = []

    def write(self, records) -> None:
        if self.fail_write:
            raise OSError("cannot write")
        self.records.extend(records)


class _Sink:
    def __init__(self, *, fail_open: bool = False, fail_write: bool = False) -> None:
        self.fail_open = fail_open
        self.session = _RunSink(fail_write=fail_write)

    def open_run(self, header):
        if self.fail_open:
            raise OSError("cannot open")
        return self.session


@pytest.mark.parametrize("sink", [_Sink(fail_open=True), _Sink(fail_write=True)])
def test_attached_in_memory_sink_failure_prevents_logical_success(sink: _Sink) -> None:
    runtime = Runtime(_single_spec(), {"node": lambda state: state}, sink=sink)
    with pytest.raises(SinkFailed) as raised:
        runtime.run(run_id="sink-failure")
    assert raised.value.trace.records[-1]["kind"] == "run_failed"
    assert raised.value.trace.records[-1]["cause"]["kind"] == "sink_failure"


def test_attached_in_memory_sink_delivery_is_disclosed() -> None:
    sink = _Sink()
    result = Runtime(_single_spec(), {"node": lambda state: state}, sink=sink).run(
        run_id="sink-header"
    )
    assert result.trace.run["sink"] == {
        "flush_interval_ms": 0,
        "chunk_records": 1,
    }
    assert sink.session.records == list(result.trace.records)


def test_rejection_absence_survives_pickle() -> None:
    original = Rejection("candidate", "not selected", "2026-08-05T00:00:00Z")
    restored = pickle.loads(pickle.dumps(original))
    assert restored.to_dict() == original.to_dict()
    assert "evidence" not in restored.to_dict()


def test_motus_config_failure_is_a_typed_pre_run_error() -> None:
    class Node:
        def __init__(self) -> None:
            self.calls = 0

        def motus_config(self):
            self.calls += 1
            if self.calls > 1:
                raise RuntimeError("config exploded")
            return {"stable": True}

        def __call__(self, state: State) -> State:
            return state

    runtime = Runtime(_single_spec(), {"node": Node()})
    with pytest.raises(NodeConfigurationError) as raised:
        runtime.run(run_id="config-error")
    assert isinstance(raised.value.cause, RuntimeError)
    assert runtime.trace is None
