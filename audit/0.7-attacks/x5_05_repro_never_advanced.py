"""x5-05 -- MINIMAL REPRO: a session opened and then never told anything.

ADR-011 Decision 2:  "`finish` ... Called exactly once, when the runtime will
send that session nothing more."
ADR-011 Consequences: "Rows 1-3 of ADR-010's twelve (a session opened and
given no records) remain possible, but are now KNOWABLE: the sink is told
`complete=False`."

Both are false for a driver that is created and never advanced.
`Runtime._start` binds the sink -- `open_run` runs, the sink creates its file
-- but the record-producing generator has not begun, so `_managed_execute`'s
`finally` (the ONLY caller of `_ObservationHub.close()`, runtime.py:780) never
runs.  `finish` is never called; the file is left open, header-only, and
indistinguishable from a live run, which is the exact condition ADR-011 says
it removed.

The shipped test for this (tests/test_motus_evidence.py::
test_a_session_is_told_whether_its_record_sequence_is_whole) calls
`next(driver)` before dropping it, so it steps over this path.
"""

from __future__ import annotations

import gc
import json
import subprocess
from pathlib import Path

from vitruvyan_motus import DurabilityProfile, Runtime, State

from x5_common import (
    LINEAR, LINEAR_SPEC_PATH, OUT, PYTHON, VALIDATE, Report, registry,
)

D = OUT / "x5_05"
D.mkdir(parents=True, exist_ok=True)


class FileSink:
    """The sink ADR-011 says can now be written honestly."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self.events: list[str] = []

    def open_run(self, header):
        self.events.append("open_run")
        return _Session(self.path, header, self.events)


class _Session:
    def __init__(self, path, header, events):
        self.events = events
        self.fh = open(path, "w", encoding="utf-8", newline="\n")
        self.fh.write(json.dumps(header) + "\n")
        self.fh.flush()
        self.closed = False

    def write(self, records):
        self.events.append(f"write({len(records)})")
        for rec in records:
            self.fh.write(json.dumps(rec) + "\n")
        self.fh.flush()

    def finish(self, *, complete: bool) -> None:
        self.events.append(f"finish(complete={complete})")
        self.fh.close()
        self.closed = True


def one(profile, label, surface):
    path = D / f"{label}.jsonl"
    sink = FileSink(path)
    rt = Runtime(LINEAR, registry(), durability_profile=profile, sink=sink)
    driver = surface(rt)
    session = None
    del driver
    gc.collect()
    return sink, path


def main() -> int:
    r = Report("x5-05 never-advanced driver: session opened, never finished")

    for profile in (DurabilityProfile.SYNCHRONOUS, DurabilityProfile.BUFFERED,
                    DurabilityProfile.IN_MEMORY):
        for surface_name, surface in (
            ("stream", lambda rt: rt.stream(State.empty("never"))),
            ("astream", lambda rt: rt.astream(State.empty("never"))),
        ):
            label = f"{profile.value}-{surface_name}"
            sink, path = one(profile, label, surface)
            lines = [ln for ln in path.read_text().splitlines() if ln.strip()]
            proc = subprocess.run(
                [str(PYTHON), str(VALIDATE), "jsonl", str(path),
                 "--spec", str(LINEAR_SPEC_PATH), "--allow-incomplete"],
                capture_output=True, text=True,
            )
            r.record(
                f"{label}: session receives finish()",
                any(e.startswith("finish") for e in sink.events),
                f"events={sink.events} lines_on_disk={len(lines)} "
                f"validator(--allow-incomplete)="
                f"{'PASS' if proc.returncode == 0 else 'FAIL: ' + (proc.stdout.strip().splitlines() or [''])[0]}",
            )

    # ---- the realistic shape: an exception between stream() and next() ---- #
    path = D / "realistic.jsonl"
    sink = FileSink(path)
    rt = Runtime(LINEAR, registry(),
                 durability_profile=DurabilityProfile.SYNCHRONOUS, sink=sink)
    try:
        driver = rt.stream(State.empty("realistic"))
        raise RuntimeError("caller's setup failed before the first next()")
    except RuntimeError:
        pass
    del driver
    gc.collect()
    r.record(
        "realistic: caller raises before the first next()",
        any(e.startswith("finish") for e in sink.events),
        f"events={sink.events}",
    )

    # ---- the Runtime itself is fine; only the sink is stranded ------------ #
    follow = rt.run(State.empty("follow"))
    r.record(
        "the Runtime is not wedged (the claim IS released)",
        follow.status == "completed",
        f"a second run on the same Runtime: {follow.status}; "
        f"but the first session's file handle is still open and it was never "
        f"told anything",
    )

    return r.dump()


if __name__ == "__main__":
    raise SystemExit(main())
