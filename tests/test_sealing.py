"""What an anchoring cadence buys, and what it cannot buy at any cadence.

ADR-021 refuses to name an interval. These assert the arithmetic that lets a
deployment name its own, including the case where the honest answer is that no
interval works and the witness is what the run actually needs.
"""

from __future__ import annotations

import pytest

from vitruvyan_motus.sealing import (
    coverage, describe, interval_for, unprotected_fraction,
)


def test_coverage_falls_as_the_interval_grows():
    durations = [10.0] * 100
    assert coverage(durations, 1.0) == 1.0
    assert coverage(durations, 10.0) == 1.0
    assert coverage(durations, 20.0) == pytest.approx(0.5)
    assert coverage(durations, 100.0) == pytest.approx(0.1)


def test_the_unprotected_share_is_the_one_worth_reading():
    """ADR-020: a run that begins and ends between two anchors can have its
    whole episode removed, and the chain that remains is consistent about the
    runs that are left. That share is what an interval buys down."""
    durations = [1.0] * 10
    assert unprotected_fraction(durations, 10.0) == pytest.approx(0.9)


def test_the_interval_returned_is_the_largest_that_meets_the_target():
    """Longest interval means fewest anchors means lowest cost. A shorter one
    would meet the target too and charge for it."""
    durations = [4.0, 8.0, 16.0, 32.0]
    chosen = interval_for(durations, target=0.75)
    assert coverage(durations, chosen) >= 0.75
    assert coverage(durations, chosen * 1.01) < 0.75


def test_full_coverage_needs_an_interval_no_longer_than_the_shortest_run():
    durations = [3.0, 30.0, 300.0]
    assert interval_for(durations, target=1.0) == pytest.approx(3.0)


def test_no_measurement_is_refused_rather_than_defaulted():
    """The failure ADR-021 names: a number chosen because it sounded prudent."""
    with pytest.raises(ValueError, match="ADR-021 refuses"):
        coverage([], 60.0)
    with pytest.raises(ValueError, match="ADR-021 refuses"):
        interval_for([], target=0.9)


@pytest.mark.parametrize("bad", [0.0, -1.0, float("-0.0")])
def test_a_non_positive_interval_is_refused(bad):
    with pytest.raises(ValueError, match="positive number of seconds"):
        coverage([1.0], bad)


@pytest.mark.parametrize("bad", [0.0, -0.1, 1.5])
def test_an_impossible_target_is_refused(bad):
    with pytest.raises(ValueError, match="target coverage"):
        interval_for([1.0], target=bad)


def test_describe_prices_the_choice_without_choosing_it():
    durations = [60.0] * 100
    report = describe(durations, 3600.0)
    assert report["anchors_per_hour"] == pytest.approx(1.0)
    assert report["coverage"] == pytest.approx(1 / 60)
    assert report["unprotected"] > 0.98
    assert report["runs_measured"] == 100.0
    assert set(report) == {
        "interval_seconds", "anchors_per_hour", "coverage", "unprotected",
        "runs_measured", "median_duration", "p95_duration",
    }


def test_short_runs_cannot_be_covered_by_any_anchor_anybody_would_pay_for():
    """The finding this module exists to make visible, and it is the reason
    ADR-021 was right to refuse a number.

    Sub-second runs need a sub-second anchor to gain execution continuity, and
    no public chain does that at a price anybody pays. The answer is not to
    anchor harder — it is that the anchor cannot give this property to a run
    shorter than its own cadence, which is what the witness of ADR-020
    decision 3 is for."""
    subsecond = [0.4] * 1000
    assert interval_for(subsecond, target=0.9) < 0.5
    hourly = describe(subsecond, 3600.0)
    assert hourly["unprotected"] > 0.999, (
        "an hourly anchor was found to protect sub-second runs, which would "
        "mean this arithmetic is not measuring what ADR-020 describes")


def test_sealing_locally_is_not_the_rate_this_module_takes():
    """A checkpoint that has not left the operator's machine proves nothing to
    a third party (ADR-020). The interval that bounds the rewrite window is the
    ANCHORING one, and the report names it that way so nobody prices a local
    file write as though it were a transaction."""
    report = describe([10.0], 60.0)
    assert "anchors_per_hour" in report
    assert "checkpoints_per_hour" not in report
