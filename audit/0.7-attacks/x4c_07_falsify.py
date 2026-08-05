"""x4c-07 — Falsification attempts against x4c-04 and x4c-06.

Every objection a maintainer would raise, tested:

1.  "Your sink raises twice; a real sink raises once."
        -> raise-once sink (the canonical lost-ack: the append committed, the
           acknowledgement failed, the retry succeeds).
2.  "A sink that persists before raising is broken."
        -> then what does a sink that persists NOTHING produce?  (control)
3.  "The duplicate is only in your file, the evidence is fine."
        -> can the package's OWN reader (`Trace.from_dict`) load the artifact?
4.  "The buffered loss is just the declared window."
        -> is any amount of waiting, or any public API, enough to close it?
5.  "Abandoning a driver is user error, the docs say to use `with`."
        -> does anything in the package/contract say so, and does the
           in-memory Trace at least still tell the truth?
6.  "Cancellation through a driver is always trace-recorded."
        -> guarantees.md §6 StreamDriver, checked against the abandoned path.
"""

from __future__ import annotations

import gc
import json
import os
import time
from pathlib import Path

from vitruvyan_motus import (
    DurabilityProfile, Runtime, SinkFailed, State, Trace, TRACE_SCHEMA_VERSION,
)

from x4c_common import (
    LINEAR, LINEAR_SPEC_PATH, OUT, JsonlFileSink, Report, kinds_of_file,
    registry, validate_file,
)


class RaiseOnceRunSink:
    """Durably appends, then raises ONCE for the named kind. Then behaves."""

    def __init__(self, path: Path, header: dict, kinds: tuple[str, ...]) -> None:
        self.path, self.kinds, self.raised = path, kinds, 0
        self._fh = open(path, "w", encoding="utf-8", newline="\n")
        self._emit({"schema_version": TRACE_SCHEMA_VERSION, "run": header})

    def _emit(self, obj) -> None:
        self._fh.write(json.dumps(obj, ensure_ascii=False, separators=(",", ":"),
                                  allow_nan=False) + "\n")
        self._fh.flush()
        os.fsync(self._fh.fileno())

    def write(self, records: tuple[dict, ...]) -> None:
        for record in records:
            self._emit(record)
        if self.raised == 0 and any(r.get("kind") in self.kinds for r in records):
            self.raised += 1
            raise OSError("append committed; acknowledgement lost")


class DropAndRaiseRunSink(RaiseOnceRunSink):
    """Control: persists NOTHING for the batch it refuses."""

    def write(self, records: tuple[dict, ...]) -> None:
        if any(r.get("kind") in self.kinds for r in records):
            self.raised += 1
            raise OSError("refused before writing anything")
        for record in records:
            self._emit(record)


def sink_factory(cls, directory: Path, kinds):
    class _S:
        def __init__(self) -> None:
            self.sessions = []
            Path(directory).mkdir(parents=True, exist_ok=True)

        def open_run(self, header):
            s = cls(Path(directory) / f"run-{len(self.sessions) + 1:02d}.jsonl",
                    header, kinds)
            self.sessions.append(s)
            return s

    return _S()


def seqs_of(path: Path) -> list[int]:
    return [
        json.loads(l)["seq"]
        for l in path.read_text(encoding="utf-8").split("\n")[1:] if l.strip()
    ]


def loadable(path: Path) -> str:
    lines = [l for l in path.read_text(encoding="utf-8").split("\n") if l.strip()]
    header = json.loads(lines[0])
    try:
        Trace.from_dict({
            "schema_version": header["schema_version"], "run": header["run"],
            "records": [json.loads(l) for l in lines[1:]],
        })
        return "loads"
    except Exception as exc:  # noqa: BLE001
        return f"{type(exc).__name__}: {exc}"


def main() -> int:
    r = Report("x4c-07 falsification")
    SYNC = DurabilityProfile.SYNCHRONOUS

    # --- 1. raise-once sink (the canonical lost ack) ---------------------- #
    for kind, pre_cancel in (("run_completed", False), ("run_cancelled", True)):
        s = sink_factory(RaiseOnceRunSink, OUT / "07" / f"once-{kind}", (kind,))
        rt = Runtime(LINEAR, registry(), durability_profile=SYNC, sink=s)
        if pre_cancel:
            rt.cancel("cancelled before first run")
        try:
            outcome = rt.run().status
        except SinkFailed:
            outcome = "SinkFailed"
        except BaseException as exc:  # noqa: BLE001
            outcome = type(exc).__name__
        path = s.sessions[-1].path
        seqs = seqs_of(path)
        dupes = sorted({x for x in seqs if seqs.count(x) > 1})
        code, out = validate_file(path, "jsonl", spec_path=LINEAR_SPEC_PATH)
        r.record(
            f"FALSIFY 1: raise-once sink, {kind}", code == 0 and not dupes,
            f"outcome={outcome} raised={s.sessions[-1].raised} dup_seq={dupes or 'none'} "
            f"tail={kinds_of_file(path)[-3:]} rc={code} :: "
            f"{out.splitlines()[0][:90] if out else 'VALID'}",
        )
        r.record(
            f"   ...and the package can reload it", loadable(path) == "loads",
            loadable(path),
        )

    # --- 2. control: a sink that persists nothing before raising ---------- #
    s = sink_factory(DropAndRaiseRunSink, OUT / "07" / "drop", ("run_completed",))
    rt = Runtime(LINEAR, registry(), durability_profile=SYNC, sink=s)
    try:
        rt.run()
        outcome = "no raise"
    except SinkFailed:
        outcome = "SinkFailed"
    path = s.sessions[-1].path
    seqs = seqs_of(path)
    code, out = validate_file(path, "jsonl", spec_path=LINEAR_SPEC_PATH)
    r.record(
        "FALSIFY 2: sink that persists nothing -> only truncation", code == 0,
        f"outcome={outcome} dup={sorted({x for x in seqs if seqs.count(x) > 1}) or 'none'} "
        f"tail={kinds_of_file(path)[-2:]} rc={code} :: "
        f"{out.splitlines()[0][:90] if out else 'VALID'}",
    )

    # --- 4. buffered loss: is there ANY public way to close the window? --- #
    s4 = JsonlFileSink(OUT / "07" / "buffered", prefix="b")
    rt4 = Runtime(LINEAR, registry(), durability_profile=DurabilityProfile.BUFFERED,
                  sink=s4, chunk_records=1000, flush_interval_ms=50)
    d4 = rt4.stream()
    for i, _ in enumerate(d4):
        if i >= 5:
            break
    del d4
    gc.collect()
    time.sleep(1.0)          # 20x the declared window
    gc.collect()
    time.sleep(0.3)
    public_flush = [
        n for n in dir(rt4)
        if not n.startswith("_") and "flush" in n.lower()
    ] + [n for n in dir(s4) if "flush" in n.lower()]
    r.record(
        "FALSIFY 4: any way to recover the buffered records",
        bool(kinds_of_file(s4.sessions[0].path)[1:]),
        f"after 20x flush_interval_ms + 2 gc passes: "
        f"{kinds_of_file(s4.sessions[0].path)[1:] or '[]'}; "
        f"public flush API on Runtime/sink: {public_flush or 'none'}",
    )

    # --- 5. does the in-memory Trace at least tell the truth? ------------- #
    s5 = JsonlFileSink(OUT / "07" / "abandoned", prefix="a")
    rt5 = Runtime(LINEAR, registry(), durability_profile=SYNC, sink=s5)
    d5 = rt5.stream()
    for i, _ in enumerate(d5):
        if i >= 3:
            break
    trace_before = d5.trace
    del d5
    gc.collect()
    r.record(
        "FALSIFY 5: in-memory trace of an abandoned run has a terminal",
        trace_before is not None
        and trace_before.records[-1]["kind"].startswith("run_")
        and trace_before.records[-1]["kind"] != "run_started",
        f"last in-memory record = {trace_before.records[-1]['kind']!r} "
        f"({len(trace_before.records)} records); persisted = "
        f"{kinds_of_file(s5.sessions[0].path)[-1:]}",
    )

    # --- 6. guarantees.md §6 StreamDriver: cancellation is trace-recorded - #
    r.record(
        "FALSIFY 6: abandonment lands as run_cancelled, 'never as an "
        "abandoned generator'",
        "run_cancelled" in kinds_of_file(s5.sessions[0].path),
        f"persisted kinds = {kinds_of_file(s5.sessions[0].path)[1:]}",
    )

    return r.dump()


if __name__ == "__main__":
    raise SystemExit(main())
