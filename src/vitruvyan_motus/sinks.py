"""A durable file sink, and the reference implementation of the protocol.

Until now the package shipped only :class:`InMemoryTraceSink`, which disclaims
crash persistence — so the ``buffered`` and ``synchronous`` durability profiles
were contractual promises nobody could keep without writing their own sink
first. This module closes that: :class:`JsonlTraceSink` is a real one.

It is deliberately written against the protocol and nothing else. It imports no
schema version and inspects no record kind: the header it is handed is already
the document's first line, and whether the account is whole is something the
runtime tells it. If either were untrue this file could not be written, which
is why it doubles as the executable proof of ADR-011.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import threading
from pathlib import Path
from typing import Any

__all__ = ["JsonlTraceSink"]

_SAFE = re.compile(r"[^A-Za-z0-9._-]")
_MAX_STEM = 96


def _file_stem(run_id: str) -> str:
    """A filesystem-safe stem that still names the run it came from.

    ``run_id`` is caller-supplied and may be any string of 1..200 characters —
    including ``../`` and separators. Sanitising alone would collide two
    different runs onto one file, so a short digest of the original is appended:
    the name stays readable, and distinct runs stay distinct.
    """
    digest = hashlib.sha256(run_id.encode("utf-8")).hexdigest()[:12]
    safe = _SAFE.sub("_", run_id)[:_MAX_STEM].strip("._-") or "run"
    return f"{safe}-{digest}"


class _JsonlRunSession:
    """One run's file. Written as it goes, named for what it turned out to be."""

    __slots__ = ("path", "partial_path", "pending_path", "_handle", "_lock",
                 "_records", "_fsync", "complete")

    def __init__(self, directory: Path, header: dict[str, Any], *, fsync: bool) -> None:
        stem = _file_stem(header["run"]["run_id"])
        self.path = directory / f"{stem}.jsonl"
        self.partial_path = directory / f"{stem}.partial.jsonl"
        # Written under a third name throughout. A process that dies mid-run
        # leaves `.part` behind, which is exactly what it is: a stream nobody
        # ever declared finished. Neither of the other two names can appear
        # unless the runtime said so.
        self.pending_path = directory / f"{stem}.jsonl.part"
        self._lock = threading.Lock()
        self._records = 0
        self._fsync = fsync
        self.complete: bool | None = None
        self._handle = open(self.pending_path, "w", encoding="utf-8")
        self._emit(header)

    def _emit(self, value: dict[str, Any]) -> None:
        self._handle.write(json.dumps(value, ensure_ascii=False) + "\n")
        self._handle.flush()
        if self._fsync:
            os.fsync(self._handle.fileno())

    def write(self, records: tuple[dict[str, Any], ...]) -> None:
        with self._lock:
            for record in records:
                self._emit(record)
                self._records += 1

    def finish(self, *, complete: bool) -> None:
        """Name the file for what it actually holds.

        A session that received no records cannot produce a valid artifact at
        all — the schema requires at least one — so publishing a header-only
        file would be publishing something no reader can accept. It is removed
        instead. A prefix is kept under a name that says so, because a truncated
        account is still evidence; it just is not a whole one.
        """
        with self._lock:
            if self.complete is not None:
                return  # the runtime calls this once; a sink should not assume
            self.complete = complete
            self._handle.close()
            if complete:
                self.pending_path.replace(self.path)
            elif self._records == 0:
                self.pending_path.unlink(missing_ok=True)
            else:
                self.pending_path.replace(self.partial_path)

    @property
    def artifact(self) -> Path | None:
        """Where this run's evidence ended up, or None if there was none."""
        if self.complete is None:
            return self.pending_path
        if self.complete:
            return self.path
        return self.partial_path if self._records else None


class JsonlTraceSink:
    """Durable per-run JSONL files, one per run, in a directory.

    Each run becomes a document in the JSONL form ``contract/validate.py``
    accepts: the trace header on line one, one record per line after it. A run
    that reaches its terminal lands as ``<run>.jsonl``; one that is cut short
    lands as ``<run>.partial.jsonl``; one whose process died mid-write is left
    as ``<run>.jsonl.part``, since nothing ever declared it over.

    ``fsync`` defaults to True because that is what the ``synchronous``
    durability profile buys — ``guarantees.md`` invariant II is explicit that
    claiming a stronger guarantee than the profile bought is a contract
    violation, and a sink that only calls ``flush`` has not bought crash
    survival. Set it False for the ``buffered`` profile, where the run header
    already discloses a loss window, or in tests where the cost is not worth it.
    """

    __slots__ = ("directory", "fsync", "_sessions", "_lock")

    def __init__(self, directory: str | os.PathLike[str], *, fsync: bool = True) -> None:
        self.directory = Path(directory)
        self.directory.mkdir(parents=True, exist_ok=True)
        self.fsync = fsync
        self._sessions: list[_JsonlRunSession] = []
        self._lock = threading.Lock()

    def open_run(self, header: dict[str, Any]) -> _JsonlRunSession:
        session = _JsonlRunSession(self.directory, header, fsync=self.fsync)
        with self._lock:
            self._sessions.append(session)
        return session

    @property
    def sessions(self) -> tuple[_JsonlRunSession, ...]:
        """Every session this sink has opened, in the order it opened them."""
        with self._lock:
            return tuple(self._sessions)

    @property
    def artifacts(self) -> tuple[Path, ...]:
        """The files that exist, for the runs that produced any."""
        return tuple(
            path for path in (session.artifact for session in self.sessions)
            if path is not None
        )
