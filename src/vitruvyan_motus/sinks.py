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


def _sync_directory(directory: Path) -> None:
    """Commit a directory entry, which ``fsync`` on the file does not.

    Creating a file and renaming it are both directory operations. A sink that
    syncs only the data has bought half of what the ``synchronous`` profile
    promises: the bytes survive, the name they are published under may not.
    """
    descriptor = os.open(directory, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    except OSError:
        pass  # not every filesystem permits it; the data sync already happened
    finally:
        os.close(descriptor)


class _JsonlRunSession:
    """One run's file. Written as it goes, named for what it turned out to be."""

    __slots__ = ("path", "partial_path", "pending_path", "_handle", "_lock",
                 "_records", "_fsync", "_directory", "complete", "published")

    def __init__(self, directory: Path, header: dict[str, Any], *, fsync: bool) -> None:
        self._lock = threading.Lock()
        self._records = 0
        self._fsync = fsync
        self._directory = directory
        self.complete: bool | None = None
        self.published: Path | None = None

        # Created exclusively, and never through a symlink. O_EXCL means this
        # session owns a file no other session is holding: two runs sharing a
        # run_id -- a retried job keeping its job id is the obvious case -- get
        # two files instead of one truncating the other, and a second live run
        # cannot interleave its records into the first one's document.
        # O_NOFOLLOW refuses a symlink planted at this path: the name is a pure
        # function of run_id and therefore fully predictable, so following one
        # would let anyone who can write to the directory choose where the
        # trace lands. guarantees.md section 5 requires containment.
        stem = _file_stem(header["run"]["run_id"])
        self._handle, chosen = self._create(stem)
        self.path = directory / f"{chosen}.jsonl"
        self.partial_path = directory / f"{chosen}.partial.jsonl"
        # A third name throughout: a process that dies mid-run leaves `.part`,
        # which is exactly what it is -- a stream nobody ever declared over.
        self.pending_path = directory / f"{chosen}.jsonl.part"
        try:
            self._emit(header)
            if fsync:
                _sync_directory(directory)
        except BaseException:
            self._handle.close()
            raise

    def _create(self, stem: str):
        """Claim an unused name, and prove the file we opened is really ours."""
        for attempt in range(1, 1000):
            candidate = stem if attempt == 1 else f"{stem}-{attempt}"
            target = self._directory / f"{candidate}.jsonl.part"
            try:
                descriptor = os.open(
                    target,
                    os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0),
                )
            except FileExistsError:
                continue
            handle = os.fdopen(descriptor, "w", encoding="utf-8")
            # Belt and braces: O_EXCL|O_NOFOLLOW already refuses a planted
            # link, but containment is a contract property and asserting it on
            # the RESOLVED path is the only check that cannot be fooled --
            # comparing `.parent` passes for a file that is not there at all.
            resolved = target.resolve()
            if resolved.parent != self._directory.resolve():
                handle.close()
                target.unlink(missing_ok=True)
                raise OSError(f"refusing to write trace evidence outside {self._directory}")
            return handle, candidate
        raise OSError(f"cannot claim a free artifact name for {stem!r}")

    def _emit(self, value: dict[str, Any]) -> None:
        # The same separators `Trace.to_jsonl` uses. A durable artifact that
        # merely parses to the same document as the writer's own encoding is
        # not byte-reproducible against it, and costs ~10% of every file.
        self._handle.write(
            json.dumps(value, ensure_ascii=False, separators=(",", ":")) + "\n"
        )
        self._handle.flush()
        if self._fsync:
            os.fsync(self._handle.fileno())

    def write(self, records: tuple[dict[str, Any], ...]) -> None:
        with self._lock:
            for record in records:
                self._emit(record)
                self._records += 1

    def _publish_as(self, destination: Path) -> Path:
        """Rename onto a free name, so publishing never destroys an account."""
        target, attempt = destination, 1
        while attempt < 1000:
            try:
                # `link` + `unlink` rather than `replace`: replace() silently
                # clobbers, and this sink exists to stop evidence disappearing.
                os.link(self.pending_path, target)
                self.pending_path.unlink()
                return target
            except FileExistsError:
                attempt += 1
                target = destination.with_name(
                    destination.name.replace(".", f"-{attempt}.", 1)
                )
        raise OSError(f"cannot publish {self.pending_path} without overwriting")

    def finish(self, *, complete: bool) -> None:
        """Name the file for what it actually holds.

        A session that received no records cannot produce a valid artifact at
        all — the schema requires at least one — so publishing a header-only
        file would be publishing something no reader can accept. It is removed
        instead. A prefix is kept under a name that says so, because a truncated
        account is still evidence; it just is not a whole one.

        ``complete`` and ``published`` are set only once the file is actually
        where they say it is. A rename can fail — a full disk, a read-only
        directory, a name taken by a directory — and reporting a published
        artifact that is not there is worse than reporting the truth.
        """
        with self._lock:
            if self.complete is not None:
                return  # the runtime calls this once; a sink should not assume
            self._handle.close()
            published: Path | None = None
            try:
                if complete:
                    published = self._publish_as(self.path)
                elif self._records == 0:
                    self.pending_path.unlink(missing_ok=True)
                else:
                    published = self._publish_as(self.partial_path)
            finally:
                # Two different facts, recorded separately. What the runtime
                # said about the run is true whether or not this sink managed
                # to act on it, and a publish can fail — a full disk, a
                # read-only directory, a name taken by a directory. Letting the
                # failure suppress `complete` too would leave the session
                # claiming to be in flight forever, for a run that ended.
                self.published = published
                self.complete = complete
                if self._fsync:
                    _sync_directory(self._directory)

    @property
    def artifact(self) -> Path | None:
        """Where this run's evidence actually is, or None if there is none.

        Never a path that was intended: a run still in flight answers with the
        pending file, a finished one with what was published, and a session
        whose publish failed with the pending file it is still in.
        """
        if self.complete is None:
            return self.pending_path if self.pending_path.exists() else None
        if self.published is not None:
            return self.published
        return self.pending_path if self.pending_path.exists() else None


class JsonlTraceSink:
    """Durable per-run JSONL files, one per run, in a directory.

    Each run becomes a document in the JSONL form ``contract/validate.py``
    accepts: the trace header on line one, one record per line after it.

    The filename is a **sanitised, digest-suffixed** form of the run id, not the
    run id verbatim — ``run_id`` is caller-supplied and may contain path
    separators, so it is neither safe nor unique as a bare filename. A run named
    ``"order/4471"`` lands as ``order_4471-<12 hex>.jsonl``. The suffix is a
    short digest of the original id, so two ids that sanitise alike still get
    distinct files. Use :meth:`path_for` rather than reconstructing the name;
    the run id itself is always inside the file, in the header.

    The extension states what the file is: ``.jsonl`` for a run that reached its
    terminal, ``.partial.jsonl`` for one cut short, ``.jsonl.part`` for one
    whose process died mid-write, since nothing ever declared it over.

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

    def path_for(self, run_id: str, *, status: str = "complete") -> Path:
        """Where a run *would* land, without reconstructing the digest by hand.

        The integrator's need this answers: a service records a ``run_id`` in
        its own audit log, and later wants the trace file for it. The filename
        is not the run id, so guessing ``f"{run_id}.jsonl"`` finds nothing —
        this resolves it through the same stem function the sink writes with.

        ``status`` selects which of the three names: ``"complete"`` →
        ``.jsonl``, ``"partial"`` → ``.partial.jsonl``, ``"pending"`` →
        ``.jsonl.part``. This is the *predicted* path and does not touch the
        disk; the file exists only if a run of that id finished in that state.

        Exact for a unique ``run_id``. If the same id was written twice on this
        sink, the later run carried a ``-2`` suffix (see :meth:`open_run`), and
        for those you must read ``sessions``/``artifacts`` rather than predict.
        """
        suffix = {
            "complete": ".jsonl",
            "partial": ".partial.jsonl",
            "pending": ".jsonl.part",
        }
        if status not in suffix:
            raise ValueError(f"status must be one of {sorted(suffix)}, not {status!r}")
        return self.directory / f"{_file_stem(run_id)}{suffix[status]}"

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
