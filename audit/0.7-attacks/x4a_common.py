"""Shared fixtures for the x4a_* concurrency round (_ObservationHub).

House style follows .attack/x2_common.py: a Report object, named cases,
PASS/FAIL lines.  Nothing here modifies the product; every helper only
builds graphs, sinks and listeners out of the public surface.
"""

from __future__ import annotations

import sys
import threading
import time
from typing import Any

from vitruvyan_motus import DurabilityProfile, Fact, GraphSpec, Runtime, State

# Widen the interleavings.  This changes scheduling granularity only.
sys.setswitchinterval(1e-6)


def chain_doc(n: int, name: str = "x4a-chain") -> dict[str, Any]:
    """A linear graph of ``n`` pure nodes, n0 -> n1 -> ... -> END."""
    nodes = [{"name": f"n{i}", "effect_class": "pure"} for i in range(n)]
    transitions: dict[str, Any] = {}
    for i in range(n - 1):
        transitions[f"n{i}"] = {"kind": "next", "to": f"n{i + 1}"}
    transitions[f"n{n - 1}"] = {"kind": "terminal"}
    return {
        "schema_version": "1.0.0",
        "name": name,
        "version": "1.0.0",
        "entry": "n0",
        "nodes": nodes,
        "transitions": transitions,
    }


def chain(n: int, name: str = "x4a-chain") -> GraphSpec:
    return GraphSpec.from_dict(chain_doc(n, name))


def writer_nodes(n: int, pause_s: float = 0.0) -> dict[str, Any]:
    """Node registry for :func:`chain`; each node writes one fact."""

    def make(i: int):
        def node(state: State) -> State:
            if pause_s:
                time.sleep(pause_s)
            return state.with_fact(
                Fact(key=f"k{i}", value=i, source=f"n{i}", ts="2026-08-05T00:00:00Z")
            )
        node.__qualname__ = f"x4a_node_{i}"
        return node

    return {f"n{i}": make(i) for i in range(n)}


class RecordingSink:
    """A TraceSink that records every batch it was handed, with the thread."""

    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.sessions: list[RecordingRunSink] = []

    def open_run(self, header: dict[str, Any]) -> "RecordingRunSink":
        session = RecordingRunSink(header, self)
        with self.lock:
            self.sessions.append(session)
        return session

    @property
    def batches(self) -> list[tuple[str, tuple[dict[str, Any], ...]]]:
        with self.lock:
            if not self.sessions:
                return []
            return list(self.sessions[-1].batches)

    @property
    def seqs(self) -> list[int]:
        return [r["seq"] for _thread, batch in self.batches for r in batch]

    @property
    def kinds(self) -> list[str]:
        return [r["kind"] for _thread, batch in self.batches for r in batch]


class RecordingRunSink:
    def __init__(self, header: dict[str, Any], owner: RecordingSink) -> None:
        self.header = header
        self.owner = owner
        self.batches: list[tuple[str, tuple[dict[str, Any], ...]]] = []
        self.threads: set[str] = set()

    def write(self, records: tuple[dict[str, Any], ...]) -> None:
        name = threading.current_thread().name
        with self.owner.lock:
            self.batches.append((name, tuple(records)))
            self.threads.add(name)


class FailingSink(RecordingSink):
    """Rejects the ``fail_on``-th write (1-based); optionally only off-thread."""

    def __init__(self, fail_on: int = 1, only_timer_thread: bool = False,
                 delay_s: float = 0.0) -> None:
        super().__init__()
        self.fail_on = fail_on
        self.only_timer_thread = only_timer_thread
        self.delay_s = delay_s
        self.writes = 0
        self.failed_on_thread: str | None = None

    def open_run(self, header: dict[str, Any]) -> "FailingRunSink":
        session = FailingRunSink(header, self)
        with self.lock:
            self.sessions.append(session)
        return session


class FailingRunSink(RecordingRunSink):
    owner: FailingSink

    def write(self, records: tuple[dict[str, Any], ...]) -> None:
        name = threading.current_thread().name
        off_thread = name != threading.main_thread().name
        with self.owner.lock:
            self.owner.writes += 1
            n = self.owner.writes
        if self.owner.delay_s:
            time.sleep(self.owner.delay_s)
        should = n >= self.owner.fail_on
        if self.owner.only_timer_thread and not off_thread:
            should = False
        if should:
            with self.owner.lock:
                self.owner.failed_on_thread = name
            raise IOError(f"sink refused write #{n} on {name}")
        super().write(records)


def buffered_runtime(
    n: int, *, sink: Any, chunk_records: int, flush_interval_ms: int,
    listeners: tuple[Any, ...] = (), pause_s: float = 0.0,
    max_attempts: int = 1,
) -> Runtime:
    return Runtime(
        chain(n),
        writer_nodes(n, pause_s=pause_s),
        durability_profile=DurabilityProfile.BUFFERED,
        sink=sink,
        listeners=listeners,
        chunk_records=chunk_records,
        flush_interval_ms=flush_interval_ms,
        max_attempts=max_attempts,
    )


def seq_report(seqs: list[int]) -> dict[str, Any]:
    """Gaplessness / duplication summary for a list of persisted seqs."""
    return {
        "count": len(seqs),
        "unique": len(set(seqs)),
        "duplicates": sorted({s for s in seqs if seqs.count(s) > 1}),
        "sorted": seqs == sorted(seqs),
        "gapless": seqs == list(range(1, len(seqs) + 1)),
        "max": max(seqs) if seqs else None,
    }


class Report:
    """Accumulate PASS/FAIL rows so every script prints one table."""

    def __init__(self, title: str) -> None:
        self.title = title
        self.rows: list[tuple[str, str, str]] = []

    def record(self, name: str, ok: bool, detail: str = "") -> None:
        self.rows.append((name, "PASS" if ok else "FAIL", detail))
        print(f"[{'PASS' if ok else 'FAIL'}] {name}  {detail}", flush=True)

    def dump(self) -> int:
        width = max(len(r[0]) for r in self.rows)
        print(f"\n=== {self.title} ===")
        for name, verdict, detail in self.rows:
            print(f"{name.ljust(width)}  {verdict}  {detail}")
        failures = sum(1 for r in self.rows if r[1] == "FAIL")
        print(f"-- {len(self.rows)} checks, {failures} FAIL")
        return failures
