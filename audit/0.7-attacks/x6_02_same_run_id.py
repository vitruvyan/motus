"""Attack: the file name is a pure function of run_id, and nothing checks the
target does not already exist.

The commit message says this sink exists to prevent "sanitising alone maps
distinct runs onto one file and silently overwrites evidence".  The digest
suffix does that for *distinct* run_ids.  This asks the other half of the
question: what happens when the same run_id shows up twice -- sequentially, and
concurrently -- on one sink, and on two sinks pointed at one directory.
"""

from __future__ import annotations

import json
import sys
import threading
import time

sys.path.insert(0, __file__.rsplit("/", 1)[0])

from x6_common import (  # noqa: E402
    JsonlTraceSink, Report, State, SYNC, listing, registry, runtime, scratch,
    spec_path, validate_expected,
)


def run_ids_of(path):
    ids = []
    for i, line in enumerate(path.read_text(encoding="utf-8").splitlines()):
        if not line.strip():
            continue
        obj = json.loads(line)
        if i == 0:
            ids.append(obj["run"]["run_id"])
    return ids


def intents_of(path):
    """Which run's evidence is actually in this file?"""
    text = path.read_text(encoding="utf-8")
    out = set()
    for line in text.splitlines():
        if not line.strip():
            continue
        obj = json.loads(line)
        if obj.get("kind") == "run_started":
            out.add(obj.get("state_digest", "?"))
    return out


def sequential_same_id(rep: Report) -> None:
    """Two runs, same run_id, one sink, one after the other."""
    directory = scratch("same-id-sequential")
    sink = JsonlTraceSink(directory, fsync=False)

    first = runtime(sink=sink).run(State.empty("FIRST-RUN"), run_id="dup")
    files_after_first = listing(directory)
    first_bytes = (directory / files_after_first[0]).read_bytes()

    second = runtime(sink=sink).run(State.empty("SECOND-RUN"), run_id="dup")
    files_after_second = listing(directory)

    rep.note(f"after first run:  {files_after_first}")
    rep.note(f"after second run: {files_after_second}")

    survived = len(files_after_second) == 2
    rep.record(
        "sequential same run_id keeps both accounts",
        survived,
        f"files={files_after_second} "
        f"first_trace_seq={len(first.trace.to_dict()['records'])} "
        f"second_trace_seq={len(second.trace.to_dict()['records'])}",
    )
    if not survived:
        now = (directory / files_after_second[0]).read_bytes()
        rep.record(
            "the first run's bytes still exist somewhere",
            now == first_bytes,
            "the surviving file is the SECOND run; the first is gone"
            if now != first_bytes else "",
        )
    # And the sink's own accessors still claim two artifacts.
    rep.record(
        "sink.artifacts does not point two sessions at one file",
        len(set(sink.artifacts)) == len(sink.artifacts),
        f"artifacts={[p.name for p in sink.artifacts]}",
    )
    for path in set(sink.artifacts):
        ok, out = validate_expected(path, spec=spec_path())
        rep.record(f"surviving artifact validates ({path.name[:24]})", ok, out[:200])


def concurrent_same_id(rep: Report) -> None:
    """Two runs, same run_id, one sink, at the same time."""
    directory = scratch("same-id-concurrent")
    sink = JsonlTraceSink(directory, fsync=False)
    barrier = threading.Barrier(2)
    errors: list[BaseException] = []

    def go(tag: str) -> None:
        def slow(state):
            barrier.wait(timeout=10)
            time.sleep(0.02)
            return state
        try:
            runtime(sink=sink, nodes={"a": slow, "b": slow, "c": slow}).run(
                State.empty(tag), run_id="dup-concurrent"
            )
        except BaseException as exc:  # noqa: BLE001
            errors.append(exc)

    threads = [threading.Thread(target=go, args=(t,)) for t in ("T1", "T2")]
    for t in threads:
        t.start()
    for t in threads:
        t.join(30)

    files = listing(directory)
    rep.note(f"concurrent same run_id: errors={[type(e).__name__ for e in errors]} files={files}")
    rep.record(
        "concurrent same run_id does not share one file",
        len(files) == 2,
        f"files={files}",
    )
    for name in files:
        path = directory / name
        ok, out = validate_expected(path, spec=spec_path())
        rep.record(f"concurrent artifact validates ({name[:24]})", ok, out[:300])


def two_sinks_one_directory(rep: Report) -> None:
    """Two sinks, one directory, distinct run_ids -- and then the same one."""
    directory = scratch("two-sinks")
    left = JsonlTraceSink(directory, fsync=False)
    right = JsonlTraceSink(directory, fsync=False)

    runtime(sink=left).run(State.empty("LEFT"), run_id="shared-id")
    left_bytes = left.artifacts[0].read_bytes()
    runtime(sink=right).run(State.empty("RIGHT"), run_id="shared-id")

    rep.record(
        "two sinks on one directory keep both accounts",
        len(listing(directory)) == 2,
        f"files={listing(directory)}",
    )
    rep.record(
        "the left sink's artifact still holds the left run",
        left.artifacts[0].read_bytes() == left_bytes,
        "left sink's file was overwritten by the right sink's run",
    )


def main() -> int:
    rep = Report("x6_02 same run_id / shared directory")
    sequential_same_id(rep)
    concurrent_same_id(rep)
    two_sinks_one_directory(rep)
    return rep.dump()


if __name__ == "__main__":
    raise SystemExit(main())
