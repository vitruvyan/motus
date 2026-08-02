"""
axis.orders — the orderable contract.

An order is nodes plus observers, nothing else: a flat, ordered tuple of
Nodes the Runner consumes in sequence, and a tuple of observers the Runner
notifies. Not a graph builder, not a registry, not a plugin system — the
same discipline the kernel holds its own nodes to (state in, state out,
no knowledge of the runner) extended one level out.

Orders live OUTSIDE this repo, as separate installable distributions —
`axis-order-<name>` on PyPI, `axis_order_<name>` as the importable module
— depending on this kernel (`axis>=X,<Y`), never the reverse. The kernel
never imports an order; it only ever receives one, already built, from
its caller:

    from axis import Runner
    from axis_order_curator import CuratorOrder

    order = CuratorOrder()
    spec = order.spec()
    runner = Runner(spec.nodes)
    for observer in spec.observers:
        runner.attach(observer)

Discovery (entry points, a registry, a marketplace) is a later, separate
decision, earned only once there's a second real consumer of this
contract. This module is the contract alone: no hooks, no callbacks, no
discovery machinery.
"""

from dataclasses import dataclass
from typing import Protocol, Tuple, runtime_checkable

from axis.node import Node
from axis.runner import RunnerObserver


@dataclass(frozen=True)
class OrderSpec:
    """
    What an order hands the kernel: a name, a version, the axis version
    range it was built against, and what it contributes — nodes (the
    ORDER of execution, a flat tuple, not a graph) and observers (the
    Runner's own observer protocol, not a second notification system).

    requires_axis is a PEP 440 specifier string (e.g. ">=0.4,<0.5"),
    stored but not validated here — this is the contract, not a loader.
    A future loader is where `packaging.specifiers` checks it against
    the running axis.__version__ before handing the nodes to a Runner.
    """

    name: str
    version: str
    requires_axis: str
    nodes: Tuple[Node, ...]
    observers: Tuple[RunnerObserver, ...] = ()


@runtime_checkable
class Order(Protocol):
    """
    An order is anything that can describe itself as an OrderSpec.

    That's the whole contract — one method, no lifecycle hooks, no
    discovery. @runtime_checkable makes `isinstance(x, Order)` work,
    the cheapest possible seam for "is this an order" without a registry.
    """

    def spec(self) -> OrderSpec:
        ...
