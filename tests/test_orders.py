"""
Tests for the orderable contract (axis/orders.py) — Issue 8.

An order is what it contributes to a run plus what it says on the wire,
nothing else. These tests pin exactly that: OrderSpec is a frozen, flat
description a Runner can consume directly; Order is a one-method
structural Protocol; an order with no nodes at all (observer-only, or a
bus-driven package whose whole contract is its channels) is legal; and
the channel vocabulary is owned by the spec, not scattered in constants.
"""

import dataclasses

import pytest

from axis.orders import Channels, Order, OrderSpec
from axis.runner import Runner
from axis.state import GraphState, Fact
from axis.events import now
from axis.policy import Policy


def sample_node(state: GraphState) -> GraphState:
    return state.with_fact(Fact("order_ran", True, "sample_node", now()))


def test_order_spec_construction_and_frozen():
    """OrderSpec holds exactly what an order contributes: name, version,
    the axis range it was built against, and a flat node tuple. It's
    frozen — the same discipline as GraphState and Event."""
    spec = OrderSpec(
        name="axis-order-curator",
        version="0.1.0",
        requires_axis=">=0.4,<0.5",
        nodes=(sample_node,),
    )

    assert spec.name == "axis-order-curator"
    assert spec.version == "0.1.0"
    assert spec.requires_axis == ">=0.4,<0.5"
    assert spec.nodes == (sample_node,)
    assert spec.observers == ()  # default
    assert spec.channels == Channels()  # default: nothing on the wire

    with pytest.raises(dataclasses.FrozenInstanceError):
        spec.name = "renamed"


def test_toy_order_satisfies_order_protocol():
    """A class needs only a spec() method to be an Order — no base
    class, no registration. @runtime_checkable makes isinstance work."""
    class ToyOrder:
        def spec(self) -> OrderSpec:
            return OrderSpec(
                name="toy",
                version="0.0.1",
                requires_axis=">=0.4",
                nodes=(sample_node,),
            )

    toy = ToyOrder()
    assert isinstance(toy, Order)
    assert not isinstance(object(), Order)


def test_runner_consumes_order_spec_nodes_directly():
    """Runner(order.spec().nodes) — no adapter, no wrapper. An order's
    nodes trace exactly like any other node list."""
    class GreeterOrder:
        def spec(self) -> OrderSpec:
            return OrderSpec(
                name="greeter",
                version="1.0.0",
                requires_axis=">=0.4,<0.5",
                nodes=(sample_node,),
            )

    order = GreeterOrder()
    runner = Runner(order.spec().nodes, policy=Policy.STRICT)

    result = runner.run(GraphState.new("order-run"))

    assert result.fact("order_ran") is True
    assert len(result.events) > 0  # traced normally, same as any run


def test_order_spec_observers_attach_and_observe():
    """Observers declared on the spec are the Runner's own
    RunnerObserver — attach() is all a caller needs to do, no order-
    specific plumbing."""
    class RecordingObserver:
        def __init__(self):
            self.seen = []

        def observe(self, event_type, state, **kwargs):
            self.seen.append(event_type)

    observer = RecordingObserver()

    class ObservedOrder:
        def spec(self) -> OrderSpec:
            return OrderSpec(
                name="observed",
                version="1.0.0",
                requires_axis=">=0.4,<0.5",
                nodes=(sample_node,),
                observers=(observer,),
            )

    spec = ObservedOrder().spec()
    runner = Runner(spec.nodes, policy=Policy.STRICT)
    for obs in spec.observers:
        runner.attach(obs)

    runner.run(GraphState.new("order-observed"))

    assert "graph_start" in observer.seen
    assert "graph_end" in observer.seen
    assert len(observer.seen) > 2  # node-level events too


def test_order_spec_with_empty_nodes_is_legal():
    """An order with no nodes — an observer-only audit package, or a
    bus-driven order of pure decision functions the graph reaches over
    the wire — is a legal OrderSpec. nodes genuinely defaults."""
    spec = OrderSpec(
        name="axis-order-audit",
        version="0.1.0",
        requires_axis=">=0.4,<0.5",
    )

    assert spec.nodes == ()
    assert spec.observers == ()

    # A Runner built from it is a legal (if trivial) no-op graph.
    runner = Runner(spec.nodes, policy=Policy.STRICT)
    result = runner.run(GraphState.new("empty-order"))
    assert result.trace_id.startswith("empty-order-")


def test_channels_are_the_specs_own_wire_vocabulary():
    """A bus-driven order carries its channel names in the spec itself
    — one owner, frozen, so a loader can check that every `consumes`
    has a producer instead of the names drifting in per-package
    constant files."""
    channels = Channels(
        produces=("codex.restoration.completed",),
        consumes=("codex.restoration.requested",),
    )
    spec = OrderSpec(
        name="axis-order-codex-hunters",
        version="0.1.0",
        requires_axis=">=0.4,<0.5",
        channels=channels,
    )

    assert spec.channels.produces == ("codex.restoration.completed",)
    assert spec.channels.consumes == ("codex.restoration.requested",)

    with pytest.raises(dataclasses.FrozenInstanceError):
        spec.channels.produces = ()
