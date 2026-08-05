"""The three observation surfaces: durable, live and execution-coupled."""

from __future__ import annotations

import copy
import threading
import time
from collections.abc import AsyncIterator, Callable, Iterator
from typing import Any, Protocol, runtime_checkable

__all__ = [
    "TraceSink", "TraceRunSink", "Listener", "InMemoryTraceSink",
    "StreamDriver", "AsyncStreamDriver",
]


def _iterator_is_finished(iterator: Any) -> bool:
    """Whether the underlying generator has actually terminated.

    A driver latches itself closed when its iterator is done, so that a later
    context exit cannot cancel an unrelated run (ADR-008 §1). But not every
    exception out of ``next``/``__anext__`` means "done": a concurrency clash
    raises ``RuntimeError``/``ValueError`` while the generator is very much
    alive, and latching on that abandons a live run with no terminal record.
    A finished generator has released its frame; anything else has not.
    """
    for attribute in ("gi_frame", "ag_frame", "cr_frame"):
        if hasattr(iterator, attribute):
            return getattr(iterator, attribute) is None
    return True  # not a generator: assume the exception ended it


@runtime_checkable
class TraceRunSink(Protocol):
    """One run-scoped durable session receiving ordered record batches."""

    def write(self, records: tuple[dict[str, Any], ...]) -> None: ...


@runtime_checkable
class TraceSink(Protocol):
    """Factory binding a trace header to one durable run session."""

    def open_run(self, header: dict[str, Any]) -> TraceRunSink: ...


@runtime_checkable
class Listener(Protocol):
    """Live read-only surface.

    Record mutation and callback failures are isolated.  Delivery is
    synchronous, so callbacks can delay the runner and code holding the
    Runtime may use its public cancellation surface.
    """

    def on_record(self, record: dict[str, Any]) -> None: ...


class _InMemoryRunSink:
    __slots__ = ("header", "_records")

    def __init__(self, header: dict[str, Any]) -> None:
        self.header = copy.deepcopy(header)
        self._records: list[dict[str, Any]] = []

    def write(self, records: tuple[dict[str, Any], ...]) -> None:
        self._records.extend(copy.deepcopy(records))

    @property
    def records(self) -> tuple[dict[str, Any], ...]:
        return tuple(copy.deepcopy(self._records))


class InMemoryTraceSink:
    """Run-partitioned test sink with no crash-persistence promise."""

    __slots__ = ("_runs", "_lock")

    def __init__(self) -> None:
        self._runs: list[_InMemoryRunSink] = []
        self._lock = threading.Lock()

    def open_run(self, header: dict[str, Any]) -> TraceRunSink:
        run = _InMemoryRunSink(header)
        with self._lock:
            self._runs.append(run)
        return run

    @property
    def header(self) -> dict[str, Any] | None:
        with self._lock:
            return copy.deepcopy(self._runs[-1].header) if self._runs else None

    @property
    def records(self) -> tuple[dict[str, Any], ...]:
        with self._lock:
            return self._runs[-1].records if self._runs else ()

    @property
    def runs(self) -> tuple[dict[str, Any], ...]:
        with self._lock:
            runs = tuple(self._runs)
        return tuple(
            {"header": copy.deepcopy(run.header), "records": list(run.records)}
            for run in runs
        )


class _ObservationHub:
    """Runtime-owned delivery coordinator; deliberately not public."""

    __slots__ = (
        "profile", "sink", "listeners", "chunk_records", "flush_interval_ms",
        "_buffer", "_last_flush", "listener_failures", "_lock", "_timer",
        "_async_failure", "_closed", "_run_sink",
    )

    def __init__(
        self,
        *,
        profile: str,
        sink: TraceSink | None,
        listeners: tuple[Listener | Callable[[dict[str, Any]], None], ...],
        chunk_records: int,
        flush_interval_ms: int,
    ) -> None:
        if profile not in ("in-memory", "buffered", "synchronous"):
            raise ValueError(f"unsupported durability profile {profile!r}")
        if profile != "in-memory" and sink is None:
            raise ValueError(f"durability profile {profile!r} requires a TraceSink")
        if isinstance(chunk_records, bool) or not isinstance(chunk_records, int) or chunk_records < 1:
            raise ValueError("chunk_records must be an integer >= 1")
        if isinstance(flush_interval_ms, bool) or not isinstance(flush_interval_ms, int) or flush_interval_ms < 0:
            raise ValueError("flush_interval_ms must be an integer >= 0")
        self.profile = profile
        self.sink = sink
        self.listeners = listeners
        self.chunk_records = chunk_records
        self.flush_interval_ms = flush_interval_ms
        self._buffer: list[dict[str, Any]] = []
        self._last_flush = time.monotonic()
        self.listener_failures = 0
        self._lock = threading.RLock()
        self._timer: threading.Timer | None = None
        self._async_failure: BaseException | None = None
        self._closed = False
        self._run_sink: TraceRunSink | None = None

    def bind(self, header: dict[str, Any]) -> None:
        """Open the required run-scoped session without losing its failure."""
        if self.sink is None:
            return
        try:
            session = self.sink.open_run(copy.deepcopy(header))
            if not callable(getattr(session, "write", None)):
                raise TypeError("TraceSink.open_run must return a TraceRunSink")
            self._run_sink = session
        except BaseException as exc:
            self._async_failure = exc

    def _cancel_timer_locked(self) -> None:
        timer, self._timer = self._timer, None
        if timer is not None:
            timer.cancel()

    def _schedule_timer_locked(self) -> None:
        if (
            self._timer is not None or self._closed or not self._buffer
            or self.flush_interval_ms <= 0
        ):
            return
        elapsed = (time.monotonic() - self._last_flush) * 1000
        delay = max(0.0, (self.flush_interval_ms - elapsed) / 1000)
        self._timer = threading.Timer(delay, self._timer_flush)
        self._timer.daemon = True
        self._timer.start()

    def _flush_locked(self) -> None:
        if not self._buffer:
            return
        assert self._run_sink is not None
        batch = tuple(copy.deepcopy(self._buffer))
        try:
            self._run_sink.write(batch)
        except BaseException as exc:
            self._async_failure = exc
            raise
        self._buffer.clear()
        self._last_flush = time.monotonic()

    def _timer_flush(self) -> None:
        with self._lock:
            self._timer = None
            if self._closed or self._async_failure is not None:
                return
            try:
                self._flush_locked()
            except BaseException:
                # The runner observes the stored failure at its next
                # persistence boundary; terminal success always forces one.
                return

    def persist(self, record: dict[str, Any], *, force: bool = False) -> None:
        if self._async_failure is not None:
            raise self._async_failure
        if self.profile == "in-memory" and self._run_sink is None:
            return
        assert self._run_sink is not None
        if self.profile in ("in-memory", "synchronous"):
            self._run_sink.write((copy.deepcopy(record),))
            return
        with self._lock:
            if self._async_failure is not None:
                raise self._async_failure
            self._buffer.append(copy.deepcopy(record))
            elapsed_ms = (time.monotonic() - self._last_flush) * 1000
            should_flush = force or len(self._buffer) >= self.chunk_records
            if self.flush_interval_ms == 0 or elapsed_ms >= self.flush_interval_ms:
                should_flush = True
            if should_flush:
                self._cancel_timer_locked()
                self._flush_locked()
            else:
                self._schedule_timer_locked()

    def best_effort(self, record: dict[str, Any]) -> None:
        try:
            self.persist(record, force=True)
        except BaseException:
            pass

    def notify(self, record: dict[str, Any]) -> None:
        for listener in self.listeners:
            delivered = copy.deepcopy(record)
            try:
                method = getattr(listener, "on_record", None)
                if method is not None:
                    method(delivered)
                else:
                    listener(delivered)  # type: ignore[misc]
            except BaseException:
                self.listener_failures += 1

    def close(self) -> None:
        """Stop background scheduling after a run has reached a terminal."""
        with self._lock:
            self._closed = True
            self._cancel_timer_locked()


class StreamDriver(Iterator[dict[str, Any]]):
    """Consumer-paced execution surface.

    The runtime cannot advance until the consumer asks for the next record.
    ``close`` is an explicit cooperative cancellation: it drains only the
    cancellation terminal, never the pending node attempt.
    """

    __slots__ = ("_iterator", "_cancel", "_closed", "_trace_getter")

    def __init__(
        self,
        iterator: Iterator[dict[str, Any]],
        cancel: Callable[[str], None],
        trace_getter: Callable[[], Any],
    ) -> None:
        self._iterator = iterator
        self._cancel = cancel
        self._closed = False
        self._trace_getter = trace_getter

    def __iter__(self) -> "StreamDriver":
        return self

    def __next__(self) -> dict[str, Any]:
        if self._closed:
            raise StopIteration
        try:
            return next(self._iterator)
        except BaseException:
            # Exhaustion and execution failures both finish this driver.  In
            # particular, normal exhaustion must make context-manager exit a
            # no-op rather than queueing cancellation for a later run.  A
            # concurrency clash does NOT finish it — latching there would
            # abandon a live run with no terminal record.
            if _iterator_is_finished(self._iterator):
                self._closed = True
            raise

    @property
    def trace(self) -> Any:
        return self._trace_getter()

    def close(self, reason: str = "stream consumer stopped") -> None:
        if self._closed:
            return
        self._cancel(reason)
        try:
            while True:
                next(self._iterator)
        except StopIteration:
            pass
        finally:
            self._closed = True

    def __enter__(self) -> "StreamDriver":
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        if not self._closed:
            self.close("stream context exited")


class AsyncStreamDriver(AsyncIterator[dict[str, Any]]):
    """The asynchronous twin of :class:`StreamDriver`.

    Same contract, same cancellation semantics: the runtime cannot advance
    until the consumer asks for the next record, exhaustion closes the driver
    so a later context exit cannot cancel an unrelated run (ADR-008 §1), and
    ``aclose`` on a live run drains only the cancellation terminal.
    """

    __slots__ = ("_iterator", "_cancel", "_closed", "_trace_getter", "_requested")

    def __init__(
        self,
        iterator: AsyncIterator[dict[str, Any]],
        cancel: Callable[[str], None],
        trace_getter: Callable[[], Any],
    ) -> None:
        self._iterator = iterator
        self._cancel = cancel
        self._closed = False
        self._requested = False
        self._trace_getter = trace_getter

    def __aiter__(self) -> "AsyncStreamDriver":
        return self

    async def __anext__(self) -> dict[str, Any]:
        if self._closed:
            raise StopAsyncIteration
        try:
            return await self._iterator.__anext__()
        except BaseException:
            # Exhaustion and execution failures both finish this driver, so
            # context exit afterwards is a no-op rather than a cancellation
            # queued against a later run.  "asynchronous generator is already
            # running" is neither: the generator is alive and another task is
            # inside it, so latching there would strand that run for good.
            if _iterator_is_finished(self._iterator):
                self._closed = True
            raise

    @property
    def trace(self) -> Any:
        return self._trace_getter()

    async def aclose(self, reason: str = "stream consumer stopped") -> None:
        if self._closed:
            return
        if not self._requested:
            # The cancellation that actually stopped the run is the first one.
            # A second aclose (a retry, or __aexit__ after an explicit call)
            # must not overwrite the reason the trace will attribute it to.
            self._requested = True
            self._cancel(reason)
        if getattr(self._iterator, "ag_running", False):
            # A consumer is inside __anext__ right now — the graceful-shutdown
            # shape, where a supervisor closes a driver another task is
            # reading. Draining here would raise "asynchronous generator is
            # already running" and orphan the run with no terminal. The
            # cancellation is bound; the consumer drives it to run_cancelled
            # and closes this driver on the way out.
            return
        try:
            while True:
                await self._iterator.__anext__()
        except StopAsyncIteration:
            pass
        finally:
            self._closed = True

    async def __aenter__(self) -> "AsyncStreamDriver":
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:
        if not self._closed:
            await self.aclose("stream context exited")
