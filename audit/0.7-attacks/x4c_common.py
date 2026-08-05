"""Shared fixtures for the x4c_* durability / sink-semantics round.

Nothing here asserts. It provides:

*  ``JsonlFileSink`` — a MINIMAL but genuinely durable ``TraceSink``: one file
   per run, header line then one line per record, fsync on every write.  This
   is the sink the package does not ship; every claim about "a conforming file
   sink" in this round is measured against this one.
*  ``FailingSink`` — the same, with injectable failure at open / first batch /
   middle batch / terminal batch.
*  ``validate_file`` — runs ``contract/validate.py`` as the contract says to.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

from vitruvyan_motus import (
    TRACE_SCHEMA_VERSION,
    GraphSpec,
    Runtime,
    State,
)

REPO = Path(__file__).resolve().parents[1]
VALIDATE = REPO / "contract" / "validate.py"
PYTHON = REPO / ".venv" / "bin" / "python"
OUT = Path(
    os.environ.get(
        "X4C_OUT",
        tempfile.mkdtemp(prefix="x4c-artifacts-"),
    )
)
OUT.mkdir(parents=True, exist_ok=True)


# --------------------------------------------------------------------------- #
# The durable sink the package does not ship                                  #
# --------------------------------------------------------------------------- #


class _JsonlRunSink:
    """One run-scoped session writing the JSONL encoding of one trace."""

    def __init__(self, path: Path, header: dict[str, Any], *, fail=None) -> None:
        self.path = path
        self.header = header
        self.batches = 0
        self.records_written = 0
        self._fail = fail
        # Line 1 of the JSONL encoding is `#/$defs/TraceHeader`:
        # {schema_version, run}.  `open_run` hands over only the `run` object,
        # so the version has to come from somewhere else -- see x4c_01.
        line = json.dumps(
            {"schema_version": TRACE_SCHEMA_VERSION, "run": header},
            ensure_ascii=False, separators=(",", ":"), allow_nan=False,
        )
        self._fh = open(self.path, "w", encoding="utf-8", newline="\n")
        self._fh.write(line + "\n")
        self._fh.flush()
        os.fsync(self._fh.fileno())

    def write(self, records: tuple[dict[str, Any], ...]) -> None:
        self.batches += 1
        if self._fail is not None:
            self._fail(self.batches, records)
        for record in records:
            self._fh.write(
                json.dumps(
                    record, ensure_ascii=False, separators=(",", ":"),
                    allow_nan=False,
                )
                + "\n"
            )
            self.records_written += 1
        self._fh.flush()
        os.fsync(self._fh.fileno())


class JsonlFileSink:
    """A TraceSink writing one independently checkable JSONL file per run."""

    def __init__(self, directory: Path, *, prefix: str = "run", fail=None,
                 fail_open: BaseException | None = None) -> None:
        self.directory = Path(directory)
        self.directory.mkdir(parents=True, exist_ok=True)
        self.prefix = prefix
        self.sessions: list[_JsonlRunSink] = []
        self.headers: list[dict[str, Any]] = []
        self._fail = fail
        self._fail_open = fail_open
        self.open_calls = 0

    def open_run(self, header: dict[str, Any]):
        self.open_calls += 1
        self.headers.append(header)
        if self._fail_open is not None:
            raise self._fail_open
        path = self.directory / f"{self.prefix}-{self.open_calls:02d}.jsonl"
        session = _JsonlRunSink(path, header, fail=self._fail)
        self.sessions.append(session)
        return session

    @property
    def paths(self) -> list[Path]:
        return [s.path for s in self.sessions]


def fail_on_batch(n: int, exc: BaseException | None = None):
    """Injector: raise on the n-th write batch of the session."""

    def _fail(batch: int, records: tuple[dict[str, Any], ...]) -> None:
        if batch == n:
            raise (exc or RuntimeError(f"sink refused batch {n}"))

    return _fail


def fail_on_kind(kind: str, exc: BaseException | None = None):
    """Injector: raise on the first batch containing a record of ``kind``."""

    def _fail(batch: int, records: tuple[dict[str, Any], ...]) -> None:
        if any(r.get("kind") == kind for r in records):
            raise (exc or RuntimeError(f"sink refused batch containing {kind}"))

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


def write_text(name: str, text: str) -> Path:
    path = OUT / name
    path.write_bytes(text.encode("utf-8"))
    return path


# --------------------------------------------------------------------------- #
# Graph fixtures                                                              #
# --------------------------------------------------------------------------- #

LINEAR_DOC: dict[str, Any] = {
    "schema_version": "1.0.0",
    "name": "x4c-linear",
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
LINEAR_SPEC_PATH = write_json("x4c-linear.graphspec.json", LINEAR.to_dict())


def node(state: State) -> State:
    return state


def registry() -> dict[str, Any]:
    return {"a": node, "b": node, "c": node, "d": node}


def runtime(**kwargs: Any) -> Runtime:
    return Runtime(LINEAR, registry(), **kwargs)


def kinds_of_file(path: Path) -> list[str]:
    out = []
    for i, line in enumerate(path.read_text(encoding="utf-8").splitlines()):
        if not line.strip():
            continue
        try:
            obj = json.loads(line)
        except ValueError:
            out.append("<unparseable>")
            continue
        out.append("HEADER" if i == 0 else obj.get("kind", "?"))
    return out


class Report:
    def __init__(self, title: str) -> None:
        self.title = title
        self.rows: list[tuple[str, str, str]] = []

    def record(self, name: str, ok: bool, detail: str = "") -> None:
        self.rows.append((name, "OK" if ok else "BROKEN", detail))
        print(f"[{'OK    ' if ok else 'BROKEN'}] {name}  {detail}", flush=True)

    def dump(self) -> int:
        width = max(len(r[0]) for r in self.rows)
        print(f"\n=== {self.title} ===   artifacts: {OUT}")
        for name, verdict, detail in self.rows:
            print(f"{name.ljust(width)}  {verdict}  {detail}")
        broken = sum(1 for r in self.rows if r[1] == "BROKEN")
        print(f"-- {len(self.rows)} checks, {broken} BROKEN")
        return broken


if __name__ == "__main__":
    print("artifacts dir:", OUT)
    print("python:", sys.version)
