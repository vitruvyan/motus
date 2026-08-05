"""x4c-06 — The terminal retry: what a sink that PERSISTED-then-RAISED gets.

`Runtime._store(..., terminal=True)` handles a refused terminal in two ways:

    try:
        self._hub.persist(record, force=True)
    except BaseException as exc:
        if record["kind"] == "run_completed":
            failure = self._sink_failure_record(record["seq"], None, exc)
            self._hub.best_effort(failure)      # <-- re-enters the sink
            ...
        self._hub.best_effort(record)           # <-- re-enters the sink

Both arms send another batch to the same session after a write raised.  Under
`synchronous` (and `in-memory` + attached sink) nothing latches the earlier
failure, so the second batch is really attempted.

A sink whose write DURABLY LANDS and then raises is not a broken sink, it is
the ordinary distributed case: fsync fails after the page cache took the data,
a network sink writes and loses its ack, a WAL appends and then the commit
record fails.  guarantees.md §6 assigns retry to the sink — "Sink-level retry,
if any, is sink configuration" — not to the runner.

This measures what such a sink ends up holding.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

from vitruvyan_motus import (
    DurabilityProfile, GraphSpec, Runtime, SinkFailed, State, TRACE_SCHEMA_VERSION,
)

from x4c_common import (
    LINEAR, LINEAR_SPEC_PATH, OUT, Report, kinds_of_file, registry,
    validate_file, write_json,
)

MISS_DOC = {
    "schema_version": "1.0.0", "name": "x4c-miss", "version": "1.0.0", "entry": "a",
    "nodes": [{"name": "a", "effect_class": "pure"}, {"name": "b", "effect_class": "pure"}],
    "transitions": {
        "a": {"kind": "route", "on": "never_written", "map": {"x": "b"}},
        "b": {"kind": "terminal"},
    },
}
MISS = GraphSpec.from_dict(dict(MISS_DOC))
MISS_SPEC_PATH = write_json("x4c-miss-06.graphspec.json", MISS.to_dict())


class LandThenRaiseRunSink:
    """Durably appends the batch, THEN reports failure for the named kind.

    Exactly one legitimate sink behaviour: the bytes are on disk and fsynced,
    the acknowledgement is what failed.
    """

    def __init__(self, path: Path, header: dict, kinds: tuple[str, ...]) -> None:
        self.path = path
        self.kinds = kinds
        self.raised = 0
        self._fh = open(path, "w", encoding="utf-8", newline="\n")
        self._write({"schema_version": TRACE_SCHEMA_VERSION, "run": header})

    def _write(self, obj) -> None:
        self._fh.write(json.dumps(obj, ensure_ascii=False, separators=(",", ":"),
                                  allow_nan=False) + "\n")
        self._fh.flush()
        os.fsync(self._fh.fileno())

    def write(self, records: tuple[dict, ...]) -> None:
        for record in records:
            self._write(record)          # the data IS durable now
        if any(r.get("kind") in self.kinds for r in records):
            self.raised += 1
            raise OSError("fsync of the commit marker failed after the append")


class LandThenRaiseSink:
    def __init__(self, directory: Path, kinds: tuple[str, ...]) -> None:
        self.directory = Path(directory)
        self.directory.mkdir(parents=True, exist_ok=True)
        self.kinds = kinds
        self.sessions: list[LandThenRaiseRunSink] = []

    def open_run(self, header):
        session = LandThenRaiseRunSink(
            self.directory / f"run-{len(self.sessions) + 1:02d}.jsonl",
            header, self.kinds,
        )
        self.sessions.append(session)
        return session


def node(state: State) -> State:
    return state


def raiser(state: State) -> State:
    raise ValueError("node failed on purpose")


def probe(r: Report, name, *, profile, kinds, spec=LINEAR,
          spec_path=LINEAR_SPEC_PATH, nodes=None, pre_cancel=False,
          hub=None) -> None:
    sink = LandThenRaiseSink(OUT / "06" / name.replace(" ", "_")[:44], kinds)
    kwargs = dict(durability_profile=profile, sink=sink)
    kwargs.update(hub or {})
    rt = Runtime(spec, nodes or registry(), **kwargs)
    if pre_cancel:
        rt.cancel("cancelled before first run")
    outcome = "?"
    try:
        outcome = rt.run().status
    except SinkFailed:
        outcome = "SinkFailed"
    except BaseException as exc:  # noqa: BLE001
        outcome = type(exc).__name__
    path = sink.sessions[-1].path
    seqs = []
    for line in path.read_text(encoding="utf-8").split("\n")[1:]:
        if line.strip():
            seqs.append(json.loads(line)["seq"])
    dupes = sorted({s for s in seqs if seqs.count(s) > 1})
    code, out = validate_file(path, "jsonl", spec_path=spec_path)
    kinds_seen = kinds_of_file(path)[1:]
    r.record(
        name, code == 0 and not dupes,
        f"outcome={outcome} sink_raised={sink.sessions[-1].raised} "
        f"seqs={seqs[-4:]} dup_seq={dupes or 'none'} "
        f"tail={kinds_seen[-3:]} rc={code} :: "
        f"{out.splitlines()[0][:100] if out else 'VALID'}",
    )
    return path


def main() -> int:
    r = Report("x4c-06 sink persisted-then-raised: what the file ends up holding")
    SYNC = DurabilityProfile.SYNCHRONOUS
    BUF = DurabilityProfile.BUFFERED
    MEM = DurabilityProfile.IN_MEMORY
    bufkw = {"chunk_records": 1, "flush_interval_ms": 0}

    probe(r, "sync / run_completed lands then raises", profile=SYNC,
          kinds=("run_completed",))
    probe(r, "sync / run_cancelled lands then raises", profile=SYNC,
          kinds=("run_cancelled",), pre_cancel=True)
    nodes = dict(registry()); nodes["b"] = raiser
    probe(r, "sync / run_failed(node) lands then raises", profile=SYNC,
          kinds=("run_failed",), nodes=nodes)
    probe(r, "sync / run_failed(route_miss) lands then raises", profile=SYNC,
          kinds=("run_failed",), spec=MISS, spec_path=MISS_SPEC_PATH,
          nodes={"a": node, "b": node})
    probe(r, "in-memory+sink / run_completed lands then raises", profile=MEM,
          kinds=("run_completed",))
    probe(r, "buffered / run_completed lands then raises", profile=BUF,
          kinds=("run_completed",), hub=bufkw)
    probe(r, "buffered / run_cancelled lands then raises", profile=BUF,
          kinds=("run_cancelled",), pre_cancel=True, hub=bufkw)
    probe(r, "CONTROL sync / mid transition lands then raises", profile=SYNC,
          kinds=("transition",))

    return r.dump()


if __name__ == "__main__":
    raise SystemExit(main())
