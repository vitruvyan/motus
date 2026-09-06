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


def test_a_rand_source_that_is_not_k_over_2_53_is_quantised_not_refused():
    # ADR-030 decision 4 (2026-09-06 review correction): a caller-supplied
    # source is not required to already sit on the k / 2^53 grid. 0.1 is an
    # ordinary probability a caller is entitled to pass as `random_source=`;
    # refusing it (an earlier exact-match check did) refused most of the
    # domain the parameter used to accept, for no reason the contract states.
    # n = floor(v * 2^53) quantises it, and the node receives n / 2^53 — the
    # quantised value, not 0.1 itself — so what is recorded and what the node
    # saw always agree.
    control = _RunController(random_source=lambda: 0.1)
    given = control.node_context.rand()
    n = control.draws_since(0)[0].value
    assert isinstance(n, int) and not isinstance(n, bool)
    assert 0 <= n < 2 ** 53
    assert n / 2 ** 53 == given


def test_the_grid_point_recorded_is_the_one_below_the_value_not_the_nearest():
    # contract/node-protocol.md 6.1's formula is n = floor(v * 2^53); it
    # matters that the wording says "below" and not "nearest" (2026-09-06
    # review correction). 0.42's exact product with 2^53 lands precisely on
    # the halfway mark between two grid points -- Fraction(0.42) * 2**53 ==
    # 3783023686991216.5 -- so floor and Python's own round-half-EVEN happen
    # to agree here (`.attack/116/round3/r04_rand_quantisation.py`, section
    # B); 0.123456789 does not sit on a tie (frac 0.875) and floor and any
    # nearest-rounding convention disagree unambiguously, which is the
    # assertion that actually distinguishes the two formulas.
    control = _RunController(random_source=lambda: 0.42)
    given = control.node_context.rand()
    n = control.draws_since(0)[0].value
    assert n == 3783023686991216            # floor, not 3783023686991217 (ceiling)
    assert given == 0.41999999999999993     # moved AWAY from 0.42, not toward it
    assert given != 0.42

    control = _RunController(random_source=lambda: 0.123456789)
    given = control.node_context.rand()
    n = control.draws_since(0)[0].value
    assert n == 1111999897873515            # floor: round() would give ...516
    assert given == 0.1234567889999999


def test_a_rand_source_outside_the_unit_interval_is_still_refused():
    for bad in (1.0, -0.0001, float("nan"), float("inf")):
        control = _RunController(random_source=lambda bad=bad: bad)
        with pytest.raises(ValueError, match=r"\[0, 1\)"):
            control.node_context.rand()


def test_replay_constraints_are_strings_not_string_iterables():
    with pytest.raises(TypeError):
        ReplayStatus("partial", "not-a-sequence-of-constraints")  # type: ignore[arg-type]
    with pytest.raises(TypeError):
        ReplayStatus("partial", (42,))  # type: ignore[arg-type]
