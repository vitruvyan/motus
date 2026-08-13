"""What a checkpoint cadence buys, priced against the runs it is protecting.

ADR-021 decision 2 refuses to name a checkpoint interval "until it is derived
from run-duration distribution against cost per anchored checkpoint". **This
module ships no default and no timer.** `CommitmentLog.seal()` stays explicit,
because a library that starts sealing on a schedule of its own has acquired a
background thread nobody asked for. What ships is the arithmetic, so that
"measure first, then choose" is something a deployment can actually do.

**Sealing and anchoring are two rates, and only one of them protects
anything.** Sealing is local: it closes a Merkle window and writes a file the
same operator could rewrite. ADR-020 is explicit that *"checkpointed" means
externally anchored, never merely written* — a checkpoint that has not left the
operator's machine proves nothing to a third party. So the rate that bounds the
rewrite window is the **anchoring** rate, and every function here takes that
one. Sealing more often than you anchor is still worth doing, and it buys
something else: smaller windows mean shorter inclusion proofs and less
disclosure of unrelated commitments (ADR-021 decision 2). It does not buy
protection.

The quantity that matters comes from ADR-020: a run that begins and ends
between two anchors can have its whole episode removed, and the chain that
remains is perfectly consistent about the runs that are left. A run whose
`BEGIN` was anchored before it finished cannot. With anchors published every
``interval`` seconds and a run starting at an arbitrary moment, the wait to the
next anchor is uniform on ``[0, interval]``, so a run of duration ``d`` is
covered with probability ``min(d / interval, 1)``.

**The model's assumption, stated because it is the one that fails first:** run
starts are independent of the anchoring schedule. That is false for a
deployment whose work arrives on the same cron as its anchors, and there the
measured coverage will be worse than this arithmetic predicts.

**What the arithmetic usually says, and it is not what people expect.** For
runs measured in seconds, covering most of them needs an anchor every few
seconds — which no public chain will do at a price anybody pays. The honest
reading is not "anchor harder". It is that **an anchor cannot give execution
continuity to a run shorter than its own cadence, and that is precisely what
the witness in ADR-020 decision 3 is for**: an acknowledgement in milliseconds,
aggregated and anchored later on the anchor's timescale. Use this module to
find out whether your runs are long enough for the anchor to do the job, and
reach for the witness when they are not.

Nothing here is imported by the runtime.
"""

from __future__ import annotations

from typing import Sequence

__all__ = ["coverage", "unprotected_fraction", "interval_for", "describe"]


def _durations(samples: Sequence[float]) -> list[float]:
    values = [float(d) for d in samples]
    if not values:
        raise ValueError(
            "an interval derived from no measurement is a number chosen "
            "because it sounded prudent, which ADR-021 refuses")
    if any(d <= 0 for d in values):
        raise ValueError("a run duration must be positive")
    return values


def _interval(value: float) -> float:
    seconds = float(value)
    if not seconds > 0:
        raise ValueError("an anchoring interval must be a positive number of seconds")
    return seconds


def coverage(durations: Sequence[float], interval: float) -> float:
    """Fraction of runs whose `BEGIN` is anchored before the run finishes.

    These are the runs that gain what an anchor gives: their episode can no
    longer be removed wholesale, because a value covering it has already left
    the operator's control.
    """
    values = _durations(durations)
    seconds = _interval(interval)
    return sum(min(d / seconds, 1.0) for d in values) / len(values)


def unprotected_fraction(durations: Sequence[float], interval: float) -> float:
    """The complement, and the number worth putting in front of somebody.

    The share of runs that begin and end between two anchors — the class
    ADR-020 says can be removed entirely, leaving a chain that is perfectly
    consistent about the runs that remain.
    """
    return 1.0 - coverage(durations, interval)


def interval_for(durations: Sequence[float], *, target: float,
                 tolerance: float = 1e-9) -> float:
    """The LARGEST interval reaching `target` coverage — the cheapest one.

    Longer interval, fewer anchors, lower cost. Coverage falls monotonically as
    the interval grows, so the largest interval meeting the target is the one
    to run, and bisection finds it.
    """
    values = _durations(durations)
    if not 0.0 < target <= 1.0:
        raise ValueError("target coverage must be greater than 0 and at most 1")
    # Coverage is exactly 1 at the shortest run and below: anchor more often
    # than the fastest run finishes and every run is covered.
    low, high = min(values), max(values)
    if coverage(values, low) < target:
        raise ValueError("this target is not reachable for these durations")
    if coverage(values, high) >= target:
        high *= 2
        while coverage(values, high) >= target:
            high *= 2
    while high - low > tolerance * max(1.0, low):
        middle = (low + high) / 2
        if coverage(values, middle) >= target:
            low = middle
        else:
            high = middle
    return low


def describe(durations: Sequence[float], interval: float) -> dict[str, float]:
    """Everything needed to price a choice, in one call.

    `anchors_per_hour` is what multiplies by the cost of one anchored
    checkpoint. `unprotected` is what it buys down. The two together are the
    trade ADR-021 asks a deployment to make with its own numbers, and neither
    of them is a number this project may choose on somebody else's behalf.
    """
    seconds = _interval(interval)
    values = _durations(durations)
    ordered = sorted(values)
    return {
        "interval_seconds": seconds,
        "anchors_per_hour": 3600.0 / seconds,
        "coverage": coverage(values, seconds),
        "unprotected": unprotected_fraction(values, seconds),
        "runs_measured": float(len(values)),
        "median_duration": ordered[len(ordered) // 2],
        "p95_duration": ordered[min(len(ordered) - 1, int(len(ordered) * 0.95))],
    }
