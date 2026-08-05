"""Shared fixtures for the x5_* round: ADR-011 sink-protocol sufficiency.

Everything here is written against the NEW protocol only:

*  ``open_run(header)`` receives the document's ``TraceHeader``
   ``{schema_version, run}`` and the sink writes it verbatim as line 1.
   Nothing is imported from the writer -- that is the whole ADR-011 claim.
*  ``finish(*, complete)`` is recorded, with call ordering and a thread id, so
   "exactly once" and "agrees with the file" are both measurable.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import threading
from pathlib import Path
from typing import Any

from vitruvyan_motus import DurabilityProfile, GraphSpec, Runtime, State

REPO = Path(__file__).resolve().parents[1]
VALIDATE = REPO / "contract" / "validate.py"
PYTHON = REPO / ".venv" / "bin" / "python"
OUT = Path(os.environ.get("X5_OUT", tempfile.mkdtemp(prefix="x5-artifacts-")))
OUT.mkdir(parents=True, exist_ok=True)


# --------------------------------------------------------------------------- #
# A sink written against the protocol and nothing else                        #
# --------------------------------------------------------------------------- #


class ProtocolJsonlRunSink:
    """One run session.  Writes exactly what it was handed, unchanged."""

    def __init__(self, path: Path, header: dict[str, Any], owner) -> None:
        self.path = path
        self.header = header
        self.owner = owner
        self.batches: list[tuple[str, ...]] = []
        self.records_written = 0
        self.finish_calls: list[bool] = []
        self.finish_threads: list[int] = []
        self.writes_after_finish = 0
        self.closed = False
        self._fh = open(path, "w", encoding="utf-8", newline="\n")
        # Line 1 is `#/$defs/TraceHeader`.  Verbatim -- no reconstruction, no
        # TRACE_SCHEMA_VERSION import.  If the protocol is sufficient this is
        # a conforming first line.
        self._emit(header)

    def _emit(self, obj: Any) -> None:
        self._fh.write(
            json.dumps(obj, ensure_ascii=False, separators=(",", ":"),
                       allow_nan=False) + "\n"
        )
        self._fh.flush()
        os.fsync(self._fh.fileno())

    def write(self, records: tuple[dict[str, Any], ...]) -> None:
        if self.finish_calls:
            self.writes_after_finish += 1
            self.owner.violations.append(
                f"{self.path.name}: write() after finish() "
                f"({tuple(r['kind'] for r in records)})"
            )
        self.batches.append(tuple(r["kind"] for r in records))
        if self.owner.fail is not None:
            self.owner.fail(len(self.batches), records, self)
        for record in records:
            self._emit(record)
            self.records_written += 1

    def finish(self, *, complete: bool) -> None:
        if self.finish_calls:
            self.owner.violations.append(
                f"{self.path.name}: finish() called {len(self.finish_calls)+1} times"
            )
        self.finish_calls.append(complete)
        self.finish_threads.append(threading.get_ident())
        if self.owner.finish_raises is not None:
            raise self.owner.finish_raises
        self._fh.close()
        self.closed = True

    # ---- what the file actually says -------------------------------------- #

    @property
    def lines(self) -> list[Any]:
        text = self.path.read_text(encoding="utf-8")
        return [json.loads(ln) for ln in text.splitlines() if ln.strip()]

    @property
    def kinds(self) -> list[str]:
        return [
            ("HEADER" if i == 0 else obj.get("kind", "?"))
            for i, obj in enumerate(self.lines)
        ]

    @property
    def file_has_terminal(self) -> bool:
        return any(k in TERMINALS for k in self.kinds[1:])


TERMINALS = frozenset({"run_completed", "run_failed", "run_cancelled"})


class ProtocolJsonlSink:
    """TraceSink writing one independently checkable JSONL file per run."""

    def __init__(self, directory: Path, *, prefix: str = "run", fail=None,
                 fail_open: BaseException | None = None,
                 finish_raises: BaseException | None = None,
                 no_finish: bool = False) -> None:
        self.directory = Path(directory)
        self.directory.mkdir(parents=True, exist_ok=True)
        self.prefix = prefix
        self.fail = fail
        self.finish_raises = finish_raises
        self.no_finish = no_finish
        self._fail_open = fail_open
        self.sessions: list[ProtocolJsonlRunSink] = []
        self.headers: list[dict[str, Any]] = []
        self.open_calls = 0
        self.violations: list[str] = []
        self._lock = threading.Lock()

    def open_run(self, header: dict[str, Any]):
        with self._lock:
            self.open_calls += 1
            n = self.open_calls
        self.headers.append(header)
        if self._fail_open is not None:
            raise self._fail_open
        path = self.directory / f"{self.prefix}-{n:02d}.jsonl"
        session = ProtocolJsonlRunSink(path, header, self)
        if self.no_finish:
            # A sink that omits `finish` entirely: ADR-011 says it "behaves as
            # before and forfeits only the distinction".
            object.__setattr__(session, "finish", None)
        with self._lock:
            self.sessions.append(session)
        return session

    # ---- summaries -------------------------------------------------------- #

    @property
    def finishes(self) -> list[list[bool]]:
        return [s.finish_calls for s in self.sessions]

    def summary(self) -> str:
        parts = []
        for s in self.sessions:
            parts.append(
                f"{s.path.name}: finish={s.finish_calls} "
                f"file_terminal={s.file_has_terminal} kinds={s.kinds}"
            )
        return " | ".join(parts) or "<no sessions>"


def fail_on_batch(n: int, exc: BaseException | None = None):
    def _fail(batch, records, session):
        if batch == n:
            raise (exc or RuntimeError(f"sink refused batch {n}"))
    return _fail


def fail_on_kind(kind: str, exc: BaseException | None = None):
    def _fail(batch, records, session):
        if any(r.get("kind") == kind for r in records):
            raise (exc or RuntimeError(f"sink refused batch containing {kind}"))
    return _fail


def commit_then_raise_on_kind(kind: str):
    """The lost-acknowledgement shape: the batch IS written, then it raises."""
    def _fail(batch, records, session):
        if any(r.get("kind") == kind for r in records):
            for record in records:
                session._emit(record)
                session.records_written += 1
            raise RuntimeError(f"ack lost after committing batch with {kind}")
    return _fail


# --------------------------------------------------------------------------- #
# Validation                                                                  #
# --------------------------------------------------------------------------- #


def validate_file(path: Path, artifact: str = "jsonl", *,
                  spec_path: Path | None = None,
                  allow_incomplete: bool = False) -> tuple[int, str]:
    cmd = [str(PYTHON), str(VALIDATE), artifact, str(path)]
    if spec_path is not None:
        cmd += ["--spec", str(spec_path)]
    if allow_incomplete:
        cmd += ["--allow-incomplete"]
    proc = subprocess.run(cmd, capture_output=True, text=True)
    return proc.returncode, (proc.stdout + proc.stderr).strip()


def write_json(name: str, value: Any) -> Path:
    path = OUT / name
    path.write_text(
        json.dumps(value, ensure_ascii=False, separators=(",", ":"),
                   allow_nan=False),
        encoding="utf-8",
    )
    return path


# --------------------------------------------------------------------------- #
# Graph fixtures                                                              #
# --------------------------------------------------------------------------- #

LINEAR_DOC: dict[str, Any] = {
    "schema_version": "1.0.0",
    "name": "x5-linear",
    "version": "1.0.0",
    "entry": "a",
    "nodes": [
        {"name": "a", "effect_class": "pure"},
        {"name": "b", "effect_class": "pure"},
        {"name": "c", "effect_class": "pure"},
        {"name": "d", "effect_class": "pure"},
    ],
    "transitions": {
        "a": {"kind": "next", "to": "b"},
        "b": {"kind": "next", "to": "c"},
        "c": {"kind": "next", "to": "d"},
        "d": {"kind": "terminal"},
    },
}
LINEAR = GraphSpec.from_dict(dict(LINEAR_DOC))
LINEAR_SPEC_PATH = write_json("x5-linear.graphspec.json", LINEAR.to_dict())


def node(state: State) -> State:
    return state


def registry() -> dict[str, Any]:
    return {"a": node, "b": node, "c": node, "d": node}


def runtime(**kwargs: Any) -> Runtime:
    return Runtime(LINEAR, registry(), **kwargs)


SYNC = DurabilityProfile.SYNCHRONOUS
BUFFERED = DurabilityProfile.BUFFERED
INMEM = DurabilityProfile.IN_MEMORY


class Report:
    def __init__(self, title: str) -> None:
        self.title = title
        self.rows: list[tuple[str, str, str]] = []

    def record(self, name: str, ok: bool, detail: str = "") -> None:
        self.rows.append((name, "OK" if ok else "BROKEN", detail))
        print(f"[{'OK    ' if ok else 'BROKEN'}] {name}  {detail}", flush=True)

    def dump(self) -> int:
        width = max((len(r[0]) for r in self.rows), default=10)
        print(f"\n=== {self.title} ===   artifacts: {OUT}")
        for name, verdict, detail in self.rows:
            print(f"{name.ljust(width)}  {verdict}  {detail}")
        broken = sum(1 for r in self.rows if r[1] == "BROKEN")
        print(f"-- {len(self.rows)} checks, {broken} BROKEN")
        return broken


if __name__ == "__main__":
    print("artifacts dir:", OUT)
    print("python:", sys.version)
