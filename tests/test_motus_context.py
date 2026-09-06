from __future__ import annotations

from datetime import datetime, timezone

import pytest

from vitruvyan_motus.context import ContextDraw, ReplayStatus, RunContext, _RunController


def test_node_context_has_only_the_three_mediated_capabilities():
    control = _RunController()
    context = control.node_context
    assert isinstance(context, RunContext)
    assert [name for name in dir(context) if not name.startswith("_")] == [
        "now", "rand", "record_effect", "uuid"
    ]


def test_kernel_sources_do_not_become_node_draws():
    instant = datetime(2026, 8, 4, tzinfo=timezone.utc)
    control = _RunController(clock=lambda: instant, identity=lambda: "kernel-id")
    assert control.timestamp() == "2026-08-04T00:00:00Z"
    assert control.kernel_uuid() == "kernel-id"
    assert control.next_seq() == 1
    assert control.draws_since(0) == ()


def test_node_draws_are_ordered_and_recordable():
    instant = datetime(2026, 8, 4, 12, 30, tzinfo=timezone.utc)
    control = _RunController(
        clock=lambda: instant,
        identity=lambda: "node-id",
        random_source=lambda: 0.25,
        replay=ReplayStatus.declared("full"),
    )
    ctx = control.node_context
    assert ctx.now() == instant
    assert ctx.rand() == 0.25
    assert ctx.uuid() == "node-id"
    # ADR-030 decision 5: from 3.2.0 the rand draw is recorded as the 53-bit
    # integer n the float is made from (0.25 == 2^51 / 2^53); the node still
    # receives the float.
    assert [draw.to_dict() for draw in control.draws_since(0)] == [
        {"source": "now", "value": "2026-08-04T12:30:00Z"},
        {"source": "rand", "value": 2 ** 51},
        {"source": "uuid", "value": "node-id"},
    ]


@pytest.mark.parametrize("cursor", [-1, 1, True, 1.5])
def test_draw_cursor_is_strict(cursor):
    control = _RunController()
    with pytest.raises((TypeError, ValueError)):
        control.draws_since(cursor)


def test_replay_status_is_canonical_and_only_degrades():
    control = _RunController(replay=ReplayStatus.declared("full"))
    status = control.downgrade("partial", "node:fetch:ambient_network")
    assert status.to_dict() == {
        "capability": "partial",
        "constraints": ["node:fetch:ambient_network"],
    }
    with pytest.raises(ValueError, match="only degrade"):
        control.downgrade("full", "impossible-upgrade")


@pytest.mark.parametrize(
    ("source", "value"),
    [("clock", "x"), ("rand", float("nan")), ("rand", 1.0),
     ("rand", 2 ** 53), ("rand", -1), ("uuid", 7)],
)
def test_context_draw_refuses_values_outside_its_wire_contract(source, value):
    with pytest.raises((TypeError, ValueError)):
        ContextDraw(source, value)  # type: ignore[arg-type]


def test_context_draw_accepts_a_53_bit_integer_and_a_pre_3_2_float():
    # The two wire forms, both produced by this package: the integer n from a
    # 3.2.0 run, and the float a pre-3.2.0 run recorded (reads keep working).
    assert ContextDraw("rand", 0).value == 0
    assert ContextDraw("rand", 2 ** 53 - 1).value == 2 ** 53 - 1
    assert ContextDraw("rand", 0.25).value == 0.25


def test_a_rand_source_that_is_not_k_over_2_53_is_refused():
    # A draw must be reproducible under the contract's formula n / 2^53. A
    # source returning 0.1 (== k / 2^55, not k / 2^53) cannot be recorded
    # honestly: the integer would either leave [0, 2^53) or reproduce a
    # different float. Refused rather than silently quantised.
    control = _RunController(random_source=lambda: 0.1)
    with pytest.raises(ValueError, match=r"2\^53"):
        control.node_context.rand()


def test_replay_constraints_are_strings_not_string_iterables():
    with pytest.raises(TypeError):
        ReplayStatus("partial", "not-a-sequence-of-constraints")  # type: ignore[arg-type]
    with pytest.raises(TypeError):
        ReplayStatus("partial", (42,))  # type: ignore[arg-type]
